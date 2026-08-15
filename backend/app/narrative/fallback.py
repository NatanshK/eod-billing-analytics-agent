"""The narrative of last resort.

Assembled directly from the figure registry, so no number can enter that did not
come from the report. A missing API key, a timeout, and a model returning prose
all land here; the response says source="fallback" so nobody mistakes it for
model output.
"""

from __future__ import annotations

import json

from ..core.figures import FigureRegistry
from .validator import GroundedNarrative, ground

#: Written in the same token language the model is asked to use. A template that
#: drifts out of sync with the registry fails loudly in tests rather than
#: emitting a broken sentence.
GREETING = "Good evening! Here's today's summary for {{clinic_name}} ({{business_date}}):"

CAVEAT = (
    "Cost price isn't in the billing data, so these are revenue figures, not "
    "profit — flagging that rather than estimating it."
)


def _sentences(registry: FigureRegistry) -> list[str]:
    """Build the body from whichever figures this day has."""
    lines: list[str] = []

    if "collection_rate" in registry:
        lines.append(
            "{{total_billed}} billed across {{visit_count}}, "
            "{{total_collected}} collected ({{collection_rate}})."
        )
    else:
        lines.append("{{total_billed}} billed across {{visit_count}}, {{total_collected}} collected.")

    if "pending_visit_count" in registry:
        lines.append("{{outstanding}} is still outstanding across {{pending_visit_count}}.")
    else:
        lines.append("{{outstanding}} is still outstanding.")

    if "refunds" in registry:
        lines.append("{{refunds}} went back out across {{refund_count}}.")

    if "peak_hour" in registry:
        lines.append("Busiest hour: {{peak_hour}}, with {{peak_hour_revenue}} in revenue.")

    movers: list[str] = []
    if "top_qty_1_name" in registry:
        movers.append("Top mover by quantity: {{top_qty_1_name}} ({{top_qty_1_units}}).")
    if "top_revenue_1_name" in registry:
        movers.append("Top by revenue: {{top_revenue_1_name}} ({{top_revenue_1_value}}).")
    lines.extend(movers)

    return lines


def build_fallback(registry: FigureRegistry, reason: str | None = None) -> GroundedNarrative:
    """Compose the deterministic summary for this day.

    Goes through ground() like any model draft rather than being trusted for
    being ours, which also means every grounding test exercises this path.
    """
    draft = {
        "greeting": GREETING,
        "body_lines": _sentences(registry),
        "caveat": CAVEAT,
    }

    narrative = ground(json.dumps(draft), registry)
    narrative.source = "fallback"
    narrative.fallback_reason = reason
    return narrative
