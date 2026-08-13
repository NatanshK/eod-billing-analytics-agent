"""Money handling.

Every monetary value in this service is an ``int`` number of paise. Rupee floats
never appear: they are lossy under addition, and the reconciliation totals here
are compared for exact equality against the narrative layer's traced figures.

The formatter in this module is the *only* place paise become a display string.
``frontend/src/lib/money.ts`` is a character-for-character port of it — the
Traced Figures panel matches rendered strings, so any divergence between the two
would surface as a false grounding failure.
"""

from __future__ import annotations

RUPEE = "₹"


def group_indian(digits: str) -> str:
    """Group an integer digit-string in the Indian system: 1234567 -> 12,34,567.

    The last three digits form one group; everything above is grouped in twos.
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
    """Render integer paise as a rupee string.

    Whole rupees print without a decimal part (``4285000 -> "₹42,850"``); a
    non-zero paise remainder prints two decimal places (``4285050 -> "₹42,850.50"``).
    Negative amounts carry a leading minus before the currency symbol.
    """
    if not isinstance(paise, int) or isinstance(paise, bool):
        raise TypeError(f"format_paise expects int paise, got {type(paise).__name__}")

    sign = "-" if paise < 0 else ""
    whole, remainder = divmod(abs(paise), 100)
    body = group_indian(str(whole))
    if remainder:
        body = f"{body}.{remainder:02d}"
    return f"{sign}{RUPEE}{body}"


def percent(numerator: int, denominator: int) -> int | None:
    """Integer percentage, or ``None`` when the denominator is zero.

    Returning ``None`` rather than 0 matters: a day with nothing billed has an
    *undefined* collection rate, and the UI must say so instead of showing "0%",
    which reads as a catastrophically bad day rather than an empty one.
    """
    if denominator == 0:
        return None
    return round(numerator * 100 / denominator)
