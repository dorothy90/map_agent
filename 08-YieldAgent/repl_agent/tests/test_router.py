from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from repl_agent import router as router_module
from repl_agent import session_store
from repl_agent import tools as tools_module
from repl_agent.router import router as repl_router
from repl_agent.runtime.base import ExecutionError, ExecutionResult, PlotArtifact


ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": 3.5}]


class FakeRuntime:
    def __init__(self, result: ExecutionResult | None = None):
        self.result = result
        self.created: list[str] = []
        self.executed: list[tuple[str, str, str, float]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.closed: list[str] = []

    def create_session(self, session_id, rows, query):
        self.created.append(session_id)

    def execute(self, session_id, run_id, code, timeout_seconds):
        self.executed.append((session_id, run_id, code, timeout_seconds))
        assert self.result is not None
        return self.result

    def cancel(self, session_id, run_id):
        self.cancelled.append((session_id, run_id))
        return True

    def close_session(self, session_id):
        self.closed.append(session_id)

    def close_all(self):
        pass


class FakeAgent:
    def __init__(self, state: dict | None = None):
        self.state = state or {}
        self.inputs = None
        self.config = None
        self.stream_mode = None

    async def aget_state(self, config):
        return SimpleNamespace(values=self.state)

    async def astream(self, inputs, config, stream_mode):
        self.inputs = inputs
        self.config = config
        self.stream_mode = stream_mode
        yield "messages", (AIMessageChunk(content="결과"), {})


class FakeToolAgent(FakeAgent):
    async def astream(self, inputs, config, stream_mode):
        yield "updates", {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{
                "id": "tool-1",
                "name": "run_python",
                "args": {"code": "print(1)"},
            }],
        )]}}
        yield "updates", {"tools": {"messages": [ToolMessage(
            content='{"status":"success","stdout":"1\\n","execution_time_ms":1}',
            tool_call_id="tool-1",
            name="run_python",
        )]}}


class FakeArtifactAgent(FakeAgent):
    async def astream(self, inputs, config, stream_mode):
        yield "custom", {
            "kind": "artifacts",
            "tool_call_id": "tool-1",
            "artifacts": [{
                "artifact_id": "plot-1",
                "kind": "plotly",
                "mime_type": "application/vnd.plotly.v1+json",
                "spec": {"data": [], "layout": {}},
            }],
        }


class FailingAgent(FakeAgent):
    async def astream(self, inputs, config, stream_mode):
        if False:
            yield None
        raise RuntimeError("developer bug")


class BlockingAgent(FakeAgent):
    async def astream(self, inputs, config, stream_mode):
        if False:
            yield None
        await asyncio.Event().wait()


class TrackingBlockingAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.finalized = asyncio.Event()

    async def astream(self, inputs, config, stream_mode):
        try:
            self.started.set()
            if False:
                yield None
            await asyncio.Event().wait()
        finally:
            self.finalized.set()


def decode_sse(body: str) -> list[dict]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in body.strip().split("\n\n")
    ]


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    async def fetch_rows(lotcd, start, end, fail_name):
        return ROWS

    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_fetch_rows", fetch_rows)
    monkeypatch.setattr(session_store, "_runtime", fake)
    session_store.close_all_sessions()
    yield
    session_store.close_all_sessions()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(repl_router, prefix="/repl")
    return TestClient(app)


@pytest.fixture
def ready_session() -> str:
    info = asyncio.run(session_store.create_session(
        "L12345", "2026-02-01", "2026-04-30", "OPEN"
    ))
    return info["session_id"]


@pytest.fixture
def busy_session(ready_session) -> str:
    session_store.begin_run(ready_session, "existing-run")
    return ready_session


def test_chat_stream_uses_standard_envelope(client, ready_session, monkeypatch):
    monkeypatch.setattr(router_module, "get_agent", lambda: FakeAgent())
    response = client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "mean"}
    )

    events = decode_sse(response.text)

    assert events[0]["type"] == "RUN_STARTED"
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert [event["type"] for event in events[1:-1]] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"
    ]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert {event["thread_id"] for event in events} == {ready_session}
    assert len({event["run_id"] for event in events}) == 1


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
    assert tool_events[-1]["result"]["stdout"] == "1\n"


def test_custom_artifacts_become_typed_events(client, ready_session, monkeypatch):
    monkeypatch.setattr(router_module, "get_agent", lambda: FakeArtifactAgent())

    events = decode_sse(client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "plot"}
    ).text)

    artifact = next(event for event in events if event["type"] == "ARTIFACT")
    assert artifact["tool_call_id"] == "tool-1"
    assert artifact["artifact"]["artifact_id"] == "plot-1"


