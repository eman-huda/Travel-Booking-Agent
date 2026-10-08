"""Configuration and API key protection."""
import io
import json
import logging

import pytest

from app.config import ConfigurationError, Settings
from app.tracing.logging_config import RedactingJSONFormatter


def test_missing_openai_key_fails_clearly(tmp_path):
    s = Settings(_env_file=None, llm_provider="openai", openai_api_key=None)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY is missing"):
        s.validate_llm()


def test_placeholder_key_rejected():
    s = Settings(_env_file=None, llm_provider="openai", openai_api_key="your_key_here")
    with pytest.raises(ConfigurationError):
        s.validate_llm()


def test_api_key_never_exposed():
    secret = "sk-test-SECRET-1234567890abcdef"
    s = Settings(_env_file=None, llm_provider="openai", openai_api_key=secret, llm_model="gpt-4.1-mini")
    s.validate_llm()
    assert secret not in repr(s) and secret not in str(s.model_dump())
    summary = json.dumps(s.public_summary())
    assert secret not in summary and '"openai_key_configured": true' in summary


def test_logs_redact_keys():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingJSONFormatter())
    log = logging.getLogger("redaction-test")
    log.addHandler(handler)
    log.warning("calling with sk-live-abcdefghijklmnop", extra={"data": {"authorization": "Bearer abc", "q": "ok"}})
    out = stream.getvalue()
    assert "sk-live" not in out and "Bearer abc" not in out and "[REDACTED" in out


def test_openai_provider_uses_responses_api():
    from app.llm.openai_provider import OpenAIProvider

    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return type("R", (), {"output_text": '{"origin": "Islamabad"}'})()

    fake = type("C", (), {"responses": FakeResponses()})()
    p = OpenAIProvider(api_key="unused", model="m", client=fake)
    assert p.generate_json(task="t", system="sys", prompt="p", context={}) == {"origin": "Islamabad"}
    assert fake.responses.kwargs["instructions"] == "sys" and fake.responses.kwargs["text"]["format"]["type"] == "json_object"
