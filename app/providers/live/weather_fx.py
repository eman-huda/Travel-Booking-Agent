"""Real weather (Open-Meteo forecast or last year's observations) and exchange rates (open.er-api.com)."""
from __future__ import annotations

from datetime import date, timedelta

from app.providers.base import ExchangeRateProvider, WeatherProvider
from app.providers.live.geo import LiveGeo
from app.providers.live.http import CachedHTTP
from app.tools.errors import UpstreamAPIError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FX_URL = "https://open.er-api.com/v6/latest/{base}"


def describe_wmo(code: int | None) -> str:
    if code is None:
        return "Unknown conditions"
    if code == 0:
        return "Clear sky"
    if code in (1, 2):
        return "Mainly clear to partly cloudy"
    if code == 3:
        return "Overcast"
    if code in (45, 48):
        return "Fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "Rain or showers"
    if 71 <= code <= 77 or code in (85, 86):
        return "Snow"
    if code >= 95:
        return "Thunderstorms"
    return "Mixed conditions"


class LiveWeather(WeatherProvider):
    def __init__(self, http: CachedHTTP, geo: LiveGeo):
        self.http, self.geo = http, geo

    def get_weather(self, destination: str, on_date: date) -> dict:
        city = self.geo.require(destination)
        days_ahead = (on_date - date.today()).days
        if 0 <= days_ahead <= 15:
            url, query_date, basis = FORECAST_URL, on_date, "forecast"
        else:
            try:
                query_date = on_date.replace(year=on_date.year - 1)
            except ValueError:  # 29 February
                query_date = on_date - timedelta(days=365)
            url, basis = ARCHIVE_URL, "last_year_observed"
        body, _ = self.http.get_json(url, service="Weather service", params={
            "latitude": city["lat"], "longitude": city["lon"], "daily": "temperature_2m_max,weather_code",
            "start_date": query_date.isoformat(), "end_date": query_date.isoformat(), "timezone": "auto"})
        try:
            daily = body["daily"]
            temp, code = daily["temperature_2m_max"][0], daily["weather_code"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamAPIError(502, "Weather service returned no data for that date") from exc
        if temp is None:
            raise UpstreamAPIError(502, "Weather service returned no temperature for that date")
        source = "open-meteo" if basis == "forecast" else f"open-meteo archive ({query_date.isoformat()})"
        return {"status": "success", "destination": city["name"], "date": on_date.isoformat(), "temperature": temp,
                "condition": describe_wmo(code), "basis": basis, "source": source}


class LiveExchange(ExchangeRateProvider):
    def __init__(self, http: CachedHTTP):
        self.http = http

    def get_exchange_rate(self, from_currency: str, to_currency: str) -> dict:
        body, fetched_at = self.http.get_json(FX_URL.format(base=from_currency), service="Exchange rate service")
        if body.get("result") != "success":
            raise UpstreamAPIError(502, f"Exchange rate service: {body.get('error-type', 'unknown error')}")
        rate = (body.get("rates") or {}).get(to_currency)
        if not rate:
            raise UpstreamAPIError(400, f"No rate available for {from_currency}/{to_currency}")
        return {"status": "success", "from": from_currency, "to": to_currency, "rate": rate,
                "retrieved_at": fetched_at, "source": "open.er-api.com"}
