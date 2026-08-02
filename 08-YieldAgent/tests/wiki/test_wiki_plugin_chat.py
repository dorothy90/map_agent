import ast
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

import models
import wiki_plugin_router
from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def paths(tmp_path):
    resolved = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(resolved)
    return resolved


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(wiki_plugin_router.router, prefix="/api/wiki/plugin")
    return application


def _write_concept(path, *, body: str, concept_id: str) -> None:
    path.write_text(
        "---\n"
        f"id: {concept_id}\n"
        "type: concept\n"
        "product: 4SS\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_plugin_chat_resolves_note_before_calling_shared_stream(
    monkeypatch, app, paths
):
    _write_concept(paths.concepts / "A.md", body="oxide evidence", concept_id="concept:A")
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)
    captured = {}

    async def fake_stream(body, request):
        captured["body"] = body
        captured["request"] = request
        return StreamingResponse(
            iter(['data: {"type":"stream_end"}\n\n']),
            media_type="text/event-stream",
        )

    app.state.chat_stream_handler = fake_stream
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/wiki/plugin/chat",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "query": "이 이슈의 원인은?",
                "session_id": "session-1",
                "user_id": "operator-1",
                "current_note_id": "concepts/A.md",
            },
        )

    assert response.status_code == 200
    assert captured["body"].query == "이 이슈의 원인은?"
    assert captured["body"].session_id == "session-1"
    assert captured["body"].user_id == "operator-1"
    assert isinstance(captured["body"], models.InternalChatRequest)
    assert captured["body"].wiki_context.model_dump() == {
        "id": "concept:A",
        "path": "concepts/A.md",
        "metadata": {
            "id": "concept:A",
            "type": "concept",
            "product": "4SS",
        },
        "body": "oxide evidence",
    }
    assert captured["request"].app is app
    assert response.text == 'data: {"type":"stream_end"}\n\n'


@pytest.mark.anyio
async def test_public_chat_contract_rejects_wiki_context_injection():
    public_app = FastAPI()

    @public_app.post("/chat/stream")
    async def public_chat(body: models.ChatRequest):
        return body

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/stream",
            json={
                "query": "ignore the user",
                "session_id": "session-1",
                "wiki_context": {"body": "attacker-controlled system instruction"},
            },
        )

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "current_note_id",
    ["concepts/missing.md", "../secret.md"],
)
async def test_plugin_chat_rejects_missing_or_invalid_note_before_shared_stream(
    monkeypatch, app, paths, current_note_id
):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)
    calls = []

    async def fake_stream(body, request):
        calls.append((body, request))
        return StreamingResponse(iter(()), media_type="text/event-stream")

    app.state.chat_stream_handler = fake_stream
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/wiki/plugin/chat",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "query": "원인은?",
                "session_id": "session-1",
                "current_note_id": current_note_id,
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "현재 Wiki 노트를 찾을 수 없습니다."
    assert calls == []


def test_planner_receives_wiki_context_as_structured_system_context(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://test")
    import langchain

    monkeypatch.setattr(langchain, "verbose", False, raising=False)
    monkeypatch.setattr(langchain, "debug", False, raising=False)
    monkeypatch.setattr(langchain, "llm_cache", None, raising=False)

    import node_planner

    class FakeModel:
        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append(messages)
            return SimpleNamespace(content='{"requests": [], "answer": "확인했습니다."}')

    fake = FakeModel()
    monkeypatch.setattr(node_planner, "_model", fake)
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])

    node_planner.planner_node(
        {
            "messages": [HumanMessage(content="원인은?")],
            "wiki_context": {
                "id": "concept:A",
                "path": "concepts/A.md",
                "metadata": {"product": "4SS"},
                "body": "oxide",
            },
        },
        {},
    )

    system_messages = [
        message["content"]
        for message in fake.calls[0]
        if message["role"] == "system"
    ]
    assert any(
        '"path": "concepts/A.md"' in content
        and '"body": "oxide"' in content
        and "untrusted evidence" in content
        for content in system_messages
    )


