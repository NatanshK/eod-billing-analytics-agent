from __future__ import annotations

import pytest

from app.core.analytics import compute_analytics, hour_label, hour_label_short
from app.core.parsing import parse_billing_log


def _analytics(payload, top_n=5):
    parsed = parse_billing_log(payload)
    assert parsed.is_valid, [e.to_dict() for e in parsed.errors]
    return compute_analytics(parsed.visits, top_n=top_n)


@pytest.mark.parametrize(
    "hour,short,full",
    [
        (0, "12am", "12am–1am"),
        (9, "9am", "9am–10am"),
        (11, "11am", "11am–12pm"),
        (12, "12pm", "12pm–1pm"),
        (13, "1pm", "1pm–2pm"),
        (23, "11pm", "11pm–12am"),
    ],
)
def test_hour_labels(hour, short, full):
    assert hour_label_short(hour) == short
    assert hour_label(hour) == full


# --------------------------------------------------------------------------
# Revenue by hour
# --------------------------------------------------------------------------


def test_happy_day_hourly_revenue(happy_day):
    """Two visits land in the 12pm hour: 65000 + 50000 = 115000."""
    a = _analytics(happy_day)
    by_hour = {b.hour: b.revenue_paise for b in a.revenue_by_hour}

    assert by_hour[9] == 20_000
    assert by_hour[10] == 40_000
    assert by_hour[12] == 115_000
    assert by_hour[15] == 38_000


def test_quiet_hours_inside_the_day_are_kept(happy_day):
    """Hours 11, 13 and 14 had no business and must render as empty bars.

    Dropping them would close the gap and misrepresent the shape of the day.
    """
    a = _analytics(happy_day)
    hours = [b.hour for b in a.revenue_by_hour]

    assert hours == list(range(9, 16))
    assert {b.hour: b.revenue_paise for b in a.revenue_by_hour}[11] == 0


def test_peak_hour_is_called_out(happy_day):
    a = _analytics(happy_day)

    assert a.peak_hour is not None
    assert a.peak_hour.hour == 12
    assert a.peak_hour.revenue_paise == 115_000
    assert a.peak_hour.label == "12pm–1pm"
    assert sum(1 for b in a.revenue_by_hour if b.is_peak) == 1


def test_refunds_subtract_from_their_own_hour(edge_day):
    """The 14:00 refund of 15000 makes that hour negative, not zero."""
    a = _analytics(edge_day)
    by_hour = {b.hour: b.revenue_paise for b in a.revenue_by_hour}

    assert by_hour[14] == -15_000
    assert by_hour[15] == -5_000
    assert by_hour[13] == 60_000
    assert a.peak_hour.hour == 13


def test_late_night_visit_buckets_at_23(edge_day):
    a = _analytics(edge_day)
    by_hour = {b.hour: b.revenue_paise for b in a.revenue_by_hour}

    assert by_hour[23] == 15_000
    assert [b.hour for b in a.revenue_by_hour] == list(range(9, 24))


def test_empty_day_has_no_peak(empty_day):
    a = _analytics(empty_day)

    assert a.revenue_by_hour == []
    assert a.peak_hour is None
    assert a.top_by_qty == []


def test_peak_is_none_when_nothing_was_collected():
    """A day of pure refunds has no busiest hour worth naming."""
    payload = {
        "clinic_id": "C-1",
        "visits": [
            {
                "visit_id": "R-1",
                "timestamp": "2026-07-27T10:00:00Z",
                "line_items": [],
                "payment_mode": "cash",
                "amount_paid_paise": -5000,
                "discount_paise": 0,
                "is_refund": True,
            }
        ],
    }
    assert _analytics(payload).peak_hour is None


# --------------------------------------------------------------------------
# The two rankings
# --------------------------------------------------------------------------


def test_rankings_are_genuinely_different_orderings(happy_day):
    """The whole point of showing both lists: they disagree.

    Paracetamol moves the most units but earns the least; Atorvastatin is the
    reverse. A single ranking would hide one of those facts.
    """
    a = _analytics(happy_day)

    assert [d.drug_name for d in a.top_by_qty] == [
        "PARACETAMOL",
        "METFORMIN",
        "OMEPRAZOLE",
        "AMOXICILLIN",
        "ATORVASTATIN",
    ]
    assert [d.drug_name for d in a.top_by_revenue] == [
        "METFORMIN",
        "ATORVASTATIN",
        "AMOXICILLIN",
        "OMEPRAZOLE",
        "PARACETAMOL",
    ]


def test_ranking_values(happy_day):
    a = _analytics(happy_day)
    qty = {d.drug_name: d.qty for d in a.top_by_qty}
    revenue = {d.drug_name: d.revenue_paise for d in a.top_by_revenue}

    assert qty["PARACETAMOL"] == 14  # 10 in V-001 plus 4 in V-003
    assert revenue["PARACETAMOL"] == 28_000
    assert revenue["AMOXICILLIN"] == 40_500  # pre-discount line value
    assert revenue["METFORMIN"] == 69_000


def test_each_rank_carries_both_metrics(happy_day):
    """A row in either list shows units and rupees, so the lists stay comparable."""
    a = _analytics(happy_day)
    para = next(d for d in a.top_by_qty if d.drug_name == "PARACETAMOL")

    assert (para.qty, para.revenue_paise) == (14, 28_000)
    assert [d.rank for d in a.top_by_qty] == [1, 2, 3, 4, 5]


def test_drug_names_are_normalised(edge_day):
    """'  cetirizine  ' and 'CETIRIZINE' must be one row, not two."""
    a = _analytics(edge_day)
    names = [d.drug_name for d in a.top_by_qty]

    assert "CETIRIZINE" in names
    assert len(names) == len(set(names))


def test_refunded_drugs_are_excluded_from_rankings(edge_day):
    """V-106 refunds an Atorvastatin; a returned drug did not move."""
    a = _analytics(edge_day)

    assert "ATORVASTATIN" not in [d.drug_name for d in a.top_by_qty]
    assert "ATORVASTATIN" not in [d.drug_name for d in a.top_by_revenue]
    assert a.distinct_drug_count == 5


def test_ties_break_alphabetically(edge_day):
    """Paracetamol and Metformin both moved 10 units; the order must be stable."""
    a = _analytics(edge_day)
    top_two = [(d.drug_name, d.qty) for d in a.top_by_qty[:2]]

    assert top_two == [("METFORMIN", 10), ("PARACETAMOL", 10)]


def test_ranking_is_deterministic_across_runs(edge_day):
    first = _analytics(edge_day)
    second = _analytics(edge_day)

    assert [d.to_dict() for d in first.top_by_qty] == [d.to_dict() for d in second.top_by_qty]


def test_top_n_is_honoured(happy_day):
    a = _analytics(happy_day, top_n=3)

    assert len(a.top_by_qty) == 3
    assert len(a.top_by_revenue) == 3
    assert a.distinct_drug_count == 5  # the count is of all drugs, not the top slice
