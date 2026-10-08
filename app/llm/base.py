"""Provider-agnostic LLM interface. The agent depends only on this."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any


class LLMError(RuntimeError):
    pass


def parse_json_text(text: str) -> dict:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.S)
        if not match:
            raise LLMError("Model did not return JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise LLMError("Model returned JSON that is not an object")
    return value


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def generate_json(self, *, task: str, system: str, prompt: str, context: dict[str, Any]) -> dict: ...

    @abstractmethod
    def generate_text(self, *, task: str, system: str, prompt: str, context: dict[str, Any]) -> str: ...
