"""user_memory.py 단위 테스트 — 서버 불필요, 로컬 Mongo만 (부재 시 skip).

    pytest tests/test_user_memory.py -v
"""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import user_memory
from user_memory import (
    _PROFILE_MAX_CHARS,
    get_profile,
    make_feedback_event,
    save_profile,
    update_profile_from_feedback,
)

pytestmark = pytest.mark.no_server


def _mongo_up() -> bool:
    try:
        user_memory._collection().database.client.admin.command("ping")
        return True
    except Exception:
        return False


requires_mongo = pytest.mark.skipif(not _mongo_up(), reason="local MongoDB not reachable")


@pytest.fixture
def user_id():
    uid = f"ut_mem_{uuid.uuid4().hex[:8]}"
    yield uid
    try:
        user_memory._collection().delete_one({"_id": uid})
    except Exception:
        pass


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=self._content)


@requires_mongo
def test_save_get_roundtrip(user_id):
    assert get_profile(user_id) == ""
    save_profile(user_id, "- 간결한 요약 선호")
    assert get_profile(user_id) == "- 간결한 요약 선호"


@requires_mongo
def test_save_truncates_failsafe(user_id):
    save_profile(user_id, "가" * (_PROFILE_MAX_CHARS + 1000))
    assert len(get_profile(user_id)) == _PROFILE_MAX_CHARS


def test_get_profile_empty_user_id():
    assert get_profile("") == ""


def test_make_feedback_event_shape():
    ev = make_feedback_event(
        touchpoint="task_confirm",
        decision="rejected",
        message="mining 후속을 실행할까요?",
        user_answer="아니요 됐어요",
        agent="mining_agent",
    )
    assert ev == {
        "touchpoint": "task_confirm",
        "decision": "rejected",
        "message": "mining 후속을 실행할까요?",
        "user_answer": "아니요 됐어요",
        "agent": "mining_agent",
    }


@requires_mongo
def test_update_profile_targeted_addition(user_id, monkeypatch):
    """LLM canned JSON으로 표적-갱신: 기존 불릿 보존 + 신규 불릿 추가."""
    import common

    save_profile(user_id, "- 간결한 요약 선호")
    canned = (
        '{"chain_of_thought": "거절 피드백 반영",'
        ' "updated_profile": "- 간결한 요약 선호\\n- 자동 mining 후속 제안은 원하지 않는 편"}'
    )
    fake = _FakeLLM(canned)
    monkeypatch.setattr(common, "get_llm", lambda *a, **k: fake)

    events = [
        make_feedback_event(
            touchpoint="task_confirm",
            decision="rejected",
            message="mining 후속을 실행할까요?",
            user_answer="아니요, 매번 물어보지 않아도 됩니다",
            agent="mining_agent",
        )
    ]
    update_profile_from_feedback(user_id, events)

    profile = get_profile(user_id)
    assert "- 간결한 요약 선호" in profile          # 기존 항목 보존
    assert "mining 후속 제안은 원하지 않는" in profile  # 표적 추가
    assert len(fake.calls) == 1


@requires_mongo
def test_update_profile_no_change_skips_save(user_id, monkeypatch):
    import common

    save_profile(user_id, "- 간결한 요약 선호")
    before = user_memory._collection().find_one({"_id": user_id})["updates"]
    canned = '{"chain_of_thought": "신호 약함", "updated_profile": "- 간결한 요약 선호"}'
    monkeypatch.setattr(common, "get_llm", lambda *a, **k: _FakeLLM(canned))

    update_profile_from_feedback(user_id, [make_feedback_event(
        touchpoint="task_confirm", decision="rejected", message="m", user_answer="아니오")])

    after = user_memory._collection().find_one({"_id": user_id})["updates"]
    assert after == before  # 변경 없음 → save 생략


def test_update_profile_swallows_llm_failure(monkeypatch):
    """LLM 예외/파싱 실패가 절대 전파되지 않는다."""
    import common

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(common, "get_llm", boom)
    update_profile_from_feedback("some_user", [make_feedback_event(
        touchpoint="task_confirm", decision="rejected", message="m", user_answer="아니오")])


def test_update_profile_swallows_mongo_failure(monkeypatch):
    def boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(user_memory, "_collection", boom)
    assert get_profile("u") == ""
    save_profile("u", "p")  # no raise
    update_profile_from_feedback("u", [{"touchpoint": "t", "decision": "d",
                                        "message": "m", "user_answer": "a"}])


def test_update_profile_noop_without_events_or_user(monkeypatch):
    import common

    def must_not_call(*a, **k):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(common, "get_llm", must_not_call)
    update_profile_from_feedback("", [{"x": 1}])
    update_profile_from_feedback("user", [])
