"""Normal tool behaviour, schema enforcement and the allowlist."""
from app.schemas.tools import FlightSearchOutput, HotelSearchOutput
from tests.conftest import DEPART, flight_args, hotel_args


def test_normal_flight_search(executor):
    res = executor.execute("search_flights", flight_args(), node="test")
    assert res.status == "success"
    out = FlightSearchOutput.model_validate(res.data)
    assert len(out.flights) == 6
    f = out.flights[0]
    for field in ("flight_id", "airline", "origin", "destination", "departure", "arrival", "duration", "price", "currency", "stops"):
        assert getattr(f, field) is not None
    assert all(fl.departure.date() == DEPART for fl in out.flights)


def test_normal_hotel_search_respects_budget_cap(executor):
    res = executor.execute("search_hotels", hotel_args(budget=150), node="test")
    assert res.status == "success"
    out = HotelSearchOutput.model_validate(res.data)
    assert out.hotels and all(h.nightly_price <= 150 for h in out.hotels)
    for field in ("hotel_id", "name", "location", "rating", "nightly_price", "currency", "amenities", "availability"):
        assert hasattr(out.hotels[0], field)


def test_weather_exchange_and_destination(executor):
    w = executor.execute("get_weather", {"destination": "Dubai", "date": DEPART.isoformat()}, node="test")
    x = executor.execute("get_exchange_rate", {"from_currency": "USD", "to_currency": "AED"}, node="test")
    d = executor.execute("get_destination_info", {"destination": "Dubai"}, node="test")
    assert w.status == x.status == d.status == "success"
    assert x.data["from"] == "USD" and x.data["to"] == "AED" and abs(x.data["rate"] - 3.6725) < 1e-6
    assert d.data["attractions"] and d.data["transport"]


def test_unknown_route_returns_empty_not_invented(executor):
    args = flight_args() | {"origin": "Paris", "destination": "Tokyo"}
    res = executor.execute("search_flights", args, node="test")
    assert res.status == "success" and res.data["flights"] == []


def test_invalid_arguments_rejected_by_schema(executor):
    res = executor.execute("search_flights", flight_args() | {"passengers": 0}, node="test")
    assert res.status == "error" and res.error.type == "invalid_argument"
    res = executor.execute("search_flights", flight_args() | {"cabin_class": "first"}, node="test")
    assert res.status == "error" and res.error.type == "invalid_argument"  # unknown args are forbidden


def test_unregistered_tool_is_rejected(executor):
    res = executor.execute("run_shell", {"cmd": "rm -rf /"}, node="test")
    assert res.status == "error" and res.error.type == "unknown_tool" and not res.error.retryable


def test_every_tool_call_is_traced(settings):
    from tests.conftest import make_executor
    ex, _, tracer = make_executor(settings)
    ex.execute("search_flights", flight_args(), node="n1")
    ex.execute("search_hotels", hotel_args(), node="n2")
    events = tracer.tool_events()
    assert [e.tool_name for e in events] == ["search_flights", "search_hotels"]
    assert all(e.run_id == "test-run" and e.duration_ms is not None and e.status == "success" for e in events)
