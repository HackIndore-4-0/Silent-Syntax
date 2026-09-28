"""agentguard/evaluation/trace_judge.py — per-trace LLM quality
judgment + model recommendation, computed automatically after every
llm_call TraceStep (distinct from the user-triggered
ModelBenchmarkEngine flow tested elsewhere)."""
from __future__ import annotations

from agentguard.evaluation.trace_judge import (
    TRACE_JUDGMENT_KIND,
    build_trace_judgment,
    judge_output,
    recommend_model,
    record_trace_judgment,
)
from agentguard.llm.provider import ModelProvider
from agentguard.models import TraceStep
from agentguard.storage.memory import InMemoryRunRepository


class _ScriptedProvider(ModelProvider):
    """Returns exactly what the test wants the judge to "say" — no
    network, no DeterministicTestProvider heuristics that don't match
    this module's expected JSON shape."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _make_step(**overrides) -> TraceStep:
    defaults = dict(
        run_id="run-1",
        kind="llm_call",
        name="litellm.acompletion",
        input={"kwargs": {"messages": [{"role": "user", "content": "What is 2+2?"}]}},
        output="4",
        outcome="success",
        model_name="gpt-4o-mini",
        tokens_input=10,
        tokens_output=2,
        cost_usd=0.0001,
    )
    defaults.update(overrides)
    return TraceStep(**defaults)


async def test_judge_output_parses_a_well_formed_verdict():
    provider = _ScriptedProvider(
        '{"score": 0.9, "verdict": "good", "issues": [], "suggested_fix": ""}'
    )
    result = await judge_output(provider, "What is 2+2?", "4")
    assert result == {"score": 0.9, "verdict": "good", "issues": [], "suggested_fix": ""}


async def test_judge_output_falls_back_to_needs_improvement_on_bad_json():
    provider = _ScriptedProvider("not json at all")
    result = await judge_output(provider, "input", "output")
    assert result["verdict"] == "needs_improvement"
    assert result["score"] is None
    assert "not valid JSON" in result["issues"][0]


async def test_judge_output_truncates_very_long_input_and_output():
    provider = _ScriptedProvider('{"score": 0.5, "verdict": "needs_improvement", "issues": [], "suggested_fix": ""}')
    await judge_output(provider, "x" * 10_000, "y" * 10_000)
    assert "…(truncated)" in provider.prompts[0]
    assert len(provider.prompts[0]) < 10_000


def test_recommend_model_suggests_a_stronger_model_on_low_score():
    model, reason = recommend_model(
        model_name="mistral-small-latest", tokens_input=100, tokens_output=50, uses_tools=False, score=0.2,
    )
    assert model != "mistral-small-latest"
    assert model is not None
    assert "poor" in reason or "needs_improvement" in reason


def test_recommend_model_suggests_a_cheaper_model_on_high_score():
    model, reason = recommend_model(
        model_name="gpt-4o", tokens_input=100, tokens_output=50, uses_tools=False, score=0.95,
    )
    assert model != "gpt-4o"
    assert model is not None
    assert "lower list price" in reason


def test_recommend_model_keeps_current_model_on_moderate_score():
    model, reason = recommend_model(
        model_name="gpt-4o-mini", tokens_input=100, tokens_output=50, uses_tools=False, score=0.75,
    )
    assert model == "gpt-4o-mini"
    assert "no change recommended" in reason


def test_recommend_model_respects_tool_requirement():
    # mistral-small-latest doesn't support tools -- a tool-using call must
    # never be "recommended" toward a model that can't even make the call.
    model, _ = recommend_model(
        model_name="mistral-small-latest", tokens_input=10, tokens_output=10, uses_tools=True, score=0.2,
    )
    from agentguard.llm.catalog import MODEL_CATALOG

    entry = next(e for e in MODEL_CATALOG if e.model == model)
    assert entry.supports_tools


def test_recommend_model_with_unknown_model_name_still_returns_a_candidate():
    model, reason = recommend_model(
        model_name="some-custom-finetune", tokens_input=10, tokens_output=10, uses_tools=False, score=0.1,
    )
    assert model is not None
    assert reason


async def test_build_trace_judgment_embeds_run_id_and_step_id():
    provider = _ScriptedProvider('{"score": 0.9, "verdict": "good", "issues": [], "suggested_fix": ""}')
    step = _make_step()

    import agentguard.evaluation.trace_judge as trace_judge_module

    original = trace_judge_module.get_default_provider
    trace_judge_module.get_default_provider = lambda: provider
    try:
        recommendation = await build_trace_judgment(workspace_id="ws-1", run_id="run-1", step=step)
    finally:
        trace_judge_module.get_default_provider = original

    assert recommendation.kind == TRACE_JUDGMENT_KIND
    assert recommendation.subject_id == step.id
    assert recommendation.workspace_id == "ws-1"
    assert recommendation.recommendation["run_id"] == "run-1"
    assert recommendation.recommendation["step_id"] == step.id
    assert recommendation.recommendation["score"] == 0.9
    assert recommendation.recommendation["verdict"] == "good"
    assert recommendation.evidence_ids == [step.id]


async def test_record_trace_judgment_persists_a_fetchable_recommendation():
    import agentguard.evaluation.trace_judge as trace_judge_module

    provider = _ScriptedProvider('{"score": 0.4, "verdict": "needs_improvement", "issues": ["off-topic"], "suggested_fix": "add context"}')
    original = trace_judge_module.get_default_provider
    trace_judge_module.get_default_provider = lambda: provider
    try:
        repository = InMemoryRunRepository()
        step = _make_step()
        await record_trace_judgment(repository, "ws-1", "run-1", step)
        rows = await repository.list_recommendations(kind=TRACE_JUDGMENT_KIND, workspace_id="ws-1")
    finally:
        trace_judge_module.get_default_provider = original

    assert len(rows) == 1
    assert rows[0]["subject_id"] == step.id
    assert rows[0]["recommendation"]["verdict"] == "needs_improvement"
    assert rows[0]["recommendation"]["suggested_fix"] == "add context"


async def test_record_trace_judgment_never_raises_when_provider_fails():
    import agentguard.evaluation.trace_judge as trace_judge_module

    class _ExplodingProvider(ModelProvider):
        async def complete(self, prompt: str) -> str:
            raise RuntimeError("provider is down")

    original = trace_judge_module.get_default_provider
    trace_judge_module.get_default_provider = lambda: _ExplodingProvider()
    try:
        repository = InMemoryRunRepository()
        step = _make_step()
        await record_trace_judgment(repository, "ws-1", "run-1", step)  # must not raise
        rows = await repository.list_recommendations(kind=TRACE_JUDGMENT_KIND, workspace_id="ws-1")
    finally:
        trace_judge_module.get_default_provider = original

    assert rows == []
