"""
Supervisor Node — Yield/WADS 라우팅 담당
=========================================
Pydantic with_structured_output() 방식으로 JSON 파싱 오류 없이 안정적으로 라우팅합니다.

라우팅 대상:
  yield_agent  → pt1h 수율 조회
  wads_agent   → WADS 열화 검출 리포트 조회
  END          → 범위 외 요청
"""

from __future__ import annotations

import json
import operator
import os
import re
import logging
from datetime import date
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse import get_client, observe
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command, RetryPolicy
from pydantic import BaseModel, Field

from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.supervisor")

# ── LLM 모델 ────────────────────────────────────────────────
_model = ChatOpenAI(
    model="gpt-oss-120b",
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
    request_timeout=180,
)


# ── Pydantic 라우팅 결정 모델 ────────────────────────────────
class RouteResponse(BaseModel):
    """Supervisor의 라우팅 결정 — with_structured_output으로 타입 보장"""

    next: Literal["yield_agent", "wads_agent", "map_agent", "FINISH"] = Field(
        description="다음에 실행할 에이전트"
    )
    lotcd: str = Field(default="4SS", description="3~4자리 제품코드만 (예: 4SS, 5NA, 6E2). 전체 lot ID(예: 4SS2DPD)는 절대 입력하지 말 것")
    ref_date: str = Field(
        default="", description="Yield 기준날짜 YYYYMMDD (yield_agent 전용)"
    )
    wads_end_tm: str = Field(
        default="", description="WADS 조회 날짜 YYYY-MM-DD (wads_agent 전용)"
    )
    filter_params: list[str] = Field(
        default=[],
        description="표시할 파라미터 목록 (비어있으면 전체 표시). 예: ['VTH', 'IDSAT']"
    )
    unit: str = Field(default="weekly", description='"weekly" | "monthly" | "daily"')
    periods: int = Field(default=0, description="조회 기간 수 (0 = 기본값: weekly=4, monthly=3, daily=4)")
    message: str = Field(description="사용자에게 전달할 한국어 메시지")
    # Map 파라미터
    map_lot_id:   str = Field(default="", description="단일 lot ID (예: 'LOTABC123')")
    map_lot_ids:  str = Field(default="", description="복수 lot IDs, 쉼표 구분")
    map_wf_ids:   str = Field(default="", description="wafer IDs, 쉼표 구분")
    map_groupkey: str = Field(default="", description="lot_id.wf_id 형식 (예: 'LOT001.01,LOT001.02')")
    map_type:     str = Field(default="binmap", description="binmap | cummap | all")
    map_bin_type: str = Field(default="pt1h_bin", description="pt1h_bin | pt2c_bin")
    # Yield lot 비교 파라미터
    yield_lot_ids:  str = Field(default="", description="수율 조회용 lot ID 목록, 쉼표 구분 (예: '4SS2DPD,4SSXCEW')")
    yield_groupkey: str = Field(default="", description="수율 조회용 lot.wf 형식 (예: '4SS2DPD.01,4SS2DPD.05')")


# ── Rewrite 시스템 프롬프트 ─────────────────────────────────
REWRITE_SYSTEM_PROMPT = """\
You are a query rewriter for a semiconductor yield analysis system.
Rewrite the user's message to be explicit and unambiguous using the provided context.

Rules:
- If the message contains an explicit time reference (저번주, 이번주, 오늘, 금일, N주전, 지난주, 어제, specific date)
  AND a request word (알려줘, 보여줘, 조회, 알아봐) → treat as a YIELD query.
  Expand using the current product code (lotcd) from context if available.
  Example: context has "현재 제품: 4SS" + user "저번주 알려줘" → "저번주 4SS 수율 알려줘"
  Do NOT apply AI suggestion context when an explicit time reference is present.
- If the message already has clear intent ("4SS 수율 알려줘"), return it UNCHANGED
- If "AI 직전 응답" context exists and user sends a pure short affirmative with NO time/topic hint
  ("응", "네", "좋아", "부탁해"):
  → Expand into a concrete command based on what the AI previously suggested
  → Example: AI said "WADS 열화 검출 리포트를 확인하시겠습니까?" + user "응" → "WADS 열화 검출 리포트 보여줘"
  → Example: AI said "다른 제품 코드로 조회할까요?" + user "응" → "다른 제품 코드로 조회해줘"
- If NO "AI 직전 응답" context, use only prior state context (lotcd, anomaly_params) to expand
- If specific parameter names are mentioned, preserve them exactly
- Respond with ONLY the rewritten query string. No explanation.
"""


