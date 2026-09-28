"""get_default_provider's priority order (agentguard/llm/provider.py):
OpenRouter (this project's documented .env key for LLM-as-Judge) first,
then Anthropic, then the deterministic stand-in -- never silently
falls back to the stand-in when a real key IS configured."""
from __future__ import annotations

import pytest

from agentguard.llm.provider import (
    AnthropicProvider,
    DeterministicTestProvider,
    OpenRouterProvider,
    get_default_provider,
)

try:
    import anthropic  # noqa: F401

    _ANTHROPIC_INSTALLED = True
except ImportError:
    _ANTHROPIC_INSTALLED = False


def test_no_keys_configured_falls_back_to_deterministic(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_default_provider(), DeterministicTestProvider)


def test_empty_string_openrouter_key_is_treated_as_unconfigured(monkeypatch):
    # .env.example ships OPENROUTER_API_KEY='' -- an unfilled-in key must
    # never be mistaken for "configured".
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_default_provider(), DeterministicTestProvider)


def test_openrouter_key_activates_openrouter_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = get_default_provider()
    assert isinstance(provider, OpenRouterProvider)


def test_openrouter_is_preferred_over_anthropic_when_both_are_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(get_default_provider(), OpenRouterProvider)


@pytest.mark.skipif(not _ANTHROPIC_INSTALLED, reason="`anthropic` package not installed in this environment")
def test_anthropic_key_activates_anthropic_provider_when_openrouter_unset(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    provider = get_default_provider()
    assert isinstance(provider, AnthropicProvider)


def test_openrouter_provider_defaults_to_gpt_oss_20b(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_JUDGE_MODEL", raising=False)
    assert OpenRouterProvider().model == "openrouter/openai/gpt-oss-20b"


def test_openrouter_provider_respects_judge_model_env_override(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_JUDGE_MODEL", "openrouter/openai/gpt-4o-mini")
    assert OpenRouterProvider().model == "openrouter/openai/gpt-4o-mini"


def test_openrouter_provider_explicit_model_wins_over_env(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_JUDGE_MODEL", "openrouter/openai/gpt-4o-mini")
    assert OpenRouterProvider(model="openrouter/anthropic/claude-3-5-haiku").model == "openrouter/anthropic/claude-3-5-haiku"


async def test_openrouter_provider_complete_calls_litellm_with_api_key(monkeypatch):
    captured = {}

    class _FakeMessage:
        content = "hello from the judge"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    provider = OpenRouterProvider(model="openrouter/openai/gpt-oss-20b")
    result = await provider.complete("judge this")

    assert result == "hello from the judge"
    assert captured["model"] == "openrouter/openai/gpt-oss-20b"
    assert captured["api_key"] == "sk-or-test"
    assert captured["messages"] == [{"role": "user", "content": "judge this"}]
