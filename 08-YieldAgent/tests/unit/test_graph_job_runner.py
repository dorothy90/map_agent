import sys
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from graph_job_runner import GraphRunRequest, GraphRunResult, run_graph


class FakeGraph:
    def __init__(self):
        self._chunks = []
        self.calls = []
        self.invocations = []
        self.state = SimpleNamespace(values={}, tasks=[])

    def streams(self, *chunks):
        self._chunks = list(chunks)

    async def astream(self, stream_input, *, config, stream_mode):
        self.calls.append((stream_input, config, stream_mode))
        for chunk in self._chunks:
            if "custom" in chunk:
                yield "custom", chunk["custom"]
            else:
                yield "updates", chunk

    async def aget_state(self, config):
        return self.state

    async def ainvoke(self, stream_input, config):
        self.invocations.append((stream_input, config))


async def async_false():
    return False


async def async_true():
    return True


async def async_emit(event):
    return None


@pytest.mark.asyncio
async def test_interrupt_returns_waiting_input():
    graph = FakeGraph()
    emitted = []

    async def emit(event):
        emitted.append(event)

    interrupt = {
        "interrupt_type": "missing_param",
        "param": "lotcd",
        "message": "제품코드",
        "route": "yield_agent",
        "fields": [],
    }
    graph.streams({"__interrupt__": [SimpleNamespace(value=interrupt)]})

    result = await run_graph(
        graph,
        GraphRunRequest(
            job_id="j1",
            owner_id="u1",
            session_id="s1",
            thread_id="h1:s1",
            query="q",
        ),
        emit,
        async_false,
    )

    assert result.outcome == "WAITING_INPUT"
    assert result.latest_interrupt == interrupt
    assert emitted[-1]["type"] == "interrupt"


@pytest.mark.asyncio
async def test_cancellation_stops_between_events():
    graph = FakeGraph()
    graph.streams({"custom": {"kind": "status", "message": "started"}})
    request = GraphRunRequest(
        job_id="j1",
        owner_id="u1",
        session_id="s1",
        thread_id="h1:s1",
        query="q",
    )

    result = await run_graph(graph, request, async_emit, async_true)

    assert result.outcome == "CANCELLED"


@pytest.mark.asyncio
async def test_initial_run_uses_namespaced_thread_and_trusted_owner():
    graph = FakeGraph()
    graph.streams({"yield_agent": {"messages": []}})
    request = GraphRunRequest(
        job_id="j1",
        owner_id="trusted-user",
        session_id="raw-session",
        thread_id="owner-hash:raw-session",
        query="수율 조회",
    )

    result = await run_graph(graph, request, async_emit, async_false)

    stream_input, config, modes = graph.calls[0]
    assert config["configurable"]["thread_id"] == "owner-hash:raw-session"
    assert stream_input["user_id"] == "trusted-user"
    assert stream_input["messages"][0].content == "수율 조회"
    assert modes == ["updates", "custom"]
    assert result.outcome == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_value", ["승인", {"lotcd": "4SS"}])
async def test_resume_uses_command_with_original_value(resume_value):
    graph = FakeGraph()
    request = GraphRunRequest(
        job_id="j1",
        owner_id="u1",
        session_id="s1",
        thread_id="h1:s1",
        query="q",
        resume_value=resume_value,
    )

    await run_graph(graph, request, async_emit, async_false)

    stream_input = graph.calls[0][0]
    assert isinstance(stream_input, Command)
    assert stream_input.resume == resume_value


@pytest.mark.asyncio
async def test_unrelated_legacy_resume_drains_optional_gate_and_starts_fresh(monkeypatch):
    graph = FakeGraph()
    graph.state = SimpleNamespace(
        values={"turn_id": "old-turn"},
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={
            "interrupt_type": "task_confirm",
            "message": "후속 분석",
        })])],
    )
    monkeypatch.setitem(
        sys.modules,
        "supervisor",
        SimpleNamespace(_resume_is_interrupt_answer=lambda value, pending: False),
    )
    request = GraphRunRequest(
        "j1", "trusted", "s1", "hash:s1", "새 질문", "기존 게이트와 무관"
    )

    await run_graph(graph, request, async_emit, async_false)

    assert isinstance(graph.invocations[0][0], Command)
    assert graph.invocations[0][0].resume == ""
    fresh_input = graph.calls[0][0]
    assert isinstance(fresh_input, dict)
    assert fresh_input["messages"][0].content == "새 질문"


