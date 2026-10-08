"""Runs the agent graph for one request, streams real progress events, and persists the run."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from app.agent.context import AgentContext
from app.agent.graph import build_graph
from app.agent.nodes import NODE_LABELS
from app.agent.recovery import RecoveryPolicy
from app.agent.state import TravelState
from app.config import Settings, get_settings
from app.failures.injector import FailureInjector
from app.failures.registry import get_scenario
from app.llm.base import LLMProvider
from app.llm.factory import build_llm
from app.providers.factory import build_services
from app.safety.redaction import redact
from app.safety.sandbox import SandboxGuard
from app.schemas.run import ExpectationCheck, RecoverySummary, RunMetrics, RunRecord, RunRequest
from app.storage.db import RunStore
from app.tools.executor import ToolExecutor
from app.tools.travel_tools import build_registry
from app.tracing.logging_config import configure_logging, get_logger
from app.tracing.tracer import Tracer

log = get_logger("runner")
_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


class AgentRunner:
    def __init__(self, settings: Settings | None = None, llm: LLMProvider | None = None, store: RunStore | None = None):
        self.settings = settings or get_settings()
        configure_logging(self.settings.log_level, self.settings.log_dir)
        self.llm = llm or build_llm(self.settings)  # raises ConfigurationError with a clear message
        self.store = store or RunStore(self.settings.database_path)

    def iter_run(self, request: RunRequest) -> Iterator[dict[str, Any]]:
        """Yields {'type': 'node_start'|'node_end', ...} as the graph executes, then {'type': 'result', 'record': RunRecord}."""
        scenario = get_scenario(request.failure_mode)
        simulate_booking = request.simulate_booking or scenario.requires_booking
        run_id = new_run_id()
        tracer = Tracer(run_id)
        services = build_services(self.settings)
        registry = build_registry(services)
        injector = FailureInjector(scenario, self.settings.tool_timeout_seconds, self.settings.injected_timeout_delay_seconds)
        executor = ToolExecutor(registry, SandboxGuard(), injector, tracer, self.settings.tool_timeout_seconds)
        ctx = AgentContext(settings=self.settings, llm=self.llm, executor=executor, registry=registry, tracer=tracer,
                           policy=RecoveryPolicy(self.settings), cities=services.destinations, scenario=scenario)
        initial = TravelState(run_id=run_id, user_request=request.request_text, failure_mode=scenario.key,
                              simulate_booking=simulate_booking,
                              request_overrides=request.overrides.model_dump(mode="json", exclude_none=True))
        initial_dump = initial.model_dump(mode="json")
        started = time.perf_counter()
        final_values: dict[str, Any] = initial_dump
        error: str | None = None
        log.info("run started", extra={"data": {"run_id": run_id, "failure_mode": scenario.key}})
        try:
            config = {"configurable": {"ctx": ctx}, "recursion_limit": 60}
            for mode, payload in get_graph().stream(initial, config, stream_mode=["tasks", "values"]):
                if mode == "tasks":
                    name = payload.get("name")
                    if "input" in payload:
                        yield {"type": "node_start", "node": name, "label": NODE_LABELS.get(name, name)}
                    else:
                        yield {"type": "node_end", "node": name, "error": str(payload.get("error")) if payload.get("error") else None}
                elif mode == "values":
                    final_values = payload
        except Exception as exc:  # unexpected failure: still produce and store a record
            error = f"{type(exc).__name__}: {exc}"
            log.exception("run crashed")

        final_state = TravelState.model_validate(final_values) if isinstance(final_values, dict) else final_values
        record = self._build_record(run_id, request, scenario, initial_dump, final_state, tracer, started, error)
        self.store.save(record)
        log.info("run finished", extra={"data": {"run_id": run_id, "status": record.status}})
        yield {"type": "result", "record": record}

    def run(self, request: RunRequest) -> RunRecord:
        record = None
        for event in self.iter_run(request):
            if event["type"] == "result":
                record = event["record"]
        return record

    def _build_record(self, run_id, request, scenario, initial_dump, state: TravelState, tracer: Tracer,
                      started: float, error: str | None) -> RunRecord:
        status = state.status if state.status != "running" else "error"
        final_response = state.final_response or ""
        errors = list(state.errors)
        if error:
            status = "error"
            errors.append(error)
            final_response = final_response or f"The agent stopped because of an internal error ({error})."
        events = [e.model_dump(mode="json") for e in tracer.events]
        tool_events = [e for e in events if e["event_type"] == "tool"]
        actions = [a.model_dump() for a in state.recovery_actions]
        recovery_result = ("N/A" if not actions else
                           "success" if status in ("recovered", "completed") else
                           "partial" if status == "completed_with_warnings" else "failed")
        final_dump = redact(state.model_dump(mode="json", exclude={"tool_trace", "last_tool_result"}))
        return RunRecord(
            run_id=run_id, agent_version=self.settings.agent_version, created_at=datetime.now(timezone.utc),
            llm_provider=self.settings.llm_provider, model=self.settings.active_model,
            scenario_id=scenario.scenario_id, failure_mode=scenario.key, user_request=request.request_text,
            status=status, final_response=final_response, initial_state=redact(initial_dump), final_state=final_dump,
            tool_trace=tool_events, events=events, errors=errors,
            recovery=RecoverySummary(attempted=bool(actions), result=recovery_result, actions=actions),
            metrics=RunMetrics(
                tool_calls=len(tool_events),
                tool_failures=sum(1 for e in tool_events if e["status"] == "failure"),
                failures_detected=sum(1 for a in actions),
                retries=state.retry_count,
                llm_calls=sum(1 for e in events if e["event_type"] == "llm"),
                duration_ms=round((time.perf_counter() - started) * 1000, 1)),
            expectation=ExpectationCheck(
                scenario_id=scenario.scenario_id, failure_mode=scenario.key,
                expected_behavior=scenario.expected_behavior, expected_outcome=scenario.expected_outcome,
                actual_outcome=status,
                matches=(status == scenario.expected_outcome) or (status == "needs_clarification" and scenario.key == "none" and False)),
        )
