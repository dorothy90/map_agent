from datetime import datetime, timezone
from types import SimpleNamespace

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
    assert captured["body"].wiki_context == {
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
        for content in system_messages
    )


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


def test_additive_chat_and_event_models_keep_existing_clients_valid():
    request = models.ChatRequest(query="hello", session_id="session-1")
    message = models.MessageEvent(agent="planner", content="hello")
    history = models.HistoryMessage(role="assistant", content="hello")

    assert request.wiki_context is None
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
