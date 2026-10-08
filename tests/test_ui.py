"""Headless smoke test of the Streamlit dashboard."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "frontend" / "streamlit_app.py")


def test_dashboard_runs_normal_and_failure_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("INJECTED_TIMEOUT_DELAY_SECONDS", "0")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    from app.config import get_settings
    get_settings.cache_clear()
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    at.text_area[0].set_value("Islamabad to Dubai on 2026-12-01 for 5 days, $1500, comfortable hotel, just me").run()
    [b for b in at.button if b.label == "Run agent"][0].click().run()
    assert not at.exception and at.session_state.record.status == "completed"
    at.toggle[0].set_value(True).run()
    [s for s in at.selectbox if s.label == "Failure injection"][0].set_value("Hotel Search Timeout").run()
    [b for b in at.button if b.label == "Run agent"][0].click().run()
    assert not at.exception and at.session_state.record.status == "recovered"
    at.radio[0].set_value("Run history").run()
    assert not at.exception
    get_settings.cache_clear()
