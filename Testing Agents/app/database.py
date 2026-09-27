"""
In-memory mock flight & booking database for the Flight Booking AI Agent demo.

IMPORTANT: All data below is entirely fictional demo data. It does not represent
real flights, airlines schedules, or prices, and must never be presented to a
user as real. There is no connection to any real flight API or payment system.
"""
import random
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Mock flight inventory
# ---------------------------------------------------------------------------
# Each flight dict is DEMO DATA ONLY.
# F001-F014 are the original hand-authored flights (kept stable so IDs referenced
# in README.md / sample_conversations.md keep working). F015+ are generated below
# to give the demo a much larger, bidirectional, multi-date network so realistic
# searches ("Delhi to Indore next Tuesday", "Mumbai to Chennai in November", ...)
# don't come back empty.
_HANDWRITTEN_FLIGHTS: Dict[str, dict] = {
    "F001": {
        "flight_id": "F001", "airline": "IndiGo", "flight_number": "6E-204",
        "source": "Indore", "destination": "Delhi",
        "departure_time": "2026-10-15T08:00:00", "arrival_time": "2026-10-15T09:40:00",
        "duration": "1h 40m", "price": 4500.0, "seats_available": 24, "is_demo_data": True,
    },
    "F002": {
        "flight_id": "F002", "airline": "Air India", "flight_number": "AI-408",
        "source": "Indore", "destination": "Delhi",
        "departure_time": "2026-10-15T14:30:00", "arrival_time": "2026-10-15T16:10:00",
        "duration": "1h 40m", "price": 5200.0, "seats_available": 12, "is_demo_data": True,
    },
    "F003": {
        "flight_id": "F003", "airline": "Vistara", "flight_number": "UK-945",
        "source": "Delhi", "destination": "Mumbai",
        "departure_time": "2026-10-15T10:00:00", "arrival_time": "2026-10-15T12:10:00",
        "duration": "2h 10m", "price": 6100.0, "seats_available": 30, "is_demo_data": True,
    },
    "F004": {
        "flight_id": "F004", "airline": "SpiceJet", "flight_number": "SG-112",
        "source": "Delhi", "destination": "Mumbai",
        "departure_time": "2026-10-15T18:45:00", "arrival_time": "2026-10-15T20:55:00",
        "duration": "2h 10m", "price": 4800.0, "seats_available": 5, "is_demo_data": True,
    },
    "F005": {
        "flight_id": "F005", "airline": "IndiGo", "flight_number": "6E-330",
        "source": "Mumbai", "destination": "Bengaluru",
        "departure_time": "2026-10-15T07:15:00", "arrival_time": "2026-10-15T08:55:00",
        "duration": "1h 40m", "price": 3900.0, "seats_available": 40, "is_demo_data": True,
    },
    "F006": {
        "flight_id": "F006", "airline": "Akasa Air", "flight_number": "QP-1401",
        "source": "Mumbai", "destination": "Bengaluru",
        "departure_time": "2026-10-16T20:00:00", "arrival_time": "2026-10-16T21:40:00",
        "duration": "1h 40m", "price": 4200.0, "seats_available": 18, "is_demo_data": True,
    },
    "F007": {
        "flight_id": "F007", "airline": "Air India", "flight_number": "AI-609",
        "source": "Bengaluru", "destination": "Hyderabad",
        "departure_time": "2026-10-15T09:30:00", "arrival_time": "2026-10-15T10:40:00",
        "duration": "1h 10m", "price": 3200.0, "seats_available": 22, "is_demo_data": True,
    },
    "F008": {
        "flight_id": "F008", "airline": "Vistara", "flight_number": "UK-812",
        "source": "Bengaluru", "destination": "Chennai",
        "departure_time": "2026-10-16T13:00:00", "arrival_time": "2026-10-16T14:10:00",
        "duration": "1h 10m", "price": 2900.0, "seats_available": 0, "is_demo_data": True,
    },
    "F009": {
        "flight_id": "F009", "airline": "IndiGo", "flight_number": "6E-556",
        "source": "Chennai", "destination": "Kolkata",
        "departure_time": "2026-10-15T11:20:00", "arrival_time": "2026-10-15T13:45:00",
        "duration": "2h 25m", "price": 5600.0, "seats_available": 15, "is_demo_data": True,
    },
    "F010": {
        "flight_id": "F010", "airline": "SpiceJet", "flight_number": "SG-267",
        "source": "Kolkata", "destination": "Delhi",
        "departure_time": "2026-10-16T06:45:00", "arrival_time": "2026-10-16T09:05:00",
        "duration": "2h 20m", "price": 5100.0, "seats_available": 9, "is_demo_data": True,
    },
    "F011": {
        "flight_id": "F011", "airline": "Air India", "flight_number": "AI-101",
        "source": "Indore", "destination": "Mumbai",
        "departure_time": "2026-10-15T16:00:00", "arrival_time": "2026-10-15T17:35:00",
        "duration": "1h 35m", "price": 4700.0, "seats_available": 20, "is_demo_data": True,
    },
    "F012": {
        "flight_id": "F012", "airline": "IndiGo", "flight_number": "6E-882",
        "source": "Delhi", "destination": "Indore",
        "departure_time": "2026-10-16T19:10:00", "arrival_time": "2026-10-16T20:50:00",
        "duration": "1h 40m", "price": 4600.0, "seats_available": 27, "is_demo_data": True,
    },
    "F013": {
        "flight_id": "F013", "airline": "Vistara", "flight_number": "UK-233",
        "source": "Hyderabad", "destination": "Delhi",
        "departure_time": "2026-10-15T21:00:00", "arrival_time": "2026-10-15T23:05:00",
        "duration": "2h 05m", "price": 5900.0, "seats_available": 14, "is_demo_data": True,
    },
    "F014": {
        "flight_id": "F014", "airline": "Akasa Air", "flight_number": "QP-1755",
        "source": "Mumbai", "destination": "Delhi",
        "departure_time": "2026-10-16T05:30:00", "arrival_time": "2026-10-16T07:40:00",
        "duration": "2h 10m", "price": 6300.0, "seats_available": 33, "is_demo_data": True,
    },
}

