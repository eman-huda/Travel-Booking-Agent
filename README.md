# AI Travel Agent Testbed

A runnable, observable AI travel agent built as a research testbed for **GreatTest**, an agent-testing project.

The agent works on **real travel data**: live flights and hotels from Google Flights and Google Hotels (through SerpApi), real exchange rates, real weather, real airports and real destination information. It then **books the selected flight and hotel in a sandbox**. Every booking is simulated in code, so no real booking, payment, cancellation or email can ever happen.

Every tool call is traced, failures can be injected deterministically on top of the real data, and every run can be exported for GreatTest. A mock data mode is kept for offline, fully reproducible tests.

## Contents

1. Project overview
2. Architecture
3. Installation
4. Environment and API key setup
5. Running the backend
6. Running the frontend
7. Running with Docker
8. Available tools
9. Failure injection
10. Testing
11. How the agent works
12. Security and sandbox
13. GreatTest integration
14. Known limitations

## 1. Project overview

A user describes a trip in plain language, for example:

> I want to travel from Islamabad to Dubai for 5 days. My budget is $1500. I prefer a comfortable hotel and activities that are not too expensive.

The agent:

1. extracts origin, destination, dates, travellers, budget, currency and preferences
2. asks a clarification question if anything essential is missing (it never invents dates)
3. plans which tools to call
4. checks exchange rates, searches flights and hotels, gets weather and destination information
5. validates every tool result and recovers from failures with a bounded retry policy
6. selects a flight and hotel against the budget and preferences
7. builds and validates a day-by-day itinerary
8. books the flight and hotel in the sandbox (simulated; on by default)
9. explains the recommendation and states plainly what could not be verified

## 2. Architecture

```
                 +------------------------+        +---------------------------+
  Browser  --->  |  Streamlit dashboard   |        |  FastAPI backend          | <--- GreatTest / scripts
                 |  frontend/             |        |  app/main.py              |
                 +-----------+------------+        +-------------+-------------+
                             |   in-process                      |
                             +----------------+  +---------------+
                                              v  v
                                   +----------------------+
                                   |  AgentRunner         |  app/agent/runner.py
                                   |  LangGraph graph     |  app/agent/graph.py, nodes.py
                                   +----+-----------+-----+
                                        |           |
                        LLM provider    |           |   every tool call
            (OpenAI / Ollama / stub)    v           v
                              +-----------+   +------------------------------+
                              | app/llm/  |   | ToolExecutor                 |  app/tools/executor.py
                              +-----------+   |  allowlist + permission      |  app/safety/
                                              |  input schema validation     |  app/schemas/tools.py
                                              |  failure injection           |  app/failures/
                                              |  timeout                     |
                                              |  output schema validation    |
                                              |  trace event                 |  app/tracing/
                                              +--------------+---------------+
                                                             v
                                              +------------------------------+
                                              | Providers (mock JSON data,   |  app/providers/, data/
                                              | optional read-only real APIs)|
                                              | Sandbox booking ledger       |  app/safety/sandbox.py
                                              +------------------------------+
                                   Runs, traces and exports -> SQLite  app/storage/db.py
```

Project layout:

```
app/
  config.py              settings; clear error if the LLM key is missing
  main.py                FastAPI backend
  agent/                 state.py (TravelState), graph.py, nodes.py, recovery.py,
                         runner.py, context.py, prompts.py, request_parser.py
  tools/                 base.py (ToolSpec), registry.py, executor.py,
                         travel_tools.py, itinerary.py, errors.py
  schemas/               travel.py, tools.py (tool I/O), trace.py, run.py
  llm/                   base.py, openai_provider.py, ollama_provider.py,
                         stub_provider.py, factory.py
  providers/             base.py, mock_provider.py, real_providers.py, factory.py
  safety/                permissions.py, sandbox.py, redaction.py
  failures/              scenarios.py, registry.py, injector.py
  tracing/               tracer.py, logging_config.py
  storage/db.py          SQLite run history
data/                    flights.json, hotels.json, destinations.json,
                         weather.json, currency.json
frontend/                streamlit_app.py, ui/components.py, ui/history.py, ui/styles.py
scripts/                 run_scenario_suite.py
tests/                   pytest suite (81 tests)
```

