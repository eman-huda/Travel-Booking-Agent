"""Typed LangGraph state for one agent run."""
from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.schemas.travel import (
    DestinationInfo,
    ExchangeRate,
    Flight,
    Hotel,
    Itinerary,
    Notice,
    PlanStep,
    Preferences,
    RecoveryAction,
    ValidationResult,
    WeatherReport,
)
from app.schemas.trace import ToolResult


def merge_dict(left: dict, right: dict) -> dict:
    out = dict(left or {})
    out.update(right or {})
    return out


class TravelState(BaseModel):
    # run metadata
    run_id: str
    failure_mode: str = "none"
    simulate_booking: bool = False
    user_request: str
    request_overrides: dict[str, Any] = Field(default_factory=dict)

    # extracted request
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    duration_days: int | None = None
    travelers: int | None = None
    budget: float | None = None
    currency: str = "USD"
    preferences: Preferences = Field(default_factory=Preferences)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None

    # plan
    plan: list[PlanStep] = Field(default_factory=list)
    plan_rationale: str | None = None

    # tool data
    flights: list[Flight] = Field(default_factory=list)
    hotels: list[Hotel] = Field(default_factory=list)
    selected_flight: Flight | None = None
    selected_hotel: Hotel | None = None
    hotel_ranking: list[str] = Field(default_factory=list)
    selection_reasons: list[str] = Field(default_factory=list)
    weather: WeatherReport | None = None
    exchange_rate: ExchangeRate | None = None          # pricing currency -> destination local currency
    budget_rate: float = 1.0                            # pricing currency (USD) -> user's budget currency
    destination_info: DestinationInfo | None = None
    itinerary: Itinerary | None = None
    validation_results: Annotated[dict[str, ValidationResult], merge_dict] = Field(default_factory=dict)
    bookings: Annotated[dict[str, Any], merge_dict] = Field(default_factory=dict)

    # control and recovery
    next_action: str | None = None
    retry_count: int = 0
    tool_attempts: Annotated[dict[str, int], merge_dict] = Field(default_factory=dict)
    last_tool_result: Annotated[dict[str, ToolResult], merge_dict] = Field(default_factory=dict)
    hotel_search_relaxed: bool = False
    hotel_budget_cap: float | None = None
    excluded_hotel_ids: list[str] = Field(default_factory=list)
    tried_hotel_ids: list[str] = Field(default_factory=list)
    itinerary_repairs: int = 0
    booking_alternatives_used: int = 0
    abort_reason: str | None = None

    # observability (append-only)
    errors: Annotated[list[str], operator.add] = Field(default_factory=list)
    notices: Annotated[list[Notice], operator.add] = Field(default_factory=list)
    unverified: Annotated[list[str], operator.add] = Field(default_factory=list)
    recovery_actions: Annotated[list[RecoveryAction], operator.add] = Field(default_factory=list)
    tool_trace: Annotated[list[dict], operator.add] = Field(default_factory=list)
    visited_nodes: Annotated[list[str], operator.add] = Field(default_factory=list)

    # output
    final_response: str | None = None
    status: str = "running"
