"""Golden-value tests for the reconciliation layer.

Expected numbers are computed by hand from the fixtures and written out as
literals. That is deliberate: asserting against a value re-derived by the code
under test would prove only that the code agrees with itself.
"""

from __future__ import annotations

import pytest

from app.core.parsing import parse_billing_log
from app.core.reconciliation import (
    Reconciliation,
    ReconciliationInvariantError,
    _assert_invariants,
    reconcile,
)


def _reconcile(payload) -> Reconciliation:
    parsed = parse_billing_log(payload)
    assert parsed.is_valid, [e.to_dict() for e in parsed.errors]
    return reconcile(parsed.visits)


# --------------------------------------------------------------------------
# Happy day
# --------------------------------------------------------------------------


def test_happy_day_headline_totals(happy_day):
    """V-001..V-005: billed 232000, of which 213000 collected, 19000 unpaid."""
    r = _reconcile(happy_day)

    assert r.total_billed_paise == 232_000
    assert r.total_collected_paise == 213_000
    assert r.outstanding_paise == 19_000
    assert r.refunds_paise == 0
    assert r.net_collected_paise == 213_000
    assert r.overpayments_paise == 0


def test_happy_day_counts_and_rate(happy_day):
    r = _reconcile(happy_day)

    assert r.visit_count == 5
    assert r.billable_visit_count == 5
    assert r.pending_visit_count == 1  # only V-004 is short
    assert r.refund_count == 0
    assert r.collection_rate_pct == 92  # 213000/232000 = 91.8%


def test_happy_day_discounts_reduce_billed(happy_day):
    """V-002 grosses 40500 with a 500 discount, so it bills 40000 exactly."""
    parsed = parse_billing_log(happy_day)
    v2 = next(v for v in parsed.visits if v.visit_id == "V-002")

    assert v2.gross_paise == 40_500
    assert v2.discount_paise == 500
    assert v2.billed_paise == 40_000
    assert v2.outstanding_paise == 0


def test_happy_day_payment_mode_breakdown(happy_day):
    r = _reconcile(happy_day)
    by_mode = {m.mode: m for m in r.by_mode}

    assert (by_mode["cash"].billed_paise, by_mode["cash"].collected_paise) == (89_000, 70_000)
    assert (by_mode["card"].billed_paise, by_mode["card"].collected_paise) == (40_000, 40_000)
    assert (by_mode["upi"].billed_paise, by_mode["upi"].collected_paise) == (103_000, 103_000)
    assert by_mode["cash"].outstanding_paise == 19_000
    assert by_mode["card"].outstanding_paise == 0
    assert by_mode["upi"].outstanding_paise == 0


def test_all_three_modes_present_even_when_unused(happy_day):
    """The dashboard table always has three rows; an unused mode shows zeroes."""
    r = _reconcile(happy_day)
    assert [m.mode for m in r.by_mode] == ["cash", "card", "upi"]


# --------------------------------------------------------------------------
# Edge day — the non-happy path the brief asks for
# --------------------------------------------------------------------------


def test_edge_day_headline_totals(edge_day):
    r = _reconcile(edge_day)

    assert r.total_billed_paise == 119_500
    assert r.total_collected_paise == 105_000  # gross of refunds
    assert r.refunds_paise == 20_000  # 15000 negative-signed + 5000 positive-signed
    assert r.net_collected_paise == 85_000
    assert r.collection_rate_pct == 88


def test_overpayment_does_not_cancel_another_visits_shortfall(edge_day):
    """V-105 overpays by 2500; V-102 is 17000 short. Outstanding stays 17000.

    Computing outstanding as (billed - collected) across the whole day would
    report 14500 here and quietly understate what the clinic is owed.
    """
    r = _reconcile(edge_day)

    assert r.outstanding_paise == 17_000
    assert r.overpayments_paise == 2_500
    assert r.pending_visit_count == 1
    assert r.total_billed_paise - r.total_collected_paise == 14_500  # the wrong answer


