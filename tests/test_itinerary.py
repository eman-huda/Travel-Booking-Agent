"""Itinerary validation catches dates, budget, ordering, duplicates and inconsistencies."""
from app.schemas.tools import CreateItineraryInput, FlightSearchOutput, HotelSearchOutput, ValidateItineraryInput
from app.schemas.travel import Attraction, Preferences
from app.tools.itinerary import build_itinerary, validate
from tests.conftest import DEPART, RETURN, flight_args, hotel_args


def _inputs(executor):
    f = FlightSearchOutput.model_validate(executor.execute("search_flights", flight_args(), node="t").data).flights[2]
    h = HotelSearchOutput.model_validate(executor.execute("search_hotels", hotel_args(), node="t").data).hotels[1]
    attractions = [Attraction.model_validate(a) for a in
                   executor.execute("get_destination_info", {"destination": "Dubai"}, node="t").data["attractions"]]
    it = build_itinerary(CreateItineraryInput(destination="Dubai", start_date=DEPART, end_date=RETURN, travelers=1,
                                              selected_flight=f, selected_hotel=h, user_preferences=Preferences(activity_budget="low"),
                                              attractions=attractions))
    return f, h, it


def _check(it, f, h, budget=1500.0):
    return {i.code for i in validate(ValidateItineraryInput(itinerary=it, budget=budget, departure_date=DEPART,
                                                            return_date=RETURN, selected_flight=f, selected_hotel=h))
            if i.severity == "error"}


def test_valid_itinerary_passes(executor):
    f, h, it = _inputs(executor)
    assert len(it.days) == 5 and _check(it, f, h) == set()


def test_missing_day_detected(executor):
    f, h, it = _inputs(executor)
    it.days.pop(2)
    assert {"day_count_mismatch", "missing_days"} <= _check(it, f, h)


def test_duplicate_activity_detected(executor):
    f, h, it = _inputs(executor)
    dup = next(x for x in it.days[1].items if x.attraction_id)
    it.days[2].items.append(dup.model_copy(update={"time": "21:30"}))
    assert "duplicate_activity" in _check(it, f, h)


def test_impossible_ordering_detected(executor):
    f, h, it = _inputs(executor)
    it.days[1].items.reverse()
    assert "impossible_ordering" in _check(it, f, h)


def test_budget_exceeded_detected(executor):
    f, h, it = _inputs(executor)
    assert "budget_exceeded" in _check(it, f, h, budget=300.0)


def test_flight_and_hotel_inconsistencies_detected(executor):
    f, h, it = _inputs(executor)
    wrong_flight = f.model_copy(update={"departure": f.departure.replace(day=1) if f.departure.day != 1 else f.departure.replace(day=2)})
    unavailable = h.model_copy(update={"availability": False})
    other_currency = h.model_copy(update={"currency": "AED"})
    assert "flight_date_mismatch" in _check(it, wrong_flight, h)
    assert "hotel_unavailable" in _check(it, f, unavailable)
    assert "currency_mismatch" in _check(it, f, other_currency)
