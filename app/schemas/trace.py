"""Trace and tool result models. ToolResult is what the agent sees; TraceEvent is what researchers see."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ToolErrorType = Literal[
    "timeout",
    "api_error",
    "invalid_argument",
    "schema_validation",
    "not_permitted",
    "unknown_tool",
    "internal",
]


class ToolError(BaseModel):
    type: ToolErrorType
    message: str
    retryable: bool


class ToolResult(BaseModel):
    """Agent-facing result. Never contains information about injected failures."""

    tool: str
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    duration_ms: float
    attempt: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TraceEvent(BaseModel):
    """Researcher-facing trace record."""

    event_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    run_id: str
    timestamp: datetime = Field(default_factory=_now)
    event_type: Literal["node", "tool", "llm", "recovery"]
    node: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    result: Any | None = None
    status: Literal["started", "success", "failure", "retrying", "skipped", "info"]
    duration_ms: float | None = None
    error: str | None = None
    retry_number: int = 0
    message: str | None = None
    injected_failure: dict[str, Any] | None = None  # researchers only; never shown to the agent
