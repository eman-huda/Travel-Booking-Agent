"""Builds the allowlisted tool registry with explicit schemas and handlers."""
from __future__ import annotations

from dataclasses import dataclass

from app.providers.base import (
    DestinationProvider,
    ExchangeRateProvider,
    FlightProvider,
    HotelProvider,
    WeatherProvider,
)
from app.safety.permissions import Permission
from app.safety.sandbox import SandboxBookingService
from app.schemas import tools as s
from app.tools.base import ToolSpec
from app.tools.itinerary import build_itinerary, validate
from app.tools.registry import ToolRegistry


@dataclass
class Services:
    flights: FlightProvider
    hotels: HotelProvider
    weather: WeatherProvider
    exchange: ExchangeRateProvider
    destinations: DestinationProvider
    bookings: SandboxBookingService


def build_registry(sv: Services) -> ToolRegistry:
    reg = ToolRegistry()
    R, W = Permission.READ_ONLY, Permission.SIMULATED_WRITE

    reg.register(ToolSpec(
        "search_flights", "Search round-trip flights between two cities for given dates. Prices are USD per passenger.",
        s.SearchFlightsInput, s.FlightSearchOutput, R,
        lambda a: sv.flights.search_flights(a.origin, a.destination, a.departure_date, a.return_date, a.passengers),
        critical=True))
    reg.register(ToolSpec(
        "search_hotels", "Search hotels in a city for check-in/check-out dates, optionally capped by a USD nightly price.",
        s.SearchHotelsInput, s.HotelSearchOutput, R,
        lambda a: sv.hotels.search_hotels(a.destination, a.check_in, a.check_out, a.guests, a.budget),
        critical=True))
    reg.register(ToolSpec(
        "get_weather", "Get the expected weather for a destination on a date.",
        s.WeatherInput, s.WeatherOutput, R,
        lambda a: sv.weather.get_weather(a.destination, a.date)))
    reg.register(ToolSpec(
        "get_exchange_rate", "Get the conversion rate between two ISO currency codes.",
        s.ExchangeRateInput, s.ExchangeRateOutput, R,
        lambda a: sv.exchange.get_exchange_rate(a.from_currency, a.to_currency)))
    reg.register(ToolSpec(
        "get_destination_info", "Get a destination description, attractions with sample costs, transport and general travel information.",
        s.DestinationInput, s.DestinationOutput, R,
        lambda a: sv.destinations.get_destination_info(a.destination)))
    reg.register(ToolSpec(
        "create_itinerary", "Build a day-by-day itinerary from the selected flight, hotel, preferences and attractions.",
        s.CreateItineraryInput, s.CreateItineraryOutput, R,
        lambda a: {"status": "success", "itinerary": build_itinerary(a).model_dump(mode="json")},
        critical=True))

    def _validate(a: s.ValidateItineraryInput) -> dict:
        issues = validate(a)
        return {"status": "success", "valid": not any(i.severity == "error" for i in issues),
                "issues": [i.model_dump() for i in issues]}

    reg.register(ToolSpec(
        "validate_itinerary", "Check an itinerary for date, budget, ordering, duplicate and consistency problems.",
        s.ValidateItineraryInput, s.ValidateItineraryOutput, R, _validate, critical=True))
    reg.register(ToolSpec(
        "book_flight", "SANDBOX ONLY. Simulate booking a flight. No real booking or payment happens.",
        s.BookFlightInput, s.SimulatedBookingOutput, W,
        lambda a: sv.bookings.book_flight(a.flight_id, a.passengers)))
    reg.register(ToolSpec(
        "reserve_hotel", "SANDBOX ONLY. Simulate reserving a hotel room. No real reservation or payment happens.",
        s.ReserveHotelInput, s.SimulatedBookingOutput, W,
        lambda a: sv.bookings.reserve_hotel(a.hotel_id, a.check_in, a.check_out, a.guests)))
    return reg