@observe(name="rewrite_node")
def rewrite_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {}

    # 마지막 HumanMessage 추출
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
    )
    if not last_human:
        return {}

    # 구조화된 state 필드로 컨텍스트 구성 (messages 누적 없음 → O(1) 토큰)
    ctx_parts = []
    if state.get("lotcd"):
        ctx_parts.append(f"현재 제품: {state['lotcd']}")
    if state.get("anomaly_params"):
        params = [a["param"] for a in state["anomaly_params"][:5]]
        ctx_parts.append(f"이전 이상감지 파라미터: {', '.join(params)}")
    if state.get("analysis_result"):
        ctx_parts.append(f"이전 분석 요약: {state['analysis_result'][:300]}")

    # 마지막 agent AI 메시지에서 꼬리(제안/질문) 추출 — agent 이름 무관, 6+ 노드 확장 가능
    last_agent_msg = next(
        (m for m in reversed(messages)
         if isinstance(m, AIMessage) and getattr(m, "name", "") not in ("", "supervisor")),
        None,
    )
    if last_agent_msg:
        tail_lines = [l.strip() for l in last_agent_msg.content.strip().split("\n") if l.strip()][-3:]
        ctx_parts.append(f"AI 직전 응답 (마지막 부분): {' / '.join(tail_lines)}")

    context = "\n".join(ctx_parts) if ctx_parts else "이전 컨텍스트 없음"

    response = _model.invoke(
        [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nUser message: {last_human.content}\n\nRewrite:"},
        ],
        config={"callbacks": _lf_callbacks()},
    )

    rewritten = response.content.strip()
    logger.info("[Rewrite] '%s' → '%s'", last_human.content, rewritten)

    # 동일 ID로 교체 → add_messages가 제자리 업데이트, 풀 리스트 반환 불필요
    return {"messages": [HumanMessage(content=rewritten, id=last_human.id)]}


