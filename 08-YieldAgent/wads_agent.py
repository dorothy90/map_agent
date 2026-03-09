from __future__ import annotations

import contextvars
from dotenv import load_dotenv
import os
from datetime import date
from typing import Any, Dict, List, Optional, Annotated, Literal, TypedDict

import pandas as pd
import oracledb
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from rich import print
from langfuse import observe, get_client

from lf_utils import lf_callbacks as _lf_callbacks
from common import get_oracle_connection as _get_oracle_connection

# .env 로드 및 모델 설정
load_dotenv(override=True)
_MODEL_NAME = os.getenv("RETRIEVE_CHAIN_MODEL")
_BASE_URL = os.getenv("OPENROUTER_BASE_URL")
_API_KEY = os.getenv("OPENROUTER_API_KEY")

_ORACLE_TABLE = os.getenv("WADS_TABLE", "WADS_TABLE")

# 도구 결과를 요청별로 격리하는 ContextVar (thread-safe & async-safe)
# 각 wads_agent_node 호출 시 새 dict를 set()하여 격리
_tool_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_wads_tool_payload"
)


def _get_tool_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 tool payload storage 반환 (없으면 초기화)"""
    try:
        return _tool_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {"reports": []}
        _tool_payload_var.set(storage)
        return storage


@observe(name="wads_query_oracle")
def _query_wads_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    columns: str = "*",
) -> pd.DataFrame:
    """Oracle에서 WADS 데이터 조회 (SQL WHERE + LIKE 필터링)

    Args:
        lotcd: 로트코드 부분 일치 필터 (case-insensitive)
        end_tm: 종료시간 부분 일치 필터
        parameter: 스텝 설명 부분 일치 필터
        columns: 조회할 컬럼 (기본값: "*")

    Returns:
        필터링된 결과 DataFrame
    """
    conditions = []
    bind_vars = {}

    if lotcd:
        conditions.append("UPPER(LOTCD) LIKE UPPER(:lotcd)")
        bind_vars["lotcd"] = f"%{lotcd}%"
    if end_tm:
        conditions.append("END_TM LIKE :end_tm")
        bind_vars["end_tm"] = f"%{end_tm}%"
    if parameter:
        conditions.append("UPPER(CTN_DESC) LIKE UPPER(:parameter)")
        bind_vars["parameter"] = f"%{parameter}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT {columns} FROM {_ORACLE_TABLE} WHERE {where_clause}"

    conn = _get_oracle_connection()
    try:
        # CLOB을 문자열로 자동 변환하도록 output type handler 설정
        def output_type_handler(cursor, metadata):
            if metadata.type_code is oracledb.DB_TYPE_CLOB:
                return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)

        conn.outputtypehandler = output_type_handler

        cursor = conn.cursor()
        cursor.execute(sql, bind_vars)

        col_names = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()

        df = pd.DataFrame(rows, columns=col_names)
        return df
    finally:
        conn.close()


# -------------------- WADS Tools --------------------
# 도구 결과는 요약만 LLM에 전달하고, 실제 데이터는 ContextVar(_tool_payload_var)에 저장


@tool
def wads_query_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    parameter: Optional[str] = None,
) -> str:
    """
    WADS 데이터를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 메타정보를 반환합니다.

    Args:
        lotcd: 랏코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 데이터는 별도 저장됨)
    """
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER",
        )
    except Exception as e:
        storage["query"] = [
            {
                "error": "DBError",
                "detail": f"Oracle 연결/조회 오류: {e}",
            }
        ]
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        storage["query"] = [
            {"error": "NoMatch", "detail": "조건에 맞는 WADS 데이터가 없습니다."}
        ]
        return "조건에 맞는 WADS 데이터가 없습니다."

    # 실제 데이터는 컨텍스트 저장소에 저장 (LLM context에 포함되지 않음)
    result = filtered_df[["lotcd", "end_tm", "parameter"]].to_dict(orient="records")
    storage["query"] = result

    # LLM에게는 요약만 전달
    return f"WADS 데이터 조회 완료: 총 {len(result)}건의 데이터가 조회되었습니다. (데이터는 화면에 별도 표시됩니다)"


@tool
def wads_get_html_report(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    limit: int = 1,
) -> str:
    """
    WADS HTML 리포트를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 HTML 콘텐츠를 반환합니다.
    여러 번 호출하면 모든 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.
        limit: 하위호환을 위해 받지만, 현재는 무시됩니다. (항상 1개만 반환)

    Returns:
        조회 결과 요약 메시지 (실제 HTML은 별도 저장됨)
    """
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER, HTML",
        )
    except Exception as e:
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        return "조건에 맞는 WADS 데이터가 없습니다."

    # 실제 데이터는 컨텍스트 저장소에 리스트로 누적 (LLM context에 포함되지 않음)
    if "reports" not in storage:
        storage["reports"] = []

    for _, row in filtered_df.iterrows():
        storage["reports"].append(
            {
                "lotcd": row["lotcd"],
                "end_tm": row["end_tm"],
                "parameter": row["parameter"],
                "html": row["html"],
            }
        )

    count = len(filtered_df)
    summary = ", ".join(
        f"lotcd={r['lotcd']} parameter={r['parameter']}"
        for r in storage["reports"][-count:]
    )
    # LLM에게는 메타정보 요약만 전달 (HTML 전체 내용은 제외)
    return f"WADS HTML 리포트 조회 완료: {count}건 — {summary} (리포트는 화면에 별도 표시됩니다)"


# -------------------- WADS Agent (StateGraph 패턴) --------------------
# 도구 결과가 LLM context에 포함되지 않도록 직접 StateGraph 구성

# WADS Agent용 시스템 프롬프트
WADS_SYSTEM_PROMPT_TEMPLATE = """당신은 WADS(Weekly Aggregation Data System) 전문 어시스턴트입니다.