## 3. Installation

Requires Python 3.12 or later.

```bash
cd travel-agent-testbed
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Environment and API key setup

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Then edit `.env` in the project root. You need two things: a travel data source and an LLM.

### Travel data

| Setting | Meaning |
|---|---|
| `DATA_MODE=live` (default) | Real data. Needs `SERPAPI_API_KEY`. |
| `DATA_MODE=mock` | Local sample data. Offline, no key, fully reproducible. Used by the test suite. |
| `SERPAPI_API_KEY` | Free key: create an account at https://serpapi.com, then copy the key from https://serpapi.com/manage-api-key. The free plan includes 250 searches a month. |
| `SERPAPI_GL` | Google country for results (default `pk`). |
| `LIVE_CACHE_TTL_HOURS` | Identical live searches are reused for this many hours (default 6) to save quota. `0` disables the cache. |

A normal live run uses **3 SerpApi searches** (outbound flights, return flights for the chosen outbound, hotels). Repeating the same request within the cache window uses none, so you can run all 15 failure scenarios on the same request for about 3 searches in total.

### LLM

| Provider | Settings in `.env` | Key |
|---|---|---|
| Groq (free) | `LLM_PROVIDER=groq`, `GROQ_API_KEY=gsk_...`, `GROQ_MODEL=openai/gpt-oss-120b` | Free from https://console.groq.com/keys |
| OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=sk-...`, `LLM_MODEL=gpt-4.1-mini` | Paid |
| Ollama (local) | `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=llama3.1` | None |
| Stub (offline) | `LLM_PROVIDER=stub` | None |

If a model is retired by the provider, change only the model line. Keys are read as `SecretStr`, never shown in the UI or API, never written to traces or the cache, and redacted from logs. If a required key is missing, the API refuses to start and the UI shows exactly what to fix.

Fully offline (no keys at all): `DATA_MODE=mock` and `LLM_PROVIDER=stub`.

Other useful settings:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_TOOL_RETRIES` | 1 | Retries per logical tool call |
| `MAX_TOTAL_RETRIES` | 6 | Retry cap for a whole run |
| `TOOL_TIMEOUT_SECONDS` | 30 | Real timeout applied to every tool call |
| `INJECTED_TIMEOUT_DELAY_SECONDS` | 0.5 | How long an injected timeout waits before failing |
| `STALE_DATA_MAX_AGE_HOURS` | 24 | Freshness limit for search results |
| `FAILURE_MODE` | none | Default failure mode for API and CLI runs |

### What is real and what is simulated (live mode)

| Part | Source | Real? |
|---|---|---|
| Flights (outbound and return) | Google Flights via SerpApi | Real, as shown to shoppers at search time |
| Hotels | Google Hotels via SerpApi | Real listings and nightly prices, with a link to each listing |
| City lookup and airports | Open-Meteo geocoding and the OurAirports dataset (`data/airports.csv`) | Real |
| Local currency of a country | GeoNames country table (`data/countries.csv`) | Real |
| Exchange rates | open.er-api.com | Real daily rates |
| Weather | Open-Meteo forecast (up to about 15 days ahead), otherwise the observed weather on the same date last year | Real |
| Description | Wikipedia | Real |
| Transport and safety notes | Wikivoyage | Real |
| Attractions and their prices | Wikivoyage listings for the city and its districts, with OpenStreetMap as a backup | Real; prices are shown as published and are not added to the total |
| Flight and hotel bookings | Sandbox ledger in `app/safety/sandbox.py` | **Simulated. No real booking is possible.** |

## 5. Running the backend

```bash
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status and safe configuration summary (no secrets) |
| `GET /tools` | Tool catalogue with input and output JSON schemas |
| `GET /graph` | Agent graph edges |
| `GET /scenarios` | All failure scenarios with expected behaviour |
| `POST /runs` | Run the agent |
| `GET /runs` | Run history |
| `GET /runs/{run_id}` | Full run record |
| `GET /runs/{run_id}/export` | GreatTest export JSON |

Example run:

