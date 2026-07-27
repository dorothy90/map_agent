# REPL Agent Runtime and Event Modernization Design

**Date:** 2026-07-27

**Status:** Awaiting written-spec review

## Objective

Modernize `08-YieldAgent/repl_agent` without replacing its FastAPI, LangChain,
LangGraph, Vite, React, or Plotly foundations. The change introduces an AG-UI-compatible
event contract, structured Python execution results, a process-backed stateful Python
runtime with hard timeout and cancellation, and a reducer-driven analysis UI.

The first delivery covers the previously agreed P0 and P1 scope only.

## Scope

### Included

- AG-UI-compatible event names and fields implemented locally without AG-UI SDK dependencies.
- Structured run, tool-call, tool-result, artifact, cancellation, and error events.
- A `PythonRuntime` boundary between the LangChain tool and Python execution.
- One persistent Python worker process per REPL session.
- Hard execution timeout and user cancellation by terminating the worker process.
- Explicit `runtime_lost` session state after worker termination or failure.
- Same-session concurrency rejection.
- Explicit session close and application-shutdown cleanup for worker processes.
- Bounded stdout and stderr capture.
- React event parsing, reducer state management, and analysis cards.
- Structured Plotly artifacts while retaining the current Plotly renderer.
- Backend unit tests, real worker integration tests, React tests, and a real end-to-end scenario.

### Excluded

- Database persistence and event replay.
- Authentication and user ownership.
- Docker, E2B, Daytona, or other remote sandboxes.
- Automatic worker recreation after timeout or cancellation.
- DataFrame lineage, result anchoring, and report generation.
- CopilotKit UI or AG-UI SDK adoption.
- Last-expression auto-display.

## Architectural Approach

Implementation proceeds as tested vertical slices:

1. Define and adopt the event and result contracts.
2. Introduce the runtime interface and process-backed implementation.
3. Move the React client to reducer-managed analysis runs.
4. Verify the complete FastAPI-to-browser path with real data, a real LLM call, and real tool execution.

This preserves the existing agent behavior while replacing transport and execution internals behind
explicit boundaries.

```text
React
  |-- useReplStream
  |-- replReducer
  `-- AnalysisCard
          |
          | POST /repl/chat (SSE)
          v
FastAPI router
          |
          | ReplEvent stream
          v
LangChain create_agent
          |
          | run_python
          v
PythonRuntime Protocol
          |
          v
ProcessPythonRuntime
          |
          | IPC
          v
Per-session Python worker
  |-- df and query
  |-- persistent user variables
  |-- pandas/numpy/scipy/statsmodels/plotly
  `-- ExecutionResult
```

## Backend Components

### Event Contract

A focused backend event module defines discriminated Pydantic models. Every event contains:

- `type`
- `run_id`
- `thread_id`
- `sequence`

The event set is:

- `RUN_STARTED`
- `TEXT_MESSAGE_START`
- `TEXT_MESSAGE_CONTENT`
- `TEXT_MESSAGE_END`
- `TOOL_CALL_START`
- `TOOL_CALL_ARGS`
- `TOOL_CALL_END`
- `TOOL_RESULT`
- `ARTIFACT`
- `RUN_FINISHED`
- `RUN_ERROR`
- `RUN_CANCELLED`

`RUN_CANCELLED` and `ARTIFACT` are local extensions. They retain the common envelope so a later
AG-UI SDK migration can translate them without changing the agent or runtime.

Sequences start at one and increase monotonically within a run. The router owns sequence allocation;
the agent and runtime do not generate sequence numbers.

### Structured Execution Result

Python execution returns a typed value rather than a plain stdout string:

```python
class ExecutionResult(BaseModel):
    status: Literal[
        "success",
        "error",
        "timeout",
        "cancelled",
        "runtime_lost",
    ]
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: ExecutionError | None = None
    plots: list[PlotArtifact] = Field(default_factory=list)
    execution_time_ms: int
```

`ExecutionError` contains a stable error code, exception name, safe message, and traceback.
`PlotArtifact` contains an artifact ID, MIME type, and Plotly JSON specification.
Each captured output stream is capped at 50,000 characters. Additional text is discarded and the
corresponding `*_truncated` flag is set.