@pytest.mark.asyncio
async def test_plan_review_emits_message_then_interrupt():
    graph = FakeGraph()
    emitted = []

    async def emit(event):
        emitted.append(event)

    graph.streams({
        "__interrupt__": [SimpleNamespace(value={
            "type": "plan_review",
            "tasks": [{"task_id": "t1", "agent": "yield_agent", "goal": "수율", "params": {}}],
            "missing_params": [],
        })]
    })

    result = await run_graph(
        graph,
        GraphRunRequest("j1", "u1", "s1", "h1:s1", "q"),
        emit,
        async_false,
    )

    assert [event["type"] for event in emitted[-2:]] == ["message", "interrupt"]
    assert emitted[-1]["interrupt_type"] == "plan_review"
    assert result.outcome == "WAITING_INPUT"


@pytest.mark.asyncio
async def test_updates_are_translated_and_final_result_is_normalized():
    graph = FakeGraph()
    emitted = []

    async def emit(event):
        emitted.append(event)

    message = SimpleNamespace(
        content="완료",
        name="yield_agent",
        additional_kwargs={},
    )
    graph.streams(
        {"custom": {"kind": "status", "message": "started"}},
        {"yield_agent": {
            "step_count": 1,
            "messages": [message],
            "agent_suggestion": "다음 분석",
        }},
    )

    result = await run_graph(
        graph,
        GraphRunRequest("j1", "u1", "s1", "h1:s1", "q"),
        emit,
        async_false,
    )

    event_types = [event["type"] for event in emitted]
    assert event_types[0] == "stream_start"
    assert "status" in event_types
    assert "node_complete" in event_types
    assert "message" in event_types
    assert "suggestion" in event_types
    assert event_types[-1] == "stream_end"
    assert result.final_result["messages"] == [{"agent": "yield_agent", "content": "완료"}]
    assert result.final_result["suggestion"] == "다음 분석"
    assert result.final_result["step_count"] == 1


class RealGraphState(TypedDict, total=False):
    messages: list
    user_id: str
    answer: str


@pytest.mark.asyncio
async def test_real_langgraph_interrupt_and_dict_resume_use_same_checkpoint():
    def ask_for_input(state: RealGraphState):
        answer = interrupt({
            "interrupt_type": "missing_param",
            "param": "lotcd",
            "message": "제품코드",
            "route": "yield_agent",
            "fields": [{"slot": "lotcd", "label": "제품코드", "type": "lotcd"}],
        })
        return {"answer": answer["lotcd"]}

    builder = StateGraph(RealGraphState)
    builder.add_node("ask", ask_for_input)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    events = []

    async def emit(event):
        events.append(event)

    initial = GraphRunRequest("j1", "trusted", "s1", "hash:s1", "조회")
    waiting = await run_graph(graph, initial, emit, async_false)
    resumed = await run_graph(
        graph,
        GraphRunRequest(
            "j1", "trusted", "s1", "hash:s1", "조회", {"lotcd": "4SS"}
        ),
        emit,
        async_false,
    )

    snapshot = await graph.aget_state({"configurable": {"thread_id": "hash:s1"}})
    assert waiting.outcome == "WAITING_INPUT"
    assert resumed.outcome == "SUCCEEDED"
    assert snapshot.values["answer"] == "4SS"
    assert snapshot.values["user_id"] == "trusted"


@pytest.mark.asyncio
async def test_legacy_route_is_streaming_adapter_for_runner(monkeypatch):
    import agent_server
    from models import ChatRequest

    inserted = []

    class ChatTurns:
        async def insert_one(self, document):
            inserted.append(document)

    async def fake_run_graph(graph, request, emit, cancelled):
        await emit({"type": "stream_start", "session_id": request.session_id, "query": request.query})
        return GraphRunResult(
            outcome="SUCCEEDED",
            final_result={
                "messages": [],
                "artifacts": [],
                "suggestion": "",
                "step_count": 0,
                "elapsed": 0.0,
                "user_id": "",
                "memory_feedback": [],
            },
        )

    monkeypatch.setattr(agent_server, "run_graph", fake_run_graph)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            graph=object(),
            motor_db=SimpleNamespace(chat_turns=ChatTurns()),
        )),
        is_disconnected=async_false,
    )

    response = await agent_server.chat_stream(
        ChatRequest(query="q", session_id="s1", user_id="legacy-user"), request
    )
    chunks = [chunk async for chunk in response.body_iterator]

    first_chunk = chunks[0].decode() if isinstance(chunks[0], bytes) else chunks[0]
    assert '"type": "stream_start"' in first_chunk
    assert inserted[0]["session_id"] == "s1"
