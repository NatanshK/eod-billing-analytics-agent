"""Domain error types.

Every rejection carries a machine-readable code, the field at fault, and a hint
telling the front desk what to do about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any


@dataclass(frozen=True)
class RowError:
    """A single rejected row, addressed precisely enough to fix by hand."""

    row_index: int
    code: str
    message: str
    field: str | None = None
    visit_id: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "row_index": self.row_index,
            "code": self.code,
            "message": self.message,
        }
        if self.field is not None:
            out["field"] = self.field
        if self.visit_id is not None:
            out["visit_id"] = self.visit_id
        if self.hint is not None:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class RowWarning:
    """Something odd but legitimate: accepted, surfaced, never silently dropped.

    Makes clamping behaviour — a discount larger than the line total, an
    overpayment — visible in the response instead of quietly changing the totals.
    """

    row_index: int
    code: str
    message: str
    visit_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "row_index": self.row_index,
            "code": self.code,
            "message": self.message,
        }
        if self.visit_id is not None:
            out["visit_id"] = self.visit_id
        return out


class BillingLogError(Exception):
    """Raised when a payload cannot be ingested. Maps to a 422, never a 500."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_billing_log",
        errors: list[RowError] | None = None,
        valid_rows: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.errors: list[RowError] = errors or []
        self.valid_rows = valid_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "valid_rows": self.valid_rows,
            "rejected_rows": len(self.errors),
            "errors": [e.to_dict() for e in self.errors],
        }


class DayNotFoundError(Exception):
    """No billing day stored for this clinic/date. Maps to a 404."""

    def __init__(self, clinic_id: str, business_date: str) -> None:
        super().__init__(f"No billing log ingested for {clinic_id} on {business_date}")
        self.clinic_id = clinic_id
        self.business_date = business_date


class NarrativeNotCachedError(Exception):
    """A cached narrative was requested but none matches the current data hash."""

    def __init__(self, clinic_id: str, business_date: str, *, stale: bool = False) -> None:
        super().__init__("No current narrative cached for this day")
        self.clinic_id = clinic_id
        self.business_date = business_date
        self.stale = stale
