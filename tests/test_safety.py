"""Sandbox and permission guarantees enforced in code."""
import socket

import httpx
import pytest

from app.safety.permissions import Permission, SafetyViolation, TOOL_POLICY, assert_permitted
from app.safety.redaction import redact, redact_text, sanitise_user_request
from app.schemas.run import RunRequest
from app.schemas.tools import BookFlightInput, SimulatedBookingOutput
from app.tools.base import ToolSpec
from app.tools.registry import ToolRegistry
from tests.conftest import DEMO_REQUEST, make_executor


def test_sandbox_booking_is_labelled_and_simulated(settings):
    ex, services, _ = make_executor(settings)
    res = ex.execute("book_flight", {"flight_id": "ISBDXB-1110-02", "passengers": 1}, node="t")
    assert res.status == "success"
    assert res.data["environment"] == "sandbox" and res.data["booking_id"].startswith("TEST-FLIGHT-")
    assert "No real booking" in res.data["message"]
    assert services.bookings.ledger[-1]["booking_id"] == res.data["booking_id"]


def test_no_real_booking_execution_no_network(settings, monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access attempted during a simulated write")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    ex, _, _ = make_executor(settings)
    assert ex.execute("book_flight", {"flight_id": "X1", "passengers": 2}, node="t").status == "success"
    assert ex.execute("reserve_hotel", {"hotel_id": "DXB-H01", "check_in": "2026-11-10", "check_out": "2026-11-14",
                                        "guests": 2}, node="t").status == "success"


def test_real_write_tools_cannot_be_registered():
    reg = ToolRegistry()
    spec = ToolSpec("book_flight", "real booking", BookFlightInput, SimulatedBookingOutput, Permission.REAL_WRITE, lambda a: {})
    with pytest.raises(SafetyViolation):
        reg.register(spec)
    with pytest.raises(SafetyViolation):
        assert_permitted("charge_card", Permission.SIMULATED_WRITE)
    with pytest.raises(SafetyViolation):
        assert_permitted("search_flights", Permission.SIMULATED_WRITE)  # declared permission must match policy
    assert Permission.REAL_WRITE not in TOOL_POLICY.values()


def test_simulated_write_must_return_sandbox_result(settings):
    from app.safety.sandbox import SandboxGuard
    with pytest.raises(SafetyViolation):
        SandboxGuard().check_output("book_flight", Permission.SIMULATED_WRITE, {"status": "CONFIRMED", "environment": "production"})


def test_booking_never_runs_unless_requested(runner):
    rec = runner.run(RunRequest(request_text=DEMO_REQUEST))
    assert not any(e["tool_name"] in ("book_flight", "reserve_hotel") for e in rec.tool_trace)


def test_payment_data_removed_before_llm(runner):
    rec = runner.run(RunRequest(request_text=DEMO_REQUEST + " My card is 4111 1111 1111 1111 cvv 123."))
    dump = rec.model_dump_json()
    assert "4111 1111 1111 1111" not in str(rec.events) and "payment_data_removed" in dump


def test_redaction_of_secrets():
    assert redact_text("key sk-abc123def456ghi789") == "key [REDACTED_SECRET]"
    assert redact({"api_key": "x", "nested": {"password": "y"}, "ok": 1}) == {"api_key": "[REDACTED]", "nested": {"password": "[REDACTED]"}, "ok": 1}
    assert sanitise_user_request("pay with 4111111111111111")[1] is True
    assert sanitise_user_request("run-20261007-172154-21485a")[1] is False  # not a card
