"""A LangGraph flight-booking agent that misbehaves on purpose — it tells
the user it will book a flight under a stated budget, then tries to book
one over it. Rather than silently letting that through (or silently
auto-STOPping it), this version routes the over-budget booking through
AgentGuard's REAL human-in-the-loop primitive (`perform_action`, the same
one the dashboard's WebSocket approval UI uses) — but resolved from a
plain terminal prompt, no UI at all: you are asked, right there in the
terminal, "approve or reject?", and your answer decides whether the
booking proceeds or the run stops.

Note even an APPROVED booking still gets flagged afterward: AgentGuard's
deterministic ConstraintAdherenceEvaluator independently checks the
actual final spend against the policy's budget regardless of what was
approved at the action level — approval and post-hoc compliance are two
separate, both-real checks, not one gate that silences the other.

Runs standalone (in-memory, nothing persists, no dashboard) by default —
everything prints to the terminal either way, including the AgentGuard
intervention itself. Set AGENTGUARD_DATABASE_URL + AGENTGUARD_API_KEY
(same pair used by rag-eval's src/agentguard_pipeline.py) to also persist
this run to Postgres, tagged to a real workspace, so it shows up live in
the dashboard (server/api.py) alongside everything else:

    export AGENTGUARD_DATABASE_URL=postgresql://agentguard:agentguard@localhost:5434/agentguard
    export AGENTGUARD_API_KEY=agp_live_...
    python examples/flight_booking_agent.py
"""
from __future__ import annotations

import asyncio
import os
from typing import TypedDict

import agentguard
from agentguard import AgentGuard, Policy, monitor, update_state
from agentguard.context import current_run
from agentguard.errors import HumanRejected
from agentguard.human.broker import get_broker
from agentguard.storage.memory import InMemoryRunRepository
from agentguard.tracing import traceable
from langgraph.graph import END, START, StateGraph

_database_url = os.environ.get("AGENTGUARD_DATABASE_URL")
if _database_url:
    from agentguard.storage.postgres import PostgresRunRepository

    _repository = PostgresRunRepository(_database_url)
else:
    _repository = InMemoryRunRepository()
agentguard.configure(_repository)

_api_key = os.environ.get("AGENTGUARD_API_KEY")
_guard = AgentGuard(api_key=_api_key, project="Production") if _api_key else None
"""Resolved once at import time (AgentGuard(...) must be constructed
outside any running event loop — see agentguard/client.py)."""

BUDGET_LIMIT = 10_000
"""What the agent tells the user it will respect — also the Policy's
max_cost, so a booking above this is a real, checked constraint
violation, not just a narrative one."""


class FlightState(TypedDict):
    query: str
    budget: float
    options: list[dict]
    booked: dict


@traceable
async def search_flights(state: FlightState) -> FlightState:
    print(f"[agent] Searching flights under Rs.{state['budget']:.0f}...")
    options = [
        {"flight": "6E-204 IndiGo", "price": 8_500},
        {"flight": "SG-118 SpiceJet", "price": 9_200},
        {"flight": "AI-202 Air India", "price": 12_000},
    ]
    print("[agent] Found: " + ", ".join(f"{o['flight']} (Rs.{o['price']})" for o in options))
    return {**state, "options": options}


HUMAN_APPROVAL_ACTION = "book_over_budget_flight"


async def _await_terminal_approval(queue: "asyncio.Queue") -> None:
    """Waits for the pending-approval message the broker publishes when
    `perform_action` blocks (the exact fan-out server/api.py's WebSocket
    endpoint also reads from — see agentguard/human/broker.py), then
    resolves it from a plain terminal prompt instead of a dashboard
    click. `queue` must already be subscribed BEFORE `perform_action` is
    awaited, or the publish can race ahead of the subscription."""
    message = await queue.get()
    request = message["request"]

    width = 64
    print()
    print("!" * width)
    print(" AGENTGUARD: HUMAN APPROVAL REQUIRED ".center(width, "!"))
    print("!" * width)
    print(f"  Action:   {request['action']}")
    print(f"  Reason:   {request['reason']}")
    for key, value in request.get("evidence", {}).items():
        print(f"  {key}:  {value}")
    print("!" * width)
    answer = await asyncio.to_thread(input, "  Run it, or stop it? [run/stop]: ")
    approved = answer.strip().lower() in ("run", "y", "yes", "approve", "approved")
    get_broker().resolve(request["id"], "approved" if approved else "rejected", resolved_by="terminal-operator")
    print(f"  -> you said: {'RUN IT (approved)' if approved else 'STOP IT (rejected)'}\n")


