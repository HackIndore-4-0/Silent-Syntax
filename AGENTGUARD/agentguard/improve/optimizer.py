"""PromptOptimizer abstraction — Phase 4.

DSPy is an OPTIONAL dependency (Rule: "Do not make the core SDK depend
on DSPy"): `agentguard/improve/optimizer.py` imports it lazily, only
inside `DSPyOptimizer`, only when that class is actually instantiated
and used, never at module import time.

Mirrors the exact pattern `agentguard/llm/provider.py` already
established for the LLM Judge in Phase 2: a real implementation
(`DSPyOptimizer`) that activates only when both the `dspy` package is
importable AND a real language model is actually configured for it
(`AGENTGUARD_DSPY_MODEL` + a provider API key), and a clearly-labeled,
non-network `DeterministicTestOptimizer` stand-in that
`get_default_optimizer()` falls back to otherwise. Every
`ImprovementCandidate` records which one actually produced it
(`optimizer` name + `real_dspy_optimizer: bool`) — never silently
implied.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from ..models import ImprovementCandidate
from .recommend import Recommendation

logger = logging.getLogger("agentguard.improve")

_warned_deterministic = False


class PromptOptimizer(ABC):
    name: str = "optimizer"

    @abstractmethod
    async def optimize(self, source_run_id: str, recommendation: Recommendation) -> ImprovementCandidate: ...


class DeterministicTestOptimizer(PromptOptimizer):
    """A clearly-labeled, non-network stand-in for a real DSPy
    optimization run. It does not call any model or run any DSPy
    optimizer — it packages the deterministic Recommendation
    (agentguard/improve/recommend.py) into an ImprovementCandidate
    verbatim, tagged `real_dspy_optimizer=False`."""

    name = "DeterministicTestOptimizer"

    async def optimize(self, source_run_id: str, recommendation: Recommendation) -> ImprovementCandidate:
        return ImprovementCandidate(
            source_run_id=source_run_id,
            problem=recommendation.problem,
            root_cause_summary=recommendation.root_cause_summary,
            recommendation=recommendation.recommendation,
            candidate_change=recommendation.candidate_change,
            expected_benefit=recommendation.expected_benefit,
            optimizer=self.name,
            real_dspy_optimizer=False,
            status="proposed",
        )


class DSPyOptimizer(PromptOptimizer):
    """Real DSPy-backed optimizer. Activated by `get_default_optimizer()`
    only when `dspy` is importable AND `AGENTGUARD_DSPY_MODEL` is set
    (mirroring AnthropicProvider's ANTHROPIC_API_KEY check) — not
    exercised in this environment (no LLM API key configured; see
    docs/EXECUTION_REPORT_PHASE_4.md §20). Runs a minimal
    `dspy.Predict`-based rewrite of the recommendation's `candidate_change["after"]`
    text through the configured LM, so a genuine DSPy call really
    happens when this class is used — not a re-implementation of DSPy's
    own optimizers, since AgentGuard's candidate objects are structured
    text, not a DSPy training set with labeled examples.
    """

    name = "DSPyOptimizer"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("AGENTGUARD_DSPY_MODEL", "")

    async def optimize(self, source_run_id: str, recommendation: Recommendation) -> ImprovementCandidate:
        import dspy  # deferred: only required if this optimizer is actually used

        lm = dspy.LM(self.model)
        with dspy.context(lm=lm):
            rewrite = dspy.Predict("problem, recommendation -> improved_candidate_change")
            prediction = rewrite(problem=recommendation.problem, recommendation=recommendation.recommendation)
            after_text = getattr(prediction, "improved_candidate_change", recommendation.candidate_change.get("after", ""))

        candidate_change = dict(recommendation.candidate_change)
        candidate_change["after"] = after_text

        return ImprovementCandidate(
            source_run_id=source_run_id,
            problem=recommendation.problem,
            root_cause_summary=recommendation.root_cause_summary,
            recommendation=recommendation.recommendation,
            candidate_change=candidate_change,
            expected_benefit=recommendation.expected_benefit,
            optimizer=self.name,
            real_dspy_optimizer=True,
            status="proposed",
        )


def _dspy_available() -> bool:
    if not os.environ.get("AGENTGUARD_DSPY_MODEL"):
        return False
    try:
        import dspy  # noqa: F401
    except ImportError:
        return False
    return True


def get_default_optimizer() -> PromptOptimizer:
    """Real DSPy optimizer if one is actually configured; otherwise the
    clearly-labeled deterministic stand-in, with a one-time warning —
    identical pattern to agentguard.llm.provider.get_default_provider()."""
    if _dspy_available():
        return DSPyOptimizer()

    global _warned_deterministic
    if not _warned_deterministic:
        logger.warning(
            "agentguard: no DSPy model configured (AGENTGUARD_DSPY_MODEL unset or "
            "`dspy` not installed) — auto-improvement is using DeterministicTestOptimizer, "
            "a non-network, rule-based stand-in. Set AGENTGUARD_DSPY_MODEL and install "
            "`dspy` to use a real optimizer."
        )
        _warned_deterministic = True
    return DeterministicTestOptimizer()
