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
from langgraph.graph import StateGraph, START, END, add_messages
from pydantic import BaseModel, Field

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

    next: Literal["yield_agent", "wads_agent", "FINISH"] = Field(
        description="다음에 실행할 에이전트"
    )
    lotcd: str = Field(default="4SS", description="제품코드 (예: 4SS, 5NA, 6E2)")
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
    message: str = Field(description="사용자에게 전달할 한국어 메시지")


# ── Rewrite 시스템 프롬프트 ─────────────────────────────────
REWRITE_SYSTEM_PROMPT = """\
You are a query rewriter for a semiconductor yield analysis system.
Rewrite the user's message to be explicit and unambiguous using the provided context.

Rules:
- If the message already has clear intent ("4SS 수율 알려줘"), return it UNCHANGED
- If it is a vague follow-up ("보여줘", "응", "네", "좋아"):
  → Expand using prior context (anomaly params, lotcd, last action)
  → "보여줘" + anomaly_params=[VTH,IDSAT] → "VTH, IDSAT WADS 열화 리포트 보여줘"
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
    context = "\n".join(ctx_parts) if ctx_parts else "이전 컨텍스트 없음"

    response = _model.invoke([
        {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nUser message: {last_human.content}\n\nRewrite:"},
    ])

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
- If user mentions specific parameter names (VTH, IDSAT, FMAX, etc.), list them in filter_params
- Example: "VTH만 보여줘" → filter_params: ["VTH"]
- Example: "VTH랑 IDSAT 수율" → filter_params: ["VTH", "IDSAT"]
- No specific params → filter_params: []

Route to **yield_agent** when the user asks about:
- 수율(yield), pt1h 데이터, weekly metrics, 주간 파라미터
- Examples: "오늘 4SS 수율 알려줘", "저번주 수율 보여줘", "2주전 4SS 수율"

Route to **wads_agent** when:
- User says "보여줘", "응", "네", "좋아", "부탁해" — especially after a yield result
- User asks about 열화(degradation) detection, 검출 리포트, WADS 리포트
- Examples: "보여줘", "열화 리포트 보여줘", "1월 20일 검출 list 보여줘"

Route to **FINISH** when the request is unrelated to yield or WADS.

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
- Detect from user query (e.g. 4SS, 5NA, 6E2)
- Default: "4SS"
- For follow-up queries, keep the same lotcd from conversation history

=== OUTPUT FORMAT (STRICT) ===
Respond with ONLY a raw JSON object — no markdown, no code fences, no extra fields:
{{
  "next": "yield_agent" | "wads_agent" | "FINISH",
  "lotcd": "<product code, default 4SS>",
  "ref_date": "<YYYYMMDD for yield_agent, else empty string>",
  "wads_end_tm": "<YYYY-MM-DD for wads_agent, else empty string>",
  "filter_params": ["VTH", "IDSAT"],
  "message": "<Korean message>"
}}\
"""


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def supervisor_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """Supervisor 노드: with_structured_output으로 타입 안전 라우팅.

    이전 대화 히스토리(messages)를 그대로 LLM에 전달하므로
    "보여줘" 같은 follow-up 쿼리에서도 컨텍스트가 유지됩니다.
    """
    # Langfuse 트레이스 설정
    configurable = config.get("configurable", {}) if config else {}
    session_id = configurable.get("session_id", "")
    if session_id:
        try:
            get_client().update_current_trace(
                session_id=session_id,
                metadata={"anomaly_count": configurable.get("anomaly_count", 0)},
                tags=["yield-agent", "v3"],
            )
        except Exception:
            pass

    messages = state.get("messages", [])
    today = date.today()
    today_yyyymmdd = today.strftime("%Y%m%d")
    today_yyyy_mm_dd = today.strftime("%Y-%m-%d")

    prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today_yyyymmdd,
        today_yyyy_mm_dd=today_yyyy_mm_dd,
    )

    # 이전 이상감지 결과가 있으면 명시적으로 힌트 주입
    anomaly_params = state.get("anomaly_params", [])
    if anomaly_params:
        param_names = ", ".join(a["param"] for a in anomaly_params)
        prompt += (
            f"\n\n[이전 분석 결과] 이상 감지된 파라미터 ({len(anomaly_params)}개): {param_names}"
            "\n→ 사용자가 '보여줘', '응', '네' 등으로 응답하면 \"next\": \"wads_agent\"로 라우팅하세요."
        )

    # 직접 호출 + 정규식 JSON 추출
    # (gpt-oss-120b는 response_format=json_object를 지원하지 않아 content가 빈 문자열로 반환됨)
    try:
        raw = _model.invoke([{"role": "system", "content": prompt}, *messages])
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
        "next": decision.next,
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

    # 라우팅
    next: str


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402

workflow = StateGraph(YieldQueryState)
workflow.add_node("rewrite", rewrite_node)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("yield_agent", yield_agent_node)
workflow.add_node("wads_agent", wads_agent_node)
workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    lambda x: x["next"],
    {
        "yield_agent": "yield_agent",
        "wads_agent": "wads_agent",
        "FINISH": END,
    },
)
workflow.add_edge("yield_agent", END)
workflow.add_edge("wads_agent", END)

yield_supervisor = workflow.compile()