```bash
curl -X POST http://localhost:8000/runs -H "Content-Type: application/json" -d '{
  "request_text": "I want to travel from Islamabad to Dubai on 12 November 2026 for 5 days. My budget is $1500. I prefer a comfortable hotel.",
  "failure_mode": "hotel_timeout",
  "simulate_booking": false
}'
```

## 6. Running the frontend

```bash
streamlit run frontend/streamlit_app.py
```

Open http://localhost:8501. The backend does not need to be running: the dashboard runs the same `AgentRunner` in-process, so its progress indicator follows real graph execution, and it writes to the same SQLite database as the API.

Dashboard tabs:

| Tab | Shows |
|---|---|
| Trip overview | Route, dates, travellers, budget, estimated total, recommendation, agent notes, cost breakdown, the agent's tool plan |
| Flight options | Validated flights with the selected one highlighted |
| Hotel options | Validated hotels; selected highlighted, unavailable or rejected ones faded |
| Itinerary | Day-by-day timeline and validation result |
| Agent trace | Execution graph coloured by status, step-by-step board with retries, every tool call with input, output, duration and status |
| Testing | Active scenario, expected and actual behaviour, recovery, retries, final status, match |
| Research (Research mode only) | Metrics, state transitions, full event log, initial and final state, LLM calls, injected-failure records |

The Run history view lists every stored run, compares two to four runs side by side, runs the full scenario suite and exports any run.

## 7. Running with Docker

```bash
docker compose up --build
```

API at http://localhost:8000 and dashboard at http://localhost:8501. Secrets are read from `.env` at runtime and are never baked into the image. Runs are stored in `./var` on the host. If you have no OpenAI key, set `LLM_PROVIDER=stub` in `.env`, otherwise the API container exits with the configuration message.

## 8. Available tools

| Tool | Permission | Purpose |
|---|---|---|
| `search_flights` | READ_ONLY | Round-trip flights; price is USD per passenger |
| `search_hotels` | READ_ONLY | Hotels for the stay, optionally capped by a nightly USD price |
| `get_weather` | READ_ONLY | Expected weather for a destination and date |
| `get_exchange_rate` | READ_ONLY | Rate between two ISO currency codes |
| `get_destination_info` | READ_ONLY | Description, attractions (with published prices where available), transport, general information |
| `get_return_flights` | READ_ONLY | Return-leg options for the chosen outbound flight (live searches return the outbound first) |
| `create_itinerary` | READ_ONLY | Builds the day-by-day itinerary |
| `validate_itinerary` | READ_ONLY | Checks dates, missing days, ordering, flight and hotel dates, duplicates, currency and budget |
| `book_flight` | SIMULATED_WRITE | Sandbox only; returns a `TEST-FLIGHT-nnn` ID |
| `reserve_hotel` | SIMULATED_WRITE | Sandbox only; returns a `TEST-HOTEL-nnn` ID |

Each tool has a name, description, Pydantic input schema (unknown arguments are rejected), Pydantic output schema, permission and handler. `GET /tools` returns the JSON schemas.

In live mode the agent works for any city that has a nearby airport. Mock mode covers Islamabad, Lahore and Karachi as origins, and Dubai, Istanbul, Doha and Kuala Lumpur as destinations, with invented schedules and fictional hotels.

Failure injection works the same way in both modes: it is applied on top of the real responses in live mode.

## 9. Failure injection

The application runs normally with `FAILURE_MODE=none`. A failure only happens when you explicitly select one.

How to activate:

* **UI:** turn on Test mode in the sidebar, then choose from Failure injection. With Test mode off the mode is always `none`.
* **API:** send `"failure_mode": "<key>"` in the `POST /runs` body.
* **CLI:** `python scripts/run_scenario_suite.py --stub --only hotel_timeout`

