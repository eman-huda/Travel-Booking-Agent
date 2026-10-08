"""SQLite persistence for experiment runs."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.schemas.run import RunRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_request TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    failure_mode TEXT NOT NULL,
    final_status TEXT NOT NULL,
    expected_outcome TEXT,
    matches_expectation INTEGER,
    tool_calls INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    retries INTEGER NOT NULL,
    recovery TEXT NOT NULL,
    duration_ms REAL,
    llm_provider TEXT,
    model TEXT,
    final_response TEXT,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
"""


class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def save(self, r: RunRecord) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.run_id, r.created_at.isoformat(), r.user_request, r.scenario_id, r.failure_mode, r.status,
                 r.expectation.expected_outcome, int(r.expectation.matches), r.metrics.tool_calls,
                 r.metrics.failures_detected, r.metrics.retries, r.recovery.result, r.metrics.duration_ms,
                 r.llm_provider, r.model, r.final_response, r.model_dump_json()))

    def list_runs(self, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{k: row[k] for k in row.keys() if k != "record_json"} for row in rows]

    def get(self, run_id: str) -> RunRecord | None:
        with self._connect() as con:
            row = con.execute("SELECT record_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return RunRecord.model_validate_json(row["record_json"]) if row else None

    def delete_all(self) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM runs")
