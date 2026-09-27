"""Pure flight-selection logic for flight_booking_agent.py, kept in its
own zero-dependency module so it's unit-testable without importing
agentguard -- importing flight_booking_agent.py itself has side effects
(it configures the SDK's active repository, and constructs a real
AgentGuard client, from whatever is in the environment)."""
from __future__ import annotations


def select_flight(options: list[dict], budget: float) -> dict:
    """Picks the cheapest option that fits `budget`. If none do, picks
    the cheapest option overall -- the best an honest agent can offer --
    so a human approval request proposes the BEST available option
    instead of an arbitrary (or worst) one."""
    in_budget = [o for o in options if o["price"] <= budget]
    return min(in_budget or options, key=lambda o: o["price"])
