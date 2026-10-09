"""End-to-end runs through the LangGraph agent."""
from app.schemas.run import RequestOverrides, RunRequest
from tests.conftest import DEMO_REQUEST, DEPART

EXPECTED_ORDER = ["get_exchange_rate", "search_flights", "search_hotels", "get_weather",
                  "get_destination_info", "create_itinerary", "validate_itinerary", "book_flight", "reserve_hotel"]


def test_normal_request_end_to_end(runner):
    rec = runner.run(RunRequest(request_text=DEMO_REQUEST))
    assert rec.status == "completed"
    assert [e["tool_name"] for e in rec.tool_trace] == EXPECTED_ORDER
    st = rec.final_state
    assert st["origin"] == "Islamabad" and st["destination"] == "Dubai" and st["travelers"] == 1
    assert st["budget"] == 1500 and st["preferences"]["min_hotel_rating"] == 4.0
    assert st["selected_hotel"]["rating"] >= 4.0
    assert len(st["itinerary"]["days"]) == 5
    assert st["validation_results"]["itinerary"]["valid"] is True
    assert st["itinerary"]["total_estimated_cost"] <= 1500
    assert "plan for Islamabad to Dubai" in rec.final_response
    assert st["bookings"]["flight"]["booking_id"].startswith("TEST-FLIGHT-")
    assert st["bookings"]["hotel"]["status"] == "CONFIRMED" and st["bookings"]["hotel"]["environment"] == "sandbox"
    nodes = [e["node"] for e in rec.events if e["event_type"] == "node" and e["status"] == "started"]
    assert nodes[0] == "understand_request" and nodes[-1] == "final_response"


def test_hotel_timeout_end_to_end(runner):
    rec = runner.run(RunRequest(request_text=DEMO_REQUEST, failure_mode="hotel_timeout"))
    seq = [(e["tool_name"], e["status"]) for e in rec.tool_trace]
    i = seq.index(("search_hotels", "failure"))
    assert seq[i + 1] == ("search_hotels", "success")
    assert rec.final_state["itinerary"] is not None and rec.status == "recovered"
    assert rec.recovery.result == "success" and rec.recovery.actions[0]["action"] == "retry_after_timeout"
    injected = [e for e in rec.tool_trace if e.get("injected_failure")]
    assert len(injected) == 1 and injected[0]["injected_failure"]["scenario_id"] == "FS-02"


def test_missing_date_asks_for_clarification(runner):
    rec = runner.run(RunRequest(request_text="I want to travel from Islamabad to Dubai for 5 days next month. My budget is $1500."))
    assert rec.status == "needs_clarification"
    assert "departure date" in rec.final_response and "next month" in rec.final_response
    assert rec.metrics.tool_calls == 0  # no tools run on missing information


def test_ui_overrides_take_precedence(runner):
    ov = RequestOverrides(departure_date=DEPART, travelers=2, budget=3000)
    rec = runner.run(RunRequest(request_text="Islamabad to Dubai for 5 days, comfortable hotel", overrides=ov))
    st = rec.final_state
    assert st["travelers"] == 2 and st["budget"] == 3000 and st["departure_date"] == DEPART.isoformat()


def test_runs_are_persisted_and_exportable(runner):
    rec = runner.run(RunRequest(request_text=DEMO_REQUEST, failure_mode="malformed_hotels"))
    loaded = runner.store.get(rec.run_id)
    assert loaded and loaded.status == rec.status
    export = loaded.to_greattest()
    for key in ("run_id", "agent_version", "scenario_id", "failure_mode", "initial_state", "tool_trace",
                "final_state", "final_response", "errors", "recovery", "status"):
        assert key in export
    assert runner.store.list_runs()[0]["run_id"] == rec.run_id
