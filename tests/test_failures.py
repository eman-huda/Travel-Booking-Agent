"""Failure injection: deterministic, reproducible, invisible to the agent."""
import pytest

from app.failures.registry import get_scenario, list_scenarios
from tests.conftest import flight_args, hotel_args, make_executor


def test_none_mode_never_fails(settings):
    ex, _, _ = make_executor(settings, "none")
    for _ in range(3):
        assert ex.execute("search_flights", flight_args(), node="t").status == "success"
        assert ex.execute("search_hotels", hotel_args(), node="t").status == "success"


def test_malformed_output_detected_by_schema_validation(settings):
    ex, _, tracer = make_executor(settings, "malformed_flights")
    res = ex.execute("search_flights", flight_args(), node="t", attempt=0)
    assert res.status == "error" and res.error.type == "schema_validation" and res.error.retryable
    assert tracer.tool_events()[-1].injected_failure["kind"] == "malformed"
    # transient: the retry returns valid data
    assert ex.execute("search_flights", flight_args(), node="t", attempt=1).status == "success"


def test_timeout_handling(settings):
    ex, _, _ = make_executor(settings, "hotel_timeout")
    res = ex.execute("search_hotels", hotel_args(), node="t", attempt=0)
    assert res.status == "error" and res.error.type == "timeout" and res.error.retryable
    assert ex.execute("search_hotels", hotel_args(), node="t", attempt=1).status == "success"


def test_empty_results(settings):
    ex, _, _ = make_executor(settings, "empty_flights")
    for attempt in range(3):  # persistent
        res = ex.execute("search_flights", flight_args(), node="t", attempt=attempt)
        assert res.status == "success" and res.data["flights"] == []


def test_injection_is_invisible_to_agent(settings):
    for sc in list_scenarios():
        if sc.target_tool not in ("search_flights", "search_hotels"):
            continue
        ex, _, _ = make_executor(settings, sc.key)
        args = flight_args() if sc.target_tool == "search_flights" else hotel_args()
        res = ex.execute(sc.target_tool, args, node="t", attempt=0)
        visible = res.model_dump_json().lower()
        assert "inject" not in visible and "scenario" not in visible and sc.scenario_id.lower() not in visible


@pytest.mark.parametrize("mode", [s.key for s in list_scenarios()])
def test_failures_are_deterministic(settings, mode):
    sc = get_scenario(mode)
    if not sc.target_tool or sc.target_tool not in ("search_flights", "search_hotels"):
        return
    args = flight_args() if sc.target_tool == "search_flights" else hotel_args()
    outcomes = []
    for _ in range(2):
        ex, _, _ = make_executor(settings, mode)
        r = ex.execute(sc.target_tool, args, node="t", attempt=0)
        data = r.data or {}
        data.pop("retrieved_at", None)
        outcomes.append((r.status, r.error.type if r.error else None, str(data)))
    assert outcomes[0] == outcomes[1]


def test_unknown_failure_mode_rejected():
    with pytest.raises(ValueError):
        get_scenario("random_chaos")
