"""GoldenDatasetValidator (agentguard/evaluation/dataset/validator.py).

Covers the anti-hallucination guards: no evidence -> UNSUPPORTED
without ever calling the judge, an uncited or out-of-set citation ->
NEEDS_REVIEW, an adversarial recheck downgrading a rubber-stamped
VERIFIED, and the deterministic (never judge-self-reported) confidence
formula.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation.dataset import DatasetExample, GoldenDatasetValidator, JudgeVerdict, StaticEvidenceSource

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


def _example(question="what is the refund window?", expected_answer="30 days") -> DatasetExample:
    return DatasetExample(dataset_version_id="dv-1", question=question, expected_answer=expected_answer)


class TestNoEvidence:
    async def test_no_evidence_is_unsupported_and_never_calls_the_judge(self, fake_repository):
        async def judge_should_not_be_called(example, evidence):
            raise AssertionError("judge must not be called when no evidence was retrieved")

        source = StaticEvidenceSource({})
        validator = GoldenDatasetValidator(fake_repository, source, judge_should_not_be_called)

        result = await validator.validate_example(_example())

        assert result.golden_status == "UNSUPPORTED"
        assert result.validation_confidence == 0.0
        assert "no evidence" in result.validation_reason


class TestCitationEnforcement:
    async def test_uncited_verified_verdict_is_rejected_as_needs_review(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds allowed within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[], explanation="looks right")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "NEEDS_REVIEW"
        assert "no cited evidence" in result.validation_reason

    async def test_uncited_incorrect_verdict_is_also_rejected(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds allowed within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="INCORRECT", cited_evidence_ids=[], explanation="looks wrong")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "NEEDS_REVIEW"

    async def test_uncited_unsupported_verdict_is_trusted_not_penalized(self, fake_repository):
        # UNSUPPORTED's whole point is "nothing retrieved settles this" —
        # an empty citation list there is the CORRECT shape of that
        # finding, not a compliance failure to reject.
        source = StaticEvidenceSource({"q": [("something unrelated", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="UNSUPPORTED", cited_evidence_ids=[], explanation="evidence doesn't address the question")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "UNSUPPORTED"
        assert result.validation_reason == "evidence doesn't address the question"

    async def test_citation_outside_retrieved_set_is_rejected(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds allowed within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=["not-a-real-id"], explanation="looks right")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "NEEDS_REVIEW"

    async def test_citation_outside_retrieved_set_is_rejected_even_for_unsupported(self, fake_repository):
        # The "no citation required for UNSUPPORTED" relaxation must
        # never become "any citation is trusted for UNSUPPORTED" — a
        # bogus/out-of-set id is still rejected regardless of classification.
        source = StaticEvidenceSource({"q": [("refunds allowed within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="UNSUPPORTED", cited_evidence_ids=["not-a-real-id"], explanation="...")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "NEEDS_REVIEW"


class TestValidCitation:
    async def test_valid_citation_produces_the_judges_classification_and_a_computed_confidence(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds allowed within 30 days of purchase", 0.9)]})
        captured_evidence_id = {}

        async def judge(example, evidence):
            captured_evidence_id["id"] = evidence[0].id
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[evidence[0].id], explanation="matches evidence")

        validator = GoldenDatasetValidator(fake_repository, source, judge)
        result = await validator.validate_example(_example(question="q", expected_answer="30 days"))

        assert result.golden_status == "VERIFIED"
        assert result.validation_confidence is not None
        assert 0.9 <= result.validation_confidence <= 1.0  # base score 0.9 + string-match bonus, capped at 1.0

        stored_evidence = await fake_repository.list_evidence_for_example(result.id)
        assert len(stored_evidence) == 1
        assert stored_evidence[0]["cited_in_verdict"] is True

    async def test_multiple_agreeing_passages_increase_confidence_over_a_single_passage(self, fake_repository):
        async def judge_factory(evidence_texts):
            async def judge(example, evidence):
                return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[e.id for e in evidence], explanation="agree")
            return judge

        single_source = StaticEvidenceSource({"q": [("policy text A", 0.7)]})
        single_validator = GoldenDatasetValidator(fake_repository, single_source, await judge_factory(None))
        single_result = await single_validator.validate_example(_example(question="q", expected_answer="nomatch"))

        multi_source = StaticEvidenceSource({"q2": [("policy text A", 0.7), ("policy text B", 0.7), ("policy text C", 0.7)]})
        multi_validator = GoldenDatasetValidator(fake_repository, multi_source, await judge_factory(None))
        multi_result = await multi_validator.validate_example(_example(question="q2", expected_answer="nomatch"))

        assert multi_result.validation_confidence > single_result.validation_confidence


class TestAdversarialRecheck:
    async def test_adversarial_disagreement_downgrades_verified_to_needs_review(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[evidence[0].id], explanation="looks correct")

        async def adversarial_judge(example, evidence):
            return JudgeVerdict(classification="INCORRECT", cited_evidence_ids=[evidence[0].id], explanation="actually the window is 14 days for this SKU")

        validator = GoldenDatasetValidator(fake_repository, source, judge, adversarial_judge=adversarial_judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "NEEDS_REVIEW"
        assert "adversarial recheck disagreed" in result.validation_reason

    async def test_adversarial_agreement_keeps_verified(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[evidence[0].id], explanation="looks correct")

        async def adversarial_judge(example, evidence):
            return JudgeVerdict(classification="VERIFIED", cited_evidence_ids=[evidence[0].id], explanation="no issue found")

        validator = GoldenDatasetValidator(fake_repository, source, judge, adversarial_judge=adversarial_judge)
        result = await validator.validate_example(_example(question="q"))

        assert result.golden_status == "VERIFIED"


class TestPersistence:
    async def test_validated_example_is_persisted_via_the_repository(self, fake_repository):
        source = StaticEvidenceSource({"q": [("refunds within 30 days", 0.9)]})

        async def judge(example, evidence):
            return JudgeVerdict(classification="INCORRECT", cited_evidence_ids=[evidence[0].id], explanation="wrong window")

        example = _example(question="q")
        await fake_repository.save_dataset_example(example)
        validator = GoldenDatasetValidator(fake_repository, source, judge)

        await validator.validate_example(example)

        fetched = await fake_repository.get_dataset_example(example.id)
        assert fetched["golden_status"] == "INCORRECT"
        assert fetched["validated_at"] is not None