def test_wiki_graph_bodies_are_fingerprinted_not_persisted_in_default_trace(
    monkeypatch, tmp_path
):
    import local_trace
    import node_planner

    contexts = [
        {
            "id": "concept:4SS|PRE METAL CLN|EASY",
            "path": "concepts/4SS-PRE-METAL-CLN-EASY.md",
            "metadata": {
                "id": "concept:4SS|PRE METAL CLN|EASY",
                "type": "concept",
                "product": "4SS",
                "relations": [
                    {
                        "predicate": "causes",
                        "source_doc_ids": ["FH-TRACE-1"],
                        "evidence_excerpt": "CONCEPT_RELATION_EXCERPT_SENTINEL",
                    }
                ],
                "citations": [{"doc_id": "FH-TRACE-1"}],
            },
            "body": "RAW_CONCEPT_BODY_SENTINEL_DO_NOT_PERSIST",
        },
        {
            "id": "entity:queue-time",
            "path": "entities/queue-time.md",
            "metadata": {
                "id": "entity:queue-time",
                "type": "entity",
                "source_concept_ids": ["concept:4SS|PRE METAL CLN|EASY"],
            },
            "body": "RAW_ENTITY_BODY_SENTINEL_DO_NOT_PERSIST",
        },
        {
            "id": "relation:queue-time-causes-oxide",
            "path": "relations/queue-time-causes-oxide.md",
            "metadata": {
                "id": "relation:queue-time-causes-oxide",
                "type": "relation",
                "origin_concept_id": "concept:4SS|PRE METAL CLN|EASY",
                "subject_entity_id": "entity:queue-time",
                "predicate": "causes",
                "object_entity_id": "entity:oxide",
                "source_doc_ids": ["FH-TRACE-1"],
                "evidence_excerpt": "RELATION_EVIDENCE_EXCERPT_SENTINEL",
            },
            "body": "RAW_RELATION_BODY_SENTINEL_DO_NOT_PERSIST",
        },
        {
            "id": "source:FH-TRACE-1",
            "path": "sources/FH-TRACE-1.md",
            "metadata": {
                "id": "source:FH-TRACE-1",
                "type": "source",
                "doc_id": "FH-TRACE-1",
            },
            "body": "RAW_SOURCE_BODY_SENTINEL_DO_NOT_PERSIST",
        },
    ]
    sentinels = [context["body"] for context in contexts]
    trace_json = tmp_path / "last_turns.json"
    trace_html = tmp_path / "last_turns.html"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: trace_html)
    monkeypatch.setattr(local_trace, "_last_turn_keep", lambda: 4)

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            serialized_messages = json.dumps(messages, ensure_ascii=False)
            sentinel = sentinels[self.calls - 1]
            assert sentinel in serialized_messages
            assert kwargs["config"]["callbacks"] == []
            return SimpleNamespace(
                content=json.dumps(
                    {"requests": [], "answer": f"echo: {sentinel}"},
                    ensure_ascii=False,
                )
            )

    callbacks = [object()]
    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: callbacks)
    for index, context in enumerate(contexts):
        tokens = local_trace.set_trace_context("trace-test", f"turn-test-{index}")
        try:
            local_trace.emit_trace_event(
                "user_turn_started",
                source="test",
                payload={"question_preview": "원인은?"},
            )
            node_planner.planner_node(
                {
                    "messages": [HumanMessage(content="원인은?")],
                    "wiki_context": context,
                },
                {},
            )
        finally:
            local_trace.reset_trace_context(tokens)
    local_trace._persist_turns()

    persisted = json.loads(trace_json.read_text(encoding="utf-8"))
    serialized = json.dumps(persisted, ensure_ascii=False)
    planner_inputs = [
        detail["payload"]
        for turn in persisted
        for detail in turn["details"]
        if detail["label"] == "planner.input"
    ]
    for sentinel in (
        *sentinels,
        "CONCEPT_RELATION_EXCERPT_SENTINEL",
        "RELATION_EVIDENCE_EXCERPT_SENTINEL",
    ):
        assert sentinel not in serialized
    for context in contexts:
        assert context["id"] in serialized
        assert hashlib.sha256(context["body"].encode()).hexdigest() in serialized
        provider_output = json.dumps(
            {"requests": [], "answer": f'echo: {context["body"]}'},
            ensure_ascii=False,
        )
        assert hashlib.sha256(provider_output.encode()).hexdigest() in serialized
        assert hashlib.sha256(f'echo: {context["body"]}'.encode()).hexdigest() in serialized
    assert len(planner_inputs) == 4
    assert '"relation_count": 1' in planner_inputs[0]["meta"]
    assert '"predicate": "causes"' in planner_inputs[2]["meta"]
    assert '"source_doc_ids": ["FH-TRACE-1"]' in planner_inputs[2]["meta"]
    assert '"doc_id": "FH-TRACE-1"' in planner_inputs[3]["meta"]


