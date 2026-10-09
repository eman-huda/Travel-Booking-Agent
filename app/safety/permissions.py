"""Tool permission policy. Enforced in code by the registry and executor, not by prompts."""
from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    READ_ONLY = "READ_ONLY"
    SIMULATED_WRITE = "SIMULATED_WRITE"
    REAL_WRITE = "REAL_WRITE"  # declared so it can be rejected; never allowed


ALLOWED_PERMISSIONS: frozenset[Permission] = frozenset({Permission.READ_ONLY, Permission.SIMULATED_WRITE})

# The allowlist. A tool that is not listed here cannot be registered or executed.
TOOL_POLICY: dict[str, Permission] = {
    "search_flights": Permission.READ_ONLY,
    "search_hotels": Permission.READ_ONLY,
    "get_weather": Permission.READ_ONLY,
    "get_exchange_rate": Permission.READ_ONLY,
    "get_destination_info": Permission.READ_ONLY,
    "get_return_flights": Permission.READ_ONLY,
    "create_itinerary": Permission.READ_ONLY,
    "validate_itinerary": Permission.READ_ONLY,
    "book_flight": Permission.SIMULATED_WRITE,
    "reserve_hotel": Permission.SIMULATED_WRITE,
}


class SafetyViolation(Exception):
    """Raised when something tries to cross the sandbox boundary."""


def assert_permitted(tool_name: str, declared: Permission) -> Permission:
    policy = TOOL_POLICY.get(tool_name)
    if policy is None:
        raise SafetyViolation(f"Tool '{tool_name}' is not on the allowlist")
    if declared == Permission.REAL_WRITE or policy == Permission.REAL_WRITE:
        raise SafetyViolation(f"Tool '{tool_name}' requests REAL_WRITE, which is not permitted in this system")
    if declared != policy:
        raise SafetyViolation(f"Tool '{tool_name}' declares {declared.value} but policy is {policy.value}")
    if policy not in ALLOWED_PERMISSIONS:
        raise SafetyViolation(f"Permission {policy.value} is not allowed")
    return policy