**현재 날짜: {current_date}**
(사용자가 "1월 4일"처럼 연도 없이 날짜를 말하면, 현재 연도 기준으로 해석하세요)

사용자가 주간 집계 데이터, 변곡점 분석, Layer1 리포트에 대해 질문하면 적절한 도구를 사용하여 정보를 조회하고 답변합니다.

## 사용 가능한 도구:
1. **wads_query_data**: WADS 데이터 메타정보 조회
   - lotcd, end_tm, parameter로 필터링하여 매칭되는 데이터 목록 반환
   - HTML 콘텐츠는 제외하고 메타정보만 반환

2. **wads_get_html_report**: WADS HTML 리포트 조회
   - lotcd, end_tm, parameter로 필터링하여 HTML 리포트 반환
   - **여러 리포트 요청 시**: 각 조건별로 도구를 여러 번 호출하세요. 모든 리포트가 누적되어 표시됩니다.
   - 예: step01, step02 리포트 요청 시 → wads_get_html_report(parameter="step01") + wads_get_html_report(parameter="step02")

## 데이터 구조:
- lotcd: 로트코드 (예: 5NA, 4SA, 6E2)
- end_tm: 종료 시간 (예: 2026-01-01 18:07:01)
- parameter: 스텝 설명 (예: step01, step02, ..., step09)
- html: Layer1 전수 집계 테이블 HTML

## 응답 규칙:
- 사용자가 특정 조건을 언급하면 해당 필터를 적용하세요.
- 조건을 언급하지 않으면 전체 데이터를 조회합니다.
- HTML 리포트를 요청하면 wads_get_html_report를 사용하세요.
- 데이터 목록만 필요하면 wads_query_data를 사용하세요.
- 조회 결과가 없으면 명확하게 안내합니다.
- 응답은 한국어로 친절하게 제공합니다.

## 사용 예시:
- 전체 데이터 조회: wads_query_data()
- 특정 로트 조회: wads_query_data(lotcd="5NA")
- 특정 날짜 조회: wads_query_data(end_tm="2026-01-01")
- 특정 스텝 리포트: wads_get_html_report(parameter="step01")
- 복합 조건: wads_get_html_report(lotcd="5NA", parameter="step05")

## 중요: 응답 형식
- 도구 호출 결과(데이터/리포트)는 별도의 HTML 카드로 자동 표시됩니다.
- 따라서 **테이블이나 표를 직접 만들지 마세요**.
- 조회 결과에 대해 간단한 요약/설명만 1-2문장으로 작성하세요.
- 예시: "5NA 로트의 step01 리포트를 조회했습니다."

