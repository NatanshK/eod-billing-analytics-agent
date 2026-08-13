"""Narrative generation, end to end.

The control flow is deliberately boring, because the interesting property is what
it *cannot* do: there is no path through this module that returns an ungrounded
narrative, and no path that turns a provider failure into a 5xx.

    try the model  ->  gates pass?  -> return it, source="llm"
                   ->  gates fail?  -> one repair attempt
                                    -> still failing -> deterministic fallback
    provider down / no key          -> deterministic fallback
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ..core.analytics import Analytics
from ..core.figures import build_registry
from ..core.reconciliation import Reconciliation
from .fallback import build_fallback
from .prompt import build_messages, build_repair_messages
from .provider import LLMProvider, ProviderError, ProviderNotConfigured
from .validator import GroundedNarrative, GroundingError, ground

log = logging.getLogger(__name__)


def generate_narrative(
    reconciliation: Reconciliation,
    analytics: Analytics,
    *,
    clinic_id: str,
    business_date: date,
    clinic_name: str | None = None,
    provider: LLMProvider | None = None,
) -> GroundedNarrative:
    """Produce a grounded, owner-facing summary of this clinic-day.

    Never raises for a model problem. The worst case is a deterministic summary
    with ``source="fallback"`` and a reason the UI can show.
    """
    registry = build_registry(
        reconciliation,
        analytics,
        clinic_id=clinic_id,
        business_date=business_date,
        clinic_name=clinic_name,
    )

    narrative, reason = _try_model(registry, provider)
    if narrative is None:
        narrative = build_fallback(registry, reason=reason)

    narrative.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return narrative


def _try_model(
    registry, provider: LLMProvider | None
) -> tuple[GroundedNarrative | None, str | None]:
    """Attempt model generation with exactly one corrective retry.

    Returns ``(narrative, None)`` on success or ``(None, reason)`` on any
    failure, so the caller can report *why* it fell back.
    """
    if provider is None:
        from .provider import default_provider

        provider = default_provider()

    if not getattr(provider, "is_configured", True):
        return None, "no model API key is configured"

    try:
        raw = provider.complete(build_messages(registry))
    except ProviderNotConfigured as exc:
        return None, str(exc)
    except ProviderError as exc:
        log.warning("narrative provider failed: %s", exc)
        return None, str(exc)

    try:
        return ground(raw, registry), None
    except GroundingError as exc:
        # Bind outside the handler: Python clears the `as` name on block exit.
        first_gate, first_reason = exc.gate, exc.reason
        log.info("model draft rejected at gate '%s': %s", first_gate, first_reason)

    # One repair round-trip. A model that breaks the contract twice will not
    # honour it on a third attempt, and the owner is waiting on the response.
    try:
        repaired = provider.complete(build_repair_messages(registry, raw, first_reason))
    except ProviderError as exc:
        return None, f"draft rejected ({first_gate}); retry failed: {exc}"

    try:
        return ground(repaired, registry), None
    except GroundingError as exc:
        log.warning("model draft rejected twice; falling back (%s)", exc.reason)
        return None, (
            f"model output failed grounding checks twice ({first_gate}, then {exc.gate})"
        )
