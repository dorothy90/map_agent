from __future__ import annotations

from dotenv import load_dotenv
import os
from typing import Any, Dict, List, Optional, Annotated, Literal

import oracledb
import pandas as pd

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from rich import print

# .env 로드 및 모델 설정
load_dotenv(override=True)
_MODEL_NAME = os.getenv("RETRIEVE_CHAIN_MODEL")
_BASE_URL = os.getenv("OPENROUTER_BASE_URL")
_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Oracle DB 연결 설정
_ORACLE_USER = os.getenv("ORACLE_USER")
_ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
_ORACLE_DSN = os.getenv("ORACLE_DSN")
_ORACLE_TABLE = os.getenv("ORACLE_TABLE", "WADS_TABLE")

# 도구 결과를 임시 저장할 전역 변수 (LLM context에 포함되지 않는 실제 데이터)
# reports는 리스트로 저장하여 여러 리포트를 누적
_TOOL_PAYLOAD_STORAGE: Dict[str, Any] = {"reports": []}


def _get_oracle_connection() -> oracledb.Connection:
    """Oracle thin-mode 연결 생성"""
    return oracledb.connect(
        user=_ORACLE_USER,
        password=_ORACLE_PASSWORD,
        dsn=_ORACLE_DSN,
    )


def _query_wads_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    ctn_desc: Optional[str] = None,
    columns: str = "*",
) -> pd.DataFrame:
    """Oracle에서 WADS 데이터 조회 (SQL WHERE + LIKE 필터링)

    Args:
        lotcd: 로트코드 부분 일치 필터 (case-insensitive)
        end_tm: 종료시간 부분 일치 필터
        ctn_desc: 스텝 설명 부분 일치 필터
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
    if ctn_desc:
        conditions.append("UPPER(CTN_DESC) LIKE UPPER(:ctn_desc)")
        bind_vars["ctn_desc"] = f"%{ctn_desc}%"

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
# 도구 결과는 요약만 LLM에 전달하고, 실제 데이터는 _TOOL_PAYLOAD_STORAGE에 저장


@tool
def wads_query_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    ctn_desc: Optional[str] = None,
) -> str:
    """
    WADS 데이터를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 메타정보를 반환합니다.

    Args:
        lotcd: 랏코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        ctn_desc: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 데이터는 별도 저장됨)
    """
    global _TOOL_PAYLOAD_STORAGE

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            ctn_desc=ctn_desc,
            columns="LOTCD, END_TM, CTN_DESC",
        )
    except Exception as e:
        _TOOL_PAYLOAD_STORAGE["query"] = [
            {
                "error": "DBError",
                "detail": f"Oracle 연결/조회 오류: {e}",
            }
        ]
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        _TOOL_PAYLOAD_STORAGE["query"] = [
            {"error": "NoMatch", "detail": "조건에 맞는 WADS 데이터가 없습니다."}
        ]
        return "조건에 맞는 WADS 데이터가 없습니다."

    # 실제 데이터는 전역 저장소에 저장 (LLM context에 포함되지 않음)
    result = filtered_df[["lotcd", "end_tm", "ctn_desc"]].to_dict(orient="records")
    _TOOL_PAYLOAD_STORAGE["query"] = result

    # LLM에게는 요약만 전달
    return f"WADS 데이터 조회 완료: 총 {len(result)}건의 데이터가 조회되었습니다. (데이터는 화면에 별도 표시됩니다)"


@tool
def wads_get_html_report(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    ctn_desc: Optional[str] = None,
    limit: int = 1,
) -> str:
    """
    WADS HTML 리포트를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 HTML 콘텐츠를 반환합니다.
    여러 번 호출하면 모든 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        ctn_desc: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.
        limit: 하위호환을 위해 받지만, 현재는 무시됩니다. (항상 1개만 반환)

    Returns:
        조회 결과 요약 메시지 (실제 HTML은 별도 저장됨)
    """
    global _TOOL_PAYLOAD_STORAGE

    try:
        filtered_df = _query_wads_data(lotcd=lotcd, end_tm=end_tm, ctn_desc=ctn_desc)
    except Exception as e:
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        return "조건에 맞는 WADS 데이터가 없습니다."

    # 실제 데이터는 전역 저장소에 리스트로 누적 (LLM context에 포함되지 않음)
    report_data = {
        "lotcd": filtered_df.iloc[0]["lotcd"],
        "end_tm": filtered_df.iloc[0]["end_tm"],
        "ctn_desc": filtered_df.iloc[0]["ctn_desc"],
        "html": filtered_df.iloc[0]["html"],
    }

    # reports 리스트에 추가 (덮어쓰지 않고 누적)
    if "reports" not in _TOOL_PAYLOAD_STORAGE:
        _TOOL_PAYLOAD_STORAGE["reports"] = []
    _TOOL_PAYLOAD_STORAGE["reports"].append(report_data)

    # LLM에게는 메타정보 요약만 전달 (HTML 전체 내용은 제외)
    return f"WADS HTML 리포트 조회 완료: lotcd={report_data['lotcd']}, end_tm={report_data['end_tm']}, ctn_desc={report_data['ctn_desc']} (리포트는 화면에 별도 표시됩니다)"


# -------------------- WADS Agent (StateGraph 패턴) --------------------
# 도구 결과가 LLM context에 포함되지 않도록 직접 StateGraph 구성

# WADS Agent용 시스템 프롬프트
WADS_SYSTEM_PROMPT_TEMPLATE = """당신은 WADS(Weekly Aggregation Data System) 전문 어시스턴트입니다.

