"""Grounding tests — the core of the agentic requirement.

Every test answers one question: can a model, misbehaving in some specific way,
get an untraceable number in front of the clinic owner? The answer must be no,
and the request must survive in every case.
"""

from __future__ import annotations

import json
import re

import pytest

from app.core.analytics import compute_analytics
from app.core.figures import build_registry
from app.core.parsing import parse_billing_log
from app.core.reconciliation import reconcile
from app.narrative.fallback import build_fallback
from app.narrative.provider import ProviderError, ProviderNotConfigured
from app.narrative.service import generate_narrative
from app.narrative.validator import GroundingError, ground


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


@pytest.fixture
def registry(happy_day):
    parsed = parse_billing_log(happy_day)
    return build_registry(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id=parsed.clinic_id,
        business_date=parsed.business_date,
        clinic_name="Mehta Multi-Specialty Clinic",
    )


def draft(greeting="Evening!", body=None, caveat="Cost price isn't recorded, so this is revenue."):
    return json.dumps(
        {
            "greeting": greeting,
            "body_lines": body if body is not None else ["{{total_billed}} billed today."],
            "caveat": caveat,
        }
    )


class FakeProvider:
    """A model that returns whatever the test tells it to, in order."""

    is_configured = True

    def __init__(self, *responses, raises=None):
        self.responses = list(responses)
        self.raises = raises
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(messages)
        if self.raises:
            raise self.raises
        if not self.responses:
            raise AssertionError("provider called more times than the test expected")
        return self.responses.pop(0)


def narrate(happy_day, provider):
    parsed = parse_billing_log(happy_day)
    return generate_narrative(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id=parsed.clinic_id,
        business_date=parsed.business_date,
        clinic_name="Mehta Multi-Specialty Clinic",
        provider=provider,
    )


# --------------------------------------------------------------------------
# The registry is the vocabulary
# --------------------------------------------------------------------------


def test_registry_covers_the_headline_figures(registry):
    assert registry.get("total_billed").display == "₹2,320"
    assert registry.get("total_collected").display == "₹2,130"
    assert registry.get("outstanding").display == "₹190"
    assert registry.get("collection_rate").display == "92%"
    assert registry.get("visit_count").display == "5 visits"


def test_every_figure_traces_to_a_report_field(registry):
    for figure in registry:
        assert figure.field_path
        assert figure.display


def test_absent_metrics_are_absent_from_the_vocabulary(registry):
    """The happy day has no refunds, so there is no way to write about refunds."""
    assert "refunds" not in registry
    assert "refund_count" not in registry


def test_refund_figures_appear_only_when_there_were_refunds(edge_day):
    parsed = parse_billing_log(edge_day)
    reg = build_registry(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id=parsed.clinic_id,
        business_date=parsed.business_date,
    )

    assert reg.get("refunds").display == "₹200"
    assert reg.get("refund_count").display == "2 refunds"


def test_singular_plural_is_handled(happy_day):
    """'1 visit', not '1 visits' — the owner reads this on WhatsApp."""
    parsed = parse_billing_log(happy_day)
    reg = build_registry(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id=parsed.clinic_id,
        business_date=parsed.business_date,
    )
    assert reg.get("pending_visit_count").display == "1 visit"


# --------------------------------------------------------------------------
# Gate 1 — parse
# --------------------------------------------------------------------------


def test_non_json_response_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground("Sure! Here's your summary: the clinic did well today.", registry)
    assert exc.value.gate == "parse"


def test_truncated_json_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground('{"greeting": "Hi", "body_lines": ["{{total_billed}}"', registry)
    assert exc.value.gate == "parse"


def test_empty_response_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground("   ", registry)
    assert exc.value.gate == "parse"


def test_markdown_fenced_json_is_accepted(registry):
    """Common model behaviour, not a contract violation — unwrap and continue."""
    fenced = f"```json\n{draft()}\n```"
    assert ground(fenced, registry).lines


def test_json_array_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground('["a summary"]', registry)
    assert exc.value.gate == "parse"


# --------------------------------------------------------------------------
# Gate 2 — schema
# --------------------------------------------------------------------------


def test_missing_required_key_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground('{"greeting": "Hi"}', registry)
    assert exc.value.gate == "schema"


