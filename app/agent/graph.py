"""Explicit LangGraph definition with observable tool and validation boundaries."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent import nodes as n
from app.agent.state import TravelState


def _route(mapping: dict[str, str], default: str):
    def router(state: TravelState) -> str:
        return state.next_action if state.next_action in mapping else default
    return router


# Edges are also exported for the UI graph view.
GRAPH_EDGES: list[tuple[str, str, str | None]] = [
    ("START", "understand_request", None),
    ("understand_request", "clarify", "missing info"),
    ("understand_request", "plan_trip", "complete"),
    ("clarify", "END", None),
    ("plan_trip", "get_currency", None),
    ("get_currency", "search_flights", None),
    ("search_flights", "validate_flights", None),
    ("validate_flights", "search_flights", "retry"),
    ("validate_flights", "search_hotels", "ok"),
    ("validate_flights", "final_response", "abort"),
    ("search_hotels", "validate_hotels", None),
    ("validate_hotels", "search_hotels", "retry"),
    ("validate_hotels", "get_weather", "ok"),
    ("validate_hotels", "final_response", "abort"),
    ("get_weather", "get_destination_info", None),
    ("get_destination_info", "select_options", None),
    ("select_options", "create_itinerary", "ok"),
    ("select_options", "final_response", "abort"),
    ("create_itinerary", "validate_itinerary", "ok"),
    ("create_itinerary", "final_response", "abort"),
    ("validate_itinerary", "select_options", "repair"),
    ("validate_itinerary", "book_trip", "book"),
    ("validate_itinerary", "final_response", "finish"),
    ("book_trip", "create_itinerary", "alternative"),
    ("book_trip", "final_response", "finish"),
    ("final_response", "END", None),
]


def build_graph():
    g = StateGraph(TravelState)
    for name in ["understand_request", "clarify", "plan_trip", "get_currency", "search_flights", "validate_flights",
                 "search_hotels", "validate_hotels", "get_weather", "get_destination_info", "select_options",
                 "create_itinerary", "validate_itinerary", "book_trip", "final_response"]:
        g.add_node(name, getattr(n, name))

    g.add_edge(START, "understand_request")
    g.add_conditional_edges("understand_request", _route({"clarify": "clarify", "plan": "plan_trip"}, "clarify"),
                            {"clarify": "clarify", "plan": "plan_trip"})
    g.add_edge("clarify", END)
    g.add_edge("plan_trip", "get_currency")
    g.add_edge("get_currency", "search_flights")
    g.add_edge("search_flights", "validate_flights")
    g.add_conditional_edges("validate_flights", _route({"retry": 1, "continue": 1, "abort": 1}, "abort"),
                            {"retry": "search_flights", "continue": "search_hotels", "abort": "final_response"})
    g.add_edge("search_hotels", "validate_hotels")
    g.add_conditional_edges("validate_hotels", _route({"retry": 1, "continue": 1, "abort": 1}, "abort"),
                            {"retry": "search_hotels", "continue": "get_weather", "abort": "final_response"})
    g.add_edge("get_weather", "get_destination_info")
    g.add_edge("get_destination_info", "select_options")
    g.add_conditional_edges("select_options", _route({"abort": 1}, "continue"),
                            {"continue": "create_itinerary", "abort": "final_response"})
    g.add_conditional_edges("create_itinerary", _route({"abort": 1}, "continue"),
                            {"continue": "validate_itinerary", "abort": "final_response"})
    g.add_conditional_edges("validate_itinerary", _route({"repair": 1, "book": 1, "finish": 1}, "finish"),
                            {"repair": "select_options", "book": "book_trip", "finish": "final_response"})
    g.add_conditional_edges("book_trip", _route({"rebuild": 1, "finish": 1}, "finish"),
                            {"rebuild": "create_itinerary", "finish": "final_response"})
    g.add_edge("final_response", END)
    return g.compile()
