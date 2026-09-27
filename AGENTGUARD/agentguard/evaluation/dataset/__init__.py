"""Golden Dataset Validation (Phase 3) — checks a dataset's
question/expected-answer examples against a real, connected evidence
source rather than trusting the developer-supplied answer as ground
truth. See GoldenDatasetValidator's own docstring for the full
deterministic-first / evidence-constrained-judge / adversarial-recheck
pipeline.
"""
from ...models import Dataset, DatasetExample, DatasetVersion, Evidence, GoldenStatus
from .llm_judge import make_llm_judge
from .sources.base import EvidenceSource
from .sources.document_source import DocumentEvidenceSource
from .sources.mongo_source import MongoEvidenceSource
from .sources.static_source import StaticEvidenceSource
from .sources.url_source import URLEvidenceSource
from .sources.vector_source import VectorEvidenceSource
from .validator import GoldenDatasetValidator, JudgeFn, JudgeVerdict

__all__ = [
    "Dataset",
    "DatasetVersion",
    "DatasetExample",
    "Evidence",
    "GoldenStatus",
    "EvidenceSource",
    "StaticEvidenceSource",
    "DocumentEvidenceSource",
    "URLEvidenceSource",
    "MongoEvidenceSource",
    "VectorEvidenceSource",
    "GoldenDatasetValidator",
    "JudgeFn",
    "JudgeVerdict",
    "make_llm_judge",
]
