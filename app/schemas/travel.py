"""Core travel domain models shared by tools, agent state and UI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PRICING_CURRENCY = "USD"  # currency used by the travel data providers


def normalise_currency(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError("currency must be a 3-letter ISO code")
    return value


class Preferences(BaseModel):
    avoid_early_departure: bool = False
    earliest_departure_hour: int = Field(7, ge=0, le=23)
    prefer_direct: bool = False
    min_hotel_rating: float | None = Field(None, ge=0, le=5)
    activity_budget: Literal["low", "medium", "high"] = "medium"
    notes: list[str] = Field(default_factory=list)


class Flight(BaseModel):
    """Round-trip flight option. price is per passenger for the round trip."""

    model_config = ConfigDict(extra="ignore")

    flight_id: str = Field(min_length=1)
    airline: str = Field(min_length=1)
    flight_number: str
    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    duration: str
    duration_minutes: int = Field(gt=0)
    return_flight_number: str
    return_departure: datetime
    return_arrival: datetime
    price: float = Field(gt=0)
    currency: str
    stops: int = Field(ge=0, le=3)
    cabin: str = "Economy"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return normalise_currency(v)

    @model_validator(mode="after")
    def _ordering(self) -> "Flight":
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        if self.return_departure <= self.arrival:
            raise ValueError("return departure must be after outbound arrival")
        if self.return_arrival <= self.return_departure:
            raise ValueError("return arrival must be after return departure")
        return self


class Hotel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hotel_id: str = Field(min_length=1)
    name: str
    city: str
    location: str
    rating: float = Field(ge=0, le=5)
    nightly_price: float = Field(gt=0)
    currency: str
    amenities: list[str] = Field(default_factory=list)
    availability: bool | None  # None means the provider did not confirm availability
    room_type: str = "Standard room"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return normalise_currency(v)


class Attraction(BaseModel):
    attraction_id: str
    name: str
    category: str
    estimated_cost: float = Field(ge=0)
    currency: str
    duration_hours: float = Field(gt=0)
    best_time: Literal["morning", "afternoon", "evening", "any"] = "any"
    indoor: bool = False


class WeatherReport(BaseModel):
    status: Literal["success"] = "success"
    destination: str
    date: date
    temperature: float
    condition: str
    basis: Literal["forecast", "climate_average"] = "climate_average"
    source: str = "mock"


class ExchangeRate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["success"] = "success"
    from_currency: str = Field(alias="from")
    to_currency: str = Field(alias="to")
    rate: float = Field(gt=0)
    retrieved_at: datetime
    source: str = "mock"


class DestinationInfo(BaseModel):
    status: Literal["success"] = "success"
    destination: str
    country: str
    description: str
    local_currency: str
    attractions: list[Attraction]
    transport: list[str]
    general_info: list[str]
    source: str = "mock"


class ItineraryItem(BaseModel):
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    activity: str
    kind: Literal["flight", "transfer", "hotel", "attraction", "meal", "free"]
    attraction_id: str | None = None
    estimated_cost: float = 0.0
    notes: str | None = None


class ItineraryDay(BaseModel):
    day_number: int = Field(ge=1)
    date: date
    title: str
    items: list[ItineraryItem]
    weather: str | None = None


class Itinerary(BaseModel):
    destination: str
    start_date: date
    end_date: date
    travelers: int
    currency: str
    days: list[ItineraryDay]
    cost_breakdown: dict[str, float]
    total_estimated_cost: float
    assumptions: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class Notice(BaseModel):
    """Something the agent wants the user or researcher to know."""

    level: Literal["info", "warning", "error"]
    code: str
    message: str
    source: str | None = None


class RecoveryAction(BaseModel):
    node: str
    tool: str | None
    issue: str
    action: str
    detail: str
    attempt: int = 0


class PlanStep(BaseModel):
    tool: str
    reason: str
