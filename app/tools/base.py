"""Tool specification: name, description, schemas, permission and handler."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from app.safety.permissions import Permission


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: Permission
    handler: Callable[[BaseModel], dict]
    critical: bool = False  # the trip cannot be planned without this tool

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "critical": self.critical,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(by_alias=True),
        }
