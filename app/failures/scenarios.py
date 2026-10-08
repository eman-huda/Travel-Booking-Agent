"""Failure scenario definitions. Each scenario is deterministic and reproducible."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class FailureKind(str, Enum):
    NONE = "none"
    TIMEOUT = "timeout"
    EMPTY = "empty"
    MALFORMED = "malformed"
    API_ERROR = "api_error"
    INVALID_ARGUMENT = "invalid_argument"
    PARTIAL = "partial"
    STALE = "stale"
    CONTRADICTORY = "contradictory"
    DUPLICATE = "duplicate"
    WRONG_CURRENCY = "wrong_currency"
    BOOKING_UNAVAILABLE = "booking_unavailable"


Outcome = Literal["completed", "recovered", "completed_with_warnings", "graceful_failure"]


@dataclass(frozen=True)
class FailureScenario:
    scenario_id: str
    key: str
    label: str
    kind: FailureKind
    target_tool: str | None
    # Attempts (0-based) on which the failure fires. None means every attempt (persistent).
    failing_attempts: tuple[int, ...] | None
    description: str
    expected_behavior: str
    expected_outcome: Outcome
    requires_booking: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def fires_on(self, tool_name: str, attempt: int) -> bool:
        if self.kind == FailureKind.NONE or tool_name != self.target_tool:
            return False
        return self.failing_attempts is None or attempt in self.failing_attempts

    @property
    def persistence(self) -> str:
        if self.kind == FailureKind.NONE:
            return "n/a"
        if self.failing_attempts is None:
            return "persistent (every call fails)"
        if self.failing_attempts == (0,):
            return "transient (first call only)"
        return "transient (calls " + ", ".join(str(a + 1) for a in self.failing_attempts) + ")"


SCENARIOS: tuple[FailureScenario, ...] = (
    FailureScenario("FS-00", "none", "None", FailureKind.NONE, None, None,
        "Normal execution. No failure is injected.",
        "All tools succeed; the agent builds and validates an itinerary.", "completed"),
    FailureScenario("FS-01", "flight_timeout", "Flight Search Timeout", FailureKind.TIMEOUT, "search_flights", (0,),
        "search_flights times out on the first call only.",
        "Agent detects the timeout, retries once, receives flights and continues.", "recovered"),
    FailureScenario("FS-02", "hotel_timeout", "Hotel Search Timeout", FailureKind.TIMEOUT, "search_hotels", (0,),
        "search_hotels times out on the first call only.",
        "Agent detects the timeout, retries once, receives hotels and continues.", "recovered"),
    FailureScenario("FS-03", "empty_flights", "Empty Flight Results", FailureKind.EMPTY, "search_flights", None,
        "search_flights always returns an empty list.",
        "Agent does not invent flights; it stops and explains that no flights were found for the dates.", "graceful_failure"),
    FailureScenario("FS-04", "empty_hotels", "Empty Hotel Results", FailureKind.EMPTY, "search_hotels", (0,),
        "search_hotels returns an empty list on the first call.",
        "Agent relaxes the non-critical nightly price cap, searches again and flags the relaxation.", "recovered"),
    FailureScenario("FS-05", "malformed_flights", "Malformed Flight Output", FailureKind.MALFORMED, "search_flights", (0,),
        "search_flights returns records with wrong types and missing fields on the first call.",
        "Schema validation rejects the output; agent retries and continues with valid data.", "recovered"),
    FailureScenario("FS-06", "malformed_hotels", "Malformed Hotel Output", FailureKind.MALFORMED, "search_hotels", (0,),
        "search_hotels returns records with wrong types and missing fields on the first call.",
        "Schema validation rejects the output; agent retries and continues with valid data.", "recovered"),
    FailureScenario("FS-07", "api_error", "API Error", FailureKind.API_ERROR, "search_hotels", None,
        "The hotel service returns HTTP 503 on every call.",
        "Agent retries once, then reports that hotels could not be retrieved without inventing any.", "graceful_failure"),
    FailureScenario("FS-08", "invalid_argument", "Invalid Tool Argument", FailureKind.INVALID_ARGUMENT, "search_flights", (0,),
        "The first search_flights call is sent with corrupted arguments.",
        "Input validation rejects the call; agent rebuilds arguments from validated state and retries.", "recovered"),
    FailureScenario("FS-09", "partial_result", "Partial Tool Result", FailureKind.PARTIAL, "search_hotels", None,
        "search_hotels returns a truncated result where some hotels have unconfirmed availability.",
        "Agent retries once, then continues only with confirmed hotels and flags the rest as unverified.", "completed_with_warnings"),
    FailureScenario("FS-10", "stale_data", "Stale Data", FailureKind.STALE, "search_flights", (0,),
        "search_flights returns fares that are 72 hours old on the first call.",
        "Agent detects data older than the freshness limit and requests fresh results.", "recovered"),
    FailureScenario("FS-11", "contradictory_data", "Contradictory Data", FailureKind.CONTRADICTORY, "search_hotels", None,
        "search_hotels returns the same hotel ID twice with different prices and availability.",
        "Agent does not pick one silently; it retries, then excludes the conflicting hotels and flags them.", "completed_with_warnings"),
    FailureScenario("FS-12", "duplicate_results", "Duplicate Results", FailureKind.DUPLICATE, "search_flights", None,
        "search_flights returns exact duplicate flight records.",
        "Agent removes duplicates before comparing options and notes it.", "recovered"),
    FailureScenario("FS-13", "wrong_currency", "Wrong Currency", FailureKind.WRONG_CURRENCY, "search_hotels", None,
        "search_hotels returns prices in AED while the service contract is USD.",
        "Agent detects the currency mismatch, converts with get_exchange_rate and notes it.", "recovered"),
    FailureScenario("FS-14", "booking_unavailable", "Booking Unavailable", FailureKind.BOOKING_UNAVAILABLE, "reserve_hotel", (0,),
        "The sandbox hotel reservation reports the selected room as unavailable.",
        "Agent selects the next suitable hotel, rebuilds and revalidates the itinerary, then reserves again.", "recovered",
        requires_booking=True),
)
