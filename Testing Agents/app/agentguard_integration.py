
import logging
from typing import Any, Optional

import agentguard
from agentguard import Policy
from agentguard.auth.security import hash_token
from agentguard.auth.service import resolve_project
from agentguard.storage.memory import InMemoryRunRepository
from agentguard.storage.postgres import PostgresRunRepository

from app.config import (
    AGENTGUARD_API_KEY,
    AGENTGUARD_DATABASE_URL,
    AGENTGUARD_HUMAN_TIMEOUT_S,
    AGENTGUARD_PROJECT,
)

logger = logging.getLogger("agentguard_integration")

if AGENTGUARD_DATABASE_URL:
    agentguard.configure(PostgresRunRepository(AGENTGUARD_DATABASE_URL))
else:
    # No real Postgres configured for the AgentGuard platform -> keep this
    # demo fully self-contained (no external services required to run it).
    agentguard.configure(InMemoryRunRepository())

_tenancy: dict[str, Optional[str]] = {"workspace_id": None, "project_id": None}
_tenancy_resolved = False


async def ensure_ready() -> None:
    
    global _tenancy_resolved
    if _tenancy_resolved or not AGENTGUARD_API_KEY:
        _tenancy_resolved = True
        return

    repository = agentguard._runtime.get_repository()
    try:
        key_data = await repository.get_api_key_by_hash(hash_token(AGENTGUARD_API_KEY))
        if key_data is None or key_data.get("status") != "active" or key_data.get("revoked_at") is not None:
            logger.warning(
                "AgentGuard: AGENTGUARD_API_KEY was rejected (unknown/inactive/revoked); "
                "runs will be recorded unauthenticated (no workspace/project tag)."
            )
        else:
            await repository.update_api_key_last_used(key_data["id"])
            workspace_id = key_data["workspace_id"]
            project = await resolve_project(repository, workspace_id=workspace_id, project_name=AGENTGUARD_PROJECT)
            _tenancy["workspace_id"] = workspace_id
            _tenancy["project_id"] = project.id
            logger.info("AgentGuard: resolved workspace/project '%s' for future runs.", AGENTGUARD_PROJECT)
    except Exception:  # noqa: BLE001 - never let a bad key/unreachable DB break the chat API
        logger.exception(
            "AgentGuard: failed to resolve API key/tenancy; runs will be recorded unauthenticated."
        )
    _tenancy_resolved = True


#: Tools that must not actually take effect (decrement a seat, mark a
#: booking cancelled) until a human approves — see app/tools.py's
#: `agentguard.perform_action(...)` calls and app/main.py's
#: POST /api/runs/{run_id}/approve|reject.
HUMAN_APPROVAL_ACTIONS = ["book_flight", "cancel_booking"]


def monitor_turn(*, max_cost: Optional[float] = None, agent_version: str = "flight-booking-agent"):
    
    return agentguard.trace(
        policy=Policy(
            max_cost=max_cost,
            require_approval=HUMAN_APPROVAL_ACTIONS,
            human_timeout_s=AGENTGUARD_HUMAN_TIMEOUT_S,
        ),
        llm_judge=True,
        agent_version=agent_version,
        workspace_id=_tenancy["workspace_id"],
        project_id=_tenancy["project_id"],
    )


def current_run_id() -> Optional[str]:
   
    ctx = agentguard.context.current_run()
    return ctx.run.id if ctx is not None else None


async def _collect_metric_results(repository: Any, run_id: str) -> list[dict[str, Any]]:
    
    eval_runs = await repository.list_evaluation_runs(workspace_id=_tenancy["workspace_id"])
    results: list[dict[str, Any]] = []
    for eval_run in eval_runs:
        if run_id not in (eval_run.get("source_run_ids") or []):
            continue
        for r in await repository.list_evaluation_results(eval_run["id"]):
            results.append(
                {
                    "metric": r["metric"],
                    "available": r["available"],
                    "score": r["score"],
                    "passed": r["passed"],
                    "reason": r["reason"],
                }
            )
    return results


async def get_run_summary(run_id: Optional[str], *, wait_for_metrics: bool = True) -> Optional[dict[str, Any]]:
    
    if run_id is None:
        return None
    if wait_for_metrics:
        await agentguard.wait_for_background_tasks()

    repository = agentguard._runtime.get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        return None
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "evaluations": [
            {"evaluator": e["evaluator"], "passed": e["passed"], "label": e["label"], "evidence": e["evidence"]}
            for e in run.get("evaluations", [])
        ],
        "decisions": [
            {"outcome": d["outcome"], "reason": d["reason"]} for d in run.get("decisions", [])
        ],
        "metrics": await _collect_metric_results(repository, run_id),
    }
