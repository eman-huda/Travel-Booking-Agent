"""Sandbox guard and the only booking service in the system (simulated, in memory)."""
from __future__ import annotations

import itertools
import threading
from datetime import date

from app.safety.permissions import Permission, SafetyViolation, assert_permitted

SANDBOX_LABEL = "SANDBOX — NO REAL TRANSACTION"
SIMULATED_MESSAGE = "Simulated booking only. No real booking was made."


class SandboxGuard:
    """Checks every tool call before and after execution."""

    def authorize(self, tool_name: str, declared: Permission) -> Permission:
        return assert_permitted(tool_name, declared)

    def check_output(self, tool_name: str, permission: Permission, output: dict) -> None:
        if permission == Permission.SIMULATED_WRITE:
            if output.get("environment") != "sandbox":
                raise SafetyViolation(f"{tool_name} returned a non-sandbox result; rejected")


class SandboxBookingService:
    """Records simulated bookings in memory only.

    There is deliberately no payment, airline, hotel, email or network client here.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._flight_seq = itertools.count(1)
        self._hotel_seq = itertools.count(1)
        self.ledger: list[dict] = []

    def book_flight(self, flight_id: str, passengers: int) -> dict:
        with self._lock:
            booking_id = f"TEST-FLIGHT-{next(self._flight_seq):03d}"
            record = {"type": "flight", "booking_id": booking_id, "flight_id": flight_id, "passengers": passengers}
            self.ledger.append(record)
        return {"status": "CONFIRMED", "booking_id": booking_id, "environment": "sandbox", "message": SIMULATED_MESSAGE}

    def reserve_hotel(self, hotel_id: str, check_in: date, check_out: date, guests: int) -> dict:
        with self._lock:
            booking_id = f"TEST-HOTEL-{next(self._hotel_seq):03d}"
            record = {"type": "hotel", "booking_id": booking_id, "hotel_id": hotel_id,
                      "check_in": check_in.isoformat(), "check_out": check_out.isoformat(), "guests": guests}
            self.ledger.append(record)
        return {"status": "CONFIRMED", "booking_id": booking_id, "environment": "sandbox", "message": SIMULATED_MESSAGE}


__all__ = ["SandboxGuard", "SandboxBookingService", "SafetyViolation", "SANDBOX_LABEL", "SIMULATED_MESSAGE"]
