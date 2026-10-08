"""Tool executor: allowlist, permission check, input validation, failure injection,
timeout, output schema validation and tracing, for every single tool call."""
from __future__ import annotations

import concurrent.futures
import copy
import time
from typing import Any

from pydantic import ValidationError

from app.failures.injector import SERVICE_NAMES, FailureInjector
from app.safety.permissions import SafetyViolation
from app.safety.sandbox import SandboxGuard
from app.schemas.trace import ToolError, ToolResult, TraceEvent
from app.tools.errors import ToolTimeoutError, UpstreamAPIError
from app.tools.registry import ToolRegistry, UnknownToolError
from app.tracing.tracer import Tracer

_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


def _summarise_validation(exc: ValidationError, limit: int = 4) -> str:
    parts = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    more = len(exc.errors()) - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, guard: SandboxGuard, injector: FailureInjector,
                 tracer: Tracer, timeout_seconds: float = 5.0):
        self.registry = registry
        self.guard = guard
        self.injector = injector
        self.tracer = tracer
        self.timeout_seconds = timeout_seconds

    def execute(self, tool_name: str, arguments: dict[str, Any], *, node: str, attempt: int = 0) -> ToolResult:
        start = time.perf_counter()
        injection: dict | None = None
        sent_args: dict[str, Any] = copy.deepcopy(arguments)
        result: ToolResult

        def _err(kind, message, retryable) -> ToolResult:
            return ToolResult(tool=tool_name, status="error", attempt=attempt,
                              duration_ms=(time.perf_counter() - start) * 1000,
                              error=ToolError(type=kind, message=message, retryable=retryable))

        try:
            spec = self.registry.get(tool_name)
            self.guard.authorize(spec.name, spec.permission)

            sent_args, injection, short_circuit = self.injector.before_call(tool_name, sent_args, attempt)
            try:
                validated = spec.input_model.model_validate(sent_args)
            except ValidationError as exc:
                result = _err("invalid_argument", f"Invalid arguments for {tool_name}: {_summarise_validation(exc)}", True)
            else:
                if short_circuit is not None:
                    raw = short_circuit
                else:
                    future = _POOL.submit(spec.handler, validated)
                    try:
                        raw = future.result(timeout=self.timeout_seconds)
                    except concurrent.futures.TimeoutError:
                        raise ToolTimeoutError(SERVICE_NAMES.get(tool_name, tool_name), self.timeout_seconds)
                raw, post_injection = self.injector.after_call(tool_name, raw, attempt)
                injection = injection or post_injection
                try:
                    output = spec.output_model.model_validate(raw)
                except ValidationError as exc:
                    result = _err("schema_validation",
                                  f"{tool_name} returned data that does not match its output schema: {_summarise_validation(exc)}",
                                  True)
                else:
                    data = output.model_dump(mode="json", by_alias=True)
                    self.guard.check_output(spec.name, spec.permission, data)
                    result = ToolResult(tool=tool_name, status="success", data=data, attempt=attempt,
                                        duration_ms=(time.perf_counter() - start) * 1000)
        except UnknownToolError:
            result = _err("unknown_tool", f"'{tool_name}' is not a registered tool", False)
        except SafetyViolation as exc:
            result = _err("not_permitted", str(exc), False)
        except ToolTimeoutError as exc:
            injection = injection or getattr(exc, "injection", None)
            result = _err("timeout", str(exc), True)
        except UpstreamAPIError as exc:
            injection = injection or getattr(exc, "injection", None)
            result = _err("api_error", str(exc), exc.status_code >= 500 or exc.status_code == 429)
        except Exception as exc:  # pragma: no cover - defensive
            result = _err("internal", f"{tool_name} failed: {type(exc).__name__}", False)

        self.tracer.record(TraceEvent(
            run_id=self.tracer.run_id, event_type="tool", node=node, tool_name=tool_name,
            arguments=sent_args, result=result.data if result.status == "success" else None,
            status="success" if result.status == "success" else "failure",
            duration_ms=result.duration_ms,
            error=(f"[{result.error.type}] {result.error.message}" if result.error else None),
            retry_number=attempt, injected_failure=injection,
        ))
        return result
