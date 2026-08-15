"""Grounding enforcement for model output.

Four gates, in order. A draft failing any of them is discarded whole; a summary
that is half-checked is not checked.

The gates work because of the shape of the contract: the model writes
{{total_billed}}, never ₹42,850. The question is never "does this number look
like one of ours?" but "does this token resolve?".

Gate 4 re-scans the rendered text and requires every digit to sit inside a span
substitution produced — the same audit a grader would run, run on ourselves, so
a bug in substitution cannot put an untraceable digit in front of the owner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.figures import Figure, FigureRegistry

TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
DIGIT_RE = re.compile(r"\d")

#: Quantities spelled out as words. Found in live output: asked for no digits,
#: the model wrote "Three refunds were processed" instead of citing
#: {{refund_count}}. The figure was right, but nothing had checked it.
#:
#: "one" is included despite being a common pronoun, with the two idioms that
#: would trip it excluded below. A false positive costs a repair round-trip and
#: at worst the fallback — still correct output. A false negative puts an
#: unverified number in front of the owner.
NUMBER_WORD_RE = re.compile(
    r"\b(?!one\s+of\b)(?<!no\s)("
    r"zero|nil|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|lakhs|crore|"
    r"crores|million|billion|dozen"
    r")\b",
    re.IGNORECASE,
)

#: Claims this dataset cannot support. Permitted in the caveat, where the model
#: is supposed to say the number is unavailable, and nowhere else.
UNSUPPORTED_CLAIM_RE = re.compile(
    r"\b(profits?|margins?|markups?|cogs|cost\s+price|purchase\s+price|net\s+worth)\b",
    re.IGNORECASE,
)

MAX_BODY_LINES = 8
MAX_LINE_CHARS = 240


class GroundingError(Exception):
    """A draft failed a gate. Carries which one, for the fallback reason."""

    def __init__(self, gate: str, reason: str) -> None:
        super().__init__(f"{gate}: {reason}")
        self.gate = gate
        self.reason = reason


class NarrativeDraft(BaseModel):
    """The shape the model is asked to return."""

    model_config = ConfigDict(extra="ignore")

    greeting: str = Field(min_length=1, max_length=MAX_LINE_CHARS)
    body_lines: list[str] = Field(min_length=1, max_length=MAX_BODY_LINES)
    caveat: str = Field(default="", max_length=MAX_LINE_CHARS)


@dataclass
class GroundedNarrative:
    """A narrative whose every figure is traceable to the report."""

    lines: list[str]
    caveat: str
    figures_used: list[Figure] = field(default_factory=list)
    source: str = "llm"
    fallback_reason: str | None = None
    generated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrative_lines": self.lines,
            "caveat": self.caveat,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "generated_at": self.generated_at,
            "traced_figures": [f.to_dict() for f in self.figures_used],
        }


# --------------------------------------------------------------------------
# Gate 1 — parse
# --------------------------------------------------------------------------


def strip_code_fences(text: str) -> str:
    """Models wrap JSON in ``` fences often enough to be worth handling."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
    return re.sub(r"\s*```$", "", without_open).strip()


