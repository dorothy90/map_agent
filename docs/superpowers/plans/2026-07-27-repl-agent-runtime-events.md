# REPL Agent Runtime and Event Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the REPL agent's mutable in-process `exec()` and ad-hoc SSE handling with a process-backed stateful Python runtime, an AG-UI-compatible local event contract, hard timeout/cancellation, and reducer-driven React analysis cards.

**Architecture:** Keep FastAPI, LangChain `create_agent`, LangGraph streaming, Vite/React, and Plotly. Add typed event and execution contracts, route all generated Python through one persistent worker process per session, and adapt the existing API/UI incrementally so every task leaves a testable system.

**Tech Stack:** Python 3.11+, FastAPI 0.115+, Pydantic v2, LangChain 1.2+, LangGraph 1.0+, `multiprocessing` spawn/Pipe, pandas, NumPy, SciPy, statsmodels, Plotly, React 19, TypeScript 5.7, Vite 6, Vitest 3, Testing Library 16, Playwright 1.54.

## Global Constraints

- Implement only the approved P0+P1 scope in `docs/superpowers/specs/2026-07-27-repl-agent-runtime-events-design.md`.
- Do not add CopilotKit or AG-UI SDK dependencies; implement the approved compatible local contract.
- Do not replace FastAPI, LangChain, LangGraph, Vite, React, or Plotly.
- Use one persistent spawned Python worker per REPL session; do not retain an in-thread `exec()` fallback.
- Default Python execution timeout is exactly 60 seconds.
- Cap stdout and stderr independently at exactly 50,000 characters and expose truncation flags.
- Timeout, cancellation, IPC corruption, and unexpected worker exit must terminate the worker and mark the session `runtime_lost`.
- Python syntax/runtime errors must not destroy a healthy worker.
- Same-session concurrent runs return HTTP 409 `session_busy`; do not queue them.
- Process separation is not a sandbox; do not claim filesystem or network isolation.
- Preserve first-turn DataFrame summary injection and existing `create_agent` behavior.
- Preserve Plotly JSON rendering and `emit_plot(fig)` semantics.
- Do not introduce keyword, regex, phrase-list, or special-case natural-language routing rules.
- Each task follows TDD and ends with the listed focused tests plus a small commit.
- Final completion requires real sample data, a real OpenRouter LLM call, actual tool execution, actual Plotly rendering, and actual timeout/cancel process termination.

## File Map

### Backend files to create

- `08-YieldAgent/repl_agent/events.py` — Pydantic event union and per-run sequence emitter.
- `08-YieldAgent/repl_agent/runtime/__init__.py` — the single shared runtime instance.
- `08-YieldAgent/repl_agent/runtime/base.py` — execution models and `PythonRuntime` protocol.
- `08-YieldAgent/repl_agent/runtime/worker.py` — bounded output capture and child-process command loop.
- `08-YieldAgent/repl_agent/runtime/process.py` — parent-side process lifecycle, IPC, timeout, cancel, and close.
- `08-YieldAgent/repl_agent/run_registry.py` — active async run cancellation controls.
- `08-YieldAgent/repl_agent/tests/test_events.py` — event contract tests.
- `08-YieldAgent/repl_agent/tests/test_worker.py` — direct worker-core tests.
- `08-YieldAgent/repl_agent/tests/test_process_runtime.py` — real child-process tests.
- `08-YieldAgent/repl_agent/tests/test_session_store.py` — session state/lifecycle tests.
- `08-YieldAgent/repl_agent/tests/test_router.py` — standardized SSE and endpoint tests.

### Backend files to modify

- `08-YieldAgent/repl_agent/session_store.py` — metadata/state store backed by `PythonRuntime`.
- `08-YieldAgent/repl_agent/tools.py` — structured runtime invocation and artifact streaming.
- `08-YieldAgent/repl_agent/router.py` — typed SSE conversion, cancel/close endpoints, run cleanup.
- `08-YieldAgent/repl_agent/prompts.py` — describe structured tool results without changing semantic planning rules.
- `08-YieldAgent/agent_server.py` — close all REPL workers during FastAPI shutdown.

### Frontend files to create

- `08-YieldAgent/repl_agent/frontend/src/replEvents.ts` — event types, validation, and SSE frame parsing.
- `08-YieldAgent/repl_agent/frontend/src/replReducer.ts` — deterministic run state transitions.
- `08-YieldAgent/repl_agent/frontend/src/useReplStream.ts` — POST stream and cancel client.
- `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx` — one card per user analysis run.
- `08-YieldAgent/repl_agent/frontend/src/replEvents.test.ts` — chunk/framing tests.
- `08-YieldAgent/repl_agent/frontend/src/replReducer.test.ts` — reducer tests.
- `08-YieldAgent/repl_agent/frontend/src/useReplStream.test.tsx` — transport/cancel tests.
- `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx` — UI state tests.
- `08-YieldAgent/repl_agent/frontend/src/App.test.tsx` — server-side session close behavior.
- `08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx` — runtime-loss input lockout.
- `08-YieldAgent/repl_agent/frontend/src/test/setup.ts` — Testing Library matchers.
- `08-YieldAgent/repl_agent/frontend/playwright.config.ts` — live browser configuration.
- `08-YieldAgent/repl_agent/frontend/e2e/repl-live.spec.ts` — real browser/LLM/tool/plot scenario.

### Frontend files to modify

- `08-YieldAgent/repl_agent/frontend/src/Chat.tsx` — compose the hook and analysis cards.
- `08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.tsx` — accept `PlotArtifact`.
- `08-YieldAgent/repl_agent/frontend/src/App.tsx` — close server session before clearing it.
- `08-YieldAgent/repl_agent/frontend/src/styles.css` — analysis card, status, and cancel styling.
- `08-YieldAgent/repl_agent/frontend/package.json` — test scripts and dev dependencies.
- `08-YieldAgent/repl_agent/frontend/package-lock.json` — generated dependency lock update.

