"""Run tracer: the authoritative, researcher-facing record of everything the agent did."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from app.safety.redaction import redact
from app.schemas.trace import TraceEvent
from app.tracing.logging_config import get_logger

log = get_logger("trace")


class Tracer:
    def __init__(self, run_id: str, listener: Callable[[TraceEvent], None] | None = None):
        self.run_id = run_id
        self.events: list[TraceEvent] = []
        self._lock = threading.Lock()
        self._node_started: dict[str, float] = {}
        self.listener = listener

    def record(self, event: TraceEvent) -> TraceEvent:
        event.arguments = redact(event.arguments) if event.arguments is not None else None
        event.result = redact(event.result) if event.result is not None else None
        with self._lock:
            self.events.append(event)
        log.info(f"{event.event_type}:{event.tool_name or event.node}:{event.status}",
                 extra={"data": event.model_dump(mode="json", exclude={"result"})})
        if self.listener:
            try:
                self.listener(event)
            except Exception:  # listener problems must never break a run
                log.exception("trace listener failed")
        return event

    # ---------- node lifecycle ----------
    def node_start(self, node: str) -> None:
        self._node_started[node] = time.perf_counter()
        self.record(TraceEvent(run_id=self.run_id, event_type="node", node=node, status="started"))

    def node_end(self, node: str, status: str, message: str | None = None) -> None:
        started = self._node_started.pop(node, None)
        dur = (time.perf_counter() - started) * 1000 if started else None
        self.record(TraceEvent(run_id=self.run_id, event_type="node", node=node, status=status,
                               duration_ms=dur, message=message))

    # ---------- other events ----------
    def recovery(self, node: str, tool: str | None, issue: str, action: str, detail: str, attempt: int) -> None:
        self.record(TraceEvent(run_id=self.run_id, event_type="recovery", node=node, tool_name=tool,
                               status="retrying" if action.startswith("retry") else "info",
                               message=f"{issue} -> {action}: {detail}", retry_number=attempt))

    def llm(self, node: str, task: str, status: str, duration_ms: float, output: Any = None, error: str | None = None) -> None:
        self.record(TraceEvent(run_id=self.run_id, event_type="llm", node=node, tool_name=f"llm.{task}",
                               status=status, duration_ms=duration_ms, result=output, error=error))

    def tool_events(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type == "tool"]
