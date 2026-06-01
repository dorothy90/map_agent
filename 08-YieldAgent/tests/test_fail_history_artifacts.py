from __future__ import annotations

import importlib
from pathlib import Path
import sys

from langchain_core.messages import HumanMessage


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_fail_history_agent_wraps_message_as_readable_html_artifact(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://localhost")

    fail_history_agent = importlib.import_module("fail_history_agent")

    def fake_do_search(**kwargs):
        return {"total": 0, "results": [], "retrieval_mode": "baseline"}

    monkeypatch.setattr(fail_history_agent, "do_search", fake_do_search)

    result = fail_history_agent.fail_history_agent_node(
        {
            "messages": [HumanMessage(content="TWT 불량이력 찾아줘")],
            "current_task_id": "task_1",
            "current_task_goal": "TWT 불량이력 검색",
        },
        {},
    )

    message = result["messages"][0]
    artifacts = result["fail_history_artifacts"]

    assert len(artifacts) == 1
    assert artifacts[0]["type"] == "html"
    assert artifacts[0]["mime"] == "text/html"
    assert artifacts[0]["agent"] == "fail_history_agent"
    assert artifacts[0]["data"] != message.content
    assert "<article class=\"paper\">" in artifacts[0]["data"]
    assert "Fail History Report" in artifacts[0]["data"]
    assert "불량이력 분석 결과" in artifacts[0]["data"]
    assert "조건에 맞는 불량이력이 없습니다" in artifacts[0]["data"]

    envelope = message.additional_kwargs["result"]
    assert envelope["metadata"]["artifact_count"] == 1
    assert envelope["artifact_refs"][0]["artifact_type"] == "html"


def test_agent_server_preserves_declared_markdown_artifact_type(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://localhost")

    agent_server = importlib.import_module("agent_server")

    assert (
        agent_server._artifact_type_from_artifact(
            {"type": "markdown", "mime": "text/markdown"},
            "### 답변",
        )
        == agent_server.ArtifactType.markdown
    )
    assert (
        agent_server._artifact_type_from_artifact(
            {"mime": "text/markdown"},
            "일반 텍스트",
        )
        == agent_server.ArtifactType.markdown
    )
