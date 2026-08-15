"""Validation tests.

The brief asks for "a specific, actionable error — not a generic 500". These
tests assert that every rejection names the offending row *and* field, carries a
stable machine-readable code, and that the whole batch of problems comes back in
one pass rather than one per upload.
"""

from __future__ import annotations

import pytest

from app.core.errors import BillingLogError
from app.core.parsing import PaymentMode, parse_billing_log, parse_timestamp


def codes_for(payload) -> dict[str, list]:
    parsed = parse_billing_log(payload)
    out: dict[str, list] = {}
    for err in parsed.errors:
        out.setdefault(err.code, []).append(err)
    return out


# --------------------------------------------------------------------------
# Every defect in the invalid fixture is caught, precisely
# --------------------------------------------------------------------------


def test_invalid_day_reports_every_problem_in_one_pass(invalid_day):
    """Nine broken rows, nine distinct diagnoses, one upload."""
    parsed = parse_billing_log(invalid_day)

    assert len(parsed.visits) == 1  # only V-201 survives
    assert {e.code for e in parsed.errors} == {
        "non_integer_paise",
        "non_positive_quantity",
        "missing_timezone",
        "invalid_payment_mode",
        "duplicate_visit_id",
        "negative_payment",
        "empty_refund",
        "missing_field",
        "negative_discount",
    }


@pytest.mark.parametrize(
    "code,row_index,field",
    [
        ("non_integer_paise", 1, "amount_paid_paise"),
        ("non_positive_quantity", 2, "line_items[0].qty"),
        ("missing_timezone", 3, "timestamp"),
        ("invalid_payment_mode", 4, "payment_mode"),
        ("duplicate_visit_id", 5, "visit_id"),
        ("negative_payment", 6, "amount_paid_paise"),
        ("empty_refund", 7, "amount_paid_paise"),
        ("missing_field", 8, "amount_paid_paise"),
        ("negative_discount", 9, "discount_paise"),
    ],
)
def test_errors_point_at_the_exact_row_and_field(invalid_day, code, row_index, field):
    err = next(e for e in parse_billing_log(invalid_day).errors if e.code == code)

    assert err.row_index == row_index
    assert err.field == field
    assert err.message


def test_float_paise_is_rejected_not_coerced(invalid_day):
    """Integer paise is the whole point; 13500.0 is a data-entry defect."""
    err = next(e for e in parse_billing_log(invalid_day).errors if e.code == "non_integer_paise")

    assert "integer" in err.message.lower()
    assert "paise" in err.hint.lower()


def test_actionable_errors_carry_a_hint(invalid_day):
    """Codes a clinic can act on say what to do, not just what is wrong."""
    errors = {e.code: e for e in parse_billing_log(invalid_day).errors}

    assert "is_refund=true" in errors["non_positive_quantity"].hint
    assert "is_refund=true" in errors["negative_payment"].hint
    assert "unique" in errors["duplicate_visit_id"].hint
    assert "cash, card, upi" in errors["invalid_payment_mode"].hint


def test_duplicate_visit_id_names_the_earlier_row(invalid_day):
    err = next(e for e in parse_billing_log(invalid_day).errors if e.code == "duplicate_visit_id")

    assert "V-201" in err.message
    assert "row 0" in err.message


def test_errors_carry_visit_id_when_known(invalid_day):
    err = next(e for e in parse_billing_log(invalid_day).errors if e.code == "missing_timezone")
    assert err.visit_id == "V-204"


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_hour",
    [
        ("2026-07-27T12:30:00Z", 12),
        ("2026-07-27T12:30:00+00:00", 12),
        ("2026-07-27T18:00:00+05:30", 12),  # IST converts back to 12:30 UTC
    ],
)
def test_timestamps_normalise_to_utc(raw, expected_hour):
    assert parse_timestamp(raw).hour == expected_hour


def test_naive_timestamp_is_refused():
    """Guessing the zone would put the peak hour in the wrong bucket."""
    with pytest.raises(LookupError):
        parse_timestamp("2026-07-27T12:30:00")


def test_unparseable_timestamp_raises_value_error():
    with pytest.raises(ValueError):
        parse_timestamp("27/07/2026 12:30")


# --------------------------------------------------------------------------
# Tolerant input handling where the meaning is unambiguous
# --------------------------------------------------------------------------


def _row(**overrides):
    row = {
        "clinic_id": "C-1",
        "visit_id": "V-1",
        "timestamp": "2026-07-27T10:00:00Z",
        "line_items": [],
        "payment_mode": "cash",
        "amount_paid_paise": 1000,
        "discount_paise": 0,
        "is_refund": False,
    }
    row.update(overrides)
    return [row]


@pytest.mark.parametrize("mode", ["UPI", "Upi", " upi "])
def test_payment_mode_casing_is_normalised(mode):
    parsed = parse_billing_log(_row(payment_mode=mode))

    assert parsed.is_valid
    assert parsed.visits[0].payment_mode is PaymentMode.UPI


@pytest.mark.parametrize("flag,expected", [(True, True), ("true", True), (1, True), (0, False)])
def test_refund_flag_accepts_unambiguous_spellings(flag, expected):
    parsed = parse_billing_log(_row(is_refund=flag, amount_paid_paise=-500 if expected else 500))

    assert parsed.is_valid, [e.to_dict() for e in parsed.errors]
    assert parsed.visits[0].is_refund is expected


