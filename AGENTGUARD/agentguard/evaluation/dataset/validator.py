"""GoldenDatasetValidator — Phase 3 (design doc §9): checks a dataset
example against real, retrieved evidence rather than trusting the
developer-supplied `expected_answer` as ground truth.

Anti-hallucination guards, in the order they run:

1. No evidence retrieved -> UNSUPPORTED, deterministically, WITHOUT
   ever calling the judge. An empty retrieval is real information; a
   judge asked to classify with nothing to cite would have to either
   refuse (fine) or fabricate a plausible-sounding answer from its own
   training knowledge (not fine) — so this path never reaches it.
2. The judge's own verdict is REJECTED if it cites an evidence id
   outside what was actually retrieved, OR if it classifies VERIFIED/
   INCORRECT with no citation at all — those two classifications
   assert that SPECIFIC evidence does or doesn't support the answer,
   so an empty citation there is exactly the uncited "looks correct to
   me" guess this guard exists to catch. A classification of
   UNSUPPORTED/AMBIGUOUS/OUTDATED legitimately has NO citation (the
   whole point of those labels is "nothing retrieved settles this"),
   so an empty list there is trusted, not penalized.
3. A VERIFIED verdict is downgraded if a second, adversarially-framed
   judge call disagrees — catches the base judge's own tendency to
   rubber-stamp a plausible-looking answer.
4. `validation_confidence` is a small, documented, deterministic
   formula over the CITED evidence's own retrieval scores and count —
   never the judge's self-reported certainty, which is known to be
   poorly calibrated.

What this validator does NOT do: detect a contradiction between free-
text evidence and the expected answer without a judge call (that is a
genuine natural-language-understanding problem, not something a
deterministic string check can safely claim to solve) — the string-
match check below is a confidence SIGNAL, not a substitute for the
judge, and never overrides its classification on its own.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ...models import DatasetExample, Evidence, GoldenStatus
from ...storage.repository import RunRepository
from .sources.base import EvidenceSource


@dataclass
class JudgeVerdict:
    classification: GoldenStatus
    cited_evidence_ids: list[str] = field(default_factory=list)
    explanation: str = ""


JudgeFn = Callable[[DatasetExample, list[Evidence]], Awaitable[JudgeVerdict]]

_AGREEMENT_BONUS_PER_EXTRA_PASSAGE = 0.05
_MAX_AGREEMENT_BONUS = 0.15
_STRING_MATCH_BONUS = 0.1
_CLASSIFICATIONS_REQUIRING_CITATION = {"VERIFIED", "INCORRECT"}
"""Only these two assert that SPECIFIC evidence settles the question —
an empty citation there is an unfounded guess. UNSUPPORTED/AMBIGUOUS/
OUTDATED/NEEDS_REVIEW from the judge itself legitimately have no
citation (that IS the finding), so they're never penalized for it."""


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _string_match_found(expected_answer: str, evidence: list[Evidence]) -> bool:
    needle = _normalize(expected_answer)
    return bool(needle) and any(needle in _normalize(e.text) for e in evidence)


def _compute_confidence(cited: list[Evidence], expected_answer: str) -> float:
    if not cited:
        return 0.0
    mean_score = sum(e.retrieval_score for e in cited) / len(cited)
    agreement_bonus = min(_MAX_AGREEMENT_BONUS, _AGREEMENT_BONUS_PER_EXTRA_PASSAGE * (len(cited) - 1))
    match_bonus = _STRING_MATCH_BONUS if _string_match_found(expected_answer, cited) else 0.0
    return max(0.0, min(1.0, mean_score + agreement_bonus + match_bonus))


class GoldenDatasetValidator:
    def __init__(
        self,
        repository: RunRepository,
        source: EvidenceSource,
        judge: JudgeFn,
        *,
        adversarial_judge: JudgeFn | None = None,
    ) -> None:
        self._repository = repository
        self._source = source
        self._judge = judge
        self._adversarial_judge = adversarial_judge

    async def validate_example(self, example: DatasetExample) -> DatasetExample:
        raw_evidence = await self._source.retrieve(example.question)
        evidence = [e.model_copy(update={"example_id": example.id}) for e in raw_evidence]

        if not evidence:
            return await self._finalize(example, "UNSUPPORTED", 0.0, "no evidence retrieved for this question")

        verdict = await self._judge(example, evidence)
        valid_ids = {e.id for e in evidence}
        cited_ids_all_valid = set(verdict.cited_evidence_ids).issubset(valid_ids)
        citation_present_if_required = (
            bool(verdict.cited_evidence_ids) if verdict.classification in _CLASSIFICATIONS_REQUIRING_CITATION else True
        )
        citation_valid = cited_ids_all_valid and citation_present_if_required

        if not citation_valid:
            classification: GoldenStatus = "NEEDS_REVIEW"
            if not cited_ids_all_valid:
                reason = "judge verdict cited evidence outside the retrieved set — rejected, not trusted"
            else:
                reason = (
                    f"judge classified {verdict.classification} with no cited evidence, but that "
                    "classification requires a specific citation — rejected, not trusted"
                )
            cited_ids: set[str] = set()
        else:
            classification = verdict.classification
            reason = verdict.explanation
            cited_ids = set(verdict.cited_evidence_ids)
            if classification == "VERIFIED" and self._adversarial_judge is not None:
                adversarial = await self._adversarial_judge(example, evidence)
                if adversarial.classification != "VERIFIED":
                    classification = "NEEDS_REVIEW"
                    reason = f"adversarial recheck disagreed: {adversarial.explanation}"

        cited_evidence = [e for e in evidence if e.id in cited_ids]
        confidence = _compute_confidence(cited_evidence, example.expected_answer)

        for e in evidence:
            await self._repository.save_evidence(e.model_copy(update={"cited_in_verdict": e.id in cited_ids}))

        return await self._finalize(example, classification, confidence, reason)

    async def _finalize(
        self, example: DatasetExample, classification: GoldenStatus, confidence: float, reason: str
    ) -> DatasetExample:
        updated = example.model_copy(
            update={
                "golden_status": classification,
                "validation_confidence": confidence,
                "validation_reason": reason,
                "validated_at": datetime.now(timezone.utc),
            }
        )
        await self._repository.save_dataset_example(updated)
        return updated