def test_non_wiki_planner_trace_preserves_provider_output(monkeypatch, tmp_path):
    import local_trace
    import node_planner

    provider_output = "NON_WIKI_PROVIDER_OUTPUT_SENTINEL"
    trace_json = tmp_path / "last_turns.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")

    class FakeModel:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {"requests": [], "answer": provider_output},
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    tokens = local_trace.set_trace_context("trace-test", "turn-test")
    try:
        local_trace.emit_trace_event(
            "user_turn_started",
            source="test",
            payload={"question_preview": "원인은?"},
        )
        node_planner.planner_node(
            {"messages": [HumanMessage(content="원인은?")]},
            {},
        )
    finally:
        local_trace.reset_trace_context(tokens)
    local_trace._persist_turns()

    assert provider_output in trace_json.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("source_doc_ids", "first_source_id"),
    [
        (None, "FH-CITATION-0"),
        ("FH-STRING", "FH-STRING"),
        (["FH-LIST", "FH-LIST"], "FH-LIST"),
    ],
)
def test_wiki_trace_metadata_from_malformed_oversized_yaml_is_bounded(
    monkeypatch, tmp_path, paths, source_doc_ids, first_source_id
):
    import local_trace
    import node_planner
    from wiki_plugin_notes import load_note_context

    long_id = "entity:" + "X" * 500
    unsafe_nested = "NESTED_METADATA_SENTINEL_MUST_NOT_PERSIST"
    citation_ids = [f"FH-CITATION-{index}" for index in range(30)]
    note_path = paths.entities / f"malformed-{first_source_id}.md"
    note_path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="MALFORMED_YAML_BODY_SENTINEL",
                id=long_id,
                type={"nested": unsafe_nested},
                predicate=[unsafe_nested],
                origin_concept_id=123,
                source_doc_ids=source_doc_ids,
                citations=[
                    None,
                    *({"doc_id": doc_id} for doc_id in citation_ids),
                    {"doc_id": citation_ids[0]},
                    {"doc_id": "Y" * 500},
                ],
            )
        ),
        encoding="utf-8",
    )
    loaded = load_note_context(paths, f"entities/{note_path.name}")
    context = {
        "id": loaded["metadata"].get("id"),
        "path": loaded["note_path"],
        "metadata": loaded["metadata"],
        "body": loaded["body_markdown"],
    }
    trace_json = tmp_path / f"{first_source_id}.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")

    class FakeModel:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(
                content='{"requests": [], "answer": "확인했습니다."}'
            )

    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    tokens = local_trace.set_trace_context("trace-test", "turn-test")
    try:
        local_trace.emit_trace_event(
            "user_turn_started",
            source="test",
            payload={"question_preview": "원인은?"},
        )
        node_planner.planner_node(
            {
                "messages": [HumanMessage(content="원인은?")],
                "wiki_context": context,
            },
            {},
        )
    finally:
        local_trace.reset_trace_context(tokens)
    local_trace._persist_turns()

    persisted = json.loads(trace_json.read_text(encoding="utf-8"))
    planner_input = next(
        detail["payload"]
        for detail in persisted[0]["details"]
        if detail["label"] == "planner.input"
    )
    trace_context = json.loads(planner_input["meta"].split("\n", 1)[1])
    trace_metadata = trace_context["metadata"]
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert unsafe_nested not in serialized
    assert "MALFORMED_YAML_BODY_SENTINEL" not in serialized
    assert trace_metadata["id"] == long_id[:160]
    assert trace_metadata["origin_concept_id"] == "123"
    assert "type" not in trace_metadata
    assert "predicate" not in trace_metadata
    assert trace_metadata["source_doc_ids"][0] == first_source_id
    assert len(trace_metadata["source_doc_ids"]) == 20
    assert len(set(trace_metadata["source_doc_ids"])) == 20
    assert all(len(doc_id) <= 160 for doc_id in trace_metadata["source_doc_ids"])


class _StreamTraceGraph:
    def __init__(self):
        self.planner_update = {}

    async def aget_state(self, config):
        return SimpleNamespace(values={}, tasks=[])

    async def astream(self, stream_input, config, stream_mode):
        import node_planner

        self.planner_update = node_planner.planner_node(stream_input, config)
        yield "updates", {"planner": self.planner_update}


class _PlannerSupervisorStreamTraceGraph:
    def __init__(self):
        self.custom_events = []
        self.planner_update = {}
        self.supervisor_command = None

    async def aget_state(self, config):
        return SimpleNamespace(values={}, tasks=[])

    async def astream(self, stream_input, config, stream_mode):
        import node_planner
        import node_supervisor

        self.planner_update = node_planner.planner_node(stream_input, config)
        yield "updates", {"planner": self.planner_update}

        state = {**stream_input, **self.planner_update}
        self.supervisor_command = node_supervisor.supervisor_node(state, config)
        for event in self.custom_events:
            yield "custom", dict(event)
        yield "updates", {"supervisor": self.supervisor_command.update}


