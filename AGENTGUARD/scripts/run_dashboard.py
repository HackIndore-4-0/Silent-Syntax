"""Runs the AgentGuard dashboard/API for manual testing.

No PostgreSQL is available in this development environment (no
`docker`, no `AGENTGUARD_DATABASE_URL` — see
docs/EXECUTION_REPORT_PHASE_3.md §1 / EXECUTION_REPORT_PHASE_4.md §20),
so this script configures `agentguard.storage.memory.InMemoryRunRepository`
(the same repository the test suite runs against) and seeds it with a
handful of real, executed runs covering every phase's features, so the
dashboard has real data to browse the moment it starts:

  - Phase 1/2: a plain CONTINUE run, a RETRY-then-CONTINUE run
  - Phase 3:   the canonical budget-failure run (STOP), its rollback,
               and its recovery run (CONTINUE) + counterfactual
  - Phase 4:   a tool-fallback recovery run, a registered policy
               ("checkout_policy", two versions), and a proposed
               (never auto-deployed) improvement candidate

Run with:
    .venv/Scripts/python.exe scripts/run_dashboard.py
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agentguard
from agentguard import AgentGuard, Policy, monitor
from agentguard._runtime import configure
from agentguard.auth import create_api_key, signup
from agentguard.errors import TransientError
from agentguard.improve.optimizer import DeterministicTestOptimizer
from agentguard.improve.workflow import propose_improvement
from agentguard.policy.registry import PolicyRegistry
from agentguard.recovery import generate_counterfactual, rollback, seed_recovery_state
from agentguard.storage.memory import InMemoryRunRepository
from agentguard.tools.registry import get_registry

repo = InMemoryRunRepository()
configure(repo)

DEMO_EMAIL = "demo@agentguard.dev"
DEMO_PASSWORD = "agentguard-demo-2026"


async def _signup_demo_account() -> tuple:
    """A separate, plain `async def` run via its own `asyncio.run(...)`
    (see `if __name__ == "__main__"` below) — kept apart from `seed()`
    so `AgentGuard(...)` (synchronous by design; see agentguard/client.py)
    can be constructed in between, with no event loop already running."""
    signup_result = await signup(repo, email=DEMO_EMAIL, password=DEMO_PASSWORD, name="Demo User")
    key_result = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
        project_id=signup_result.project.id, name="seed-script",
    )
    return signup_result, key_result


async def seed(guard: AgentGuard, signup_result) -> None:
    # -- Phase 1/2: a simple healthy run -------------------------------------
    @guard.monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False, agent_version="v1")
    async def simple_agent(task: str) -> str:
        agentguard.update_state(max_budget=45000)
        agentguard.record_tokens(model_name="claude-haiku-4-5-20251001", input_tokens=420, output_tokens=180, cost_usd=0.0009)
        return "selected a 45000 laptop"

    await simple_agent("find a laptop under budget")

    # -- Phase 2: a RETRY that then succeeds ---------------------------------
    attempts = {"n": 0}

    @guard.monitor(policy=Policy(max_cost=60000, retry_limit=2), llm_judge=False)
    async def flaky_agent(task: str) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TransientError("price lookup service timed out", tool="price_api")
        agentguard.update_state(max_budget=52000)
        return "selected a 52000 laptop"

    await flaky_agent("find a laptop under budget (retry demo)")

    # -- Phase 3: the canonical budget-failure -> rollback -> recovery -------
    @guard.monitor(policy=Policy(max_cost=60000, version=3), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "selected a 67000 laptop"

    @guard.monitor(policy=Policy(max_cost=60000, version=3), llm_judge=False)
    async def safe_recovery_agent(task: str) -> str:
        agentguard.update_state(max_budget=52000)
        return "selected a 52000 laptop"

    await drifting_agent("find a laptop under budget")
    failed_run_id = [rid for rid in repo.runs][-1]
    root_cause = await repo.get_root_cause(failed_run_id)
    checkpoints = await repo.list_checkpoints(failed_run_id)

    rollback_result = await rollback(repo, failed_run_id, "S2")
    seed_recovery_state(
        rollback_result.restored_state, parent_run_id=failed_run_id, checkpoint_id=rollback_result.checkpoint.id
    )
    await safe_recovery_agent("retry the task with the budget constraint intact")
    recovery_run_id = [rid for rid in repo.runs if rid != failed_run_id][-1]

    updated_run = await repo.get_run(failed_run_id)
    recovery_run = await repo.get_run(recovery_run_id)
    recovery_checkpoints = await repo.list_checkpoints(recovery_run_id)
    cf = generate_counterfactual(
        updated_run, checkpoints, recovery_run, recovery_checkpoints, rollback_result.checkpoint.id, root_cause
    )
    await repo.save_counterfactual(cf)

    # -- Phase 4: tool fallback recovery --------------------------------------
    registry = get_registry()

    async def flaky_search(q: str) -> str:
        raise RuntimeError("search_api is down")

    async def backup_search(q: str) -> str:
        return f"results for {q!r} from the backup index"

    registry.register_tool("search_api", flaky_search)
    registry.register_tool("search_api_backup", backup_search)
    registry.register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)
    from agentguard.models import ToolAlternative

    await repo.save_tool_alternative(
        ToolAlternative(
            primary="search_api", fallback="search_api_backup", reliability_threshold=0.8,
            workspace_id=signup_result.workspace.id,
        )
    )

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def shopping_agent(task: str) -> str:
        result = await agentguard.context.call_tool("search_api", "laptop under budget", primary_attempts=2)
        agentguard.update_state(max_budget=50000)
        return result

    await shopping_agent("find a laptop (tool fallback demo)")

    # -- Phase 4/Dashboard V2: policy control portal seed data, scoped to
    # the demo workspace so it shows up in the authenticated Policy
    # Management page. ---------------------------------------------------
    from server.dashboard_v2 import _create_scoped, _save_version_scoped

    policy_registry = PolicyRegistry(repo)
    await _create_scoped(
        policy_registry, "checkout_policy", Policy(max_cost=60000, retry_limit=2, require_approval=["payment"]),
        signup_result.workspace.id,
    )
    await _save_version_scoped(
        policy_registry, "checkout_policy", Policy(max_cost=60000, retry_limit=5, require_approval=["payment"]),
        signup_result.workspace.id,
    )

    # -- Phase 4: a proposed (never auto-deployed) improvement candidate ------
    async def improved_agent(task: str) -> str:
        @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def _agent(t: str) -> str:
            agentguard.update_state(max_budget=52000)
            return "within budget"

        await _agent(task)
        return [rid for rid in repo.runs][-1]

    candidate, _evaluation = await propose_improvement(
        repo, failed_run_id, corpus_run_ids=[failed_run_id], candidate_agent_fn=improved_agent,
        optimizer=DeterministicTestOptimizer(),
    )
    # propose_improvement() (Phase 4) predates Dashboard V2's workspace
    # scoping; tag the candidate it produced with this demo's workspace
    # so it appears in the authenticated Recommendations page.
    candidate.workspace_id = signup_result.workspace.id
    await repo.save_improvement_candidate(candidate)

    print(f"Seeded {len(repo.runs)} runs into the in-memory repository:")
    for run_id, run in repo.runs.items():
        print(f"  {run_id}  {run.agent_name:<20} {run.status.value}")


if __name__ == "__main__":
    signup_result, key_result = asyncio.run(_signup_demo_account())
    print(f"Demo account: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"Workspace: {signup_result.workspace.id}   API key: {key_result.raw_key}")

    guard = AgentGuard(api_key=key_result.raw_key, project="production")  # sync — no loop running here

    asyncio.run(seed(guard, signup_result))

    import uvicorn

    from server.api import app

    print("\nStarting AgentGuard dashboard at http://127.0.0.1:8000  (Ctrl+C to stop)\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