**현재 날짜: {current_date}**
(사용자가 "1월 4일"처럼 연도 없이 날짜를 말하면, 현재 연도 기준으로 해석하세요)

사용자가 주간 집계 데이터, 변곡점 분석, Layer1 리포트에 대해 질문하면 적절한 도구를 사용하여 정보를 조회하고 답변합니다.

## 사용 가능한 도구:
1. **wads_query_data**: WADS 데이터 메타정보 조회
   - lotcd, end_tm, ctn_desc로 필터링하여 매칭되는 데이터 목록 반환
   - HTML 콘텐츠는 제외하고 메타정보만 반환

2. **wads_get_html_report**: WADS HTML 리포트 조회
   - lotcd, end_tm, ctn_desc로 필터링하여 HTML 리포트 반환
   - **여러 리포트 요청 시**: 각 조건별로 도구를 여러 번 호출하세요. 모든 리포트가 누적되어 표시됩니다.
   - 예: step01, step02 리포트 요청 시 → wads_get_html_report(ctn_desc="step01") + wads_get_html_report(ctn_desc="step02")

## 데이터 구조:
- lotcd: 로트코드 (예: 5NA, 4SA, 6E2)
- end_tm: 종료 시간 (예: 2026-01-01 18:07:01)
- ctn_desc: 스텝 설명 (예: step01, step02, ..., step09)
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
- 특정 스텝 리포트: wads_get_html_report(ctn_desc="step01")
- 복합 조건: wads_get_html_report(lotcd="5NA", ctn_desc="step05")

## 중요: 응답 형식
- 도구 호출 결과(데이터/리포트)는 별도의 HTML 카드로 자동 표시됩니다.
- 따라서 **테이블이나 표를 직접 만들지 마세요**.
- 조회 결과에 대해 간단한 요약/설명만 1-2문장으로 작성하세요.
- 예시: "5NA 로트의 step01 리포트를 조회했습니다."
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
class WADSAgentState(Dict):
    """WADS Agent 내부 상태"""

    messages: Annotated[List, add_messages]


