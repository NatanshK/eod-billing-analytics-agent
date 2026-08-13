"""End-of-day reconciliation.

Pure functions over a list of :class:`Visit`. This module is the ground truth the
brief describes: it never calls an LLM, never reaches for the network, and never
reads global state. Given the same visits it returns the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .money import format_paise, percent
from .parsing import PAYMENT_MODES, Visit


@dataclass
class ModeTotals:
    """One row of the payment-mode breakdown table."""

    mode: str
    billed_paise: int = 0
    collected_paise: int = 0
    outstanding_paise: int = 0
    refunds_paise: int = 0
    visit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "visit_count": self.visit_count,
            "billed_paise": self.billed_paise,
            "billed_display": format_paise(self.billed_paise),
            "collected_paise": self.collected_paise,
            "collected_display": format_paise(self.collected_paise),
            "outstanding_paise": self.outstanding_paise,
            "outstanding_display": format_paise(self.outstanding_paise),
            "refunds_paise": self.refunds_paise,
            "refunds_display": format_paise(self.refunds_paise),
        }


@dataclass
class Reconciliation:
    """The four headline figures plus their per-mode decomposition."""

    total_billed_paise: int
    total_collected_paise: int
    outstanding_paise: int
    refunds_paise: int
    net_collected_paise: int
    overpayments_paise: int

    visit_count: int
    billable_visit_count: int
    pending_visit_count: int
    refund_count: int
    collection_rate_pct: int | None

    by_mode: list[ModeTotals] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_billed_paise": self.total_billed_paise,
            "total_billed_display": format_paise(self.total_billed_paise),
            "total_collected_paise": self.total_collected_paise,
            "total_collected_display": format_paise(self.total_collected_paise),
            "outstanding_paise": self.outstanding_paise,
            "outstanding_display": format_paise(self.outstanding_paise),
            "refunds_paise": self.refunds_paise,
            "refunds_display": format_paise(self.refunds_paise),
            "net_collected_paise": self.net_collected_paise,
            "net_collected_display": format_paise(self.net_collected_paise),
            "overpayments_paise": self.overpayments_paise,
            "overpayments_display": format_paise(self.overpayments_paise),
            "visit_count": self.visit_count,
            "billable_visit_count": self.billable_visit_count,
            "pending_visit_count": self.pending_visit_count,
            "refund_count": self.refund_count,
            "collection_rate_pct": self.collection_rate_pct,
            "by_mode": [m.to_dict() for m in self.by_mode],
        }


class ReconciliationInvariantError(RuntimeError):
    """The per-mode breakdown failed to sum back to the headline totals.

    This is an internal consistency bug, not a data problem. Raising beats
    serving a report whose table disagrees with its own stat cards.
    """


def reconcile(visits: list[Visit]) -> Reconciliation:
    """Compute the EOD reconciliation for one clinic-day.

    Definitions worth stating, because the brief leaves them open and the
    narrative layer quotes these numbers verbatim:

    * ``total_collected`` is **gross** of refunds; ``refunds`` is reported
      alongside it and ``net_collected`` carries the difference. This keeps the
      "billed − collected = outstanding" identity readable on the dashboard.
    * ``outstanding`` sums the per-visit shortfall, so an overpaid visit cannot
      mask an unpaid one. Any excess is reported separately as ``overpayments``.
    * Refund visits contribute nothing to billed or outstanding — only to
      ``refunds``.
    """
    modes: dict[str, ModeTotals] = {m: ModeTotals(mode=m) for m in PAYMENT_MODES}

    total_billed = 0
    total_collected = 0
    total_outstanding = 0
    total_refunds = 0
    total_overpaid = 0
    pending_visits = 0
    refund_count = 0
    billable_visits = 0

    for visit in visits:
        bucket = modes.setdefault(visit.payment_mode.value, ModeTotals(mode=visit.payment_mode.value))
        bucket.visit_count += 1

        total_billed += visit.billed_paise
        total_collected += visit.collected_paise
        total_outstanding += visit.outstanding_paise
        total_refunds += visit.refund_paise
        total_overpaid += visit.overpaid_paise

        bucket.billed_paise += visit.billed_paise
        bucket.collected_paise += visit.collected_paise
        bucket.outstanding_paise += visit.outstanding_paise
        bucket.refunds_paise += visit.refund_paise

        if visit.is_refund:
            refund_count += 1
        else:
            billable_visits += 1
            if visit.outstanding_paise > 0:
                pending_visits += 1

    result = Reconciliation(
        total_billed_paise=total_billed,
        total_collected_paise=total_collected,
        outstanding_paise=total_outstanding,
        refunds_paise=total_refunds,
        net_collected_paise=total_collected - total_refunds,
        overpayments_paise=total_overpaid,
        visit_count=len(visits),
        billable_visit_count=billable_visits,
        pending_visit_count=pending_visits,
        refund_count=refund_count,
        collection_rate_pct=percent(total_collected, total_billed),
        by_mode=[modes[m] for m in PAYMENT_MODES if m in modes],
    )

    _assert_invariants(result)
    return result


def _assert_invariants(r: Reconciliation) -> None:
    """The mode columns must sum to the headline totals, exactly."""
    checks = (
        ("billed", sum(m.billed_paise for m in r.by_mode), r.total_billed_paise),
        ("collected", sum(m.collected_paise for m in r.by_mode), r.total_collected_paise),
        ("outstanding", sum(m.outstanding_paise for m in r.by_mode), r.outstanding_paise),
        ("refunds", sum(m.refunds_paise for m in r.by_mode), r.refunds_paise),
    )
    for label, got, expected in checks:
        if got != expected:
            raise ReconciliationInvariantError(
                f"payment-mode {label} sums to {got} but the total is {expected}"
            )
