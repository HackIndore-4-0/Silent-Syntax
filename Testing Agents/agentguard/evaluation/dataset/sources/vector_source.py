"""VectorEvidenceSource — a thin adapter over ANY already-configured
vector database's similarity search, rather than a vendor-specific
integration (Pinecone/Chroma/pgvector/Weaviate/Qdrant all reduce to
"given a query string, return scored passages"). The caller supplies
`query_fn`, built from whichever client SDK they already use — this
class owns none of that connection/embedding logic itself, so it never
needs a hard dependency on any single vector DB's SDK.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from ....models import Evidence
from .base import EvidenceSource

VectorQueryFn = Callable[[str], Awaitable[list[tuple[str, float]]]]
"""question -> [(passage_text, similarity_score), ...], already sorted
best-first by whatever vector DB `query_fn` wraps."""


class VectorEvidenceSource(EvidenceSource):
    def __init__(self, query_fn: VectorQueryFn, *, source_label: str = "vector_db", max_results: int = 5) -> None:
        self._query_fn = query_fn
        self._source_label = source_label
        self._max_results = max_results

    async def retrieve(self, question: str) -> list[Evidence]:
        passages = await self._query_fn(question)
        return [
            Evidence(example_id="", source_ref=self._source_label, text=text, retrieval_score=score)
            for text, score in passages[: self._max_results]
        ]