---

### Task 1: Define execution and event contracts

**Files:**
- Create: `08-YieldAgent/repl_agent/runtime/base.py`
- Create: `08-YieldAgent/repl_agent/events.py`
- Test: `08-YieldAgent/repl_agent/tests/test_events.py`

**Interfaces:**
- Consumes: Pydantic v2 already present in the project.
- Produces: `ExecutionError`, `PlotArtifact`, `ExecutionResult`, `PythonRuntime`, `EventEmitter`, and `ReplEvent` used by all later backend tasks.

- [ ] **Step 1: Write failing contract tests**

```python
# 08-YieldAgent/repl_agent/tests/test_events.py
from repl_agent.events import EventEmitter, RunStarted, ToolResult
from repl_agent.runtime.base import ExecutionError, ExecutionResult, PlotArtifact


def test_execution_result_excludes_plots_from_tool_json():
    result = ExecutionResult(
        status="success",
        stdout="42\n",
        plots=[PlotArtifact(artifact_id="p1", spec={"data": [], "layout": {}})],
        execution_time_ms=7,
    )
    payload = result.to_tool_payload()
    assert payload["status"] == "success"
    assert payload["stdout"] == "42\n"
    assert "plots" not in payload


def test_execution_error_has_stable_public_shape():
    error = ExecutionError(
        code="python_runtime_error",
        exception_name="ValueError",
        message="bad value",
        traceback="Traceback...",
    )
    assert error.model_dump()["code"] == "python_runtime_error"


def test_event_emitter_allocates_monotonic_sequence():
    emitter = EventEmitter(run_id="run-1", thread_id="thread-1")
    first = emitter.build(RunStarted)
    second = emitter.build(ToolResult, tool_call_id="tool-1", result={"status": "success"})
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.type == "RUN_STARTED"
    assert second.model_dump()["tool_call_id"] == "tool-1"
```

- [ ] **Step 2: Run the tests and confirm contract modules are missing**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_events.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'repl_agent.events'`.

- [ ] **Step 3: Implement the execution models and runtime protocol**

```python
# 08-YieldAgent/repl_agent/runtime/base.py
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

ExecutionStatus = Literal["success", "error", "timeout", "cancelled", "runtime_lost"]


class ExecutionError(BaseModel):
    code: str
    exception_name: str
    message: str
    traceback: str = ""


class PlotArtifact(BaseModel):
    artifact_id: str
    kind: Literal["plotly"] = "plotly"
    mime_type: Literal["application/vnd.plotly.v1+json"] = "application/vnd.plotly.v1+json"
    spec: dict[str, Any]


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: ExecutionError | None = None
    plots: list[PlotArtifact] = Field(default_factory=list)
    execution_time_ms: int

    def to_tool_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude={"plots"})


class PythonRuntime(Protocol):
    def create_session(self, session_id: str, rows: list[dict[str, Any]], query: dict[str, str]) -> None: ...
    def execute(self, session_id: str, run_id: str, code: str, timeout_seconds: float) -> ExecutionResult: ...
    def cancel(self, session_id: str, run_id: str) -> bool: ...
    def close_session(self, session_id: str) -> None: ...
    def close_all(self) -> None: ...
```

- [ ] **Step 4: Implement the event models and emitter**

Create the common event envelope and the twelve approved concrete event classes with `Literal` discriminators; export their union as `ReplEvent`:

```python
class BaseReplEvent(BaseModel):
    type: str
    run_id: str
    thread_id: str
    sequence: int = Field(ge=1)

class RunStarted(BaseReplEvent):
    type: Literal["RUN_STARTED"] = "RUN_STARTED"
class TextMessageStart(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_START"] = "TEXT_MESSAGE_START"
    message_id: str
class TextMessageContent(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_CONTENT"] = "TEXT_MESSAGE_CONTENT"
    message_id: str
    content: str
class TextMessageEnd(BaseReplEvent):
    type: Literal["TEXT_MESSAGE_END"] = "TEXT_MESSAGE_END"
    message_id: str
class ToolCallStart(BaseReplEvent):
    type: Literal["TOOL_CALL_START"] = "TOOL_CALL_START"
    tool_call_id: str
    name: str
class ToolCallArgs(BaseReplEvent):
    type: Literal["TOOL_CALL_ARGS"] = "TOOL_CALL_ARGS"
    tool_call_id: str
    args: dict[str, Any]
class ToolCallEnd(BaseReplEvent):
    type: Literal["TOOL_CALL_END"] = "TOOL_CALL_END"
    tool_call_id: str
class ToolResult(BaseReplEvent):
    type: Literal["TOOL_RESULT"] = "TOOL_RESULT"
    tool_call_id: str
    result: dict[str, Any]
class ArtifactEvent(BaseReplEvent):
    type: Literal["ARTIFACT"] = "ARTIFACT"
    tool_call_id: str
    artifact: PlotArtifact
class RunFinished(BaseReplEvent):
    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
class RunError(BaseReplEvent):
    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    code: str
    message: str
class RunCancelled(BaseReplEvent):
    type: Literal["RUN_CANCELLED"] = "RUN_CANCELLED"
    code: Literal["execution_cancelled"] = "execution_cancelled"
    message: str = "실행이 취소되었습니다. 새 세션을 시작해주세요."

ReplEvent = Annotated[
    RunStarted | TextMessageStart | TextMessageContent | TextMessageEnd |
    ToolCallStart | ToolCallArgs | ToolCallEnd | ToolResult |
    ArtifactEvent | RunFinished | RunError | RunCancelled,
    Field(discriminator="type"),
]

class EventEmitter:
    def __init__(self, run_id: str, thread_id: str):
        self.run_id = run_id
        self.thread_id = thread_id
        self._sequence = 0

    def build(self, event_cls, **payload):
        self._sequence += 1
        return event_cls(
            run_id=self.run_id,
            thread_id=self.thread_id,
            sequence=self._sequence,
            **payload,
        )
```

Import `PlotArtifact` from `runtime.base`; do not duplicate the artifact schema in this module.

- [ ] **Step 5: Run focused and existing backend tests**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_events.py repl_agent/tests/test_mock_routes.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the contracts**

```bash
git add 08-YieldAgent/repl_agent/events.py 08-YieldAgent/repl_agent/runtime/base.py 08-YieldAgent/repl_agent/tests/test_events.py
git commit -m "feat(repl): add runtime event contracts"
```

---

### Task 2: Build the stateful worker execution core

**Files:**
- Create: `08-YieldAgent/repl_agent/runtime/worker.py`
- Test: `08-YieldAgent/repl_agent/tests/test_worker.py`

**Interfaces:**
- Consumes: `ExecutionError`, `ExecutionResult`, and `PlotArtifact` from Task 1.
- Produces: `build_namespace(rows, query)`, `execute_code(namespace, code)`, and `worker_main(connection, rows, query)` for Task 3.

- [ ] **Step 1: Write failing direct worker tests**

```python
from repl_agent.runtime.worker import build_namespace, execute_code

ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": "3.5"}]
QUERY = {"lotcd": "P1", "start": "2026-01-01", "end": "2026-01-31", "fail_name": "OPEN"}


def test_namespace_exposes_required_analysis_libraries():
    ns = build_namespace(ROWS, QUERY)
    assert set(("df", "query", "pd", "np", "px", "go", "sm", "scipy")) <= ns.keys()


def test_variables_persist_between_executions():
    ns = build_namespace(ROWS, QUERY)
    assert execute_code(ns, "answer = 40 + 2").status == "success"
    result = execute_code(ns, "print(answer)")
    assert result.stdout == "42\n"


def test_stdout_is_bounded_and_reports_truncation():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "print('x' * 60000)")
    assert len(result.stdout) == 50000
    assert result.stdout_truncated is True


def test_stderr_is_captured_separately():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "import sys; print('warning', file=sys.stderr)")
    assert result.stdout == ""
    assert result.stderr == "warning\n"


