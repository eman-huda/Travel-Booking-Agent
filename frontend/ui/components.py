"""Rendering helpers for the travel dashboard."""
from __future__ import annotations

import html
import json
from datetime import date, datetime

import pandas as pd
import streamlit as st

from app.agent.graph import GRAPH_EDGES
from app.agent.nodes import NODE_LABELS
from app.agent.state import TravelState
from app.failures.registry import get_scenario
from app.safety.sandbox import SANDBOX_LABEL
from app.schemas.run import RunRecord
from frontend.ui.styles import AMBER, CLEAR, HORIZON, MIST, SIGNAL, SLATE, STATUS_COLOURS, STATUS_TEXT

e = html.escape
KIND_ICON = {"flight": "✈", "transfer": "🚕", "hotel": "🏨", "attraction": "📍", "meal": "🍽", "free": "☀"}
STAMP = {"success": ("✓", CLEAR), "failure": ("✗", SIGNAL), "retrying": ("↻", AMBER), "skipped": ("–", SLATE),
         "info": ("i", HORIZON)}


def badge(text: str, status: str | None = None, solid: bool = False) -> str:
    colour = STATUS_COLOURS.get(status or text, SLATE)
    style = f"background:{colour};" if solid else f"color:{colour};"
    return f'<span class="badge{" solid" if solid else ""}" style="{style}">{e(text)}</span>'


def state_of(record: RunRecord) -> TravelState:
    return TravelState.model_validate(record.final_state)


def fmt_date(d: date | None) -> str:
    return d.strftime("%a %d %b %Y") if d else "Not set"


def money(v: float | None, cur: str = "USD") -> str:
    return "Not set" if v is None else f"{v:,.2f} {cur}"


def export_button(record: RunRecord, key: str) -> None:
    st.download_button("Export run JSON", data=json.dumps(record.to_greattest(), indent=2, default=str),
                       file_name=f"{record.run_id}.json", mime="application/json", key=key,
                       help="GreatTest export: initial state, tool trace, final state, recovery and status.")


