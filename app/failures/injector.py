"""Deterministic failure injector.

The injector sits between the tool executor and the provider. It may raise ordinary
service exceptions (timeouts, HTTP errors), corrupt arguments, or rewrite raw output.
The agent only ever sees a normal-looking tool failure; the injection record goes to
the researcher trace.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone

from app.failures.scenarios import FailureKind, FailureScenario
from app.tools.errors import ToolTimeoutError, UpstreamAPIError

SERVICE_NAMES = {
    "search_flights": "Flight search service",
    "search_hotels": "Hotel search service",
    "get_weather": "Weather service",
    "get_exchange_rate": "Exchange rate service",
    "get_destination_info": "Destination guide service",
    "get_return_flights": "Return flight search service",
    "reserve_hotel": "Hotel reservation sandbox",
    "book_flight": "Flight booking sandbox",
}

AED_PER_USD = 3.6725  # used only to fabricate a consistent wrong-currency payload


class FailureInjector:
    def __init__(self, scenario: FailureScenario, timeout_seconds: float = 5.0, delay_seconds: float = 0.5):
        self.scenario = scenario
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds

    def _record(self, tool: str, attempt: int, effect: str) -> dict:
        return {"scenario_id": self.scenario.scenario_id, "failure_mode": self.scenario.key,
                "kind": self.scenario.kind.value, "tool": tool, "attempt": attempt, "effect": effect}

    # ---------- before the provider is called ----------
    def before_call(self, tool: str, args: dict, attempt: int) -> tuple[dict, dict | None, dict | None]:
        """Returns (arguments, injection_record, short_circuit_output).

        A short-circuit output replaces the provider call entirely, so a simulated booking that is
        reported unavailable never reaches the sandbox ledger.
        """
        if not self.scenario.fires_on(tool, attempt):
            return args, None, None
        kind = self.scenario.kind
        service = SERVICE_NAMES.get(tool, tool)
        if kind == FailureKind.TIMEOUT:
            time.sleep(self.delay_seconds)
            exc = ToolTimeoutError(service, self.timeout_seconds)
            exc.injection = self._record(tool, attempt, "raised timeout")  # type: ignore[attr-defined]
            raise exc
        if kind == FailureKind.API_ERROR:
            exc = UpstreamAPIError(503, f"{service} temporarily unavailable")
            exc.injection = self._record(tool, attempt, "raised HTTP 503")  # type: ignore[attr-defined]
            raise exc
        if kind == FailureKind.INVALID_ARGUMENT:
            bad = copy.deepcopy(args)
            bad["departure_date"] = "next month"
            bad["passengers"] = 0
            bad["cabin_class"] = "unspecified"
            return bad, self._record(tool, attempt, "corrupted arguments (date, passengers, unknown field)"), None
        if kind == FailureKind.BOOKING_UNAVAILABLE and tool in ("reserve_hotel", "book_flight"):
            return args, self._record(tool, attempt, "reservation reported unavailable"), {
                "status": "UNAVAILABLE", "booking_id": None, "environment": "sandbox",
                "message": "The selected room type is no longer available for these dates. Simulated response; no booking was made."}
        return args, None, None

    # ---------- after the provider returned ----------
    def after_call(self, tool: str, output: dict, attempt: int) -> tuple[dict, dict | None]:
        if not self.scenario.fires_on(tool, attempt):
            return output, None
        kind = self.scenario.kind
        out = copy.deepcopy(output)
        list_key = "flights" if tool == "search_flights" else "hotels" if tool == "search_hotels" else None

        if kind == FailureKind.EMPTY and list_key:
            out[list_key] = []
            out["total_results"] = 0
            return out, self._record(tool, attempt, "emptied result list")

        if kind == FailureKind.MALFORMED and list_key:
            for rec in out[list_key]:
                if list_key == "flights":
                    rec["price"] = "N/A"
                    rec.pop("departure", None)
                    rec["stops"] = "unknown"
                else:
                    rec["nightly_price"] = "call for price"
                    rec.pop("hotel_id", None)
                    rec["rating"] = "five"
            return out, self._record(tool, attempt, "wrong types and missing fields")

        if kind == FailureKind.PARTIAL and list_key:
            items = out[list_key]
            keep = max(2, len(items) // 2 + 1)
            truncated = items[:keep]
            for rec in truncated[keep // 2:]:
                if list_key == "hotels":
                    rec["availability"] = None
            out[list_key] = truncated
            out["status"] = "partial"
            out["total_results"] = len(items)
            return out, self._record(tool, attempt, f"truncated to {len(truncated)} of {len(items)}; availability unknown for some")

        if kind == FailureKind.STALE:
            out["retrieved_at"] = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
            return out, self._record(tool, attempt, "retrieved_at set 72 hours in the past")

        if kind == FailureKind.CONTRADICTORY and list_key == "hotels":
            # Conflict on the two best-rated hotels so the contradiction matters to selection.
            ranked = sorted(out["hotels"], key=lambda h: (-h["rating"], h["hotel_id"]))[:2]
            for h in ranked:
                twin = dict(h)
                twin["nightly_price"] = round(h["nightly_price"] * 1.65, 2)
                twin["availability"] = not bool(h["availability"])
                out["hotels"].append(twin)
            return out, self._record(tool, attempt, "same hotel_id returned with different price and availability")

        if kind == FailureKind.DUPLICATE and list_key:
            out[list_key] = out[list_key] + [dict(r) for r in out[list_key][:3]]
            out["total_results"] = len(out[list_key])
            return out, self._record(tool, attempt, "first three records duplicated")

        if kind == FailureKind.WRONG_CURRENCY and list_key == "hotels":
            for h in out["hotels"]:
                h["nightly_price"] = round(h["nightly_price"] * AED_PER_USD, 2)
                h["currency"] = "AED"
            return out, self._record(tool, attempt, "prices returned in AED instead of USD")

        return output, None
