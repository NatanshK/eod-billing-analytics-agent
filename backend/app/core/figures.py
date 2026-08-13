"""The figure registry — the contract that keeps the narrative grounded.

Every number the narrative is allowed to mention is enumerated here, once, with
three things attached: the token the model writes instead of the digits, the
exact string that token renders to, and the report field it came from.

That single structure serves three purposes which therefore cannot disagree:

* it is the **vocabulary** handed to the model (tokens only, no digits),
* it is the **substitution table** used to render the final text, and
* it is the **traced-figures payload** the UI panel displays as proof.

The design consequence is the important one: because the model emits
``{{total_billed}}`` rather than "₹42,850", a hallucinated figure is not a
number that slips through a checker — it is a token that does not resolve, and
resolution failure is a hard error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .analytics import Analytics
from .money import format_paise
from .reconciliation import Reconciliation


@dataclass(frozen=True)
class Figure:
    """One quotable number, traceable back to the report field it came from."""

    key: str
    display: str
    field_path: str
    description: str
    value_paise: int | None = None
    value_number: int | None = None

    @property
    def token(self) -> str:
        return "{{" + self.key + "}}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "display": self.display,
            "field_path": self.field_path,
        }
        if self.value_paise is not None:
            out["value_paise"] = self.value_paise
        if self.value_number is not None:
            out["value_number"] = self.value_number
        return out


class FigureRegistry:
    """An ordered, immutable set of figures keyed by token name."""

    def __init__(self, figures: list[Figure]) -> None:
        self._figures = figures
        self._by_key = {f.key: f for f in figures}

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __iter__(self):
        return iter(self._figures)

    def __len__(self) -> int:
        return len(self._figures)

    def get(self, key: str) -> Figure | None:
        return self._by_key.get(key)

    @property
    def keys(self) -> list[str]:
        return [f.key for f in self._figures]

    @property
    def displays(self) -> set[str]:
        """Every string a rendered narrative is permitted to contain."""
        return {f.display for f in self._figures}

    def to_list(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self._figures]

    def used(self, keys: list[str]) -> list[Figure]:
        """The figures actually cited, in registry order, de-duplicated."""
        wanted = set(keys)
        return [f for f in self._figures if f.key in wanted]


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def build_registry(
    reconciliation: Reconciliation,
    analytics: Analytics,
    *,
    clinic_id: str,
    business_date: date,
    clinic_name: str | None = None,
    top_n: int = 3,
) -> FigureRegistry:
    """Enumerate every figure the narrative may cite for this clinic-day.

    Only figures that actually mean something are included. A day with no
    refunds contributes no refund figure, so the model has no way to write a
    sentence about refunds that did not happen — the vocabulary simply lacks the
    word.

    The clinic name and business date are registry entries too, not free text.
    Both are report fields, and both can contain digits — ``CLINIC-MEHTA-001``,
    ``27 Jul 2026`` — so routing them through the registry keeps the "every digit
    is traced" rule true without carving out an exception for labels.
    """
    figures: list[Figure] = []

    def add(
        key: str,
        display: str,
        field_path: str,
        description: str,
        *,
        paise: int | None = None,
        number: int | None = None,
    ) -> None:
        figures.append(
            Figure(
                key=key,
                display=display,
                field_path=field_path,
                description=description,
                value_paise=paise,
                value_number=number,
            )
        )

    # --- identity ---------------------------------------------------------
    add(
        "clinic_name",
        clinic_name or clinic_id,
        "clinic_id",
        "The clinic this report covers",
    )
    add(
        "business_date",
        business_date.strftime("%-d %b %Y"),
        "business_date",
        "The day this report covers",
    )

    # --- headline reconciliation ------------------------------------------
    add(
        "total_billed",
        format_paise(reconciliation.total_billed_paise),
        "reconciliation.total_billed_paise",
        "Total amount billed across the day",
        paise=reconciliation.total_billed_paise,
    )
    add(
        "total_collected",
        format_paise(reconciliation.total_collected_paise),
        "reconciliation.total_collected_paise",
        "Total amount actually collected, before refunds",
        paise=reconciliation.total_collected_paise,
    )
    add(
        "outstanding",
        format_paise(reconciliation.outstanding_paise),
        "reconciliation.outstanding_paise",
        "Amount still owed, summed per visit",
        paise=reconciliation.outstanding_paise,
    )

    if reconciliation.refunds_paise:
        add(
            "refunds",
            format_paise(reconciliation.refunds_paise),
            "reconciliation.refunds_paise",
            "Total refunded to patients",
            paise=reconciliation.refunds_paise,
        )
        add(
            "refund_count",
            _plural(reconciliation.refund_count, "refund"),
            "reconciliation.refund_count",
            "How many refunds were issued",
            number=reconciliation.refund_count,
        )

    add(
        "visit_count",
        _plural(reconciliation.billable_visit_count, "visit"),
        "reconciliation.billable_visit_count",
        "Number of billable visits (refund rows excluded)",
        number=reconciliation.billable_visit_count,
    )

    if reconciliation.pending_visit_count:
        add(
            "pending_visit_count",
            _plural(reconciliation.pending_visit_count, "visit"),
            "reconciliation.pending_visit_count",
            "How many visits still have money outstanding",
            number=reconciliation.pending_visit_count,
        )

    if reconciliation.collection_rate_pct is not None:
        add(
            "collection_rate",
            f"{reconciliation.collection_rate_pct}%",
            "reconciliation.collection_rate_pct",
            "Collected as a percentage of billed",
            number=reconciliation.collection_rate_pct,
        )

    # --- payment modes ----------------------------------------------------
    for mode in reconciliation.by_mode:
        if mode.billed_paise == 0 and mode.collected_paise == 0:
            continue  # an unused mode has nothing to say
        add(
            f"{mode.mode}_collected",
            format_paise(mode.collected_paise),
            f"reconciliation.by_mode[{mode.mode}].collected_paise",
            f"Collected via {mode.mode}",
            paise=mode.collected_paise,
        )

    # --- analytics --------------------------------------------------------
    if analytics.peak_hour is not None:
        peak = analytics.peak_hour
        add(
            "peak_hour",
            peak.label,
            f"analytics.revenue_by_hour[{peak.hour}].label",
            "The busiest hour of the day by revenue",
        )
        add(
            "peak_hour_revenue",
            format_paise(peak.revenue_paise),
            f"analytics.revenue_by_hour[{peak.hour}].revenue_paise",
            "Revenue collected during the busiest hour",
            paise=peak.revenue_paise,
        )

    for drug in analytics.top_by_qty[:top_n]:
        add(
            f"top_qty_{drug.rank}_name",
            drug.drug_name,
            f"analytics.top_by_qty[{drug.rank - 1}].drug_name",
            f"Rank {drug.rank} medicine by units sold",
        )
        add(
            f"top_qty_{drug.rank}_units",
            _plural(drug.qty, "unit"),
            f"analytics.top_by_qty[{drug.rank - 1}].qty",
            f"Units sold of the rank {drug.rank} medicine by quantity",
            number=drug.qty,
        )

    for drug in analytics.top_by_revenue[:top_n]:
        add(
            f"top_revenue_{drug.rank}_name",
            drug.drug_name,
            f"analytics.top_by_revenue[{drug.rank - 1}].drug_name",
            f"Rank {drug.rank} medicine by revenue",
        )
        add(
            f"top_revenue_{drug.rank}_value",
            format_paise(drug.revenue_paise),
            f"analytics.top_by_revenue[{drug.rank - 1}].revenue_paise",
            f"Revenue from the rank {drug.rank} medicine by revenue",
            paise=drug.revenue_paise,
        )

    return FigureRegistry(figures)


#: Metrics the owner might expect but which this data cannot support. The brief
#: is explicit: say so plainly rather than approximating one as another. Cost
#: price is absent from the billing schema, so every profit-shaped question is
#: unanswerable from this input — not merely hard.
UNAVAILABLE_METRICS: tuple[tuple[str, str], ...] = (
    ("profit", "line items carry a selling price but no cost price"),
    ("margin", "no cost price means margin cannot be derived"),
    ("inventory on hand", "the billing log records sales, not stock levels"),
    ("patient counts by doctor", "not part of this report"),
)
