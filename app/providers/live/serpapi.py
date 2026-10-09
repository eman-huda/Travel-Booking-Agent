"""Live flights and hotels from Google Flights and Google Hotels (read-only).

Two interchangeable search services are supported, chosen in .env:

    SEARCH_PROVIDER=serpapi    (default)  https://serpapi.com
    SEARCH_PROVIDER=searchapi             https://www.searchapi.io

Put the key for whichever service you use in SERPAPI_API_KEY.

Both services only return search results. Neither has any booking capability, so nothing here can
create a reservation. Booking stays in the in-memory sandbox (app/safety/sandbox.py).
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BASE_DIR
from app.providers.base import FlightProvider, HotelProvider
from app.providers.live.geo import LiveGeo
from app.providers.live.http import CachedHTTP
from app.tools.errors import UpstreamAPIError

NO_RESULTS_MARKERS = ("hasn't returned any results", "no results", "returned no results")
MAX_FLIGHTS = 8
MAX_HOTELS = 15

# Differences between the two services, kept in one place.
BACKENDS = {
    "serpapi": {
        "url": "https://serpapi.com/search.json",
        "label": "SerpApi",
        "round_trip": {"type": "1"},
        "hotel_price": ("rate_per_night", "extracted_lowest"),
        "hotel_rating": "overall_rating",
        "hotel_max_price": "max_price",
    },
    "searchapi": {
        "url": "https://www.searchapi.io/api/v1/search",
        "label": "SearchApi",
        "round_trip": {"flight_type": "round_trip"},
        "hotel_price": ("price_per_night", "extracted_price"),
        "hotel_rating": "rating",
        "hotel_max_price": "price_max",
    },
}


class _SearchBackendSetting(BaseSettings):
    """Reads SEARCH_PROVIDER from the environment or .env (kept here so only this file changes)."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")
    search_provider: Literal["serpapi", "searchapi"] = "serpapi"


def _airport_dt(airport: dict) -> datetime:
    """SerpApi gives time='YYYY-MM-DD HH:MM'; SearchApi gives date='YYYY-MM-DD' and time='HH:MM'."""
    raw = f"{airport['date']} {airport['time']}" if airport.get("date") else airport["time"]
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M%p"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise UpstreamAPIError(502, f"Unrecognised flight time format: {raw!r}")


