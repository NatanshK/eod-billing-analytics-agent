"""Prompt construction for the narrative layer.

The prompt is a convenience for getting good prose on the first try. It is not
where the grounding guarantee lives — that is in validator.py, which rejects any
digit the model writes regardless of what it was asked.
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
- Never spell a number out either. "three refunds" is exactly as wrong as "3 \
refunds" — write {{refund_count}}. This includes one, two, three, ten, hundred, \
thousand, lakh, crore.
- Each figure already renders as a complete phrase **including its unit**: \
`visit_count` renders to "18 visits", not "18". So write "across \
{{visit_count}}", never "across {{visit_count}} visits" — that would read as \
"18 visits visits". Check `renders_to` before adding a word after a key.
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
    """One corrective round-trip before giving up.

    Worth exactly one retry: a model that ignores the contract twice will not
    honour it on the third, and the owner is waiting.
    """
    return [
        *build_messages(registry),
        {"role": "assistant", "content": previous[:2000]},
        {"role": "user", "content": REPAIR_INSTRUCTION.format(reason=reason)},
    ]
