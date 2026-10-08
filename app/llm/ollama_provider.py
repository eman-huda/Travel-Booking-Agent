"""Ollama provider for local models (http://localhost:11434 by default)."""
from __future__ import annotations

import httpx

from app.llm.base import LLMError, LLMProvider, parse_json_text


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout

    def _chat(self, system: str, prompt: str, json_mode: bool) -> str:
        body = {"model": self.model, "stream": False, "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if json_mode:
            body["format"] = "json"
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMError(f"Ollama request failed: {type(exc).__name__}") from exc

    def generate_json(self, *, task, system, prompt, context):
        return parse_json_text(self._chat(system, prompt, True))

    def generate_text(self, *, task, system, prompt, context):
        return self._chat(system, prompt, False).strip()
