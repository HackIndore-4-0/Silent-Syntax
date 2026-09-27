"""MongoEvidenceSource — a fixed, developer-declared MongoDB query
BUILDER (`filter_fn: question -> find() filter dict`), never an LLM-
generated query against a live database — same rule as
PostgresEvidenceSource. Uses `motor` (the standard async MongoDB
driver) lazily, so `pip install agentguard[mongo]` (or a plain
`pip install motor`) is only needed if this source is actually used.

Disclosure: exercised in this repo's tests against a hand-built fake
collection (no live MongoDB reachable in this dev environment — see
the module's test file), not a real `mongod`/Atlas instance. Smoke-test
against a real MongoDB before relying on this in production, the same
disclosure this codebase already gives for the Ragas adapter.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....models import Evidence
from .base import EvidenceSource

FilterFn = Callable[[str], dict[str, Any]]


class MongoEvidenceSource(EvidenceSource):
    def __init__(
        self,
        connection_string: str,
        database: str,
        collection: str,
        filter_fn: FilterFn,
        *,
        text_field: str = "text",
        score_field: str | None = None,
        max_results: int = 5,
    ) -> None:
        self._connection_string = connection_string
        self._database = database
        self._collection_name = collection
        self._filter_fn = filter_fn
        self._text_field = text_field
        self._score_field = score_field
        self._max_results = max_results
        self._client: Any = None

    def _get_collection(self) -> Any:
        if self._client is None:
            import motor.motor_asyncio  # deferred: only required if this source is actually used

            self._client = motor.motor_asyncio.AsyncIOMotorClient(self._connection_string)
        return self._client[self._database][self._collection_name]

    async def retrieve(self, question: str) -> list[Evidence]:
        collection = self._get_collection()
        query = self._filter_fn(question)
        source_ref = f"{self._database}.{self._collection_name}"

        results: list[Evidence] = []
        cursor = collection.find(query).limit(self._max_results)
        async for doc in cursor:
            text = str(doc.get(self._text_field) or "")
            if not text:
                continue
            score = float(doc[self._score_field]) if self._score_field and self._score_field in doc else 1.0
            results.append(Evidence(example_id="", source_ref=source_ref, text=text, retrieval_score=score))
        return results

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
