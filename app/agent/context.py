"""Per-run dependencies passed to graph nodes through the LangGraph config."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.agent.recovery import RecoveryPolicy
from app.config import Settings
from app.failures.scenarios import FailureScenario
from app.llm.base import LLMError, LLMProvider
from app.providers.base import DestinationProvider
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tracing.tracer import Tracer


@dataclass
class AgentContext:
    settings: Settings
    llm: LLMProvider
    executor: ToolExecutor
    registry: ToolRegistry
    tracer: Tracer
    policy: RecoveryPolicy
    cities: DestinationProvider
    scenario: FailureScenario

    def llm_json(self, *, node: str, task: str, system: str, prompt: str, context: dict[str, Any]) -> dict:
        start = time.perf_counter()
        try:
            out = self.llm.generate_json(task=task, system=system, prompt=prompt, context=context)
        except LLMError as exc:
            self.tracer.llm(node, task, "failure", (time.perf_counter() - start) * 1000, error=str(exc))
            raise
        self.tracer.llm(node, task, "success", (time.perf_counter() - start) * 1000, output=out)
        return out

    def llm_text(self, *, node: str, task: str, system: str, prompt: str, context: dict[str, Any]) -> str:
        start = time.perf_counter()
        try:
            out = self.llm.generate_text(task=task, system=system, prompt=prompt, context=context)
        except LLMError as exc:
            self.tracer.llm(node, task, "failure", (time.perf_counter() - start) * 1000, error=str(exc))
            raise
        self.tracer.llm(node, task, "success", (time.perf_counter() - start) * 1000, output={"text": out})
        return out