def parse_json(raw: str) -> dict:
    if not raw or not raw.strip():
        raise GroundingError("parse", "the model returned an empty response")

    text = strip_code_fences(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GroundingError("parse", f"response was not valid JSON ({exc.msg})") from exc

    if not isinstance(parsed, dict):
        raise GroundingError("parse", f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


# --------------------------------------------------------------------------
# Gate 2 — schema
# --------------------------------------------------------------------------


def validate_schema(payload: dict) -> NarrativeDraft:
    try:
        return NarrativeDraft.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"]) or "response"
        raise GroundingError("schema", f"{location}: {first['msg']}") from exc


# --------------------------------------------------------------------------
# Gate 3 — pre-substitution audit
# --------------------------------------------------------------------------


def audit_draft(draft: NarrativeDraft, registry: FigureRegistry) -> list[str]:
    """Check the draft before any substitution happens.

    Returns the token keys cited, in order of first appearance. Raises if the
    model wrote a literal number, cited a figure that does not exist, or made a
    claim this dataset cannot support.
    """
    authored = [draft.greeting, *draft.body_lines]
    seen: list[str] = []

    for index, text in enumerate(authored + [draft.caveat]):
        where = _describe(index, len(authored))
        is_caveat = index == len(authored)

        for key in TOKEN_RE.findall(text):
            if key not in registry:
                raise GroundingError(
                    "tokens",
                    f"{where} cites '{{{{{key}}}}}', which is not a figure in this report",
                )
            if key not in seen:
                seen.append(key)

        # Any number that is not part of a token is one the model invented —
        # whether it wrote it in digits or spelled it out.
        residue = TOKEN_RE.sub("", text)
        digit = DIGIT_RE.search(residue)
        if digit:
            raise GroundingError(
                "literal_number",
                f"{where} contains the literal digit '{digit.group()}' outside a figure token",
            )

        spelled = NUMBER_WORD_RE.search(residue)
        if spelled:
            raise GroundingError(
                "spelled_number",
                f"{where} spells out the number '{spelled.group()}' instead of citing a figure",
            )

        if not is_caveat:
            match = UNSUPPORTED_CLAIM_RE.search(text)
            if match:
                raise GroundingError(
                    "unsupported_claim",
                    f"{where} claims '{match.group()}', which this billing data cannot support",
                )

    return seen


def _describe(index: int, authored_count: int) -> str:
    if index == 0:
        return "the greeting"
    if index < authored_count:
        return f"body line {index}"
    return "the caveat"


# --------------------------------------------------------------------------
# Gate 4 — post-substitution audit
# --------------------------------------------------------------------------


def render(text: str, registry: FigureRegistry) -> tuple[str, list[tuple[int, int]]]:
    """Substitute tokens, recording the span each substitution occupies.

    The spans make the final audit exact: rather than guessing whether a digit
    looks like a report figure, we know which characters came from the registry.
    """
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    position = 0

    for match in TOKEN_RE.finditer(text):
        literal = text[cursor : match.start()]
        out.append(literal)
        position += len(literal)

        figure = registry.get(match.group(1))
        if figure is None:  # pragma: no cover - gate 3 already rejected this
            raise GroundingError("tokens", f"unknown figure '{match.group(1)}'")

        out.append(figure.display)
        spans.append((position, position + len(figure.display)))
        position += len(figure.display)
        cursor = match.end()

    out.append(text[cursor:])
    return "".join(out), spans


def assert_no_repeated_unit(rendered: str, spans: list[tuple[int, int]], where: str) -> None:
    """Catch a unit written twice because the figure already carried it.

    Figures render as complete phrases — visit_count is "18 visits", not "18" —
    so "{{visit_count}} visits" produces "18 visits visits". The number is traced
    and correct; the prose is not sendable. Same treatment as any rejected draft.
    """
    for start, end in spans:
        trailing = rendered[:end].split()
        if not trailing:
            continue
        unit = trailing[-1].strip(".,;:!?()").lower()
        if not unit or not unit.isalpha():
            continue

        following = rendered[end:].lstrip()
        word = following.split(" ")[0].strip(".,;:!?()").lower() if following else ""
        if word and word == unit:
            raise GroundingError(
                "repeated_unit",
                f"{where} repeats the unit '{unit}', which the figure already includes",
            )


def assert_every_digit_is_traced(rendered: str, spans: list[tuple[int, int]], where: str) -> None:
    """Every digit in the finished text must have come from the registry."""
    for match in DIGIT_RE.finditer(rendered):
        index = match.start()
        if not any(start <= index < end for start, end in spans):
            raise GroundingError(
                "untraced_number",
                f"{where} contains a digit at position {index} that no report figure produced",
            )


# --------------------------------------------------------------------------
# The whole pipeline
# --------------------------------------------------------------------------


def ground(raw_response: str, registry: FigureRegistry) -> GroundedNarrative:
    """Run all four gates over a raw model response.

    Raises GroundingError on any failure; the caller falls back rather than
    salvaging a partially trustworthy summary.
    """
    payload = parse_json(raw_response)
    draft = validate_schema(payload)
    used_keys = audit_draft(draft, registry)

    authored = [draft.greeting, *draft.body_lines]
    lines: list[str] = []
    for index, text in enumerate(authored):
        where = _describe(index, len(authored))
        rendered, spans = render(text, registry)
        assert_every_digit_is_traced(rendered, spans, where)
        assert_no_repeated_unit(rendered, spans, where)
        rendered = rendered.strip()
        if rendered:
            lines.append(rendered)

    caveat_text, caveat_spans = render(draft.caveat, registry)
    assert_every_digit_is_traced(caveat_text, caveat_spans, "the caveat")
    assert_no_repeated_unit(caveat_text, caveat_spans, "the caveat")

    if not lines:
        raise GroundingError("schema", "the narrative rendered to no visible text")

    return GroundedNarrative(
        lines=lines,
        caveat=caveat_text.strip(),
        figures_used=registry.used(used_keys),
        source="llm",
    )
