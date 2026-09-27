"""FastAPI backend for the Flight Booking AI Agent (demo project).

Serves the chat UI and a small JSON API that runs the LangChain tool-calling
agent and returns both its natural-language reply and a structured trace of
every tool call it made, so an external Agent Evaluator can inspect exactly
which tools were selected, with which arguments, and what they returned.
"""
import uuid
from pathlib import Path
from typing import Any, List, Optional

import agentguard
from agentguard.errors import HumanRejected, HumanReplanRequested
from agentguard.human.resolution import resolve_human_review
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent import clear_session, get_agent_executor
from app.agentguard_integration import current_run_id, ensure_ready, get_run_summary, monitor_turn

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Flight Booking AI Agent (Demo)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    budget: Optional[float] = None
    """Optional budget ceiling to declare for this turn (e.g. an evaluator
    sending "book something under 6000" also sends budget=6000). Enforced
    as an AgentGuard Policy(max_cost=...) constraint — see
    app/agentguard_integration.py — independently of anything the LLM
    itself infers from the message text."""


class ToolTrace(BaseModel):
    tool: str
    tool_input: dict
    output: Any


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    trace: List[ToolTrace]
    agentguard: Optional[dict] = None
    """The AgentGuard run's verdict for this turn (status/evaluations/
    decisions) — e.g. a constraint_adherence "constraint_violated" finding
    when a booking exceeded the declared `budget`. None if AgentGuard
    couldn't be reached."""
    human_intervention: Optional[dict] = None
    """Set only when this turn's booking/cancellation was blocked on a
    human decision that resolved to a rejection or timeout (see
    app/tools.py's agentguard.perform_action calls) — `reply` is still a
    real LLM-generated message explaining that to the user, this field
    just gives the caller/evaluator a structured status + run_id too."""


#: session_id -> run_id for whichever turn is CURRENTLY executing (set the
#: moment a turn starts, cleared when it ends) — lets a second, concurrent
#: request discover the run_id to approve/reject before the first request
#: (which is blocked awaiting that very decision) has returned.
_in_flight_runs: dict[str, str] = {}


def _serialize_intermediate_steps(steps) -> List[ToolTrace]:
    trace = []
    for action, observation in steps:
        trace.append(
            ToolTrace(
                tool=action.tool,
                tool_input=action.tool_input if isinstance(action.tool_input, dict) else {"input": action.tool_input},
                output=observation,
            )
        )
    return trace


async def _run_agent_turn(message: str, session_id: str, out: dict) -> str:
    """One agent turn, run inside an AgentGuard-monitored context (see the
    @monitor_turn(...) decoration applied per-request in `chat()` below).
    Reports the real price of any booking made this turn via
    agentguard.update_state(max_budget=...) so the built-in
    ConstraintAdherenceEvaluator can compare it against the turn's declared
    budget policy.

    Returns just the reply text: monitor_turn's auto_evaluate metric suite
    scores this function's own return value as `actual_output` (see
    app/agentguard_integration.py), and text metrics (faithfulness, answer
    relevancy, ...) need the plain answer, not a structured dict. The
    trace/run_id the API response also needs are written into `out`
    instead — the ambient AgentGuard run context is gone once this
    function returns, so they can't be read back afterward otherwise.

    `out["run_id"]` is captured FIRST, before the executor even starts —
    not after `ainvoke()` returns — because a book_flight/cancel_booking
    call that blocks on human approval (app/tools.py) may raise
    HumanRejected from *inside* that call, aborting this function before
    it would otherwise reach the end. The caller (`chat()`) still needs
    the run_id in that case to report which run is now STOPped, and
    `_in_flight_runs` (main.py module state) needs it immediately so a
    concurrent approve/reject request can find it while this one is
    still blocked."""
    out["run_id"] = current_run_id()
    if out["run_id"] is not None:
        _in_flight_runs[session_id] = out["run_id"]
    try:
        executor = get_agent_executor()
        result = await executor.ainvoke(
            {"input": message},
            config={"configurable": {"session_id": session_id}},
        )
    finally:
        _in_flight_runs.pop(session_id, None)

    reply = result.get("output", "")
    trace = _serialize_intermediate_steps(result.get("intermediate_steps", []))
    out["trace"] = trace

    for step in trace:
        if step.tool == "book_flight" and isinstance(step.output, dict) and step.output.get("success"):
            agentguard.update_state(max_budget=step.output["booking"]["total_price"])

    return reply


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty.")

    try:
        get_agent_executor()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    await ensure_ready()
    turn_out: dict = {}
    monitored_turn = monitor_turn(max_cost=req.budget)(_run_agent_turn)
    human_intervention: Optional[dict] = None
    try:
        reply = await monitored_turn(req.message, session_id, turn_out)
    except (HumanRejected, HumanReplanRequested) as exc:
        # A human declined (or a request_approval timeout denied) the
        # pending book_flight/cancel_booking action — @monitor already
        # recorded this as a real STOP decision (decorator.py), so this
        # is an expected, meaningful outcome for the caller/evaluator to
        # see, not a server error. `reply` becomes an honest explanation
        # rather than the LLM's own (now never-produced) text.
        human_intervention = {"outcome": "rejected", "action": exc.action, "reason": str(exc)}
        reply = (
            f"This request was not completed: a human reviewer declined (or did not respond "
            f"in time to) the pending '{exc.action}' action. {exc}"
        )
    except Exception as exc:  # LLM API errors, tool failures, etc.
        raise HTTPException(status_code=502, detail=f"Agent execution failed: {exc}")

    agentguard_summary = await get_run_summary(turn_out.get("run_id"))

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        trace=turn_out.get("trace", []),
        agentguard=agentguard_summary,
        human_intervention=human_intervention,
    )