def test_wrong_type_for_body_lines_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground('{"greeting": "Hi", "body_lines": "one long string", "caveat": ""}', registry)
    assert exc.value.gate == "schema"


def test_empty_body_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground('{"greeting": "Hi", "body_lines": [], "caveat": ""}', registry)
    assert exc.value.gate == "schema"


def test_unexpected_extra_keys_are_tolerated(registry):
    payload = json.loads(draft())
    payload["confidence"] = 0.9
    assert ground(json.dumps(payload), registry).lines


# --------------------------------------------------------------------------
# Gate 3 — the model must not write digits
# --------------------------------------------------------------------------


def test_literal_rupee_amount_is_rejected(registry):
    """The headline failure mode: a plausible-looking invented number."""
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["₹42,850 was billed today."]), registry)

    assert exc.value.gate == "literal_number"
    assert "body line 1" in exc.value.reason


def test_a_correct_number_written_literally_is_still_rejected(registry):
    """Even the *right* number, typed rather than cited, breaks traceability.

    If we accepted this we would be checking arithmetic, not provenance — and the
    Traced Figures panel would have nothing to point at.
    """
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["₹2,320 billed today."]), registry)
    assert exc.value.gate == "literal_number"


def test_digit_in_the_greeting_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground(draft(greeting="Good evening, 27 July!"), registry)
    assert "greeting" in exc.value.reason


def test_digit_in_the_caveat_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground(draft(caveat="Cost price for all 5 items is missing."), registry)
    assert exc.value.gate == "literal_number"


# --------------------------------------------------------------------------
# Gate 3 — numbers spelled out in words
#
# Found in live output, not in review: told to write no digits, the model wrote
# "Three refunds were processed". A digit-only scan calls that clean, and the
# figure was never checked against the report.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Three refunds were processed today.",
        "We saw eighteen visits.",
        "About forty thousand rupees came in.",
        "Roughly two lakh outstanding.",
        "A dozen medicines moved.",
        "Billed and collected both show zero.",
    ],
)
def test_a_number_spelled_out_in_words_is_rejected(registry, line):
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=[line]), registry)
    assert exc.value.gate == "spelled_number"


def test_a_spelled_number_in_the_caveat_is_also_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground(draft(caveat="Cost price is missing for three of the line items."), registry)
    assert exc.value.gate == "spelled_number"


@pytest.mark.parametrize(
    "line",
    [
        "Takings are steady, one of the better days this week.",
        "No one was turned away today.",
        "Everything balanced cleanly.",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_a_number(registry, line):
    """'one' is a pronoun as often as a quantity; the idioms must survive."""
    result = ground(draft(body=[line]), registry)
    assert result.lines


# --------------------------------------------------------------------------
# Gate 4 — a unit written twice
# --------------------------------------------------------------------------


def test_a_unit_the_figure_already_carries_is_rejected(registry):
    """`visit_count` renders to "18 visits", so this would read "18 visits visits".

    Grounded, correct, and unsendable — the text goes to a clinic owner on
    WhatsApp.
    """
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["We saw {{visit_count}} visits today."]), registry)

    assert exc.value.gate == "repeated_unit"
    assert "visits" in exc.value.reason


def test_the_same_figure_used_correctly_is_accepted(registry):
    result = ground(draft(body=["We saw {{visit_count}} today."]), registry)
    assert any("visit" in line for line in result.lines)


def test_a_word_that_merely_follows_a_figure_is_fine(registry):
    """Only an exact repeat of the figure's own trailing word is a duplicate."""
    result = ground(draft(body=["{{visit_count}} came through the door."]), registry)
    assert result.lines


def test_unknown_token_is_rejected(registry):
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["Profit was {{total_profit}} today."]), registry)

    assert exc.value.gate == "tokens"
    assert "total_profit" in exc.value.reason


def test_token_for_a_metric_this_day_lacks_is_rejected(registry):
    """The happy day had no refunds; citing a refund figure must not resolve."""
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["We refunded {{refunds}} today."]), registry)
    assert exc.value.gate == "tokens"


