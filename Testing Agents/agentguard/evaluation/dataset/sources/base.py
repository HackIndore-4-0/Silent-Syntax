"""EvidenceSource interface — a dataset example is validated against
whatever a source ACTUALLY retrieves, never against a judge model's own
training-data knowledge (design doc §9/§21: no LLM-generated queries
against a live source, ever — a source's query shape is fixed at
registration time by the developer, reviewed like any other code)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ....models import Evidence


class EvidenceSource(ABC):
    @abstractmethod
    async def retrieve(self, question: str) -> list[Evidence]:
        """Return real, sourced passages relevant to `question`. An
        empty list is a valid, honest answer ("no evidence found") —
        GoldenDatasetValidator treats that as UNSUPPORTED, never as a
        reason to fall back on the judge's own knowledge."""
        raise NotImplementedError