class _PlannerSupervisorAgentStreamTraceGraph(_PlannerSupervisorStreamTraceGraph):
    def __init__(self):
        super().__init__()
        self.agent_update = {}

    async def astream(self, stream_input, config, stream_mode):
        import fail_history_agent
        import node_planner
        import node_supervisor

        self.planner_update = node_planner.planner_node(stream_input, config)
        yield "updates", {"planner": self.planner_update}

        state = {**stream_input, **self.planner_update}
        self.supervisor_command = node_supervisor.supervisor_node(state, config)
        for event in self.custom_events:
            yield "custom", dict(event)
        yield "updates", {"supervisor": self.supervisor_command.update}

        state = {**state, **self.supervisor_command.update}
        self.agent_update = fail_history_agent.fail_history_agent_node(state, config)
        yield "updates", {"fail_history_agent": self.agent_update}


class _StreamTraceTurns:
    def __init__(self):
        self.documents = []

    async def insert_one(self, document):
        self.documents.append(document)


class _StreamTraceClient:
    def set_current_trace_io(self, **kwargs):
        return None

    def flush(self):
        return None


async def _consume_stream_response(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_kind",
    ["direct_answer", "canonical_slot", "canonical_retry_slot"],
)
async def test_wiki_shared_stream_diagnostics_omit_provider_echo(
    monkeypatch, tmp_path, provider_kind
):
    import agent_server
    import local_trace
    import node_planner

    sentinel = f"RAW_WIKI_{provider_kind.upper()}_SENTINEL"
    if provider_kind == "direct_answer":
        provider_content = {
            "requests": [],
            "answer": f"echo: {sentinel}",
        }
    else:
        provider_content = {
            "requests": [
                {
                    "intent": "fail_history_search",
                    "agent": "fail_history_agent",
                    "slots": {"dh_query": sentinel},
                    "goal": "불량 이력 조회",
                }
            ],
            "answer": "",
        }

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            if provider_kind == "canonical_retry_slot" and self.calls == 1:
                return SimpleNamespace(content='{"requests": [], "answer": ""}')
            return SimpleNamespace(
                content=json.dumps(provider_content, ensure_ascii=False)
            )

    trace_json = tmp_path / f"{provider_kind}.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")
    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    monkeypatch.setattr(agent_server, "get_client", lambda: _StreamTraceClient())
    monkeypatch.setattr(agent_server, "SIMULATED_STREAM_DELAY", 0)

    turns = _StreamTraceTurns()
    graph = _StreamTraceGraph()
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph=graph,
                motor_db=SimpleNamespace(chat_turns=turns),
            )
        )
    )
    request = models.InternalChatRequest(
        query="원인은?",
        session_id="session-wiki-trace",
        wiki_context={
            "id": "concept:trace",
            "path": "concepts/trace.md",
            "metadata": {"id": "concept:trace", "type": "concept"},
            "body": sentinel,
        },
    )

    response = await agent_server._chat_stream(request, req)
    sse_text = await _consume_stream_response(response)
    local_trace._persist_turns()

    persisted_trace = trace_json.read_text(encoding="utf-8")
    persisted_session = json.dumps(turns.documents, ensure_ascii=False, default=str)
    assert sentinel not in persisted_trace
    assert hashlib.sha256(sentinel.encode()).hexdigest() in persisted_trace
    if provider_kind == "direct_answer":
        assert sentinel in sse_text
        assert sentinel in persisted_session
    else:
        assert graph.planner_update["task_plan"][0]["params"]["dh_query"] == sentinel


@pytest.mark.anyio
async def test_non_wiki_shared_stream_preserves_provider_answer_in_trace_and_session(
    monkeypatch, tmp_path
):
    import agent_server
    import local_trace
    import node_planner

    sentinel = "NON_WIKI_SHARED_STREAM_SENTINEL"

    class FakeModel:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {"requests": [], "answer": sentinel}, ensure_ascii=False
                )
            )

    trace_json = tmp_path / "non-wiki.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")
    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    monkeypatch.setattr(agent_server, "get_client", lambda: _StreamTraceClient())
    monkeypatch.setattr(agent_server, "SIMULATED_STREAM_DELAY", 0)

    turns = _StreamTraceTurns()
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph=_StreamTraceGraph(),
                motor_db=SimpleNamespace(chat_turns=turns),
            )
        )
    )
    response = await agent_server._chat_stream(
        models.ChatRequest(query="원인은?", session_id="session-public-trace"),
        req,
    )
    sse_text = await _consume_stream_response(response)
    local_trace._persist_turns()

    assert sentinel in sse_text
    assert sentinel in trace_json.read_text(encoding="utf-8")
    assert sentinel in json.dumps(turns.documents, ensure_ascii=False, default=str)