# ── 시스템 프롬프트 ──────────────────────────────────────────
SUPERVISOR_SYSTEM_PROMPT = """\
You are a supervisor managing semiconductor yield data queries and WADS degradation reports.

TODAY's DATE: {today}

AVAILABLE AGENTS:
- yield_agent : Fetches weekly pt1h parametric test data and displays a table
- wads_agent  : Fetches WADS (degradation detection) reports from Oracle DB

=== ROUTING RULES ===

=== PARAMETER FILTER ===
- If user mentions specific parameter names (VTH, IDSAT, FMAX, IOFF, etc.), list them in filter_params
- Example: "VTH만 보여줘" → filter_params: ["VTH"]
- Example: "VTH랑 IDSAT 수율" → filter_params: ["VTH", "IDSAT"]
- No specific params → filter_params: []

Route to **yield_agent** when the user asks about:
- 수율(yield), pt1h 데이터, weekly metrics, 주간 파라미터
- Examples: "오늘 4SS 수율 알려줘", "저번주 수율 보여줘", "2주전 4SS 수율"

Route to **wads_agent** when the user explicitly requests:
- 열화(degradation) detection, 검출 리포트, WADS 리포트
- Examples: "WADS 열화 검출 리포트 보여줘", "열화 리포트 보여줘", "1월 20일 검출 list 보여줘"
- Note: Short affirmatives like "응", "보여줘" are already expanded by the rewrite step into explicit commands. Route based on the rewritten content.

Route to **map_agent** when the user explicitly requests:
- 웨이퍼 맵, wafer map, binmap, cummap, 누적 패스레이트
- lot 지정 + map/맵 시각화 요청
- Examples: "LOT001 binmap 보여줘", "LOT001.01 cummap", "LOT001,LOT002 웨이퍼 맵 비교"

=== MAP PARAMETERS ===
- map_lot_id:   사용자가 단일 lot_id를 지정한 경우 (예: "4SS2DPD", "4SSXCEW")
- map_lot_ids:  사용자가 복수 lot을 쉼표로 나열한 경우 (예: "4SS2DPD,4SSXCEW")
- map_wf_ids:   wf_id를 명시한 경우 (예: "01,02,03")
- map_groupkey: "lot.wf" 형식으로 지정한 경우 (예: "LOT001.01,LOT001.02")
- map_type:     "binmap"(기본) | "cummap" | "all"
- map_bin_type: "pt1h_bin"(기본) | "pt2c_bin"

=== YIELD LOT FILTER ===
- 사용자가 specific lot ID(길이 > 5자)를 언급하고 수율/비교 조회 → yield_lot_ids에 저장, next="yield_agent"
  예: "4SS2DPD 수율 알려줘"      → yield_lot_ids="4SS2DPD", next="yield_agent"
  예: "4SS2DPD,4SSXCEW 비교"   → yield_lot_ids="4SS2DPD,4SSXCEW", next="yield_agent"
- LOT.WF 형식(숫자 서픽스 포함) + 수율/비교 → yield_groupkey에 저장, next="yield_agent"
  예: "4SS2DPD.01,4SS2DPD.05 비교"  → yield_groupkey="4SS2DPD.01,4SS2DPD.05", next="yield_agent"
  예: "4SS2DPD.01,4SS2DPD.05 수율"  → yield_groupkey="4SS2DPD.01,4SS2DPD.05", next="yield_agent"
  ※ 단, "맵"/"map" 키워드가 없는 경우에만 yield_groupkey 사용
- yield_lot_ids/yield_groupkey 있으면 lotcd는 lot ID 앞 3-4자에서 자동 추론
  예: "4SS2DPD" → lotcd="4SS"
- yield_lot_ids/yield_groupkey 없으면 기존 lotcd 기반 period 조회 유지

Route to **FINISH** when the request is unrelated to yield, WADS, or wafer map.

=== UNIT & PERIODS ===
unit 결정:
  "월별", "매월", "월간"  → unit="monthly"
  "일별", "매일", "일간"  → unit="daily"
  명시 없음              → unit="weekly"

periods 오버라이드 (자연어에서 숫자 파싱):
  "최근 3달"  → unit="monthly", periods=3
  "지난 7일"  → unit="daily",   periods=7
  "6주 치"    → unit="weekly",  periods=6
  숫자 없으면 → periods=0 (yield_agent가 기본값 적용: weekly=4, monthly=3, daily=4)

=== TIME REFERENCE ===

For yield_agent → ref_date (YYYYMMDD):
  "오늘" / "금일"        → {today_yyyymmdd}
  "이번주"               → this week's Monday in YYYYMMDD
  "저번주" / "지난주"    → last week's Monday in YYYYMMDD
  "N주전"                → N weeks ago Monday in YYYYMMDD
  specific date "1월 20일" → 20260120
  no time mentioned      → {today_yyyymmdd}

For wads_agent → wads_end_tm (YYYY-MM-DD):
  "보여줘" / no date    → {today_yyyy_mm_dd}
  "1월 20일"            → 2026-01-20
  "저번주"              → last week's Monday in YYYY-MM-DD

=== PRODUCT CODE ===
- lotcd is a SHORT 3-4 character code only: "4SS", "5NA", "6E2"
- A full lot ID (> 5 chars) like "4SS2DPD", "4SSXCEW" is NOT a lotcd
  → for yield/수율 queries:  put it in yield_lot_ids (NOT map_lot_id)
  → for map/맵 queries:     put it in map_lot_id or map_lot_ids
- When yield_lot_ids is set, auto-infer lotcd from the first 3-4 chars (e.g. "4SS2DPD" → lotcd="4SS")
- Default: "4SS"
- For follow-up queries, keep the same lotcd from conversation history

=== MULTI-STEP REASONING ===
You can call agents MULTIPLE TIMES in sequence to achieve the user's goal.
After each agent completes, you see its result summary (marked with [AGENT_RESULT]) in the message history.

DECISION FLOW:
1. Analyze the user's goal and current results so far
2. If more data/analysis is needed → call next agent
3. If goal is fully achieved → FINISH

EXAMPLES:
- "4SS IOFF 수율 이상한데 원인 분석해줘"
  → yield_agent(IOFF) → 이상 감지됨 → wads_agent(열화 확인) → map_agent(cummap) → FINISH

- "4SS 수율 알려줘" → yield_agent → FINISH (단순 조회는 1스텝)

RULES:
- 같은 에이전트를 동일 파라미터로 재호출 금지
- 최대 4스텝 이내 완료
- 단순 조회는 1스텝으로 끝낼 것

=== CRITICAL: OUTPUT FORMAT (STRICT) ===
You MUST ALWAYS respond with ONLY a single raw JSON object.
Do NOT continue or summarize agent results. Do NOT output markdown, analysis, or explanations.
Even when you see agent results in the message history, your ONLY job is to output the next routing JSON.
{{
  "next": "yield_agent" | "wads_agent" | "map_agent" | "FINISH",
  "lotcd": "<product code, default 4SS>",
  "ref_date": "<YYYYMMDD for yield_agent, else empty string>",
  "wads_end_tm": "<YYYY-MM-DD for wads_agent, else empty string>",
  "filter_params": ["VTH", "IDSAT"],
  "unit": "weekly",
  "periods": 0,
  "message": "<Korean message>",
  "map_lot_id":   "",
  "map_lot_ids":  "",
  "map_wf_ids":   "",
  "map_groupkey": "",
  "map_type":     "binmap",
  "map_bin_type": "pt1h_bin",
  "yield_lot_ids":  "",
  "yield_groupkey": ""
}}\
"""


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def supervisor_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[Literal["yield_agent", "wads_agent", "map_agent", "__end__"]]:
    """Supervisor 노드: ReAct 스타일 멀티스텝 루프.

    각 스텝마다 에이전트 결과를 확인하고 다음 행동을 결정합니다.
    Command를 반환하여 state 업데이트 + 라우팅을 하나로 통합합니다.
    """
    step_count = state.get("step_count", 0) + 1

    # 최대 스텝 강제 종료 (무한루프 방지)
    if step_count > 4:
        logger.warning("[Supervisor] 최대 스텝(%d) 초과 → 강제 종료", step_count)
        return Command(
            update={
                "step_count": step_count,
                "messages": [AIMessage(content="분석을 완료했습니다.", name="supervisor")],
            },
            goto=END,
        )

    configurable = config.get("configurable", {}) if config else {}
    messages = state.get("messages", [])
    today = date.today()
    today_yyyymmdd = today.strftime("%Y%m%d")
    today_yyyy_mm_dd = today.strftime("%Y-%m-%d")

    prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today_yyyymmdd,
        today_yyyy_mm_dd=today_yyyy_mm_dd,
    )

    # 이전 이상감지 결과가 있으면 컨텍스트 정보로 주입 (라우팅은 rewrite된 메시지 기반)
    anomaly_params = state.get("anomaly_params", [])
    if anomaly_params:
        param_names = ", ".join(a["param"] for a in anomaly_params)
        prompt += (
            f"\n\n[이전 분석 결과] 이상 감지된 파라미터 ({len(anomaly_params)}개): {param_names}"
        )

    # 에이전트 메시지를 짧은 요약으로 교체 (LLM이 분석 텍스트를 이어쓰지 않도록)
    _AGENT_NAMES = {"yield_agent", "wads_agent", "map_agent"}
    _MAX_AGENT_MSG_LEN = 200

    condensed = []
    for m in messages:
        agent_name = getattr(m, "name", "")
        if isinstance(m, AIMessage) and agent_name in _AGENT_NAMES and len(m.content) > _MAX_AGENT_MSG_LEN:
            summary = m.content[:_MAX_AGENT_MSG_LEN].rsplit("\n", 1)[0]
            condensed.append(AIMessage(
                content=f"[AGENT_RESULT:{agent_name}] {summary}...(결과 생략)",
                name=agent_name,
            ))
        else:
            condensed.append(m)

    # token_counter=len → 메시지 개수 기준 (문자수 아님). 최근 20개 메시지만 전달.
    trimmed_messages = trim_messages(condensed, max_tokens=20, strategy="last", token_counter=len)
    try:
        raw = _model.invoke(
            [{"role": "system", "content": prompt}, *trimmed_messages],
            config={"callbacks": _lf_callbacks()},
        )
        content = raw.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise ValueError(f"JSON 없음: {content[:200]}")
        data = json.loads(match.group())
        decision = RouteResponse(**data)
    except Exception as e:
        logger.error("Supervisor 파싱 실패: %s", e)
        decision = RouteResponse(
            next="FINISH",
            lotcd=state.get("lotcd") or "4SS",
            ref_date=today_yyyymmdd,
            wads_end_tm="",
            message="요청을 이해하지 못했습니다. 다시 시도해 주세요.",
        )

    # 동일 에이전트 재호출 방지: 직전 에이전트와 같으면 FINISH
    if decision.next not in ("FINISH",):
        last_agent = next(
            (getattr(m, "name", "") for m in reversed(messages)
             if isinstance(m, AIMessage) and getattr(m, "name", "") in _AGENT_NAMES),
            None,
        )
        if last_agent == decision.next:
            logger.info("[Supervisor] 동일 에이전트(%s) 재호출 방지 → FINISH", last_agent)
            decision = RouteResponse(
                next="FINISH",
                lotcd=decision.lotcd,
                ref_date=decision.ref_date,
                wads_end_tm=decision.wads_end_tm,
                message=decision.message or "분석을 완료했습니다.",
            )

    # lotcd: 이전 State 값 유지 (follow-up 대화)
    prev_lotcd = state.get("lotcd", "")
    new_lotcd = decision.lotcd or prev_lotcd or "4SS"

    # Langfuse — 라우팅 결정 후 메타데이터 기록
    try:
        get_client().update_current_trace(
            metadata={
                "anomaly_count": configurable.get("anomaly_count", 0),
                "route": decision.next,
                "lotcd": new_lotcd,
                "step": step_count,
            }
        )
    except Exception:
        pass

    # 빈 문자열 기본값 처리
    ref_date = decision.ref_date or today_yyyymmdd
    wads_end_tm = decision.wads_end_tm or (
        today_yyyy_mm_dd if decision.next == "wads_agent" else ""
    )

    result_message = AIMessage(content=decision.message, name="supervisor")

    logger.info(
        "[Supervisor] step=%d next=%-12s lotcd=%-6s ref_date=%s wads_end_tm=%s",
        step_count,
        decision.next,
        new_lotcd,
        ref_date,
        wads_end_tm,
    )

    update_dict = {
        "step_count": step_count,
        "messages": [result_message],
        "lotcd": new_lotcd,
        "ref_date": ref_date,
        "wads_end_tm": wads_end_tm,
        "filter_params": decision.filter_params,
        "unit": decision.unit or "weekly",
        "periods": decision.periods,
        "map_lot_id":   decision.map_lot_id,
        "map_lot_ids":  decision.map_lot_ids,
        "map_wf_ids":   decision.map_wf_ids,
        "map_groupkey": decision.map_groupkey,
        "map_type":     decision.map_type or "binmap",
        "map_bin_type": decision.map_bin_type or "pt1h_bin",
        "yield_lot_ids":  decision.yield_lot_ids,
        "yield_groupkey": decision.yield_groupkey,
    }

    if decision.next == "FINISH":
        return Command(update=update_dict, goto=END)

    return Command(update=update_dict, goto=decision.next)


