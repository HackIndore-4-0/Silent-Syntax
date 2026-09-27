"""E2E Demo 5 — CI GATE (Phase 4 spec §31).

Baseline constraint_adherence = 96%, candidate = 88%, threshold = 5
percentage points -> regression = 8 points -> BLOCK, non-zero exit.
Then a candidate within threshold -> PASS, exit code 0.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.ci.gate import run_ci_gate


async def _make_batch(n_pass: int, n_fail: int) -> None:
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def compliant_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "within budget"

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def violating_agent(task: str) -> str:
        agentguard.update_state(max_budget=70000)
        return "over budget"

    for _ in range(n_pass):
        await compliant_agent("task")
    for _ in range(n_fail):
        await violating_agent("task")


async def test_demo5_ci_gate_blocks_an_8_point_regression(fake_repository):
    # Baseline: 24/25 pass -> 96% constraint adherence.
    await _make_batch(24, 1)
    baseline_ids = list(fake_repository.runs.keys())

    # Candidate: 22/25 pass -> 88% constraint adherence.
    await _make_batch(22, 3)
    candidate_ids = [rid for rid in fake_repository.runs if rid not in baseline_ids]

    result = await run_ci_gate(fake_repository, baseline_ids, candidate_ids, threshold_pct=5.0)

    assert result.result == "block"
    assert round(result.regressions["constraint_adherence"], 1) == -8.0  # 8 percentage points, negative = regression


async def test_demo5_ci_gate_allows_a_within_threshold_candidate(fake_repository):
    # Baseline: 24/25 pass -> 96%.
    await _make_batch(24, 1)
    baseline_ids = list(fake_repository.runs.keys())

    # Candidate: 23/25 pass -> 92% (a 4-point regression, within the 5-point threshold).
    await _make_batch(23, 2)
    candidate_ids = [rid for rid in fake_repository.runs if rid not in baseline_ids]

    result = await run_ci_gate(fake_repository, baseline_ids, candidate_ids, threshold_pct=5.0)

    assert result.result == "pass"
    assert "constraint_adherence" not in result.regressions


def test_demo5_ci_gate_cli_exit_codes(fake_repository):
    import asyncio

    from typer.testing import CliRunner

    from agentguard.cli import app

    asyncio.run(_make_batch(24, 1))
    baseline_ids = list(fake_repository.runs.keys())
    asyncio.run(_make_batch(22, 3))
    candidate_ids = [rid for rid in fake_repository.runs if rid not in baseline_ids]

    runner = CliRunner()
    blocked = runner.invoke(
        app, ["ci-gate", "--baseline-runs", ",".join(baseline_ids), "--candidate-runs", ",".join(candidate_ids), "--threshold", "5"]
    )
    assert blocked.exit_code == 1
    assert "BLOCK" in blocked.output

    passing = runner.invoke(
        app, ["ci-gate", "--baseline-runs", ",".join(baseline_ids), "--candidate-runs", ",".join(baseline_ids), "--threshold", "5"]
    )
    assert passing.exit_code == 0
    assert "PASS" in passing.output
