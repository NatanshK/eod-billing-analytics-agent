"""Prompt construction for the narrative layer.

The prompt does not ask the model to be careful with numbers. It removes its
ability to write them: the vocabulary it is given contains tokens, and the
validator rejects digits. Prompt wording is a convenience for getting good prose
on the first try — the guarantee lives in :mod:`validator`, not here.
"""

from __future__ import annotations

import json

from ..core.figures import UNAVAILABLE_METRICS, FigureRegistry

SYSTEM_PROMPT = """\
You write short end-of-day summaries for Indian clinic owners, delivered over \
WhatsApp. Warm, plain, and direct — the way a competent practice manager would \
message their doctor at closing time.

You will be given a list of FIGURES. Each figure has a key and the value it \
renders to.

The single hard rule: NEVER write a digit. Not a rupee amount, not a count, not \
a percentage, not a date. To refer to any number, write its key wrapped in double \
braces, exactly like {{total_billed}}. The system substitutes the real value \
afterwards.

- Use only keys from the FIGURES list. A key that is not on the list will be \
rejected and your whole response discarded.
- Do not restate a figure's value in words either ("about forty thousand rupees" \
is as wrong as "40000").
- If something is not in the FIGURES list, you do not know it. Do not estimate, \
infer, or imply it.
- Some metrics cannot be computed from this data at all; they are listed under \
UNAVAILABLE. Mention this plainly in the `caveat` field and nowhere else.

Return a single JSON object, no markdown fence:
{
  "greeting":   "one short opening line",
  "body_lines": ["3 to 5 short lines, one fact each"],
  "caveat":     "one line naming what could not be computed and why"
}
"""


def build_user_prompt(registry: FigureRegistry) -> str:
    """Render the figure vocabulary and the unavailable-metric list."""
    figures = [
        {"key": f.key, "renders_to": f.display, "means": f.description} for f in registry
    ]
    unavailable = [{"metric": name, "why": why} for name, why in UNAVAILABLE_METRICS]

    return (
        "FIGURES — the only numbers that exist for this day:\n"
        f"{json.dumps(figures, indent=2, ensure_ascii=False)}\n\n"
        "UNAVAILABLE — cannot be computed from a billing log:\n"
        f"{json.dumps(unavailable, indent=2)}\n\n"
        "Write the summary now. Reference figures as {{key}}. Write no digits."
    )


def build_messages(registry: FigureRegistry) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(registry)},
    ]


REPAIR_INSTRUCTION = (
    "Your previous response was rejected: {reason}. "
    "Return only a single valid JSON object with the keys greeting, body_lines and "
    "caveat. Reference every number as {{key}} using the FIGURES list. Write no digits."
)


def build_repair_messages(
    registry: FigureRegistry, previous: str, reason: str
) -> list[dict[str, str]]:
    """One corrective round-trip before giving up on the model.

    Worth exactly one retry: a model that ignores the contract twice is not going
    to honour it on the third attempt, and the owner is waiting.
    """
    return [
        *build_messages(registry),
        {"role": "assistant", "content": previous[:2000]},
        {"role": "user", "content": REPAIR_INSTRUCTION.format(reason=reason)},
    ]
