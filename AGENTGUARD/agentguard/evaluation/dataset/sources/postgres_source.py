"""PostgresEvidenceSource — a PARAMETERIZED query template only.

`query` is a fixed SQL string with a single placeholder for the
question (e.g. "SELECT text, ts_rank(...) AS score FROM policies WHERE
to_tsvector(text) @@ plainto_tsquery($1) ORDER BY score DESC LIMIT 5"),
declared once by the developer at dataset-registration time and
reviewed like any other application code. This class NEVER generates
SQL itself and never accepts a query built from a golden example or an
LLM — that would be exactly the "hand an LLM live write/query
credentials to a production DB" risk the design doc's security section
(§21) rules out.
"""
from __future__ import annotations

from typing import Any

from ....models import Evidence
from .base import EvidenceSource


class PostgresEvidenceSource(EvidenceSource):
    def __init__(
        self,
        dsn: str,
        query: str,
        *,
        text_column: str = "text",
        score_column: str | None = None,
        max_results: int = 5,
    ) -> None:
        self._dsn = dsn
        self._query = query
        self._text_column = text_column
        self._score_column = score_column
        self._max_results = max_results
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            import asyncpg  # deferred: only required if this source is actually used

            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        return self._pool

    async def retrieve(self, question: str) -> list[Evidence]:
        pool = await self._get_pool()
        rows = await pool.fetch(self._query, question)
        source_ref = self._dsn.rsplit("@", 1)[-1]  # never include credentials in a stored source_ref
        results = []
        for row in rows[: self._max_results]:
            d = dict(row)
            score = float(d[self._score_column]) if self._score_column else 1.0
            results.append(Evidence(example_id="", source_ref=source_ref, text=str(d[self._text_column]), retrieval_score=score))
        return results

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
