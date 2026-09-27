"""diagnose_evaluation_failure (agentguard/evaluation/diagnose.py) —
closes the loop: WHY a metric failed, WHERE in code, WHAT prompt might
fix it. Covers: no source_step_id -> unavailable, a real fake-provider
round trip producing a full diagnosis (including the real call-site
code_file/function/lineno now captured for successful calls), the
deterministic stand-in never fabricating a suggestion, a provider
failure degrading gracefully, and the unknown-result-id error.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.evaluation import EvaluationResult, diagnose_evaluation_failure
from agentguard.llm.provider import DeterministicTestProvider, ModelProvider
from agentguard.tracing import wrap_llm_client

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


class _FakeOpenAIResponse:
    def __init__(self, text):
        self.model = "gpt-4o-mini"
        self.choices = [type("C", (), {"message": type("M", (), {"content": text, "tool_calls": None})()})]
        self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()


class _FakeOpenAIClient:
    class chat:
        class completions:
            @staticmethod
            async def create(**kwargs):
                return _FakeOpenAIResponse("Paris is the capital of France, population 2 million.")


class _FakeSuggestionProvider(ModelProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    async def complete(self, prompt: str) -> str:
        return self._response


class _RaisingProvider(ModelProvider):
    async def complete(self, prompt: str) -> str:
        raise RuntimeError("provider unreachable")


async def _seed_run_with_llm_call(repo) -> tuple[str, str]:
    """Real @monitor + wrap_llm_client call, so code_file/function/
    lineno come from the ACTUAL call-site capture path, not a
    hand-built TraceStep."""

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        client = wrap_llm_client(_FakeOpenAIClient())
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "What is the capital of France?"}]
        )
        return response.choices[0].message.content

    await agent("q")
    run_id = next(iter(repo.runs))
    steps = await repo.list_trace_steps_for_run(run_id)
    llm_step = next(s for s in steps if s["kind"] == "llm_call")
    return run_id, llm_step["id"]


async def _save_failing_eval_result(repo, *, source_step_id: str | None, reason: str) -> str:
    result = EvaluationResult(
        evaluation_run_id="eval-run-1", source_run_id="run-1", source_step_id=source_step_id,
        metric="faithfulness", score=0.2, passed=False, reason=reason,
    )
    await repo.save_evaluation_result(result)
    return result.id


class TestDiagnoseEvaluationFailure:
    async def test_no_source_step_id_is_unavailable(self, fake_repository):
        result_id = await _save_failing_eval_result(fake_repository, source_step_id=None, reason="score too low")

        diagnosis = await diagnose_evaluation_failure(fake_repository, result_id)

        assert diagnosis.available is False
        assert "no source_step_id" in diagnosis.unavailable_reason

    async def test_unknown_result_id_raises(self, fake_repository):
        with pytest.raises(ValueError, match="no EvaluationResult"):
            await diagnose_evaluation_failure(fake_repository, "does-not-exist")

    async def test_full_diagnosis_with_a_real_fake_provider(self, fake_repository):
        run_id, step_id = await _seed_run_with_llm_call(fake_repository)
        result_id = await _save_failing_eval_result(
            fake_repository, source_step_id=step_id,
            reason="claims a population figure not present in any retrieved context",
        )
        provider = _FakeSuggestionProvider(
            '{"suggested_prompt": "Answer using ONLY the provided context. If population is not given, say so.", '
            '"explanation": "Added an explicit instruction not to invent unsupported facts."}'
        )

        diagnosis = await diagnose_evaluation_failure(fake_repository, result_id, provider=provider)

        assert diagnosis.available is True
        assert diagnosis.why == "claims a population figure not present in any retrieved context"
        # Real call-site capture (recording.py's _find_call_site), not a hand-built value.
        assert diagnosis.code_file == __file__
        assert diagnosis.code_function == "agent"
        assert isinstance(diagnosis.code_lineno, int)
        assert diagnosis.original_prompt == [{"role": "user", "content": "What is the capital of France?"}]
        assert "Paris" in diagnosis.actual_output
        assert "ONLY the provided context" in diagnosis.suggested_prompt
        assert "unsupported facts" in diagnosis.suggestion_explanation

    async def test_deterministic_stand_in_never_fabricates_a_suggestion(self, fake_repository):
        run_id, step_id = await _seed_run_with_llm_call(fake_repository)
        result_id = await _save_failing_eval_result(fake_repository, source_step_id=step_id, reason="low score")

        diagnosis = await diagnose_evaluation_failure(
            fake_repository, result_id, provider=DeterministicTestProvider(simulated_latency_s=0.0)
        )

        assert diagnosis.available is False
        assert diagnosis.suggested_prompt is None
        assert "did not return a suggested_prompt" in diagnosis.unavailable_reason
        # WHERE is still real, even though WHAT (the prompt suggestion) is unavailable.
        assert diagnosis.code_file == __file__

    async def test_provider_failure_degrades_gracefully(self, fake_repository):
        run_id, step_id = await _seed_run_with_llm_call(fake_repository)
        result_id = await _save_failing_eval_result(fake_repository, source_step_id=step_id, reason="low score")

        diagnosis = await diagnose_evaluation_failure(fake_repository, result_id, provider=_RaisingProvider())

        assert diagnosis.available is False
        assert "provider unreachable" in diagnosis.unavailable_reason
        assert diagnosis.code_file == __file__  # WHERE was already resolved before the provider call failed
