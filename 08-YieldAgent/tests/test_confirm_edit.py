"""P2 task_confirm 3분류 해석기(_interpret_confirm_response) 단위 테스트 — 서버 불필요.

    pytest tests/test_confirm_edit.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import node_supervisor
from node_supervisor import ConfirmDecision, _interpret_confirm_response

pytestmark = pytest.mark.no_server

TASK = {
    "agent": "wads_agent",
    "goal": "4SS WADS 열화 검출 리포트 조회",
    "params": {"lotcd": "4SS", "wads_start_tm": "2026-07-04", "wads_end_tm": "2026-07-04"},
}


class _FakeModel:
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        if isinstance(self._content, Exception):
            raise self._content
        return SimpleNamespace(content=self._content)


def _patch_llm(monkeypatch, content):
    fake = _FakeModel(content)
    monkeypatch.setattr(node_supervisor, "_model", fake)
    return fake.calls


def test_approve(monkeypatch):
    _patch_llm(monkeypatch, '{"reasoning":"긍정","decision":"approve","slot_updates":{}}')
    d = _interpret_confirm_response("네 진행해주세요", TASK)
    assert d.decision == "approve" and d.slot_updates == {}


def test_approve_with_changes_extracts_slots(monkeypatch):
    calls = _patch_llm(
        monkeypatch,
        '{"reasoning":"공정 변경","decision":"approve_with_changes",'
        '"slot_updates":{"wads_category":"PT1C"}}',
    )
    d = _interpret_confirm_response("응 근데 PT1C 검출만 봐줘", TASK)
    assert d.decision == "approve_with_changes"
    assert d.slot_updates == {"wads_category": "PT1C"}
    # 프롬프트에 agent의 슬롯 사전과 현재 params가 실려야 한다
    system = calls[0][0]["content"]
    assert "wads_category" in system and "4SS" in system


def test_reject(monkeypatch):
    _patch_llm(monkeypatch, '{"reasoning":"거절","decision":"reject","slot_updates":{}}')
    d = _interpret_confirm_response("아니 됐어", TASK)
    assert d.decision == "reject"


def test_empty_answer_rejects_without_llm(monkeypatch):
    calls = _patch_llm(monkeypatch, '{"decision":"approve"}')
    assert _interpret_confirm_response("", TASK).decision == "reject"
    assert _interpret_confirm_response(None, TASK).decision == "reject"
    assert calls == []  # 빈 응답은 LLM 호출 없이 즉시 reject


def test_llm_failure_rejects(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("LLM down"))
    assert _interpret_confirm_response("응 좋아", TASK).decision == "reject"


def test_garbage_json_rejects(monkeypatch):
    _patch_llm(monkeypatch, "이건 JSON이 아님")
    assert _interpret_confirm_response("응 좋아", TASK).decision == "reject"


def test_invalid_decision_value_rejects(monkeypatch):
    _patch_llm(monkeypatch, '{"reasoning":"?","decision":"maybe","slot_updates":{}}')
    assert _interpret_confirm_response("응?", TASK).decision == "reject"


def test_dict_resume_uses_task_confirm_key(monkeypatch):
    _patch_llm(monkeypatch, '{"reasoning":"긍정","decision":"approve","slot_updates":{}}')
    d = _interpret_confirm_response({"task_confirm": "예"}, TASK)
    assert d.decision == "approve"


def test_confirm_decision_defaults():
    assert ConfirmDecision().decision == "reject"