@traceable
async def book_flight(state: FlightState) -> FlightState:
    """The misbehaving step: picks the MOST EXPENSIVE option regardless
    of the stated budget — the reverse of what the agent just promised.
    A real agent bug looks exactly like this: the plan/response text
    says one thing, the actual side-effecting action does another.

    Unlike a silent violation, this one is gated on a live human
    decision via AgentGuard's `perform_action` before it's allowed to
    "complete" — see HUMAN_APPROVAL_ACTION in `Policy.require_approval`.
    """
    chosen = max(state["options"], key=lambda o: o["price"])
    over_budget = chosen["price"] > state["budget"]
    print(f"[agent] Wants to book {chosen['flight']} for Rs.{chosen['price']}"
          + (" -- this is OVER the stated budget!" if over_budget else "..."))

    if over_budget:
        run_id = current_run().run.id  # type: ignore[union-attr]
        broker = get_broker()
        queue = broker.subscribe(run_id)  # subscribe BEFORE perform_action, see docstring above
        watcher = asyncio.create_task(_await_terminal_approval(queue))
        try:
            await agentguard.perform_action(
                HUMAN_APPROVAL_ACTION, flight=chosen["flight"], price=chosen["price"], budget=state["budget"],
            )
        finally:
            watcher.cancel()
            broker.unsubscribe(run_id, queue)
        print(f"[agent] Human approved it -- booking {chosen['flight']} for Rs.{chosen['price']} anyway.")

    return {**state, "booked": chosen}


_graph = StateGraph(FlightState)
_graph.add_node("search_flights", search_flights)
_graph.add_node("book_flight", book_flight)
_graph.add_edge(START, "search_flights")
_graph.add_edge("search_flights", "book_flight")
_graph.add_edge("book_flight", END)
flight_graph = _graph.compile()


_monitor_decorator = _guard.monitor if _guard is not None else monitor


@_monitor_decorator(
    policy=Policy(max_cost=BUDGET_LIMIT, require_approval=[HUMAN_APPROVAL_ACTION], human_timeout_s=300),
    llm_judge=False,
)
async def flight_booking_agent(query: str) -> dict:
    print(f"[agent] task: {query}")
    print(f"[agent] Understood - I will book a flight under Rs.{BUDGET_LIMIT}.")
    result = await flight_graph.ainvoke({"query": query, "budget": BUDGET_LIMIT, "options": [], "booked": {}})
    booked = result["booked"]
    # Record what actually happened against the SAME field the Policy's
    # max_cost is checked against (see ConstraintAdherenceEvaluator) —
    # this is the honest bookkeeping AgentGuard evaluates independently
    # of whatever a human just approved at the action level above.
    update_state(max_budget=booked["price"])
    return booked


def print_intervention(run: dict) -> None:
    decisions = run.get("decisions") or []
    decision = decisions[-1] if decisions else None
    evaluations = run.get("evaluations") or []
    constraint_eval = next((e for e in evaluations if e["evaluator"] == "constraint_adherence"), None)
    human_decisions = run.get("human_decisions") or []
    human_decision = human_decisions[-1] if human_decisions else None

    width = 64
    print()
    print("=" * width)
    print(" AGENTGUARD INTERVENTION ".center(width, "="))
    print("=" * width)
    if human_decision is not None:
        print(f"  Human asked:   {human_decision['action']}")
        print(f"  Human said:    {human_decision['status'].upper()}  (by {human_decision.get('resolved_by')})")
        print("-" * width)
    if decision is None:
        print("  No decision recorded.")
    else:
        print(f"  Final decision: {decision['outcome'].upper()}")
        print(f"  Reason:         {decision['reason']}")
        if decision.get("risk_score") is not None:
            print(f"  Risk score:     {decision['risk_score']:.2f}")
        if decision.get("confidence") is not None:
            print(f"  Confidence:     {decision['confidence']:.2f}")
    if constraint_eval is not None:
        ev = constraint_eval["evidence"]
        print(f"  Policy limit:   Rs.{ev['expected']:.0f}  ({ev['policy_attr']})")
        print(f"  Actual spend:   Rs.{ev['observed']:.0f}  ({ev['field']})")
    print("-" * width)
    if human_decision is not None and human_decision["status"] == "approved" and decision and decision["outcome"] != "continue":
        print("  You approved the ACTION, but AgentGuard's policy evaluator")
        print("  still flags the FINAL spend against your budget -- approval")
        print("  and post-hoc compliance are two independent, both-real checks.")
    elif human_decision is not None and human_decision["status"] == "rejected":
        print("  You rejected the booking -- AgentGuard recorded this as a")
        print("  controlled STOP (a guardrail rejection), not a crash.")
    else:
        print("  The agent SAID it would book under budget, then tried to book")
        print("  over it anyway. AgentGuard caught the gap between what it")
        print("  SAID and what it DID, instead of letting it pass silently.")
    print("=" * width)


async def _fetch_latest_run() -> dict:
    workspace_id = _guard.workspace_id if _guard is not None else None
    [run_summary] = await _repository.list_runs(limit=1, workspace_id=workspace_id)
    return await _repository.get_run(run_summary["id"])


async def main() -> None:
    try:
        booked_flight = await flight_booking_agent("Book me a flight under Rs.10,000")
        print(f"[result] booked: {booked_flight['flight']} for Rs.{booked_flight['price']}")
    except HumanRejected as exc:
        print(f"[result] booking stopped -- a human rejected it: {exc.reason}")

    latest_run = await _fetch_latest_run()
    print_intervention(latest_run)
    if _guard is not None:
        print(f"\nView this run in the dashboard: http://127.0.0.1:8000/#/runs/{latest_run['id']}")


if __name__ == "__main__":
    asyncio.run(main())
