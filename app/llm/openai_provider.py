"""OpenAI provider using the Responses API."""
from __future__ import annotations

from typing import Any

from app.llm.base import LLMError, LLMProvider, parse_json_text


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0, client: Any = None):
        self.model = model
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
        self._client = client  # key lives only inside the SDK client

    def _call(self, system: str, prompt: str, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {"model": self.model, "instructions": system, "input": prompt}
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        try:
            response = self._client.responses.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {type(exc).__name__}") from exc
        text = getattr(response, "output_text", None)
        if not text:
            raise LLMError("OpenAI returned an empty response")
        return text

    def generate_json(self, *, task, system, prompt, context):
        return parse_json_text(self._call(system, prompt + "\n\nRespond with a single JSON object only.", True))

    def generate_text(self, *, task, system, prompt, context):
        return self._call(system, prompt, False).strip()
