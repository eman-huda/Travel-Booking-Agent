"""Application configuration loaded from environment variables and .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

PLACEHOLDER_KEYS = {"", "your_key_here", "sk-your-key", "changeme"}


class ConfigurationError(RuntimeError):
    """Raised when the application cannot start because configuration is invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Travel Agent Testbed"
    agent_version: str = "1.0.0"

    # LLM
    llm_provider: Literal["openai", "ollama", "stub"] = "openai"
    openai_api_key: SecretStr | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = 45.0
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # Failure injection and recovery
    failure_mode: str = "none"
    max_tool_retries: int = Field(1, ge=0, le=3)
    max_total_retries: int = Field(6, ge=0, le=20)
    max_booking_alternatives: int = Field(2, ge=0, le=5)
    max_itinerary_repairs: int = Field(1, ge=0, le=3)
    tool_timeout_seconds: float = Field(5.0, gt=0)
    injected_timeout_delay_seconds: float = Field(0.5, ge=0)
    stale_data_max_age_hours: int = Field(24, ge=1)

    # Optional read-only real providers
    weather_provider: Literal["mock", "open_meteo"] = "mock"
    exchange_provider: Literal["mock", "open_er_api"] = "mock"

    # Paths
    data_dir: Path = BASE_DIR / "data"
    database_path: Path = BASE_DIR / "var" / "runs.db"
    log_dir: Path = BASE_DIR / "var" / "logs"
    log_level: str = "INFO"

    @property
    def active_model(self) -> str:
        if self.llm_provider == "ollama":
            return self.ollama_model
        if self.llm_provider == "stub":
            return "stub-deterministic"
        return self.llm_model

    def validate_llm(self) -> None:
        """Fail fast with a clear message when the LLM provider is not usable."""
        if self.llm_provider == "openai":
            key = self.openai_api_key.get_secret_value().strip() if self.openai_api_key else ""
            if key in PLACEHOLDER_KEYS:
                raise ConfigurationError(
                    "OPENAI_API_KEY is missing. Copy .env.example to .env in the project root "
                    "and set OPENAI_API_KEY to your OpenAI key (and LLM_MODEL to the model you "
                    "want). To run offline without a key, set LLM_PROVIDER=stub."
                )
            if self.llm_model.strip() in {"", "your_selected_model"}:
                raise ConfigurationError(
                    "LLM_MODEL is not set. Put the OpenAI model name you want to use in .env, "
                    "for example LLM_MODEL=gpt-4.1-mini."
                )

    def public_summary(self) -> dict:
        """Configuration that is safe to show in the UI or API. Never includes secrets."""
        return {
            "app_name": self.app_name,
            "agent_version": self.agent_version,
            "llm_provider": self.llm_provider,
            "model": self.active_model,
            "weather_provider": self.weather_provider,
            "exchange_provider": self.exchange_provider,
            "max_tool_retries": self.max_tool_retries,
            "max_total_retries": self.max_total_retries,
            "openai_key_configured": bool(
                self.openai_api_key
                and self.openai_api_key.get_secret_value().strip() not in PLACEHOLDER_KEYS
            ),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