@app.get("/api/sessions/{session_id}/current-run")
def get_current_run(session_id: str) -> dict:
    """The run_id of whichever turn is CURRENTLY executing for this
    session, if any — lets a human/tester discover the run to inspect or
    approve/reject (see the endpoints below) from a second request while
    the original /api/chat call is still blocked awaiting that decision."""
    run_id = _in_flight_runs.get(session_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail="no turn is currently in flight for this session.")
    return {"session_id": session_id, "run_id": run_id}


@app.get("/api/runs/{run_id}/pending-approval")
async def get_pending_approval(run_id: str) -> dict:
    """The open (status="pending") human-approval request for this run,
    if any — the action name and the evidence book_flight/cancel_booking
    passed to agentguard.perform_action(...) (flight_id, total_price,
    passenger_count, ...), so a reviewer can decide without needing the
    full AgentGuard dashboard."""
    repository = agentguard._runtime.get_repository()
    decisions = await repository.list_human_decisions(run_id)
    pending = [d for d in decisions if d.get("status") == "pending"]
    if not pending:
        raise HTTPException(status_code=404, detail="no pending human-approval request for this run.")
    return pending[-1]


class HumanDecisionRequest(BaseModel):
    resolved_by: Optional[str] = None
    reason: str = ""


async def _resolve(run_id: str, outcome: str, body: HumanDecisionRequest) -> dict:
    repository = agentguard._runtime.get_repository()
    result = await resolve_human_review(
        repository, run_id, outcome, resolved_by=body.resolved_by, reason=body.reason
    )
    if not result.resolved:
        raise HTTPException(status_code=404, detail="no open human-approval request for this run.")
    return {
        "run_id": run_id,
        "outcome": outcome,
        # `live=True`: a coroutine (the blocked perform_action() call) was
        # actually unblocked just now — the run's own Decision is recorded
        # moments later by @monitor once that coroutine resumes/raises,
        # not by this call itself (see agentguard/human/resolution.py).
        "live": result.live,
    }


@app.post("/api/runs/{run_id}/approve")
async def approve_run_action(run_id: str, body: HumanDecisionRequest = HumanDecisionRequest()) -> dict:
    """Approve the pending book_flight/cancel_booking action for `run_id`
    — unblocks the agentguard.perform_action(...) call still awaiting a
    decision inside the (still in-flight) /api/chat request for this
    turn, letting the booking/cancellation actually proceed."""
    return await _resolve(run_id, "approved", body)


@app.post("/api/runs/{run_id}/reject")
async def reject_run_action(run_id: str, body: HumanDecisionRequest = HumanDecisionRequest()) -> dict:
    """Reject the pending action — raises HumanRejected inside the
    blocked /api/chat request, which @monitor turns into a STOP decision
    and no booking/cancellation ever takes effect."""
    return await _resolve(run_id, "rejected", body)


@app.post("/api/sessions/{session_id}/reset")
def reset_session(session_id: str) -> dict:
    clear_session(session_id)
    return {"session_id": session_id, "status": "reset"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
