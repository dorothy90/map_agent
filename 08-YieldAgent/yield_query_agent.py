# ============================================================
# 1. 환경 설정
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import sys
import json
import time
import httpx
import logging
import functools
from datetime import date, datetime, timedelta
from typing import Annotated, TypedDict, Literal
from pathlib import Path
from tabulate import tabulate

from langchain_core.messages import convert_to_messages, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command

# ── Langfuse 트레이싱 ────────────────────────────────────
from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
from langfuse.decorators import observe, langfuse_context

# Langfuse 콜백 핸들러 (환경변수에서 키 자동 로드)
langfuse_handler = LangfuseCallbackHandler()

# ── 로깅 설정 ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("yield_agent")


def timed(func):
    """함수 실행 시간을 측정하는 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.info(f"▶ {func.__name__} 시작")
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.info(f"◀ {func.__name__} 완료 ({elapsed:.2f}s)")
        return result
    return wrapper


# ============================================================
# 1.1 커스텀 State 정의 (Agent들이 공유하는 데이터)
# ============================================================
class YieldQueryState(TypedDict):
    """Yield Query Supervisor의 공유 State

    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    """
    messages: Annotated[list, add_messages]  # 대화 히스토리

    # 조회 파라미터
    lotcd: str         # 제품코드 (예: "4SS")
    ref_date: str      # 기준 날짜 YYYYMMDD (예: "20260209")

    # 결과 데이터
    weeks_data: list   # 4주치 API 응답 데이터 [{week, lotcount, wfCount, ...}, ...]
    table_result: str  # 최종 테이블 문자열
    analysis_result: str  # LLM 분석 결과 (열화/개선 Top 3 등)

    # WADS 관련
    wads_end_tm: str       # WADS 조회용 end_tm (예: "2026-02-09")
    wads_artifacts: list   # WADS HTML 아티팩트 리스트

    # 라우팅
    next_agent: str    # 다음 에이전트 (yield_agent, wads_agent, END)


# ============================================================
# 2. 헬퍼 함수
# ============================================================
def pretty_print_message(message, indent=False):
    """개별 메시지를 포맷팅하여 출력합니다."""
    pretty_message = message.pretty_repr(html=True)
    if not indent:
        print(pretty_message)
        return

    indented = "\n".join("\t" + c for c in pretty_message.split("\n"))
    print(indented)


def pretty_print_messages(update, last_message=False):
    """그래프 실행 결과의 메시지 업데이트를 포맷팅하여 출력합니다."""
    is_subgraph = False

    if isinstance(update, tuple):
        ns, update = update
        if len(ns) == 0:
            return

        graph_id = ns[-1].split(":")[0]
        print(f"[서브그래프 {graph_id}로부터 업데이트]")
        print("\n")
        is_subgraph = True

    for node_name, node_update in update.items():
        if node_update is None or "messages" not in node_update:
            continue
        update_label = f"[노드 {node_name}로부터 업데이트]"
        if is_subgraph:
            update_label = "\t" + update_label

        print(update_label)
        print("\n")

        messages = convert_to_messages(node_update["messages"])
        if last_message:
            messages = messages[-1:]

        for m in messages:
            pretty_print_message(m, indent=is_subgraph)
        print("\n")


# ============================================================
# 3. 날짜 유틸리티
# ============================================================
def _iso_week_str(d: date) -> str:
    """date -> ISO 주차 문자열 (예: 2026-W06)"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_monday(d: date) -> date:
    """date가 속한 주의 월요일을 반환"""
    return d - timedelta(days=d.isoweekday() - 1)


def _get_4_week_dates(ref: date) -> list[date]:
    """기준 날짜가 속한 주부터 과거 4주의 월요일 리스트 반환 (오래된순)

    예: ref가 2026-W06이면 -> [W03 월요일, W04 월요일, W05 월요일, W06 월요일]
    """
    monday = _week_monday(ref)
    return [monday - timedelta(weeks=i) for i in range(3, -1, -1)]


# ============================================================
# 4. FastAPI 호출 함수
# ============================================================
API_BASE_URL = os.getenv("YIELD_API_BASE_URL", "http://127.0.0.1:8000")

