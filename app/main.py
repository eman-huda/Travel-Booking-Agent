"""FastAPI backend. GreatTest and other clients drive the agent through this API."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app.agent.graph import GRAPH_EDGES
from app.agent.runner import AgentRunner
from app.config import ConfigurationError, get_settings
from app.failures.registry import get_scenario, list_scenarios
from app.providers.factory import build_services
from app.schemas.run import RunRequest
from app.tools.travel_tools import build_registry

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        _state["runner"] = AgentRunner(settings)
    except ConfigurationError as exc:
        print(f"\nCONFIGURATION ERROR: {exc}\n", file=sys.stderr)
        raise SystemExit(1) from None
    yield
    _state.clear()


app = FastAPI(title="AI Travel Agent Testbed API", version=get_settings().agent_version, lifespan=lifespan,
              description="Run the travel agent, inject failures and export traces for GreatTest. "
                          "All booking tools are sandbox simulations; no real transaction is possible.")


def runner() -> AgentRunner:
    return _state["runner"]


@app.get("/health")
def health():
    return {"status": "ok", **get_settings().public_summary()}


@app.get("/tools")
def tools():
    return build_registry(build_services(get_settings())).describe()


@app.get("/graph")
def graph():
    return {"edges": [{"from": a, "to": b, "condition": c} for a, b, c in GRAPH_EDGES]}


@app.get("/scenarios")
def scenarios():
    return [{"scenario_id": s.scenario_id, "key": s.key, "label": s.label, "target_tool": s.target_tool,
             "kind": s.kind.value, "persistence": s.persistence, "description": s.description,
             "expected_behavior": s.expected_behavior, "expected_outcome": s.expected_outcome}
            for s in list_scenarios()]


@app.post("/runs")
def create_run(request: RunRequest):
    try:
        get_scenario(request.failure_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    record = runner().run(request)
    return {"run_id": record.run_id, "status": record.status, "final_response": record.final_response,
            "metrics": record.metrics.model_dump(), "recovery": record.recovery.model_dump(),
            "expectation": record.expectation.model_dump(), "export_url": f"/runs/{record.run_id}/export"}


@app.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500)):
    return runner().store.list_runs(limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    record = runner().store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.model_dump(mode="json")


@app.get("/runs/{run_id}/export")
def export_run(run_id: str):
    record = runner().store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return JSONResponse(record.to_greattest(),
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})