| ID | Key | Target tool | What is injected | Persistence | Expected outcome |
|---|---|---|---|---|---|
| FS-00 | `none` | | Nothing | | completed |
| FS-01 | `flight_timeout` | search_flights | Timeout | First call only | recovered |
| FS-02 | `hotel_timeout` | search_hotels | Timeout | First call only | recovered |
| FS-03 | `empty_flights` | search_flights | Empty list | Every call | graceful_failure |
| FS-04 | `empty_hotels` | search_hotels | Empty list | First call only | recovered (nightly price cap relaxed) |
| FS-05 | `malformed_flights` | search_flights | Wrong types, missing fields | First call only | recovered |
| FS-06 | `malformed_hotels` | search_hotels | Wrong types, missing fields | First call only | recovered |
| FS-07 | `api_error` | search_hotels | HTTP 503 | Every call | graceful_failure |
| FS-08 | `invalid_argument` | search_flights | Corrupted arguments | First call only | recovered |
| FS-09 | `partial_result` | search_hotels | Truncated result, availability unknown | Every call | completed_with_warnings |
| FS-10 | `stale_data` | search_flights | Results 72 hours old | First call only | recovered |
| FS-11 | `contradictory_data` | search_hotels | Same hotel ID with different price and availability | Every call | completed_with_warnings |
| FS-12 | `duplicate_results` | search_flights | Duplicate records | Every call | recovered (deduplicated) |
| FS-13 | `wrong_currency` | search_hotels | Prices in AED instead of USD | Every call | recovered (converted) |
| FS-14 | `booking_unavailable` | reserve_hotel | Room unavailable | First call only | recovered (alternative hotel) |

Rules the injector follows:

* **Deterministic:** the same scenario and attempt number always produce the same result. Nothing is random.
* **Logged:** every injected failure is written to the trace with its scenario ID, kind, target tool, attempt and effect.
* **Invisible to the agent:** the agent receives an ordinary error such as `Hotel search service did not respond within 5.0s` or `HTTP 503`. The words "injected" and the scenario ID never reach it.
* **Booking unavailable** needs the booking step, so it turns on sandbox booking automatically and replaces the call before it reaches the sandbox ledger, so no phantom booking is recorded.
* Injected timeouts wait `INJECTED_TIMEOUT_DELAY_SECONDS` (0.5 seconds by default) rather than the full timeout so experiments stay quick. Set it to 5 for realistic durations.

## 10. Testing

```bash
pytest
```

The suite runs offline: mock data, the stub LLM, and recorded SerpApi-style responses for the live provider tests. It needs no keys and no network, and uses no API quota.

| File | Covers |
|---|---|
| `test_tools.py` | Normal flight and hotel search, weather, exchange, destination info, schema rejection, unknown tools, tracing |
| `test_failures.py` | Malformed output detection, timeout handling, empty results, determinism of every scenario, injection invisible to the agent |
| `test_recovery.py` | Retry behaviour, maximum retry limit, zero-retry setting, every recovery path, all 15 scenarios against expected outcomes |
| `test_itinerary.py` | Valid itinerary, missing days, duplicates, impossible ordering, budget, flight date, hotel availability, currency |
| `test_safety.py` | Sandbox booking, no network during simulated writes, REAL_WRITE rejection, sandbox output check, payment data removal, redaction |
| `test_config.py` | Missing and placeholder key errors, key never exposed, log redaction, OpenAI Responses API call shape |
| `test_agent_e2e.py` | Normal end-to-end run, hotel timeout end-to-end recovery, clarification on missing date, UI overrides, persistence and export |
| `test_api.py` | Health, catalogues, run and export, invalid failure mode |
| `test_ui.py` | Headless Streamlit run in normal and failure modes, plus history page |
| `test_live_providers.py` | SerpApi flight, return and hotel mapping, city and airport resolution, Wikivoyage parsing, Overpass outage handling, live end-to-end run with sandbox booking, key never cached |

Scenario suite with a comparison table:

```bash
python scripts/run_scenario_suite.py --stub                 # fully offline (mock data, stub LLM)
python scripts/run_scenario_suite.py                        # your .env settings: live data and your LLM
python scripts/run_scenario_suite.py --stub --export exports/
```

### The section 30 demo