# ── 공유 State 정의 ──────────────────────────────────────
class YieldQueryState(TypedDict):
    """Yield Query Supervisor의 공유 State

    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    멀티스텝 루프에서 artifacts는 operator.add reducer로 누적됩니다.
    """

    messages: Annotated[list, add_messages]
    step_count: int  # supervisor 루프 카운터

    # 조회 파라미터
    lotcd: str
    ref_date: str
    unit: str      # "weekly" | "monthly" | "daily"
    periods: int   # 조회 기간 수 (0 = 기본값)

    # 결과 데이터
    weeks_data: list
    table_result: str
    analysis_result: str

    # Yield 관련 — reducer로 누적 (멀티스텝에서 여러 에이전트 결과 보존)
    yield_artifacts: Annotated[list, operator.add]

    # WADS 관련
    wads_end_tm: str
    wads_artifacts: Annotated[list, operator.add]

    # 이상감지
    anomaly_params: list

    # 파라미터 필터
    filter_params: list  # 표시할 파라미터 필터 (빈 list = 전체)

    # Map Agent 파라미터
    map_lot_id:   str
    map_lot_ids:  str
    map_wf_ids:   str
    map_groupkey: str
    map_type:     str
    map_bin_type: str

    # Yield lot 비교 파라미터
    yield_lot_ids:  str
    yield_groupkey: str

    # Map 결과
    map_result:    str
    map_artifacts: Annotated[list, operator.add]

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402

# 에이전트 노드 재시도 정책 (Oracle/LLM 일시적 오류 자동 재시도)
_retry = RetryPolicy(max_attempts=2, initial_interval=1.0)

workflow = StateGraph(YieldQueryState)
workflow.add_node("rewrite", rewrite_node)
workflow.add_node("supervisor", supervisor_node)  # Command가 라우팅 처리
workflow.add_node("yield_agent", yield_agent_node, retry_policy=_retry)
workflow.add_node("wads_agent", wads_agent_node, retry_policy=_retry)
workflow.add_node("map_agent", map_agent_node, retry_policy=_retry)

workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "supervisor")
# supervisor → agent: Command(goto=...)가 처리 (conditional_edges 불필요)
# agent → supervisor: 정적 엣지로 루프 복귀
workflow.add_edge("yield_agent", "supervisor")
workflow.add_edge("wads_agent", "supervisor")
workflow.add_edge("map_agent", "supervisor")

yield_supervisor = workflow.compile()