def test_plot_is_returned_as_structured_artifact():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "fig = px.histogram(df, x='fail_value'); emit_plot(fig)")
    assert result.status == "success"
    assert result.plots[0].kind == "plotly"
    assert "data" in result.plots[0].spec


def test_runtime_error_keeps_namespace_usable():
    ns = build_namespace(ROWS, QUERY)
    failed = execute_code(ns, "raise ValueError('bad')")
    recovered = execute_code(ns, "print('still alive')")
    assert failed.error.code == "python_runtime_error"
    assert recovered.stdout == "still alive\n"
```

- [ ] **Step 2: Run the worker tests and verify failure**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_worker.py -q`

Expected: collection fails because `runtime.worker` does not exist.

- [ ] **Step 3: Implement bounded capture, namespace construction, and execution**

Implement `BoundedTextBuffer(io.TextIOBase)` with `limit=50_000`, `truncated`, `write()`, and `getvalue()`. `build_namespace` must construct the DataFrame inside the worker and expose all documented libraries, including the currently missing `scipy` binding. `execute_code` must:

```python
started = time.perf_counter()
stdout = BoundedTextBuffer()
stderr = BoundedTextBuffer()
plots: list[PlotArtifact] = []

def emit_plot(fig: Any) -> None:
    plots.append(PlotArtifact(
        artifact_id=str(uuid.uuid4()),
        spec=json.loads(fig.to_json()),
    ))

namespace["emit_plot"] = emit_plot
try:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exec(code, namespace)  # noqa: S102 -- isolated process, not a security sandbox
except SyntaxError as exc:
    error = ExecutionError(code="python_syntax_error", exception_name=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
except Exception as exc:
    error = ExecutionError(code="python_runtime_error", exception_name=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
```

Return `ExecutionResult` with elapsed milliseconds and truncation flags. Do not catch `BaseException`.

- [ ] **Step 4: Implement the child command loop**

`worker_main` sends `{"type": "ready"}` after namespace creation, accepts only `{"type": "execute", "run_id", "code"}` and `{"type": "close"}`, and sends `ExecutionResult.model_dump(mode="json")`. An unknown command returns a `worker_protocol_error` result without executing code.

- [ ] **Step 5: Run worker and contract tests**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_worker.py repl_agent/tests/test_events.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the worker core**

```bash
git add 08-YieldAgent/repl_agent/runtime/worker.py 08-YieldAgent/repl_agent/tests/test_worker.py
git commit -m "feat(repl): add stateful worker core"
```

---

### Task 3: Implement real process lifecycle, timeout, and cancellation

**Files:**
- Create: `08-YieldAgent/repl_agent/runtime/process.py`
- Create: `08-YieldAgent/repl_agent/runtime/__init__.py`
- Test: `08-YieldAgent/repl_agent/tests/test_process_runtime.py`

**Interfaces:**
- Consumes: `PythonRuntime`, `ExecutionResult`, and `worker_main`.
- Produces: `ProcessPythonRuntime` and shared `runtime` used by session storage and tools.

- [ ] **Step 1: Write real child-process tests**

```python
import threading
import time

from repl_agent.runtime.process import ProcessPythonRuntime

ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": "3.5"}]
QUERY = {"lotcd": "P1", "start": "2026-01-01", "end": "2026-01-31", "fail_name": "OPEN"}


def test_process_runtime_preserves_state():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        runtime.execute("s1", "r1", "value = 9", 2)
        result = runtime.execute("s1", "r2", "print(value)", 2)
        assert result.stdout == "9\n"
    finally:
        runtime.close_all()


def test_timeout_terminates_worker_and_loses_runtime():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    result = runtime.execute("s1", "r1", "while True: pass", 0.2)
    assert result.status == "timeout"
    assert runtime.is_alive("s1") is False


def test_cancel_terminates_actual_running_worker():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", runtime.execute("s1", "r1", "while True: pass", 10)))
    thread.start()
    time.sleep(0.2)
    assert runtime.cancel("s1", "r1") is True
    thread.join(timeout=2)
    assert thread.is_alive() is False
    assert box["result"].status == "cancelled"
    assert runtime.is_alive("s1") is False


def test_unexpected_worker_exit_becomes_runtime_lost():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    result = runtime.execute("s1", "r1", "raise SystemExit(3)", 2)
    assert result.status == "runtime_lost"
    assert runtime.is_alive("s1") is False


def test_close_session_reaps_child_process():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    runtime.close_session("s1")
    assert runtime.is_alive("s1") is False
```