def test_refund_visits_are_excluded_from_billing(edge_day):
    """A refund moves the refunds figure only — never billed or outstanding."""
    parsed = parse_billing_log(edge_day)
    refunds = [v for v in parsed.visits if v.is_refund]

    assert len(refunds) == 2
    for visit in refunds:
        assert visit.billed_paise == 0
        assert visit.collected_paise == 0
        assert visit.outstanding_paise == 0
    assert {v.refund_paise for v in refunds} == {15_000, 5_000}


def test_refund_sign_conventions_both_normalise(edge_day):
    """V-106 arrives negative, V-107 positive; both become positive magnitudes."""
    parsed = parse_billing_log(edge_day)
    by_id = {v.visit_id: v for v in parsed.visits}

    assert by_id["V-106"].refund_paise == 15_000
    assert by_id["V-107"].refund_paise == 5_000

    codes = {w.code for w in parsed.warnings}
    assert "mixed_refund_sign_convention" in codes


def test_discount_larger_than_total_clamps_and_warns(edge_day):
    """V-104: 24000 of drugs against a 30000 discount bills 0, not -6000."""
    parsed = parse_billing_log(edge_day)
    v104 = next(v for v in parsed.visits if v.visit_id == "V-104")

    assert v104.gross_paise == 24_000
    assert v104.discount_paise == 30_000
    assert v104.billed_paise == 0

    warning = next(w for w in parsed.warnings if w.visit_id == "V-104")
    assert warning.code == "discount_exceeds_total"


def test_consultation_only_visit_is_valid(edge_day):
    """Empty line_items is a real visit shape, not a malformed row."""
    parsed = parse_billing_log(edge_day)
    v103 = next(v for v in parsed.visits if v.visit_id == "V-103")

    assert v103.line_items == ()
    assert v103.billed_paise == 0
    assert v103.outstanding_paise == 0


def test_edge_day_payment_mode_breakdown(edge_day):
    r = _reconcile(edge_day)
    by_mode = {m.mode: m for m in r.by_mode}

    assert by_mode["cash"].billed_paise == 20_000
    assert by_mode["cash"].refunds_paise == 5_000
    assert by_mode["card"].billed_paise == 84_500
    assert by_mode["card"].outstanding_paise == 17_000
    assert by_mode["upi"].billed_paise == 15_000
    assert by_mode["upi"].refunds_paise == 15_000


# --------------------------------------------------------------------------
# Empty day
# --------------------------------------------------------------------------


def test_empty_day_does_not_divide_by_zero(empty_day):
    r = _reconcile(empty_day)

    assert r.total_billed_paise == 0
    assert r.collection_rate_pct is None  # undefined, not 0%
    assert r.visit_count == 0
    assert [m.mode for m in r.by_mode] == ["cash", "card", "upi"]


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["happy_day", "edge_day", "empty_day"])
def test_mode_columns_sum_to_totals(fixture, request):
    """reconcile() asserts this internally; this proves the check runs."""
    r = _reconcile(request.getfixturevalue(fixture))

    assert sum(m.billed_paise for m in r.by_mode) == r.total_billed_paise
    assert sum(m.collected_paise for m in r.by_mode) == r.total_collected_paise
    assert sum(m.outstanding_paise for m in r.by_mode) == r.outstanding_paise
    assert sum(m.refunds_paise for m in r.by_mode) == r.refunds_paise


def test_invariant_violation_raises(happy_day):
    """A corrupted breakdown must raise rather than serve a self-contradicting report."""
    r = _reconcile(happy_day)
    r.by_mode[0].billed_paise += 1

    with pytest.raises(ReconciliationInvariantError, match="billed"):
        _assert_invariants(r)


def test_all_money_fields_are_integers(edge_day):
    """No float may reach the report; paise are integers end to end."""
    r = _reconcile(edge_day)
    payload = r.to_dict()

    for key, value in payload.items():
        if key.endswith("_paise"):
            assert isinstance(value, int), f"{key} is {type(value).__name__}"
    for mode in payload["by_mode"]:
        for key, value in mode.items():
            if key.endswith("_paise"):
                assert isinstance(value, int), f"by_mode.{mode['mode']}.{key}"
