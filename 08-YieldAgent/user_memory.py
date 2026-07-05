"""사용자 선호 장기 메모리 — user_id별 자유 텍스트 프로필 (Mongo user_profiles).

HITL 접점(task_confirm 거절 / postwads_choice 선택·미선택 / plan_review cancel·modify)의
피드백 이벤트를 턴 종료 시 LLM 1회 호출로 프로필에 반영하고, 다음 턴 planner가
Structured context로 참고한다.

원칙(god-state 교훈): 프로필에는 정성적 선호만 저장한다. 슬롯 값(제품코드/lot/날짜/
파라미터 구체값)은 절대 영속하지 않는다 — 값 도출은 매 턴 planner의 몫.
모든 public 함수는 예외를 삼킨다 — 메모리 실패가 본 파이프라인을 죽이면 안 된다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from pydantic import BaseModel

logger = logging.getLogger("yield_agent.user_memory")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "yield_agent"
_COLLECTION_NAME = "user_profiles"
_PROFILE_MAX_CHARS = 4000  # 코드 레벨 failsafe (프롬프트 규율은 1200자)

_client = None
_client_lock = threading.Lock()


class MemoryUpdateResult(BaseModel):
    chain_of_thought: str = ""
    updated_profile: str


def _collection():
    """lazy 모듈 싱글턴 MongoClient — planner 워커 스레드/백그라운드 flush 스레드 공용."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from pymongo import MongoClient

                _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    return _client[MONGO_DB][_COLLECTION_NAME]


def get_profile(user_id: str) -> str:
    """user_id의 선호 프로필 텍스트. 부재/예외 → "" (조용히)."""
    if not user_id:
        return ""
    try:
        doc = _collection().find_one({"_id": user_id})
        return str((doc or {}).get("profile") or "")
    except Exception as e:
        logger.warning("[UserMemory] get_profile 실패 (무시): %s", e)
        return ""


def save_profile(user_id: str, profile: str) -> None:
    if not user_id:
        return
    try:
        _collection().update_one(
            {"_id": user_id},
            {
                "$set": {
                    "profile": str(profile or "")[:_PROFILE_MAX_CHARS],
                    "updated_at": datetime.now(timezone.utc),
                },
                "$inc": {"updates": 1},
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning("[UserMemory] save_profile 실패 (무시): %s", e)


def make_feedback_event(
    *,
    touchpoint: str,
    decision: str,
    message: str,
    user_answer: str,
    agent: str = "",
    goal: str = "",
    options: list | None = None,
) -> dict:
    """HITL 접점 피드백 이벤트 빌더 (순수 dict, I/O 없음).

    decision은 확정된 코드 분기의 이름 그대로 — 의미 해석은 프로필 갱신 LLM이 한다.
    """
    ev = {
        "touchpoint": touchpoint,
        "decision": decision,
        "message": str(message or ""),
        "user_answer": str(user_answer or ""),
    }
    if agent:
        ev["agent"] = agent
    if goal:
        ev["goal"] = goal
    if options:
        ev["options"] = options
    return ev


MEMORY_UPDATE_SYSTEM = """너는 반도체 수율 분석 어시스턴트의 '사용자 선호 프로필' 관리자다.
프로필은 이 사용자가 어시스턴트의 제안·계획·선택지에 어떻게 반응해왔는지에 대한 정성적
선호 메모이며, 분석 계획 수립 시 참고 자료로 쓰인다.
<현재_프로필>과 이번 턴의 <피드백_이벤트>(어시스턴트가 제시한 제안/계획/선택지와 사용자의
실제 응답 원문)를 보고 프로필을 갱신해라.

절대 규칙:
1. 전체 덮어쓰기 금지. 기존 프로필의 다른 항목은 글자 그대로 보존하고, 이번 피드백이 직접
   뒷받침하는 부분만 표적 추가하거나 표적 수정한다.
2. 정성적 선호만 기록한다. 구체 값(제품코드, lot 번호, 날짜, 특정 파라미터명 등)은 절대
   기록하지 않는다.
   - 나쁜 예: "4SS 제품을 주로 본다", "IGATE(P) 파라미터를 선택했다"
   - 좋은 예: "후속 제안 중 웨이퍼 맵 시각화를 고르는 경향", "여러 작업 일괄 실행보다
     계획을 줄여 실행하는 것을 선호"
3. 1회성 상황과 반복 성향을 구별해라. 단일 이벤트는 "~하는 경향(1회 관찰)" 수준으로
   보수적으로 기록하고, 기존 항목과 같은 방향이면 그 항목을 강화하는 표현으로 수정하며,
   상충하면 최신 피드백 쪽으로 완화·수정한다.
4. 프로필은 한국어 불릿 목록("- "로 시작), 최대 12항목·전체 1200자 이내. 초과 시 오래되고
   약한 항목부터 병합하거나 제거해라.
5. 이번 이벤트에서 배울 것이 없으면(단순 클릭 거절 1회 등 신호가 약하면) 프로필을 그대로
   반환해라.

출력은 아래 JSON 하나만:
{"chain_of_thought": "<무엇을 왜 추가/수정/유지했는지 단계적 근거>",
 "updated_profile": "<갱신된 프로필 전문>"}

예시:
<현재_프로필>
- 분석 결과는 간결한 요약을 선호
</현재_프로필>
<피드백_이벤트>
[{"touchpoint": "task_confirm", "decision": "rejected",
  "message": "검출된 열화 파라미터로 mining 분석을 이어서 실행할까요?",
  "user_answer": "아니요, mining은 됐어요. 매번 물어보지 않아도 됩니다",
  "agent": "mining_agent"}]
</피드백_이벤트>
출력:
{"chain_of_thought": "사용자가 mining 후속 제안을 거절하면서 '매번 물어보지 않아도 된다'고 반복 성향을 직접 표명했다. 기존 항목과 무관한 새 선호이므로 불릿 하나만 추가하고 기존 항목은 그대로 보존한다. 구체 값은 포함하지 않았다.",
 "updated_profile": "- 분석 결과는 간결한 요약을 선호\\n- 자동 mining 후속 분석 제안은 원하지 않는 편 (직접 요청 시에만 실행 선호)"}"""


def update_profile_from_feedback(user_id: str, events: list[dict]) -> None:
    """이번 턴의 피드백 이벤트로 프로필을 LLM 1회 호출로 갱신 (턴 종료 백그라운드 전용).

    실패는 전부 로그 후 무시 — 절대 raise하지 않는다.
    """
    if not user_id or not events:
        return
    try:
        from common import extract_json_from_llm, get_llm

        current = get_profile(user_id)
        user_msg = (
            f"<현재_프로필>\n{current or '(비어 있음)'}\n</현재_프로필>\n"
            f"<피드백_이벤트>\n{json.dumps(events, ensure_ascii=False, default=str)}\n</피드백_이벤트>"
        )
        raw = (
            get_llm()
            .invoke(
                [
                    {"role": "system", "content": MEMORY_UPDATE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ]
            )
            .content
            or ""
        )
        result = extract_json_from_llm(raw, MemoryUpdateResult)
        updated = (result.updated_profile or "").strip()
        if updated and updated != current:
            save_profile(user_id, updated)
            logger.info(
                "[UserMemory] 프로필 갱신 user=%s (%d→%d자) 근거=%s",
                user_id,
                len(current),
                len(updated),
                (result.chain_of_thought or "")[:200],
            )
        else:
            logger.info("[UserMemory] 프로필 변경 없음 user=%s (events=%d)", user_id, len(events))
    except Exception as e:
        logger.warning("[UserMemory] update_profile_from_feedback 실패 (무시): %s", e)