@pytest.mark.anyio
@pytest.mark.parametrize("provider_kind", ["canonical_slot", "canonical_retry_slot"])
async def test_wiki_planner_supervisor_stream_diagnostics_omit_provider_echo(
    monkeypatch, tmp_path, provider_kind
):
    import agent_server
    import local_trace
    import node_planner
    import node_supervisor

    slot_sentinel = f"WIKI_{provider_kind.upper()}_SLOT_SENTINEL"
    goal_sentinel = f"WIKI_{provider_kind.upper()}_GOAL_SENTINEL"
    provider_content = {
        "requests": [
            {
                "intent": "fail_history_search",
                "agent": "fail_history_agent",
                "slots": {"dh_query": slot_sentinel},
                "goal": f"분석 목표 {goal_sentinel}",
            }
        ],
        "answer": "",
    }

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            if provider_kind == "canonical_retry_slot" and self.calls == 1:
                return SimpleNamespace(content='{"requests": [], "answer": ""}')
            return SimpleNamespace(
                content=json.dumps(provider_content, ensure_ascii=False)
            )

    trace_json = tmp_path / f"planner-supervisor-{provider_kind}.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")
    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    monkeypatch.setattr(agent_server, "get_client", lambda: _StreamTraceClient())
    monkeypatch.setattr(agent_server, "SIMULATED_STREAM_DELAY", 0)

    graph = _PlannerSupervisorStreamTraceGraph()

    def capture_custom(kind, event):
        payload = event.model_dump() if hasattr(event, "model_dump") else event
        graph.custom_events.append({"kind": kind, **payload})

    monkeypatch.setattr(node_supervisor, "stream_event", capture_custom)
    turns = _StreamTraceTurns()
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph=graph,
                motor_db=SimpleNamespace(chat_turns=turns),
            )
        )
    )
    request = models.InternalChatRequest(
        query="원인은?",
        session_id=f"session-{provider_kind}",
        wiki_context={
            "id": "concept:trace",
            "path": "concepts/trace.md",
            "metadata": {"id": "concept:trace", "type": "concept"},
            "body": f"{slot_sentinel}\n{goal_sentinel}",
        },
    )

    response = await agent_server._chat_stream(request, req)
    sse_text = await _consume_stream_response(response)
    local_trace._persist_turns()

    persisted_trace = trace_json.read_text(encoding="utf-8")
    persisted = json.loads(persisted_trace)
    details = {detail["label"]: detail["payload"] for detail in persisted[0]["details"]}
    assert {
        "supervisor.current_task",
        "supervisor.dispatch_state",
        "stream.custom",
    } <= details.keys()
    assert slot_sentinel not in persisted_trace
    assert goal_sentinel not in persisted_trace
    serialized_params = json.dumps(
        {"dh_query": slot_sentinel}, ensure_ascii=False, sort_keys=True
    )
    assert hashlib.sha256(serialized_params.encode()).hexdigest() in persisted_trace
    assert hashlib.sha256(f"분석 목표 {goal_sentinel}".encode()).hexdigest() in persisted_trace
    assert graph.supervisor_command.goto == "fail_history_agent"
    current_task = graph.supervisor_command.update["current_task"]
    assert current_task["params"]["dh_query"] == slot_sentinel
    assert current_task["goal"] == f"분석 목표 {goal_sentinel}"
    assert goal_sentinel in sse_text
    assert any(
        goal_sentinel in json.dumps(event, ensure_ascii=False)
        for event in graph.custom_events
    )


