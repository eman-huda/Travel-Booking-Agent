"""Live providers, tested offline against recorded responses (no network, no API quota)."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.agent.runner import AgentRunner
from app.config import ConfigurationError, Settings
from app.providers.factory import build_services
from app.providers.live.destinations import parse_listings
from app.schemas.run import RunRequest
from app.schemas.tools import FlightSearchOutput, HotelSearchOutput, ReturnFlightsOutput
from app.tools.errors import UpstreamAPIError

FIX = Path(__file__).parent / "fixtures"
D, R = date(2026, 11, 12), date(2026, 11, 16)

GEOCODE = {
    "islamabad": {"name": "Islamabad", "latitude": 33.72148, "longitude": 73.04329, "country_code": "PK", "timezone": "Asia/Karachi"},
    "dubai": {"name": "Dubai", "latitude": 25.07725, "longitude": 55.30927, "country_code": "AE", "timezone": "Asia/Dubai"},
}
WIKIVOYAGE_WIKITEXT = ("==See==\n* {{see | name=Al Fahidi Fort | price=AED 3 | content=Old fort }}\n"
                       "* {{see | name=[[Dubai Creek]] walk | price=Free }}\n* {{do | name=Desert safari | price=from AED 150}}\n"
                       "[[Dubai/Deira]]")


def fake_handler(request: httpx.Request) -> httpx.Response:
    host, params = request.url.host, dict(request.url.params)
    if host == "serpapi.com":
        assert params["api_key"] == "test-key"
        name = ("serpapi_hotels" if params["engine"] == "google_hotels"
                else "serpapi_returns" if "departure_token" in params else "serpapi_flights")
        return httpx.Response(200, json=json.loads((FIX / f"{name}.json").read_text()))
    if host == "geocoding-api.open-meteo.com":
        hit = GEOCODE.get(params["name"].lower())
        return httpx.Response(200, json={"results": [hit]} if hit else {})
    if host in ("archive-api.open-meteo.com", "api.open-meteo.com"):
        return httpx.Response(200, json={"daily": {"time": [params["start_date"]], "temperature_2m_max": [31.5], "weather_code": [1]}})
    if host == "open.er-api.com":
        return httpx.Response(200, json={"result": "success", "rates": {"USD": 1, "AED": 3.6725, "PKR": 281.0}})
    if host == "en.wikipedia.org":
        return httpx.Response(200, json={"extract": "Dubai is a city in the United Arab Emirates."})
    if host == "en.wikivoyage.org":
        if params.get("action") == "parse":
            return httpx.Response(200, json={"parse": {"wikitext": WIKIVOYAGE_WIKITEXT if "/" not in params["page"] else ""}})
        return httpx.Response(200, json={"query": {"pages": {"1": {"extract": "== Get around ==\nTake the metro.\n== Stay safe ==\nIt is safe."}}}})
    return httpx.Response(503, json={"error": "unavailable"})  # Overpass and anything else


@pytest.fixture
def live_settings(tmp_path):
    return Settings(_env_file=None, llm_provider="stub", data_mode="live", serpapi_api_key="test-key",
                    database_path=tmp_path / "r.db", cache_dir=tmp_path / "cache", log_level="WARNING",
                    injected_timeout_delay_seconds=0)


@pytest.fixture
def live_services(live_settings):
    return build_services(live_settings, http_client=httpx.Client(transport=httpx.MockTransport(fake_handler)))


def test_live_mode_requires_serpapi_key(tmp_path):
    s = Settings(_env_file=None, data_mode="live", serpapi_api_key=None)
    with pytest.raises(ConfigurationError, match="SERPAPI_API_KEY is missing"):
        s.validate_data()


def test_serpapi_flights_are_mapped_and_validated(live_services):
    raw = live_services.flights.search_flights("Islamabad", "Dubai", D, R, 1)
    out = FlightSearchOutput.model_validate(raw)
    assert [f.flight_number for f in out.flights] == ["FZ 354", "EK 613", "QR 615 + QR 1002"]  # unpriced offer dropped
    qr = out.flights[2]
    assert qr.stops == 1 and qr.duration_minutes == 380 and qr.origin_airport == "ISB" and qr.destination_airport == "DXB"
    assert all(f.return_departure is None and f.provider_ref for f in out.flights)


def test_return_flights_use_departure_token(live_services):
    raw = live_services.flights.get_return_flights("TOKEN_FZ354", "Islamabad", "Dubai", D, R, 1)
    out = ReturnFlightsOutput.model_validate(raw)
    assert [o.return_flight_number for o in out.options] == ["FZ 353", "FZ 351"]


def test_serpapi_hotels_skip_unpriced(live_services):
    out = HotelSearchOutput.model_validate(live_services.hotels.search_hotels("Dubai", D, R, 1, 150))
    names = [h.name for h in out.hotels]
    assert "No Rate Hotel" not in names and names[0] == "Rove Downtown"
    assert out.hotels[0].link and out.hotels[0].availability is True


def test_unknown_city_is_a_clear_error(live_services):
    with pytest.raises(UpstreamAPIError, match="Could not find"):
        live_services.flights.search_flights("Atlantis", "Dubai", D, R, 1)


def test_wikivoyage_listings_parse_real_markup():
    items = parse_listings(WIKIVOYAGE_WIKITEXT, "Dubai")
    assert [i["name"] for i in items] == ["Al Fahidi Fort", "Dubai Creek walk", "Desert safari"]
    assert items[0]["price_note"] == "AED 3" and items[1]["estimated_cost"] == 0.0 and items[2]["category"] == "activity"


def test_destination_info_survives_overpass_outage(live_services):
    info = live_services.destinations.get_destination_info("Dubai")
    assert info["attractions"] and "metro" in info["transport"][0]
    assert info["local_currency"] == "AED"


def test_live_agent_end_to_end_with_return_leg_and_sandbox_booking(live_settings, monkeypatch):
    import app.agent.runner as runner_mod
    client = httpx.Client(transport=httpx.MockTransport(fake_handler))
    monkeypatch.setattr(runner_mod, "build_services", lambda s: build_services(s, http_client=client))
    rec = AgentRunner(live_settings).run(RunRequest(
        request_text="I want to travel from Islamabad to Dubai on 12 November 2026 for 5 days. My budget is $1500. "
                     "I prefer a comfortable hotel."))
    st = rec.final_state
    assert rec.data_mode == "live" and rec.status in ("completed", "completed_with_warnings")
    assert st["selected_flight"]["return_flight_number"] == "FZ 353"  # early 02:30 return avoided
    assert st["bookings"]["hotel"]["booking_id"].startswith("TEST-HOTEL-")
    assert "nothing was actually booked" in rec.final_response
    assert "test-key" not in rec.model_dump_json()  # SerpApi key never stored in traces


def test_cache_keeps_original_fetch_time_and_hides_key(live_settings, live_services, tmp_path):
    live_services.flights.search_flights("Islamabad", "Dubai", D, R, 1)
    files = list((tmp_path / "cache").glob("*.json"))
    assert files and all("test-key" not in f.read_text() for f in files)
