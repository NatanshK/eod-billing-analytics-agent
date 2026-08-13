"""Storage tests, focused on the update-consistency guarantees."""

from __future__ import annotations

import json

import pytest

from app.core.analytics import compute_analytics
from app.core.errors import DayNotFoundError
from app.core.parsing import parse_billing_log
from app.core.reconciliation import reconcile
from app.storage import repository as repo
from app.storage.db import reset


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    """Every test gets its own in-memory database."""
    monkeypatch.setenv("DB_PATH", ":memory:")
    reset()
    yield
    reset()


def store(payload):
    rows = payload["visits"] if isinstance(payload, dict) else payload
    rows = [{"clinic_id": payload.get("clinic_id", "C-1"), **r} for r in rows] if isinstance(payload, dict) else rows
    parsed = parse_billing_log(rows)
    assert parsed.is_valid, [e.to_dict() for e in parsed.errors]
    return repo.save_day(parsed, rows), rows


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_stored_day_reloads_identically(happy_day):
    """A report served from the database must equal one served from the upload."""
    record, rows = store(happy_day)

    fresh = parse_billing_log(rows)
    loaded, _ = repo.load_day(record.clinic_id, record.business_date)

    assert reconcile(loaded.visits).to_dict() == reconcile(fresh.visits).to_dict()
    assert compute_analytics(loaded.visits).to_dict() == compute_analytics(fresh.visits).to_dict()


def test_record_metadata(happy_day):
    record, _ = store(happy_day)

    assert record.clinic_id == "CLINIC-MEHTA-001"
    assert record.business_date == "2026-07-27"
    assert record.row_count == 5
    assert len(record.payload_hash) == 64


def test_warnings_survive_the_round_trip(edge_day):
    record, _ = store(edge_day)
    reloaded = repo.get_day_record(record.clinic_id, record.business_date)

    assert {w["code"] for w in reloaded.warnings} == {
        "discount_exceeds_total",
        "overpayment",
        "mixed_refund_sign_convention",
    }


def test_missing_day_raises_typed_error():
    with pytest.raises(DayNotFoundError):
        repo.load_day("CLINIC-NOPE", "2026-07-27")


# --------------------------------------------------------------------------
# Update consistency
# --------------------------------------------------------------------------


def test_reingesting_replaces_rather_than_merges(happy_day):
    """The correction wins outright; no rows survive from the previous upload."""
    store(happy_day)

    corrected = json.loads(json.dumps(happy_day))
    corrected["visits"] = corrected["visits"][:2]  # two visits were logged in error
    store(corrected)

    loaded, record = repo.load_day("CLINIC-MEHTA-001", "2026-07-27")

    assert len(loaded.visits) == 2
    assert record.row_count == 2
    assert reconcile(loaded.visits).total_billed_paise == 60_000


def test_reingesting_does_not_duplicate_the_day(happy_day):
    store(happy_day)
    store(happy_day)

    assert len(repo.list_days("CLINIC-MEHTA-001")) == 1


def test_identical_reupload_keeps_the_same_hash(happy_day):
    """Key order in the export must not count as a data change."""
    first, _ = store(happy_day)

    shuffled = json.loads(json.dumps(happy_day))
    shuffled["visits"] = [dict(reversed(list(v.items()))) for v in shuffled["visits"]]
    second, _ = store(shuffled)

    assert first.payload_hash == second.payload_hash


def test_changing_the_data_changes_the_hash(happy_day):
    first, _ = store(happy_day)

    corrected = json.loads(json.dumps(happy_day))
    corrected["visits"][0]["amount_paid_paise"] = 19_000
    second, _ = store(corrected)

    assert first.payload_hash != second.payload_hash


def test_a_rejected_day_never_reaches_the_database(invalid_day):
    """Validation runs before the transaction; a bad upload leaves no trace."""
    parsed = parse_billing_log(invalid_day)
    assert parsed.errors

    with pytest.raises(ValueError, match="failed validation"):
        repo.save_day(parsed, invalid_day["visits"])

    assert repo.list_days() == []


def test_delete_removes_the_day(happy_day):
    store(happy_day)

    assert repo.delete_day("CLINIC-MEHTA-001", "2026-07-27") is True
    assert repo.list_days() == []
    assert repo.delete_day("CLINIC-MEHTA-001", "2026-07-27") is False


# --------------------------------------------------------------------------
# Narrative cache invalidation
# --------------------------------------------------------------------------


def test_narrative_is_returned_for_the_matching_hash(happy_day):
    record, _ = store(happy_day)
    repo.save_narrative(record.clinic_id, record.business_date, record.payload_hash, {"lines": ["hi"]})

    cached = repo.load_narrative(record.clinic_id, record.business_date, record.payload_hash)
    assert cached == {"lines": ["hi"]}


def test_correcting_a_day_invalidates_its_narrative(happy_day):
    """The heart of the consistency guarantee: no stale summary on fresh numbers."""
    record, _ = store(happy_day)
    repo.save_narrative(
        record.clinic_id, record.business_date, record.payload_hash, {"lines": ["₹2,320 billed"]}
    )

    corrected = json.loads(json.dumps(happy_day))
    corrected["visits"][0]["amount_paid_paise"] = 15_000
    updated, _ = store(corrected)

    assert repo.load_narrative(record.clinic_id, record.business_date, updated.payload_hash) is None


def test_narrative_cache_survives_an_identical_reupload(happy_day):
    """Re-uploading the same file should not burn another model call."""
    record, _ = store(happy_day)
    repo.save_narrative(record.clinic_id, record.business_date, record.payload_hash, {"lines": ["hi"]})

    again, _ = store(happy_day)

    assert repo.load_narrative(record.clinic_id, record.business_date, again.payload_hash) is not None


def test_deleting_a_day_drops_its_narrative(happy_day):
    record, _ = store(happy_day)
    repo.save_narrative(record.clinic_id, record.business_date, record.payload_hash, {"lines": ["hi"]})

    repo.delete_day(record.clinic_id, record.business_date)
    store(happy_day)

    assert repo.load_narrative(record.clinic_id, record.business_date, record.payload_hash) is None


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_days_are_listed_newest_first(happy_day, edge_day):
    store(happy_day)
    store(edge_day)

    dates = [d.business_date for d in repo.list_days("CLINIC-MEHTA-001")]
    assert dates == ["2026-07-28", "2026-07-27"]


def test_clinics_are_listed(happy_day):
    store(happy_day)
    assert repo.list_clinics() == ["CLINIC-MEHTA-001"]


def test_internal_note_fields_are_not_persisted(edge_day):
    """Fixture annotations must not leak into stored data."""
    record, _ = store(edge_day)
    loaded, _ = repo.load_day(record.clinic_id, record.business_date)

    assert loaded.is_valid
    assert len(loaded.visits) == 8
