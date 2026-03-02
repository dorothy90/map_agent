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
import os
import re
import logging
from datetime import date
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler as _LFHandler
from langgraph.graph import StateGraph, START, END, add_messages
from pydantic import BaseModel, Field

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.supervisor")


def _lf_callbacks() -> list:
    """현재 Langfuse span에 연결된 LangChain CallbackHandler 반환.
    @observe 컨텍스트 안에서 호출해야 generation이 child span으로 기록됨."""
    lf = get_client()
    trace_id = lf.get_current_trace_id()
    if not trace_id:
        return []
    return [_LFHandler(trace_context={
        "trace_id": trace_id,
        "parent_span_id": lf.get_current_observation_id(),
    })]

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
    need_cummap: bool = Field(
        default=False,
        description="True면 yield 테이블 후 4주치 category별 cummap도 생성. 특정 파라미터 수율 질문 시 True"
    )
    message: str = Field(description="사용자에게 전달할 한국어 메시지")
    # Map 파라미터
    map_lot_id:   str = Field(default="", description="단일 lot ID (예: 'LOTABC123')")
    map_lot_ids:  str = Field(default="", description="복수 lot IDs, 쉼표 구분")
    map_wf_ids:   str = Field(default="", description="wafer IDs, 쉼표 구분")
    map_groupkey: str = Field(default="", description="lot_id.wf_id 형식 (예: 'LOT001.01,LOT001.02')")
    map_type:     str = Field(default="binmap", description="binmap | cummap | all")
    map_bin_type: str = Field(default="pt1h_bin", description="pt1h_bin | pt2c_bin")


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

    # 마지막 HumanMessage만 교체
    new_messages = list(messages)
    for i in range(len(new_messages) - 1, -1, -1):
        if isinstance(new_messages[i], HumanMessage):
            new_messages[i] = HumanMessage(content=rewritten)
            break

    return {"messages": new_messages}


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

=== need_cummap (수율 + cummap 동시 요청) ===
- When user asks about yield of a SPECIFIC parameter → need_cummap: true
- Example: "IOFF 수율 알려줘" → yield_agent, filter_params: ["IOFF"], need_cummap: true
- Example: "VTH 수율 보여줘" → yield_agent, filter_params: ["VTH"], need_cummap: true
- Example: "수율 알려줘" (no specific param) → yield_agent, need_cummap: false
- When need_cummap is true, yield_agent runs first then map_agent generates 4-week category cummaps

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

Route to **FINISH** when the request is unrelated to yield, WADS, or wafer map.

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
- A full lot ID like "4SS2DPD", "4SSXCEW" is NOT a lotcd — it goes into map_lot_id or map_lot_ids
- Rule: if the string is longer than 5 characters, it is a lot ID, NOT a lotcd
- Default: "4SS"
- For follow-up queries, keep the same lotcd from conversation history

=== OUTPUT FORMAT (STRICT) ===
Respond with ONLY a raw JSON object — no markdown, no code fences, no extra fields:
{{
  "next": "yield_agent" | "wads_agent" | "map_agent" | "FINISH",
  "lotcd": "<product code, default 4SS>",
  "ref_date": "<YYYYMMDD for yield_agent, else empty string>",
  "wads_end_tm": "<YYYY-MM-DD for wads_agent, else empty string>",
  "filter_params": ["VTH", "IDSAT"],
  "need_cummap": false,
  "message": "<Korean message>",
  "map_lot_id":   "",
  "map_lot_ids":  "",
  "map_wf_ids":   "",
  "map_groupkey": "",
  "map_type":     "binmap",
  "map_bin_type": "pt1h_bin"
}}\
"""


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def supervisor_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """Supervisor 노드: with_structured_output으로 타입 안전 라우팅.

    이전 대화 히스토리(messages)를 그대로 LLM에 전달하므로
    "보여줘" 같은 follow-up 쿼리에서도 컨텍스트가 유지됩니다.
    """
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

    # 직접 호출 + 정규식 JSON 추출
    # (gpt-oss-120b는 response_format=json_object를 지원하지 않아 content가 빈 문자열로 반환됨)
    try:
        raw = _model.invoke(
            [{"role": "system", "content": prompt}, *messages],
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

    # lotcd: 이전 State 값 유지 (follow-up 대화)
    prev_lotcd = state.get("lotcd", "")
    new_lotcd = decision.lotcd or prev_lotcd or "4SS"

    # Langfuse — 라우팅 결정 후 메타데이터 기록 (session_id/tags는 root trace에서 설정)
    try:
        get_client().update_current_trace(
            metadata={
                "anomaly_count": configurable.get("anomaly_count", 0),
                "route": decision.next,
                "lotcd": new_lotcd,
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
        "[Supervisor] next=%-12s lotcd=%-6s ref_date=%s wads_end_tm=%s",
        decision.next,
        new_lotcd,
        ref_date,
        wads_end_tm,
    )

    return {
        "messages": [result_message],
        "lotcd": new_lotcd,
        "ref_date": ref_date,
        "wads_end_tm": wads_end_tm,
        "filter_params": decision.filter_params,
        "need_cummap": decision.need_cummap,
        "next": decision.next,
        "map_lot_id":   decision.map_lot_id,
        "map_lot_ids":  decision.map_lot_ids,
        "map_wf_ids":   decision.map_wf_ids,
        "map_groupkey": decision.map_groupkey,
        "map_type":     decision.map_type or "binmap",
        "map_bin_type": decision.map_bin_type or "pt1h_bin",
    }


# ── 공유 State 정의 ──────────────────────────────────────
class YieldQueryState(TypedDict):
    """Yield Query Supervisor의 공유 State

    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    """

    messages: Annotated[list, add_messages]

    # 조회 파라미터
    lotcd: str
    ref_date: str

    # 결과 데이터
    weeks_data: list
    table_result: str
    analysis_result: str

    # Yield 관련
    yield_artifacts: list

    # WADS 관련
    wads_end_tm: str
    wads_artifacts: list

    # 이상감지
    anomaly_params: list

    # 파라미터 필터
    filter_params: list  # 표시할 파라미터 필터 (빈 list = 전체)

    # yield → map 연계 (category별 cummap)
    need_cummap: bool
    cummap_weeks: list     # [{"week": "2026-W04", "start": "20260119", "end": "20260126"}, ...]
    cummap_target_bin: str  # fail bin value (예: "H")
    cummap_category: str    # category 이름 (예: "IOFF")

    # Map Agent 파라미터
    map_lot_id:   str
    map_lot_ids:  str
    map_wf_ids:   str
    map_groupkey: str
    map_type:     str
    map_bin_type: str

    # Map 결과
    map_result:    str
    map_artifacts: list

    # 라우팅
    next: str

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402

workflow = StateGraph(YieldQueryState)
workflow.add_node("rewrite", rewrite_node)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("yield_agent", yield_agent_node)
workflow.add_node("wads_agent", wads_agent_node)
workflow.add_node("map_agent", map_agent_node)
workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    lambda x: x["next"],
    {
        "yield_agent": "yield_agent",
        "wads_agent":  "wads_agent",
        "map_agent":   "map_agent",
        "FINISH":      END,
    },
)
workflow.add_conditional_edges(
    "yield_agent",
    lambda x: "map_agent" if x.get("need_cummap") else END,
    {"map_agent": "map_agent", END: END},
)
workflow.add_edge("wads_agent", END)
workflow.add_edge("map_agent", END)

yield_supervisor = workflow.compile()
