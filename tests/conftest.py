from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.runner import AgentRunner  # noqa: E402
from app.config import Settings  # noqa: E402
from app.failures.injector import FailureInjector  # noqa: E402
from app.failures.registry import get_scenario  # noqa: E402
from app.providers.factory import build_services  # noqa: E402
from app.safety.sandbox import SandboxGuard  # noqa: E402
from app.tools.executor import ToolExecutor  # noqa: E402
from app.tools.travel_tools import build_registry  # noqa: E402
from app.tracing.tracer import Tracer  # noqa: E402

DEPART = date.today() + timedelta(days=40)
RETURN = DEPART + timedelta(days=4)
DEMO_REQUEST = (f"I want to travel from Islamabad to Dubai on {DEPART.isoformat()} for 5 days. My budget is $1500. "
                "I prefer a comfortable hotel and activities that are not too expensive.")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, llm_provider="stub", data_mode="mock", database_path=tmp_path / "runs.db", log_dir=tmp_path / "logs",
                    injected_timeout_delay_seconds=0.0, log_level="WARNING")


@pytest.fixture
def runner(settings) -> AgentRunner:
    return AgentRunner(settings)


def make_executor(settings: Settings, failure_mode: str = "none"):
    services = build_services(settings)
    registry = build_registry(services)
    tracer = Tracer("test-run")
    injector = FailureInjector(get_scenario(failure_mode), settings.tool_timeout_seconds, settings.injected_timeout_delay_seconds)
    return ToolExecutor(registry, SandboxGuard(), injector, tracer, settings.tool_timeout_seconds), services, tracer


@pytest.fixture
def executor(settings):
    ex, _, _ = make_executor(settings)
    return ex


def flight_args():
    return {"origin": "Islamabad", "destination": "Dubai", "departure_date": DEPART.isoformat(),
            "return_date": RETURN.isoformat(), "passengers": 1}


def hotel_args(budget=None):
    return {"destination": "Dubai", "check_in": DEPART.isoformat(), "check_out": RETURN.isoformat(),
            "guests": 1, "budget": budget}
