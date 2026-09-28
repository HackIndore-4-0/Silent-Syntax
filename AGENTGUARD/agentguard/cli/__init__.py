"""AgentGuard CLI — Phase 4.

    agentguard init-db                                   (Phase 1)
    agentguard tail <run_id> [--follow]                   (Phase 4)
    agentguard replay <run_id> [--json]                   (Phase 4)
    agentguard report <run_id> [--json]                   (Phase 4)
    agentguard compare <run_a> <run_b> [--json]           (Phase 4)
    agentguard ci-gate --baseline-runs=... --candidate-runs=... --threshold=5
    agentguard verify-audit <run_id> [--json]             (Phase 4)

Every command calls the real AgentGuard APIs (replay engine, Reliability
Report builder, regression comparison, CI gate, audit-chain
verification) against whatever repository `agentguard._runtime.get_repository()`
resolves to — the real `PostgresRunRepository.from_env()` by default, or
whatever a caller `agentguard.configure()`d first (tests configure an
in-memory repository this way; see tests/unit/test_cli.py). None of
these commands print hard-coded sample text.

Built on Typer (a thin wrapper over Click, already a transitive
dependency here) so `agentguard --help` documents every command and
option automatically.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Optional

import typer

app = typer.Typer(name="agentguard", help="AgentGuard CLI", no_args_is_help=True, add_completion=False)

_PERCENT_METRICS = {
    "correctness",
    "goal_completion",
    "constraint_adherence",
    "decision_consistency",
    "tool_usage",
    "behavioral_reliability",
    "risk",
    "confidence",
}

_TERMINAL_STATUSES = {"continue", "stop", "failed", "rolled_back"}


def _status_str(value: Any) -> str:
    """Normalizes a RunStatus (possibly still a Python enum instance,
    not yet JSON-serialized) to its plain string value for display."""
    return str(getattr(value, "value", value))


def _fmt(value: float | None, *, metric: str | None = None, signed: bool = False) -> str:
    if value is None:
        return "unavailable"
    if metric in _PERCENT_METRICS:
        v = value * 100
        return f"{v:+.0f}%" if signed else f"{v:.0f}%"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


# -- init-db (Phase 1, unchanged) -------------------------------------------


async def _init_db() -> None:
    from ..storage.postgres import PostgresRunRepository

    repo = PostgresRunRepository.from_env()
    await repo.init_schema()
    await repo.close()
    typer.echo("agentguard: schema created/verified.")


@app.command(name="init-db", help="Create/upgrade AgentGuard's PostgreSQL schema.")
def init_db() -> None:
    asyncio.run(_init_db())


# -- tail --------------------------------------------------------------------


async def _tail(run_id: str, follow: bool, interval: float) -> int:
    from .._runtime import get_repository

    repo = get_repository()
    run = await repo.get_run(run_id)
    if run is None:
        typer.echo(f"run {run_id!r} not found", err=True)
        return 1

    seen = 0
    while True:
        events = await repo.list_audit_events(run_id)
        for event in events[seen:]:
            ts = event.get("created_at")
            ts_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
            typer.echo(f"{ts_str} {event['event_type']}")
        seen = len(events)

        run = await repo.get_run(run_id)
        terminal = run is not None and run.get("status") in _TERMINAL_STATUSES
        if not follow or terminal:
            if follow and terminal:
                typer.echo(f"-- run reached terminal status {run['status']!r}; stopping --")
            return 0
        await asyncio.sleep(interval)


@app.command(help="Display a run's chronological audit-event stream.")
def tail(
    run_id: str,
    follow: bool = typer.Option(
        False,
        "--follow",
        help=(
            "Poll for new events until the run reaches a terminal status. "
            "This is a correct HISTORICAL polling stream, not a live push "
            "subscription — AgentGuard has no general event pub/sub outside "
            "the dashboard's HUMAN-approval WebSocket channel (see README.md)."
        ),
    ),
    interval: float = typer.Option(2.0, "--interval", help="Seconds between polls in --follow mode."),
) -> None:
    raise typer.Exit(code=asyncio.run(_tail(run_id, follow, interval)))


# -- report --------------------------------------------------------------------


async def _report(run_id: str, json_output: bool) -> int:
    from .._runtime import get_repository
    from ..reliability.report import build_reliability_report

    repo = get_repository()
    run = await repo.get_run(run_id)
    if run is None:
        typer.echo(f"run {run_id!r} not found", err=True)
        return 1

    risk_assessments = await repo.list_risk_assessments(run_id)
    root_cause = await repo.get_root_cause(run_id)
    audit_events = await repo.list_audit_events(run_id)
    rpt = build_reliability_report(run, risk_assessments=risk_assessments, root_cause=root_cause, audit_events=audit_events)
    data = rpt.to_dict()

    if json_output:
        typer.echo(json.dumps(data, indent=2, default=str))
        return 0

    typer.echo(f"Run: {run_id}")
    typer.echo(f"Status: {_status_str(data['status'])}")
    typer.echo(f"Policy version: {(run.get('policy') or {}).get('version')}")
    typer.echo("")
    for name, dim in data["dimensions"].items():
        typer.echo(f"  {name:<24}{_fmt(dim['value'])}")
    typer.echo("")
    typer.echo(f"Risk: {data['risk']}")
    typer.echo(f"Confidence: {_fmt(data['confidence'])}")
    typer.echo(f"Recovery: {data['recovery']['status']}")
    typer.echo(f"Root cause: {data['root_cause_summary'] or 'none'}")
    typer.echo("Decision history:")
    for d in run.get("decisions") or []:
        typer.echo(f"  {_status_str(d['outcome'])}: {d['reason']}")
    return 0


@app.command(help="Six-dimension Reliability Report for a run (reuses the Phase 3 report builder).")
def report(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    raise typer.Exit(code=asyncio.run(_report(run_id, json_output)))


# -- replay --------------------------------------------------------------------


async def _replay(run_id: str, json_output: bool) -> int:
    from .._runtime import get_repository
    from ..replay import replay as replay_run

    repo = get_repository()
    try:
        session = await replay_run(repo, run_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        return 1

    if json_output:
        payload = {
            "run_id": session.run_id,
            "mode": session.mode,
            "no_external_side_effects": session.no_external_side_effects,
            "task": session.task,
            "policy_version": session.policy_version,
            "agent_version": session.agent_version,
            "root_cause": session.root_cause,
            "risk": session.risk,
            "confidence": session.confidence,
            "steps": [{"step": s.step, "label": s.label, "kind": s.kind, "data": s.data} for s in session.steps],
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return 0

    typer.echo("REPLAY MODE")
    typer.echo("NO EXTERNAL SIDE EFFECTS")
    typer.echo(f"run_id: {run_id}")
    typer.echo(f"policy_version: {session.policy_version}   agent_version: {session.agent_version or '—'}")
    for s in session.steps:
        typer.echo(f"\nStep {s.step} [{s.kind}]")
        typer.echo(s.label)
    return 0


@app.command(help="Reconstruct a run step-by-step for inspection. SAFE/DRY-RUN — no external side effects.")
def replay(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    raise typer.Exit(code=asyncio.run(_replay(run_id, json_output)))


# -- compare --------------------------------------------------------------------


async def _compare(run_a: str, run_b: str, json_output: bool) -> int:
    from .._runtime import get_repository
    from ..regression.compare import compare_runs

    repo = get_repository()
    try:
        result = await compare_runs(repo, run_a, run_b)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        return 1

    if json_output:
        typer.echo(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    typer.echo(f"{'metric':<26}{'run A':>12}{'run B':>12}{'delta':>12}")
    for m in result.metrics:
        delta_str = _fmt(m.delta, metric=m.metric, signed=True) if m.available else "n/a"
        typer.echo(f"{m.metric:<26}{_fmt(m.value_a, metric=m.metric):>12}{_fmt(m.value_b, metric=m.metric):>12}{delta_str:>12}")
    return 0


@app.command(help="Factual metric-by-metric delta between two runs. No winner is declared.")
def compare(run_a: str, run_b: str, json_output: bool = typer.Option(False, "--json")) -> None:
    raise typer.Exit(code=asyncio.run(_compare(run_a, run_b, json_output)))


# -- ci-gate --------------------------------------------------------------------


async def _ci_gate(
    baseline_runs: str, candidate_runs: str, threshold: float, protected_metric: Optional[list[str]], json_output: bool
) -> int:
    from .._runtime import get_repository
    from ..ci.gate import CIGateError, run_ci_gate

    repo = get_repository()
    baseline_ids = [x.strip() for x in baseline_runs.split(",") if x.strip()]
    candidate_ids = [x.strip() for x in candidate_runs.split(",") if x.strip()]

    try:
        result = await run_ci_gate(
            repo, baseline_ids, candidate_ids, threshold_pct=threshold, protected_metrics=protected_metric or None
        )
    except (CIGateError, ValueError) as exc:
        typer.echo(f"ci-gate error: {exc}", err=True)
        return 2

    if json_output:
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    else:
        typer.echo(f"protected metrics: {', '.join(result.protected_metrics)}")
        typer.echo(f"threshold: {result.threshold_pct} percentage points")
        if result.regressions:
            typer.echo("Regressions:")
            for k, v in result.regressions.items():
                typer.echo(f"  {k}: {v:+.1f} pct points")
        else:
            typer.echo("No protected metric regressed beyond the threshold.")
        typer.echo(result.result.upper())

    return 0 if result.result == "pass" else 1


@app.command(name="ci-gate", help="Block or allow a deployment based on regression against a baseline corpus.")
def ci_gate(
    baseline_runs: str = typer.Option(..., "--baseline-runs", help="Comma-separated baseline run IDs."),
    candidate_runs: str = typer.Option(..., "--candidate-runs", help="Comma-separated candidate run IDs."),
    threshold: float = typer.Option(5.0, "--threshold", help="Max allowed regression, in percentage points."),
    protected_metric: Optional[list[str]] = typer.Option(
        None, "--protected-metric", help="Repeatable. Defaults to the six Reliability Report dimensions."
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    raise typer.Exit(code=asyncio.run(_ci_gate(baseline_runs, candidate_runs, threshold, protected_metric, json_output)))


# -- verify-audit --------------------------------------------------------------------


async def _verify_audit(run_id: str, json_output: bool) -> int:
    from .._runtime import get_repository
    from ..audit.chain import verify_audit_chain

    repo = get_repository()
    run = await repo.get_run(run_id)
    if run is None:
        typer.echo(f"run {run_id!r} not found", err=True)
        return 1

    result = await verify_audit_chain(repo, run_id)
    if json_output:
        typer.echo(json.dumps(result, indent=2, default=str))
    else:
        typer.echo(f"{result['event_count']} events checked")
        if result["intact"]:
            typer.echo("AUDIT INTACT")
        else:
            typer.echo("AUDIT INTEGRITY VIOLATION")
            typer.echo(f"First broken event: {result['first_invalid_event']}")
    return 0 if result["intact"] else 1


@app.command(name="verify-audit", help="Recompute and verify a run's SHA-256 audit hash chain (reuses Phase 3 verification).")
def verify_audit(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    raise typer.Exit(code=asyncio.run(_verify_audit(run_id, json_output)))


# -- mcp-serve ---------------------------------------------------------------


async def _resolve_workspace_id(api_key: str) -> str:
    from ..auth.service import authenticate_api_key
    from .._runtime import get_repository

    repo = get_repository()
    auth_user = await authenticate_api_key(repo, api_key)
    if auth_user is None:
        typer.echo("agentguard mcp-serve: invalid or revoked API key", err=True)
        raise typer.Exit(code=1)
    return auth_user.api_key.workspace_id


async def _mcp_serve(api_key: str) -> None:
    from ..mcp.server import build_server
    from .._runtime import get_repository

    workspace_id = await _resolve_workspace_id(api_key)
    mcp = build_server(get_repository(), workspace_id)
    # FastMCP.run(transport="stdio") is sync and calls anyio.run(...)
    # itself — calling it here would mean two separate asyncio.run()-
    # style entries in one process. Its own async equivalent,
    # run_stdio_async(), lets the auth step and the server share the
    # ONE loop this whole command runs under (asyncio.run(_mcp_serve(...))
    # below), which is unambiguously correct rather than relying on two
    # separate loop lifetimes never interfering with each other.
    # show_banner=False: an MCP host drives this process, not a human
    # watching a terminal — nothing should print before the first real
    # JSON-RPC message goes over stdout.
    await mcp.run_stdio_async(show_banner=False)


@app.command(
    name="mcp-serve",
    help="Run a read-only MCP server exposing this workspace's governance data over stdio. Blocks until terminated. "
    "Requires the `mcp` extra (pip install agentguard[mcp], which installs fastmcp).",
)
def mcp_serve(
    api_key: str = typer.Option(
        ..., "--api-key", envvar="AGENTGUARD_API_KEY",
        help="Workspace-scoped API key (prefer the AGENTGUARD_API_KEY env var so the raw key never appears in `ps`).",
    ),
) -> None:
    # Unlike every other command here, this one never returns during
    # normal operation.
    asyncio.run(_mcp_serve(api_key))


# -- worker --------------------------------------------------------------------


async def _worker(poll_interval_s: float) -> None:
    from .._runtime import get_repository
    from ..evaluation.dataset.llm_judge import make_llm_judge
    from ..evaluation.dataset.sources.postgres_source import PostgresEvidenceSource
    from ..jobs import (
        DATASET_VALIDATION_JOB_KIND,
        EVALUATION_SUITE_RUN_JOB_KIND,
        MODEL_BENCHMARK_RUN_JOB_KIND,
        Worker,
        make_dataset_validation_handler,
        make_evaluation_run_handler,
        make_model_benchmark_run_handler,
    )
    from ..llm.provider import get_default_provider
    from ..tracing.litellm_wrap import make_benchmark_model_call_fn
    import os

    handlers: dict[str, Any] = {}

    # Needs only the repository (unlike dataset_validation's external
    # evidence-source requirement below), so it's always registered. A
    # suite referencing a deepeval.* key without `deepeval` installed
    # surfaces a loud, per-item job failure -- never a silent skip --
    # through the same optional-dependency contract every other
    # deepeval.* touchpoint in this codebase already uses.
    handlers[EVALUATION_SUITE_RUN_JOB_KIND] = make_evaluation_run_handler(get_repository())
    typer.echo("agentguard worker: evaluation_suite_run handler registered")

    # Also needs only the repository — the real model_call_fn calls
    # litellm.acompletion directly (no gateway/RunContext requirement),
    # so this is always registered too, same rationale as above.
    handlers[MODEL_BENCHMARK_RUN_JOB_KIND] = make_model_benchmark_run_handler(
        get_repository(), model_call_fn=make_benchmark_model_call_fn()
    )
    typer.echo("agentguard worker: model_benchmark_run handler registered")

    validation_db_url = os.environ.get("AGENTGUARD_VALIDATION_DB_URL")
    validation_query = os.environ.get("AGENTGUARD_VALIDATION_QUERY")
    if validation_db_url and validation_query:
        source = PostgresEvidenceSource(validation_db_url, validation_query)
        judge = make_llm_judge(get_default_provider())
        handlers[DATASET_VALIDATION_JOB_KIND] = make_dataset_validation_handler(get_repository(), source, judge)
        typer.echo(f"agentguard worker: dataset_validation handler registered (source={validation_db_url.rsplit('@', 1)[-1]!r})")
    else:
        typer.echo(
            "agentguard worker: AGENTGUARD_VALIDATION_DB_URL/AGENTGUARD_VALIDATION_QUERY not set — "
            "dataset_validation jobs will fail explicitly (no handler registered) rather than being silently skipped."
        )

    if not handlers:
        typer.echo("agentguard worker: no job kinds configured — nothing to claim. Exiting.", err=True)
        raise typer.Exit(code=1)

    worker = Worker(get_repository(), handlers)
    typer.echo(f"agentguard worker: polling every {poll_interval_s}s for kinds={list(handlers)}. Blocks until terminated.")
    await worker.run_forever(poll_interval_s=poll_interval_s)


@app.command(
    name="worker",
    help="Run a durable job-queue worker (agentguard/jobs/). Blocks until terminated. Always wires the "
    "evaluation_suite_run and model_benchmark_run job kinds. Also wires dataset_validation when "
    "AGENTGUARD_VALIDATION_DB_URL + AGENTGUARD_VALIDATION_QUERY are set (the judge model is "
    "agentguard.llm.provider.get_default_provider() — a real Anthropic call if ANTHROPIC_API_KEY is "
    "configured, otherwise the clearly-labeled deterministic stand-in).",
)
def worker(
    poll_interval_s: float = typer.Option(2.0, "--poll-interval", help="Seconds to sleep between empty-queue polls."),
) -> None:
    asyncio.run(_worker(poll_interval_s))


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
