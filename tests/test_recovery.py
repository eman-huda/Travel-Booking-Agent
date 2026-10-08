"""Recovery behaviour through the full agent graph."""
import pytest

from app.config import Settings
from app.agent.runner import AgentRunner
from app.failures.registry import list_scenarios
from app.schemas.run import RunRequest
from tests.conftest import DEMO_REQUEST


def run(runner, mode, **kw):
    return runner.run(RunRequest(request_text=DEMO_REQUEST, failure_mode=mode, **kw))


def tool_attempts(record, tool):
    return [e for e in record.tool_trace if e["tool_name"] == tool]


def test_retry_behaviour_hotel_timeout(runner):
    rec = run(runner, "hotel_timeout")
    calls = tool_attempts(rec, "search_hotels")
    assert [c["status"] for c in calls] == ["failure", "success"]
    assert [c["retry_number"] for c in calls] == [0, 1]
    assert rec.status == "recovered" and rec.metrics.retries == 1
    assert rec.final_state["itinerary"] is not None


def test_maximum_retry_limit_is_enforced(runner):
    rec = run(runner, "api_error")  # persistent 503
    calls = tool_attempts(rec, "search_hotels")
    assert len(calls) == runner.settings.max_tool_retries + 1
    assert rec.status == "graceful_failure"
    assert rec.final_state["itinerary"] is None
    assert "Hotel search failed" in rec.final_response


def test_retry_limit_zero_means_no_retry(tmp_path):
    s = Settings(_env_file=None, llm_provider="stub", max_tool_retries=0, database_path=tmp_path / "r.db",
                 injected_timeout_delay_seconds=0, log_level="WARNING")
    rec = AgentRunner(s).run(RunRequest(request_text=DEMO_REQUEST, failure_mode="hotel_timeout"))
    assert len(tool_attempts(rec, "search_hotels")) == 1 and rec.status == "graceful_failure"


def test_empty_flights_not_hallucinated(runner):
    rec = run(runner, "empty_flights")
    assert rec.status == "graceful_failure"
    assert rec.final_state["selected_flight"] is None and rec.final_state["flights"] == []
    assert "No flights were found" in rec.final_response


def test_empty_hotels_relaxes_noncritical_constraint(runner):
    rec = run(runner, "empty_hotels")
    calls = tool_attempts(rec, "search_hotels")
    assert calls[0]["arguments"]["budget"] is not None and calls[1]["arguments"]["budget"] is None
    assert rec.status == "recovered"


def test_contradictory_data_is_flagged_not_silently_resolved(runner):
    rec = run(runner, "contradictory_data")
    st = rec.final_state
    assert st["excluded_hotel_ids"], "conflicting hotels must be excluded"
    assert st["selected_hotel"]["hotel_id"] not in st["excluded_hotel_ids"]
    assert any("conflict" in u for u in st["unverified"])
    assert rec.status == "completed_with_warnings"


def test_partial_result_uses_only_verified_hotels(runner):
    rec = run(runner, "partial_result")
    assert rec.final_state["selected_hotel"]["availability"] is True
    assert any("availability not confirmed" in u for u in rec.final_state["unverified"])


def test_wrong_currency_detected_and_normalised(runner):
    rec = run(runner, "wrong_currency")
    assert any(a["issue"] == "wrong_currency" for a in rec.recovery.actions)
    assert all(h["currency"] == "USD" for h in rec.final_state["hotels"])
    assert any(e["tool_name"] == "get_exchange_rate" and e["arguments"]["from_currency"] == "AED" for e in rec.tool_trace)


def test_stale_data_triggers_fresh_fetch(runner):
    rec = run(runner, "stale_data")
    assert [a["action"] for a in rec.recovery.actions] == ["retry_for_fresh_data"]
    assert rec.status == "recovered"


def test_invalid_argument_rebuilt_and_retried(runner):
    rec = run(runner, "invalid_argument")
    calls = tool_attempts(rec, "search_flights")
    assert calls[0]["status"] == "failure" and "Invalid arguments" in calls[0]["error"]
    assert calls[1]["status"] == "success" and calls[1]["arguments"]["passengers"] == 1


def test_booking_unavailable_selects_alternative(runner):
    rec = run(runner, "booking_unavailable")
    st = rec.final_state
    assert st["bookings"]["hotel"]["status"] == "CONFIRMED"
    assert st["tried_hotel_ids"] and st["selected_hotel"]["hotel_id"] not in st["tried_hotel_ids"]
    assert st["validation_results"]["itinerary"]["valid"] is True
    assert rec.status == "recovered"


@pytest.mark.parametrize("scenario", list_scenarios(), ids=lambda s: s.key)
def test_every_scenario_meets_expected_outcome(runner, scenario):
    rec = run(runner, scenario.key)
    assert rec.status == scenario.expected_outcome, rec.final_response
    assert rec.expectation.matches
    assert rec.metrics.retries <= runner.settings.max_total_retries