@pytest.mark.anyio
async def test_non_wiki_planner_supervisor_stream_preserves_task_diagnostics(
    monkeypatch, tmp_path
):
    import agent_server
    import local_trace
    import node_planner
    import node_supervisor

    slot_sentinel = "NON_WIKI_SUPERVISOR_SLOT_SENTINEL"
    goal_sentinel = "NON_WIKI_SUPERVISOR_GOAL_SENTINEL"

    class FakeModel:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "requests": [
                            {
                                "intent": "fail_history_search",
                                "agent": "fail_history_agent",
                                "slots": {"dh_query": slot_sentinel},
                                "goal": goal_sentinel,
                            }
                        ],
                        "answer": "",
                    },
                    ensure_ascii=False,
                )
            )

    trace_json = tmp_path / "non-wiki-planner-supervisor.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")
    monkeypatch.setattr(node_planner, "_model", FakeModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    monkeypatch.setattr(agent_server, "get_client", lambda: _StreamTraceClient())
    monkeypatch.setattr(agent_server, "SIMULATED_STREAM_DELAY", 0)

    graph = _PlannerSupervisorStreamTraceGraph()

    def capture_custom(kind, event):
        payload = event.model_dump() if hasattr(event, "model_dump") else event
        graph.custom_events.append({"kind": kind, **payload})

    monkeypatch.setattr(node_supervisor, "stream_event", capture_custom)
    turns = _StreamTraceTurns()
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph=graph,
                motor_db=SimpleNamespace(chat_turns=turns),
            )
        )
    )

    response = await agent_server._chat_stream(
        models.ChatRequest(query="원인은?", session_id="session-public-supervisor"),
        req,
    )
    await _consume_stream_response(response)
    local_trace._persist_turns()

    persisted_trace = trace_json.read_text(encoding="utf-8")
    assert slot_sentinel in persisted_trace
    assert goal_sentinel in persisted_trace
    assert (
        graph.supervisor_command.update["current_task"]["params"]["dh_query"]
        == slot_sentinel
    )


def test_planner_langfuse_observation_disables_output_capture():
    import node_planner

    decorator = inspect.getsource(node_planner.planner_node).splitlines()[0]
    assert "capture_input=False" in decorator
    assert "capture_output=False" in decorator


def test_wiki_chat_runtime_observations_disable_payload_capture():
    import node_planner

    runtime_root = Path(node_planner.__file__).parent
    unsafe_observations = []
    for module_path in sorted(runtime_root.glob("*.py")):
        if module_path.name == "wiki_summarizer.py":
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "observe"
                ):
                    continue
                options = {keyword.arg: keyword.value for keyword in decorator.keywords}
                input_disabled = isinstance(
                    options.get("capture_input"), ast.Constant
                ) and options["capture_input"].value is False
                output_disabled = isinstance(
                    options.get("capture_output"), ast.Constant
                ) and options["capture_output"].value is False
                if not (input_disabled and output_disabled):
                    unsafe_observations.append(f"{module_path.name}:{node.name}")

    assert unsafe_observations == []


def test_langfuse_span_output_respects_wiki_capture_context(monkeypatch):
    import lf_utils

    outputs = []
    client = SimpleNamespace(
        update_current_span=lambda **kwargs: outputs.append(kwargs)
    )
    monkeypatch.setattr(lf_utils, "get_client", lambda: client)

    token = lf_utils.set_lf_capture_disabled(True)
    try:
        lf_utils.update_lf_span_output({"value": "WIKI_SENTINEL"})
    finally:
        lf_utils.reset_lf_capture_disabled(token)
    assert outputs == []

    lf_utils.update_lf_span_output({"value": "NON_WIKI_VALUE"})
    assert outputs == [{"output": {"value": "NON_WIKI_VALUE"}}]


