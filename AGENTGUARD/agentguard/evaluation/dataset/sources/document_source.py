"""DocumentEvidenceSource — chunks one or more local PDF/DOCX/plain-text
files into passages ONCE at construction time (the design doc's §9
"chunked + embedded once at dataset-registration time, cached" —
re-parsing a large PDF on every retrieve() call would be wasteful, and
the file content is assumed static for the process's lifetime), then
scores passages against a question by deterministic word-overlap.

No embedding model is assumed available/configured, so this uses a
real, explainable, zero-dependency ranking (token-overlap ratio) rather
than fabricating a similarity score from an uncalled model — the exact
same "no LLM in the loop unless the caller explicitly wires one" rule
PostgresEvidenceSource follows. A caller wanting embedding-based
ranking should use VectorEvidenceSource instead, supplying their own
embedding/similarity backend.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ....models import Evidence
from .base import EvidenceSource

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _load_pdf_text(path: Path) -> str:
    import pypdf  # deferred: only required if a .pdf is actually loaded

    reader = pypdf.PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _load_docx_text(path: Path) -> str:
    import docx  # deferred: only required if a .docx is actually loaded

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def _load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf_text(path)
    if suffix == ".docx":
        return _load_docx_text(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += max(1, chunk_size - overlap)
    return chunks


class DocumentEvidenceSource(EvidenceSource):
    def __init__(
        self,
        paths: list[str | Path],
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        max_results: int = 5,
    ) -> None:
        self._chunks: list[tuple[str, str]] = []  # (source_ref, chunk_text)
        for raw_path in paths:
            path = Path(raw_path)
            text = _load_text(path)
            self._chunks.extend((str(path), chunk) for chunk in _chunk_text(text, chunk_size, chunk_overlap))
        self._max_results = max_results

    async def retrieve(self, question: str) -> list[Evidence]:
        q_tokens = _tokenize(question)
        if not q_tokens:
            return []
        scored: list[tuple[float, str, str]] = []
        for source_ref, chunk in self._chunks:
            c_tokens = _tokenize(chunk)
            if not c_tokens:
                continue
            overlap = len(q_tokens & c_tokens)
            if overlap == 0:
                continue
            scored.append((overlap / len(q_tokens), source_ref, chunk))
        scored.sort(key=lambda t: -t[0])
        return [
            Evidence(example_id="", source_ref=source_ref, text=chunk, retrieval_score=min(1.0, score))
            for score, source_ref, chunk in scored[: self._max_results]
        ]
