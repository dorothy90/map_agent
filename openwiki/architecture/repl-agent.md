---
type: Architecture
title: REPL Verification Agent
description: The REPL agent subsystem that verifies yield hypotheses numerically and graphically against loaded data using an isolated Python worker process per session.
tags: [repl, verification, agent, subprocess, sse]
openwiki:
  roles: [architecture, domain]
  source_paths: [08-YieldAgent/repl_agent/router.py, 08-YieldAgent/repl_agent/agent.py, 08-YieldAgent/repl_agent/session_store.py, 08-YieldAgent/repl_agent/runtime/process.py, 08-YieldAgent/repl_agent/runtime/worker.py, 08-YieldAgent/repl_agent/events.py, 08-YieldAgent/repl_agent/tools.py, 08-YieldAgent/repl_agent/prompts.py]
  symbols: [router, create_session, begin_run, finish_run, cancel_run, ProcessPythonRuntime, worker_main, run_python, EventEmitter, ExecutionResult]
  test_paths: [08-YieldAgent/repl_agent/tests/test_router.py, 08-YieldAgent/repl_agent/tests/test_session_store.py, 08-YieldAgent/repl_agent/tests/test_process_runtime.py, 08-YieldAgent/repl_agent/tests/test_worker.py, 08-YieldAgent/repl_agent/tests/test_events.py]
  invariants: ["Each session runs in an isolated multiprocessing spawn-context process with its own persistent namespace.", "A Python-level error keeps the worker alive; only timeout/cancel/broken-pipe causes runtime_lost.", "REPL sessions close before MongoDB teardown during app shutdown."]
  validation_commands: ["pytest 08-YieldAgent/repl_agent/tests/ -q"]
---

# REPL Verification Agent

The REPL agent (`08-YieldAgent/repl_agent/`) is a semiconductor yield hypothesis verifier. While the main yield-agent supervisor generates hypotheses and root-cause analyses, the REPL agent takes a candidate hypothesis and **confirms or rejects it numerically and graphically** against actual data.

## Architecture

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    Client[Frontend] -->|POST /repl/session| Router[router.py]
    Router --> SS[session_store.py]
    SS -->|spawn| RT[runtime/process.py<br/>ProcessPythonRuntime]
    RT -->|Pipe| Worker[runtime/worker.py<br/>worker_main]
    SS -->|load data| API[DATA_API_BASE_URL<br/>or mock]
    Client -->|POST /repl/chat| Router
    Router -->|begin_run| RR[run_registry.py]
    Router -->|stream| Agent[agent.py<br/>create_agent]
    Agent -->|run_python tool| Tools[tools.py]
    Tools -->|exec code| RT
    RT -->|ExecutionResult| Tools
    Tools -->|custom event| Router
    Router -->|SSE| Client
```

## Responsibility

A user opens a session scoped to a fixed data slice — `lotcd`, a date range (`start`/`end`), and a `fail_name` (OPEN/SHORT/LEAK). The agent loads that slice once into a persistent pandas `df`, then answers free-form questions: descriptive statistics, distributions, outlier detection, statistical tests (t-test/chi-square/ANOVA/Shapiro), regression, and Plotly visualizations. Answers follow a 3-part verdict (judgment → supporting numbers → chart) or a 4-phase EDA template for exploratory requests.

## HTTP endpoints

Mounted via `app.include_router(repl_router, prefix="/repl")` in `agent_server.py`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/repl/health` | Health check |
| POST | `/repl/session` | Create session: load data by `lotcd`/`start`/`end`/`fail_name` (+ optional `column_guideline`); returns `session_id`, `rowcount`, `columns`, `numeric_columns` |
| POST | `/repl/chat` | Chat: `session_id` + free `query`; streams SSE |
| POST | `/repl/runs/{run_id}/cancel` | Cancel an active run (idempotent) |
| DELETE | `/repl/session/{session_id}` | Close session and tear down worker |
| GET | `/repl/mock/data` | Mock data route (debug/standalone testing) |

Session-state errors return structured HTTP errors: 404 `session_not_found`, 409 `session_busy`, 410 `runtime_lost`; worker startup failure → 502 `worker_start_failed` with scrubbed public messages.

## Session lifecycle

`session_store.py` is the authority for session records and runtime ownership:

1. **Create** (`create_session`): fetches rows (external API if `DATA_API_BASE_URL` set, else in-process mock), builds a `pd.DataFrame`, generates a text summary (`df.shape`, dtypes, `df.head()`, schema hints), and spawns an isolated Python worker via `runtime.create_session`. The session record is published **after** the worker acknowledges `{"type":"ready"}`.
2. **States**: `ready` → `running` → `ready` (normal) or `runtime_lost` (terminal).
3. **Chat** (`router.chat`): assigns `run_id`, calls `begin_run` (atomic `ready`→`running`), registers a `RunControl` in `run_registry`, streams. First-turn messages get the session summary prefixed; subsequent turns don't (checkpointer preserves history). `recursion_limit=30` caps tool loops.
4. **Cancel**: signals the in-process `cancel_event` and destructively stops the worker. Cancel is idempotent and always leaves the session `runtime_lost`.
5. **Close** (`DELETE`): removes the record and shuts down the worker (idempotent).
6. **Shutdown all** (`close_all_sessions`): bumps a lifecycle generation, clears records/cancellations, calls `runtime.close_all()`. Called during app shutdown **before** MongoDB teardown.

## Runtime model

`runtime/` implements isolated Python execution in a separate OS process per session:

- **`base.py`** — `PythonRuntime` Protocol (`create_session`, `execute`, `cancel`, `close_session`, `close_all`), `ExecutionResult` (bounded stdout/stderr with truncation flags, `plots`, `execution_time_ms`), `ExecutionError`, `PlotArtifact`. `to_tool_payload` strips `plots` and `traceback`, replaces error messages with a fixed public dictionary.
- **`process.py`** — `ProcessPythonRuntime`: spawns one `multiprocessing` "spawn"-context process per session, communicating over a duplex `Pipe`. Each session is a `_WorkerHandle` with `io_lock`/`state_lock`, terminal-run tracking, and one-owner `_shutdown` (terminate→join→kill→join→close). Arbitrates timeout, cancelled, and `runtime_lost` conditions. A Python-level error does **not** lose the runtime — the same worker stays usable.
- **`worker.py`** — Child process entry `worker_main`: builds the execution namespace (`df`, `query`, `pd`, `np`, `px`, `go`, `sm`, `scipy`), injects `emit_plot(fig)` for Plotly capture, sends `{"type":"ready"}`, then loops on `recv`: `execute` runs `exec(code, namespace)` with stdout/stderr redirected to `BoundedTextBuffer` (50,000-char cap). Variables persist across executions within the same process.

## Event model

`events.py` defines a discriminated-union `ReplEvent`. Every event carries `type`, `run_id`, `thread_id` (=`session_id`), and a monotonically increasing `sequence`. Stream lifecycle:

`RUN_STARTED` → (text and/or tool events) → terminal {`RUN_FINISHED` | `RUN_ERROR` | `RUN_CANCELLED`}.

- **Text**: `TEXT_MESSAGE_START` → `TEXT_MESSAGE_CONTENT` → `TEXT_MESSAGE_END`
- **Tools**: `TOOL_CALL_START` → `TOOL_CALL_ARGS` → `TOOL_CALL_END` → `TOOL_RESULT`
- **Artifacts**: `ARTIFACT` (Plotly JSON spec) — emitted from the `custom` stream mode
- **Terminal**: `RUN_FINISHED` (success), `RunError` (`agent_stream_error`, `execution_timeout`, `runtime_lost`), `RunCancelled` (`execution_cancelled`)

## Tools

Single tool (`tools.py`): `run_python(code)` — executes Python on the session's pre-loaded `df` and persistent namespace. Returns JSON with `status`, `stdout`, `stderr`, `execution_time_ms`, error info (no `plots`/`traceback` in payload). Plots are pushed out-of-band via `get_stream_writer()` as `custom` events. On a destructive status (`timeout`/`cancelled`/`runtime_lost`) it calls `mark_runtime_lost`. Timeout default is 60s. This is `exec()`-based, not a security sandbox (internal-network / single-user assumption).

## Agent

`agent.py` — Lazy singleton built with `langchain.agents.create_agent`, model from `common.get_llm()` (OpenRouter Nemotron), tools=`[run_python]`, `InMemorySaver` checkpointer. Lazy init avoids import-time failures when OpenRouter env is unset.

## Relationship to main system

- Mounted alongside the wiki routers in `agent_server.py`.
- Reuses `common.get_llm()` (same LLM backend as the yield supervisor).
- Does **not** share the yield agent's `MongoDBSaver` checkpointer (uses its own `InMemorySaver`).
- Shutdown ordering: `wiki_queue.stop` → `close_all_sessions` → `mongo.close` (verified by `test_agent_server_lifespan.py`).
- Self-contained: its own session store, run registry, runtime pool, event model, and mock data. No import dependency on the supervisor graph.

## When to consult this page

- Changing the REPL session lifecycle or runtime model.
- Adding new tools or event types.
- Modifying worker process isolation or error handling.

## Validation

```bash
pytest 08-YieldAgent/repl_agent/tests/ -q
```

Tests cover: SSE contract, session lifecycle, process runtime arbitration, worker namespace, event serialization, and app lifespan shutdown ordering.
