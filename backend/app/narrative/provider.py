"""LLM transport.

An OpenAI-compatible chat-completions client, pointed at OpenRouter by default.
The service depends only on the LLMProvider protocol, which is what lets the
grounding tests inject a model that misbehaves on purpose.

Every remote failure here is raised as ProviderError. The service catches it and
falls back, so a third party being down never reaches the client as a 5xx.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: A free-tier model by default, so a reviewer with a fresh OpenRouter key sees
#: model-written narratives without spending anything. Any OpenAI-compatible
#: model id works; only the name changes.
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

#: Free-tier requests queue behind paid ones, so the ceiling is generous.
DEFAULT_TIMEOUT_SECONDS = 45.0

#: Room for the JSON plus slack. A tight budget truncates the response, which
#: fails gate 1 for a reason unrelated to grounding.
DEFAULT_MAX_TOKENS = 2000

#: The figures are fixed by the registry and the model only chooses phrasing, so
#: thinking tokens buy nothing — and on a hybrid model they come out of the same
#: budget as the answer. "none" disables reasoning; "exclude" would only hide it
#: while still paying for it.
DEFAULT_REASONING_EFFORT = "none"


class ProviderError(Exception):
    """The model could not be reached or refused to answer."""


class ProviderNotConfigured(ProviderError):
    """No API key set. Expected in local development and on a demo deploy."""


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class OpenRouterProvider:
    """Chat completions over OpenRouter's OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout if timeout is not None else float(
            os.environ.get("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self.reasoning_effort = os.environ.get(
            "LLM_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.is_configured:
            raise ProviderNotConfigured("OPENROUTER_API_KEY is not set")

        payload = {
            "model": self.model,
            "messages": messages,
            # Low but non-zero: the figures are fixed by the contract, so the only
            # thing temperature varies is phrasing.
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            # OpenRouter drops parameters a model does not support, so this is
            # inert against a non-reasoning model.
            "reasoning": {"effort": self.reasoning_effort},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter attribution headers; harmless against other providers.
            "HTTP-Referer": os.environ.get("PUBLIC_APP_URL", "http://localhost:5173"),
            "X-Title": "SwasthiQ EOD Billing Agent",
        }

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"the model did not respond within {self.timeout:g}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not reach the model provider: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"model provider returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

        return _extract_content(response)


def _extract_content(response: httpx.Response) -> str:
    """Pull the message text out, treating a surprising shape as a provider error.

    A gateway returning 200 with an error body is common enough to handle
    explicitly; letting a KeyError escape here would surface as a 500.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("model provider returned a non-JSON body") from exc

    if isinstance(body, dict) and body.get("error"):
        detail = body["error"]
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        raise ProviderError(f"model provider reported an error: {message}")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("model provider returned an unrecognised response shape") from exc

    if not isinstance(content, str):
        raise ProviderError("model returned a non-text completion")
    return content


def default_provider() -> OpenRouterProvider:
    return OpenRouterProvider()
