"""FastAPI endpoints."""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import DEMO_REQUEST


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("DATA_MODE", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("INJECTED_TIMEOUT_DELAY_SECONDS", "0")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_health_and_catalogues(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and "openai_api_key" not in h
    assert len(client.get("/scenarios").json()) == 15
    names = {t["name"] for t in client.get("/tools").json()}
    assert {"search_flights", "book_flight", "validate_itinerary"} <= names


def test_run_and_export(client):
    r = client.post("/runs", json={"request_text": DEMO_REQUEST, "failure_mode": "flight_timeout"})
    assert r.status_code == 200 and r.json()["status"] == "recovered"
    export = client.get(r.json()["export_url"]).json()
    assert export["failure_mode"] == "flight_timeout" and export["scenario_id"] == "FS-01"
    assert client.get("/runs").json()[0]["run_id"] == export["run_id"]


def test_bad_failure_mode_rejected(client):
    assert client.post("/runs", json={"request_text": DEMO_REQUEST, "failure_mode": "chaos"}).status_code == 422
