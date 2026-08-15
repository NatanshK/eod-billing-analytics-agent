"""End-to-end API tests.

The recurring assertion: bad input produces a *useful* 4xx, never a 500, and a
misbehaving model never turns into a failed request.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.config import API_PREFIX as P
from app.storage.db import reset

CLINIC = "CLINIC-MEHTA-001"


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    reset()
    yield
    reset()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def loaded(client, happy_day):
    response = client.post(f"{P}/billing-logs", json=happy_day)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_ingest_returns_a_day_summary(client, happy_day):
    response = client.post(f"{P}/billing-logs", json=happy_day)
    body = response.json()

    assert response.status_code == 201
    assert body["clinic_id"] == CLINIC
    assert body["business_date"] == "2026-07-27"
    assert body["visits_ingested"] == 5
    assert body["rejected_rows"] == []
    assert len(body["payload_hash"]) == 64


def test_ingest_accepts_a_file_upload(client, happy_day):
    """The clinic exports a file; the API takes it without a wrapper."""
    upload = io.BytesIO(json.dumps(happy_day).encode())
    response = client.post(
        f"{P}/billing-logs", files={"file": ("2026-07-27.json", upload, "application/json")}
    )

    assert response.status_code == 201
    assert response.json()["visits_ingested"] == 5


def test_ingest_accepts_a_bare_array(client, happy_day):
    rows = [{"clinic_id": CLINIC, **v} for v in happy_day["visits"]]
    assert client.post(f"{P}/billing-logs", json=rows).status_code == 201


def test_ingest_reports_warnings_without_failing(client, edge_day):
    response = client.post(f"{P}/billing-logs", json=edge_day)
    body = response.json()

    assert response.status_code == 201
    assert {w["code"] for w in body["warnings"]} == {
        "discount_exceeds_total",
        "overpayment",
        "mixed_refund_sign_convention",
    }


# --------------------------------------------------------------------------
# Ingest failures — actionable, never a 500
# --------------------------------------------------------------------------


def test_strict_mode_rejects_the_whole_day(client, invalid_day):
    response = client.post(f"{P}/billing-logs?strict=true", json=invalid_day)
    body = response.json()

    assert response.status_code == 422
    assert body["error"] == "invalid_billing_log"
    assert body["valid_rows"] == 1
    assert body["rejected_rows"] == 9

    first = body["errors"][0]
    assert {"row_index", "code", "message", "field"} <= set(first)


def test_every_rejection_names_a_row_and_a_code(client, invalid_day):
    body = client.post(f"{P}/billing-logs?strict=true", json=invalid_day).json()

    for err in body["errors"]:
        assert isinstance(err["row_index"], int)
        assert err["code"] and err["message"]


def test_default_mode_quarantines_bad_rows_and_keeps_the_day(client, invalid_day):
    """One bad row must not cost the clinic the other seventeen."""
    response = client.post(f"{P}/billing-logs", json=invalid_day)
    body = response.json()

    assert response.status_code == 201
    assert body["visits_ingested"] == 1
    assert len(body["rejected_rows"]) == 9
    assert body["rows_received"] == 10


def test_a_quarantined_row_is_absent_from_the_totals_it_is_reported_beside(client):
    """The disclosure banner and the stat cards have to agree.

    A row rejected for belonging to another clinic used to be listed under
    `rejected_rows` *and* summed into this clinic's billed total — so the screen
    said "1 of 2 rows included" above figures that included both, and CLN-B's
    ₹500 was filed under CLN-A.
    """
    rows = [
        {
            "clinic_id": "CLN-A",
            "visit_id": "V-1",
            "timestamp": "2026-07-27T09:00:00Z",
            "line_items": [{"drug_name": "PARACETAMOL", "qty": 1, "unit_price_paise": 10000}],
            "payment_mode": "cash",
            "amount_paid_paise": 10000,
            "discount_paise": 0,
            "is_refund": False,
        },
        {
            "clinic_id": "CLN-B",
            "visit_id": "V-2",
            "timestamp": "2026-07-27T10:00:00Z",
            "line_items": [{"drug_name": "IBUPROFEN", "qty": 1, "unit_price_paise": 50000}],
            "payment_mode": "cash",
            "amount_paid_paise": 50000,
            "discount_paise": 0,
            "is_refund": False,
        },
    ]

    created = client.post(f"{P}/billing-logs", json=rows).json()
    assert created["clinic_id"] == "CLN-A"
    assert created["visits_ingested"] == 1
    assert [e["code"] for e in created["rejected_rows"]] == ["clinic_id_mismatch"]

    body = client.get(f"{P}/reports/CLN-A/2026-07-27/reconciliation").json()
    assert body["row_count"] == 1 and body["rows_received"] == 2
    assert body["reconciliation"]["total_billed_paise"] == 10000  # not 60000
    assert body["reconciliation"]["visit_count"] == 1

    # The other clinic's drug must not appear in this clinic's rankings either.
    analytics = client.get(f"{P}/reports/CLN-A/2026-07-27/analytics").json()
    assert [d["drug_name"] for d in analytics["analytics"]["top_by_qty"]] == ["PARACETAMOL"]


def test_rejected_rows_are_repeated_on_every_report(client, invalid_day):
    """A screen can never show totals without disclosing what is missing."""
    client.post(f"{P}/billing-logs", json=invalid_day)

    for path in ("reconciliation", "analytics", ""):
        url = f"{P}/reports/{CLINIC}/2026-07-29/{path}".rstrip("/")
        body = client.get(url).json()
        assert len(body["rejected_rows"]) == 9
        assert body["row_count"] == 1
        assert body["rows_received"] == 10


def test_malformed_json_is_a_422_not_a_500(client):
    response = client.post(
        f"{P}/billing-logs",
        content=b'{"visits": [ {"visit_id": "V-1",} ]}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "malformed_json"
    assert "line" in response.json()["errors"][0]["message"]


def test_empty_body_is_a_422(client):
    response = client.post(
        f"{P}/billing-logs", content=b"", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"] == "empty_payload"


def test_unrecognised_payload_shape_is_a_422(client):
    response = client.post(f"{P}/billing-logs", json={"unexpected": "shape"})

    assert response.status_code == 422
    assert response.json()["error"] == "unrecognised_payload"


def test_an_empty_log_alone_cannot_be_filed(client):
    """No rows means no clinic and no date to file the day under."""
    response = client.post(f"{P}/billing-logs", json=[])
    body = response.json()

    assert response.status_code == 422
    assert body["error"] == "unattributable_empty_log"
    assert "clinic_id" in body["errors"][0]["hint"]


def test_an_empty_day_is_ingestable_when_identified(client):
    """A clinic that saw nobody still had a day; it reports as all zeroes."""
    response = client.post(
        f"{P}/billing-logs?clinic_id={CLINIC}&business_date=2026-07-26", json=[]
    )
    assert response.status_code == 201
    assert response.json()["visits_ingested"] == 0

    body = client.get(f"{P}/reports/{CLINIC}/2026-07-26/reconciliation").json()
    assert body["reconciliation"]["total_billed_paise"] == 0
    assert body["reconciliation"]["collection_rate_pct"] is None


def test_supplied_identity_must_agree_with_the_rows(client, happy_day):
    """A mismatch is a filing error, not something to silently override."""
    response = client.post(f"{P}/billing-logs?business_date=2026-01-01", json=happy_day)

    assert response.status_code == 422
    assert response.json()["error"] == "business_date_mismatch"


def test_invalid_supplied_date_is_a_422(client):
    response = client.post(
        f"{P}/billing-logs?clinic_id=X&business_date=27-07-2026", json=[]
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_business_date"


def test_multipart_without_a_file_is_a_422(client):
    response = client.post(f"{P}/billing-logs", data={"notafile": "x"}, files={})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def test_reconciliation_endpoint(client, loaded):
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27/reconciliation").json()
    rec = body["reconciliation"]

    assert body["clinic_name"] == "Mehta Multi-Specialty Clinic"
    assert body["clinic_location"] == "Kanpur, Uttar Pradesh"
    assert body["business_date_display"] == "27 Jul 2026"
    assert rec["total_billed_paise"] == 232_000
    assert rec["total_billed_display"] == "₹2,320"
    assert rec["collection_rate_pct"] == 92
    assert [m["mode"] for m in rec["by_mode"]] == ["cash", "card", "upi"]


def test_analytics_endpoint(client, loaded):
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27/analytics").json()
    analytics = body["analytics"]

    assert analytics["peak_hour"]["label"] == "12pm–1pm"
    assert analytics["peak_hour"]["revenue_display"] == "₹1,150"
    assert [d["drug_name"] for d in analytics["top_by_qty"]][0] == "PARACETAMOL"
    assert [d["drug_name"] for d in analytics["top_by_revenue"]][0] == "METFORMIN"
    assert analytics["revenue_basis"]


def test_analytics_limit_is_honoured(client, loaded):
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27/analytics?limit=2").json()
    assert len(body["analytics"]["top_by_qty"]) == 2


def test_analytics_limit_is_validated(client, loaded):
    response = client.get(f"{P}/reports/{CLINIC}/2026-07-27/analytics?limit=0")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_limit"


def test_full_report_endpoint_returns_both_halves(client, loaded):
    body = client.get(f"{P}/reports/{CLINIC}/2026-07-27").json()

    assert "reconciliation" in body and "analytics" in body
    assert body["reconciliation"]["total_billed_paise"] == 232_000


def test_unknown_day_is_a_404_with_a_hint(client):
    response = client.get(f"{P}/reports/{CLINIC}/2026-01-01/reconciliation")
    body = response.json()

    assert response.status_code == 404
    assert body["error"] == "day_not_found"
    assert "billing-logs" in body["hint"]


def test_unknown_clinic_days_is_a_404(client):
    assert client.get(f"{P}/clinics/NOPE/days").status_code == 404


def test_days_listing_drives_the_date_picker(client, happy_day, edge_day):
    client.post(f"{P}/billing-logs", json=happy_day)
    client.post(f"{P}/billing-logs", json=edge_day)

    body = client.get(f"{P}/clinics/{CLINIC}/days").json()

    assert [d["business_date"] for d in body["days"]] == ["2026-07-28", "2026-07-27"]
    assert body["days"][0]["business_date_display"] == "28 Jul 2026"
    assert body["days"][0]["has_warnings"] is True
    assert body["name"] == "Mehta Multi-Specialty Clinic"


def test_clinics_listing(client, loaded):
    body = client.get(f"{P}/clinics").json()

    assert body["clinics"][0]["clinic_id"] == CLINIC
    assert body["clinics"][0]["days"] == 1


def test_delete_then_fetch_is_a_404(client, loaded):
    assert client.delete(f"{P}/reports/{CLINIC}/2026-07-27").status_code == 204
    assert client.get(f"{P}/reports/{CLINIC}/2026-07-27").status_code == 404


# --------------------------------------------------------------------------
# Narrative
# --------------------------------------------------------------------------


def test_narrative_falls_back_without_an_api_key(client, loaded):
    """No key configured: still a 200, clearly labelled as not model output."""
    response = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "fallback"
    assert body["fallback_reason"]
    assert body["narrative_lines"]
    assert body["traced_figures"]


def test_every_narrative_figure_traces_to_a_report_field(client, loaded):
    body = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative").json()

    for figure in body["traced_figures"]:
        assert figure["field_path"]
        assert figure["display"]
        assert any(figure["display"] in line for line in body["narrative_lines"])


def test_narrative_figures_match_the_reconciliation_exactly(client, loaded):
    """The proof the panel exists to show, asserted rather than eyeballed."""
    report = client.get(f"{P}/reports/{CLINIC}/2026-07-27/reconciliation").json()
    narrative = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative").json()

    traced = {f["field_path"]: f for f in narrative["traced_figures"]}
    billed = traced["reconciliation.total_billed_paise"]

    assert billed["value_paise"] == report["reconciliation"]["total_billed_paise"]
    assert billed["display"] == report["reconciliation"]["total_billed_display"]


def test_narrative_states_the_profit_caveat(client, loaded):
    body = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative").json()
    assert "profit" in body["caveat"].lower()


def test_narrative_is_cached_on_the_second_request(client, loaded):
    first = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative").json()
    second = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative").json()

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["narrative_lines"] == second["narrative_lines"]


def test_force_regenerates(client, loaded):
    client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative")
    forced = client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative?force=true").json()

    assert forced["cached"] is False


def test_get_narrative_before_generating_is_a_404(client, loaded):
    response = client.get(f"{P}/reports/{CLINIC}/2026-07-27/narrative")

    assert response.status_code == 404
    assert response.json()["error"] == "narrative_not_current"
    assert "POST" in response.json()["hint"]


def test_get_returns_the_cached_narrative(client, loaded):
    client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative")
    response = client.get(f"{P}/reports/{CLINIC}/2026-07-27/narrative")

    assert response.status_code == 200
    assert response.json()["cached"] is True


def test_correcting_a_day_invalidates_the_narrative(client, happy_day):
    """End-to-end version of the consistency guarantee."""
    client.post(f"{P}/billing-logs", json=happy_day)
    client.post(f"{P}/reports/{CLINIC}/2026-07-27/narrative")
    assert client.get(f"{P}/reports/{CLINIC}/2026-07-27/narrative").status_code == 200

    corrected = json.loads(json.dumps(happy_day))
    corrected["visits"][0]["amount_paid_paise"] = 15_000
    client.post(f"{P}/billing-logs", json=corrected)

    stale = client.get(f"{P}/reports/{CLINIC}/2026-07-27/narrative")
    assert stale.status_code == 404, "a stale narrative must not survive a correction"


def test_narrative_for_an_unknown_day_is_a_404(client):
    assert client.post(f"{P}/reports/{CLINIC}/2026-01-01/narrative").status_code == 404


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------


def test_health_reports_llm_configuration(client):
    body = client.get(f"{P}/health").json()

    assert body["status"] == "ok"
    assert body["llm_configured"] is False
    assert body["narrative_source_if_asked_now"] == "fallback"


def test_root_points_at_the_docs(client):
    assert client.get("/").json()["docs"] == "/docs"


def test_openapi_schema_builds(client):
    """A broken response model would surface here rather than in production."""
    assert client.get("/openapi.json").status_code == 200


def test_cors_headers_are_present(client, loaded):
    response = client.get(
        f"{P}/reports/{CLINIC}/2026-07-27/reconciliation",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