- [ ] **Step 2: Run tests and verify the process runtime is absent**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_process_runtime.py -q`

Expected: collection fails for missing `runtime.process`.

- [ ] **Step 3: Implement `ProcessPythonRuntime`**

Use `multiprocessing.get_context("spawn")` and `Pipe(duplex=True)`. Store a `_WorkerHandle` per session with process, parent connection, active run ID, cancelled run ID, and a send/receive lock. Required behavior:

```python
def execute(self, session_id, run_id, code, timeout_seconds):
    handle = self._require_handle(session_id)
    with handle.io_lock:
        handle.active_run_id = run_id
        handle.connection.send({"type": "execute", "run_id": run_id, "code": code})
        if not handle.connection.poll(timeout_seconds):
            self._terminate(handle)
            return self._terminal_result("timeout", "execution_timeout", "Python execution exceeded 60 seconds")
        try:
            payload = handle.connection.recv()
        except (EOFError, OSError):
            status = "cancelled" if handle.cancelled_run_id == run_id else "runtime_lost"
            code = "execution_cancelled" if status == "cancelled" else "worker_protocol_error"
            return self._terminal_result(status, code, "Python worker stopped before returning a result")
        finally:
            handle.active_run_id = None
        return ExecutionResult.model_validate(payload)
```

`cancel` must set `cancelled_run_id` before terminating without waiting for `io_lock`. `_terminate` uses `terminate()`, `join(timeout=2)`, then `kill()` and a second `join()` if necessary. `close_session` is idempotent, sends `close` only to an idle healthy worker, and always closes the Pipe. `close_all` iterates over a snapshot of session IDs.

- [ ] **Step 4: Export one runtime instance**

```python
# 08-YieldAgent/repl_agent/runtime/__init__.py
from .process import ProcessPythonRuntime

runtime = ProcessPythonRuntime(startup_timeout_seconds=10)

__all__ = ["ProcessPythonRuntime", "runtime"]
```

- [ ] **Step 5: Run process tests twice to detect leaked children**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_process_runtime.py -q && PYTHONPATH=. pytest repl_agent/tests/test_process_runtime.py -q`

Expected: both runs pass and exit without hanging.

- [ ] **Step 6: Commit process lifecycle support**

```bash
git add 08-YieldAgent/repl_agent/runtime/__init__.py 08-YieldAgent/repl_agent/runtime/process.py 08-YieldAgent/repl_agent/tests/test_process_runtime.py
git commit -m "feat(repl): isolate Python in workers"
```

---

### Task 4: Move session state onto the runtime lifecycle

**Files:**
- Modify: `08-YieldAgent/repl_agent/session_store.py`
- Test: `08-YieldAgent/repl_agent/tests/test_session_store.py`

**Interfaces:**
- Consumes: shared `runtime.create_session`, `runtime.cancel`, `runtime.close_session`, and `runtime.close_all`.
- Produces: `SessionRecord`, `create_session`, `get_session`, `begin_run`, `finish_run`, `mark_runtime_lost`, `cancel_run`, `close_session`, and `close_all_sessions`.

- [ ] **Step 1: Write session transition tests with a fake runtime**

Define this fake above the tests, monkeypatch `session_store._runtime`, and test:

```python
class FakeRuntime:
    def __init__(self):
        self.created: list[str] = []
        self.cancelled: list[tuple[str, str]] = []
        self.closed: list[str] = []

    def create_session(self, session_id, rows, query):
        self.created.append(session_id)
    def execute(self, session_id, run_id, code, timeout_seconds):
        raise AssertionError("not used by session-store tests")
    def cancel(self, session_id, run_id):
        self.cancelled.append((session_id, run_id))
        return True
    def close_session(self, session_id):
        self.closed.append(session_id)
    def close_all(self):
        pass

def test_begin_run_rejects_second_run(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = asyncio.run(session_store.create_session("L12345", "2026-02-01", "2026-04-30", "OPEN"))
    session_store.begin_run(info["session_id"], "run-1")
    with pytest.raises(SessionStateError) as exc:
        session_store.begin_run(info["session_id"], "run-2")
    assert exc.value.code == "session_busy"


def test_cancel_marks_runtime_lost(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = asyncio.run(session_store.create_session("L12345", "2026-02-01", "2026-04-30", "OPEN"))
    session_store.begin_run(info["session_id"], "run-1")
    assert session_store.cancel_run("run-1") is True
    assert session_store.get_session(info["session_id"]).status == "runtime_lost"


def test_close_session_is_idempotent(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = asyncio.run(session_store.create_session("L12345", "2026-02-01", "2026-04-30", "OPEN"))
    assert session_store.close_session(info["session_id"]) is True
    assert session_store.close_session(info["session_id"]) is False
```