The LangChain tool serializes the non-plot portion of `ExecutionResult` to JSON for the LLM. Plot
artifacts are also emitted through the existing LangGraph custom stream path and converted into
`ARTIFACT` events by the router.

### Python Runtime Boundary

The agent tool consumes a narrow runtime contract:

```python
class PythonRuntime(Protocol):
    def create_session(
        self,
        session_id: str,
        rows: list[dict[str, Any]],
        query: dict[str, str],
    ) -> None: ...

    def execute(
        self,
        session_id: str,
        run_id: str,
        code: str,
        timeout_seconds: float,
    ) -> ExecutionResult: ...

    def cancel(self, session_id: str, run_id: str) -> bool: ...

    def close_session(self, session_id: str) -> None: ...
```

The default implementation is `ProcessPythonRuntime`. No in-thread execution fallback is exposed
through the API because a timed-out Python thread cannot be stopped safely.

### Worker Process

Each REPL session owns one process and one command/result IPC channel. The worker:

- builds `df` from the session rows;
- initializes `query`, pandas, NumPy, SciPy, statsmodels, and Plotly names;
- executes commands sequentially in one persistent globals dictionary;
- captures stdout and stderr separately;
- converts Plotly figures emitted through `emit_plot(fig)` into JSON artifacts;
- returns one structured result per command.

The worker never handles HTTP, LangGraph messages, or SSE events.

The parent serializes execution per session. A second run against a busy session is rejected with
HTTP 409 and `session_busy`; it is not queued.

Process separation provides enforceable termination and state isolation between REPL sessions. It
is not a security sandbox: generated code still runs with the host account's filesystem and network
permissions. Strong security isolation remains outside this delivery.

### Session State

The session store retains only:

- session metadata and query;
- the summary supplied to the first agent turn;
- the runtime session reference;
- runtime status: `ready`, `busy`, `runtime_lost`, or `closed`;
- active `run_id`, if any.

The FastAPI process no longer owns the mutable Python globals dictionary.

## Request and Event Flow

### Session Creation

1. `POST /repl/session` validates the request and fetches rows.
2. The service generates `session_id` and builds the data summary.
3. `ProcessPythonRuntime.create_session` starts the worker and transfers rows and query.
4. The worker acknowledges successful namespace initialization.
5. Only after acknowledgment does the session store publish the session as `ready`.
6. Worker startup failure returns HTTP 502 with `worker_start_failed`; no session remains registered.

### Chat Run

1. `POST /repl/chat` validates the session and rejects `busy` or `runtime_lost` sessions.
2. The router creates `run_id`, marks the session busy, and emits `RUN_STARTED`.
3. LangGraph message chunks become `TEXT_MESSAGE_*` events.
4. Tool calls become `TOOL_CALL_*` events.
5. `run_python` calls `PythonRuntime.execute` with a 60-second timeout.
6. The result becomes `TOOL_RESULT`; each plot becomes a separate `ARTIFACT`.
7. The assistant completes and the router emits `RUN_FINISHED`.
8. The session returns to `ready` unless the runtime was lost.

The router places `run_id` in the LangGraph configurable context so `run_python` can pass it to the
runtime and correlate its result with the surrounding tool call.

### Cancellation

`POST /repl/runs/{run_id}/cancel` resolves the owning session and verifies that the run is active.
It terminates the worker process, marks the session `runtime_lost`, and causes the open chat stream
to emit `RUN_CANCELLED`. The browser then closes its stream. Repeated cancellation is idempotent.

Cancellation is intentionally destructive to the Python runtime because CPython cannot safely
interrupt arbitrary generated code while preserving interpreter state.

### Session Close

`DELETE /repl/session/{session_id}` closes a ready or lost session and terminates any remaining
worker. The frontend calls it before discarding the current session through “새 세션”. The FastAPI
lifespan shutdown hook closes every remaining worker so server shutdown does not orphan child
processes. Closing a busy session uses the same destructive termination behavior as cancellation.

### Timeout