@pytest.mark.anyio
async def test_wiki_stream_disables_callbacks_through_reachable_agent(
    monkeypatch, tmp_path
):
    import agent_server
    import fail_history_agent
    import lf_utils
    import local_trace
    import node_planner
    import node_supervisor

    sentinel = "WIKI_DOWNSTREAM_AGENT_CALLBACK_SENTINEL"

    class PlannerModel:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "requests": [
                            {
                                "intent": "fail_history_search",
                                "agent": "fail_history_agent",
                                "slots": {"dh_query": sentinel},
                                "goal": sentinel,
                            }
                        ],
                        "answer": "",
                    }
                )
            )

    class AgentModel:
        def __init__(self):
            self.callback_lists = []
            self.inputs = []

        def invoke(self, messages, config):
            self.callback_lists.append(config.get("callbacks"))
            self.inputs.append(str(messages))
            return SimpleNamespace(content="분석 결과 [FH-doc-1]")

    class LangfuseClient:
        def get_current_trace_id(self):
            return "trace-id"

        def get_current_observation_id(self):
            return "observation-id"

    agent_model = AgentModel()
    trace_json = tmp_path / "downstream-agent-callbacks.json"
    monkeypatch.setattr(local_trace, "_TURNS", [])
    monkeypatch.setattr(local_trace, "_TURNS_LOADED", True)
    monkeypatch.setattr(local_trace, "_last_turns_json_path", lambda: trace_json)
    monkeypatch.setattr(local_trace, "_last_turn_path", lambda: tmp_path / "trace.html")
    monkeypatch.setattr(node_planner, "_model", PlannerModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])
    monkeypatch.setattr(fail_history_agent, "_fh_model", agent_model)
    monkeypatch.setattr(
        fail_history_agent,
        "do_search",
        lambda **kwargs: {
            "retrieval_mode": "baseline",
            "results": [
                {
                    "doc_id": "doc-1",
                    "product": "4SS",
                    "fail_type": "IOFF",
                    "cause_oper": "PT1H",
                    "cause": "원인",
                    "action": "조치",
                }
            ],
        },
    )
    monkeypatch.setattr(lf_utils, "get_client", lambda: LangfuseClient())
    monkeypatch.setattr(lf_utils, "llm_trace_handler", lambda: "local-callback")
    monkeypatch.setattr(lf_utils, "_LFHandler", lambda **kwargs: "remote-callback")
    monkeypatch.setattr(agent_server, "get_client", lambda: _StreamTraceClient())
    monkeypatch.setattr(agent_server, "SIMULATED_STREAM_DELAY", 0)

    graph = _PlannerSupervisorAgentStreamTraceGraph()

    def capture_custom(kind, event):
        payload = event.model_dump() if hasattr(event, "model_dump") else event
        graph.custom_events.append({"kind": kind, **payload})

    monkeypatch.setattr(node_supervisor, "stream_event", capture_custom)
    turns = _StreamTraceTurns()
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph=graph,
                motor_db=SimpleNamespace(chat_turns=turns),
            )
        )
    )
    request = models.InternalChatRequest(
        query="원인은?",
        session_id="session-downstream-agent",
        wiki_context={
            "id": "concept:trace",
            "path": "concepts/trace.md",
            "metadata": {"id": "concept:trace", "type": "concept"},
            "body": sentinel,
        },
    )

    response = await agent_server._chat_stream(request, req)
    await _consume_stream_response(response)

    assert agent_model.callback_lists == [[]]
    assert sentinel in agent_model.inputs[0]
    assert lf_utils.lf_callbacks() == ["local-callback", "remote-callback"]


def test_planner_propagates_model_invocation_failure(monkeypatch):
    import node_planner

    provider_error = RuntimeError("provider unavailable")

    class FailingModel:
        def invoke(self, messages, **kwargs):
            raise provider_error

    monkeypatch.setattr(node_planner, "_model", FailingModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])

    with pytest.raises(RuntimeError) as raised:
        node_planner.planner_node(
            {"messages": [HumanMessage(content="원인은?")]},
            {},
        )

    assert raised.value is provider_error


def test_planner_keeps_natural_fallback_for_invalid_json(monkeypatch):
    import node_planner

    class InvalidJsonThenFallbackModel:
        def __init__(self):
            self.responses = iter(("not json", "자연어 안내"))

        def invoke(self, messages, **kwargs):
            return SimpleNamespace(content=next(self.responses))

    monkeypatch.setattr(node_planner, "_model", InvalidJsonThenFallbackModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])

    result = node_planner.planner_node(
        {"messages": [HumanMessage(content="안녕하세요")]},
        {},
    )

    assert result["messages"][0].content == "자연어 안내"


def test_planner_propagates_fallback_model_invocation_failure(monkeypatch):
    import node_planner

    provider_error = RuntimeError("fallback provider unavailable")

    class InvalidJsonThenFailureModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="not json")
            raise provider_error

    monkeypatch.setattr(node_planner, "_model", InvalidJsonThenFailureModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])

    with pytest.raises(RuntimeError) as raised:
        node_planner.planner_node(
            {"messages": [HumanMessage(content="안녕하세요")]},
            {},
        )

    assert raised.value is provider_error


def test_planner_propagates_empty_retry_model_invocation_failure(monkeypatch):
    import node_planner

    provider_error = RuntimeError("retry provider unavailable")

    class EmptyPlanThenFailureModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content='{"requests": [], "answer": ""}')
            raise provider_error

    monkeypatch.setattr(node_planner, "_model", EmptyPlanThenFailureModel())
    monkeypatch.setattr(node_planner, "_lf_callbacks", lambda: [])

    with pytest.raises(RuntimeError) as raised:
        node_planner.planner_node(
            {"messages": [HumanMessage(content="분석해줘")]},
            {},
        )

    assert raised.value is provider_error


