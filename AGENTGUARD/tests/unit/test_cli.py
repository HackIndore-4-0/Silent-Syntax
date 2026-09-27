"""CLI tests are plain (non-async) `def` functions: Typer commands call
`asyncio.run(...)` internally (a real CLI process has no event loop
already running), so the test driving them must not itself be inside a
pytest-asyncio event loop — same reasoning as
tests/integration/test_websocket_approval.py's `asyncio.run(...)`-based
tests. Setup uses `asyncio.run()` explicitly instead of `await`.
"""
from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

import agentguard
from agentguard import Policy, monitor
from agentguard.cli import app

from ..fakes import InMemoryRunRepository

runner = CliRunner()


@pytest.fixture
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


def _make_runs(fake_repository) -> tuple[str, str]:
    @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "over"

    @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
    async def good_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(drifting_agent("find a laptop"))
    asyncio.run(good_agent("find a laptop safely"))
    ids = list(fake_repository.runs.keys())
    return ids[0], ids[1]


def test_cli_report_uses_real_reliability_report(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["report", run_a])
    assert result.exit_code == 0
    assert "Status: stop" in result.output
    assert "constraint_adherence" in result.output
    assert "Root cause:" in result.output


def test_cli_report_json_matches_report_builder(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["report", run_a, "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["status"] == "stop"
    assert set(data["dimensions"]) == {
        "correctness", "goal_completion", "constraint_adherence",
        "decision_consistency", "tool_usage", "behavioral_reliability",
    }


def test_cli_report_unknown_run_exits_nonzero(fake_repository):
    result = runner.invoke(app, ["report", "does-not-exist"])
    assert result.exit_code != 0


def test_cli_replay_shows_steps_in_safe_mode(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["replay", run_a])
    assert result.exit_code == 0
    assert "REPLAY MODE" in result.output
    assert "NO EXTERNAL SIDE EFFECTS" in result.output
    assert "Step 1" in result.output


def test_cli_replay_json(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["replay", run_a, "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["mode"] == "safe"
    assert data["no_external_side_effects"] is True
    assert len(data["steps"]) > 0


def test_cli_compare_shows_metric_run_a_run_b_delta_columns(fake_repository):
    run_a, run_b = _make_runs(fake_repository)
    result = runner.invoke(app, ["compare", run_a, run_b])
    assert result.exit_code == 0
    assert "metric" in result.output
    assert "run A" in result.output
    assert "run B" in result.output
    assert "delta" in result.output
    assert "constraint_adherence" in result.output
    # never a subjective winner
    assert "winner" not in result.output.lower()
    assert "better" not in result.output.lower()


def test_cli_verify_audit_reports_intact(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["verify-audit", run_a])
    assert result.exit_code == 0
    assert "events checked" in result.output
    assert "AUDIT INTACT" in result.output


def test_cli_verify_audit_reports_violation_and_first_broken_event(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    stored = fake_repository.audit_events[run_a]
    tampered_id = stored[2].id
    stored[2].payload["tampered"] = True

    result = runner.invoke(app, ["verify-audit", run_a])
    assert result.exit_code == 1
    assert "AUDIT INTEGRITY VIOLATION" in result.output
    assert tampered_id in result.output


def test_cli_ci_gate_pass_exits_zero(fake_repository):
    run_a, run_b = _make_runs(fake_repository)
    # candidate (run_b, CONTINUE) is not worse than baseline (run_b itself)
    result = runner.invoke(app, ["ci-gate", "--baseline-runs", run_b, "--candidate-runs", run_b, "--threshold", "5"])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_ci_gate_block_exits_nonzero(fake_repository):
    run_a, run_b = _make_runs(fake_repository)
    # baseline = good run, candidate = bad run -> constraint_adherence regresses hard
    result = runner.invoke(app, ["ci-gate", "--baseline-runs", run_b, "--candidate-runs", run_a, "--threshold", "5"])
    assert result.exit_code == 1
    assert "BLOCK" in result.output


def test_cli_tail_shows_chronological_events(fake_repository):
    run_a, _ = _make_runs(fake_repository)
    result = runner.invoke(app, ["tail", run_a])
    assert result.exit_code == 0
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert "RUN_START" in lines[0]
    assert "RUN_COMPLETION" in lines[-1]
