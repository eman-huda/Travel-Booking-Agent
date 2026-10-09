"""Run request and run record models, including the GreatTest export format."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class RequestOverrides(BaseModel):
    """Structured values from the UI form. Non-null values override what the LLM extracted."""

    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    travelers: int | None = Field(None, ge=1, le=9)
    budget: float | None = Field(None, gt=0)
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3}$")


class RunRequest(BaseModel):
    request_text: str = Field(min_length=3, max_length=4000)
    overrides: RequestOverrides = Field(default_factory=RequestOverrides)
    failure_mode: str = "none"
    simulate_booking: bool = True  # sandbox booking only; no real booking is ever possible


class RunMetrics(BaseModel):
    tool_calls: int
    tool_failures: int
    failures_detected: int
    retries: int
    llm_calls: int
    duration_ms: float


class RecoverySummary(BaseModel):
    attempted: bool
    result: str  # N/A | success | partial | failed
    actions: list[dict[str, Any]]


class ExpectationCheck(BaseModel):
    scenario_id: str
    failure_mode: str
    expected_behavior: str
    expected_outcome: str
    actual_outcome: str
    matches: bool


class RunRecord(BaseModel):
    run_id: str
    agent_version: str
    created_at: datetime
    llm_provider: str
    model: str
    data_mode: str = "mock"
    scenario_id: str
    failure_mode: str
    user_request: str
    status: str
    final_response: str
    initial_state: dict[str, Any]
    final_state: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    events: list[dict[str, Any]]
    errors: list[str]
    recovery: RecoverySummary
    metrics: RunMetrics
    expectation: ExpectationCheck

    def to_greattest(self) -> dict[str, Any]:
        """Export format consumed by GreatTest."""
        return {
            "schema_version": "greattest.run.v1",
            "run_id": self.run_id,
            "agent_version": self.agent_version,
            "scenario_id": self.scenario_id,
            "failure_mode": self.failure_mode,
            "created_at": self.created_at.isoformat(),
            "llm": {"provider": self.llm_provider, "model": self.model},
            "data_mode": self.data_mode,
            "initial_state": self.initial_state,
            "tool_trace": self.tool_trace,
            "events": self.events,
            "final_state": self.final_state,
            "final_response": self.final_response,
            "errors": self.errors,
            "recovery": self.recovery.model_dump(),
            "metrics": self.metrics.model_dump(),
            "expectation": self.expectation.model_dump(),
            "status": self.status,
        }