## 중요: 데이터 없음 vs 연결 오류 구분
- 도구가 "조건에 맞는 WADS 데이터가 없습니다"를 반환하면 → 연결 오류가 아님. "해당 조건의 WADS 데이터가 없습니다"로 안내.
- 도구가 "Oracle 연결/조회에 실패했습니다"를 반환한 경우에만 → 연결 오류로 안내.
- 데이터가 없는 것을 절대 "연결 오류", "시스템 오류"로 표현하지 마세요.
"""

# WADS 전용 도구 리스트
WADS_TOOLS = [wads_query_data, wads_get_html_report]

# WADS Agent용 모델
_wads_model = ChatOpenAI(
    model=_MODEL_NAME,
    base_url=_BASE_URL,
    api_key=_API_KEY,
    temperature=0,
)


# WADS Agent State 정의
class WADSAgentState(TypedDict):
    """WADS Agent 내부 상태"""

    messages: Annotated[List, add_messages]


def _build_wads_graph():
    """WADS Agent StateGraph 구조 빌드 (1회만 실행, 모듈 수준 싱글턴)

    날짜는 호출 시 SystemMessage로 주입하므로 그래프 자체는 날짜에 무관합니다.
    """
    from langchain_core.messages import SystemMessage

    # LLM에 도구 바인딩
    llm_with_tools = _wads_model.bind_tools(WADS_TOOLS)

    # StateGraph 빌더 생성
    builder = StateGraph(WADSAgentState)

    def chatbot(state: WADSAgentState):
        """도구를 사용할 수 있는 챗봇 노드"""
        messages = state["messages"]
        # SystemMessage가 없으면 placeholder (호출자가 반드시 주입)
        if not messages or not isinstance(messages[0], SystemMessage):
            from datetime import datetime

            fallback_date = datetime.now().strftime("%Y년 %m월 %d일")
            fallback_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(
                current_date=fallback_date
            )
            messages = [SystemMessage(content=fallback_prompt)] + list(messages)

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    builder.add_node("chatbot", chatbot)
    tool_node = ToolNode(tools=WADS_TOOLS, handle_tool_errors=True)
    builder.add_node("tools", tool_node)
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")
    builder.add_edge(START, "chatbot")

    return builder.compile(checkpointer=False)


# 모듈 수준 싱글턴 — 매번 재생성하지 않음
_wads_graph = _build_wads_graph()


def _create_wads_agent():
    """호환성 유지용 래퍼 — 싱글턴 그래프 반환"""
    return _wads_graph


# -------------------- HTML 렌더링 --------------------
def _html_escape(s: Any) -> str:
    txt = "" if s is None else str(s)
    return (
        txt.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_wads_query_html(payload: List[Dict[str, Any]]) -> str:
    """wads_query_data 결과를 HTML로 렌더링"""
    first = payload[0] if payload else {}
    is_error = bool(first.get("error"))

    style = """<style>
