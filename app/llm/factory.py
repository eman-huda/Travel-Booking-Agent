"""Creates the configured LLM provider."""
from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider


def build_llm(settings: Settings) -> LLMProvider:
    settings.validate_llm()
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(settings.openai_api_key.get_secret_value(), settings.llm_model, settings.llm_timeout_seconds)
    if settings.llm_provider == "groq":
        from app.llm.groq_provider import GroqProvider
        return GroqProvider(settings.groq_api_key.get_secret_value(), settings.groq_model,
                            settings.groq_base_url, settings.llm_timeout_seconds)
    if settings.llm_provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider
        return OllamaProvider(settings.ollama_base_url, settings.ollama_model)
    from app.llm.stub_provider import StubLLMProvider
    return StubLLMProvider()