If no worker result arrives within 60 seconds, the parent terminates the worker, marks the session
`runtime_lost`, returns a timeout tool result, and emits `RUN_ERROR` with
`code="execution_timeout"`. The service does not silently recreate an empty worker.

## Error Contract

Stable public codes are:

- `session_not_found`
- `session_busy`
- `runtime_lost`
- `execution_timeout`
- `execution_cancelled`
- `python_syntax_error`
- `python_runtime_error`
- `worker_start_failed`
- `worker_protocol_error`
- `agent_stream_error`

Python syntax and runtime errors do not destroy a healthy worker. Timeout, cancellation, IPC
corruption, or unexpected worker exit do destroy it. Internal filesystem paths, environment
variables, and secrets are omitted from client-facing messages. Full diagnostics remain in server
logs.

## Frontend Design

### State Model

```typescript
interface ChatState {
  runs: AnalysisRun[];
  activeRunId: string | null;
}

interface AnalysisRun {
  runId: string;
  status: "running" | "completed" | "failed" | "cancelled";
  userMessage: string;
  assistantText: string;
  steps: ToolStep[];
  artifacts: Artifact[];
  error?: RunError;
  lastSequence: number;
}
```

The reducer ignores duplicate or stale sequence numbers. Tool calls and results join by
`tool_call_id`. One user query, its tool steps, artifacts, and final response form one
`AnalysisRun` rendered as one analysis card.

### Frontend Responsibilities

- `replEvents.ts`: event types and runtime parsing.
- `replReducer.ts`: deterministic event-to-state transitions.
- `useReplStream.ts`: POST streaming, chunk framing, cancellation, and network errors.
- `AnalysisCard.tsx`: question, code, results, plots, execution time, and final response.
- `Chat.tsx`: input and analysis-card composition only.
- `PlotlyMessage.tsx`: existing Plotly rendering adapted to `PlotArtifact`.

While running, a card displays the active step and a Stop button. After timeout, cancellation, or
worker loss, the input is disabled and the user is directed to start a new session.
Choosing a new session first calls the session-close endpoint and only then clears the local session.

## Verification Strategy

### Backend Unit Tests

- Pydantic event serialization and discriminators.
- Monotonic per-run sequence allocation.
- Error-code-to-HTTP mapping.
- Session state transitions.
- Busy-session rejection.
- Idempotent session close and application-shutdown cleanup.

### Real Worker Integration Tests

- Access to the session DataFrame and query.
- Variable persistence across separate executions.
- Separate stdout and stderr capture.
- Output truncation at the 50,000-character boundary.
- Structured syntax and runtime errors.
- Plotly artifact production.
- Hard termination of an infinite loop on timeout.
- Hard termination after cancellation.
- Rejection of commands after runtime loss.

### React Tests

- SSE frames split across arbitrary network chunks.
- Tool call/result correlation.
- Duplicate and stale sequence rejection.
- Artifact rendering dispatch.
- Cancel API invocation.
- Input disablement after runtime loss.

### End-to-End Gate

Completion requires an actual user scenario, not only unit tests:

1. Start the real FastAPI server and Vite frontend.
2. Create a session from the bundled sample data.
3. Send an analysis question through the browser using the configured OpenRouter model.
4. Observe an actual `run_python` tool call.
5. Confirm the worker computes against the real session DataFrame.
6. Confirm structured tool output and a real Plotly artifact render in the browser.
7. Confirm the final assistant conclusion appears in the same analysis card.
8. Exercise timeout and cancellation against actual worker processes.
9. Confirm a new session restores normal operation after runtime loss.

## Acceptance Criteria

- Existing supported yield-analysis questions continue to work.
- Python variables persist between turns in a healthy session.
- Backend and frontend use the same documented event schema.
- Tool code, structured results, Plotly output, and final text appear in one analysis card.
- Timeout and cancellation stop the underlying Python process, not only the HTTP stream.
- Runtime loss is explicit and blocks further execution in that session.
- Closing or replacing a session leaves no live worker process.
- No AG-UI, CopilotKit, database, authentication, or sandbox service dependency is introduced.
- Backend unit tests, worker integration tests, React tests, and the real end-to-end gate pass.
