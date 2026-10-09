"""Provider interfaces. Each provider returns raw JSON-like dicts, as an external API would.

Tools validate these dicts against explicit output schemas, so a real API can replace a
mock provider without changing the agent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date


class FlightProvider(ABC):
    @abstractmethod
    def search_flights(self, origin: str, destination: str, departure_date: date, return_date: date, passengers: int) -> dict: ...

    @abstractmethod
    def get_return_flights(self, provider_ref: str | None, origin: str, destination: str, departure_date: date,
                           return_date: date, passengers: int, flight_id: str | None = None) -> dict: ...


class HotelProvider(ABC):
    @abstractmethod
    def search_hotels(self, destination: str, check_in: date, check_out: date, guests: int, budget: float | None) -> dict: ...


class WeatherProvider(ABC):
    @abstractmethod
    def get_weather(self, destination: str, on_date: date) -> dict: ...


class ExchangeRateProvider(ABC):
    @abstractmethod
    def get_exchange_rate(self, from_currency: str, to_currency: str) -> dict: ...


class DestinationProvider(ABC):
    @abstractmethod
    def get_destination_info(self, destination: str) -> dict: ...

    @abstractmethod
    def resolve_city(self, name: str) -> dict | None: ...

    @abstractmethod
    def known_cities(self) -> list[str]: ...
