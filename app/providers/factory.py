"""Builds providers from settings. DATA_MODE=live uses real data; DATA_MODE=mock uses local JSON."""
from __future__ import annotations

from app.config import Settings
from app.providers.mock_provider import MockTravelData
from app.safety.sandbox import SandboxBookingService
from app.tools.travel_tools import Services


def build_services(settings: Settings, http_client=None) -> Services:
    bookings = SandboxBookingService()  # bookings are always simulated, in every mode
    if settings.data_mode == "live":
        from app.providers.live.destinations import LiveDestinations
        from app.providers.live.geo import LiveGeo
        from app.providers.live.http import CachedHTTP
        from app.providers.live.serpapi import SerpApiTravel
        from app.providers.live.weather_fx import LiveExchange, LiveWeather

        http = CachedHTTP(settings.cache_dir, settings.live_cache_ttl_hours, settings.tool_timeout_seconds, client=http_client)
        geo = LiveGeo(http, settings.data_dir)
        travel = SerpApiTravel(http, geo, settings.serpapi_api_key.get_secret_value(), gl=settings.serpapi_gl)
        return Services(flights=travel, hotels=travel, weather=LiveWeather(http, geo), exchange=LiveExchange(http),
                        destinations=LiveDestinations(http, geo), bookings=bookings)

    mock = MockTravelData(settings.data_dir)
    return Services(flights=mock, hotels=mock, weather=mock, exchange=mock, destinations=mock, bookings=bookings)