- [ ] **Step 2: Run tests and verify the old namespace API fails expectations**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_session_store.py -q`

Expected: failures for missing `SessionRecord`, `begin_run`, and `cancel_run`.

- [ ] **Step 3: Refactor the session store surgically**

Replace `_SESSIONS: dict[str, dict[str, Any]]` with `dict[str, SessionRecord]`. Keep `_fetch_rows` and `_build_session_summary`. Build a temporary DataFrame only to generate summary/columns, call `_runtime.create_session(session_id, rows, query)`, and publish the record only after worker acknowledgment. Remove `get_namespace` and expose `get_session_summary` through the record.

`finish_run(session_id, run_id, runtime_lost=False)` must only clear a matching active run; set `ready` for healthy completion or `runtime_lost` for destructive completion. `cancel_run` resolves the session by active run ID, calls runtime cancellation, and marks it lost. `close_all_sessions` closes each runtime and clears the store.

- [ ] **Step 4: Run session and mock route tests**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_session_store.py repl_agent/tests/test_mock_routes.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit session lifecycle changes**

```bash
git add 08-YieldAgent/repl_agent/session_store.py 08-YieldAgent/repl_agent/tests/test_session_store.py
git commit -m "refactor(repl): track runtime sessions"
```

---

### Task 5: Standardize LangGraph/FastAPI streaming and cancellation

**Files:**
- Create: `08-YieldAgent/repl_agent/run_registry.py`
- Modify: `08-YieldAgent/repl_agent/tools.py`
- Modify: `08-YieldAgent/repl_agent/router.py`
- Modify: `08-YieldAgent/repl_agent/prompts.py`
- Test: `08-YieldAgent/repl_agent/tests/test_router.py`

**Interfaces:**
- Consumes: event models/emitter, shared runtime, and session transition functions.
- Produces: standardized `/repl/chat`, `POST /repl/runs/{run_id}/cancel`, and `DELETE /repl/session/{session_id}` behavior for the frontend.

- [ ] **Step 1: Write endpoint and SSE tests using a fake agent stream**

Use the following fake agent and SSE helper. Create the ready/busy records through the Task 4 session-store API with a monkeypatched `FakeRuntime`; do not mutate private dictionaries directly.

```python
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

class FakeAgent:
    async def aget_state(self, config):
        return SimpleNamespace(values={})

    async def astream(self, inputs, config, stream_mode):
        yield "messages", (AIMessageChunk(content="결과"), {})

class FakeToolAgent(FakeAgent):
    async def astream(self, inputs, config, stream_mode):
        yield "updates", {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{"id": "tool-1", "name": "run_python", "args": {"code": "print(1)"}}],
        )]}}
        yield "updates", {"tools": {"messages": [ToolMessage(
            content='{"status":"success","stdout":"1\\n","execution_time_ms":1}',
            tool_call_id="tool-1",
            name="run_python",
        )]}}

def decode_sse(body: str) -> list[dict]:
    return [json.loads(block.removeprefix("data: ")) for block in body.strip().split("\n\n")]

def test_chat_stream_uses_standard_envelope(client, ready_session, monkeypatch):
    monkeypatch.setattr(router_module, "get_agent", lambda: FakeAgent())
    response = client.post("/repl/chat", json={"session_id": ready_session, "query": "mean"})
    events = decode_sse(response.text)
    assert events[0]["type"] == "RUN_STARTED"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "RUN_FINISHED"
    assert {event["thread_id"] for event in events} == {ready_session}


def test_tool_updates_become_correlated_events(client, ready_session, monkeypatch):
    monkeypatch.setattr(router_module, "get_agent", lambda: FakeToolAgent())
    events = decode_sse(client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "mean"}
    ).text)
    tool_events = [event for event in events if event["type"].startswith("TOOL_")]
    assert [event["type"] for event in tool_events] == [
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_RESULT"
    ]
    assert {event["tool_call_id"] for event in tool_events} == {"tool-1"}


