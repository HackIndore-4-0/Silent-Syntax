"""make_llm_judge (agentguard/evaluation/dataset/llm_judge.py).

Covers: a well-formed JSON judge response parses correctly, a
malformed response degrades to NEEDS_REVIEW rather than raising, and
pairing with the existing DeterministicTestProvider (no real LLM
configured) always yields the conservative NEEDS_REVIEW fallback —
never a fabricated VERIFIED.
"""
from __future__ import annotations

import pytest

from agentguard.evaluation.dataset import Evidence, make_llm_judge
from agentguard.evaluation.dataset.validator import DatasetExample
from agentguard.llm.provider import DeterministicTestProvider, ModelProvider


class _FakeProvider(ModelProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    async def complete(self, prompt: str) -> str:
        return self._response


def _example():
    return DatasetExample(dataset_version_id="dv-1", question="refund window?", expected_answer="30 days")


class TestMakeLlmJudge:
    async def test_well_formed_response_parses_correctly(self):
        evidence = [Evidence(example_id="", source_ref="s", text="30 days", retrieval_score=0.9)]
        response = (
            '{"classification": "VERIFIED", "cited_evidence_ids": ["%s"], "explanation": "matches"}' % evidence[0].id
        )
        judge = make_llm_judge(_FakeProvider(response))

        verdict = await judge(_example(), evidence)

        assert verdict.classification == "VERIFIED"
        assert verdict.cited_evidence_ids == [evidence[0].id]
        assert verdict.explanation == "matches"

    async def test_malformed_response_degrades_to_needs_review(self):
        judge = make_llm_judge(_FakeProvider("not json at all"))

        verdict = await judge(_example(), [])

        assert verdict.classification == "NEEDS_REVIEW"
        assert verdict.cited_evidence_ids == []

    async def test_deterministic_test_provider_always_yields_needs_review(self):
        # DeterministicTestProvider's canned shape (label/score/...) has no
        # "classification" key — proves the fallback is conservative, not a
        # crash, when paired with the codebase's own non-LLM stand-in.
        judge = make_llm_judge(DeterministicTestProvider(simulated_latency_s=0.0))

        verdict = await judge(_example(), [Evidence(example_id="", source_ref="s", text="x", retrieval_score=0.5)])

        assert verdict.classification == "NEEDS_REVIEW"
