"""StaticEvidenceSource — a fixed, in-memory list of passages per
question. The escape hatch for a source with no live connector yet,
and the deterministic stand-in tests use instead of a real DB/network
call."""
from __future__ import annotations

from ....models import Evidence
from .base import EvidenceSource


class StaticEvidenceSource(EvidenceSource):
    def __init__(self, passages_by_question: dict[str, list[tuple[str, float]]]) -> None:
        self._passages_by_question = passages_by_question

    async def retrieve(self, question: str) -> list[Evidence]:
        passages = self._passages_by_question.get(question, [])
        return [
            Evidence(example_id="", source_ref="static", text=text, retrieval_score=score)
            for text, score in passages
        ]
