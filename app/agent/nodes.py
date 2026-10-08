"""LangGraph nodes. Each node is one observable step: an LLM step, a tool call, or a validation step."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig

from app.agent.context import AgentContext
from app.agent.prompts import EXTRACT_SYSTEM, FINAL_SYSTEM, PLAN_SYSTEM
from app.agent.request_parser import find_dates, parse_request
from app.agent.state import TravelState
from app.llm.base import LLMError
from app.safety.permissions import Permission
from app.safety.redaction import sanitise_user_request
from app.schemas.tools import FlightSearchOutput, HotelSearchOutput
from app.schemas.trace import ToolResult
from app.schemas.travel import (
    PRICING_CURRENCY,
    DestinationInfo,
    ExchangeRate,
    Flight,
    Hotel,
    Itinerary,
    Notice,
    PlanStep,
    Preferences,
    RecoveryAction,
    ValidationIssue,
    ValidationResult,
    WeatherReport,
)
from app.tools.itinerary import rooms_needed

ACTIVITY_RESERVE_PER_DAY = {"low": 25.0, "medium": 50.0, "high": 100.0}
CANONICAL_ORDER = ["get_exchange_rate", "search_flights", "search_hotels", "get_weather",
                   "get_destination_info", "create_itinerary", "validate_itinerary", "book_flight", "reserve_hotel"]
MANDATORY = ["search_flights", "search_hotels", "create_itinerary", "validate_itinerary"]

NODE_LABELS = {
    "understand_request": "Understanding request",
    "clarify": "Asking for missing details",
    "plan_trip": "Planning tool calls",
    "get_currency": "Checking currency",
    "search_flights": "Searching flights",
    "validate_flights": "Validating flights",
    "search_hotels": "Searching hotels",
    "validate_hotels": "Validating hotels",
    "get_weather": "Checking weather",
    "get_destination_info": "Getting destination information",
    "select_options": "Comparing and selecting options",
    "create_itinerary": "Building itinerary",
    "validate_itinerary": "Validating itinerary",
    "book_trip": "Simulating sandbox booking",
    "final_response": "Writing recommendation",
}


# --------------------------------------------------------------------------- helpers
class Acc:
    """Collects tool calls, retries and notices made inside one node, then returns a state update."""

    def __init__(self, state: TravelState, ctx: AgentContext, node: str):
        self.state, self.ctx, self.node = state, ctx, node
        self.attempts = dict(state.tool_attempts)
        self.retry_count = state.retry_count
        self.trace: list[dict] = []
        self.notices: list[Notice] = []
        self.unverified: list[str] = []
        self.errors: list[str] = []
        self.recovery: list[RecoveryAction] = []
        self.last: dict[str, ToolResult] = {}

    def call(self, tool: str, args: dict, key: str | None = None) -> ToolResult:
        """key identifies one logical call; retries of the same call share a key."""
        key = key or tool
        attempt = self.attempts.get(key, 0)
        result = self.ctx.executor.execute(tool, args, node=self.node, attempt=attempt)
        self.attempts[key] = attempt + 1
        self.last[tool] = result
        self.trace.append(self.ctx.tracer.tool_events()[-1].model_dump(mode="json"))
        return result

    def can_retry(self, key: str) -> bool:
        return self.ctx.policy.can_retry(self.attempts, self.retry_count, key)

    def recover(self, tool: str | None, issue: str, action: str, detail: str, key: str | None = None) -> None:
        attempt = self.attempts.get(key or tool, 0) if tool else 0
        self.recovery.append(RecoveryAction(node=self.node, tool=tool, issue=issue, action=action, detail=detail, attempt=attempt))
        self.ctx.tracer.recovery(self.node, tool, issue, action, detail, attempt)
        if action.startswith("retry"):
            self.retry_count += 1

    def notice(self, level: str, code: str, message: str, source: str | None = None) -> None:
        self.notices.append(Notice(level=level, code=code, message=message, source=source or self.node))

    def call_with_retry(self, tool: str, args: dict, key: str | None = None) -> ToolResult:
        """Inline bounded retry used by non-critical and single-shot tool nodes."""
        key = key or tool
        while True:
            res = self.call(tool, args, key)
            if res.status == "success" or not res.error.retryable or not self.can_retry(key):
                return res
            self.recover(tool, res.error.type, self.ctx.policy.action_for_error(res.error), res.error.message, key)

    def update(self, **extra: Any) -> dict:
        out: dict[str, Any] = {
            "tool_attempts": self.attempts, "retry_count": self.retry_count, "tool_trace": self.trace,
            "notices": self.notices, "unverified": self.unverified, "errors": self.errors,
            "recovery_actions": self.recovery, "last_tool_result": self.last,
        }
        out.update(extra)
        return out


def graph_node(name: str) -> Callable:
    def deco(fn: Callable[[TravelState, AgentContext], dict]):
        def wrapper(state: TravelState, config: RunnableConfig) -> dict:
            ctx: AgentContext = config["configurable"]["ctx"]
            ctx.tracer.node_start(name)
            try:
                update = fn(state, ctx) or {}
            except Exception as exc:
                ctx.tracer.node_end(name, "failure", f"{type(exc).__name__}: {exc}")
                raise
            status = update.pop("__status__", "success")
            message = update.pop("__message__", None)
            update["visited_nodes"] = [name]
            ctx.tracer.node_end(name, status, message)
            return update
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def budget_in_pricing_currency(state: TravelState) -> float | None:
    if state.budget is None or not state.budget_rate:
        return None
    return round(state.budget / state.budget_rate, 2)


def in_plan(state: TravelState, tool: str) -> bool:
    return any(step.tool == tool for step in state.plan)


def trip_nights(state: TravelState) -> int:
    return max(1, (state.return_date - state.departure_date).days)


def activity_reserve(state: TravelState) -> float:
    days = (state.return_date - state.departure_date).days + 1
    return ACTIVITY_RESERVE_PER_DAY[state.preferences.activity_budget] * (state.travelers or 1) * days


def money(amount: float, cur: str = PRICING_CURRENCY) -> str:
    return f"{amount:,.2f} {cur}"


# --------------------------------------------------------------------------- understand
@graph_node("understand_request")
def understand_request(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "understand_request")
    today = date.today()
    clean, changed = sanitise_user_request(state.user_request)
    if changed:
        acc.notice("warning", "payment_data_removed",
                   "Payment details were removed from the request. This agent never needs or uses payment information.")
    known = ctx.cities.known_cities()
    prompt = (f"Today's date: {today.isoformat()}.\nCities with travel data: {', '.join(known)}.\n"
              f"User message:\n{clean}")
    try:
        raw = ctx.llm_json(node="understand_request", task="extract_request", system=EXTRACT_SYSTEM, prompt=prompt,
                           context={"user_request": clean, "known_cities": known, "today": today})
    except LLMError as exc:
        acc.notice("warning", "llm_extraction_failed", f"The language model could not extract the request ({exc}); "
                                                        "the rule-based parser was used instead.")
        raw = parse_request(clean, known, today)

    def as_date(v) -> date | None:
        try:
            return date.fromisoformat(str(v)) if v else None
        except ValueError:
            return None

    dep, ret = as_date(raw.get("departure_date")), as_date(raw.get("return_date"))
    # Guard against invented dates: the text must contain a concrete date expression.
    if (dep or ret) and not find_dates(clean, today):
        acc.notice("warning", "date_not_stated", "A date was proposed by the model but no specific date appears in the "
                                                 "request, so it was discarded.")
        dep = ret = None

    prefs_raw = raw.get("preferences") or {}
    try:
        prefs = Preferences(**{k: v for k, v in prefs_raw.items() if k in Preferences.model_fields and v is not None})
    except Exception:
        prefs = Preferences()

    ov = state.request_overrides or {}
    origin = ov.get("origin") or raw.get("origin")
    destination = ov.get("destination") or raw.get("destination")
    dep = as_date(ov.get("departure_date")) or dep
    ret = as_date(ov.get("return_date")) or ret
    travelers = ov.get("travelers") or raw.get("travelers")
    budget = ov.get("budget") or raw.get("budget")
    currency = (ov.get("currency") or raw.get("currency") or "USD").upper()
    duration = raw.get("duration_days")

    for label, value in (("origin", origin), ("destination", destination)):
        if value:
            city = ctx.cities.resolve_city(str(value))
            if city:
                if label == "origin":
                    origin = city["name"]
                else:
                    destination = city["name"]

    if dep and not ret and duration:
        ret = dep + timedelta(days=int(duration) - 1)
    if dep and ret:
        duration = (ret - dep).days + 1

    missing, reasons = [], []
    if not origin:
        missing.append("origin"); reasons.append("which city you are flying from")
    if not destination:
        missing.append("destination"); reasons.append("where you want to go")
    if not dep:
        missing.append("departure_date")
        hint = raw.get("date_hint")
        reasons.append(f"your exact departure date (you said '{hint}')" if hint else "your departure date")
    elif dep < today:
        missing.append("departure_date"); reasons.append(f"a departure date in the future ({dep} has passed)")
    if dep and not ret:
        missing.append("return_date"); reasons.append("your return date or how many days you will stay")
    elif dep and ret and (ret - dep).days < 1:
        missing.append("return_date"); reasons.append("a return date at least one night after departure")
    if not travelers:
        missing.append("travelers"); reasons.append("how many people are travelling")

    if budget is None:
        acc.notice("info", "no_budget", "No budget was given, so options are ranked by value without a budget cap.")

    question = None
    if missing:
        question = "Before I search, I need " + "; ".join(reasons) + "."
    return acc.update(
        origin=origin, destination=destination, departure_date=dep, return_date=ret, duration_days=duration,
        travelers=int(travelers) if travelers else None, budget=float(budget) if budget else None,
        currency=currency, preferences=prefs, missing_fields=missing, clarification_question=question,
        next_action="clarify" if missing else "plan",
        __message__=("missing: " + ", ".join(missing)) if missing else "all required fields present",
    )


@graph_node("clarify")
def clarify(state: TravelState, ctx: AgentContext) -> dict:
    return {"status": "needs_clarification", "final_response": state.clarification_question}


# --------------------------------------------------------------------------- plan
@graph_node("plan_trip")
def plan_trip(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "plan_trip")
    read_only = [t for t in ctx.registry.catalogue_for_llm() if t["permission"] == Permission.READ_ONLY.value]
    request_summary = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in {
        "origin": state.origin, "destination": state.destination, "departure_date": state.departure_date,
        "return_date": state.return_date, "travelers": state.travelers, "budget": state.budget,
        "currency": state.currency, "preferences": state.preferences.model_dump()}.items()}
    prompt = f"Request: {json.dumps(request_summary)}\nTool catalogue: {json.dumps(read_only)}"
    try:
        raw = ctx.llm_json(node="plan_trip", task="plan_trip", system=PLAN_SYSTEM, prompt=prompt,
                           context={"available_tools": [t["name"] for t in read_only]})
        steps_raw, rationale = raw.get("steps") or [], raw.get("rationale")
    except LLMError as exc:
        acc.notice("warning", "llm_planning_failed", f"Planning model call failed ({exc}); the default plan was used.")
        steps_raw, rationale = [], "Default plan."

    allowed = {t["name"] for t in read_only}
    steps: dict[str, PlanStep] = {}
    for s in steps_raw:
        name = (s or {}).get("tool") if isinstance(s, dict) else None
        if name in allowed:
            steps.setdefault(name, PlanStep(tool=name, reason=str(s.get("reason", ""))[:200]))
        elif name:
            acc.notice("warning", "plan_rejected_tool", f"The plan asked for '{name}', which is not an allowed tool here. It was ignored.")
    for name in MANDATORY:
        steps.setdefault(name, PlanStep(tool=name, reason="Required by the agent workflow."))
    if state.currency != PRICING_CURRENCY:
        steps.setdefault("get_exchange_rate", PlanStep(tool="get_exchange_rate",
                         reason=f"Needed to compare {PRICING_CURRENCY} prices with a budget in {state.currency}."))
    if state.simulate_booking:
        steps["book_flight"] = PlanStep(tool="book_flight", reason="Sandbox booking requested (simulated only).")
        steps["reserve_hotel"] = PlanStep(tool="reserve_hotel", reason="Sandbox reservation requested (simulated only).")
    ordered = sorted(steps.values(), key=lambda p: CANONICAL_ORDER.index(p.tool))
    return acc.update(plan=ordered, plan_rationale=rationale, __message__=" -> ".join(p.tool for p in ordered))


# --------------------------------------------------------------------------- currency
@graph_node("get_currency")
def get_currency(state: TravelState, ctx: AgentContext) -> dict:
    if not in_plan(state, "get_exchange_rate"):
        return {"__status__": "skipped", "__message__": "not in plan"}
    acc = Acc(state, ctx, "get_currency")
    extra: dict[str, Any] = {}
    status = "success"
    if state.currency != PRICING_CURRENCY:
        res = acc.call_with_retry("get_exchange_rate", {"from_currency": PRICING_CURRENCY, "to_currency": state.currency},
                                  key=f"get_exchange_rate:{PRICING_CURRENCY}:{state.currency}")
        if res.status == "success":
            extra["budget_rate"] = res.data["rate"]
        else:
            status = "failure"
            extra["budget_rate"] = 0.0
            acc.unverified.append(f"Conversion between {PRICING_CURRENCY} and {state.currency}, so the budget could not be checked")
            acc.notice("warning", "budget_conversion_failed", f"Could not convert {state.currency} to {PRICING_CURRENCY}: {res.error.message}")
    city = ctx.cities.resolve_city(state.destination or "")
    local = city["local_currency"] if city else None
    if local and local != PRICING_CURRENCY:
        res = acc.call_with_retry("get_exchange_rate", {"from_currency": PRICING_CURRENCY, "to_currency": local},
                                  key=f"get_exchange_rate:{PRICING_CURRENCY}:{local}")
        if res.status == "success":
            extra["exchange_rate"] = ExchangeRate.model_validate(res.data)
        else:
            status = "failure"
            acc.unverified.append(f"Local currency rate ({local})")
            acc.notice("warning", "local_rate_failed", f"Could not get the {PRICING_CURRENCY}/{local} rate: {res.error.message}")
    return acc.update(**extra, __status__=status)


# --------------------------------------------------------------------------- flights
@graph_node("search_flights")
def search_flights(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "search_flights")
    res = acc.call("search_flights", {
        "origin": state.origin, "destination": state.destination,
        "departure_date": state.departure_date.isoformat(), "return_date": state.return_date.isoformat(),
        "passengers": state.travelers,
    })
    return acc.update(__status__="success" if res.status == "success" else "failure",
                      __message__=res.error.message if res.error else f"{len(res.data['flights'])} flights")


def _abort(acc: Acc, tool: str, issue: str, reason: str) -> dict:
    acc.recover(tool, issue, "report_failure", reason)
    acc.errors.append(reason)
    return acc.update(next_action="abort", abort_reason=reason, __status__="failure", __message__=reason)


def _retry(acc: Acc, tool: str, issue: str, action: str, detail: str, **extra) -> dict:
    acc.recover(tool, issue, action, detail)
    return acc.update(next_action="retry", __status__="retrying", __message__=detail, **extra)


def _split_duplicates(records: list, key: str) -> tuple[list, int, dict[str, list]]:
    """Returns unique records, number of exact duplicates removed, and conflicting groups by key."""
    groups: dict[str, list] = {}
    for r in records:
        groups.setdefault(getattr(r, key), []).append(r)
    unique, dupes, conflicts = [], 0, {}
    for k, items in groups.items():
        dumps = {json.dumps(i.model_dump(mode="json"), sort_keys=True) for i in items}
        if len(dumps) > 1:
            conflicts[k] = items
            continue
        unique.append(items[0])
        dupes += len(items) - 1
    return unique, dupes, conflicts


def _stale_hours(retrieved_at: datetime) -> float:
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - retrieved_at).total_seconds() / 3600


@graph_node("validate_flights")
def validate_flights(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "validate_flights")
    tool = "search_flights"
    res = state.last_tool_result[tool]
    if res.status == "error":
        if res.error.retryable and acc.can_retry(tool):
            return _retry(acc, tool, res.error.type, ctx.policy.action_for_error(res.error), res.error.message)
        return _abort(acc, tool, res.error.type, f"Flight search failed after {acc.attempts.get(tool, 0)} attempt(s): {res.error.message}")

    out = FlightSearchOutput.model_validate(res.data)
    issues: list[ValidationIssue] = []
    age = _stale_hours(out.retrieved_at)
    if age > ctx.settings.stale_data_max_age_hours:
        detail = f"Fares were retrieved {age:.0f} hours ago; the freshness limit is {ctx.settings.stale_data_max_age_hours} hours."
        if acc.can_retry(tool):
            return _retry(acc, tool, "stale_data", "retry_for_fresh_data", detail)
        acc.notice("warning", "stale_flights", detail + " Prices may have changed.")
        acc.unverified.append("Current flight prices (data is out of date)")
        issues.append(ValidationIssue(code="stale_data", severity="warning", message=detail))
    if out.status == "partial":
        detail = f"Flight search returned a partial result ({len(out.flights)} of {out.total_results})."
        if acc.can_retry(tool):
            return _retry(acc, tool, "partial_result", "retry_for_complete_result", detail)
        acc.notice("warning", "partial_flights", detail)
        issues.append(ValidationIssue(code="partial_result", severity="warning", message=detail))

    flights, dupes, conflicts = _split_duplicates(out.flights, "flight_id")
    if dupes:
        msg = f"Removed {dupes} duplicate flight record(s) before comparing options."
        acc.recover(tool, "duplicate_results", "deduplicate", msg)
        acc.notice("info", "duplicates_removed", msg)
        issues.append(ValidationIssue(code="duplicates_removed", severity="info", message=msg))
    if conflicts:
        detail = f"Flight IDs with conflicting details: {', '.join(conflicts)}."
        if acc.can_retry(tool):
            return _retry(acc, tool, "contradictory_data", "retry_to_resolve_conflict", detail)
        acc.recover(tool, "contradictory_data", "exclude_conflicting_records", detail)
        acc.notice("warning", "conflicting_flights", detail + " These flights were excluded instead of guessing.")
        acc.unverified.extend(f"Flight {k}: conflicting details" for k in conflicts)

    consistent = []
    for f in flights:
        problems = []
        if f.origin != state.origin or f.destination != state.destination:
            problems.append("route does not match the request")
        if f.departure.date() != state.departure_date or f.return_departure.date() != state.return_date:
            problems.append("dates do not match the request")
        if f.currency != PRICING_CURRENCY:
            problems.append(f"priced in {f.currency}, expected {PRICING_CURRENCY}")
        if problems:
            issues.append(ValidationIssue(code="inconsistent_flight", severity="warning", message=f"{f.flight_id}: {'; '.join(problems)}"))
            acc.notice("warning", "inconsistent_flight", f"Excluded flight {f.flight_id}: {'; '.join(problems)}.")
        else:
            consistent.append(f)

    if not consistent:
        return _abort(acc, tool, "empty_results",
                      f"No flights were found from {state.origin} to {state.destination} departing {state.departure_date} "
                      f"and returning {state.return_date}. Route and dates are critical constraints, so they were not relaxed.")
    return acc.update(flights=consistent, next_action="continue",
                      validation_results={"flights": ValidationResult(valid=True, issues=issues)},
                      __message__=f"{len(consistent)} valid flights")


# --------------------------------------------------------------------------- hotels
def _eligible_flights(state: TravelState) -> list[Flight]:
    p = state.preferences
    flights = state.flights
    if p.avoid_early_departure:
        late = [f for f in flights if f.departure.hour >= p.earliest_departure_hour]
        flights = late or flights
    if p.prefer_direct:
        direct = [f for f in flights if f.stops == 0]
        flights = direct or flights
    return flights


@graph_node("search_hotels")
def search_hotels(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "search_hotels")
    cap = None
    budget_usd = budget_in_pricing_currency(state)
    if budget_usd and not state.hotel_search_relaxed:
        cheapest = min(f.price for f in _eligible_flights(state)) * state.travelers
        remaining = budget_usd - cheapest - activity_reserve(state)
        if remaining > 0:
            cap = round(remaining / (trip_nights(state) * rooms_needed(state.travelers)), 2)
        else:
            acc.notice("warning", "budget_very_low", "The cheapest suitable flight already uses most of the budget.")
    res = acc.call("search_hotels", {
        "destination": state.destination, "check_in": state.departure_date.isoformat(),
        "check_out": state.return_date.isoformat(), "guests": state.travelers, "budget": cap,
    })
    return acc.update(hotel_budget_cap=cap, __status__="success" if res.status == "success" else "failure",
                      __message__=(res.error.message if res.error else f"{len(res.data['hotels'])} hotels") +
                                  (f" (nightly cap {cap} USD)" if cap else " (no price cap)"))


@graph_node("validate_hotels")
def validate_hotels(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "validate_hotels")
    tool = "search_hotels"
    res = state.last_tool_result[tool]
    if res.status == "error":
        if res.error.retryable and acc.can_retry(tool):
            return _retry(acc, tool, res.error.type, ctx.policy.action_for_error(res.error), res.error.message)
        return _abort(acc, tool, res.error.type, f"Hotel search failed after {acc.attempts.get(tool, 0)} attempt(s): {res.error.message}")

    out = HotelSearchOutput.model_validate(res.data)
    issues: list[ValidationIssue] = []
    if out.status == "partial" and acc.can_retry(tool):
        return _retry(acc, tool, "partial_result", "retry_for_complete_result",
                      f"Hotel search returned {len(out.hotels)} of {out.total_results} results.")

    if not out.hotels:
        if state.hotel_budget_cap is not None and not state.hotel_search_relaxed and acc.can_retry(tool):
            detail = (f"No hotels at or below {state.hotel_budget_cap:.2f} USD per night. The nightly cap is a "
                      f"non-critical constraint, so it was removed and the search repeated. The total is still checked against the budget.")
            acc.notice("info", "hotel_cap_relaxed", detail)
            return _retry(acc, tool, "empty_results", "retry_relaxed_constraints", detail, hotel_search_relaxed=True)
        return _abort(acc, tool, "empty_results", f"No hotels were found in {state.destination} for {state.departure_date} to {state.return_date}.")

    hotels, dupes, conflicts = _split_duplicates(out.hotels, "hotel_id")
    if conflicts:
        detail = "; ".join(
            f"{items[0].name} ({k}) returned as " + " and ".join(
                f"{i.nightly_price:g} {i.currency}/night, {'available' if i.availability else 'unavailable'}" for i in items)
            for k, items in conflicts.items())
        if acc.can_retry(tool):
            return _retry(acc, tool, "contradictory_data", "retry_to_resolve_conflict", detail)
        acc.recover(tool, "contradictory_data", "exclude_conflicting_records", detail)
        acc.notice("warning", "conflicting_hotels", "Conflicting data was returned and not resolved by a retry: " + detail +
                   ". These hotels were excluded rather than choosing one version.")
        acc.unverified.extend(f"{items[0].name}: price and availability conflict" for items in conflicts.values())
        issues.append(ValidationIssue(code="contradictory_data", severity="warning", message=detail))
    if dupes:
        msg = f"Removed {dupes} duplicate hotel record(s)."
        acc.recover(tool, "duplicate_results", "deduplicate", msg)
        acc.notice("info", "duplicates_removed", msg)

    if out.status == "partial":
        unknown = [h for h in hotels if h.availability is None]
        if unknown:
            names = ", ".join(h.name for h in unknown)
            acc.recover(tool, "partial_result", "continue_with_verified_subset",
                        f"Availability not confirmed for: {names}. Continuing with confirmed hotels only.")
            acc.notice("warning", "partial_hotels", f"The hotel service returned a partial result ({len(out.hotels)} of "
                                                    f"{out.total_results}). Availability could not be confirmed for {names}; they were not considered.")
            acc.unverified.extend(f"{h.name}: availability not confirmed" for h in unknown)
            hotels = [h for h in hotels if h.availability is not None]

    # currency contract check
    foreign = sorted({h.currency for h in hotels if h.currency != PRICING_CURRENCY})
    for cur in foreign:
        rate_res = acc.call_with_retry("get_exchange_rate", {"from_currency": cur, "to_currency": PRICING_CURRENCY},
                                       key=f"get_exchange_rate:{cur}:{PRICING_CURRENCY}")
        affected = [h for h in hotels if h.currency == cur]
        if rate_res.status == "success":
            rate = rate_res.data["rate"]
            converted = [h.model_copy(update={"nightly_price": round(h.nightly_price * rate, 2), "currency": PRICING_CURRENCY})
                         if h.currency == cur else h for h in hotels]
            hotels = converted
            msg = f"{len(affected)} hotel price(s) arrived in {cur} instead of {PRICING_CURRENCY}; converted at {rate:.6f}."
            acc.recover(tool, "wrong_currency", "normalise_currency", msg)
            acc.notice("info", "currency_normalised", msg)
            issues.append(ValidationIssue(code="currency_mismatch", severity="info", message=msg))
        else:
            hotels = [h for h in hotels if h.currency != cur]
            acc.recover(tool, "wrong_currency", "exclude_unconvertible", f"Could not convert {cur} prices; those hotels were excluded.")
            acc.notice("warning", "currency_unconvertible", f"Hotel prices in {cur} could not be converted and were excluded.")

    hotels = [h for h in hotels if h.city.lower() == (state.destination or "").lower()]
    if not any(h.availability is True for h in hotels):
        return _abort(acc, tool, "empty_results", f"No hotels in {state.destination} could be confirmed as available for these dates.")
    return acc.update(hotels=hotels, excluded_hotel_ids=list(conflicts), next_action="continue",
                      validation_results={"hotels": ValidationResult(valid=True, issues=issues)},
                      __message__=f"{sum(1 for h in hotels if h.availability)} available hotels")


# --------------------------------------------------------------------------- context tools
@graph_node("get_weather")
def get_weather(state: TravelState, ctx: AgentContext) -> dict:
    if not in_plan(state, "get_weather"):
        return {"__status__": "skipped", "__message__": "not in plan"}
    acc = Acc(state, ctx, "get_weather")
    res = acc.call_with_retry("get_weather", {"destination": state.destination, "date": state.departure_date.isoformat()})
    if res.status != "success":
        acc.unverified.append("Weather for the travel dates")
        acc.notice("warning", "weather_unavailable", f"Weather could not be retrieved: {res.error.message}")
        return acc.update(__status__="failure")
    report = WeatherReport.model_validate(res.data)
    if report.basis == "climate_average":
        acc.notice("info", "weather_is_average", "Weather is a monthly climate average from the data provider, not a forecast.")
    return acc.update(weather=report)


@graph_node("get_destination_info")
def get_destination_info(state: TravelState, ctx: AgentContext) -> dict:
    if not in_plan(state, "get_destination_info"):
        return {"__status__": "skipped", "__message__": "not in plan"}
    acc = Acc(state, ctx, "get_destination_info")
    res = acc.call_with_retry("get_destination_info", {"destination": state.destination})
    if res.status != "success":
        acc.unverified.append("Attractions and destination information")
        acc.notice("warning", "destination_info_unavailable", f"Destination information could not be retrieved: {res.error.message}")
        return acc.update(__status__="failure")
    return acc.update(destination_info=DestinationInfo.model_validate(res.data))


# --------------------------------------------------------------------------- selection
def _flight_score(f: Flight, prefs: Preferences) -> float:
    score = -f.price * 0.25
    if f.stops == 0:
        score += 40
    if f.departure.hour < 6:
        score -= 60
    if prefs.prefer_direct and f.stops:
        score -= 80
    if f.arrival.date() > f.departure.date():
        score -= 30
    return score


@graph_node("select_options")
def select_options(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "select_options")
    prefs = state.preferences
    budget_usd = budget_in_pricing_currency(state)
    nights, rooms, travelers = trip_nights(state), rooms_needed(state.travelers), state.travelers
    reserve = activity_reserve(state)
    reasons: list[str] = []

    flights = _eligible_flights(state)
    if prefs.avoid_early_departure and all(f.departure.hour < prefs.earliest_departure_hour for f in flights):
        acc.notice("warning", "preference_not_met", f"No flight departs after {prefs.earliest_departure_hour:02d}:00; the earliest-departure preference could not be met.")
    if prefs.prefer_direct and all(f.stops for f in flights):
        acc.notice("warning", "preference_not_met", "No direct flight was found.")

    excluded = set(state.excluded_hotel_ids) | set(state.tried_hotel_ids)
    hotels = [h for h in state.hotels if h.availability is True and h.hotel_id not in excluded]
    if prefs.min_hotel_rating:
        rated = [h for h in hotels if h.rating >= prefs.min_hotel_rating]
        if not rated:
            acc.notice("warning", "preference_not_met", f"No available hotel is rated {prefs.min_hotel_rating:g} or higher; the best available was used.")
        hotels = rated or hotels
    if not hotels:
        return _abort(acc, None, "no_selectable_hotel", "No remaining hotel could be selected.")

    combos = []
    for f in flights:
        for h in hotels:
            total = f.price * travelers + h.nightly_price * nights * rooms + reserve
            feasible = budget_usd is None or total <= budget_usd
            score = _flight_score(f, prefs) + (h.rating - 3) * 150 - h.nightly_price * nights * rooms * 0.25
            combos.append((feasible, score, -total, f, h, total))

    cheapest_mode = state.itinerary_repairs > 0
    if cheapest_mode:
        combos.sort(key=lambda c: (c[5], -c[1]))
        best = combos[0]
        reasons.append("The previous plan was over budget, so the lowest-cost combination was selected.")
    else:
        combos.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        best = combos[0]
    feasible, _, _, f, h, total = best
    if not feasible:
        acc.notice("warning", "over_budget", f"No combination fits the budget of {money(budget_usd)}; the closest option costs about {money(total)} including the activity allowance.")

    eligible_note = "after your earliest preferred departure time" if prefs.avoid_early_departure else ""
    reasons.append(f"{f.airline} {f.flight_number} departs {f.departure:%H:%M} and arrives {f.arrival:%H:%M}, "
                   f"{'direct' if f.stops == 0 else str(f.stops) + ' stop'}, {money(f.price)} per person"
                   f"{' and leaves ' + eligible_note if eligible_note else ''}.")
    rating_note = f" and meets your {prefs.min_hotel_rating:g}+ rating preference" if prefs.min_hotel_rating else ""
    reasons.append(f"{h.name} in {h.location} is rated {h.rating:g} at {money(h.nightly_price)} per night{rating_note}.")
    if budget_usd:
        reasons.append(f"Flights, hotel and an activity allowance of {money(reserve)} come to about {money(total)} against a budget of {money(budget_usd)}.")

    ranking = [c[4].hotel_id for c in sorted([c for c in combos if c[3].flight_id == f.flight_id],
                                              key=lambda c: (c[0], c[1]), reverse=True)]
    return acc.update(selected_flight=f, selected_hotel=h, hotel_ranking=ranking, selection_reasons=reasons,
                      __message__=f"{f.flight_id} + {h.hotel_id}")


# --------------------------------------------------------------------------- itinerary
@graph_node("create_itinerary")
def create_itinerary(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "create_itinerary")
    if state.next_action == "abort":
        return {"__status__": "skipped"}
    attractions = [a.model_dump(mode="json") for a in state.destination_info.attractions] if state.destination_info else []
    cycle = state.itinerary_repairs + state.booking_alternatives_used
    res = acc.call_with_retry("create_itinerary", {
        "destination": state.destination, "start_date": state.departure_date.isoformat(),
        "end_date": state.return_date.isoformat(), "travelers": state.travelers, "currency": PRICING_CURRENCY,
        "selected_flight": state.selected_flight.model_dump(mode="json"),
        "selected_hotel": state.selected_hotel.model_dump(mode="json"),
        "user_preferences": state.preferences.model_dump(mode="json"),
        "attractions": attractions,
        "weather": state.weather.model_dump(mode="json") if state.weather else None,
    }, key=f"create_itinerary#{cycle}")
    if res.status != "success":
        return _abort(acc, "create_itinerary", res.error.type, f"The itinerary could not be built: {res.error.message}")
    return acc.update(itinerary=Itinerary.model_validate(res.data["itinerary"]), next_action="continue")


@graph_node("validate_itinerary")
def validate_itinerary(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "validate_itinerary")
    res = acc.call_with_retry("validate_itinerary", {
        "itinerary": state.itinerary.model_dump(mode="json"), "budget": budget_in_pricing_currency(state),
        "currency": PRICING_CURRENCY, "departure_date": state.departure_date.isoformat(),
        "return_date": state.return_date.isoformat(),
        "selected_flight": state.selected_flight.model_dump(mode="json"),
        "selected_hotel": state.selected_hotel.model_dump(mode="json"),
    }, key=f"validate_itinerary#{state.itinerary_repairs + state.booking_alternatives_used}")
    if res.status != "success":
        acc.unverified.append("Itinerary validation (the validator could not run)")
        acc.notice("warning", "validation_unavailable", f"The itinerary could not be validated: {res.error.message}")
        return acc.update(next_action="finish", __status__="failure",
                          validation_results={"itinerary": ValidationResult(valid=False, issues=[ValidationIssue(
                              code="validator_unavailable", severity="error", message=res.error.message)])})
    result = ValidationResult(valid=res.data["valid"], issues=[ValidationIssue(**i) for i in res.data["issues"]])
    codes = {i.code for i in result.issues if i.severity == "error"}
    if not result.valid and "budget_exceeded" in codes and state.itinerary_repairs < ctx.settings.max_itinerary_repairs:
        acc.recover("validate_itinerary", "budget_exceeded", "repair_select_cheaper_options",
                    "Itinerary is over budget; selecting the lowest-cost flight and hotel combination.")
        return acc.update(validation_results={"itinerary": result}, next_action="repair",
                          itinerary_repairs=state.itinerary_repairs + 1, __status__="retrying")
    for issue in result.issues:
        if issue.severity in ("error", "warning"):
            acc.notice("warning", f"itinerary_{issue.code}", issue.message, source="validate_itinerary")
    if not result.valid:
        return acc.update(validation_results={"itinerary": result}, next_action="finish", __status__="failure",
                          __message__="invalid: " + ", ".join(sorted(codes)))
    nxt = "book" if state.simulate_booking else "finish"
    return acc.update(validation_results={"itinerary": result}, next_action=nxt, __message__="valid")


# --------------------------------------------------------------------------- sandbox booking
@graph_node("book_trip")
def book_trip(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "book_trip")
    bookings: dict[str, Any] = {}
    if "flight" not in state.bookings:
        res = acc.call_with_retry("book_flight", {"flight_id": state.selected_flight.flight_id, "passengers": state.travelers})
        bookings["flight"] = res.data if res.status == "success" else {"status": "FAILED", "message": res.error.message}
    h = state.selected_hotel
    res = acc.call_with_retry("reserve_hotel", {"hotel_id": h.hotel_id, "check_in": state.departure_date.isoformat(),
                                                "check_out": state.return_date.isoformat(), "guests": state.travelers})
    if res.status == "success" and res.data["status"] == "CONFIRMED":
        bookings["hotel"] = res.data | {"hotel_id": h.hotel_id, "hotel_name": h.name}
        return acc.update(bookings=bookings, next_action="finish")
    reason = res.data["message"] if res.status == "success" else res.error.message
    tried = state.tried_hotel_ids + [h.hotel_id]
    by_id = {x.hotel_id: x for x in state.hotels}
    alternative = next((by_id[i] for i in state.hotel_ranking if i not in tried and i in by_id), None)
    if alternative and state.booking_alternatives_used < ctx.settings.max_booking_alternatives:
        detail = f"{h.name} is unavailable ({reason}). Switching to {alternative.name}, then rebuilding and revalidating the itinerary."
        acc.recover("reserve_hotel", "booking_unavailable", "retry_with_alternative_hotel", detail)
        acc.notice("info", "hotel_switched", f"{h.name} could not be reserved in the sandbox, so {alternative.name} was selected instead.")
        reasons = [r for r in state.selection_reasons if h.name not in r and "activity allowance" not in r]
        reasons.append(f"{alternative.name} in {alternative.location} (rated {alternative.rating:g}, {money(alternative.nightly_price)} "
                       f"per night) replaced {h.name}, which could not be reserved. The itinerary was rebuilt and revalidated.")
        return acc.update(bookings=bookings, selected_hotel=alternative, tried_hotel_ids=tried, selection_reasons=reasons,
                          booking_alternatives_used=state.booking_alternatives_used + 1,
                          next_action="rebuild", __status__="retrying", __message__=detail)
    acc.recover("reserve_hotel", "booking_unavailable", "report_failure", f"No alternative hotel available: {reason}")
    acc.notice("warning", "booking_failed", f"The hotel could not be reserved in the sandbox: {reason}")
    bookings["hotel"] = {"status": "UNAVAILABLE", "message": reason, "environment": "sandbox"}
    return acc.update(bookings=bookings, next_action="finish", __status__="failure")


# --------------------------------------------------------------------------- final
def determine_outcome(state: TravelState) -> str:
    if state.itinerary is None:
        return "graceful_failure"
    it_result = state.validation_results.get("itinerary")
    warnings = [n for n in state.notices if n.level in ("warning", "error")]
    hotel_booking = state.bookings.get("hotel")
    if not it_result or not it_result.valid or warnings or state.unverified or \
            (state.simulate_booking and (not hotel_booking or hotel_booking.get("status") != "CONFIRMED")):
        return "completed_with_warnings"
    if state.recovery_actions:
        return "recovered"
    return "completed"


@graph_node("final_response")
def final_response(state: TravelState, ctx: AgentContext) -> dict:
    acc = Acc(state, ctx, "final_response")
    outcome = determine_outcome(state)
    route = f"{state.origin} to {state.destination}"
    facts: dict[str, Any] = {"route": route, "outcome": "no_itinerary" if state.itinerary is None else "itinerary",
                             "travelers": state.travelers}
    if state.departure_date and state.return_date:
        facts["dates"] = f"{state.departure_date:%d %b %Y} to {state.return_date:%d %b %Y}"
        facts["days"] = (state.return_date - state.departure_date).days + 1
    if state.itinerary is None:
        facts["blocking_problems"] = [state.abort_reason or "The plan could not be completed."]
        if state.flights:
            facts["blocking_problems"].append(f"{len(state.flights)} flight option(s) were found, but without a hotel a full plan cannot be built.")
    else:
        f, h, it = state.selected_flight, state.selected_hotel, state.itinerary
        facts["flight"] = f"{f.airline} {f.flight_number}, departs {f.departure:%d %b %H:%M}, returns on {f.return_flight_number} at {f.return_departure:%d %b %H:%M}, {money(f.price)} per person"
        facts["hotel"] = f"{h.name} ({h.location}, rated {h.rating:g}), {money(h.nightly_price)} per night"
        cost = money(it.total_estimated_cost)
        if state.currency != PRICING_CURRENCY and state.budget_rate:
            cost += f" (about {money(it.total_estimated_cost * state.budget_rate, state.currency)})"
        facts["cost"] = cost
        facts["cost_breakdown"] = it.cost_breakdown
        facts["selection_reasons"] = state.selection_reasons
        if state.weather:
            facts["weather"] = f"{state.weather.condition}, around {state.weather.temperature:g}°C ({state.weather.basis.replace('_', ' ')})"
        facts["itinerary_valid"] = state.validation_results.get("itinerary").valid if state.validation_results.get("itinerary") else False
    facts["warnings"] = [n.message for n in state.notices if n.level in ("warning", "error")]
    facts["unverified"] = state.unverified
    facts["bookings"] = state.bookings

    try:
        text = ctx.llm_text(node="final_response", task="final_response", system=FINAL_SYSTEM,
                            prompt="Facts:\n" + json.dumps(facts, default=str, indent=2), context={"facts": facts})
    except LLMError as exc:
        acc.notice("warning", "llm_response_failed", f"The response model failed ({exc}); a template summary was used.")
        from app.llm.stub_provider import StubLLMProvider
        text = StubLLMProvider().generate_text(task="final_response", system="", prompt="", context={"facts": facts})

    # Deterministic appendix so verification gaps are never lost in the model's wording.
    appendix = []
    if state.bookings:
        parts = [f"{k}: {v.get('status')} ({v.get('booking_id') or 'no booking ID'})" for k, v in state.bookings.items()]
        appendix.append("Sandbox bookings (simulated, no real transaction): " + "; ".join(parts) + ".")
    gaps = list(dict.fromkeys(state.unverified))
    if gaps:
        appendix.append("Could not be verified: " + "; ".join(gaps) + ".")
    appendix.append("Prices and schedules come from the configured travel data providers (mock data by default).")
    return acc.update(final_response=text.strip() + "\n\n" + "\n".join(appendix), status=outcome,
                      __message__=outcome)
