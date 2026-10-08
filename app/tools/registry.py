"""Allowlisted tool registry. Only registered tools can ever be executed."""
from __future__ import annotations

from app.safety.permissions import SafetyViolation, assert_permitted
from app.tools.base import ToolSpec


class UnknownToolError(KeyError):
    pass


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        assert_permitted(spec.name, spec.permission)  # raises SafetyViolation
        if spec.name in self._tools:
            raise SafetyViolation(f"Tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict]:
        return [t.describe() for t in self._tools.values()]

    def catalogue_for_llm(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "permission": t.permission.value}
                for t in self._tools.values()]

    def as_langchain_tools(self, executor, node: str = "external"):
        """Expose registered tools as LangChain StructuredTools that route through the executor.

        This keeps every call validated, sandboxed, failure-injectable and traced, even when a
        LangChain tool-calling model is used instead of the explicit graph.
        """
        from langchain_core.tools import StructuredTool

        tools = []
        for spec in self._tools.values():
            def _run(_spec=spec, **kwargs):
                return executor.execute(_spec.name, kwargs, node=node).model_dump(mode="json")
            tools.append(StructuredTool.from_function(func=_run, name=spec.name,
                                                      description=spec.description, args_schema=spec.input_model))
        return tools
