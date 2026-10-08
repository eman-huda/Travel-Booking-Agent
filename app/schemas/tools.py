"""Explicit input and output schemas for every registered tool."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.travel import (
    Attraction,
    Flight,
    Hotel,
    Itinerary,
    Preferences,
    ValidationIssue,
    WeatherReport,
)


class StrictInput(BaseModel):
    """Tool inputs reject unknown arguments so the LLM cannot smuggle parameters in."""

    model_config = ConfigDict(extra="forbid")


# ---------- search_flights ----------
class SearchFlightsInput(StrictInput):
    origin: str = Field(min_length=2, max_length=60)
    destination: str = Field(min_length=2, max_length=60)
    departure_date: date
    return_date: date
    passengers: int = Field(ge=1, le=9)

    @model_validator(mode="after")
    def _dates(self):
        if self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if self.origin.strip().lower() == self.destination.strip().lower():
            raise ValueError("origin and destination must differ")
        return self


class FlightSearchOutput(BaseModel):
    status: Literal["success", "partial"]
    flights: list[Flight]
    retrieved_at: datetime
    total_results: int | None = None
    source: str = "mock"


# ---------- search_hotels ----------
class SearchHotelsInput(StrictInput):
    destination: str = Field(min_length=2, max_length=60)
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=9)
    budget: float | None = Field(None, gt=0, description="Maximum nightly price in USD; null means no cap")

    @model_validator(mode="after")
    def _dates(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class HotelSearchOutput(BaseModel):
    status: Literal["success", "partial"]
    hotels: list[Hotel]
    retrieved_at: datetime
    total_results: int | None = None
    source: str = "mock"


# ---------- get_weather ----------
class WeatherInput(StrictInput):
    destination: str = Field(min_length=2, max_length=60)
    date: date


WeatherOutput = WeatherReport


# ---------- get_exchange_rate ----------
class ExchangeRateInput(StrictInput):
    from_currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    to_currency: str = Field(pattern=r"^[A-Za-z]{3}$")

    @field_validator("from_currency", "to_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class ExchangeRateOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["success"]
    from_currency: str = Field(alias="from")
    to_currency: str = Field(alias="to")
    rate: float = Field(gt=0)
    retrieved_at: datetime
    source: str = "mock"


# ---------- get_destination_info ----------
class DestinationInput(StrictInput):
    destination: str = Field(min_length=2, max_length=60)


class DestinationOutput(BaseModel):
    status: Literal["success"]
    destination: str
    country: str
    description: str
    local_currency: str
    attractions: list[Attraction]
    transport: list[str]
    general_info: list[str]
    source: str = "mock"


# ---------- create_itinerary ----------
class CreateItineraryInput(StrictInput):
    destination: str
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=9)
    currency: str = "USD"
    selected_flight: Flight
    selected_hotel: Hotel
    user_preferences: Preferences
    attractions: list[Attraction] = Field(default_factory=list)
    weather: WeatherReport | None = None


class CreateItineraryOutput(BaseModel):
    status: Literal["success"]
    itinerary: Itinerary


# ---------- validate_itinerary ----------
class ValidateItineraryInput(StrictInput):
    itinerary: Itinerary
    budget: float | None = Field(None, gt=0)
    currency: str = "USD"
    departure_date: date
    return_date: date
    selected_flight: Flight
    selected_hotel: Hotel


class ValidateItineraryOutput(BaseModel):
    status: Literal["success"]
    valid: bool
    issues: list[ValidationIssue]


# ---------- simulated write tools ----------
class BookFlightInput(StrictInput):
    flight_id: str = Field(min_length=1)
    passengers: int = Field(ge=1, le=9)


class ReserveHotelInput(StrictInput):
    hotel_id: str = Field(min_length=1)
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=9)


class SimulatedBookingOutput(BaseModel):
    status: Literal["CONFIRMED", "UNAVAILABLE"]
    booking_id: str | None
    environment: Literal["sandbox"]
    message: str
