"""Tests against the provided sample dataset.

Its README says "don't assume anything about a day beyond what its file actually
contains", and the three days are each awkward in a different way:

* **25 Jul** — every row is a refund. Nothing was billed, so the collection rate
  is undefined and there is no busiest hour.
* **26 Jul** — an empty file. A real day on which nobody came in.
* **27 Jul** — the ordinary day, containing one row with no ``payment_mode`` and
  one misspelled drug name.

Expected values below are computed by hand from the files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import API_PREFIX as P
from app.core.analytics import compute_analytics
from app.core.parsing import parse_billing_log
from app.core.reconciliation import reconcile
from app.storage.db import reset

REAL_DIR = Path(__file__).parent / "fixtures" / "real"
CLINIC = "CLN-KNP-014"


def load(day: str):
    return json.loads((REAL_DIR / f"billing_log_{day}.json").read_text())


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    reset()
    yield
    reset()


@pytest.fixture
def client(clean):
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --------------------------------------------------------------------------
# 27 Jul — the ordinary day
# --------------------------------------------------------------------------


def test_27th_accepts_18_of_19_rows():
    parsed = parse_billing_log(load("2026-07-27"))

    assert parsed.rows_seen == 19
    assert len(parsed.visits) == 18
    assert len(parsed.errors) == 1


def test_27th_names_the_row_that_is_missing_payment_mode():
    error = parse_billing_log(load("2026-07-27")).errors[0]

    assert error.code == "missing_field"
    assert error.field == "payment_mode"
    assert error.visit_id == "V-20260727-019"
    assert error.row_index == 18


def test_27th_reconciliation():
    parsed = parse_billing_log(load("2026-07-27"))
    r = reconcile(parsed.visits)

    assert r.total_billed_paise == 319_000
    assert r.total_collected_paise == 317_200
    assert r.outstanding_paise == 1_800
    assert r.refunds_paise == 0
    assert r.collection_rate_pct == 99
    assert r.billable_visit_count == 18
    assert r.pending_visit_count == 3


def test_27th_payment_mode_breakdown():
    parsed = parse_billing_log(load("2026-07-27"))
    by_mode = {m.mode: m for m in reconcile(parsed.visits).by_mode}

    assert by_mode["cash"].billed_paise == 127_500
    assert by_mode["card"].billed_paise == 83_500
    assert by_mode["upi"].billed_paise == 108_000
    assert sum(m.billed_paise for m in by_mode.values()) == 319_000


def test_27th_peak_hour():
    parsed = parse_billing_log(load("2026-07-27"))
    peak = compute_analytics(parsed.visits).peak_hour

    assert peak.hour == 13
    assert peak.label == "1pm–2pm"
    assert peak.revenue_paise == 75_500


def test_27th_rankings_disagree():
    """Omeprazole moves the most units; Atorvastatin earns the most money."""
    parsed = parse_billing_log(load("2026-07-27"))
    a = compute_analytics(parsed.visits)

    assert a.top_by_qty[0].drug_name == "OMEPRAZOLE"
    assert a.top_by_qty[0].qty == 18
    assert a.top_by_revenue[0].drug_name == "ATORVASTATIN"
    assert a.top_by_revenue[0].revenue_paise == 120_000


def test_misspelled_drug_is_flagged_but_not_merged():
    """'PARACETMOL' is almost certainly 'PARACETAMOL' — but only almost.

    Auto-correcting would move revenue between line items on a guess. The
    warning tells the clinic; the totals stay exactly as recorded.
    """
    parsed = parse_billing_log(load("2026-07-27"))
    a = compute_analytics(parsed.visits, top_n=20)
    names = {d.drug_name: d.qty for d in a.top_by_qty}

    assert names["PARACETAMOL"] == 11
    assert names["PARACETMOL"] == 2  # counted separately, not merged

    warning = next(w for w in parsed.warnings if w.code == "possible_drug_name_typo")
    assert "PARACETMOL" in warning.message
    assert "PARACETAMOL" in warning.message


def test_distinct_drugs_are_not_over_merged():
    """Only the typo pair is flagged; real drugs stay distinct and unwarned."""
    warnings = [
        w for w in parse_billing_log(load("2026-07-27")).warnings
        if w.code == "possible_drug_name_typo"
    ]
    assert len(warnings) == 1


def test_27th_out_of_order_timestamps_are_handled():
    """Rows 1 and 2 are logged out of chronological order; bucketing is by value."""
    parsed = parse_billing_log(load("2026-07-27"))
    by_hour = {b.hour: b.revenue_paise for b in compute_analytics(parsed.visits).revenue_by_hour}

    assert by_hour[9] == 9_000  # 6000 at 09:10 plus 3000 at 09:00


# --------------------------------------------------------------------------
# 25 Jul — every row is a refund
# --------------------------------------------------------------------------


def test_25th_is_all_refunds():
    parsed = parse_billing_log(load("2026-07-25"))
    r = reconcile(parsed.visits)

    assert parsed.is_valid
    assert r.refund_count == 3
    assert r.billable_visit_count == 0
    assert r.refunds_paise == 49_000
    assert r.total_billed_paise == 0
    assert r.total_collected_paise == 0
    assert r.outstanding_paise == 0


def test_25th_collection_rate_is_undefined_not_zero():
    """Nothing was billed, so there is no rate. '0%' would be a lie."""
    parsed = parse_billing_log(load("2026-07-25"))
    assert reconcile(parsed.visits).collection_rate_pct is None


def test_25th_has_no_peak_hour():
    """No hour did any business; naming a 'busiest' one would be meaningless."""
    parsed = parse_billing_log(load("2026-07-25"))
    assert compute_analytics(parsed.visits).peak_hour is None


def test_25th_rankings_are_empty():
    """Refunded drugs did not move, so nothing belongs in either ranking."""
    a = compute_analytics(parse_billing_log(load("2026-07-25")).visits)

    assert a.top_by_qty == []
    assert a.top_by_revenue == []


def test_25th_refunds_split_by_mode():
    parsed = parse_billing_log(load("2026-07-25"))
    by_mode = {m.mode: m for m in reconcile(parsed.visits).by_mode}

    assert by_mode["card"].refunds_paise == 24_000
    assert by_mode["upi"].refunds_paise == 25_000
    assert by_mode["cash"].refunds_paise == 0


def test_25th_negative_amounts_normalise_to_positive_magnitudes():
    parsed = parse_billing_log(load("2026-07-25"))

    assert all(v.refund_paise > 0 for v in parsed.visits)
    assert all(v.collected_paise == 0 for v in parsed.visits)


# --------------------------------------------------------------------------
# 26 Jul — the empty day
# --------------------------------------------------------------------------


def test_26th_is_empty():
    parsed = parse_billing_log(load("2026-07-26"))

    assert parsed.rows_seen == 0
    assert parsed.visits == []
    assert parsed.errors == []


def test_26th_reconciles_to_zero_without_dividing_by_zero():
    parsed = parse_billing_log(load("2026-07-26"))
    r = reconcile(parsed.visits)

    assert r.total_billed_paise == 0
    assert r.collection_rate_pct is None
    assert [m.mode for m in r.by_mode] == ["cash", "card", "upi"]


# --------------------------------------------------------------------------
# All three, end to end through the API
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day", ["2026-07-25", "2026-07-26", "2026-07-27"])
def test_every_sample_day_ingests_and_reports(client, day):
    """The headline requirement: all three run through the same pipeline."""
    query = f"?clinic_id={CLINIC}&business_date={day}"
    assert client.post(f"{P}/billing-logs{query}", json=load(day)).status_code == 201

    for path in ("reconciliation", "analytics", ""):
        url = f"{P}/reports/{CLINIC}/{day}/{path}".rstrip("/")
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("day", ["2026-07-25", "2026-07-26", "2026-07-27"])
def test_every_sample_day_produces_a_grounded_narrative(client, day):
    """Including the all-refund day and the empty one, which have no peak hour."""
    query = f"?clinic_id={CLINIC}&business_date={day}"
    client.post(f"{P}/billing-logs{query}", json=load(day))

    response = client.post(f"{P}/reports/{CLINIC}/{day}/narrative")
    body = response.json()

    assert response.status_code == 200
    assert body["narrative_lines"]
    for figure in body["traced_figures"]:
        assert figure["field_path"]
        assert any(figure["display"] in line for line in body["narrative_lines"])


def test_27th_report_discloses_the_rejected_row(client):
    client.post(f"{P}/billing-logs", json=load("2026-07-27"))
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27/reconciliation").json()

    assert body["row_count"] == 18
    assert body["rows_received"] == 19
    assert body["rejected_rows"][0]["visit_id"] == "V-20260727-019"


def test_clinic_is_named_from_the_directory(client):
    client.post(f"{P}/billing-logs", json=load("2026-07-27"))
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27/reconciliation").json()

    assert body["clinic_name"] == "Mehta Multi-Specialty Clinic"
    assert body["clinic_location"] == "Kanpur, Uttar Pradesh"


def test_seeding_loads_all_three_days(clean, monkeypatch):
    """A fresh deploy comes up with the sample data already browsable."""
    monkeypatch.setenv("SEED_ON_STARTUP", "true")
    from app.seed import load_seed_files
    from app.storage import repository as repo

    loaded = load_seed_files(Path(__file__).parent.parent / "seed_data")

    assert loaded == 3
    assert [d.business_date for d in repo.list_days(CLINIC)] == [
        "2026-07-27",
        "2026-07-26",
        "2026-07-25",
    ]
