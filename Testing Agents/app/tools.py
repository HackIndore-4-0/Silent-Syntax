"""
LangChain tool definitions for the Flight Booking AI Agent.

Every tool returns a JSON-serializable dict with a `success` flag so that:
 - the LLM always receives a structured, predictable result (never a raw traceback)
 - an external Agent Evaluator can inspect exact tool arguments and outputs
Nothing here calls a real flight API or moves real money — this is demo-only data.

Every tool is wrapped with `@traceable` so AgentGuard records one TraceStep
per call (args, return value, latency) — this is what populates
`EvalCase.tools_called` for the `builtin.tool_correctness` metric and makes
every tool call visible in the Runs / Traces view, not just the final reply.
`@traceable` requires an active `@monitor`/`@trace`-wrapped run (see
app/agentguard_integration.py's `monitor_turn()`), which is always the case
here since these tools only ever run from inside `_run_agent_turn` in
app/main.py.

`book_flight` and `cancel_booking` additionally gate their actual mutating
effect behind `await agentguard.perform_action(...)` — these two are listed
in `monitor_turn()`'s `Policy(require_approval=[...])`, so AgentGuard blocks
here (with a deterministic timeout) until a human approves/rejects via
`POST /api/runs/{run_id}/approve` or `/reject` (app/main.py). A rejection or
timeout raises `HumanRejected`, which propagates out of the tool call and
is what makes @monitor record the run as STOP rather than a silent partial
success — the whole point of a human-approval gate. Both tools are async
(LangChain fully supports async tools) so `await` works directly on the
event loop already running the turn, with no thread-offload/contextvar
concerns.
"""
import uuid
from datetime import datetime
from typing import List

from langchain_core.tools import tool

import agentguard
from agentguard import traceable

from app import database as db
from app.config import FAULT_INJECTION_MODE
from app.models import (
    BookFlightInput,
    CancelBookingInput,
    GetBookingInput,
    GetFlightDetailsInput,
    RecommendFlightsInput,
    SearchFlightsInput,
)

DATE_FMT = "%Y-%m-%d"


def _parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, DATE_FMT)


def _budget_ceiling(stated_budget: float) -> float:
    """The effective price ceiling applied for a stated budget/max_price.

    Normally this is just `stated_budget` itself. When FAULT_INJECTION_MODE
    is "budget_violation" it is deliberately widened so flights priced above
    what the user asked for are still accepted as "within budget" — a
    reproducible, on-demand constraint violation for feeding known-bad runs
    to the AgentGuard evaluator. Never enabled outside of evaluator testing.
    """
    if FAULT_INJECTION_MODE == "budget_violation":
        return stated_budget * 1.35
    return stated_budget


def _time_bucket(dt: datetime) -> str:
    hour = dt.hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


@tool("search_flights", args_schema=SearchFlightsInput)
@traceable
def search_flights(
    source: str,
    destination: str,
    date: str,
    passengers: int = 1,
    max_price: float = None,
    airline: str = None,
    departure_after: str = None,
    departure_before: str = None,
) -> dict:
    """Search the mock flight database for flights matching source, destination and date.
    Optionally filter by max_price, airline, and a departure time window. Always use this
    tool instead of guessing flight availability."""
    try:
        _parse_date(date)
    except ValueError:
        return {"success": False, "error": f"Invalid date format '{date}'. Expected YYYY-MM-DD."}

    if passengers < 1:
        return {"success": False, "error": "Passenger count must be at least 1."}

    flights = db.find_flights(source, destination, date)

    if airline:
        flights = [f for f in flights if f["airline"].lower() == airline.lower()]
    if max_price is not None:
        flights = [f for f in flights if f["price"] <= _budget_ceiling(max_price)]
    if departure_after:
        flights = [
            f for f in flights
            if datetime.fromisoformat(f["departure_time"]).strftime("%H:%M") >= departure_after
        ]
    if departure_before:
        flights = [
            f for f in flights
            if datetime.fromisoformat(f["departure_time"]).strftime("%H:%M") <= departure_before
        ]

    flights_with_seats = [f for f in flights if f["seats_available"] >= passengers]
    sold_out = [f for f in flights if f["seats_available"] < passengers]

    return {
        "success": True,
        "is_demo_data": True,
        "query": {
            "source": source, "destination": destination, "date": date, "passengers": passengers,
        },
        "count": len(flights_with_seats),
        "flights": flights_with_seats,
        "insufficient_seats": sold_out,
    }


@tool("get_flight_details", args_schema=GetFlightDetailsInput)
@traceable
def get_flight_details(flight_id: str) -> dict:
    """Retrieve full details for a single flight by its flight_id."""
    flight = db.get_flight(flight_id)
    if not flight:
        return {"success": False, "error": f"No flight found with flight_id '{flight_id}'."}
    return {"success": True, "is_demo_data": True, "flight": flight}


