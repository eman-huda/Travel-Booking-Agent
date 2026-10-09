"""Groq provider (free tier) through Groq's OpenAI-compatible Chat Completions endpoint."""
from __future__ import annotations

from typing import Any

from app.llm.base import LLMError, LLMProvider, parse_json_text


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 45.0, client: Any = None):
        self.model = model
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self._client = client  # key lives only inside the SDK client

    def _call(self, system: str, prompt: str, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Groq request failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        text = response.choices[0].message.content if response.choices else None
        if not text:
            raise LLMError("Groq returned an empty response")
        return text

    def generate_json(self, *, task, system, prompt, context):
        return parse_json_text(self._call(system, prompt + "\n\nRespond with a single JSON object only.", True))

    def generate_text(self, *, task, system, prompt, context):
        return self._call(system, prompt, False).strip()