def test_busy_session_returns_structured_409(client, busy_session):
    response = client.post(
        "/repl/chat", json={"session_id": busy_session, "query": "mean"}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "session_busy"


def test_missing_and_lost_sessions_have_structured_errors(
    client, ready_session, monkeypatch
):
    session_store.begin_run(ready_session, "lost-run")
    session_store.mark_runtime_lost(ready_session, "lost-run")

    missing = client.post(
        "/repl/chat", json={"session_id": "missing", "query": "mean"}
    )
    lost = client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "mean"}
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"
    assert lost.status_code == 410
    assert lost.json()["detail"]["code"] == "runtime_lost"


def test_run_registry_is_cleaned_after_stream(client, ready_session, monkeypatch):
    monkeypatch.setattr(router_module, "get_agent", lambda: FakeAgent())
    response = client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "mean"}
    )
    run_id = decode_sse(response.text)[0]["run_id"]

    assert router_module.run_registry.get(run_id) is None
    assert session_store.get_session(ready_session).status == "ready"


def test_agent_error_is_standardized_and_cleanup_keeps_runtime_ready(
    client, ready_session, monkeypatch
):
    monkeypatch.setattr(router_module, "get_agent", lambda: FailingAgent())

    events = decode_sse(client.post(
        "/repl/chat", json={"session_id": ready_session, "query": "mean"}
    ).text)

    assert [event["type"] for event in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["code"] == "agent_error"
    assert "RuntimeError: developer bug" in events[-1]["message"]
    assert session_store.get_session(ready_session).status == "ready"
    assert router_module.run_registry.get(events[0]["run_id"]) is None


def test_first_turn_preserves_summary_and_later_turn_does_not_repeat_it(
    client, ready_session, monkeypatch
):
    first = FakeAgent()
    monkeypatch.setattr(router_module, "get_agent", lambda: first)
    client.post("/repl/chat", json={"session_id": ready_session, "query": "mean"})

    first_content = first.inputs["messages"][0]["content"]
    assert "[세션 데이터]" in first_content
    assert first_content.endswith("[질문]\nmean")
    assert first.config["configurable"]["thread_id"] == ready_session
    assert first.config["configurable"]["run_id"]
    assert first.stream_mode == ["updates", "messages", "custom"]

    later = FakeAgent(state={"messages": [AIMessage(content="earlier")]})
    monkeypatch.setattr(router_module, "get_agent", lambda: later)
    client.post("/repl/chat", json={"session_id": ready_session, "query": "next"})

    assert later.inputs["messages"][0]["content"] == "next"


def test_cancel_endpoint_is_idempotent(client, monkeypatch):
    control = router_module.run_registry.register("run-1", "session-1")
    event_state_during_cancel = []

    def cancel(run_id):
        event_state_during_cancel.append(control.cancel_event.is_set())
        return run_id == "run-1"

    monkeypatch.setattr(router_module, "cancel_run", cancel)
    try:
        first = client.post("/repl/runs/run-1/cancel")
        second = client.post("/repl/runs/run-1/cancel")
    finally:
        router_module.run_registry.unregister("run-1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert event_state_during_cancel[0] is False
    assert control.cancel_event.is_set()


def test_stream_cancellation_emits_terminal_event_and_cleans_registry(
    ready_session, monkeypatch
):
    monkeypatch.setattr(router_module, "get_agent", lambda: BlockingAgent())

    async def cancel_stream():
        response = await router_module.chat(router_module.ChatIn(
            session_id=ready_session,
            query="mean",
        ))
        stream = response.body_iterator.__aiter__()
        first = decode_sse(await anext(stream))[0]
        control = router_module.run_registry.get(first["run_id"])
        assert control is not None
        control.cancel_event.set()
        remaining = [chunk async for chunk in stream]
        return first, [
            event
            for chunk in remaining
            for event in decode_sse(chunk)
        ]

    first, remaining = asyncio.run(cancel_stream())

    assert [first["type"], *[event["type"] for event in remaining]] == [
        "RUN_STARTED", "RUN_CANCELLED"
    ]
    assert router_module.run_registry.get(first["run_id"]) is None
    assert session_store.get_session(ready_session).status == "ready"


def test_idle_runtime_cancel_is_destructive_and_endpoint_is_idempotent(
    ready_session, monkeypatch
):
    agent = BlockingAgent()
    runtime = FakeRuntime()
    runtime.cancel = lambda session_id, run_id: False
    monkeypatch.setattr(router_module, "get_agent", lambda: agent)
    monkeypatch.setattr(session_store, "_runtime", runtime)

    async def cancel_stream():
        response = await router_module.chat(router_module.ChatIn(
            session_id=ready_session,
            query="mean",
        ))
        stream = response.body_iterator.__aiter__()
        started = decode_sse(await anext(stream))[0]
        first = await router_module.cancel_active_run(started["run_id"])
        events = [
            event
            async for chunk in stream
            for event in decode_sse(chunk)
        ]
        second = await router_module.cancel_active_run(started["run_id"])
        return started, events, first, second

    started, events, first, second = asyncio.run(cancel_stream())

    assert first["cancelled"] is True
    assert second["cancelled"] is False
    assert [started["type"], *[event["type"] for event in events]] == [
        "RUN_STARTED", "RUN_CANCELLED"
    ]
    assert runtime.cancelled == []
    assert runtime.closed == [ready_session]
    record = session_store.get_session(ready_session)
    assert record is not None
    assert record.status == "runtime_lost"
    assert record.active_run_id is None


def test_request_cancellation_awaits_child_task_and_finalizes_agent_stream(
    ready_session, monkeypatch
):
    agent = TrackingBlockingAgent()
    monkeypatch.setattr(router_module, "get_agent", lambda: agent)

    async def cancel_request():
        response = await router_module.chat(router_module.ChatIn(
            session_id=ready_session,
            query="mean",
        ))
        stream = response.body_iterator.__aiter__()
        started = decode_sse(await anext(stream))[0]
        consumer = asyncio.create_task(anext(stream))
        await agent.started.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await asyncio.sleep(0)
        pending_children = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        return started, pending_children

    started, pending_children = asyncio.run(cancel_request())

    assert pending_children == []
    assert agent.finalized.is_set()
    assert router_module.run_registry.get(started["run_id"]) is None
    assert session_store.get_session(ready_session).status == "ready"


def test_delete_session_closes_worker(client, ready_session, monkeypatch):
    closed = []
    monkeypatch.setattr(
        router_module,
        "close_session",
        lambda session_id: not closed.append(session_id),
    )

    response = client.delete(f"/repl/session/{ready_session}")

    assert response.status_code == 200
    assert closed == [ready_session]


def _invoke_run_python(session_id: str, run_id: str, tool_call_id: str = "tool-1"):
    return tools_module.run_python.func(
        code="print(1)",
        tool_call_id=tool_call_id,
        config={"configurable": {"thread_id": session_id, "run_id": run_id}},
    )


def test_run_python_uses_runtime_and_emits_correlated_artifacts(
    ready_session, monkeypatch
):
    artifact = PlotArtifact(
        artifact_id="plot-1",
        spec={"data": [], "layout": {}},
    )
    runtime = FakeRuntime(ExecutionResult(
        status="success",
        stdout="1\n",
        plots=[artifact],
        execution_time_ms=1,
    ))
    writes = []
    monkeypatch.setattr(tools_module, "runtime", runtime)
    monkeypatch.setattr(tools_module, "get_stream_writer", lambda: writes.append)
    session_store.begin_run(ready_session, "run-1")

    payload = json.loads(_invoke_run_python(ready_session, "run-1"))

    assert runtime.executed == [(ready_session, "run-1", "print(1)", 60)]
    assert payload["status"] == "success"
    assert "plots" not in payload
    assert writes == [{
        "kind": "artifacts",
        "tool_call_id": "tool-1",
        "artifacts": [artifact.model_dump()],
    }]


def test_run_python_description_documents_json_result_contract():
    description = tools_module.run_python.description

    assert all(field in description for field in [
        "status",
        "stdout",
        "stderr",
        "execution_time_ms",
        "error",
        "stdout_truncated",
        "stderr_truncated",
    ])


def test_healthy_python_error_keeps_session_runtime_available(
    ready_session, monkeypatch
):
    runtime = FakeRuntime(ExecutionResult(
        status="error",
        error=ExecutionError(
            code="python_runtime_error",
            exception_name="ValueError",
            message="bad",
        ),
        execution_time_ms=1,
    ))
    monkeypatch.setattr(tools_module, "runtime", runtime)
    monkeypatch.setattr(tools_module, "get_stream_writer", lambda: lambda event: None)
    session_store.begin_run(ready_session, "run-1")

    payload = json.loads(_invoke_run_python(ready_session, "run-1"))
    session_store.finish_run(ready_session, "run-1")

    assert payload["status"] == "error"
    assert session_store.get_session(ready_session).status == "ready"


@pytest.mark.parametrize("status", ["timeout", "cancelled", "runtime_lost"])
def test_destructive_python_result_marks_session_runtime_lost(
    ready_session, monkeypatch, status
):
    runtime = FakeRuntime(ExecutionResult(
        status=status,
        error=ExecutionError(
            code=f"execution_{status}",
            exception_name="PythonRuntimeError",
            message=status,
        ),
        execution_time_ms=0,
    ))
    monkeypatch.setattr(tools_module, "runtime", runtime)
    monkeypatch.setattr(tools_module, "get_stream_writer", lambda: lambda event: None)
    session_store.begin_run(ready_session, "run-1")

    payload = json.loads(_invoke_run_python(ready_session, "run-1"))

    assert payload["status"] == status
    assert session_store.get_session(ready_session).status == "runtime_lost"
