"""Deterministic offline provider. Used for tests, CI and demos without an API key.

It implements the same tasks as a real model with transparent rules, so every
experiment remains reproducible.
"""
from __future__ import annotations

from typing import Any

from app.agent.request_parser import parse_request
from app.llm.base import LLMError, LLMProvider


class StubLLMProvider(LLMProvider):
    name = "stub"
    model = "stub-deterministic"

    def generate_json(self, *, task: str, system: str, prompt: str, context: dict[str, Any]) -> dict:
        if task == "extract_request":
            return parse_request(context["user_request"], context["known_cities"], context["today"])
        if task == "plan_trip":
            steps = [{"tool": t, "reason": r} for t, r in (
                ("get_exchange_rate", "Convert between the pricing currency, the budget currency and the local currency."),
                ("search_flights", "Find round-trip flights for the requested route and dates."),
                ("search_hotels", "Find hotels for the stay, capped by the remaining budget."),
                ("get_weather", "Plan indoor or outdoor activities around expected conditions."),
                ("get_destination_info", "Get attractions and practical information for the itinerary."),
                ("create_itinerary", "Assemble the day-by-day plan."),
                ("validate_itinerary", "Check dates, budget, ordering and duplicates before answering."),
            ) if t in context["available_tools"]]
            return {"steps": steps, "rationale": "Standard trip plan: price the trip, find transport and lodging, then build and check the itinerary."}
        raise LLMError(f"Stub provider has no rule for task '{task}'")

    def generate_text(self, *, task: str, system: str, prompt: str, context: dict[str, Any]) -> str:
        if task != "final_response":
            raise LLMError(f"Stub provider has no rule for task '{task}'")
        f = context.get("facts", {})
        if f.get("outcome") == "no_itinerary":
            problems = [p if p.endswith(".") else p + "." for p in f.get("blocking_problems", [])]
            return f"I could not complete a plan for {f.get('route', 'this trip')}. " + " ".join(problems)
        lines = [f"Here is a {f.get('days')}-day plan for {f.get('route')} from {f.get('dates')}."]
        if f.get("flight"):
            lines.append(f"Recommended flight: {f['flight']}.")
        if f.get("hotel"):
            lines.append(f"Recommended hotel: {f['hotel']}.")
        if f.get("cost"):
            lines.append(f"Estimated cost: {f['cost']}.")
        if f.get("selection_reasons"):
            lines.append("Why: " + " ".join(f["selection_reasons"]))
        if f.get("weather"):
            lines.append(f"Expected weather: {f['weather']}.")
        return " ".join(lines)