def test_profit_claim_is_rejected_outside_the_caveat(registry):
    """The brief: say plainly that it can't be computed, don't approximate it."""
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=["Your profit today was strong."]), registry)

    assert exc.value.gate == "unsupported_claim"
    assert "profit" in exc.value.reason


@pytest.mark.parametrize("claim", ["margin", "markup", "cost price", "COGS"])
def test_other_underivable_claims_are_rejected(registry, claim):
    with pytest.raises(GroundingError) as exc:
        ground(draft(body=[f"Your {claim} looked healthy."]), registry)
    assert exc.value.gate == "unsupported_claim"


def test_the_caveat_may_name_what_is_missing(registry):
    """That is the caveat's entire job — it must not be gagged by the same rule."""
    result = ground(
        draft(caveat="Cost price isn't in this data, so profit can't be calculated."),
        registry,
    )
    assert "profit" in result.caveat


# --------------------------------------------------------------------------
# Gate 4 — every digit in the output is traced
# --------------------------------------------------------------------------


def test_rendered_narrative_substitutes_real_values(registry):
    result = ground(
        draft(body=["{{total_billed}} billed across {{visit_count}}."]),
        registry,
    )

    assert result.lines[-1] == "₹2,320 billed across 5 visits."
    assert result.source == "llm"


def test_every_digit_in_the_output_came_from_the_registry(registry):
    """The audit an automated grader would run, run on ourselves."""
    result = ground(
        draft(
            body=[
                "{{total_billed}} billed across {{visit_count}}, {{total_collected}} collected ({{collection_rate}}).",
                "Busiest hour: {{peak_hour}}, with {{peak_hour_revenue}} in revenue.",
            ]
        ),
        registry,
    )

    displays = registry.displays
    for line in result.lines:
        for number in re.findall(r"[₹]?[\d,]+(?:\.\d+)?%?", line):
            assert any(number in display for display in displays), (
                f"{number!r} in {line!r} traces to no report figure"
            )


def test_traced_figures_cover_exactly_what_was_cited(registry):
    result = ground(
        draft(body=["{{total_billed}} billed, {{outstanding}} outstanding."]),
        registry,
    )

    assert [f.key for f in result.figures_used] == ["total_billed", "outstanding"]
    assert [f.field_path for f in result.figures_used] == [
        "reconciliation.total_billed_paise",
        "reconciliation.outstanding_paise",
    ]


def test_a_figure_cited_twice_is_traced_once(registry):
    result = ground(
        draft(body=["{{total_billed}} billed.", "Yes, {{total_billed}} in total."]),
        registry,
    )
    assert [f.key for f in result.figures_used] == ["total_billed"]


def test_whitespace_inside_a_token_still_resolves(registry):
    result = ground(draft(body=["{{ total_billed }} billed."]), registry)
    assert result.lines[-1] == "₹2,320 billed."


# --------------------------------------------------------------------------
# The fallback is grounded by construction
# --------------------------------------------------------------------------


def test_fallback_passes_the_same_gates(registry):
    """It is not trusted for being ours; it goes through ground() too."""
    result = build_fallback(registry, reason="testing")

    assert result.source == "fallback"
    assert result.fallback_reason == "testing"
    assert result.lines


def test_fallback_quotes_real_figures(registry):
    result = build_fallback(registry)
    joined = " ".join(result.lines)

    assert "₹2,320" in joined
    assert "5 visits" in joined
    assert "92%" in joined


def test_fallback_names_the_clinic_and_date_without_breaking_grounding(registry):
    """Both contain digits, so both must arrive through the registry."""
    result = build_fallback(registry)

    assert "Mehta Multi-Specialty Clinic" in result.lines[0]
    assert "27 Jul 2026" in result.lines[0]


def test_fallback_states_the_profit_caveat(registry):
    assert "profit" in build_fallback(registry).caveat.lower()


def test_fallback_adapts_to_the_day_it_describes(edge_day):
    """A day with refunds says so; the happy day's fallback cannot."""
    parsed = parse_billing_log(edge_day)
    reg = build_registry(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id=parsed.clinic_id,
        business_date=parsed.business_date,
    )
    joined = " ".join(build_fallback(reg).lines)

    assert "₹200" in joined and "2 refunds" in joined


