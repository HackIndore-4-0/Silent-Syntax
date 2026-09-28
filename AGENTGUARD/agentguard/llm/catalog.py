"""A small, static reference table of well-known models' declared
capabilities and list prices — NOT a live pricing feed and NOT a claim
about observed quality (that comes from ModelProfileEngine/
ModelBenchmarkEngine, computed from this workspace's own real call
history). This exists only so the "Run Experiment" UI can offer a
sensible candidate-model picker and so a caller can filter candidates by
a hard requirement (tool calling, vision, minimum context window)
*before* spending real money running a benchmark against a model that
could never have satisfied the request anyway.

Prices/context windows are provider-published figures at the time this
table was written and will drift — this is intentionally a short,
manually-curated list (a dozen or so mainstream models), not an attempt
at an exhaustive or auto-updating catalog. Model ids are litellm-style
strings, matching what agentguard.tracing.litellm_wrap already passes to
`litellm.acompletion`/`litellm.completion_cost`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCatalogEntry:
    provider: str
    model: str
    context_window: int
    input_price_per_1k: float
    output_price_per_1k: float
    supports_tools: bool
    supports_vision: bool
    supports_structured_output: bool
    capabilities: list[str] = field(default_factory=list)


MODEL_CATALOG: list[ModelCatalogEntry] = [
    ModelCatalogEntry(
        provider="openai", model="gpt-4o", context_window=128_000,
        input_price_per_1k=0.0025, output_price_per_1k=0.01,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["reasoning", "coding", "rag", "tool_calling", "vision"],
    ),
    ModelCatalogEntry(
        provider="openai", model="gpt-4o-mini", context_window=128_000,
        input_price_per_1k=0.00015, output_price_per_1k=0.0006,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["question_answering", "summarization", "classification", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="openai", model="gpt-4.1-mini", context_window=1_047_576,
        input_price_per_1k=0.0004, output_price_per_1k=0.0016,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["long_context", "rag", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="openai", model="o4-mini", context_window=200_000,
        input_price_per_1k=0.0011, output_price_per_1k=0.0044,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["reasoning", "coding", "agent_planning"],
    ),
    ModelCatalogEntry(
        provider="anthropic", model="claude-3-5-sonnet-20241022", context_window=200_000,
        input_price_per_1k=0.003, output_price_per_1k=0.015,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["reasoning", "coding", "rag", "tool_calling", "agent_planning"],
    ),
    ModelCatalogEntry(
        provider="anthropic", model="claude-3-5-haiku-20241022", context_window=200_000,
        input_price_per_1k=0.0008, output_price_per_1k=0.004,
        supports_tools=True, supports_vision=False, supports_structured_output=True,
        capabilities=["question_answering", "classification", "extraction", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="anthropic", model="claude-haiku-4-5-20251001", context_window=200_000,
        input_price_per_1k=0.001, output_price_per_1k=0.005,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["question_answering", "coding", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="google", model="gemini-1.5-pro", context_window=2_000_000,
        input_price_per_1k=0.00125, output_price_per_1k=0.005,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["long_context", "rag", "multimodal", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="google", model="gemini-1.5-flash", context_window=1_000_000,
        input_price_per_1k=0.000075, output_price_per_1k=0.0003,
        supports_tools=True, supports_vision=True, supports_structured_output=True,
        capabilities=["long_context", "summarization", "classification", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="mistral", model="mistral-large-latest", context_window=128_000,
        input_price_per_1k=0.002, output_price_per_1k=0.006,
        supports_tools=True, supports_vision=False, supports_structured_output=True,
        capabilities=["reasoning", "coding", "tool_calling"],
    ),
    ModelCatalogEntry(
        provider="mistral", model="mistral-small-latest", context_window=128_000,
        input_price_per_1k=0.0002, output_price_per_1k=0.0006,
        supports_tools=True, supports_vision=False, supports_structured_output=True,
        capabilities=["question_answering", "classification", "extraction"],
    ),
    ModelCatalogEntry(
        provider="deepseek", model="deepseek-chat", context_window=64_000,
        input_price_per_1k=0.00027, output_price_per_1k=0.0011,
        supports_tools=True, supports_vision=False, supports_structured_output=True,
        capabilities=["reasoning", "coding"],
    ),
]


def list_catalog(
    *,
    requires_tools: bool | None = None,
    requires_vision: bool | None = None,
    min_context_window: int | None = None,
) -> list[ModelCatalogEntry]:
    entries = MODEL_CATALOG
    if requires_tools:
        entries = [e for e in entries if e.supports_tools]
    if requires_vision:
        entries = [e for e in entries if e.supports_vision]
    if min_context_window is not None:
        entries = [e for e in entries if e.context_window >= min_context_window]
    return entries
