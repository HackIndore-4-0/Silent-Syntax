"""Pydantic schemas used as LangChain tool input/output contracts."""
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class PassengerDetail(BaseModel):
    """A single passenger's details, required before any booking."""

    full_name: str = Field(..., description="Passenger's full legal name.")
    age: int = Field(..., ge=0, le=120, description="Passenger's age in years.")
    email: EmailStr = Field(..., description="Passenger's contact email address.")


class SearchFlightsInput(BaseModel):
    source: str = Field(..., description="Departure city, e.g. 'Indore'.")
    destination: str = Field(..., description="Arrival city, e.g. 'Delhi'.")
    date: str = Field(..., description="Travel date in YYYY-MM-DD format.")
    passengers: int = Field(1, ge=1, le=9, description="Number of passengers travelling.")
    max_price: Optional[float] = Field(None, description="Optional filter: maximum ticket price.")
    airline: Optional[str] = Field(None, description="Optional filter: preferred airline name.")
    departure_after: Optional[str] = Field(
        None, description="Optional filter: only flights departing at/after this HH:MM (24h) time."
    )
    departure_before: Optional[str] = Field(
        None, description="Optional filter: only flights departing at/before this HH:MM (24h) time."
    )


class GetFlightDetailsInput(BaseModel):
    flight_id: str = Field(..., description="Unique flight identifier, e.g. 'F001'.")


class BookFlightInput(BaseModel):
    flight_id: str = Field(..., description="Unique flight identifier to book.")
    passengers: List[PassengerDetail] = Field(
        ..., description="Full details for every passenger on this booking."
    )
    user_confirmation: bool = Field(
        ..., description="Must be True only after the user has explicitly confirmed the booking."
    )


class GetBookingInput(BaseModel):
    booking_id: str = Field(..., description="Unique booking identifier, e.g. 'BK-XXXXXXXX'.")


class CancelBookingInput(BaseModel):
    booking_id: str = Field(..., description="Unique booking identifier to cancel.")
    user_confirmation: bool = Field(
        ..., description="Must be True only after the user has explicitly confirmed the cancellation."
    )


class RecommendFlightsInput(BaseModel):
    source: str = Field(..., description="Departure city.")
    destination: str = Field(..., description="Arrival city.")
    date: str = Field(..., description="Travel date in YYYY-MM-DD format.")
    budget: Optional[float] = Field(None, description="Maximum budget the user is willing to pay.")
    preferred_airline: Optional[str] = Field(None, description="User's preferred airline, if any.")
    preferred_time: Optional[str] = Field(
        None,
        description="User's preferred time of day: 'morning' (5-11), 'afternoon' (11-17), "
        "'evening' (17-21) or 'night' (21-5).",
    )