def test_message_event_carries_citations_from_structured_results():
    from agent_sessions import citations_from_fail_history_results

    citations = citations_from_fail_history_results(
        [
            {
                "doc_id": "FH-1",
                "source_file": "FH-1.pptx",
                "download_url": "https://internal/FH-1.pptx",
            }
        ],
        source_paths={"FH-1": "sources/FH-1.md"},
    )

    assert citations == [
        models.CitationData(
            doc_id="FH-1",
            label="FH-1",
            source_path="sources/FH-1.md",
            download_url="https://internal/FH-1.pptx",
        )
    ]


def test_citations_deduplicate_and_do_not_infer_missing_links():
    from agent_sessions import citations_from_fail_history_results

    citations = citations_from_fail_history_results(
        [
            {"doc_id": "FH-1"},
            {"doc_id": "FH-1", "download_url": "https://internal/FH-1.pptx"},
            {"doc_id": "FH-2", "source_file": "FH-2.pptx"},
            {"content": "no structured document id"},
        ],
        source_paths={"FH-1": "sources/FH-1.md"},
    )

    assert [citation.model_dump() for citation in citations] == [
        {
            "doc_id": "FH-1",
            "label": "FH-1",
            "source_path": "sources/FH-1.md",
            "download_url": "https://internal/FH-1.pptx",
        },
        {
            "doc_id": "FH-2",
            "label": "FH-2",
            "source_path": None,
            "download_url": "",
        },
    ]


def test_graph_only_result_can_emit_canonical_structured_source_citation(tmp_path):
    from agent_sessions import citations_from_fail_history_results
    from wiki_config import initialize_wiki_vault, resolve_wiki_paths
    from wiki_plugin_notes import read_source

    wiki_paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(wiki_paths)
    (wiki_paths.sources / "FH-GRAPH.md").write_text(
        "---\ntype: source\ndoc_id: FH-GRAPH\n---\n# Source\n",
        encoding="utf-8",
    )
    result_rows = [
        {"doc_id": "FH-GRAPH", "download_url": "https://internal/FH-GRAPH"},
        {"content": "Answer prose mentioning [FH-TEXT-ONLY] is not a citation"},
    ]

    source = read_source(wiki_paths, "FH-GRAPH")
    citations = citations_from_fail_history_results(
        result_rows,
        source_paths={source.doc_id: source.source_path},
    )

    assert [citation.model_dump() for citation in citations] == [
        {
            "doc_id": "FH-GRAPH",
            "label": "FH-GRAPH",
            "source_path": "sources/FH-GRAPH.md",
            "download_url": "https://internal/FH-GRAPH",
        }
    ]


def test_additive_chat_and_event_models_keep_existing_clients_valid():
    request = models.ChatRequest(query="hello", session_id="session-1")
    message = models.MessageEvent(agent="planner", content="hello")
    history = models.HistoryMessage(role="assistant", content="hello")

    assert "wiki_context" not in models.ChatRequest.model_json_schema()["properties"]
    assert message.citations == []
    assert history.citations == []


@pytest.mark.anyio
async def test_shared_session_history_preserves_structured_citations():
    from agent_sessions import load_session_history

    timestamp = datetime(2026, 8, 1, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self, documents):
            self._documents = iter(documents)

        def sort(self, *args):
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._documents)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class Turns:
        def find(self, *args):
            return Cursor(
                [
                    {
                        "session_id": "session-1",
                        "query": "원인은?",
                        "timestamp": timestamp,
                        "messages": [
                            {
                                "agent": "fail_history_agent",
                                "content": "근거입니다.",
                                "artifacts": [
                                    {
                                        "artifact_type": "html",
                                        "data": "<p>evidence</p>",
                                    }
                                ],
                                "suggestion": "다음 분석",
                                "citations": [
                                    {
                                        "doc_id": "FH-1",
                                        "label": "FH-1",
                                        "source_path": "sources/FH-1.md",
                                        "download_url": "https://internal/FH-1.pptx",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            )

    history = await load_session_history(
        SimpleNamespace(chat_turns=Turns()), "session-1"
    )

    assert [turn.role for turn in history.turns] == ["user", "assistant"]
    assert history.turns[1].citations[0].doc_id == "FH-1"
    assert history.turns[1].citations[0].source_path == "sources/FH-1.md"
    assert history.turns[1].artifacts[0].data == "<p>evidence</p>"
    assert history.turns[1].suggestion == "다음 분석"
