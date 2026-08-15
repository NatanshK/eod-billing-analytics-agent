"""Report assembly — the one place a stored day becomes an API payload.

Every read path goes through here, so all three screens describe the same
recomputed report rather than three separately derived views of it.
"""

from __future__ import annotations

from typing import Any

from ..config import DEFAULT_TOP_N, clinic_profile
from ..core.analytics import Analytics, compute_analytics
from ..core.parsing import ParsedDay
from ..core.reconciliation import Reconciliation, reconcile
from ..storage.repository import DayRecord, load_day


def day_context(clinic_id: str, business_date: str) -> tuple[ParsedDay, DayRecord]:
    return load_day(clinic_id, business_date)


def day_header(parsed: ParsedDay, record: DayRecord) -> dict[str, Any]:
    """The identity block every screen shows in its title area."""
    profile = clinic_profile(record.clinic_id)
    return {
        "clinic_id": record.clinic_id,
        "clinic_name": profile["name"],
        "clinic_location": profile["location"],
        "clinic_owner": profile["owner"],
        "business_date": record.business_date,
        "business_date_display": parsed.business_date.strftime("%-d %b %Y"),
        "ingested_at": record.ingested_at,
        "payload_hash": record.payload_hash,
        "row_count": record.row_count,
        "rows_received": record.rows_received,
        "warnings": record.warnings,
        # On every report, so no screen can present totals without disclosing
        # that some rows did not make it into them.
        "rejected_rows": record.rejected,
    }


def build_reconciliation(parsed: ParsedDay, record: DayRecord) -> dict[str, Any]:
    return {**day_header(parsed, record), "reconciliation": reconcile(parsed.visits).to_dict()}


def build_analytics(
    parsed: ParsedDay, record: DayRecord, top_n: int = DEFAULT_TOP_N
) -> dict[str, Any]:
    return {
        **day_header(parsed, record),
        "analytics": compute_analytics(parsed.visits, top_n=top_n).to_dict(),
    }


def build_full_report(
    parsed: ParsedDay, record: DayRecord, top_n: int = DEFAULT_TOP_N
) -> dict[str, Any]:
    """Both halves in one round trip, for the app shell's initial load."""
    return {
        **day_header(parsed, record),
        "reconciliation": reconcile(parsed.visits).to_dict(),
        "analytics": compute_analytics(parsed.visits, top_n=top_n).to_dict(),
    }


def computed(parsed: ParsedDay, top_n: int = DEFAULT_TOP_N) -> tuple[Reconciliation, Analytics]:
    """The two ground-truth objects the narrative layer is grounded in."""
    return reconcile(parsed.visits), compute_analytics(parsed.visits, top_n=top_n)