def _fmt(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _short_id(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


class SerpApiTravel(FlightProvider, HotelProvider):
    def __init__(self, http: CachedHTTP, geo: LiveGeo, api_key: str, gl: str = "pk", hl: str = "en",
                 backend: str | None = None):
        self.http, self.geo, self._key, self.gl, self.hl = http, geo, api_key, gl, hl
        self.backend_name = backend or _SearchBackendSetting().search_provider
        self.backend = BACKENDS[self.backend_name]
        self.source = f"google-flights-via-{self.backend_name}"

    def _search(self, params: dict, service: str) -> tuple[dict, str]:
        body, fetched_at = self.http.get_json(self.backend["url"], service=service,
                                              params={**params, "api_key": self._key, "hl": self.hl, "gl": self.gl})
        if isinstance(body, dict) and body.get("error"):
            msg = str(body["error"])
            if any(m in msg.lower() for m in NO_RESULTS_MARKERS):
                return {}, fetched_at
            raise UpstreamAPIError(502, f"{service} ({self.backend['label']}): {msg[:160]}")
        return body or {}, fetched_at

    # ------------------------------------------------------------- flights
    def _flight_params(self, o: dict, d: dict, departure_date: date, return_date: date) -> dict:
        # Several airports can serve one city; Google Flights accepts a comma-separated list.
        return {"engine": "google_flights", "departure_id": ",".join(o["iata_list"]), "arrival_id": ",".join(d["iata_list"]),
                "outbound_date": departure_date.isoformat(), "return_date": return_date.isoformat(),
                **self.backend["round_trip"], "adults": "1", "currency": "USD"}

    def search_flights(self, origin, destination, departure_date, return_date, passengers):
        o, d = self.geo.require(origin), self.geo.require(destination)
        body, fetched_at = self._search(self._flight_params(o, d, departure_date, return_date), "Flight search service")
        offers = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        flights = []
        for offer in offers:
            segs = offer.get("flights") or []
            price = offer.get("price")
            if not segs or not isinstance(price, (int, float)) or price <= 0:
                continue
            first, last = segs[0], segs[-1]
            airlines = list(dict.fromkeys(s.get("airline", "") for s in segs if s.get("airline")))
            numbers = [s.get("flight_number", "") for s in segs]
            total = offer.get("total_duration") or sum(s.get("duration", 0) for s in segs)
            token = offer.get("departure_token")
            flights.append({
                "flight_id": _short_id("GF-", token or "|".join(numbers), departure_date.isoformat()),
                "airline": " / ".join(airlines) or "Unknown airline",
                "flight_number": " + ".join(n for n in numbers if n),
                "origin": o["name"], "destination": d["name"],
                "origin_airport": first["departure_airport"]["id"], "destination_airport": last["arrival_airport"]["id"],
                "departure": _airport_dt(first["departure_airport"]).isoformat(),
                "arrival": _airport_dt(last["arrival_airport"]).isoformat(),
                "duration": _fmt(int(total)), "duration_minutes": int(total),
                "return_flight_number": None, "return_departure": None, "return_arrival": None,
                "price": float(price), "currency": "USD", "stops": len(segs) - 1,
                "cabin": first.get("travel_class") or "Economy", "provider_ref": token,
            })
            if len(flights) >= MAX_FLIGHTS:
                break
        return {"status": "success", "flights": flights, "retrieved_at": fetched_at,
                "total_results": len(offers), "source": self.source}

    def get_return_flights(self, provider_ref, origin, destination, departure_date, return_date, passengers, flight_id=None):
        if not provider_ref:
            raise UpstreamAPIError(400, "This flight has no reference for looking up return options")
        o, d = self.geo.require(origin), self.geo.require(destination)
        params = {**self._flight_params(o, d, departure_date, return_date), "departure_token": provider_ref}
        body, fetched_at = self._search(params, "Return flight search service")
        offers = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        options = []
        for offer in offers:
            segs = offer.get("flights") or []
            price = offer.get("price")
            if not segs or not isinstance(price, (int, float)) or price <= 0:
                continue
            total = offer.get("total_duration") or sum(s.get("duration", 0) for s in segs)
            options.append({
                "return_flight_number": " + ".join(s.get("flight_number", "") for s in segs if s.get("flight_number")),
                "airline": " / ".join(dict.fromkeys(s.get("airline", "") for s in segs if s.get("airline"))) or "Unknown airline",
                "return_departure": _airport_dt(segs[0]["departure_airport"]).isoformat(),
                "return_arrival": _airport_dt(segs[-1]["arrival_airport"]).isoformat(),
                "duration_minutes": int(total), "stops": len(segs) - 1,
                "price": float(price), "currency": "USD",
            })
        return {"status": "success", "options": options, "retrieved_at": fetched_at, "source": self.source}

    # ------------------------------------------------------------- hotels
    def search_hotels(self, destination, check_in, check_out, guests, budget):
        d = self.geo.require(destination)
        params = {"engine": "google_hotels", "q": f"hotels in {d['name']}, {d['country']}",
                  "check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(),
                  "adults": str(guests), "currency": "USD"}
        if budget:
            params[self.backend["hotel_max_price"]] = str(int(budget))
        body, fetched_at = self._search(params, "Hotel search service")
        props = body.get("properties") or []
        price_group, price_field = self.backend["hotel_price"]
        hotels = []
        for p in props:
            rate = (p.get(price_group) or {}).get(price_field)
            if not isinstance(rate, (int, float)) or rate <= 0:
                continue  # no price listed for these dates
            nearby = (p.get("nearby_places") or [{}])[0].get("name")
            location = p.get("description") or (f"Near {nearby}" if nearby else d["name"])
            hotels.append({
                "hotel_id": _short_id("GH-", p.get("property_token") or p.get("name", "")),
                "name": p.get("name", "Unnamed property"), "city": d["name"], "location": str(location)[:120],
                "rating": float(p.get(self.backend["hotel_rating"]) or 0.0), "nightly_price": float(rate), "currency": "USD",
                "amenities": list(p.get("amenities") or [])[:8],
                "availability": True,  # Google Hotels listed a price for these dates at search time
                "room_type": p.get("type", "hotel").replace("_", " ").title(),
                "hotel_class": p.get("extracted_hotel_class"), "reviews": p.get("reviews"), "link": p.get("link"),
            })
            if len(hotels) >= MAX_HOTELS:
                break
        return {"status": "success", "hotels": hotels, "retrieved_at": fetched_at,
                "total_results": len(props), "source": f"google-hotels-via-{self.backend_name}"}