# ------------------------------------------------------------------ header
def header(provider: str, model: str, test_mode: bool, research_mode: bool, data_mode: str = "live") -> None:
    modes = [badge("Live data" if data_mode == "live" else "Mock data", "success" if data_mode == "live" else "info"),
             badge(f"{provider}: {model}", "info")]
    modes.append(badge("Test mode on", "retrying") if test_mode else badge("Normal mode", "success"))
    if research_mode:
        modes.append(badge("Research view", "info"))
    st.markdown(f"""
    <div class="tb-header">
      <div><div class="tb-title">AI Travel Agent Testbed</div>
      <div class="tb-sub">Plan a trip with an observable LangGraph agent, then inject failures to see how it detects
      and recovers from them. Runs are stored for GreatTest.</div></div>
      <div class="tb-modes">{''.join(modes)}</div>
    </div>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ overview
def trip_overview(record: RunRecord) -> None:
    s = state_of(record)
    status = record.status
    duration = f"{(s.return_date - s.departure_date).days + 1} days" if s.departure_date and s.return_date else "Not set"
    total = s.itinerary.total_estimated_cost if s.itinerary else None
    total_text = money(total)
    if total and s.currency != "USD" and s.budget_rate:
        total_text += f" (about {total * s.budget_rate:,.0f} {s.currency})"
    st.markdown(f"""
    <div class="pass">
      <div class="card-top"><span class="pass-k">Run {e(record.run_id)}</span>{badge(STATUS_TEXT.get(status, status), status, solid=True)}</div>
      <div class="pass-route"><div class="pass-city">{e(s.origin or "Origin?")}</div><div class="pass-arrow"></div>
      <div class="pass-city">{e(s.destination or "Destination?")}</div></div>
      <div class="pass-grid">
        <div><div class="pass-k">Departure</div><div class="pass-v">{fmt_date(s.departure_date)}</div></div>
        <div><div class="pass-k">Return</div><div class="pass-v">{fmt_date(s.return_date)}</div></div>
        <div><div class="pass-k">Duration</div><div class="pass-v">{duration}</div></div>
        <div><div class="pass-k">Travellers</div><div class="pass-v">{s.travelers or "Not set"}</div></div>
        <div><div class="pass-k">Budget</div><div class="pass-v">{money(s.budget, s.currency)}</div></div>
        <div><div class="pass-k">Estimated total</div><div class="pass-v">{total_text}</div></div>
      </div>
    </div>""", unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### Recommendation")
        st.markdown(f'<div class="answer">{e(record.final_response)}</div>', unsafe_allow_html=True)
        if s.bookings:
            st.markdown(f'<div style="margin-top:.8rem"><span class="sandbox">{e(SANDBOX_LABEL)}</span></div>', unsafe_allow_html=True)
            for kind, b in s.bookings.items():
                st.markdown(f"{badge(b.get('status', '?'), b.get('status'))} {e(kind.title())}: "
                            f"`{e(str(b.get('booking_id') or 'no ID'))}` {e(b.get('message', ''))}", unsafe_allow_html=True)
    with right:
        notices = [n for n in s.notices]
        if notices:
            st.markdown("#### Notes from the agent")
            for n in notices:
                st.markdown(f'<div class="note {n.level}">{e(n.message)}</div>', unsafe_allow_html=True)
        if s.itinerary:
            st.markdown("#### Cost breakdown")
            rows = [{"Item": k.title(), "USD": round(v, 2)} for k, v in s.itinerary.cost_breakdown.items()]
            rows.append({"Item": "Total", "USD": s.itinerary.total_estimated_cost})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"USD": st.column_config.NumberColumn("USD", format="%.2f")})
        if s.plan:
            with st.expander("The agent's tool plan"):
                if s.plan_rationale:
                    st.caption(s.plan_rationale)
                for i, p in enumerate(s.plan, start=1):
                    st.markdown(f"{i}. `{p.tool}` {e(p.reason)}")
    export_button(record, key=f"exp-overview-{record.run_id}")


# ------------------------------------------------------------------ flights & hotels
def flight_options(record: RunRecord) -> None:
    s = state_of(record)
    if not s.flights:
        st.info("No validated flight options for this run. See the Agent trace tab for what happened.")
        return
    sel = s.selected_flight.flight_id if s.selected_flight else None
    st.caption(f"{len(s.flights)} validated options. Prices are per person for the round trip, in USD, "
               f"as shown at search time.")
    for f in sorted(s.flights, key=lambda x: x.price):
        chosen = f.flight_id == sel
        shown = s.selected_flight if chosen else f
        back = (f"{e(shown.return_flight_number)} {shown.return_departure:%d %b %H:%M} → {shown.return_arrival:%d %b %H:%M}"
                if shown.return_departure else "Return options are fetched for the selected flight only")
        airports = f", {e(f.origin_airport)} to {e(f.destination_airport)}" if f.origin_airport else ""
        f = shown
        st.markdown(f"""
        <div class="card {'selected' if chosen else ''}">
          <div class="card-top"><div><span class="card-title">{e(f.airline)} {e(f.flight_number)}</span>
            {badge('Selected', 'info', solid=True) if chosen else ''}</div><div class="price">{money(f.price, f.currency)}</div></div>
          <div class="legs">
            <div><b>Out</b> {f.departure:%d %b %H:%M} → {f.arrival:%H:%M}<br><span class="card-meta">{e(f.duration)}, {'direct' if f.stops == 0 else f'{f.stops} stop'}</span></div>
            <div><b>Back</b> {back}<br><span class="card-meta">{e(f.cabin)}{airports}</span></div>
          </div>
        </div>""", unsafe_allow_html=True)


def hotel_options(record: RunRecord) -> None:
    s = state_of(record)
    if not s.hotels:
        st.info("No validated hotel options for this run. See the Agent trace tab for what happened.")
        return
    sel = s.selected_hotel.hotel_id if s.selected_hotel else None
    tried = set(s.tried_hotel_ids)
    st.caption("Prices are per room per night in USD. Faded hotels were unavailable or could not be reserved.")
    cols = st.columns(2, gap="medium")
    for i, h in enumerate(sorted(s.hotels, key=lambda x: (-x.rating, x.nightly_price))):
        chosen = h.hotel_id == sel
        unavailable = h.availability is not True or h.hotel_id in tried
        avail = "Available" if h.availability else "Unavailable" if h.availability is False else "Unconfirmed"
        if h.hotel_id in tried:
            avail = "Could not be reserved"
        chips = "".join(f'<span class="chip">{e(a)}</span>' for a in h.amenities)
        with cols[i % 2]:
            st.markdown(f"""
            <div class="card {'selected' if chosen else ''} {'muted' if unavailable and not chosen else ''}">
              <div class="card-top"><div><span class="card-title">{e(h.name)}</span>
                {badge('Selected', 'info', solid=True) if chosen else ''}</div><div class="price">{money(h.nightly_price, h.currency)}</div></div>
              <div class="card-meta">{e(h.location)}, rated {h.rating:g} of 5, {e(avail.lower())}</div>
              <div class="chips">{chips}</div>
              {f'<div class="card-meta" style="margin-top:.4rem"><a href="{e(h.link)}" target="_blank" rel="noopener">View the real listing</a></div>' if h.link else ''}
            </div>""", unsafe_allow_html=True)
    if s.excluded_hotel_ids:
        st.warning("Excluded because the service returned conflicting data: " + ", ".join(s.excluded_hotel_ids))


# ------------------------------------------------------------------ itinerary
def itinerary(record: RunRecord) -> None:
    s = state_of(record)
    if not s.itinerary:
        st.info("No itinerary was built for this run.")
        return
    v = s.validation_results.get("itinerary")
    if v:
        if v.valid:
            st.success("The itinerary passed validation (dates, budget, ordering, duplicates and consistency).")
        else:
            st.error("The itinerary failed validation. Issues are listed below.")
        for issue in v.issues:
            st.markdown(f'<div class="note {"error" if issue.severity == "error" else issue.severity}">'
                        f'{e(issue.code)}: {e(issue.message)}</div>', unsafe_allow_html=True)
    blocks = []
    for d in s.itinerary.days:
        slots = "".join(
            f'<div class="slot"><span class="slot-time">{e(it.time)}</span><span>{KIND_ICON.get(it.kind, "")}</span>'
            f'<span><span class="slot-what">{e(it.activity)}</span>'
            f'{f"<br><span class=slot-note>{e(it.notes)}</span>" if it.notes else ""}</span></div>'
            for it in d.items)
        weather = f'<div class="day-weather">{e(d.weather)}</div>' if d.weather else ""
        blocks.append(f'<div class="day"><div><div class="day-label">Day {d.day_number}</div>'
                      f'<div class="day-date">{d.date:%A %d %B}</div><div class="day-weather">{e(d.title)}</div>{weather}</div>'
                      f'<div>{slots}</div></div>')
    st.markdown("".join(blocks), unsafe_allow_html=True)
    with st.expander("Assumptions behind the estimate"):
        for a in s.itinerary.assumptions:
            st.markdown(f"- {e(a)}")


# ------------------------------------------------------------------ trace
def _node_statuses(record: RunRecord) -> dict[str, str]:
    out: dict[str, str] = {}
    for ev in record.events:
        if ev["event_type"] == "node" and ev["status"] != "started":
            prev = out.get(ev["node"])
            # once a node has failed or retried, keep that visible unless it later succeeded
            out[ev["node"]] = ev["status"] if prev != "success" or ev["status"] != "skipped" else prev
    return out


def graph_view(record: RunRecord) -> None:
    statuses = _node_statuses(record)
    colour = {"success": CLEAR, "failure": SIGNAL, "retrying": AMBER, "skipped": "#9AA8B2"}
    lines = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.28;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 margin="0.12,0.05"];',
             'edge [fontname="Helvetica" fontsize=8 color="#9AA8B2"];',
             'START [shape=circle label="" width=.15 style=filled fillcolor="#172B3A"];',
             'END [shape=doublecircle label="" width=.12 style=filled fillcolor="#172B3A"];']
    visits: dict[str, int] = {}
    for ev in record.events:
        if ev["event_type"] == "node" and ev["status"] == "started":
            visits[ev["node"]] = visits.get(ev["node"], 0) + 1
    for node, label in NODE_LABELS.items():
        st_ = statuses.get(node)
        if visits.get(node, 0) > 1:
            label = f"{label} (x{visits[node]})"
        fill = colour.get(st_, "#FFFFFF")
        font = "white" if st_ in ("success", "failure", "retrying", "skipped") else "#5F7280"
        style = "rounded,filled" if st_ else "rounded,dashed"
        lines.append(f'{node} [label="{label}" fillcolor="{fill}" fontcolor="{font}" style="{style}" color="{fill if st_ else MIST}"];')
    visited = [ev["node"] for ev in record.events if ev["event_type"] == "node" and ev["status"] == "started"]
    taken = set(zip(visited, visited[1:]))
    for a, b, cond in GRAPH_EDGES:
        on_path = (a, b) in taken or (a == "START" and b == visited[:1][0] if visited else False) or \
                  (b == "END" and visited and visited[-1] == a)
        attrs = [f'label="{cond}"'] if cond else []
        if on_path:
            attrs += ['color="#172B3A"', 'penwidth=1.6']
        if cond in ("retry", "repair", "alternative"):
            attrs.append('style=dashed')
        lines.append(f'{a} -> {b} [{" ".join(attrs)}];')
    lines.append("}")
    st.graphviz_chart("\n".join(lines), width="stretch")


def step_board(record: RunRecord) -> None:
    rows = []
    for ev in record.events:
        if ev["event_type"] == "node" and ev["status"] != "started":
            sym, col = STAMP.get(ev["status"], ("•", SLATE))
            label = NODE_LABELS.get(ev["node"], ev["node"])
            msg = e(ev.get("message") or "")
            dur = f'{ev["duration_ms"]:.0f} ms' if ev.get("duration_ms") is not None else ""
            rows.append(f'<div class="board-row"><div class="stamp" style="background:{col}">{sym}</div>'
                        f'<div><div class="board-name">{e(label)}</div><div class="board-msg">{msg}</div></div>'
                        f'<div class="board-time">{dur}</div></div>')
        elif ev["event_type"] == "recovery":
            rows.append(f'<div class="board-sub">↳ {e(ev.get("message") or "")}</div>')
    st.markdown(f'<div class="board">{"".join(rows)}</div>', unsafe_allow_html=True)


def tool_calls(record: RunRecord, research: bool) -> None:
    calls = [ev for ev in record.events if ev["event_type"] == "tool"]
    if not calls:
        st.caption("No tool calls were made in this run.")
        return
    for i, ev in enumerate(calls, start=1):
        status = ev["status"]
        dur = f'{ev["duration_ms"]:.0f} ms' if ev.get("duration_ms") is not None else ""
        title = f'{i}. {ev["tool_name"]} (attempt {ev["retry_number"] + 1}) {"✓" if status == "success" else "✗"} {status.upper()} {dur}'
        with st.expander(title, expanded=False):
            st.caption(f"Called from node: {ev['node']}")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("Input")
                st.json(ev.get("arguments") or {}, expanded=False)
            with c2:
                st.markdown("Output")
                if ev.get("error"):
                    st.error(ev["error"])
                else:
                    st.json(ev.get("result") or {}, expanded=False)
            if research and ev.get("injected_failure"):
                st.warning("Researcher only: this failure was injected and is not visible to the agent.")
                st.json(ev["injected_failure"])


def agent_trace(record: RunRecord, research: bool) -> None:
    c1, c2 = st.columns([2, 3], gap="large")
    with c1:
        st.markdown("#### Execution graph")
        st.caption("Green succeeded, amber retried, red failed, grey skipped. Dashed boxes were not visited.")
        graph_view(record)
    with c2:
        st.markdown("#### Step by step")
        step_board(record)
    st.markdown("#### Tool calls")
    tool_calls(record, research)


# ------------------------------------------------------------------ testing
def testing_panel(record: RunRecord) -> None:
    sc = get_scenario(record.failure_mode)
    ex = record.expectation
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("#### Active scenario")
        st.markdown(f"""<div class="kv">
          <div class="k">Scenario</div><div class="v">{e(sc.scenario_id)} {e(sc.label)}</div>
          <div class="k">Target tool</div><div class="v">{e(sc.target_tool or 'none')}</div>
          <div class="k">Persistence</div><div class="v">{e(sc.persistence)}</div>
          <div class="k">What is injected</div><div class="v">{e(sc.description)}</div>
          <div class="k">Expected behaviour</div><div class="v">{e(sc.expected_behavior)}</div>
          <div class="k">Expected outcome</div><div class="v">{badge(ex.expected_outcome, ex.expected_outcome)}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("#### Actual behaviour")
        st.markdown(f"""<div class="kv">
          <div class="k">Final status</div><div class="v">{badge(STATUS_TEXT.get(record.status, record.status), record.status, solid=True)}</div>
          <div class="k">Matches expectation</div><div class="v">{badge('Yes' if ex.matches else 'No', 'success' if ex.matches else 'failure')}</div>
          <div class="k">Failures detected</div><div class="v">{record.metrics.failures_detected}</div>
          <div class="k">Recovery attempted</div><div class="v">{'Yes' if record.recovery.attempted else 'No'}</div>
          <div class="k">Recovery result</div><div class="v">{badge(record.recovery.result, record.recovery.result)}</div>
          <div class="k">Retries</div><div class="v">{record.metrics.retries}</div>
          <div class="k">Tool calls (failed)</div><div class="v">{record.metrics.tool_calls} ({record.metrics.tool_failures})</div>
        </div>""", unsafe_allow_html=True)
    if record.recovery.actions:
        st.markdown("#### Recovery actions")
        st.dataframe(pd.DataFrame(record.recovery.actions)[["node", "tool", "issue", "action", "detail"]],
                     hide_index=True, width="stretch")


# ------------------------------------------------------------------ research
def research_panel(record: RunRecord) -> None:
    m = record.metrics
    cols = st.columns(6)
    for col, (k, v) in zip(cols, [("Tool calls", m.tool_calls), ("Tool failures", m.tool_failures),
                                  ("Detected", m.failures_detected), ("Retries", m.retries),
                                  ("LLM calls", m.llm_calls), ("Duration", f"{m.duration_ms:.0f} ms")]):
        col.metric(k, v)
    st.markdown("#### State transitions")
    nodes = [ev for ev in record.events if ev["event_type"] == "node" and ev["status"] != "started"]
    st.code(" → ".join(f'{n["node"]}[{n["status"]}]' for n in nodes), language=None)
    st.markdown("#### Event log")
    df = pd.DataFrame([{"time": ev["timestamp"][11:23], "type": ev["event_type"], "node": ev["node"],
                        "tool": ev.get("tool_name"), "status": ev["status"], "retry": ev["retry_number"],
                        "ms": round(ev["duration_ms"], 1) if ev.get("duration_ms") else None,
                        "detail": ev.get("error") or ev.get("message"),
                        "injected": (ev.get("injected_failure") or {}).get("effect")} for ev in record.events])
    st.dataframe(df, hide_index=True, width="stretch", height=360)
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("Initial state"):
            st.json(record.initial_state, expanded=False)
    with c2:
        with st.expander("Final state"):
            st.json(record.final_state, expanded=False)
    llm = [ev for ev in record.events if ev["event_type"] == "llm"]
    with st.expander(f"LLM calls ({len(llm)})"):
        for ev in llm:
            st.markdown(f'**{ev["tool_name"]}** in `{ev["node"]}`: {ev["status"]}, {ev.get("duration_ms") or 0:.0f} ms')
            if ev.get("error"):
                st.error(ev["error"])
            elif ev.get("result"):
                st.json(ev["result"], expanded=False)
    export_button(record, key=f"exp-research-{record.run_id}")
