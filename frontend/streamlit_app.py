"""Streamlit dashboard for the AI Travel Agent Testbed.

Run with:  streamlit run frontend/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.agent.nodes import NODE_LABELS  # noqa: E402
from app.agent.runner import AgentRunner  # noqa: E402
from app.config import ConfigurationError, get_settings  # noqa: E402
from app.failures.registry import get_scenario, scenario_options  # noqa: E402
from app.safety.sandbox import SANDBOX_LABEL  # noqa: E402
from app.schemas.run import RequestOverrides, RunRequest  # noqa: E402
from frontend.ui import components as c  # noqa: E402
from frontend.ui import history  # noqa: E402
from frontend.ui.styles import CSS  # noqa: E402

st.set_page_config(page_title="AI Travel Agent Testbed", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

DEMO_REQUEST = ("I want to travel from Islamabad to Dubai for 5 days. My budget is $1500. "
                "I prefer a comfortable hotel and activities that are not too expensive.")
EXPECTED_STEPS = 13


@st.cache_resource(show_spinner=False)
def get_runner() -> AgentRunner:
    return AgentRunner(get_settings())


try:
    runner = get_runner()
except ConfigurationError as exc:
    st.error("The app cannot start because the configuration is incomplete.")
    st.markdown(f"**What to fix:** {exc}")
    st.code("cp .env.example .env\n# then edit .env:\nOPENAI_API_KEY=sk-...\nLLM_MODEL=gpt-4.1-mini\n"
            "# or run offline without a key:\nLLM_PROVIDER=stub", language="bash")
    st.stop()

ss = st.session_state
ss.setdefault("request_text", DEMO_REQUEST)
ss.setdefault("record", None)
ss.setdefault("auto_run", False)
if "pending_request_text" in ss:  # set by the clarification form before the widget renders
    ss.request_text = ss.pop("pending_request_text")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    page = st.radio("View", ["Plan a trip", "Run history"], horizontal=True, label_visibility="collapsed")
    st.markdown("### Trip details")
    st.caption("Optional. Anything filled in here overrides what the agent reads from your message.")
    origin = st.text_input("Origin", placeholder="From your message")
    destination = st.text_input("Destination", placeholder="From your message")
    d1, d2 = st.columns(2)
    dep = d1.date_input("Departure", value=None, format="DD/MM/YYYY")
    ret = d2.date_input("Return", value=None, format="DD/MM/YYYY")
    t1, t2 = st.columns(2)
    travelers = t1.selectbox("Travellers", ["Auto"] + list(range(1, 10)))
    currency = t2.selectbox("Currency", ["Auto", "USD", "AED", "PKR", "EUR", "GBP", "QAR", "MYR"])
    budget = st.number_input("Budget", min_value=0.0, step=100.0, value=0.0, help="Leave at 0 to use the budget in your message.")
    prefs = st.text_input("Preferences", placeholder="e.g. no early flights, direct only")

    st.markdown("### Testing controls")
    test_mode = st.toggle("Test mode", value=False, help="Turn on to enable failure injection.")
    options = scenario_options()
    label = st.selectbox("Failure injection", list(options), disabled=not test_mode,
                         help="Deterministic. A failure only happens when you select one here.")
    failure_mode = options[label] if test_mode else "none"
    if test_mode and failure_mode != "none":
        st.caption(get_scenario(failure_mode).description)
    simulate_booking = st.checkbox("Simulate sandbox booking", value=False,
                                   help="Adds book_flight and reserve_hotel. They are simulations only.")
    if simulate_booking or (test_mode and get_scenario(failure_mode).requires_booking):
        st.markdown(f'<span class="sandbox">{SANDBOX_LABEL}</span>', unsafe_allow_html=True)
    research_mode = st.toggle("Research mode", value=False, help="Show graph state, events, raw tool I/O and injected failures.")
    run_clicked = st.button("Run agent", type="primary", width="stretch")

c.header(runner.settings.llm_provider, runner.settings.active_model, test_mode, research_mode)

if page == "Run history":
    history.render(runner, research_mode)
    st.stop()

# ------------------------------------------------------------------ request
st.text_area("Describe your trip", key="request_text", height=96,
             help="Say where from, where to, when, for how long, how many people, your budget and preferences.")


def build_request() -> RunRequest:
    text = ss.request_text.strip()
    if prefs.strip():
        text += f" Preferences: {prefs.strip()}."
    ov = RequestOverrides(
        origin=origin.strip() or None, destination=destination.strip() or None, departure_date=dep, return_date=ret,
        travelers=None if travelers == "Auto" else int(travelers),
        budget=budget if budget > 0 else None, currency=None if currency == "Auto" else currency)
    return RunRequest(request_text=text, overrides=ov, failure_mode=failure_mode, simulate_booking=simulate_booking)


def execute(request: RunRequest) -> None:
    progress = st.progress(0.0)
    with st.status("Starting agent", expanded=True) as status:
        seen = 0
        for event in runner.iter_run(request):
            if event["type"] == "node_start":
                seen += 1
                status.update(label=f"{event['label']}...")
                status.write(f"{event['label']}")
                progress.progress(min(seen / EXPECTED_STEPS, 0.97))
            elif event["type"] == "result":
                record = event["record"]
                ss.record = record
                state = "error" if record.status in ("graceful_failure", "error") else "complete"
                status.update(label=f"Finished: {c.STATUS_TEXT.get(record.status, record.status)}", state=state, expanded=False)
        progress.progress(1.0)


if run_clicked or ss.auto_run:
    ss.auto_run = False
    if len(ss.request_text.strip()) < 3:
        st.warning("Describe the trip first.")
    else:
        execute(build_request())

record = ss.record
if record is None:
    st.info("Describe a trip and select Run agent. Turn on Test mode in the sidebar to inject a failure.")
    st.stop()

if record.status == "needs_clarification":
    st.markdown(f'<div class="note warning">{c.e(record.final_response)}</div>', unsafe_allow_html=True)
    with st.form("clarify"):
        answer = st.text_input("Your answer", placeholder="e.g. Departure date: 12 November 2026")
        if st.form_submit_button("Continue with this detail", type="primary") and answer.strip():
            ss.pending_request_text = f"{ss.request_text.strip()} {answer.strip()}"
            ss.auto_run = True
            st.rerun()

tabs = ["Trip overview", "Flight options", "Hotel options", "Itinerary", "Agent trace", "Testing"]
if research_mode:
    tabs.append("Research")
t = st.tabs(tabs)
with t[0]:
    c.trip_overview(record)
with t[1]:
    c.flight_options(record)
with t[2]:
    c.hotel_options(record)
with t[3]:
    c.itinerary(record)
with t[4]:
    c.agent_trace(record, research_mode)
with t[5]:
    c.testing_panel(record)
if research_mode:
    with t[6]:
        c.research_panel(record)
