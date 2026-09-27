"""Backward-compatible re-export.

The in-memory RunRepository moved to `agentguard.storage.memory` in
Phase 3 so examples/budget_failure_recovery.py can reuse the exact same
class this test suite already relies on (no reachable PostgreSQL in this
environment — see docs/EXECUTION_REPORT_PHASE_3.md §1). Existing test
imports (`from tests.fakes import InMemoryRunRepository` /
`from fakes import InMemoryRunRepository`) keep working unchanged.
"""
from __future__ import annotations

from agentguard.storage.memory import InMemoryRunRepository

__all__ = ["InMemoryRunRepository"]