1. Start the dashboard and leave the default request: "I want to travel from Islamabad to Dubai for 5 days. My budget is $1500. I prefer a comfortable hotel and activities that are not too expensive."
2. Select Run agent. The agent asks for your departure date because none was given, and makes no tool calls.
3. Answer, for example "Departure date: 12 November 2026", and select Continue. The agent checks currency, searches real flights and hotels, checks weather, gets destination information, selects options, fetches the return flight, builds and validates the itinerary, books both in the sandbox and shows the full trace.
4. Turn on Test mode, choose Hotel Search Timeout and select Run agent again with the same request.
5. Open Agent trace: the hotel search fails, the validation node records a timeout and retries, the second search succeeds and the run continues. The Testing tab shows expected and actual behaviour side by side.

## 11. How the agent works

### Graph

```
START -> understand_request --(missing info)--> clarify -> END
                 | (complete)
                 v
            plan_trip -> get_currency -> search_flights -> validate_flights
                                              ^                 |  retry / ok / abort
                                              +----- retry -----+
                                                                | ok
                                         search_hotels <------- +
                                              |    ^ retry
                                              v    |
                                         validate_hotels --abort--> final_response
                                              | ok
                         get_weather -> get_destination_info -> select_options <--- repair (over budget)
                                                                     |                    |
                                                          select_return_flight            |
                                                                     |                    |
                                                              create_itinerary -> validate_itinerary
                                                                     ^                    | book (optional)
                                                                     +--- alternative --- book_trip
                                                                                          |
                                                              final_response <------------+ -> END
```

* **LLM steps:** `understand_request` (extraction), `plan_trip` (which tools to use; unknown or write tools are rejected in code), `final_response` (wording only).
* **Tool steps:** search nodes only call their tool. They never decide what to do with the result.
* **Validation steps:** `validate_flights` and `validate_hotels` check the result and choose continue, retry or abort. They detect errors, stale data, partial results, duplicates, contradictions, route and date mismatches and currency mismatches.
* **Deterministic steps:** selection, itinerary building and itinerary validation are code, so results are reproducible and auditable.
* **Return leg:** Google Flights returns outbound options first. After choosing the outbound flight, `select_return_flight` calls `get_return_flights` and records the confirmed round-trip fare. If it fails, the itinerary is still built but the return schedule is flagged as unverified.

### State

`TravelState` (`app/agent/state.py`) is a Pydantic model holding the request, extracted constraints, plan, flights, hotels, selections, weather, exchange rates, destination information, itinerary, validation results, bookings, retry counters, notices, unverified items, recovery actions, tool trace and final response.

### Recovery policy

| Problem | Response |
|---|---|
| Timeout | Retry once |
| Malformed output | Rejected by the output schema, then retried |
| Invalid arguments | Rejected by the input schema, arguments rebuilt from validated state, then retried |
| API error | Retry once, then report the failure without inventing data |
| Empty flights | Report clearly; route and dates are critical and are never relaxed |
| Empty hotels | Remove the non-critical nightly price cap, search again, still check the total against the budget |
| Stale data | Request fresh results |
| Partial result | Retry once, then continue with confirmed records only and flag the rest |
| Contradictory data | Retry once, then exclude the conflicting records and flag them; never pick one silently |
| Duplicates | Remove before comparing |
| Wrong currency | Convert with `get_exchange_rate` and note it |
| Over budget | Choose the lowest-cost combination once |
| Booking unavailable | Choose the next ranked hotel, rebuild and revalidate the itinerary, reserve again |

Retries are counted per logical call (`MAX_TOOL_RETRIES`) and per run (`MAX_TOTAL_RETRIES`), so there are no infinite loops.

### Final status values

| Status | Meaning |
|---|---|
| `completed` | No problems detected |
| `recovered` | Problems detected and fully recovered |
| `completed_with_warnings` | Itinerary produced, but something could not be verified |
| `graceful_failure` | No itinerary; the reason is stated clearly |
| `needs_clarification` | Essential information missing; a question was asked |
| `error` | Unexpected internal error (recorded, never hidden) |

### Design decisions to note

* `get_currency` runs before flight search (rather than after hotels) so a non-USD budget can be converted before the hotel price cap is calculated.
* "5 days" means five calendar days and four hotel nights. "4 nights" is treated as five days.
* Travellers are taken as 1 only when the user writes in the first person ("I", "me"). For "we" or "family" without a number, the agent asks.
* The final response always ends with a code-generated list of unverified items, so the model cannot omit them.