def test_busy_session_returns_structured_409(client, busy_session):
    response = client.post("/repl/chat", json={"session_id": busy_session, "query": "mean"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "session_busy"


def test_cancel_endpoint_is_idempotent(client, active_run, monkeypatch):
    monkeypatch.setattr(router_module, "cancel_run", lambda run_id: run_id == active_run)
    first = client.post(f"/repl/runs/{active_run}/cancel")
    second = client.post(f"/repl/runs/{active_run}/cancel")
    assert first.status_code == 200
    assert second.status_code == 200


def test_delete_session_closes_worker(client, ready_session, monkeypatch):
    closed = []
    monkeypatch.setattr(router_module, "close_session", lambda session_id: not closed.append(session_id))
    response = client.delete(f"/repl/session/{ready_session}")
    assert response.status_code == 200
    assert closed == [ready_session]
```

- [ ] **Step 2: Run router tests and confirm old event names fail**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_router.py -q`

Expected: failures because the current stream emits lowercase `start`, `token`, and `end` events and has no cancel/delete endpoints.

- [ ] **Step 3: Refactor `run_python` to call the shared runtime**

Read `thread_id` and `run_id` from `config["configurable"]`. Add `tool_call_id: Annotated[str, InjectedToolCallId]` to the tool signature using `langchain_core.tools.InjectedToolCallId`, then call `runtime.execute(..., timeout_seconds=60)`. If plots exist, emit `{"kind": "artifacts", "tool_call_id": tool_call_id, "artifacts": [...]}` through `get_stream_writer()`. Return `json.dumps(result.to_tool_payload(), ensure_ascii=False)`. If status is timeout, cancelled, or runtime_lost, call `mark_runtime_lost(session_id, run_id)`.

Update only the tool-result portion of `SYSTEM_PROMPT`: explain the JSON fields and truncation flags. Do not add language-pattern examples or routing rules.

- [ ] **Step 4: Add async run cancellation control**

`RunRegistry` stores `RunControl(session_id, cancel_event=asyncio.Event())`. The router registers before returning `StreamingResponse`, and unregisters in the generator `finally`. Stream iteration must race `anext(agent_stream)` against `cancel_event.wait()` using `asyncio.wait(..., return_when=FIRST_COMPLETED)`. On cancellation, close/cancel the pending stream task and emit `RUN_CANCELLED` before returning.

- [ ] **Step 5: Convert router output to typed events**

Create one `EventEmitter` per chat request. Put `run_id` into configurable context. Convert message chunks to `TEXT_MESSAGE_START/CONTENT/END`, updates to `TOOL_CALL_*` and `TOOL_RESULT`, custom artifacts to `ARTIFACT`, and terminal conditions to `RUN_FINISHED`, `RUN_ERROR`, or `RUN_CANCELLED`. Serialize with:

```python
def _sse(event: BaseReplEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"
```

Always run `finish_run` and unregister the run control in `finally`. The cancel endpoint sets the async cancel event and calls `cancel_run`; the delete endpoint calls `close_session`.

- [ ] **Step 6: Run router, worker, and mock tests**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_router.py repl_agent/tests/test_process_runtime.py repl_agent/tests/test_mock_routes.py -q`

Expected: all tests pass without hanging or leaving worker processes.

- [ ] **Step 7: Commit the backend vertical slice**

```bash
git add 08-YieldAgent/repl_agent/run_registry.py 08-YieldAgent/repl_agent/tools.py 08-YieldAgent/repl_agent/router.py 08-YieldAgent/repl_agent/prompts.py 08-YieldAgent/repl_agent/tests/test_router.py
git commit -m "feat(repl): standardize run streaming"
```

---

### Task 6: Add frontend event parsing, reducer state, and stream hook

**Files:**
- Create: `08-YieldAgent/repl_agent/frontend/src/replEvents.ts`
- Create: `08-YieldAgent/repl_agent/frontend/src/replReducer.ts`
- Create: `08-YieldAgent/repl_agent/frontend/src/useReplStream.ts`
- Create: `08-YieldAgent/repl_agent/frontend/src/replEvents.test.ts`
- Create: `08-YieldAgent/repl_agent/frontend/src/replReducer.test.ts`
- Create: `08-YieldAgent/repl_agent/frontend/src/useReplStream.test.tsx`
- Create: `08-YieldAgent/repl_agent/frontend/src/test/setup.ts`
- Modify: `08-YieldAgent/repl_agent/frontend/package.json`
- Modify: `08-YieldAgent/repl_agent/frontend/package-lock.json`

**Interfaces:**
- Consumes: exact JSON event fields produced by Task 5.
- Produces: `ReplEvent`, `ChatState`, `replReducer`, and `useReplStream(sessionId)` for Task 7.

- [ ] **Step 1: Install the focused frontend test stack**

Run:

```bash
cd 08-YieldAgent/repl_agent/frontend
npm install --save-dev vitest@^3.2.4 jsdom@^26.1.0 @testing-library/react@^16.3.0 @testing-library/jest-dom@^6.6.3
```

Add scripts `"test": "vitest run"` and `"test:watch": "vitest"`, plus Vitest config under `vite.config.ts` with `environment: "jsdom"` and `setupFiles: "./src/test/setup.ts"`.

- [ ] **Step 2: Write failing parser and reducer tests**

```typescript
// replEvents.test.ts
it("reassembles SSE split across arbitrary chunks", async () => {
  const chunks = [
    'data: {"type":"RUN_STARTED","run_id":"r1",',
    '"thread_id":"s1","sequence":1}\n\n',
  ];
  const events: ReplEvent[] = [];
  for await (const event of parseSseChunks(chunks)) events.push(event);
  expect(events).toHaveLength(1);
  expect(events[0].type).toBe("RUN_STARTED");
});

// replReducer.test.ts
it("joins tool results and ignores duplicate sequences", () => {
  let state = initialChatState;
  state = replReducer(state, { type: "SERVER_EVENT", event: runStarted });
  state = replReducer(state, { type: "SERVER_EVENT", event: toolCall });
  state = replReducer(state, { type: "SERVER_EVENT", event: toolResult });
  const duplicate = replReducer(state, { type: "SERVER_EVENT", event: toolResult });
  expect(duplicate.runs[0].steps).toHaveLength(1);
  expect(duplicate.runs[0].steps[0].result?.status).toBe("success");
});
```

- [ ] **Step 3: Run tests and verify modules are absent**

Run: `cd 08-YieldAgent/repl_agent/frontend && npm test`

Expected: TypeScript/Vitest fails to resolve `replEvents` and `replReducer`.

- [ ] **Step 4: Implement event validation and streaming parser**

Define a discriminated TypeScript union matching all backend event payloads. `parseReplEvent(value: unknown)` must reject non-objects and missing common fields; switch on the twelve approved `type` values and return the narrowed event. `parseSseStream(response.body)` must retain a text buffer across reads and split only on blank-line SSE boundaries.

- [ ] **Step 5: Implement the reducer**

Use `runId` as identity, store `lastSequence`, ignore `sequence <= lastSequence`, join tool steps by `tool_call_id`, append text content, attach artifacts, and transition statuses only as follows:

```text
RUN_STARTED   -> running
RUN_FINISHED  -> completed
RUN_ERROR     -> failed
RUN_CANCELLED -> cancelled
```

Set `runtimeLost` when a tool/error payload status or code is `runtime_lost`, `execution_timeout`, or `execution_cancelled`.

- [ ] **Step 6: Implement and test `useReplStream`**

The hook exposes `{state, send, cancel, sending, cancelPending}`. `send(query)` dispatches a local `USER_SUBMITTED` action, POSTs `/repl/chat`, and dispatches each parsed server event. `cancel()` POSTs `/repl/runs/{activeRunId}/cancel` but leaves the stream open until `RUN_CANCELLED` arrives. Test both URLs and ensure a failed fetch creates a local failed run without inventing a server sequence.

- [ ] **Step 7: Run frontend tests and typecheck build**

Run: `cd 08-YieldAgent/repl_agent/frontend && npm test && npm run build`

Expected: all tests pass and Vite production build succeeds.

- [ ] **Step 8: Commit frontend state infrastructure**

```bash
git add 08-YieldAgent/repl_agent/frontend/package.json 08-YieldAgent/repl_agent/frontend/package-lock.json 08-YieldAgent/repl_agent/frontend/vite.config.ts 08-YieldAgent/repl_agent/frontend/src/replEvents.ts 08-YieldAgent/repl_agent/frontend/src/replReducer.ts 08-YieldAgent/repl_agent/frontend/src/useReplStream.ts 08-YieldAgent/repl_agent/frontend/src/replEvents.test.ts 08-YieldAgent/repl_agent/frontend/src/replReducer.test.ts 08-YieldAgent/repl_agent/frontend/src/useReplStream.test.tsx 08-YieldAgent/repl_agent/frontend/src/test/setup.ts
git commit -m "feat(repl-ui): add run event state"
```

---

### Task 7: Render analysis cards and close sessions cleanly

**Files:**
- Create: `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx`
- Create: `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx`
- Create: `08-YieldAgent/repl_agent/frontend/src/App.test.tsx`
- Create: `08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/Chat.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/App.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/styles.css`

**Interfaces:**
- Consumes: `AnalysisRun`, `PlotArtifact`, and `useReplStream` from Task 6.
- Produces: the approved one-query/one-card UI, Stop interaction, runtime-loss lockout, and server-side session close.

- [ ] **Step 1: Write failing component tests**

```typescript
const completedRun: AnalysisRun = {
  runId: "r1",
  status: "completed",
  userMessage: "fail_value 평균을 계산해줘",
  assistantText: "판정: 평균 차이를 확인했습니다.",
  lastSequence: 8,
  steps: [{
    toolCallId: "t1",
    name: "run_python",
    args: { code: "wafer_df = df.drop_duplicates(['lot_id', 'wf_id'])" },
    result: { status: "success", stdout: "stdout: 3.5\n", execution_time_ms: 12 },
  }],
  artifacts: [{ artifact_id: "p1", kind: "plotly", mime_type: "application/vnd.plotly.v1+json", spec: { data: [], layout: {} } }],
};
const runningRun: AnalysisRun = { ...completedRun, runId: "r2", status: "running", assistantText: "" };

it("renders code, structured output, plot, and final text in one card", () => {
  render(<AnalysisCard run={completedRun} onCancel={vi.fn()} cancelPending={false} />);
  expect(screen.getByText("fail_value 평균을 계산해줘")).toBeInTheDocument();
  expect(screen.getByText(/wafer_df =/)).toBeInTheDocument();
  expect(screen.getByText(/stdout/)).toBeInTheDocument();
  expect(screen.getByTestId("plot-artifact-p1")).toBeInTheDocument();
  expect(screen.getByText(/판정/)).toBeInTheDocument();
});

it("offers stop only while running", () => {
  const onCancel = vi.fn();
  render(<AnalysisCard run={runningRun} onCancel={onCancel} cancelPending={false} />);
  fireEvent.click(screen.getByRole("button", { name: "중지" }));
  expect(onCancel).toHaveBeenCalledOnce();
});

// App.test.tsx
it("closes the server session before returning to the session form", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: "session-1",
        rowcount: 1,
        columns: ["fail_value"],
        numeric_columns: ["fail_value"],
        query: { lotcd: "L12345", start: "2026-02-16", end: "2026-04-16", fail_name: "OPEN" },
      }),
    })
    .mockResolvedValueOnce({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "세션 시작" }));
  expect(await screen.findByText(/세션 session-/)).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "새 세션" }));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/repl/session/session-1",
    { method: "DELETE" },
  ));
  expect(await screen.findByRole("heading", { name: "새 세션" })).toBeInTheDocument();
});

