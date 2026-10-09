"""Real location resolution: Open-Meteo geocoding, the OurAirports airport list and GeoNames currencies."""
from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

from app.providers.live.http import CachedHTTP
from app.tools.errors import UpstreamAPIError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=4)
def _airports(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
    return rows


@lru_cache(maxsize=4)
def _currencies(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return {r["country_code"]: r for r in csv.DictReader(f)}


class LiveGeo:
    """Resolves a city name or IATA code to a city with coordinates, country, currency and nearest airport."""

    def __init__(self, http: CachedHTTP, data_dir: Path):
        self.http = http
        self.airports = _airports(str(Path(data_dir) / "airports.csv"))
        self.countries = _currencies(str(Path(data_dir) / "countries.csv"))
        self._memo: dict[str, dict | None] = {}

    def known_cities(self) -> list[str]:
        return sorted({a["city"] for a in self.airports if a["type"] == "large" and a["city"]})

    def airports_near(self, lat: float, lon: float, country_code: str | None = None,
                      radius_km: float = 80, limit: int = 3) -> list[dict]:
        """Large airports in the same country serving a city (for example KUL and SZB), nearest first."""
        pool = [a for a in self.airports if a["type"] == "large" and (not country_code or a["country_code"] == country_code)]
        scored = sorted(((haversine_km(lat, lon, a["lat"], a["lon"]), a) for a in pool), key=lambda x: x[0])
        return [a for d, a in scored if d <= radius_km][:limit]

    def nearest_airport(self, lat: float, lon: float) -> dict | None:
        best = None
        for kind, radius in (("large", 150), ("medium", 150), (None, 400)):
            pool = [a for a in self.airports if kind is None or a["type"] == kind]
            scored = sorted(((haversine_km(lat, lon, a["lat"], a["lon"]), a) for a in pool), key=lambda x: x[0])
            if scored and scored[0][0] <= radius:
                best = scored[0][1]
                break
        return best

    def resolve_city(self, name: str) -> dict | None:
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in self._memo:
            return self._memo[key]
        city = None
        by_code = next((a for a in self.airports if a["iata"].lower() == key), None) if len(key) == 3 else None
        if by_code:
            lat, lon, cc = by_code["lat"], by_code["lon"], by_code["country_code"]
            label, tz = by_code["city"] or by_code["name"], None
            airport = by_code
        else:
            body, _ = self.http.get_json(GEOCODE_URL, service="Geocoding service",
                                         params={"name": name.strip(), "count": 1, "language": "en", "format": "json"})
            results = (body or {}).get("results") or []
            if not results:
                self._memo[key] = None
                return None
            g = results[0]
            lat, lon, cc, label, tz = g["latitude"], g["longitude"], g.get("country_code", ""), g["name"], g.get("timezone")
            airport = self.nearest_airport(lat, lon)
        country = self.countries.get(cc, {})
        if airport:
            near = [airport] if by_code else (self.airports_near(lat, lon, cc) or [airport])
            city = {
                "name": label, "iata": near[0]["iata"], "airport_name": near[0]["name"],
                "iata_list": [a["iata"] for a in near],
                "country": country.get("country", cc), "country_code": cc,
                "local_currency": country.get("currency"), "lat": lat, "lon": lon, "timezone": tz,
                "is_destination": True,
            }
        self._memo[key] = city
        return city

    def require(self, name: str) -> dict:
        city = self.resolve_city(name)
        if not city:
            raise UpstreamAPIError(404, f"Could not find '{name}' or an airport near it")
        return city
