"""list_catalog (agentguard/llm/catalog.py) — a static reference table,
so these tests only check the filtering logic, never specific prices
(which are explicitly documented as drifting reference data, not a
contract this suite should pin)."""
from __future__ import annotations

from agentguard.llm.catalog import MODEL_CATALOG, list_catalog


def test_no_filters_returns_the_whole_catalog():
    assert list_catalog() == MODEL_CATALOG


def test_requires_tools_excludes_non_tool_models():
    entries = list_catalog(requires_tools=True)
    assert entries
    assert all(e.supports_tools for e in entries)


def test_requires_vision_excludes_non_vision_models():
    entries = list_catalog(requires_vision=True)
    assert entries
    assert all(e.supports_vision for e in entries)
    assert len(entries) < len(MODEL_CATALOG)  # catalog has at least one non-vision model


def test_min_context_window_excludes_smaller_models():
    entries = list_catalog(min_context_window=500_000)
    assert entries
    assert all(e.context_window >= 500_000 for e in entries)
    assert len(entries) < len(MODEL_CATALOG)


def test_filters_combine():
    entries = list_catalog(requires_tools=True, requires_vision=True, min_context_window=1_000_000)
    assert all(e.supports_tools and e.supports_vision and e.context_window >= 1_000_000 for e in entries)


def test_every_entry_has_a_unique_model_id():
    ids = [e.model for e in MODEL_CATALOG]
    assert len(ids) == len(set(ids))
