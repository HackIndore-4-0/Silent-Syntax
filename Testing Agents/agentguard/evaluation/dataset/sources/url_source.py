"""URLEvidenceSource — fetches ONE developer-declared URL/API endpoint
per retrieve() call (via httpx) and hands the raw response to a
developer-supplied `extract_fn` to pull out scored passages. The
request URL is fixed at construction time; `params_fn` builds the
query params from the question in a way the developer wrote and
reviewed — never an LLM-generated request against a live endpoint,
same rule as PostgresEvidenceSource/MongoEvidenceSource.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....models import Evidence
from .base import EvidenceSource

ExtractFn = Callable[[Any], list[tuple[str, float]]]
ParamsFn = Callable[[str], dict[str, Any]]


class URLEvidenceSource(EvidenceSource):
    def __init__(
        self,
        url: str,
        extract_fn: ExtractFn,
        *,
        params_fn: ParamsFn | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
        max_results: int = 5,
        transport: Any = None,
    ) -> None:
        self._url = url
        self._extract_fn = extract_fn
        self._params_fn = params_fn
        self._headers = headers or {}
        self._timeout_s = timeout_s
        self._max_results = max_results
        self._transport = transport
        """Real requests use httpx's default transport; tests inject a
        real `httpx.MockTransport` here — genuine httpx request/response
        handling exercised end to end, just without real network I/O."""

    async def retrieve(self, question: str) -> list[Evidence]:
        import httpx  # deferred: only required if this source is actually used

        params = self._params_fn(question) if self._params_fn else {"q": question}
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            response = await client.get(self._url, params=params, headers=self._headers)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                data = response.text

        passages = self._extract_fn(data)
        return [
            Evidence(example_id="", source_ref=self._url, text=text, retrieval_score=score)
            for text, score in passages[: self._max_results]
        ]