def _create_wads_agent():
    """현재 날짜를 포함한 WADS Agent 생성 (StateGraph 패턴)"""
    from datetime import datetime
    from langchain_core.messages import SystemMessage

    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    # LLM에 도구 바인딩
    llm_with_tools = _wads_model.bind_tools(WADS_TOOLS)

    # StateGraph 빌더 생성
    builder = StateGraph(WADSAgentState)

    def chatbot(state: WADSAgentState):
        """도구를 사용할 수 있는 챗봇 노드"""
        # 시스템 메시지가 없으면 추가
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    # 노드 추가
    builder.add_node("chatbot", chatbot)

    # ToolNode 추가 - 도구를 실행하는 노드
    tool_node = ToolNode(tools=WADS_TOOLS)
    builder.add_node("tools", tool_node)

    # 조건부 엣지 추가
    builder.add_conditional_edges("chatbot", tools_condition)

    # 도구 실행 후 다시 챗봇으로
    builder.add_edge("tools", "chatbot")

    # 시작점 설정
    builder.add_edge(START, "chatbot")

    # 그래프 컴파일
    return builder.compile()


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
            f"<td>{_html_escape(row.get('ctn_desc'))}</td>"
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
                    <strong>📊 리포트 {idx + 1}</strong>: {_html_escape(report.get('lotcd', ''))} / {_html_escape(report.get('ctn_desc', ''))} ({_html_escape(report.get('end_tm', ''))})
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
    - 실제 데이터(HTML 리포트, 쿼리 결과)는 _TOOL_PAYLOAD_STORAGE에서 가져와 artifacts로 전달
    """
    global _TOOL_PAYLOAD_STORAGE

    print(f"\n{'='*60}")
    print("[WADS Agent] 시작")

    q = state["question"]
    wads_ctx = state.get("wads_ctx") or {}
    print(f"question: {q}")
    print(f"wads_ctx: {wads_ctx}")

    # 이전 대화 히스토리 구성
    messages = state.get("messages", [])

    # 도구 결과 저장소 초기화 (reports는 리스트로 유지)
    _TOOL_PAYLOAD_STORAGE.clear()
    _TOOL_PAYLOAD_STORAGE["reports"] = []

    # 현재 날짜가 포함된 에이전트 생성 및 호출
    agent = _create_wads_agent()
    result = agent.invoke({"messages": messages + [HumanMessage(content=q)]})

    print(f"result messages count: {len(result.get('messages', []))}")

    # 결과에서 마지막 AI 메시지 추출
    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    # _TOOL_PAYLOAD_STORAGE에서 실제 데이터 추출
    # (도구 함수에서 저장한 데이터 - LLM context에 포함되지 않음)
    query_payload = _TOOL_PAYLOAD_STORAGE.get("query")
    reports_payload = _TOOL_PAYLOAD_STORAGE.get("reports", [])

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

    # 도구 결과 저장소 정리
    _TOOL_PAYLOAD_STORAGE.clear()
    _TOOL_PAYLOAD_STORAGE["reports"] = []

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


# -------------------- 직접 실행 테스트 --------------------
if __name__ == "__main__":
    from langchain_teddynote.messages import stream_graph

    # 테스트: StateGraph로 만든 WADS Agent 실행
    print("=== WADS Agent 테스트 (StateGraph 패턴) ===\n")
    print("도구 결과는 LLM context에 포함되지 않고, 요약만 전달됩니다.\n")

    stream_graph(
        _create_wads_agent(),
        inputs={
            "messages": [HumanMessage(content="5NA 로트의 step01 리포트를 보여줘")]
        },
    )

    # 실제 데이터가 저장소에 있는지 확인
    print("\n" + "=" * 60)
    print("[DEBUG] _TOOL_PAYLOAD_STORAGE 내용:")
    print(f"  query: {_TOOL_PAYLOAD_STORAGE.get('query') is not None}")
    reports = _TOOL_PAYLOAD_STORAGE.get("reports", [])
    print(f"  reports count: {len(reports)}")

    for idx, report in enumerate(reports):
        print(f"  report[{idx}] lotcd: {report.get('lotcd')}")
        print(f"  report[{idx}] ctn_desc: {report.get('ctn_desc')}")
        print(f"  report[{idx}] html 길이: {len(report.get('html', ''))} 글자")