@tool("book_flight", args_schema=BookFlightInput)
@traceable
async def book_flight(flight_id: str, passengers: List[dict], user_confirmation: bool) -> dict:
    """Book a flight for one or more passengers. Requires user_confirmation=True, which
    must only be set after the user has explicitly confirmed the booking in the
    conversation. Generates a unique demo booking_id and decrements seat availability."""
    if not user_confirmation:
        return {
            "success": False,
            "error": "Booking requires explicit user confirmation. Ask the user to confirm "
            "before calling this tool with user_confirmation=True.",
        }

    flight = db.get_flight(flight_id)
    if not flight:
        return {"success": False, "error": f"No flight found with flight_id '{flight_id}'."}

    if not passengers:
        return {"success": False, "error": "At least one passenger's details are required."}

    if flight["seats_available"] < len(passengers):
        return {
            "success": False,
            "error": (
                f"Only {flight['seats_available']} seat(s) available on flight {flight_id}, "
                f"but {len(passengers)} passenger(s) were provided."
            ),
        }

    booking_id = f"BK-{uuid.uuid4().hex[:8].upper()}"
    total_price = flight["price"] * len(passengers)

    # Everything above is validation only — nothing has mutated yet, so a
    # human reviewer sees the FULL picture (flight, price, passenger count)
    # before deciding. `book_flight` is listed in monitor_turn()'s
    # Policy(require_approval=[...]) (app/agentguard_integration.py), so
    # this blocks until a human approves/rejects via
    # POST /api/runs/{run_id}/approve|reject, or denies automatically on
    # policy.human_timeout_s. A rejection/timeout raises HumanRejected here,
    # which propagates out of this tool call uncaught — AgentGuard records
    # the run as STOP, and no seat is ever decremented.
    await agentguard.perform_action(
        "book_flight",
        flight_id=flight_id,
        booking_id=booking_id,
        total_price=total_price,
        passenger_count=len(passengers),
    )

    booking = {
        "booking_id": booking_id,
        "flight_id": flight_id,
        "flight": flight,
        "passengers": passengers,
        "passenger_count": len(passengers),
        "total_price": total_price,
        "status": "confirmed",
        "is_demo_booking": True,
        "created_at": datetime.utcnow().isoformat(),
    }

    db.decrement_seats(flight_id, len(passengers))
    db.save_booking(booking)

    return {"success": True, "is_demo_booking": True, "booking": booking}


@tool("get_booking", args_schema=GetBookingInput)
@traceable
def get_booking(booking_id: str) -> dict:
    """Retrieve an existing booking's details by its booking_id."""
    booking = db.get_booking(booking_id)
    if not booking:
        return {"success": False, "error": f"No booking found with booking_id '{booking_id}'."}
    return {"success": True, "booking": booking}


@tool("cancel_booking", args_schema=CancelBookingInput)
@traceable
async def cancel_booking(booking_id: str, user_confirmation: bool) -> dict:
    """Cancel an existing booking. Requires user_confirmation=True, which must only be
    set after the user has explicitly confirmed the cancellation in the conversation."""
    if not user_confirmation:
        return {
            "success": False,
            "error": "Cancellation requires explicit user confirmation. Ask the user to "
            "confirm before calling this tool with user_confirmation=True.",
        }

    booking = db.get_booking(booking_id)
    if not booking:
        return {"success": False, "error": f"No booking found with booking_id '{booking_id}'."}

    if booking["status"] == "cancelled":
        return {"success": False, "error": f"Booking '{booking_id}' is already cancelled."}

    # Same rule as book_flight: validated, nothing mutated yet, then gated
    # on human approval before the cancellation actually takes effect.
    await agentguard.perform_action(
        "cancel_booking",
        booking_id=booking_id,
        flight_id=booking["flight_id"],
        refund_amount=booking["total_price"],
    )

    booking["status"] = "cancelled"
    booking["cancelled_at"] = datetime.utcnow().isoformat()
    db.increment_seats(booking["flight_id"], booking["passenger_count"])
    db.save_booking(booking)

    return {"success": True, "booking": booking}


@tool("recommend_flights", args_schema=RecommendFlightsInput)
@traceable
def recommend_flights(
    source: str,
    destination: str,
    date: str,
    budget: float = None,
    preferred_airline: str = None,
    preferred_time: str = None,
) -> dict:
    """Recommend flights for a route/date based on the user's budget, preferred airline,
    and preferred time of day. Scores and ranks matching mock flights and explains why
    each recommendation fits. If nothing matches, relaxes constraints and returns
    alternatives with an explanation."""
    try:
        _parse_date(date)
    except ValueError:
        return {"success": False, "error": f"Invalid date format '{date}'. Expected YYYY-MM-DD."}

    candidates = db.find_flights(source, destination, date)
    if not candidates:
        return {
            "success": True,
            "matched": False,
            "reason": f"No flights found from {source} to {destination} on {date}.",
            "alternatives": [],
        }

    def score(flight: dict) -> int:
        s = 0
        if budget is not None and flight["price"] <= _budget_ceiling(budget):
            s += 2
        if preferred_airline and flight["airline"].lower() == preferred_airline.lower():
            s += 2
        if preferred_time and _time_bucket(datetime.fromisoformat(flight["departure_time"])) == preferred_time.lower():
            s += 1
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    best_score = score(ranked[0])

    if best_score == 0:
        return {
            "success": True,
            "matched": False,
            "reason": "No flights matched the given budget/airline/time preferences.",
            "alternatives": ranked[:3],
        }

    recommendations = []
    for flight in ranked:
        if score(flight) == 0:
            continue
        reasons = []
        if budget is not None and flight["price"] <= _budget_ceiling(budget):
            reasons.append(f"fits within your budget of {budget}")
        if preferred_airline and flight["airline"].lower() == preferred_airline.lower():
            reasons.append(f"is operated by your preferred airline {preferred_airline}")
        if preferred_time and _time_bucket(datetime.fromisoformat(flight["departure_time"])) == preferred_time.lower():
            reasons.append(f"departs in the {preferred_time} as you preferred")
        recommendations.append({"flight": flight, "why": "; ".join(reasons) or "closest overall match"})

    return {"success": True, "matched": True, "recommendations": recommendations}


ALL_TOOLS = [
    search_flights,
    get_flight_details,
    book_flight,
    get_booking,
    cancel_booking,
    recommend_flights,
]
