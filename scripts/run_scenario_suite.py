"""Run the same request through every failure scenario and print a comparison table.

Usage:
    python scripts/run_scenario_suite.py                # uses LLM_PROVIDER from .env
    python scripts/run_scenario_suite.py --stub         # fully offline, deterministic (mock data)
    python scripts/run_scenario_suite.py --only hotel_timeout api_error
    python scripts/run_scenario_suite.py --export exports/   # also write GreatTest JSON per run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.runner import AgentRunner  # noqa: E402
from app.config import ConfigurationError, Settings  # noqa: E402
from app.failures.registry import list_scenarios  # noqa: E402
from app.schemas.run import RunRequest  # noqa: E402

DEFAULT_REQUEST = ("I want to travel from Islamabad to Dubai on 10 November 2026 for 5 days. My budget is $1500. "
                   "I prefer a comfortable hotel and activities that are not too expensive.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true", help="fully offline: deterministic LLM and mock travel data")
    ap.add_argument("--only", nargs="*", help="scenario keys to run")
    ap.add_argument("--request", default=DEFAULT_REQUEST)
    ap.add_argument("--export", type=Path, help="directory for GreatTest JSON exports")
    args = ap.parse_args()

    overrides = {"llm_provider": "stub", "data_mode": "mock"} if args.stub else {}
    try:
        runner = AgentRunner(Settings(**overrides, log_level="WARNING"))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 2

    scenarios = [s for s in list_scenarios() if not args.only or s.key in args.only]
    rows, mismatches = [], 0
    for sc in scenarios:
        rec = runner.run(RunRequest(request_text=args.request, failure_mode=sc.key))
        ok = rec.expectation.matches
        mismatches += 0 if ok else 1
        rows.append((rec.run_id, sc.scenario_id, sc.label, rec.metrics.failures_detected, rec.metrics.retries,
                     rec.recovery.result, rec.status, sc.expected_outcome, "yes" if ok else "NO"))
        if args.export:
            args.export.mkdir(parents=True, exist_ok=True)
            (args.export / f"{rec.run_id}.json").write_text(json.dumps(rec.to_greattest(), indent=2, default=str))

    header = ("Run", "ID", "Scenario", "Failures", "Retries", "Recovery", "Actual", "Expected", "Match")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    line = lambda r: " | ".join(str(v).ljust(w) for v, w in zip(r, widths))
    print(line(header))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(line(r))
    print(f"\n{len(rows) - mismatches}/{len(rows)} scenarios matched their expected outcome.")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