# 파라미터 컬럼 순서 (API 응답의 키 중 lotcount, wfCount 제외한 pt1h 파라미터)
PARA_COLUMNS = [
    "VTH", "IDSAT", "IDLIN", "IOFF", "ION", "IGATE", "IDDQ",
    "VMIN", "FMAX", "TPD", "GM_MAX", "SS", "DIBL",
    "RON", "RDS_ON", "BVDS",
    "LEAK_ID", "LEAK_IG",
    "CGB", "CGS", "CGD", "CDS",
    "RSH", "RD", "RS",
]


@observe(name="fetch_weekly_data")
@timed
def _fetch_weekly_data(lotcd: str, date_str: str) -> dict | None:
    """FastAPI /pt1h/weekly 엔드포인트에서 1주치 데이터 조회

    Args:
        lotcd: 제품코드 (예: "4SS")
        date_str: YYYYMMDD 형식 날짜

    Returns:
        dict: API 응답 (lotcount, wfCount, VTH, IDSAT, ...) 또는 None
    """
    url = f"{API_BASE_URL}/pt1h/weekly"
    params = {
        "lotcd": lotcd,
        "unit": "weekly",
        "process": "pt1h",
        "date": date_str,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        print(f"[ERROR] API 호출 실패 ({date_str}): {e}")
        return None


@observe(name="fetch_4_weeks")
@timed
def _fetch_4_weeks(lotcd: str, ref_date: date) -> list[dict]:
    """기준 날짜로부터 최근 4주 데이터를 가져옴 (오래된 순)

    Returns:
        list[dict]: 각 dict에 'week' 키가 추가된 API 응답 리스트
    """
    mondays = _get_4_week_dates(ref_date)
    results = []

    for monday in mondays:
        date_str = monday.strftime("%Y%m%d")
        week_str = _iso_week_str(monday)
        print(f"  [API] {week_str} ({date_str}) 조회 중...")

        data = _fetch_weekly_data(lotcd, date_str)
        if data:
            data["week"] = week_str
            results.append(data)
        else:
            # 실패 시 빈 행
            results.append({"week": week_str, "lotcount": "-", "wfCount": "-"})

    return results


# ============================================================
# 5. 테이블 생성 함수
# ============================================================
def _build_table(weeks_data: list[dict], lotcd: str) -> str:
    """4주치 데이터를 테이블 문자열로 변환

    행: 주차 (WEEK)
    열: LOT, WF, VTH, IDSAT, ... (파라미터명)

    Args:
        weeks_data: _fetch_4_weeks 반환값
        lotcd: 제품코드

    Returns:
        str: 포맷된 테이블 문자열
    """
    # 헤더: WEEK | LOT | WF | VTH | IDSAT | ...
    headers = ["WEEK", "LOT", "WF"] + PARA_COLUMNS

    rows = []
    for wd in weeks_data:
        week = wd.get("week", "?")
        lotcount = wd.get("lotcount", "-")
        wf_count = wd.get("wfCount", "-")

        row = [week, lotcount, wf_count]
        for col in PARA_COLUMNS:
            val = wd.get(col, "-")
            row.append(val)
        rows.append(row)

    title = f"\n[{lotcd}] Weekly pt1h Metrics (최근 4주)\n"
    table = tabulate(rows, headers=headers, tablefmt="grid", numalign="right", stralign="center")

    return title + table


# ============================================================
# 5.1 파라미터 극성 정의
# ============================================================
# 값이 높아지면 개선인 파라미터 (2개)
HIGHER_IS_BETTER = {"VTH", "IDSAT"}
# 그 외 나머지 23개: 값이 낮아지면 개선


# ============================================================
# 5.2 LLM 분석 함수
# ============================================================
ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율(yield) 분석 전문가입니다.
주어진 주차별 pt1h 파라미터 데이터를 분석하여 인사이트를 제공합니다.
항상 한국어로 답변하세요."""

ANALYSIS_USER_PROMPT = """아래는 [{lotcd}] 제품의 최근 4주 pt1h 파라미터 데이터입니다.

{table}

=== 파라미터 극성 규칙 ===
- VTH, IDSAT: 값이 높아지면 개선 (↑ = 개선)
- 그 외 모든 파라미터: 값이 낮아지면 개선 (↓ = 개선)

=== 비교 대상 ===
- 직전주: {prev_week}
- 최신주: {curr_week}

위 두 주차를 비교하여 다음을 분석해주세요:

1. **가장 열화된 파라미터 Top 3** (파라미터명, 직전주 값, 최신주 값, 변화율%)
2. **가장 개선된 파라미터 Top 3** (파라미터명, 직전주 값, 최신주 값, 변화율%)
3. **전반적인 트렌드 요약** (1~2문장)

마크다운 표 형식으로 깔끔하게 정리해주세요."""


@observe(name="analyze_with_llm")
@timed
def _analyze_with_llm(weeks_data: list[dict], table_str: str, lotcd: str, llm) -> str:
    """최근 2주 데이터를 LLM에게 전달하여 열화/개선 분석을 받는 헬퍼 함수

    Args:
        weeks_data: 4주치 데이터 리스트 (오래된 순)
        table_str: 이미 생성된 테이블 문자열
        lotcd: 제품코드
        llm: ChatOpenAI 인스턴스

    Returns:
        str: LLM 분석 결과 (마크다운)
    """
    if len(weeks_data) < 2:
        return "분석에 필요한 2주 이상의 데이터가 부족합니다."

    prev_week = weeks_data[-2]
    curr_week = weeks_data[-1]

    user_prompt = ANALYSIS_USER_PROMPT.format(
        lotcd=lotcd,
        table=table_str,
        prev_week=prev_week.get("week", "?"),
        curr_week=curr_week.get("week", "?"),
    )

    try:
        response = llm.invoke([
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        return response.content
    except Exception as e:
        print(f"[ERROR] LLM 분석 실패: {e}")
        return f"LLM 분석 중 오류가 발생했습니다: {e}"


# ============================================================
# 6. LLM 모델 설정
# ============================================================
model = ChatOpenAI(
    model="gpt-oss-120b",
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
    request_timeout=180,
)


# ============================================================
# 7. Supervisor 노드 구현
# ============================================================
SUPERVISOR_SYSTEM_PROMPT = """You are a supervisor managing semiconductor yield data queries and WADS degradation reports.

Your job is to:
1. Analyze the user's request
2. Extract parameters (time reference, product code)
3. Route to the appropriate agent

TODAY's DATE: {today}

AVAILABLE AGENTS:
- yield_agent: Fetches weekly pt1h parametric test data and displays it in a table
- wads_agent: Fetches WADS (Weekly Aggregation Data System) degradation detection reports from Oracle DB

=== ROUTING RULES ===

**Route to yield_agent** when:
- User asks about 수율 (yield), pt1h data, weekly metrics
- Examples: "오늘 4SS 수율 알려줘", "저번주 수율 보여줘"

**Route to wads_agent** when:
- User responds "보여줘", "응", "네", "좋아", "부탁해" to a suggestion about 열화 Parameter
- User asks about 열화 (degradation) detection, 검출 리포트, WADS 리포트
- Examples: "보여줘", "열화 검출 리포트 보여줘", "1월 20일 검출 list 보여줘"

=== TIME REFERENCE (시간 표현) ===
Convert the user's natural language time reference to a concrete date.
Use TODAY's DATE above as the base for all relative calculations.

For yield_agent: output as YYYYMMDD format
- "오늘", "금일", "today" → today's date
- "이번주", "이번 주", "this week" → this week's Monday
- "저번주", "지난주", "지난 주", "last week" → last week's Monday
- "2주전", "2주 전" → 2 weeks ago Monday
- "N주전", "N주 전" → N weeks ago Monday
- Specific date like "1월 20일" → that date in YYYYMMDD
- If no time reference is given, default to today

For wads_agent: output as YYYY-MM-DD format in "wads_end_tm" field
- "보여줘", "응", "네" (simple confirmation with no date) → today's date
- "1월 20일 검출 보여줘" → "2026-01-20"
- "저번주 열화 리포트" → last week's Monday date
- If no date specified, default to today

=== PRODUCT CODE (제품코드) ===
- Look for product codes like "4SS" in the user's query
- Default: "4SS" (if not specified)
- IMPORTANT: If the user is responding to a previous yield query (e.g. "보여줘"), keep the same lotcd from context

Respond in JSON format:
{{
    "next_agent": "yield_agent" or "wads_agent" or "END",
    "lotcd": "4SS",
    "ref_date": "YYYYMMDD",
    "wads_end_tm": "YYYY-MM-DD",
    "message": "한국어로 사용자에게 전달할 메시지"
}}

Examples:
- "오늘 4SS 수율 알려줘" → {{"next_agent": "yield_agent", "lotcd": "4SS", "ref_date": "{today_yyyymmdd}", "wads_end_tm": "", "message": "4SS 금주 포함 최근 4주 수율 데이터를 조회합니다."}}
- "저번주 수율 알려줘" → {{"next_agent": "yield_agent", "lotcd": "4SS", "ref_date": "<last monday YYYYMMDD>", "wads_end_tm": "", "message": "지난주 포함 최근 4주 수율 데이터를 조회합니다."}}
- "보여줘" (after yield result) → {{"next_agent": "wads_agent", "lotcd": "4SS", "ref_date": "", "wads_end_tm": "{today_yyyy_mm_dd}", "message": "오늘 검출된 열화 Parameter 리포트를 조회합니다."}}
- "1월 20일 검출 list 보여줘" → {{"next_agent": "wads_agent", "lotcd": "4SS", "ref_date": "", "wads_end_tm": "2026-01-20", "message": "2026년 1월 20일 검출 열화 리포트를 조회합니다."}}

If the request is NOT about yield or WADS data, set next_agent to "END" and provide a helpful message.
Always respond in Korean for the message field.
"""


@observe(name="supervisor_node")
@timed
def supervisor_node(state: YieldQueryState) -> Command:
    """Supervisor 노드: 사용자 요청을 분석하고 적절한 agent로 라우팅

    LLM이 사용자 요청에서 시간 표현과 제품코드를 추출하고 다음 agent를 결정합니다.
    """
    messages = state.get("messages", [])

    # 오늘 날짜를 프롬프트에 주입
    today = date.today()
    today_str = today.strftime("%Y년 %m월 %d일 (%A)")
    today_yyyymmdd = today.strftime("%Y%m%d")

    today_yyyy_mm_dd = today.strftime("%Y-%m-%d")

    prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        today=today_str,
        today_yyyymmdd=today_yyyymmdd,
        today_yyyy_mm_dd=today_yyyy_mm_dd,
    )

    # LLM 호출
    response = model.invoke([
        {"role": "system", "content": prompt},
        *messages
    ])

    # JSON 파싱
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, IndexError):
        parsed = {
            "next_agent": "END",
            "message": "요청을 이해하지 못했습니다. 수율 관련 질문을 해주세요.",
        }

    next_agent = parsed.get("next_agent", "END")

    # 결과 메시지 생성
    result_message = AIMessage(
        content=parsed.get("message", "처리 중입니다..."),
        name="supervisor",
    )

    # lotcd: 이전 State에 값이 있으면 유지 (follow-up 대화에서 중요)
    prev_lotcd = state.get("lotcd", "")
    new_lotcd = parsed.get("lotcd", "") or prev_lotcd or "4SS"

    # State 업데이트
    update_data = {
        "messages": [result_message],
        "lotcd": new_lotcd,
        "ref_date": parsed.get("ref_date", today_yyyymmdd),
        "wads_end_tm": parsed.get("wads_end_tm", ""),
        "next_agent": next_agent,
    }

    # 파라미터 로깅
    print("=" * 60)
    print("[Supervisor] 파싱 결과:")
    print(f"  - lotcd: {update_data['lotcd']}")
    print(f"  - ref_date: {update_data['ref_date']}")
    print(f"  - wads_end_tm: {update_data['wads_end_tm']}")
    print(f"  - next_agent: {next_agent}")
    print(f"  - message: {parsed.get('message', '')}")
    print("=" * 60)

    # 다음 노드로 라우팅
    if next_agent == "END":
        return Command(goto=END, update=update_data)
    else:
        return Command(goto=next_agent, update=update_data)


# ============================================================
# 8. Yield Agent 노드 구현
# ============================================================
@observe(name="yield_agent_node")
@timed
def yield_agent_node(state: YieldQueryState) -> Command:
    """Yield Agent 노드: State에서 파라미터를 읽어 API 호출 + 테이블 생성

    Supervisor가 추출한 lotcd, ref_date를 사용하여
    최근 4주 데이터를 조회하고 테이블로 포맷합니다.
    """
    # State에서 파라미터 읽기
    lotcd = state.get("lotcd", "4SS")
    ref_date_str = state.get("ref_date", date.today().strftime("%Y%m%d"))

    # 날짜 파싱
    try:
        ref_date = datetime.strptime(ref_date_str, "%Y%m%d").date()
    except ValueError:
        ref_date = date.today()

    # 파라미터 로깅
    print("=" * 60)
    print("[Yield Agent] State에서 파라미터 읽기:")
    print(f"  - lotcd: {lotcd}")
    print(f"  - ref_date: {ref_date_str} ({_iso_week_str(ref_date)})")
    print("=" * 60)

    # FastAPI에서 4주치 데이터 조회
    print(f"\n[Yield Agent] {lotcd} 최근 4주 데이터 조회 시작...")
    weeks_data = _fetch_4_weeks(lotcd, ref_date)

    if not weeks_data or all(wd.get("lotcount") == "-" for wd in weeks_data):
        error_message = AIMessage(
            content="데이터를 조회할 수 없습니다. FastAPI 서버가 실행 중인지 확인해주세요.",
            name="yield_agent",
        )
        return Command(
            goto=END,
            update={"messages": [error_message], "weeks_data": [], "table_result": ""},
        )

    # 테이블 생성
    table_str = _build_table(weeks_data, lotcd)
    print(table_str)

    # LLM 분석 (최근 2주 비교)
    print(f"\n[Yield Agent] LLM 분석 시작 (최근 2주 비교)...")
    analysis = _analyze_with_llm(weeks_data, table_str, lotcd, model)
    print(f"\n[LLM 분석 결과]\n{analysis}")

    # 결과 메시지 생성
    result_msg = f"[{lotcd}] 최근 4주 pt1h 수율 데이터입니다.\n"
    result_msg += f"기준: {_iso_week_str(ref_date)} ({ref_date_str})\n\n"
    result_msg += table_str
    result_msg += f"\n\n---\n\n{analysis}"
    result_msg += "\n\n---\n\n> 오늘 검출된 열화 Parameter를 보여드릴까요?"

    result_message = AIMessage(content=result_msg, name="yield_agent")

    return Command(
        goto=END,
        update={
            "messages": [result_message],
            "weeks_data": weeks_data,
            "table_result": table_str,
            "analysis_result": analysis,
        },
    )


# ============================================================
# 8.1 WADS Agent 노드 구현
# ============================================================
# wads_agent.py를 같은 폴더에서 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wads_agent import (
    _create_wads_agent,
    _TOOL_PAYLOAD_STORAGE,
    _render_wads_report_html,
    _render_wads_query_html,
)


@observe(name="wads_agent_node")
@timed
def wads_agent_node(state: YieldQueryState) -> Command:
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

    # 도구 결과 저장소 초기화
    _TOOL_PAYLOAD_STORAGE.clear()
    _TOOL_PAYLOAD_STORAGE["reports"] = []

    # WADS 서브에이전트 생성 및 호출
    agent = _create_wads_agent()
    query = f"{lotcd} 로트의 {end_tm} 열화 검출 리포트를 보여줘"
    print(f"[WADS Agent] 쿼리: {query}")

    try:
        result = agent.invoke({"messages": [HumanMessage(content=query)]})
    except Exception as e:
        print(f"[ERROR] WADS Agent 실행 실패: {e}")
        error_message = AIMessage(
            content=f"WADS 리포트 조회 중 오류가 발생했습니다: {e}",
            name="wads_agent",
        )
        return Command(
            goto=END,
            update={"messages": [error_message], "wads_artifacts": []},
        )

    # 결과에서 마지막 AI 메시지 추출
    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    # _TOOL_PAYLOAD_STORAGE에서 실제 데이터 추출
    query_payload = _TOOL_PAYLOAD_STORAGE.get("query")
    reports_payload = _TOOL_PAYLOAD_STORAGE.get("reports", [])

    print(f"[WADS Agent] query_payload: {query_payload is not None}")
    print(f"[WADS Agent] reports_payload count: {len(reports_payload)}")

    # HTML 아티팩트 생성
    artifacts = []
    if reports_payload:
        html = _render_wads_report_html(reports_payload)
        artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": html,
            "title": "wads_report",
        })
    elif query_payload:
        html = _render_wads_query_html(query_payload)
        artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": html,
            "title": "wads_query",
        })

    # 도구 결과 저장소 정리
    _TOOL_PAYLOAD_STORAGE.clear()
    _TOOL_PAYLOAD_STORAGE["reports"] = []

    result_message = AIMessage(content=answer, name="wads_agent")

    return Command(
        goto=END,
        update={
            "messages": [result_message],
            "wads_artifacts": artifacts,
        },
    )


# ============================================================
# 9. StateGraph 구성
# ============================================================
workflow = StateGraph(YieldQueryState)

# 노드 추가
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("yield_agent", yield_agent_node)
workflow.add_node("wads_agent", wads_agent_node)

# 엣지 정의
workflow.add_edge(START, "supervisor")
# supervisor의 조건부 엣지는 Command가 처리하므로 별도 정의 불필요

# 컴파일
yield_supervisor = workflow.compile()


# ============================================================
# 10. 테스트 실행
# ============================================================
def run_test(test_name: str, user_message: str):
    """테스트 실행 헬퍼 함수

    StateGraph 구조에서 State를 통해 데이터가 공유됩니다.
    """
    print(f"\n{'='*80}")
    print(f"{test_name}")
    print(f"{'='*80}")
    print(f"User: {user_message}\n")

    # 초기 State 설정
    initial_state = {
        "messages": [HumanMessage(content=user_message)],
        "lotcd": "",
        "ref_date": "",
        "weeks_data": [],
        "table_result": "",
        "analysis_result": "",
        "wads_end_tm": "",
        "wads_artifacts": [],
        "next_agent": "",
    }

    # invoke로 실행하고 최종 State 받기 (Langfuse 콜백 포함)
    final_state = yield_supervisor.invoke(
        initial_state,
        config={"callbacks": [langfuse_handler]},
    )

    # 메시지 출력
    if final_state.get("messages"):
        for msg in final_state["messages"]:
            if hasattr(msg, "name") and msg.name:
                print(f"[{msg.name}]: {msg.content}\n")
            elif hasattr(msg, "content"):
                print(f"{msg.content}\n")

    # 최종 State 요약 출력
    print("[최종 State 요약]")
    print(f"  - lotcd: {final_state.get('lotcd', '')}")
    print(f"  - ref_date: {final_state.get('ref_date', '')}")
    if final_state.get("weeks_data"):
        weeks = [wd.get("week", "?") for wd in final_state["weeks_data"]]
        print(f"  - 조회 주차: {', '.join(weeks)}")
    if final_state.get("table_result"):
        print(f"  - 테이블 생성: OK")
    if final_state.get("analysis_result"):
        print(f"  - LLM 분석: OK")
    if final_state.get("wads_end_tm"):
        print(f"  - wads_end_tm: {final_state['wads_end_tm']}")
    if final_state.get("wads_artifacts"):
        print(f"  - WADS 아티팩트: {len(final_state['wads_artifacts'])}개")


if __name__ == "__main__":
    print("Yield Query Agent 시작 (StateGraph 기반)")
    print("=" * 80)
    print("Agent들이 YieldQueryState를 통해 구조화된 데이터를 공유합니다.")
    print(f"오늘 날짜: {date.today().strftime('%Y-%m-%d')}")
    print(f"API 서버: {API_BASE_URL}")
    print("=" * 80)

    # 테스트 1: 오늘 기준 4SS 수율
    run_test(
        "테스트 1: 오늘 4SS 수율",
        "오늘 4SS 수율 알려줘",
    )

    # 테스트 2: 저번주 수율
    run_test(
        "테스트 2: 저번주 수율",
        "저번주 수율 알려줘",
    )

    # 테스트 3: 2주전 수율
    run_test(
        "테스트 3: 2주전 수율",
        "2주전 4SS 수율 보여줘",
    )

    # Langfuse 트레이스 전송 보장
    langfuse_handler.flush()
    logger.info("Langfuse 트레이스 전송 완료")