.wads-card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#fff}
.wads-title{font-weight:700;margin-bottom:8px;color:#1a1a2e}
.wads-sub{color:#6b7280;font-size:12px;margin-bottom:10px}
.wads-table{width:100%;border-collapse:collapse;font-size:13px}
.wads-table th,.wads-table td{border:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top}
.wads-table th{background:#f8f9fa}
.wads-badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#e0f2fe;color:#0369a1;font-size:12px}
.wads-empty{color:#6b7280}
</style>"""

    header = (
        "<div class='wads-card'><div class='wads-title'>WADS: 데이터 조회 결과</div>"
    )
    footer = "</div>"

    if is_error:
        msg = first.get("detail") or first.get("error") or "오류가 발생했습니다."
        body = f"<div class='wads-empty'>{_html_escape(msg)}</div>"
        return style + header + body + footer

    sub = f"<div class='wads-sub'>총 <span class='wads-badge'>{len(payload)}건</span> 조회됨</div>"

    rows_html = ""
    for row in payload:
        rows_html += (
            "<tr>"
            f"<td>{_html_escape(row.get('lotcd'))}</td>"
            f"<td>{_html_escape(row.get('end_tm'))}</td>"
            f"<td>{_html_escape(row.get('parameter'))}</td>"
            "</tr>"
        )

    table = (
        "<table class='wads-table'>"
        "<thead><tr><th>LOT코드</th><th>종료시간</th><th>스텝</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )

    return style + header + sub + table + footer


def _render_wads_report_html(payload: Any) -> str:
    """wads_get_html_report 결과를 HTML로 렌더링 (단일 또는 여러 리포트)"""
    # 리스트인 경우 (여러 리포트)
    if isinstance(payload, list):
        if not payload:
            return "<div>HTML 콘텐츠가 없습니다.</div>"

        # 여러 리포트를 하나의 HTML로 합침
        html_parts = []
        for idx, report in enumerate(payload):
            if report.get("error"):
                msg = (
                    report.get("detail")
                    or report.get("error")
                    or "오류가 발생했습니다."
                )
                html_parts.append(f"<div>WADS 리포트 오류: {_html_escape(msg)}</div>")
            else:
                # 리포트 메타정보 헤더
                header = f"""<div style="background:#f0f9ff;padding:8px 12px;border-radius:8px;margin-bottom:8px;border-left:4px solid #0284c7;">
                    <strong>📊 리포트 {idx + 1}</strong>: {_html_escape(report.get('lotcd', ''))} / {_html_escape(report.get('parameter', ''))} ({_html_escape(report.get('end_tm', ''))})
                </div>"""
                html_content = report.get("html", "")
                if html_content:
                    html_parts.append(header + html_content)
                else:
                    html_parts.append(header + "<div>HTML 콘텐츠가 없습니다.</div>")

        # 구분선으로 여러 리포트 연결
        separator = (
            '<hr style="margin:20px 0;border:none;border-top:2px dashed #e5e7eb;">'
        )
        return separator.join(html_parts)

    # 단일 dict인 경우 (이전 버전 호환)
    if isinstance(payload, dict):
        if payload.get("error"):
            msg = (
                payload.get("detail") or payload.get("error") or "오류가 발생했습니다."
            )
            return f"<div>WADS 리포트 오류: {_html_escape(msg)}</div>"

        html_content = payload.get("html", "")
        if html_content:
            return html_content

    return "<div>HTML 콘텐츠가 없습니다.</div>"


# -------------------- LangGraph 노드용 래퍼 함수 --------------------
def wads_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    naive_rag.py의 LangGraph 노드로 연결되는 WADS 처리 함수.
    내부적으로 현재 날짜가 포함된 WADS Agent를 생성하여 호출합니다.

    도구 결과는 LLM context에 포함되지 않고, 별도의 artifacts로 반환됩니다.
    - LLM은 도구 실행 요약만 받습니다 (예: "WADS 데이터 조회 완료: 총 10건")
    - 실제 데이터(HTML 리포트, 쿼리 결과)는 _tool_payload_var(ContextVar)에서 가져와 artifacts로 전달
    """
    print(f"\n{'='*60}")
    print("[WADS Agent] 시작")

    q = state["question"]
    wads_ctx = state.get("wads_ctx") or {}
    print(f"question: {q}")
    print(f"wads_ctx: {wads_ctx}")

    # 이전 대화 히스토리 구성
    messages = state.get("messages", [])

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {"reports": []}
    _tool_payload_var.set(storage)

    # 현재 날짜가 포함된 에이전트 생성 및 호출
    agent = _create_wads_agent()
    result = agent.invoke(
        {"messages": messages + [HumanMessage(content=q)]},
        config={"callbacks": _lf_callbacks()},
    )

    print(f"result messages count: {len(result.get('messages', []))}")

    # 결과에서 마지막 AI 메시지 추출
    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    # 저장소에서 실제 데이터 추출 (LLM context에 포함되지 않음)
    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])

    print(f"[DEBUG] query_payload: {query_payload is not None}")
    print(f"[DEBUG] reports_payload count: {len(reports_payload)}")

    # HTML 아티팩트 생성
    artifacts = []
    kind = None

    # reports 우선 (여러 개일 수 있음), 없으면 query
    if reports_payload:
        kind = "report"
        html = _render_wads_report_html(reports_payload)
    elif query_payload:
        kind = "query"
        html = _render_wads_query_html(query_payload)
    else:
        html = None

    if html:
        artifacts = [
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": f"wads_{kind}",
            }
        ]

    result_dict = {
        "answer": answer,
        "artifacts": artifacts,
        "messages": [HumanMessage(content=q), AIMessage(content=answer)],
        "wads_ctx": {
            "active": True,
            "last_kind": kind,
        },
    }
    return result_dict


# -------------------- WADS Agent Node (LangGraph 노드용) --------------------
from common import timed  # noqa: E402


@observe(name="wads_agent_node")
@timed
def wads_agent_node(state: dict, config: RunnableConfig) -> dict:
    """WADS Agent 노드: 열화 검출 리포트를 Oracle DB에서 조회

    Supervisor가 추출한 lotcd, wads_end_tm을 사용하여
    WADS 서브에이전트를 호출하고 HTML 리포트를 가져옵니다.
    """
    lotcd = state.get("lotcd", "4SS")
    end_tm = state.get("wads_end_tm", "")
    if not end_tm:
        end_tm = date.today().strftime("%Y-%m-%d")

    # 파라미터 로깅
    print("=" * 60)
    print("[WADS Agent] State에서 파라미터 읽기:")
    print(f"  - lotcd: {lotcd}")
    print(f"  - end_tm: {end_tm}")
    print("=" * 60)

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {"reports": []}
    _tool_payload_var.set(storage)

    # WADS 서브에이전트 호출 (싱글턴 그래프 + 날짜를 SystemMessage로 주입)
    from langchain_core.messages import SystemMessage
    from datetime import datetime

    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    query = f"{lotcd} 로트의 {end_tm} WADS 리포트를 보여줘"
    print(f"[WADS Agent] 쿼리: {query}")

    try:
        # 부모 config의 LangGraph 내부 파라미터(__pregel_* 등)를 제거하고 Langfuse 콜백만 전달
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 10}
        result = _wads_graph.invoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=query),
                ]
            },
            config=sub_config,
        )
    except Exception as e:
        print(f"[ERROR] WADS Agent 실행 실패: {e}")
        error_message = AIMessage(
            content=f"WADS 리포트 조회 중 오류가 발생했습니다: {e}",
            name="wads_agent",
        )
        return {"messages": [error_message], "wads_artifacts": []}

    # 결과에서 마지막 AI 메시지 추출
    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    # 저장소에서 실제 데이터 추출 (LLM context에 포함되지 않음)
    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])

    print(f"[WADS Agent] query_payload: {query_payload is not None}")
    print(f"[WADS Agent] reports_payload count: {len(reports_payload)}")

    # HTML 아티팩트 생성
    artifacts = []
    if reports_payload:
        html = _render_wads_report_html(reports_payload)
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "wads_report",
            }
        )
    elif query_payload:
        html = _render_wads_query_html(query_payload)
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "wads_query",
            }
        )

    result_message = AIMessage(content=answer, name="wads_agent")

    return {
        "messages": [result_message],
        "wads_artifacts": artifacts,
    }


# -------------------- 직접 실행 테스트 --------------------
if __name__ == "__main__":
    from langchain_teddynote.messages import stream_graph

    # 테스트: StateGraph로 만든 WADS Agent 실행
    print("=== WADS Agent 테스트 (StateGraph 패턴) ===\n")
    print("도구 결과는 LLM context에 포함되지 않고, 요약만 전달됩니다.\n")

    stream_graph(
        _create_wads_agent(),
        inputs={
            "messages": [HumanMessage(content="4SS 로트의 2월28일 리포트를 보여줘")]
        },
    )

    # 실제 데이터가 저장소에 있는지 확인
    storage = _get_tool_payload()
    print("\n" + "=" * 60)
    print("[DEBUG] tool payload 내용:")
    print(f"  query: {storage.get('query') is not None}")
    reports = storage.get("reports", [])
    print(f"  reports count: {len(reports)}")

    for idx, report in enumerate(reports):
        print(f"  report[{idx}] lotcd: {report.get('lotcd')}")
        print(f"  report[{idx}] parameter: {report.get('parameter')}")
        print(f"  report[{idx}] html 길이: {len(report.get('html', ''))} 글자")
