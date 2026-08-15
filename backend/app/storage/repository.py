"""Persistence for ingested billing days.

How consistency is maintained on update:

1. A day is replaced, never patched. Re-ingesting (clinic_id, business_date)
   deletes and re-inserts inside one BEGIN IMMEDIATE transaction, so a reader
   sees the whole old day or the whole new one.
2. Validation completes before the transaction opens, so a payload with a bad row
   never reaches the database.
3. Only raw rows are stored; totals are recomputed on every read.
4. The narrative is cached against the SHA-256 of the rows it described, so
   correcting a day makes the old summary unfindable rather than stale.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone
from typing import Any

from ..core.errors import DayNotFoundError
from ..core.parsing import ParsedDay, parse_billing_log
from .db import serialised, transaction


@dataclass(frozen=True)
class DayRecord:
    clinic_id: str
    business_date: str
    payload_hash: str
    row_count: int
    warnings: list[dict[str, Any]]
    ingested_at: str
    rejected: list[dict[str, Any]] = dc_field(default_factory=list)
    rows_received: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "clinic_id": self.clinic_id,
            "business_date": self.business_date,
            "payload_hash": self.payload_hash,
            "row_count": self.row_count,
            "rows_received": self.rows_received,
            "warnings": self.warnings,
            "rejected_rows": self.rejected,
            "ingested_at": self.ingested_at,
        }


def compute_payload_hash(
    rows: list[dict[str, Any]], rejected: list[dict[str, Any]] | None = None
) -> str:
    """A stable fingerprint of a day's rows.

    Keys are sorted before hashing, so re-uploading the same data in a different
    key order produces the same hash and does not burn a model call.
    """
    canonical = json.dumps(
        {"accepted": rows, "rejected": rejected or []},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise_rows(parsed: ParsedDay, raw_rows: list[Any]) -> list[dict[str, Any]]:
    """Keep the accepted rows, with clinic_id resolved onto each one.

    Stored as it will be re-parsed rather than as it arrived, so a reload
    reproduces the same visits even when the uploader named the clinic once at
    the top of the file.
    """
    accepted = {v.visit_id for v in parsed.visits}
    emitted: set[str] = set()
    out: list[dict[str, Any]] = []

    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        visit_id = row.get("visit_id")
        if visit_id not in accepted:
            continue
        # Only the first occurrence of a duplicated visit_id became a visit;
        # storing the rest would collide on the primary key.
        if visit_id in emitted:
            continue
        emitted.add(visit_id)

        stored = {k: v for k, v in row.items() if not k.startswith("_")}
        stored["clinic_id"] = parsed.clinic_id
        out.append(stored)
    return out


def save_day(
    parsed: ParsedDay,
    raw_rows: list[Any],
    *,
    rejected: list[dict[str, Any]] | None = None,
    rows_received: int | None = None,
) -> DayRecord:
    """Store (or atomically replace) one clinic-day.

    `parsed` must contain only accepted rows. The caller passes failures in as
    `rejected` so they are stored alongside the day rather than dropped.
    """
    if parsed.errors:
        raise ValueError("refusing to store a billing day that failed validation")
    if not parsed.clinic_id or parsed.business_date == date.min:
        raise ValueError("cannot store a day without a clinic_id and business_date")

    rows = _normalise_rows(parsed, raw_rows)
    business_date = parsed.business_date.isoformat()
    # The hash covers what was rejected as well as what was kept: correcting a
    # bad row changes the day even when the accepted rows are untouched, and the
    # narrative must not survive that.
    rejected = rejected or []
    payload_hash = compute_payload_hash(rows, rejected)
    warnings = [w.to_dict() for w in parsed.warnings]
    ingested_at = _now()

    with transaction() as conn:
        # Replace, never merge: a correction must not leave orphan rows from a
        # longer previous upload sitting in the same day.
        conn.execute(
            "DELETE FROM visits WHERE clinic_id = ? AND business_date = ?",
            (parsed.clinic_id, business_date),
        )
        conn.execute(
            """
            INSERT INTO billing_days
                (clinic_id, business_date, payload_hash, row_count, warnings_json,
                 rejected_json, rows_received, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (clinic_id, business_date) DO UPDATE SET
                payload_hash  = excluded.payload_hash,
                row_count     = excluded.row_count,
                warnings_json = excluded.warnings_json,
                rejected_json = excluded.rejected_json,
                rows_received = excluded.rows_received,
                ingested_at   = excluded.ingested_at
            """,
            (
                parsed.clinic_id,
                business_date,
                payload_hash,
                len(rows),
                json.dumps(warnings),
                json.dumps(rejected),
                rows_received if rows_received is not None else len(rows) + len(rejected),
                ingested_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO visits (clinic_id, business_date, row_index, visit_id, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (parsed.clinic_id, business_date, i, row["visit_id"], json.dumps(row))
                for i, row in enumerate(rows)
            ],
        )

        # Replacing the rows makes the narrative stale by definition, so it goes
        # in the same transaction rather than waiting for the cache check.
        conn.execute(
            "DELETE FROM narratives WHERE clinic_id = ? AND business_date = ? AND payload_hash != ?",
            (parsed.clinic_id, business_date, payload_hash),
        )

    return DayRecord(
        clinic_id=parsed.clinic_id,
        business_date=business_date,
        payload_hash=payload_hash,
        row_count=len(rows),
        warnings=warnings,
        ingested_at=ingested_at,
        rejected=rejected,
        rows_received=rows_received if rows_received is not None else len(rows) + len(rejected),
    )


def get_day_record(clinic_id: str, business_date: str) -> DayRecord:
    with serialised() as conn:
        row = conn.execute(
            "SELECT * FROM billing_days WHERE clinic_id = ? AND business_date = ?",
            (clinic_id, business_date),
        ).fetchone()
    if row is None:
        raise DayNotFoundError(clinic_id, business_date)
    return _to_record(row)


def _to_record(row: Any) -> DayRecord:
    return DayRecord(
        clinic_id=row["clinic_id"],
        business_date=row["business_date"],
        payload_hash=row["payload_hash"],
        row_count=row["row_count"],
        warnings=json.loads(row["warnings_json"]),
        ingested_at=row["ingested_at"],
        rejected=json.loads(row["rejected_json"]),
        rows_received=row["rows_received"],
    )


def load_day(clinic_id: str, business_date: str) -> tuple[ParsedDay, DayRecord]:
    """Rehydrate a stored day back into validated visits.

    Stored rows go through the same parser the ingest endpoint used, so a report
    served from the database is identical to one served straight after upload.
    """
    # Both reads under one lock hold: taken separately they could straddle a
    # concurrent re-ingest and pair one upload's metadata with another's rows.
    with serialised() as conn:
        record = get_day_record(clinic_id, business_date)
        rows = [
            json.loads(r["payload_json"])
            for r in conn.execute(
                """
                SELECT payload_json FROM visits
                WHERE clinic_id = ? AND business_date = ?
                ORDER BY row_index
                """,
                (clinic_id, business_date),
            )
        ]

    parsed = parse_billing_log(rows) if rows else _empty_day(clinic_id, business_date)
    if parsed.errors:  # pragma: no cover - would mean the store was corrupted
        raise RuntimeError(
            f"stored rows for {clinic_id} {business_date} no longer validate: "
            f"{[e.to_dict() for e in parsed.errors]}"
        )
    return parsed, record


def _empty_day(clinic_id: str, business_date: str) -> ParsedDay:
    return ParsedDay(
        clinic_id=clinic_id,
        business_date=date.fromisoformat(business_date),
        visits=[],
        warnings=[],
        errors=[],
        rows_seen=0,
    )


def list_days(clinic_id: str | None = None) -> list[DayRecord]:
    """Every stored day, most recent first — drives the sidebar's date picker."""
    with serialised() as conn:
        if clinic_id:
            cursor = conn.execute(
                "SELECT * FROM billing_days WHERE clinic_id = ? ORDER BY business_date DESC",
                (clinic_id,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM billing_days ORDER BY clinic_id, business_date DESC"
            )
        return [_to_record(r) for r in cursor]


def list_clinics() -> list[str]:
    with serialised() as conn:
        return [
            r["clinic_id"]
            for r in conn.execute(
                "SELECT DISTINCT clinic_id FROM billing_days ORDER BY clinic_id"
            )
        ]


# --------------------------------------------------------------------------
# Narrative cache — invalidated by data, not by time
# --------------------------------------------------------------------------


def save_narrative(clinic_id: str, business_date: str, payload_hash: str, narrative: dict) -> None:
    with serialised() as conn:
        conn.execute(
            """
            INSERT INTO narratives (clinic_id, business_date, payload_hash, narrative_json, generated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (clinic_id, business_date) DO UPDATE SET
                payload_hash   = excluded.payload_hash,
                narrative_json = excluded.narrative_json,
                generated_at   = excluded.generated_at
            """,
            (clinic_id, business_date, payload_hash, json.dumps(narrative), _now()),
        )


def load_narrative(clinic_id: str, business_date: str, payload_hash: str) -> dict | None:
    """Return the cached narrative only if it describes the current rows.

    A hash mismatch means the day was re-ingested after the summary was written;
    report a miss and let the caller regenerate.
    """
    with serialised() as conn:
        row = conn.execute(
            "SELECT narrative_json, payload_hash FROM narratives WHERE clinic_id = ? AND business_date = ?",
            (clinic_id, business_date),
        ).fetchone()
    if row is None or row["payload_hash"] != payload_hash:
        return None
    return json.loads(row["narrative_json"])


def delete_day(clinic_id: str, business_date: str) -> bool:
    """Remove a day and everything derived from it."""
    with transaction() as conn:
        conn.execute(
            "DELETE FROM visits WHERE clinic_id = ? AND business_date = ?",
            (clinic_id, business_date),
        )
        conn.execute(
            "DELETE FROM narratives WHERE clinic_id = ? AND business_date = ?",
            (clinic_id, business_date),
        )
        cursor = conn.execute(
            "DELETE FROM billing_days WHERE clinic_id = ? AND business_date = ?",
            (clinic_id, business_date),
        )
        deleted = cursor.rowcount > 0
    return deleted
