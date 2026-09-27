"""Regression test for the flight_booking_agent.py demo's actual bug:
book_flight used to pick `max(options, key=price)` -- the MOST expensive
option -- regardless of the stated budget. select_flight() replaces that
with honest behavior: prefer the cheapest option that fits the budget,
and only fall back to a genuinely-over-budget pick (still the cheapest
available, not an arbitrary one) when nothing fits."""
from __future__ import annotations

from examples._flight_selection import select_flight

_INDIGO = {"flight": "6E-204 IndiGo", "price": 8_500}
_SPICEJET = {"flight": "SG-118 SpiceJet", "price": 9_200}
_AIR_INDIA = {"flight": "AI-202 Air India", "price": 12_000}


def test_picks_the_cheapest_option_that_fits_the_budget():
    options = [_INDIGO, _SPICEJET, _AIR_INDIA]
    assert select_flight(options, budget=10_000) == _INDIGO


def test_never_picks_the_most_expensive_option_just_because_it_is_last_or_first():
    # Regression: max(options, key=price) doesn't care about order, but
    # neither should the fix -- assert on VALUE, not position.
    reordered = [_AIR_INDIA, _INDIGO, _SPICEJET]
    assert select_flight(reordered, budget=10_000) == _INDIGO


def test_falls_back_to_the_cheapest_overall_when_nothing_fits_the_budget():
    options = [_INDIGO, _SPICEJET, _AIR_INDIA]
    assert select_flight(options, budget=5_000) == _INDIGO