def test_fallback_survives_an_empty_day(empty_day):
    """No visits, no peak, no drugs — and still a sentence the owner can read."""
    parsed = parse_billing_log(empty_day)
    reg = build_registry(
        reconcile(parsed.visits),
        compute_analytics(parsed.visits),
        clinic_id="CLINIC-MEHTA-001",
        business_date=parsed.business_date,
    )
    result = build_fallback(reg)

    assert result.lines
    assert "₹0" in " ".join(result.lines)


# --------------------------------------------------------------------------
# Service orchestration — no model failure becomes a request failure
# --------------------------------------------------------------------------


def test_good_model_response_is_used(happy_day):
    provider = FakeProvider(draft(body=["{{total_billed}} billed across {{visit_count}}."]))
    result = narrate(happy_day, provider)

    assert result.source == "llm"
    assert result.fallback_reason is None
    assert "₹2,320 billed across 5 visits." in result.lines
    assert len(provider.calls) == 1


def test_bad_draft_triggers_exactly_one_repair_attempt(happy_day):
    """First response invents a number; the retry behaves; we use the retry."""
    provider = FakeProvider(
        draft(body=["We billed ₹99,999 today."]),
        draft(body=["{{total_billed}} billed."]),
    )
    result = narrate(happy_day, provider)

    assert result.source == "llm"
    assert len(provider.calls) == 2
    assert "₹2,320 billed." in result.lines


def test_repair_prompt_explains_the_rejection(happy_day):
    provider = FakeProvider(
        draft(body=["We billed ₹99,999 today."]),
        draft(body=["{{total_billed}} billed."]),
    )
    narrate(happy_day, provider)

    repair_turn = provider.calls[1][-1]["content"]
    assert "rejected" in repair_turn.lower()
    assert "no digits" in repair_turn.lower()


def test_two_bad_drafts_fall_back(happy_day):
    provider = FakeProvider(
        draft(body=["We billed ₹99,999 today."]),
        draft(body=["Still ₹88,888 today."]),
    )
    result = narrate(happy_day, provider)

    assert result.source == "fallback"
    assert "twice" in result.fallback_reason
    assert len(provider.calls) == 2


def test_provider_timeout_falls_back(happy_day):
    provider = FakeProvider(raises=ProviderError("the model did not respond within 25s"))
    result = narrate(happy_day, provider)

    assert result.source == "fallback"
    assert "did not respond" in result.fallback_reason
    assert result.lines


def test_missing_api_key_falls_back(happy_day):
    provider = FakeProvider(raises=ProviderNotConfigured("OPENROUTER_API_KEY is not set"))
    result = narrate(happy_day, provider)

    assert result.source == "fallback"
    assert "OPENROUTER_API_KEY" in result.fallback_reason


def test_unconfigured_provider_skips_the_call_entirely(happy_day):
    class Unconfigured(FakeProvider):
        is_configured = False

    provider = Unconfigured()
    result = narrate(happy_day, provider)

    assert result.source == "fallback"
    assert provider.calls == []  # no pointless round-trip


def test_garbage_response_falls_back_without_raising(happy_day):
    provider = FakeProvider("not json at all", "still not json")
    result = narrate(happy_day, provider)

    assert result.source == "fallback"
    assert result.lines


def test_every_path_yields_a_traceable_narrative(happy_day):
    """Whatever the model does, the output is quotable and traced."""
    providers = [
        FakeProvider(draft(body=["{{total_billed}} billed."])),
        FakeProvider("garbage", "garbage"),
        FakeProvider(raises=ProviderError("down")),
        FakeProvider(draft(body=["₹1 billed."]), draft(body=["₹2 billed."])),
    ]

    for provider in providers:
        result = narrate(happy_day, provider)
        assert result.lines, "every path must produce a narrative"
        assert result.generated_at
        for figure in result.figures_used:
            assert figure.field_path


def test_narrative_serialises_for_the_api(happy_day):
    provider = FakeProvider(draft(body=["{{total_billed}} billed."]))
    payload = narrate(happy_day, provider).to_dict()

    assert payload["source"] == "llm"
    assert payload["narrative_lines"]
    assert payload["traced_figures"][0]["field_path"]
    assert "value_paise" in payload["traced_figures"][0]
