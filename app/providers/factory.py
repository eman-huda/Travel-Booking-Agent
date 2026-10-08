"""Builds providers from settings so individual mock providers can be swapped for real ones."""
from __future__ import annotations

from app.config import Settings
from app.providers.mock_provider import MockTravelData
from app.providers.real_providers import OpenERExchange, OpenMeteoWeather
from app.safety.sandbox import SandboxBookingService
from app.tools.travel_tools import Services


def build_services(settings: Settings) -> Services:
    mock = MockTravelData(settings.data_dir)
    weather = OpenMeteoWeather(mock, fallback=mock, timeout=settings.tool_timeout_seconds) \
        if settings.weather_provider == "open_meteo" else mock
    exchange = OpenERExchange(timeout=settings.tool_timeout_seconds) \
        if settings.exchange_provider == "open_er_api" else mock
    return Services(flights=mock, hotels=mock, weather=weather, exchange=exchange,
                    destinations=mock, bookings=SandboxBookingService())