def test_refund_flag_rejects_junk():
    parsed = parse_billing_log(_row(is_refund="maybe"))

    assert not parsed.is_valid
    assert parsed.errors[0].code == "invalid_refund_flag"


def test_unknown_extra_fields_are_ignored():
    """A log with extra columns still ingests; only our fields are enforced."""
    parsed = parse_billing_log(_row(branch_code="KNP-02", _note="anything"))
    assert parsed.is_valid


# --------------------------------------------------------------------------
# Payload envelopes
# --------------------------------------------------------------------------


def test_bare_array_payload_is_accepted():
    parsed = parse_billing_log(_row())
    assert parsed.is_valid and len(parsed.visits) == 1


def test_top_level_clinic_id_is_pushed_onto_rows():
    """Nested exports often name the clinic once, at the top."""
    rows = _row()
    del rows[0]["clinic_id"]
    parsed = parse_billing_log({"clinic_id": "C-9", "visits": rows})

    assert parsed.is_valid
    assert parsed.clinic_id == "C-9"


def test_unrecognised_payload_raises_a_typed_error():
    with pytest.raises(BillingLogError) as exc:
        parse_billing_log({"totally": "unexpected"})

    assert exc.value.code == "unrecognised_payload"
    assert exc.value.errors[0].hint


def test_non_object_row_is_reported_not_crashed():
    parsed = parse_billing_log(["not-a-row", 42])

    assert len(parsed.errors) == 2
    assert {e.code for e in parsed.errors} == {"invalid_row_type"}


# --------------------------------------------------------------------------
# Day-level invariants
# --------------------------------------------------------------------------


def test_two_clinics_in_one_file_is_rejected():
    rows = _row() + _row(clinic_id="C-2", visit_id="V-2")
    parsed = parse_billing_log(rows)

    err = next(e for e in parsed.errors if e.code == "clinic_id_mismatch")
    assert "C-2" in err.message and "C-1" in err.message


def test_two_dates_in_one_file_is_rejected():
    rows = _row() + _row(visit_id="V-2", timestamp="2026-07-28T10:00:00Z")
    parsed = parse_billing_log(rows)

    err = next(e for e in parsed.errors if e.code == "multiple_business_dates")
    assert "2026-07-28" in err.message


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"clinic_id": "C-2"}, "clinic_id_mismatch"),
        ({"timestamp": "2026-07-28T10:00:00Z"}, "multiple_business_dates"),
    ],
)
def test_a_row_failing_a_day_invariant_is_dropped_not_just_flagged(overrides, code):
    """A rejected row must not also be a counted row.

    Reporting the row and keeping it was the worst of both: the foreign clinic's
    money landed in this day's totals while the response claimed the row had been
    dropped, so "17 of 18 rows are included" was a lie in the honest direction.
    """
    rows = _row() + _row(visit_id="V-2", amount_paid_paise=50000, **overrides)
    parsed = parse_billing_log(rows)

    assert [e.code for e in parsed.errors] == [code]
    assert [v.visit_id for v in parsed.visits] == ["V-1"]
    # The surviving row paid ₹10; the rejected one must not add its ₹500.
    assert sum(v.collected_paise for v in parsed.visits) == 1000
    assert parsed.rows_seen == 2  # the day still admits it received two rows


def test_day_invariant_errors_name_the_raw_row_not_the_surviving_position():
    """The row index has to survive earlier rejections shifting the list.

    `visits` is compacted as rows fail, so its positions stop matching the file's
    line numbering. Row 0 here is rejected outright, which puts the offending
    row at visits-position 1 but raw row 2 — and the caller is looking at the
    file, not at our list.
    """
    rows = (
        _row(visit_id="V-0", payment_mode="cheque")
        + _row(visit_id="V-1")
        + _row(visit_id="V-2", clinic_id="C-2")
    )
    parsed = parse_billing_log(rows)

    mismatch = next(e for e in parsed.errors if e.code == "clinic_id_mismatch")
    assert mismatch.row_index == 2
    assert mismatch.visit_id == "V-2"


def test_a_warning_does_not_outlive_the_row_that_raised_it():
    """A warning about a dropped row points at a visit absent from the figures.

    Both rows here overpay, so the filter has to remove one warning and keep the
    other — asserting only that the count fell would pass on a filter that threw
    away everything.
    """
    rows = _row(amount_paid_paise=99999) + _row(
        visit_id="V-2", clinic_id="C-2", amount_paid_paise=88888
    )
    parsed = parse_billing_log(rows)

    assert [v.visit_id for v in parsed.visits] == ["V-1"]
    assert [(w.code, w.visit_id) for w in parsed.warnings] == [("overpayment", "V-1")]


def test_business_date_is_derived_from_the_timestamps(happy_day):
    parsed = parse_billing_log(happy_day)

    assert parsed.business_date.isoformat() == "2026-07-27"
    assert parsed.clinic_id == "CLINIC-MEHTA-001"


# --------------------------------------------------------------------------
# Warnings are surfaced, never silent
# --------------------------------------------------------------------------


def test_edge_day_warnings_are_reported(edge_day):
    parsed = parse_billing_log(edge_day)

    assert parsed.is_valid  # every one of these rows is legal
    assert {w.code for w in parsed.warnings} == {
        "discount_exceeds_total",
        "overpayment",
        "mixed_refund_sign_convention",
    }


def test_clean_day_has_no_warnings(happy_day):
    assert parse_billing_log(happy_day).warnings == []
