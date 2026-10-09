"""Deterministic itinerary builder and validator used by the create/validate tools."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from app.schemas.tools import CreateItineraryInput, ValidateItineraryInput
from app.schemas.travel import Attraction, Itinerary, ItineraryDay, ItineraryItem, ValidationIssue

ACTIVITY_CAP = {"low": 30.0, "medium": 80.0, "high": 10_000.0}


def rooms_needed(travelers: int) -> int:
    return max(1, math.ceil(travelers / 2))


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _t(hour: int, minute: int = 0) -> str:
    return f"{hour:02d}:{minute:02d}"


def build_itinerary(inp: CreateItineraryInput) -> Itinerary:
    f, h, prefs = inp.selected_flight, inp.selected_hotel, inp.user_preferences
    n_days = (inp.end_date - inp.start_date).days + 1
    nights = max(1, (inp.end_date - inp.start_date).days)
    cap = ACTIVITY_CAP[prefs.activity_budget]
    hot = inp.weather is not None and inp.weather.temperature >= 35

    # Known prices must fit the activity budget. Unknown prices are kept (the source did not say) but are
    # scheduled after free and known-cheap options, and the itinerary notes that the price is unknown.
    ranked = [(i, a) for i, a in enumerate(inp.attractions) if a.estimated_cost is None or a.estimated_cost <= cap]
    pool = [a for _, a in sorted(ranked, key=lambda x: (
        0 if x[1].estimated_cost == 0 else 1 if x[1].estimated_cost is not None else 2,
        x[1].estimated_cost or 0.0, x[0]))]
    used: set[str] = set()

    def pick(slot: str, prefer_indoor: bool = False) -> Attraction | None:
        candidates = [a for a in pool if a.attraction_id not in used and a.best_time in (slot, "any")]
        if not candidates:
            candidates = [a for a in pool if a.attraction_id not in used and a.best_time != "evening" and slot != "evening"]
        if prefer_indoor:
            candidates.sort(key=lambda a: (not a.indoor, a.estimated_cost, a.attraction_id))
        if not candidates:
            return None
        chosen = candidates[0]
        used.add(chosen.attraction_id)
        return chosen

    def attraction_item(time_str: str, a: Attraction | None, label: str) -> ItineraryItem:
        if a is None:
            return ItineraryItem(time=time_str, activity=f"Free time ({label})", kind="free")
        if a.estimated_cost is None and a.price_note:
            cost_note = f"price per {a.source or 'source'}: {a.price_note}"
        elif a.estimated_cost is None:
            cost_note = "entry price not available from the data source"
        elif a.estimated_cost:
            cost_note = f"sample cost {a.estimated_cost:g} {a.currency} per person"
        else:
            cost_note = "free entry in the sample data"
        return ItineraryItem(time=time_str, activity=a.name, kind="attraction", attraction_id=a.attraction_id,
                             estimated_cost=(a.estimated_cost or 0.0) * inp.travelers,
                             notes=f"About {a.duration_hours:g}h; {cost_note}")

    weather_note = f"{inp.weather.condition}, around {inp.weather.temperature:g}°C ({inp.weather.basis.replace('_', ' ')})" if inp.weather else None
    days: list[ItineraryDay] = []
    for i in range(n_days):
        d = inp.start_date + timedelta(days=i)
        items: list[ItineraryItem] = []
        if i == 0:
            items.append(ItineraryItem(time=_hm(f.departure), activity=f"Depart {f.origin} on {f.airline} {f.flight_number}",
                                       kind="flight", notes=f"{f.duration}, {'direct' if f.stops == 0 else f'{f.stops} stop'}"))
            arr = f.arrival
            items.append(ItineraryItem(time=_hm(arr), activity=f"Arrive in {f.destination}", kind="flight"))
            transfer = arr + timedelta(minutes=45)
            items.append(ItineraryItem(time=_hm(transfer), activity=f"Transfer to {h.name}", kind="transfer"))
            check_in = max(transfer + timedelta(minutes=60), datetime.combine(d, datetime.min.time()).replace(hour=14))
            items.append(ItineraryItem(time=_hm(check_in), activity=f"Check in at {h.name}", kind="hotel"))
            if check_in.hour <= 17:
                items.append(attraction_item(_t(18, 30), pick("evening"), "evening"))
            items.append(ItineraryItem(time=_t(20, 30), activity="Dinner near the hotel", kind="meal"))
            title = "Arrival and check-in"
        elif i == n_days - 1 and f.return_departure is None:
            items.append(ItineraryItem(time=_t(8), activity="Breakfast", kind="meal"))
            items.append(ItineraryItem(time=_t(11), activity=f"Check out of {h.name}", kind="hotel"))
            items.append(ItineraryItem(time=_t(11, 30), activity="Return flight (schedule not confirmed)", kind="flight",
                                       notes="The return flight time could not be retrieved; confirm it before travelling."))
            title = "Check-out and departure"
        elif i == n_days - 1:
            rdep = f.return_departure
            checkout = min(datetime.combine(d, datetime.min.time()).replace(hour=11), rdep - timedelta(hours=4))
            if rdep.hour >= 16:
                items.append(ItineraryItem(time=_t(8), activity="Breakfast", kind="meal"))
                items.append(attraction_item(_t(9, 0), pick("morning"), "morning"))
                checkout = max(checkout, datetime.combine(d, datetime.min.time()).replace(hour=11))
            items.append(ItineraryItem(time=_hm(checkout), activity=f"Check out of {h.name}", kind="hotel"))
            items.append(ItineraryItem(time=_hm(rdep - timedelta(hours=3)), activity="Transfer to the airport", kind="transfer"))
            items.append(ItineraryItem(time=_hm(rdep), activity=f"Return flight {f.airline} {f.return_flight_number}", kind="flight"))
            items.sort(key=lambda it: it.time)
            title = "Check-out and departure"
        else:
            items.append(ItineraryItem(time=_t(8), activity="Breakfast", kind="meal"))
            morning = pick("morning")
            items.append(attraction_item(_t(9, 30), morning, "morning"))
            items.append(ItineraryItem(time=_t(13), activity="Lunch", kind="meal"))
            afternoon = pick("afternoon", prefer_indoor=hot)
            items.append(attraction_item(_t(14, 30), afternoon, "afternoon"))
            evening = pick("evening")
            items.append(attraction_item(_t(18, 30), evening, "evening"))
            items.append(ItineraryItem(time=_t(20, 30), activity="Dinner", kind="meal"))
            named = [a.name for a in (morning, afternoon) if a]
            title = " and ".join(named[:2]) if named else "Free day"
        days.append(ItineraryDay(day_number=i + 1, date=d, title=title, items=items, weather=weather_note))

    rooms = rooms_needed(inp.travelers)
    flights_cost = round(f.price * inp.travelers, 2)
    hotel_cost = round(h.nightly_price * nights * rooms, 2)
    activities_cost = round(sum(it.estimated_cost for day in days for it in day.items), 2)
    breakdown = {"flights": flights_cost, "hotel": hotel_cost, "activities": activities_cost}
    assumptions = [f"Hotel cost assumes {rooms} room(s) for {nights} night(s).",
                   "Meals and local transport are not included in the estimate."]
    if any(a.estimated_cost is None for a in inp.attractions):
        assumptions.append("Attraction prices are shown as published by the source where available; they are not "
                           "converted or included in the total.")
    else:
        assumptions.append("Attraction costs are sample estimates from the destination data provider.")
    return Itinerary(
        destination=inp.destination, start_date=inp.start_date, end_date=inp.end_date, travelers=inp.travelers,
        currency=inp.currency, days=days, cost_breakdown=breakdown,
        total_estimated_cost=round(sum(breakdown.values()), 2),
        assumptions=assumptions,
    )


def validate(inp: ValidateItineraryInput) -> list[ValidationIssue]:
    it, f, h = inp.itinerary, inp.selected_flight, inp.selected_hotel
    issues: list[ValidationIssue] = []

    def add(code, sev, msg):
        issues.append(ValidationIssue(code=code, severity=sev, message=msg))

    expected_days = (inp.return_date - inp.departure_date).days + 1
    if it.start_date != inp.departure_date or it.end_date != inp.return_date:
        add("date_range_mismatch", "error", f"Itinerary runs {it.start_date} to {it.end_date} but the trip is {inp.departure_date} to {inp.return_date}.")
    if len(it.days) != expected_days:
        add("day_count_mismatch", "error", f"Expected {expected_days} days, found {len(it.days)}.")
    expected_dates = [inp.departure_date + timedelta(days=i) for i in range(expected_days)]
    actual_dates = [d.date for d in it.days]
    missing = [d for d in expected_dates if d not in actual_dates]
    if missing:
        add("missing_days", "error", "Missing days: " + ", ".join(str(d) for d in missing))
    if actual_dates != sorted(actual_dates) or len(set(actual_dates)) != len(actual_dates):
        add("day_order", "error", "Days are out of order or repeated.")
    for i, d in enumerate(it.days, start=1):
        if d.day_number != i:
            add("day_numbering", "warning", f"Day {d.date} is numbered {d.day_number}, expected {i}.")
        times = [x.time for x in d.items]
        if times != sorted(times):
            add("impossible_ordering", "error", f"Day {d.day_number} has activities out of time order.")

    # flights
    if f.departure.date() != inp.departure_date:
        add("flight_date_mismatch", "error", f"Outbound flight departs {f.departure.date()}, trip starts {inp.departure_date}.")
    if f.return_departure is None:
        add("return_flight_unconfirmed", "warning", "The return flight schedule has not been confirmed.")
    elif f.return_departure.date() != inp.return_date:
        add("return_flight_date_mismatch", "error", f"Return flight departs {f.return_departure.date()}, trip ends {inp.return_date}.")
    if it.days and not any(x.kind == "flight" for x in it.days[0].items):
        add("missing_outbound_flight", "error", "Day 1 does not include the outbound flight.")
    if it.days and not any(x.kind == "flight" for x in it.days[-1].items):
        add("missing_return_flight", "error", "The last day does not include the return flight.")

    # hotel
    if h.availability is not True:
        add("hotel_unavailable", "error", f"{h.name} is not confirmed as available.")
    if h.city.lower() != it.destination.lower():
        add("hotel_wrong_city", "error", f"{h.name} is in {h.city}, not {it.destination}.")
    if it.days and not any(x.kind == "hotel" and "Check in" in x.activity for x in it.days[0].items):
        add("missing_check_in", "error", "Hotel check-in is not on day 1.")
    if it.days and not any(x.kind == "hotel" and "Check out" in x.activity for x in it.days[-1].items):
        add("missing_check_out", "error", "Hotel check-out is not on the last day.")

    # duplicates
    seen: dict[str, int] = {}
    for d in it.days:
        for x in d.items:
            if x.attraction_id:
                if x.attraction_id in seen:
                    add("duplicate_activity", "error", f"'{x.activity}' appears on day {seen[x.attraction_id]} and day {d.day_number}.")
                seen.setdefault(x.attraction_id, d.day_number)

    # currency and budget
    for label, cur in (("itinerary", it.currency), ("flight", f.currency), ("hotel", h.currency)):
        if cur != inp.currency:
            add("currency_mismatch", "error", f"The {label} is priced in {cur}; expected {inp.currency}.")
    recomputed = round(sum(it.cost_breakdown.values()), 2)
    if abs(recomputed - it.total_estimated_cost) > 0.5:
        add("inconsistent_total", "error", f"Total {it.total_estimated_cost} does not equal the sum of its parts ({recomputed}).")
    if inp.budget is not None:
        if it.total_estimated_cost > inp.budget:
            add("budget_exceeded", "error", f"Estimated cost {it.total_estimated_cost:.2f} {inp.currency} exceeds the budget of {inp.budget:.2f} {inp.currency}.")
        elif it.total_estimated_cost > inp.budget * 0.9:
            add("budget_tight", "warning", f"Estimated cost uses more than 90% of the budget.")
    return issues
