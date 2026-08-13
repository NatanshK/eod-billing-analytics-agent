"""Billing-log parsing, validation and normalisation.

This module turns a raw payload into a list of :class:`Visit` records with every
monetary figure already reduced to integer paise, or into a precise list of
:class:`RowError` objects explaining which row failed and why.

Two rules shape the design:

* **Errors are collected, not raised on first failure.** A front desk fixing a
  day's log wants every problem at once, not one per upload round-trip.
* **Amounts are strictly integers.** ``4285000`` is accepted; ``4285000.0`` and
  ``"4285000"`` are not. The brief calls the integer-paise choice intentional, so
  a float arriving in a paise field is treated as a data-entry defect rather than
  quietly coerced.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from .errors import BillingLogError, RowError, RowWarning


class PaymentMode(str, Enum):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"


PAYMENT_MODES: tuple[str, ...] = tuple(m.value for m in PaymentMode)


# --------------------------------------------------------------------------
# Input models — shape and type enforcement
# --------------------------------------------------------------------------


class LineItemIn(BaseModel):
    """One dispensed drug line. Quantities and prices are strict integers."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    drug_name: str = Field(min_length=1)
    qty: StrictInt = Field(ge=1)
    unit_price_paise: StrictInt = Field(ge=0)


class VisitIn(BaseModel):
    """One visit row exactly as it arrives from the billing log."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    clinic_id: str = Field(min_length=1)
    visit_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    doctor_id: str | None = None
    line_items: list[LineItemIn] = Field(default_factory=list)
    payment_mode: PaymentMode
    amount_paid_paise: StrictInt
    discount_paise: StrictInt = Field(default=0, ge=0)
    is_refund: bool = False

    @field_validator("payment_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, v: Any) -> Any:
        """Accept ``UPI``/``Cash`` casing; the enum itself stays lowercase."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("is_refund", mode="before")
    @classmethod
    def _coerce_refund_flag(cls, v: Any) -> Any:
        """Accept the unambiguous spellings of a boolean, reject the rest.

        ``true``/``"true"``/``1`` all mean the same thing in a hand-maintained
        export and coercing them loses no information. Anything else keeps the
        strict-boolean rejection so genuine junk still surfaces as an error.
        """
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, int) and v in (0, 1):
            return bool(v)
        if isinstance(v, str) and v.strip().lower() in {"true", "false"}:
            return v.strip().lower() == "true"
        return v


# --------------------------------------------------------------------------
# Normalised domain records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LineItem:
    drug_name: str  # normalised: trimmed + uppercased
    qty: int
    unit_price_paise: int

    @property
    def line_total_paise(self) -> int:
        return self.qty * self.unit_price_paise


@dataclass(frozen=True)
class Visit:
    """A validated visit with its money already reconciled.

    The derived fields are computed once, here, so that reconciliation and
    analytics never re-derive them and cannot disagree about what a visit means.
    """

    clinic_id: str
    visit_id: str
    timestamp: datetime  # timezone-aware, normalised to UTC
    doctor_id: str | None
    line_items: tuple[LineItem, ...]
    payment_mode: PaymentMode
    is_refund: bool

    gross_paise: int  # sum of line items, before discount
    discount_paise: int
    billed_paise: int  # max(0, gross - discount); always 0 for a refund
    collected_paise: int  # cash actually taken in; always 0 for a refund
    refund_paise: int  # positive magnitude of money given back
    outstanding_paise: int  # max(0, billed - collected)
    overpaid_paise: int  # max(0, collected - billed)

    @property
    def hour(self) -> int:
        return self.timestamp.hour

    @property
    def business_date(self) -> date:
        return self.timestamp.date()


