"""Integer-paise money handling.

Floats never appear: they are lossy under addition, and these totals are compared
for exact equality against the narrative's traced figures. frontend/src/lib/money.ts
is a port of this file — if the two formatters diverge, a correct narrative looks
like a grounding failure.
"""

from __future__ import annotations

RUPEE = "₹"


def group_indian(digits: str) -> str:
    """Group digits the Indian way: 1234567 -> 12,34,567.

    Last three digits form one group; everything above is grouped in twos.
    """
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def format_paise(paise: int) -> str:
    """319000 -> "₹3,190". 4285050 -> "₹42,850.50". Minus goes before the symbol."""
    if not isinstance(paise, int) or isinstance(paise, bool):
        raise TypeError(f"format_paise expects int paise, got {type(paise).__name__}")

    sign = "-" if paise < 0 else ""
    whole, remainder = divmod(abs(paise), 100)
    body = group_indian(str(whole))
    if remainder:
        body = f"{body}.{remainder:02d}"
    return f"{sign}{RUPEE}{body}"


def percent(numerator: int, denominator: int) -> int | None:
    """Integer percentage, or None when nothing was billed.

    None rather than 0: an empty day has an undefined collection rate, and "0%"
    reads as a disastrous day rather than a quiet one.
    """
    if denominator == 0:
        return None
    return round(numerator * 100 / denominator)
