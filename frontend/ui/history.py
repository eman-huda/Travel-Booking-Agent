"""Run history and experiment comparison page."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.agent.runner import AgentRunner
from app.failures.registry import get_scenario, list_scenarios
from app.schemas.run import RunRequest
from frontend.ui import components as c

DEMO = ("I want to travel from Islamabad to Dubai on 10 November 2026 for 5 days. My budget is $1500. "
        "I prefer a comfortable hotel and activities that are not too expensive.")


def render(runner: AgentRunner, research: bool) -> None:
    st.markdown("### Run history")
    st.caption("Every run is stored in SQLite. Select runs to inspect, compare or export for GreatTest.")

    with st.expander("Run the full scenario suite"):
        st.write("Runs one request through all 15 scenarios and stores each run.")
        if runner.settings.llm_provider == "openai":
            st.warning("This makes about three OpenAI calls per scenario (roughly 45 in total).")
        text = st.text_area("Request for the suite", DEMO, key="suite_text")
        if st.button("Run all scenarios", type="primary"):
            bar = st.progress(0.0, text="Starting")
            scenarios = list_scenarios()
            for i, sc in enumerate(scenarios, start=1):
                bar.progress((i - 1) / len(scenarios), text=f"Running {sc.label}")
                runner.run(RunRequest(request_text=text, failure_mode=sc.key))
            bar.progress(1.0, text="Suite complete")

    runs = runner.store.list_runs(500)
    if not runs:
        st.info("No runs yet. Plan a trip first, or run the scenario suite above.")
        return
    df = pd.DataFrame(runs)
    df["scenario"] = df["failure_mode"].map(lambda k: get_scenario(k).label)
    df["match"] = df["matches_expectation"].map({1: "yes", 0: "no"})
    view = df.rename(columns={"run_id": "Run", "created_at": "Time", "scenario": "Scenario", "final_status": "Status",
                              "expected_outcome": "Expected", "match": "Match", "tool_calls": "Tool calls",
                              "failures": "Failures", "retries": "Retries", "recovery": "Recovery",
                              "duration_ms": "ms", "model": "Model"})
    view["Time"] = view["Time"].str[:19].str.replace("T", " ")

    f1, f2 = st.columns([3, 1])
    chosen = f1.multiselect("Filter by scenario", sorted(view["Scenario"].unique()))
    if f2.button("Clear history"):
        runner.store.delete_all()
        st.rerun()
    if chosen:
        view = view[view["Scenario"].isin(chosen)]
    cols = ["Run", "Time", "Scenario", "Status", "Expected", "Match", "Tool calls", "Failures", "Retries", "Recovery", "ms", "Model"]
    st.dataframe(view[cols], hide_index=True, width="stretch", height=min(420, 38 + 35 * len(view)))

    total = len(view)
    matched = int((view["Match"] == "yes").sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Runs shown", total)
    m2.metric("Matched expected outcome", f"{matched}/{total}")
    m3.metric("Total retries", int(view["Retries"].sum()))

    st.markdown("#### Compare runs")
    compare = st.multiselect("Choose two to four runs", view["Run"].tolist(), max_selections=4)
    if len(compare) >= 2:
        sub = view.set_index("Run").loc[compare, ["Scenario", "Status", "Tool calls", "Failures", "Retries", "Recovery", "ms"]]
        st.dataframe(sub.T, width="stretch")
        st.bar_chart(sub[["Tool calls", "Failures", "Retries"]])

    st.markdown("#### Inspect a run")
    run_id = st.selectbox("Run", view["Run"].tolist())
    record = runner.store.get(run_id) if run_id else None
    if record:
        t = st.tabs(["Overview", "Agent trace", "Testing"] + (["Research"] if research else []))
        with t[0]:
            c.trip_overview(record)
        with t[1]:
            c.agent_trace(record, research)
        with t[2]:
            c.testing_panel(record)
        if research:
            with t[3]:
                c.research_panel(record)
