"""Lookup helpers for failure scenarios."""
from __future__ import annotations

from app.failures.scenarios import SCENARIOS, FailureScenario

_BY_KEY = {s.key: s for s in SCENARIOS}
_BY_ID = {s.scenario_id: s for s in SCENARIOS}
_BY_LABEL = {s.label.lower(): s for s in SCENARIOS}


def get_scenario(key_or_id: str | None) -> FailureScenario:
    if not key_or_id:
        return _BY_KEY["none"]
    k = key_or_id.strip()
    found = _BY_KEY.get(k.lower()) or _BY_ID.get(k.upper()) or _BY_LABEL.get(k.lower())
    if not found:
        raise ValueError(f"Unknown failure mode '{key_or_id}'. Valid keys: {', '.join(_BY_KEY)}")
    return found


def list_scenarios() -> list[FailureScenario]:
    return list(SCENARIOS)


def scenario_options() -> dict[str, str]:
    """label -> key, for UI dropdowns."""
    return {s.label: s.key for s in SCENARIOS}
