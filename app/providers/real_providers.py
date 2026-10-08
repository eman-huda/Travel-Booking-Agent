"""Optional read-only real data providers. No API key needed; both are GET-only.

They are opt-in through WEATHER_PROVIDER and EXCHANGE_PROVIDER. If they cannot answer,
they raise UpstreamAPIError and the agent treats it as a normal tool failure.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from app.providers.base import DestinationProvider, ExchangeRateProvider, WeatherProvider
from app.tools.errors import UpstreamAPIError


class OpenMeteoWeather(WeatherProvider):
    """Open-Meteo forecast (about 16 days ahead). Falls back to the mock climate average beyond that."""

    URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, cities: DestinationProvider, fallback: WeatherProvider, timeout: float = 5.0):
        self.cities, self.fallback, self.timeout = cities, fallback, timeout

    def get_weather(self, destination: str, on_date: date) -> dict:
        city = self.cities.resolve_city(destination)
        if not city:
            raise UpstreamAPIError(404, f"Unknown destination '{destination}'")
        if (on_date - date.today()).days > 15 or on_date < date.today():
            return self.fallback.get_weather(destination, on_date)
        try:
            r = httpx.get(self.URL, timeout=self.timeout, params={
                "latitude": city["lat"], "longitude": city["lon"], "daily": "temperature_2m_max,weathercode",
                "start_date": on_date.isoformat(), "end_date": on_date.isoformat(), "timezone": "auto"})
            r.raise_for_status()
            daily = r.json()["daily"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise UpstreamAPIError(502, f"Weather service error: {type(exc).__name__}") from exc
        code = daily["weathercode"][0]
        condition = "Clear" if code == 0 else "Partly cloudy" if code < 4 else "Rain or showers" if code >= 51 else "Cloudy or foggy"
        return {"status": "success", "destination": city["name"], "date": on_date.isoformat(),
                "temperature": daily["temperature_2m_max"][0], "condition": condition,
                "basis": "forecast", "source": "open-meteo"}


class OpenERExchange(ExchangeRateProvider):
    """open.er-api.com free endpoint (daily reference rates)."""

    URL = "https://open.er-api.com/v6/latest/{base}"

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def get_exchange_rate(self, from_currency: str, to_currency: str) -> dict:
        try:
            r = httpx.get(self.URL.format(base=from_currency), timeout=self.timeout)
            r.raise_for_status()
            body = r.json()
            rate = body["rates"][to_currency]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise UpstreamAPIError(502, f"Exchange rate service error: {type(exc).__name__}") from exc
        return {"status": "success", "from": from_currency, "to": to_currency, "rate": rate,
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "open.er-api.com"}