// Chat.test.tsx — mock useReplStream to return runtimeLost: true.
it("disables new questions after runtime loss", () => {
  render(<Chat sessionId="session-1" />);
  expect(screen.getByPlaceholderText(/질문 입력/)).toBeDisabled();
  expect(screen.getByText(/새 세션을 시작/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and confirm `AnalysisCard` is missing**

Run: `cd 08-YieldAgent/repl_agent/frontend && npm test -- AnalysisCard.test.tsx`

Expected: module resolution failure for `AnalysisCard`.

- [ ] **Step 3: Implement `AnalysisCard` and adapt Plotly rendering**

Render the user question, collapsible tool code/result sections, stdout/stderr labels, truncation badges, elapsed milliseconds, Plotly artifacts, streaming Markdown, status/error banner, and the Stop button. Change `PlotlyMessage` to accept `artifact: PlotArtifact`, read `artifact.spec`, and add `data-testid={\`plot-artifact-${artifact.artifact_id}\`}`.

- [ ] **Step 4: Replace `Chat.tsx` ad-hoc state and parser**

Remove the local `Msg` union, `sseLines`, and repeated `setMessages`. Use `useReplStream(sessionId)`, map `state.runs` to `AnalysisCard`, and disable input when sending or `state.runtimeLost`. Render the explicit message “Python 실행 상태가 소실되었습니다. 새 세션을 시작해주세요.” when lost. Keep the existing presets but correct them to the real schema (`fail_value`, wafer-level deduplication) rather than nonexistent `value`/`wafer` fields.

- [ ] **Step 5: Close sessions before clearing the app state**

In `App.tsx`, implement an async `startNewSession` that calls `DELETE /repl/session/{session_id}` and clears local state on 200 or 404. For any other response, retain the current session and show a close error. Disable the button while closing.

- [ ] **Step 6: Add focused styles**

Add `.analysis-card`, `.analysis-status`, `.analysis-steps`, `.artifact`, `.runtime-lost`, and `.stop-button` rules by extending the existing dark palette. Do not restyle the session form or unrelated application shell.

- [ ] **Step 7: Run component tests and production build**

Run: `cd 08-YieldAgent/repl_agent/frontend && npm test && npm run build`

Expected: all frontend tests pass and build succeeds with no unused TypeScript symbols.

- [ ] **Step 8: Commit analysis UI changes**

```bash
git add 08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx 08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx 08-YieldAgent/repl_agent/frontend/src/App.test.tsx 08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx 08-YieldAgent/repl_agent/frontend/src/Chat.tsx 08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.tsx 08-YieldAgent/repl_agent/frontend/src/App.tsx 08-YieldAgent/repl_agent/frontend/src/styles.css
git commit -m "feat(repl-ui): group analysis runs"
```

---

### Task 8: Add shutdown cleanup and prove the real end-to-end scenario

**Files:**
- Modify: `08-YieldAgent/agent_server.py`
- Create: `08-YieldAgent/repl_agent/frontend/playwright.config.ts`
- Create: `08-YieldAgent/repl_agent/frontend/e2e/repl-live.spec.ts`
- Modify: `08-YieldAgent/repl_agent/frontend/package.json`
- Modify: `08-YieldAgent/repl_agent/frontend/package-lock.json`

**Interfaces:**
- Consumes: `close_all_sessions`, all API endpoints, and the completed React UI.
- Produces: deterministic server shutdown cleanup and the required real browser/LLM/tool/plot acceptance gate.

- [ ] **Step 1: Call REPL cleanup from the existing lifespan**

In the existing `finally` path of `agent_server.lifespan`, call `close_all_sessions()` after stopping the wiki queue and before closing Mongo. Do not create a second FastAPI lifespan.

- [ ] **Step 2: Add Playwright and the live configuration**

Run:

```bash
cd 08-YieldAgent/repl_agent/frontend
npm install --save-dev @playwright/test@^1.54.1
npx playwright install chromium
```

Add `"e2e:live": "playwright test e2e/repl-live.spec.ts"`. Configure base URL `http://127.0.0.1:5173`, a 180-second test timeout, Vite as one web server, and `uvicorn agent_server:app --host 127.0.0.1 --port 8001` from `08-YieldAgent` as the other. Both inherit the caller's environment.

- [ ] **Step 3: Write the initially failing live browser test**

```typescript
import { expect, test } from "@playwright/test";

test("real LLM executes Python and renders a Plotly analysis card", async ({ page }) => {
  test.skip(process.env.REPL_E2E_LIVE !== "1", "set REPL_E2E_LIVE=1 with OpenRouter and MongoDB configured");
  await page.goto("/");
  await page.getByRole("button", { name: "세션 시작" }).click();
  await expect(page.getByText(/세션 [0-9a-f]{8}/)).toBeVisible({ timeout: 60_000 });
  await page.getByPlaceholder(/질문 입력/).fill(
    "wafer 단위로 중복 제거한 fail_value의 평균과 표준편차를 계산하고 히스토그램을 emit_plot으로 반드시 보여준 뒤 가설 검증 관점에서 판정해줘",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const card = page.locator(".analysis-card").last();
  await expect(card.getByText("run_python")).toBeVisible({ timeout: 180_000 });
  await expect(card.locator(".js-plotly-plot")).toBeVisible({ timeout: 180_000 });
  await expect(card.getByText(/판정|관찰/)).toBeVisible({ timeout: 180_000 });
});
```

Run it once before the final gate with `REPL_E2E_LIVE=1`; if backend/frontend integration is incomplete, retain the exact failing assertion in the task notes, fix only that integration defect, and rerun in Step 6.

- [ ] **Step 4: Run all deterministic backend and frontend tests**

Run:

```bash
cd 08-YieldAgent
PYTHONPATH=. pytest repl_agent/tests -q
cd repl_agent/frontend
npm test
npm run build
```

Expected: every command passes. Lint/syntax alone is not sufficient.

- [ ] **Step 5: Run real timeout and cancellation integration tests**

Run: `cd 08-YieldAgent && PYTHONPATH=. pytest repl_agent/tests/test_process_runtime.py -q -k 'timeout or cancel'`

Expected: both tests pass, terminate the actual child processes, and the pytest process exits immediately.

- [ ] **Step 6: Run the real browser/LLM/tool/plot gate**

Preconditions: local MongoDB is reachable at `mongodb://localhost:27017`; `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, and `DEFAULT_MODEL` are configured; Chromium is installed.

Run: `cd 08-YieldAgent/repl_agent/frontend && REPL_E2E_LIVE=1 npm run e2e:live`

Expected: one Playwright test passes after creating a real sample-data session, receiving an actual LLM `run_python` call, executing it in the worker, and rendering a real Plotly chart and conclusion.

- [ ] **Step 7: Manually verify recovery after destructive termination and shutdown**

With the servers still running, create a session, invoke the cancel endpoint against a run executing `while True: pass` through the runtime integration harness, confirm the UI displays runtime loss and disables input, then use “새 세션” and repeat a simple `print(df.shape)` analysis. Stop Uvicorn normally and confirm no worker process remains. Record the observed run IDs and worker exit status in the implementation handoff.

- [ ] **Step 8: Commit cleanup and E2E coverage**

```bash
git add 08-YieldAgent/agent_server.py 08-YieldAgent/repl_agent/frontend/playwright.config.ts 08-YieldAgent/repl_agent/frontend/e2e/repl-live.spec.ts 08-YieldAgent/repl_agent/frontend/package.json 08-YieldAgent/repl_agent/frontend/package-lock.json
git commit -m "test(repl): add live runtime e2e gate"
```

## Final Review Checklist

- [ ] Compare every implementation task against the approved design and confirm no P2/P3 feature entered the diff.
- [ ] Run `git diff --check` and inspect `git status --short` without touching unrelated user files.
- [ ] Confirm no module still calls `exec(code, ns)` in the FastAPI process; only `runtime/worker.py` may call `exec`.
- [ ] Confirm `scipy` is truly present in the worker namespace.
- [ ] Confirm all public errors use the approved stable codes and do not leak secrets or internal paths.
- [ ] Confirm cancellation and timeout kill the child process and mark the session `runtime_lost`.
- [ ] Confirm session deletion and FastAPI shutdown leave no child worker alive.
- [ ] Confirm backend tests, frontend tests/build, real process tests, and live Playwright gate all passed with captured command output.
