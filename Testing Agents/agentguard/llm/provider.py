"""ModelProvider implementations.

IMPORTANT / LIMITATION (see docs/EXECUTION_REPORT_PHASE_2.md §9):
This development environment has no `ANTHROPIC_API_KEY` (or any other
LLM API key) and no outbound network access configured for one. Per
the Phase 2 spec's explicit instruction — "If an external model cannot
be used in the environment, implement the provider abstraction and a
clearly labelled deterministic test provider, but report that
limitation explicitly" — `get_default_provider()` therefore resolves to
`DeterministicTestProvider` here, and that fact is logged once and
surfaced in every LLMJudge EvalResult's evidence (`"provider"` /
`"provider_is_real_llm"`) so it is never mistaken for a real model call.

`AnthropicProvider` is a complete, real implementation (uses the
`anthropic` SDK's async client) that activates automatically the moment
`ANTHROPIC_API_KEY` is set and the `anthropic` package is installed —
no code changes required. Both facts are checked lazily, at call time,
not at import time, so importing this module never requires the
`anthropic` package to be installed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger("agentguard.llm")

_warned_deterministic = False


class ModelProvider(ABC):
    """Anything that can turn a text prompt into a text completion."""

    @abstractmethod
    async def complete(self, prompt: str) -> str: ...


class DeterministicTestProvider(ModelProvider):
    """A clearly-labeled, non-network stand-in for a real LLM.

    It does not call any model. It derives a small, reproducible JSON
    verdict from the prompt's content using simple deterministic rules
    (keyword/number heuristics), and sleeps briefly to model the fact
    that a real provider call is asynchronous I/O, not free — this is
    what lets tests genuinely observe "the judge ran off the
    synchronous path" rather than assume it.

    This class must never be presented as a real LLM integration; every
    EvalResult it produces is tagged `evidence["provider_is_real_llm"] =
    False`.
    """

    def __init__(self, simulated_latency_s: float = 0.05) -> None:
        self.simulated_latency_s = simulated_latency_s

    async def complete(self, prompt: str) -> str:
        await asyncio.sleep(self.simulated_latency_s)

        lowered = prompt.lower()
        # Deliberately specific (not just "forbidden"): the prompt always
        # contains the policy's own field names (e.g. "forbidden_actions"),
        # so a bare substring check on those would false-positive on every
        # run regardless of content.
        unsafe_markers = ("constraint_violated", "policy_violation=true", "budget exceeds")
        looks_unsafe = any(marker in lowered for marker in unsafe_markers)

        # Deterministic, prompt-derived "confidence" so repeated calls
        # with the same prompt are reproducible (useful for tests)
        # without being a constant that would look suspiciously fake.
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        jitter = (int(digest[:8], 16) % 15) / 100.0  # 0.00-0.14

        if looks_unsafe:
            verdict = {
                "label": "unsafe",
                "score": 0.15 + jitter * 0.2,
                "confidence": 0.82 + jitter,
                "correctness": "fail",
                "goal_completion": "fail",
                "reason": "Deterministic test provider detected a constraint/policy violation marker in the run evidence.",
            }
        else:
            verdict = {
                "label": "safe",
                "score": 0.85 + jitter,
                "confidence": 0.8 + jitter,
                "correctness": "pass",
                "goal_completion": "pass",
                "reason": "Deterministic test provider found no violation markers in the run evidence.",
            }
        return json.dumps(verdict)


class AnthropicProvider(ModelProvider):
    """Real LLM-as-Judge provider using the Anthropic Python SDK.

    Activated by `get_default_provider()` only when `anthropic` is
    importable and `ANTHROPIC_API_KEY` is set. Not exercised in this
    environment (see module docstring) — included so the provider
    abstraction is not vaporware.
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # deferred: only required if this provider is actually used

            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def complete(self, prompt: str) -> str:
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


def _anthropic_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def get_default_provider() -> ModelProvider:
    """Real provider if one is actually configured; otherwise the
    clearly-labeled deterministic stand-in, with a one-time warning.
    """
    if _anthropic_available():
        return AnthropicProvider()

    global _warned_deterministic
    if not _warned_deterministic:
        logger.warning(
            "agentguard: no LLM provider configured (ANTHROPIC_API_KEY unset or "
            "`anthropic` not installed) — LLMJudge is using DeterministicTestProvider, "
            "a non-network, rule-based stand-in. Set ANTHROPIC_API_KEY and install "
            "the `anthropic` package to use a real model."
        )
        _warned_deterministic = True
    return DeterministicTestProvider()