## 12. Security and sandbox

* **Allowlist:** only tools in `TOOL_POLICY` (`app/safety/permissions.py`) can be registered or executed. Any other name returns `unknown_tool`.
* **Permissions:** tools are `READ_ONLY` or `SIMULATED_WRITE`. `REAL_WRITE` exists only so that registering such a tool raises `SafetyViolation`. A tool whose declared permission differs from policy is also rejected.
* **Output check:** a simulated write must return `environment: "sandbox"` or the result is rejected.
* **No real side effects:** SerpApi and every other data source are read-only search APIs with no booking capability. The only booking service is an in-memory ledger (`SandboxBookingService`) with no payment, airline, hotel, email or network client. A test blocks all network access and confirms bookings still work.
* **Validated arguments:** every input is validated by Pydantic with unknown fields forbidden. The LLM cannot run shell commands or Python; it only produces text and JSON that the agent code validates.
* **Secrets and personal data:** the API key is a `SecretStr`, never logged or returned. Logs and exports are redacted for keys, tokens and passwords. Card numbers (Luhn-checked) and CVV codes are removed from the request before it reaches the LLM.
* **UI labelling:** sandbox bookings are labelled as simulated with no real transaction.

## 13. GreatTest integration

Every run is stored in SQLite (`var/runs.db`) and can be exported three ways:

* the Export run JSON button in the dashboard
* `GET /runs/{run_id}/export`
* `python scripts/run_scenario_suite.py --export exports/`

Export format (`schema_version: greattest.run.v1`):

```json
{
  "schema_version": "greattest.run.v1",
  "run_id": "run-20261007-172823-bc5dbf",
  "agent_version": "1.0.0",
  "scenario_id": "FS-02",
  "failure_mode": "hotel_timeout",
  "created_at": "...",
  "llm": {"provider": "openai", "model": "..."},
  "initial_state": {},
  "tool_trace": [],
  "events": [],
  "final_state": {},
  "final_response": "...",
  "errors": [],
  "recovery": {"attempted": true, "result": "success", "actions": []},
  "metrics": {"tool_calls": 8, "tool_failures": 1, "failures_detected": 1, "retries": 1, "llm_calls": 3, "duration_ms": 0},
  "expectation": {"expected_outcome": "recovered", "actual_outcome": "recovered", "matches": true},
  "status": "recovered"
}
```

Each `tool_trace` entry records `run_id`, `timestamp`, `node`, `tool_name`, `arguments`, `result`, `status`, `duration_ms`, `error` and `retry_number`. The `injected_failure` field records what was injected; it is visible to researchers and GreatTest but was never shown to the agent. That separation lets GreatTest compare what went wrong against what the agent noticed and did.

`events` is the full timeline: node starts and ends, LLM calls, tool calls and recovery decisions. `recovery.actions` lists each detected problem with the node, tool, issue, action and detail.

To check behavioural consistency, run the same request and scenario several times (for example with the suite script) and compare the exported tool sequences, recovery actions and outcomes.

## 14. Known limitations

* Live prices are what Google shows a shopper at search time. They are not guaranteed, bookable fares, and they change, so live runs are not exactly repeatable. Use mock mode (or the live cache) when you need identical runs.
* Fares are searched per person. For groups, seat availability for everyone is not confirmed and the agent says so.
* Attraction prices are kept as the text Wikivoyage publishes (for example "AED 3"). They are shown but not converted or added to the total.
* Public OpenStreetMap (Overpass) servers are often overloaded. They are only a backup to Wikivoyage, and failures are reported as unverified.
* City lookups (geocoding) happen inside tools and are cached; they are infrastructure, not separate agent decisions, so they do not appear as their own tool calls.
* The SerpApi integration was tested against recorded responses in the SerpApi format, and every free data source was tested live. The first run with your SerpApi key is the first live SerpApi call.
* The live OpenAI and Groq paths were tested against fake clients only.
* Docker files were syntax-checked but not built in the development environment.