@dataclass
class ParsedDay:
    """The outcome of parsing one billing log."""

    clinic_id: str
    business_date: date
    visits: list[Visit]
    warnings: list[RowWarning]
    errors: list[RowError]
    rows_seen: int

    @property
    def is_valid(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------
# Pydantic error translation
# --------------------------------------------------------------------------

_PAISE_FIELDS = {"amount_paid_paise", "discount_paise", "unit_price_paise"}
_INT_ERRORS = {"int_type", "int_parsing", "int_from_float"}

_PAISE_HINT = (
    "Amounts must be integer paise, not rupees or floats — ₹428.50 is 42850."
)


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """Render a Pydantic location tuple as ``line_items[0].qty``."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


def _classify(loc: tuple[Any, ...], etype: str, msg: str) -> tuple[str, str, str | None]:
    """Map a Pydantic failure onto (code, message, hint) the clinic can act on."""
    path = _loc_to_path(loc)
    name = next((p for p in reversed(loc) if isinstance(p, str)), path)

    if etype == "missing":
        return (
            "missing_field",
            f"{path} is required but was not present",
            None,
        )
    if name in _PAISE_FIELDS and etype in _INT_ERRORS:
        return ("non_integer_paise", f"{path} must be an integer number of paise", _PAISE_HINT)
    if name == "qty" and etype in _INT_ERRORS:
        return ("non_integer_quantity", f"{path} must be a whole number of units", None)
    if name == "qty" and etype == "greater_than_equal":
        return (
            "non_positive_quantity",
            f"{path} must be at least 1",
            "Use is_refund=true for a reversal instead of a zero or negative qty.",
        )
    if name == "unit_price_paise" and etype == "greater_than_equal":
        return ("negative_unit_price", f"{path} cannot be negative", None)
    if name == "discount_paise" and etype == "greater_than_equal":
        return (
            "negative_discount",
            f"{path} cannot be negative",
            "A negative discount is a surcharge — add it as a line item instead.",
        )
    if name == "payment_mode":
        return (
            "invalid_payment_mode",
            f"{path} is not a recognised payment mode",
            f"Expected one of: {', '.join(PAYMENT_MODES)}.",
        )
    if name == "is_refund":
        return (
            "invalid_refund_flag",
            f"{path} must be a boolean",
            "Use true or false.",
        )
    if name == "line_items" and etype in {"list_type", "iterable_type"}:
        return (
            "invalid_line_items",
            f"{path} must be an array of line items",
            "Use an empty array [] for a consultation-only visit.",
        )
    if etype == "string_too_short":
        return ("empty_field", f"{path} cannot be empty", None)
    if etype == "string_type":
        return ("invalid_type", f"{path} must be a string", None)
    return ("invalid_field", f"{path}: {msg}", None)


# --------------------------------------------------------------------------
# Payload shape handling
# --------------------------------------------------------------------------

_ROW_CONTAINER_KEYS = ("visits", "rows", "billing_log", "log", "entries", "data", "records")


def extract_rows(payload: Any) -> list[Any]:
    """Pull the visit rows out of whichever envelope the log arrived in.

    Accepts a bare array of rows, or an object wrapping them under any of the
    common container keys. A top-level ``clinic_id`` is pushed down onto rows
    that omit it, so both flat and nested exports validate identically.
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in _ROW_CONTAINER_KEYS:
            rows = payload.get(key)
            if isinstance(rows, list):
                outer_clinic = payload.get("clinic_id")
                if isinstance(outer_clinic, str):
                    rows = [
                        {"clinic_id": outer_clinic, **r} if isinstance(r, dict) and "clinic_id" not in r else r
                        for r in rows
                    ]
                return rows
        # A single visit object posted on its own.
        if "visit_id" in payload:
            return [payload]

    raise BillingLogError(
        "Could not find any billing rows in the payload",
        code="unrecognised_payload",
        errors=[
            RowError(
                row_index=-1,
                code="unrecognised_payload",
                message="Expected a JSON array of visits, or an object with a 'visits' array",
                hint="Example: {\"clinic_id\": \"C-01\", \"visits\": [ ... ]}",
            )
        ],
    )


def load_payload(raw: str | bytes) -> Any:
    """Parse JSON or newline-delimited JSON into a Python object."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    text = text.strip()
    if not text:
        raise BillingLogError(
            "The uploaded billing log is empty",
            code="empty_payload",
            errors=[
                RowError(row_index=-1, code="empty_payload", message="No content to parse")
            ],
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Fall back to JSON Lines before giving up.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(ln) for ln in lines]
            except json.JSONDecodeError:
                pass
        raise BillingLogError(
            f"The billing log is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            code="malformed_json",
            errors=[
                RowError(
                    row_index=-1,
                    code="malformed_json",
                    message=f"{exc.msg} at line {exc.lineno}, column {exc.colno}",
                    hint="Check for a trailing comma or an unquoted key near that position.",
                )
            ],
        ) from exc


# --------------------------------------------------------------------------
# Timestamp handling
# --------------------------------------------------------------------------


def parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp that must carry a UTC offset.

    A naive timestamp is rejected rather than assumed to be UTC: hour-of-day
    bucketing is one of the four things the clinic owner asks for, and silently
    guessing the zone would put the peak hour in the wrong bucket.
    """
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00") if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError(f"'{raw}' is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LookupError(f"'{raw}' has no timezone offset")
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Row -> Visit
# --------------------------------------------------------------------------


def _build_visit(row: VisitIn, ts: datetime) -> tuple[Visit, list[str]]:
    """Apply the money semantics to a type-valid row.

    Returns the visit plus any warning codes raised while normalising it. The
    semantics here are the single definition used by every downstream layer:

    * ``billed = max(0, gross - discount)`` and is always 0 for a refund visit.
    * ``outstanding`` is per-visit, so an overpayment on one visit can never
      cancel out an unpaid visit elsewhere in the day.
    * a refund's amount is stored as a positive magnitude regardless of the sign
      it arrived with.
    """
    warnings: list[str] = []

    items = tuple(
        LineItem(
            drug_name=li.drug_name.strip().upper(),
            qty=li.qty,
            unit_price_paise=li.unit_price_paise,
        )
        for li in row.line_items
    )
    gross = sum(li.line_total_paise for li in items)

    discount = row.discount_paise
    if discount > gross:
        warnings.append("discount_exceeds_total")

    if row.is_refund:
        billed = 0
        collected = 0
        refund = abs(row.amount_paid_paise)
        outstanding = 0
        overpaid = 0
    else:
        billed = max(0, gross - discount)
        collected = row.amount_paid_paise
        refund = 0
        outstanding = max(0, billed - collected)
        overpaid = max(0, collected - billed)
        if overpaid:
            warnings.append("overpayment")

    return (
        Visit(
            clinic_id=row.clinic_id,
            visit_id=row.visit_id,
            timestamp=ts,
            doctor_id=row.doctor_id,
            line_items=items,
            payment_mode=row.payment_mode,
            is_refund=row.is_refund,
            gross_paise=gross,
            discount_paise=discount,
            billed_paise=billed,
            collected_paise=collected,
            refund_paise=refund,
            outstanding_paise=outstanding,
            overpaid_paise=overpaid,
        ),
        warnings,
    )


_WARNING_MESSAGES = {
    "discount_exceeds_total": "Discount is larger than the line-item total; billed amount clamped to ₹0",
    "overpayment": "Amount paid exceeds the amount billed for this visit",
}


def parse_billing_log(payload: Any) -> ParsedDay:
    """Validate and normalise a whole day's billing log.

    Every row is checked independently so the caller receives the complete list
    of problems in one pass. Day-level invariants (one clinic, one date, unique
    visit ids) are checked after the rows, against the rows that survived.
    """
    rows = extract_rows(payload)

    visits: list[Visit] = []
    errors: list[RowError] = []
    warnings: list[RowWarning] = []
    seen_visit_ids: dict[str, int] = {}
    refund_sign_conventions: set[str] = set()

    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            errors.append(
                RowError(
                    row_index=index,
                    code="invalid_row_type",
                    message=f"Row must be a JSON object, got {type(raw_row).__name__}",
                )
            )
            continue

        peek_id = raw_row.get("visit_id") if isinstance(raw_row.get("visit_id"), str) else None

        try:
            row = VisitIn.model_validate(raw_row)
        except ValidationError as exc:
            for err in exc.errors():
                code, message, hint = _classify(err["loc"], err["type"], err["msg"])
                errors.append(
                    RowError(
                        row_index=index,
                        code=code,
                        message=message,
                        field=_loc_to_path(err["loc"]),
                        visit_id=peek_id,
                        hint=hint,
                    )
                )
            continue

        # Timestamp: parsed separately so the failure modes stay distinguishable.
        try:
            ts = parse_timestamp(row.timestamp)
        except LookupError:
            errors.append(
                RowError(
                    row_index=index,
                    code="missing_timezone",
                    message=f"timestamp '{row.timestamp}' has no timezone offset",
                    field="timestamp",
                    visit_id=row.visit_id,
                    hint="Use an explicit UTC offset, e.g. 2026-07-27T12:30:00Z.",
                )
            )
            continue
        except ValueError:
            errors.append(
                RowError(
                    row_index=index,
                    code="invalid_timestamp",
                    message=f"timestamp '{row.timestamp}' is not a valid ISO-8601 datetime",
                    field="timestamp",
                    visit_id=row.visit_id,
                    hint="Expected ISO 8601, e.g. 2026-07-27T12:30:00Z.",
                )
            )
            continue

        # Refund/payment sign coherence.
        if row.is_refund and row.amount_paid_paise == 0:
            errors.append(
                RowError(
                    row_index=index,
                    code="empty_refund",
                    message="A refund row must carry a non-zero amount_paid_paise",
                    field="amount_paid_paise",
                    visit_id=row.visit_id,
                    hint="Remove the row, or set the amount that was actually returned.",
                )
            )
            continue
        if not row.is_refund and row.amount_paid_paise < 0:
            errors.append(
                RowError(
                    row_index=index,
                    code="negative_payment",
                    message="amount_paid_paise is negative on a non-refund visit",
                    field="amount_paid_paise",
                    visit_id=row.visit_id,
                    hint="Set is_refund=true to record money going back to the patient.",
                )
            )
            continue

        if row.is_refund:
            refund_sign_conventions.add("negative" if row.amount_paid_paise < 0 else "positive")

        if row.visit_id in seen_visit_ids:
            errors.append(
                RowError(
                    row_index=index,
                    code="duplicate_visit_id",
                    message=(
                        f"visit_id '{row.visit_id}' already appeared at row "
                        f"{seen_visit_ids[row.visit_id]}"
                    ),
                    field="visit_id",
                    visit_id=row.visit_id,
                    hint="Visit ids must be unique; a repeated id would double-count the visit.",
                )
            )
            continue
        seen_visit_ids[row.visit_id] = index

        visit, warning_codes = _build_visit(row, ts)
        visits.append(visit)
        for code in warning_codes:
            warnings.append(
                RowWarning(
                    row_index=index,
                    code=code,
                    message=_WARNING_MESSAGES[code],
                    visit_id=visit.visit_id,
                )
            )

    _check_day_invariants(visits, errors, warnings, refund_sign_conventions)
    _check_drug_name_typos(visits, warnings)

    clinic_id = visits[0].clinic_id if visits else ""
    business_date = visits[0].business_date if visits else date.min

    return ParsedDay(
        clinic_id=clinic_id,
        business_date=business_date,
        visits=visits,
        warnings=warnings,
        errors=errors,
        rows_seen=len(rows),
    )


def _check_day_invariants(
    visits: list[Visit],
    errors: list[RowError],
    warnings: list[RowWarning],
    refund_sign_conventions: set[str],
) -> None:
    """Enforce the file-level rules: one clinic, one business date."""
    if not visits:
        return

    expected_clinic = visits[0].clinic_id
    expected_date = visits[0].business_date

    for index, visit in enumerate(visits):
        if visit.clinic_id != expected_clinic:
            errors.append(
                RowError(
                    row_index=index,
                    code="clinic_id_mismatch",
                    message=(
                        f"clinic_id '{visit.clinic_id}' does not match "
                        f"'{expected_clinic}' used by the rest of the file"
                    ),
                    field="clinic_id",
                    visit_id=visit.visit_id,
                    hint="A billing log covers a single clinic; split the file per clinic.",
                )
            )
        if visit.business_date != expected_date:
            errors.append(
                RowError(
                    row_index=index,
                    code="multiple_business_dates",
                    message=(
                        f"timestamp falls on {visit.business_date.isoformat()} but the file "
                        f"starts on {expected_date.isoformat()}"
                    ),
                    field="timestamp",
                    visit_id=visit.visit_id,
                    hint="An end-of-day log covers a single UTC date; split the file per day.",
                )
            )

    if len(refund_sign_conventions) > 1:
        warnings.append(
            RowWarning(
                row_index=-1,
                code="mixed_refund_sign_convention",
                message=(
                    "Refunds appear with both positive and negative amount_paid_paise; "
                    "all were normalised to a positive refund magnitude"
                ),
            )
        )


#: How alike two drug names must be before one is called out as a likely typo.
#: Tuned to catch a dropped or transposed letter (PARACETMOL vs PARACETAMOL,
#: ratio 0.96) while leaving genuinely different drugs alone — ATORVASTATIN and
#: ROSUVASTATIN sit at 0.71, and METFORMIN against any other name is lower still.
DRUG_NAME_SIMILARITY = 0.88


def _check_drug_name_typos(visits: list[Visit], warnings: list[RowWarning]) -> None:
    """Flag near-identical drug names without merging them.

    A misspelled drug splits one medicine across two rows of the rankings, which
    quietly understates both. The fix belongs to whoever owns the data, though —
    auto-correcting here would silently move revenue between line items on a
    guess, so this only ever raises a warning and the totals stay as recorded.
    """
    totals: dict[str, int] = defaultdict(int)
    for visit in visits:
        if visit.is_refund:
            continue
        for item in visit.line_items:
            totals[item.drug_name] += item.qty

    names = sorted(totals)
    for i, name in enumerate(names):
        for other in names[i + 1 :]:
            if abs(len(name) - len(other)) > 3:
                continue
            if SequenceMatcher(None, name, other).ratio() < DRUG_NAME_SIMILARITY:
                continue
            # The rarer spelling is the likely mistake.
            suspect, canonical = (
                (name, other) if totals[name] <= totals[other] else (other, name)
            )
            warnings.append(
                RowWarning(
                    row_index=-1,
                    code="possible_drug_name_typo",
                    message=(
                        f"'{suspect}' ({totals[suspect]} units) closely resembles "
                        f"'{canonical}' ({totals[canonical]} units); they are being "
                        f"counted as separate medicines"
                    ),
                )
            )


def visits_from_rows(rows: Iterable[Any]) -> ParsedDay:
    """Convenience wrapper used by the storage layer when rehydrating a day."""
    return parse_billing_log(list(rows))
