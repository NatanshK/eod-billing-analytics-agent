"""Daily analytics: revenue by hour and the two medicine rankings.

Like reconciliation, a pure function of the visit list. No LLM, no network, no
clock.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .money import format_paise
from .parsing import Visit

DEFAULT_TOP_N = 5

#: Line-item revenue is counted before visit-level discounts. A discount applies
#: to the visit as a whole and cannot be fairly attributed across its drugs, so
#: splitting it would invent a number. Reported in the API response rather than
#: left as a silent assumption.
REVENUE_BASIS = "gross_line_item_value_excluding_visit_discounts"


def hour_label_short(hour: int) -> str:
    """9 -> "9am", 12 -> "12pm", 0 -> "12am"."""
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display}{suffix}"


def hour_label(hour: int) -> str:
    """12 -> "12pm–1pm" — the form the dashboard and narrative both quote."""
    return f"{hour_label_short(hour)}–{hour_label_short((hour + 1) % 24)}"


@dataclass
class HourBucket:
    hour: int
    revenue_paise: int
    is_peak: bool = False

    @property
    def label(self) -> str:
        return hour_label(self.hour)

    @property
    def short_label(self) -> str:
        return hour_label_short(self.hour)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hour": self.hour,
            "label": self.label,
            "short_label": self.short_label,
            "revenue_paise": self.revenue_paise,
            "revenue_display": format_paise(self.revenue_paise),
            "is_peak": self.is_peak,
        }


@dataclass
class DrugRank:
    rank: int
    drug_name: str
    qty: int
    revenue_paise: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "drug_name": self.drug_name,
            "qty": self.qty,
            "revenue_paise": self.revenue_paise,
            "revenue_display": format_paise(self.revenue_paise),
        }


@dataclass
class Analytics:
    revenue_by_hour: list[HourBucket]
    peak_hour: HourBucket | None
    top_by_qty: list[DrugRank] = field(default_factory=list)
    top_by_revenue: list[DrugRank] = field(default_factory=list)
    distinct_drug_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "revenue_by_hour": [b.to_dict() for b in self.revenue_by_hour],
            "peak_hour": self.peak_hour.to_dict() if self.peak_hour else None,
            "top_by_qty": [d.to_dict() for d in self.top_by_qty],
            "top_by_revenue": [d.to_dict() for d in self.top_by_revenue],
            "distinct_drug_count": self.distinct_drug_count,
            "revenue_basis": REVENUE_BASIS,
        }


def compute_analytics(visits: list[Visit], top_n: int = DEFAULT_TOP_N) -> Analytics:
    """Bucket revenue by hour and rank medicines two independent ways.

    * Hourly revenue is collected money, with refunds subtracting from the hour
      they were issued in — what the owner means by "which hour did the most
      business".
    * The rankings exclude refunded visits, since a returned drug did not move.
      They are computed separately and may disagree, which is the point of
      showing both.
    """
    hourly: dict[int, int] = defaultdict(int)
    qty_by_drug: dict[str, int] = defaultdict(int)
    revenue_by_drug: dict[str, int] = defaultdict(int)

    for visit in visits:
        hourly[visit.hour] += visit.collected_paise - visit.refund_paise
        if visit.is_refund:
            continue
        for item in visit.line_items:
            qty_by_drug[item.drug_name] += item.qty
            revenue_by_drug[item.drug_name] += item.line_total_paise

    buckets = _build_buckets(hourly)
    peak = _pick_peak(buckets)

    return Analytics(
        revenue_by_hour=buckets,
        peak_hour=peak,
        top_by_qty=_rank(qty_by_drug, revenue_by_drug, key="qty", top_n=top_n),
        top_by_revenue=_rank(qty_by_drug, revenue_by_drug, key="revenue", top_n=top_n),
        distinct_drug_count=len(qty_by_drug),
    )


def _build_buckets(hourly: dict[int, int]) -> list[HourBucket]:
    """Return a contiguous span covering the clinic's active hours.

    Empty hours between the first and last activity are still emitted, so a quiet
    mid-afternoon reads as a gap rather than closing up and distorting the shape
    of the day.
    """
    if not hourly:
        return []
    first, last = min(hourly), max(hourly)
    return [HourBucket(hour=h, revenue_paise=hourly.get(h, 0)) for h in range(first, last + 1)]


def _pick_peak(buckets: list[HourBucket]) -> HourBucket | None:
    """Busiest hour by revenue; ties resolve to the earlier hour.

    An all-zero or all-negative day has no meaningful peak, and claiming one
    would put a meaningless number in the narrative.
    """
    if not buckets:
        return None
    best = max(buckets, key=lambda b: (b.revenue_paise, -b.hour))
    if best.revenue_paise <= 0:
        return None
    best.is_peak = True
    return best


def _rank(
    qty_by_drug: dict[str, int],
    revenue_by_drug: dict[str, int],
    *,
    key: str,
    top_n: int,
) -> list[DrugRank]:
    """Rank drugs by one metric, tie-broken alphabetically for determinism."""
    source = qty_by_drug if key == "qty" else revenue_by_drug
    ordered = sorted(source.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        DrugRank(
            rank=i,
            drug_name=name,
            qty=qty_by_drug.get(name, 0),
            revenue_paise=revenue_by_drug.get(name, 0),
        )
        for i, (name, _) in enumerate(ordered[:top_n], start=1)
    ]
