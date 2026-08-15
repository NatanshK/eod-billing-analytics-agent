"""The HTTP transport to the model provider.

test_grounding.py covers a misbehaving model; this covers a misbehaving gateway.
Every branch must end in ProviderError, the only exception the service catches —
anything else escapes as a 500.

The interesting cases are the ones that arrive looking like success: a 200 whose
body carries an error, a 200 whose body is not JSON, a completion that is not
text.
"""

from __future__ import annotations

import httpx
import pytest

from app.narrative.provider import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    OpenRouterProvider,
    ProviderError,
    ProviderNotConfigured,
    default_provider,
)

MESSAGES = [{"role": "user", "content": "hello"}]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Never let the developer's own shell configure the unit under test."""
    for name in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_TOKENS",
        "LLM_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)


def respond_with(monkeypatch, response=None, *, raises=None):
    """Stub httpx.post, capturing the request it was handed."""
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, payload=json, headers=headers, timeout=timeout)
        if raises is not None:
            raise raises
        return response

    monkeypatch.setattr(httpx, "post", fake_post)
    return captured


def ok(body: dict | str, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    if isinstance(body, str):
        return httpx.Response(status_code, text=body, request=request)
    return httpx.Response(status_code, json=body, request=request)


def completion(content) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_no_api_key_is_not_configured_and_never_calls_out(monkeypatch):
    captured = respond_with(monkeypatch, ok(completion("{}")))
    provider = OpenRouterProvider(api_key="")

    assert provider.is_configured is False
    with pytest.raises(ProviderNotConfigured):
        provider.complete(MESSAGES)
    # The point of the guard: no request is made at all.
    assert captured == {}


def test_defaults_come_from_the_module_not_the_environment():
    provider = OpenRouterProvider(api_key="sk-test")

    assert provider.model == DEFAULT_MODEL
    assert provider.max_tokens == DEFAULT_MAX_TOKENS
    assert provider.reasoning_effort == DEFAULT_REASONING_EFFORT


def test_the_default_model_is_a_free_tier_one():
    """A reviewer with a fresh key should not be charged to see the demo work."""
    assert DEFAULT_MODEL.endswith(":free")


def test_environment_overrides_every_default(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    monkeypatch.setenv("OPENROUTER_MODEL", "some/other-model")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("LLM_MAX_TOKENS", "123")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")

    provider = default_provider()

    assert provider.api_key == "sk-env"
    assert provider.model == "some/other-model"
    assert provider.base_url == "https://example.test/v1"  # trailing slash stripped
    assert provider.timeout == 7.5
    assert provider.max_tokens == 123
    assert provider.reasoning_effort == "low"


def test_explicit_arguments_beat_the_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "from/env")
    provider = OpenRouterProvider(api_key="sk-test", model="from/argument")

    assert provider.model == "from/argument"


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


def test_the_request_is_shaped_the_way_openrouter_expects(monkeypatch):
    captured = respond_with(monkeypatch, ok(completion('{"greeting": "hi"}')))
    OpenRouterProvider(api_key="sk-test", model="vendor/model").complete(MESSAGES)

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"

    payload = captured["payload"]
    assert payload["model"] == "vendor/model"
    assert payload["messages"] == MESSAGES
    # JSON mode, so gate 1 is not spent rejecting prose the model wrapped it in.
    assert payload["response_format"] == {"type": "json_object"}


def test_reasoning_is_disabled_so_thinking_cannot_eat_the_answer(monkeypatch):
    """A hybrid model's reasoning shares the completion budget with the JSON.

    Left on, a long think truncates the response and gate 1 rejects it for a
    reason that has nothing to do with grounding.
    """
    captured = respond_with(monkeypatch, ok(completion("{}")))
    OpenRouterProvider(api_key="sk-test").complete(MESSAGES)

    assert captured["payload"]["reasoning"] == {"effort": "none"}


def test_the_token_budget_leaves_room_for_the_whole_object(monkeypatch):
    captured = respond_with(monkeypatch, ok(completion("{}")))
    OpenRouterProvider(api_key="sk-test").complete(MESSAGES)

    assert captured["payload"]["max_tokens"] >= 1000


def test_the_configured_timeout_is_the_one_actually_used(monkeypatch):
    captured = respond_with(monkeypatch, ok(completion("{}")))
    OpenRouterProvider(api_key="sk-test", timeout=3.0).complete(MESSAGES)

    assert captured["timeout"] == 3.0


# --------------------------------------------------------------------------
# Transport failures
# --------------------------------------------------------------------------


def test_a_timeout_becomes_a_provider_error_naming_the_limit(monkeypatch):
    respond_with(monkeypatch, raises=httpx.TimeoutException("timed out"))

    with pytest.raises(ProviderError) as caught:
        OpenRouterProvider(api_key="sk-test", timeout=12.0).complete(MESSAGES)

    assert "did not respond within 12s" in str(caught.value)


def test_an_unreachable_host_becomes_a_provider_error(monkeypatch):
    respond_with(monkeypatch, raises=httpx.ConnectError("name resolution failed"))

    with pytest.raises(ProviderError, match="could not reach"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


@pytest.mark.parametrize("status_code", [400, 401, 402, 404, 429, 500, 503])
def test_any_non_200_becomes_a_provider_error(monkeypatch, status_code):
    """402 and 429 are the realistic ones: no credit, and free-tier throttling."""
    respond_with(monkeypatch, ok({"error": "nope"}, status_code=status_code))

    with pytest.raises(ProviderError) as caught:
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)

    assert str(status_code) in str(caught.value)


# --------------------------------------------------------------------------
# Failures wearing a 200
# --------------------------------------------------------------------------


def test_a_non_json_body_is_a_provider_error_not_a_crash(monkeypatch):
    """A proxy or captive portal returning HTML with a 200 is the usual cause."""
    respond_with(monkeypatch, ok("<html>gateway</html>"))

    with pytest.raises(ProviderError, match="non-JSON body"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


def test_an_error_body_behind_a_200_is_reported_with_its_message(monkeypatch):
    respond_with(monkeypatch, ok({"error": {"message": "rate limited", "code": 429}}))

    with pytest.raises(ProviderError, match="rate limited"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


def test_a_string_error_body_behind_a_200_is_also_handled(monkeypatch):
    respond_with(monkeypatch, ok({"error": "upstream exploded"}))

    with pytest.raises(ProviderError, match="upstream exploded"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": "not-a-list"},
    ],
    ids=["empty", "no-choices", "no-message", "no-content", "wrong-type"],
)
def test_an_unrecognised_response_shape_is_a_provider_error(monkeypatch, body):
    """Would otherwise escape as KeyError/IndexError/TypeError — i.e. a 500."""
    respond_with(monkeypatch, ok(body))

    with pytest.raises(ProviderError, match="unrecognised response shape"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


def test_a_non_text_completion_is_rejected(monkeypatch):
    """Some gateways return content as a list of parts rather than a string."""
    respond_with(monkeypatch, ok(completion([{"type": "text", "text": "hi"}])))

    with pytest.raises(ProviderError, match="non-text completion"):
        OpenRouterProvider(api_key="sk-test").complete(MESSAGES)


def test_a_well_formed_response_returns_the_content_verbatim(monkeypatch):
    raw = '{"greeting": "Evening!", "body_lines": ["{{total_billed}} billed."]}'
    respond_with(monkeypatch, ok(completion(raw)))

    assert OpenRouterProvider(api_key="sk-test").complete(MESSAGES) == raw
