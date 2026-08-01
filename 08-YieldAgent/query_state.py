"""query_state — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import operator
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import add_messages
from pydantic import BaseModel, Field


load_dotenv(override=True)





# ── Pydantic 라우팅 결정 모델 ────────────────────────────────
class CanonicalRequestItem(BaseModel):
    """LLM canonicalizer output before deterministic task building."""

    intent: str = Field(
        default="", description="정규화된 intent (예: wads_report, map)"
    )
    agent: Literal[
        "",
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "ppt_export",
        "lot_history_agent",
        "relation_tree_agent",
        "mining_agent",
    ] = Field(default="", description="실행 대상 agent")
    slots: dict = Field(
        default_factory=dict,
        description="agent slot schema에 맞춘 structured parameters",
    )
    goal: str = Field(default="", description="사용자에게 표시할 한국어 목표")
    answer: str = Field(default="", description="잘못 중첩된 direct answer 보정용")
    ambiguous_slots: list[dict] = Field(
        default_factory=list,
        description=(
            "진짜 모호해서(여러 해석이 모두 그럴듯해 하나로 확신 불가) 사용자 확인이 필요한 슬롯만. "
            "각 항목: {\"slot\":슬롯명, \"candidates\":[후보값,...], \"reason\":한국어 질문}. "
            "해당 슬롯은 slots에서 비워두고 여기 적는다. 기본값이 있는 미지정은 모호가 아니다 → 빈 리스트."
        ),
    )


class CanonicalPlanResponse(BaseModel):
    """LLM canonicalizer output schema."""

    requests: list[CanonicalRequestItem] = Field(
        default_factory=list, description="정규화된 요청 목록"
    )
    answer: str = Field(
        default="",
        description="도구 실행 없이 제공 context만으로 답할 수 있을 때의 사용자 응답",
    )


# ── yield 조회 기간: 라벨 기반 time_range → ref_date/periods/unit 변환 ──────────
# LLM이 YYYYMMDD/periods 산술을 직접 하면 "16-17주차" 같은 특정 범위가 "최근 N주"로
# 떨어지는 silent-wrong이 생긴다. planner는 자연 라벨(time_range)만 뱉고, supervisor가
# dispatch 직전 코드로 ref_date/periods/unit으로 변환한다(yield_agent_node는 무수정).
class TimeRange(BaseModel):
    """yield_agent 조회 기간을 라벨 기반으로 표현.

    라벨 포맷:
      weekly:  "YYYY-Www"   (예: "2026-W17") — ISO 주차
      monthly: "YYYY-MM"    (예: "2026-02")
      daily:   "YYYY-MM-DD" (예: "2026-05-06")
    단일 시점이면 start == end."""

    unit: Literal["weekly", "monthly", "daily"] = Field(description="시간 단위")
    start: str = Field(description="시작 라벨 (포함)")
    end: str = Field(description="끝 라벨 (포함)")


class PlanReviewResult(BaseModel):
    """plan_review LLM의 출력 스키마"""

    action: Literal["approve", "cancel", "modify"]
    requests: list[CanonicalRequestItem] = Field(
        default_factory=list,
        description="최종 canonical request 목록 (approve 시 현재 요청 그대로, modify 시 수정된 전체 목록)",
    )


# ── 공유 State 정의 ──────────────────────────────────────
class YieldQueryState(TypedDict):
    """Yield Query Supervisor의 공유 State

    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    멀티스텝 루프에서 artifacts는 operator.add reducer로 누적됩니다.

    State ownership:
    - Source of truth: messages, especially AIMessage.additional_kwargs["result"]
      when agents attach a full ResultEnvelope.
    - UI delivery: *_artifacts fields may contain artifact payloads for the
      current turn and are not resolver memory.
    - Scratchpad/index: recent_results is a bounded, payload-free projection
      rebuilt from message ResultEnvelopes. It is never canonical storage.
    """

    messages: Annotated[list, add_messages]
    step_count: int  # supervisor 루프 카운터
    trace_id: str  # local observability trace id (overwrite)
    turn_id: str  # local observability turn id (overwrite)
    wiki_context: dict  # current Obsidian note envelope (overwrite per fresh turn)

    # 조회 파라미터
    lotcd: str
    ref_date: str
    unit: str  # "weekly" | "monthly" | "daily"
    periods: int  # 조회 기간 수 (0 = 기본값)

    # 결과 데이터
    weeks_data: list
    table_result: str
    analysis_result: str

    # Yield 관련 — reducer로 누적 (멀티스텝에서 여러 에이전트 결과 보존)
    yield_artifacts: Annotated[list, operator.add]

    # WADS 관련
    wads_start_tm: str
    wads_end_tm: str
    wads_category: str  # 공정 필터 ("PT1H"/"PT1C") → CATEGORY(PT1H_TEST/PT1C_TEST)
    wads_artifacts: Annotated[list, operator.add]

    # 이상감지
    anomaly_params: list

    # 파라미터 필터
    filter_params: list  # deprecated: yield_agent always returns the full artifact

    # 통합 파라미터 (agent별 분산 → 공통)
    lot_ids: list[str]  # 7자 lot 번호 목록
    wf_ids: list[str]  # wafer ID 목록
    groupkey: str  # 그룹 집계 키
    fail_type: str  # 파라미터/불량유형 코드
    selected_fail_type: str  # postwads에서 사용자가 마지막으로 고른 단일 파라미터 (planner 문맥 상속용)
    cause_oper: str  # 원인 공정/step명

    # Map Agent 파라미터 (map-specific)
    map_type: str
    map_oper: str
    wf_mod: int  # wafer-number pattern divisor (짝수=2, 3배수=3 …); 0/absent = no filter
    wf_rem: int  # remainder for the pattern (짝수=0, 홀수=1, N배수=0)
    map_label: str  # display label for the cummap (e.g. #RN report parameter "JUNCTION")
    map_groups: list  # WADS report별 cummap fan-out [{parameter, map_oper, groupkeys}, …] (overwrite)

    # Fail History 파라미터
    dh_query: str
    fail_groups: list  # WADS report별 불량이력 fan-out [{lotcd, parameter, lot_ids}, …] (overwrite)

    # Fail History 결과
    fail_history_artifacts: Annotated[list, operator.add]
    fail_history_results: list[
        dict
    ]  # 다음-턴 번호 선택 라우팅용 raw results (overwrite, per-turn reset in agent_server)

    # Day 4: wiki memory 메타 (둘 다 turn별 overwrite, reducer 없음 — plan v3 §State/Checkpoint 가드)
    wiki_hit_ids: list[str]  # 이번 turn에 wiki_memory가 참조한 노드 id (디버그용)
    wiki_update_status: (
        str  # "queued" | "summarized" | "persisted" | "dropped" | "skipped"
    )

    # Lot History 결과
    lot_history_artifacts: Annotated[list, operator.add]

    # Relation Tree (Inline-WT 연관 분석)
    rt_groups: list  # WADS report별 연관분석 fan-out [{lotcd, parameter, lot_ids}, …] (overwrite)
    # Relation Tree 결과
    relation_tree_artifacts: Annotated[list, operator.add]

    # Mining (gini 기반 기여 파라미터 마이닝) — 상류 공유키 재사용:
    # lot_cd=lotcd, fail_name=fail_type, mode=wads_category (별도 키 없음).
    group_good: list  # 양품 그룹 식별자 (사용자 직접/상류 상속, overwrite)
    group_bad: list  # 불량 그룹 식별자 (사용자 직접/상류 상속, overwrite)
    tech: str  # 기술/공정 세대 코드
    user_id: str  # 요청 사용자 ID
    rank_limit: int  # 상위 N개 제한 (0/absent = 기본 10)

    # Map 결과
    map_result: str
    map_artifacts: Annotated[list, operator.add]

    # Mining 결과
    mining_artifacts: Annotated[list, operator.add]  # df_GINI 동적 HTML artifact (turn별 리셋)
    mining_rows: list  # 머금은 gini rows (plain, 턴 간 유지 → 재호출 없이 LLM Q&A)
    mining_sig: str  # 머금은 분석의 입력 서명 (동일 입력이면 API memo로 재사용)

    # PPT Export 결과
    ppt_artifacts: Annotated[list, operator.add]

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str

    # 사용자 선호 학습: HITL 접점 피드백 이벤트 (턴 종료 시 1회 flush, fresh 턴마다 Overwrite 리셋)
    memory_feedback: Annotated[list, operator.add]

    # Resolver scratchpad index (overwrite)
    # Full ResultEnvelope source remains in message.additional_kwargs["result"].
    # recent_results stores at most 3 pruned entries with at most 50 rows each.
    # Consumers must use result_id to retrieve the canonical message payload.
    recent_results: list[dict]

    # ReferenceResolver v1 scratchpad (overwrite, deterministic only)
    resolved_refs: dict
    reference_issues: list[dict]

    # 모호 슬롯 (시나리오 2, overwrite): planner가 채우고 supervisor missing_param HITL이 소비.
    # 각 항목 {task_id, agent, slot, candidates:[...], reason}.
    ambiguous_slots: list[dict]

    # 실행 확인 대기 task (B안, overwrite): replanner가 {task_id: 확인메시지}를 채우고
    # supervisor가 dispatch 직전 interrupt로 확인 → 처리된 id는 제거. 신규 노드 없이 체이닝 확인.
    confirm_tasks: dict

    # Step 5: WADS 검출 후속 선택을 이번 turn에 이미 제안했는지 (overwrite, turn별 리셋).
    # replanner가 sentinel 제안 시 True로 세팅 → 같은 turn 재제안/무한 추가 방지.
    postwads_offered: bool
    mainoper_offered: bool  # relation_tree main_oper 선택 제안 가드 (turn별 리셋)

    # Canonical request scratchpad (overwrite). Planner/replanner produce this
    # contract; task_builder converts it into executable tasks.
    canonical_request: dict
    canonical_requests: list[dict]
    canonical_trace: list[dict]

    # Task normalizer/validator scratchpad (overwrite, Phase 5)
    task_normalization_trace: list[dict]
    task_validation_issues: list[dict]

    # HITL Gate scratchpad (overwrite, Phase 6)
    hitl_issues: list[dict]
    hitl_responses: list[dict]

    # Planner 관련
    task_plan: list[dict]  # task_builder가 생성한 전체 계획 (overwrite)
    pending_tasks: list[dict]  # 아직 실행 안 된 task dict들 (overwrite)
    current_task: (
        dict  # 현재 executor가 받는 공통 task contract (task_id, agent, params, goal)
    )
    current_task_id: str  # 현재 실행 중인 task의 ID
    current_task_goal: str  # 현재 실행 중인 task의 한국어 goal — worker가 query 우선순위로 사용 (#12 fix)

    # 워커 task별 결과 누적 (#8 phase 1, replanner 사전작업)
    # 각 worker가 정상/에러 종료 시 [(task_id, summary)]를 append.
    # 향후 replanner_node가 plan 갱신·chained input 해소에 사용.
    past_steps: Annotated[list, operator.add]

    # canonical plan-and-execute 종료 신호 (LangChain OpenTutorial Act = Union[Response, Plan] 대응).
    # replanner_node가 plan 완료 감지 시 최종 응답 문자열을 set → should_end conditional edge가 END 분기.
    response: str