# ---------------------------------------------------------------------------
# Generated flight inventory: fleshes out the network across every direction
# and a multi-week date range, using a fixed RNG seed so the demo data is
# stable across restarts (same flights every run, no surprises for testers).
# ---------------------------------------------------------------------------
CITIES = ["Indore", "Delhi", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata"]

_AIRLINES = [
    ("IndiGo", "6E"),
    ("Air India", "AI"),
    ("Vistara", "UK"),
    ("SpiceJet", "SG"),
    ("Akasa Air", "QP"),
]

# Approximate one-way durations (minutes) between city pairs. Demo data only —
# not sourced from any real flight schedule.
_ROUTE_DURATION_MIN = {
    frozenset({"Indore", "Delhi"}): 100,
    frozenset({"Indore", "Mumbai"}): 95,
    frozenset({"Indore", "Bengaluru"}): 120,
    frozenset({"Indore", "Hyderabad"}): 90,
    frozenset({"Indore", "Chennai"}): 135,
    frozenset({"Indore", "Kolkata"}): 140,
    frozenset({"Delhi", "Mumbai"}): 130,
    frozenset({"Delhi", "Bengaluru"}): 160,
    frozenset({"Delhi", "Hyderabad"}): 130,
    frozenset({"Delhi", "Chennai"}): 165,
    frozenset({"Delhi", "Kolkata"}): 140,
    frozenset({"Mumbai", "Bengaluru"}): 100,
    frozenset({"Mumbai", "Hyderabad"}): 85,
    frozenset({"Mumbai", "Chennai"}): 110,
    frozenset({"Mumbai", "Kolkata"}): 145,
    frozenset({"Bengaluru", "Hyderabad"}): 70,
    frozenset({"Bengaluru", "Chennai"}): 70,
    frozenset({"Bengaluru", "Kolkata"}): 140,
    frozenset({"Hyderabad", "Chennai"}): 75,
    frozenset({"Hyderabad", "Kolkata"}): 115,
    frozenset({"Chennai", "Kolkata"}): 145,
}

# How many days ahead of "today" the generated network covers, and how many
# candidate departure slots are considered per route per day.
_GENERATED_DAYS_AHEAD = 60
_DEPARTURE_HOURS = list(range(5, 23))


def _format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def _generate_flights() -> Dict[str, dict]:
    rng = random.Random("flight-booking-agent-demo-seed")
    flights: Dict[str, dict] = {}
    counter = len(_HANDWRITTEN_FLIGHTS) + 1

    today = datetime.utcnow().date()
    start_date = datetime(today.year, today.month, today.day) + timedelta(days=1)

    for day_offset in range(_GENERATED_DAYS_AHEAD):
        date = start_date + timedelta(days=day_offset)
        for source in CITIES:
            for destination in CITIES:
                if source == destination:
                    continue
                duration_min = _ROUTE_DURATION_MIN[frozenset({source, destination})]
                for _ in range(rng.choice([2, 2, 3])):
                    hour = rng.choice(_DEPARTURE_HOURS)
                    minute = rng.choice([0, 15, 30, 45])
                    departure_dt = date.replace(hour=hour, minute=minute)
                    arrival_dt = departure_dt + timedelta(minutes=duration_min)
                    airline, prefix = rng.choice(_AIRLINES)
                    # Mostly plenty of seats, occasionally low/sold-out so
                    # "insufficient seats" and "sold out" flows stay testable.
                    seats = rng.choice(
                        [0, 3, 6, 9, 12, 16, 20, 24, 28, 32, 36, 40, 40, 40]
                    )
                    price = float(round(rng.uniform(2800, 8800) / 50) * 50)

                    flight_id = f"F{counter:04d}"
                    counter += 1
                    flights[flight_id] = {
                        "flight_id": flight_id,
                        "airline": airline,
                        "flight_number": f"{prefix}-{rng.randint(100, 999)}",
                        "source": source,
                        "destination": destination,
                        "departure_time": departure_dt.isoformat(),
                        "arrival_time": arrival_dt.isoformat(),
                        "duration": _format_duration(duration_min),
                        "price": price,
                        "seats_available": seats,
                        "is_demo_data": True,
                    }

    return flights


FLIGHTS: Dict[str, dict] = {**_HANDWRITTEN_FLIGHTS, **_generate_flights()}

# ---------------------------------------------------------------------------
# In-memory bookings store: booking_id -> booking dict
# ---------------------------------------------------------------------------
BOOKINGS: Dict[str, dict] = {}


def get_flight(flight_id: str) -> Optional[dict]:
    flight = FLIGHTS.get(flight_id)
    return deepcopy(flight) if flight else None


def find_flights(source: str, destination: str, date: Optional[str] = None) -> List[dict]:
    results = []
    for flight in FLIGHTS.values():
        if flight["source"].lower() != source.lower():
            continue
        if flight["destination"].lower() != destination.lower():
            continue
        if date and not flight["departure_time"].startswith(date):
            continue
        results.append(deepcopy(flight))
    return results


def decrement_seats(flight_id: str, count: int) -> None:
    FLIGHTS[flight_id]["seats_available"] -= count


def increment_seats(flight_id: str, count: int) -> None:
    FLIGHTS[flight_id]["seats_available"] += count


def save_booking(booking: dict) -> None:
    BOOKINGS[booking["booking_id"]] = booking


def get_booking(booking_id: str) -> Optional[dict]:
    booking = BOOKINGS.get(booking_id)
    return deepcopy(booking) if booking else None
