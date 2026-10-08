"""Mock travel data provider backed by local JSON files in data/."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from app.providers.base import (
    DestinationProvider,
    ExchangeRateProvider,
    FlightProvider,
    HotelProvider,
    WeatherProvider,
)
from app.tools.errors import UpstreamAPIError


@lru_cache(maxsize=16)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fmt_duration(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m"


class MockTravelData(FlightProvider, HotelProvider, WeatherProvider, ExchangeRateProvider, DestinationProvider):
    """Behaves like a set of external travel services, using deterministic local data."""

    source = "mock"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    # ---------- helpers ----------
    def _file(self, name: str) -> dict:
        return _load(str(self.data_dir / name))

    def _cities(self) -> list[dict]:
        return self._file("destinations.json")["cities"]

    def resolve_city(self, name: str) -> dict | None:
        key = (name or "").strip().lower()
        for city in self._cities():
            if key == city["name"].lower() or key in city["aliases"] or key == city["iata"].lower():
                return city
        return None

    def known_cities(self) -> list[str]:
        return [c["name"] for c in self._cities()]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---------- flights ----------
    def search_flights(self, origin, destination, departure_date, return_date, passengers):
        o, d = self.resolve_city(origin), self.resolve_city(destination)
        flights: list[dict] = []
        if o and d:
            data = self._file("flights.json")
            route = next((r for r in data["routes"] if r["origin"] == o["name"] and r["destination"] == d["name"]), None)
            if route:
                tz_shift = d["tz_offset"] - o["tz_offset"]
                weekday_factor = 1.06 if departure_date.weekday() in (3, 4) else 1.0
                for idx, opt in enumerate(route["options"], start=1):
                    dep = datetime.combine(departure_date, time.fromisoformat(opt["departure_time"]))
                    arr = dep + timedelta(minutes=opt["duration_minutes"] + tz_shift * 60)
                    rdep = datetime.combine(return_date, time.fromisoformat(opt["return_departure_time"]))
                    rarr = rdep + timedelta(minutes=opt["return_duration_minutes"] - tz_shift * 60)
                    flights.append({
                        "flight_id": f"{o['iata']}{d['iata']}-{departure_date:%m%d}-{idx:02d}",
                        "airline": opt["airline"],
                        "flight_number": opt["flight_number"],
                        "origin": o["name"],
                        "destination": d["name"],
                        "departure": dep.isoformat(),
                        "arrival": arr.isoformat(),
                        "duration": _fmt_duration(opt["duration_minutes"]),
                        "duration_minutes": opt["duration_minutes"],
                        "return_flight_number": opt["return_flight_number"],
                        "return_departure": rdep.isoformat(),
                        "return_arrival": rarr.isoformat(),
                        "price": round(opt["base_price"] * weekday_factor, 2),
                        "currency": data["currency"],
                        "stops": opt["stops"],
                        "cabin": "Economy",
                    })
        return {"status": "success", "flights": flights, "retrieved_at": self._now(),
                "total_results": len(flights), "source": self.source}

    # ---------- hotels ----------
    def search_hotels(self, destination, check_in, check_out, guests, budget):
        city = self.resolve_city(destination)
        hotels = []
        if city:
            for h in self._file("hotels.json")["hotels"].get(city["name"], []):
                if budget is not None and h["nightly_price"] > budget:
                    continue
                hotels.append(dict(h))
        return {"status": "success", "hotels": hotels, "retrieved_at": self._now(),
                "total_results": len(hotels), "source": self.source}

    # ---------- weather ----------
    def get_weather(self, destination, on_date):
        city = self.resolve_city(destination)
        data = self._file("weather.json")
        if not city or city["name"] not in data["cities"]:
            raise UpstreamAPIError(404, f"No weather data for '{destination}'")
        m = data["cities"][city["name"]][str(on_date.month)]
        return {"status": "success", "destination": city["name"], "date": on_date.isoformat(),
                "temperature": m["temperature"], "condition": m["condition"],
                "basis": "climate_average", "source": self.source}

    # ---------- currency ----------
    def get_exchange_rate(self, from_currency, to_currency):
        data = self._file("currency.json")
        rates = data["rates"]
        if from_currency not in rates or to_currency not in rates:
            raise UpstreamAPIError(400, f"Unsupported currency pair {from_currency}/{to_currency}")
        rate = rates[to_currency] / rates[from_currency]
        return {"status": "success", "from": from_currency, "to": to_currency, "rate": round(rate, 6),
                "retrieved_at": self._now(), "source": self.source}

    # ---------- destination ----------
    def get_destination_info(self, destination):
        city = self.resolve_city(destination)
        if not city or not city.get("is_destination"):
            raise UpstreamAPIError(404, f"No destination guide for '{destination}'")
        return {"status": "success", "destination": city["name"], "country": city["country"],
                "description": city["description"], "local_currency": city["local_currency"],
                "attractions": city["attractions"], "transport": city["transport"],
                "general_info": city["general_info"], "source": self.source}
