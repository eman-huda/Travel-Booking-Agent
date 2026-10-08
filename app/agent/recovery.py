"""Bounded recovery policy shared by validation nodes."""
from __future__ import annotations

from app.config import Settings
from app.schemas.trace import ToolError


class RecoveryPolicy:
    def __init__(self, settings: Settings):
        self.max_tool_retries = settings.max_tool_retries
        self.max_total_retries = settings.max_total_retries

    def retries_used(self, attempts: dict[str, int], tool: str) -> int:
        return max(0, attempts.get(tool, 0) - 1)

    def can_retry(self, attempts: dict[str, int], retry_count: int, tool: str) -> bool:
        return self.retries_used(attempts, tool) < self.max_tool_retries and retry_count < self.max_total_retries

    @staticmethod
    def action_for_error(error: ToolError) -> str:
        if error.type == "invalid_argument":
            return "retry_rebuild_args"
        if error.type == "schema_validation":
            return "retry_after_schema_rejection"
        if error.type == "timeout":
            return "retry_after_timeout"
        if error.type == "api_error":
            return "retry_after_api_error"
        return "abort"
