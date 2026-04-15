"""세션 시작 시 데이터를 미리 로드해 세션 네임스페이스를 만든다.

`POST /repl/session` 이 이 모듈의 `create_session` 을 호출해 df 를 적재하고,
이후 `run_python` 툴이 같은 세션(thread_id) 에 대해 `get_namespace(session_id)` 로
동일한 dict 를 계속 사용한다. 변수는 턴 간 유지된다.

DATA_API_BASE_URL 이 설정되면 httpx 로 외부 API 를 탄다 — 없으면 같은 프로세스의
mock 라우터 함수를 직접 호출한다. 둘 다 반환 스키마는 `{rows, query}`.

추가로: P1 개선 — 세션 생성 시 df.shape/dtypes/head 요약을 문자열로 만들어 ns 에
`__summary__` 로 저장한다. router.chat 이 첫 턴에만 이 요약을 HumanMessage 에
prefix 로 넣어 LLM 이 컬럼/dtype 탐색 tool call 을 건너뛰게 한다.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

import httpx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import scipy  # noqa: F401 — run_python 네임스페이스 노출용
import statsmodels.api as sm

from .mock.routes import get_data as _mock_get_data

_DATA_BASE = os.environ.get("DATA_API_BASE_URL")

_SESSIONS: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _base_namespace() -> dict[str, Any]:
    """매 세션 새로 만들어지는 베이스 네임스페이스 — 라이브러리 이름들만."""
    return {
        "pd": pd,
        "np": np,
        "px": px,
        "go": go,
        "sm": sm,
    }


def _build_session_summary(
    df: pd.DataFrame,
    query: dict[str, str],
    column_guideline: str | None,
) -> str:
    """LLM 첫 턴에 프리픽스로 넣을 세션 요약 텍스트를 만든다.

    reference `05-Data-Analysis-Agent/dataanalysis.py:build_system_prompt` 의
    `df.head().to_string()` 주입 패턴을 차용. df 가 비어있는 경우도 대비.
    """
    head_str = "(empty)" if df.empty else df.head().to_string()
    dtype_str = "(empty)" if df.empty else df.dtypes.to_string()
    lines: list[str] = [
        "[세션 데이터]",
        f"- LOTCD: {query['lotcd']}, 기간: {query['start']} ~ {query['end']}, fail_name: {query['fail_name']}",
        f"- df.shape: {df.shape}",
        "- dtypes:",
        dtype_str,
        "- df.head():",
        head_str,
        "",
        "[스키마 해석]",
        "- `df` 는 wafer × process long-format. 각 행 = (lotcd, lot_id, wf_id, oper, legend).",
        "- wafer 메타(fail_name/fail_value/end_tm) 는 같은 wafer 의 모든 row 에 중복 저장되어 있다.",
        "- wafer 단위 분석 시: `wafer_df = df.drop_duplicates(['lot_id','wf_id'])` 로 먼저 unique 화.",
        "- `legend_value` 는 범주형 (예: Equip1~4). 숫자 변환 금지.",
        "- `end_tm` 은 'YYYY-MM-DD HH:MM:SS' 문자열 — 시계열 분석 시 `pd.to_datetime(df['end_tm'])` 변환.",
        "",
        "[주의] 위 df 는 이미 세션에 로드되어 있음. 매 턴 다시 불러오지 말 것.",
        "CSV 로부터 로드된 숫자 컬럼(fail_value 등)은 필요 시 `pd.to_numeric(..., errors='coerce')` 로 변환 후 분석.",
    ]
    if column_guideline and column_guideline.strip():
        lines += [
            "",
            "[컬럼 설명 (사용자 제공)]",
            column_guideline.strip(),
        ]
    return "\n".join(lines)


async def _fetch_rows(
    lotcd: str, start: str, end: str, fail_name: str
) -> list[dict]:
    """LOTCD/기간/fail_name 으로 데이터 행을 가져온다."""
    params = {"lotcd": lotcd, "start": start, "end": end, "fail_name": fail_name}
    if _DATA_BASE:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_DATA_BASE}/mock/data", params=params)
            resp.raise_for_status()
            return resp.json().get("rows", [])
    # in-process mock — 포트/호스트 의존 없음
    return _mock_get_data(
        lotcd=lotcd, start=start, end=end, fail_name=fail_name
    ).get("rows", [])


async def create_session(
    lotcd: str,
    start: str,
    end: str,
    fail_name: str,
    column_guideline: str | None = None,
) -> dict[str, Any]:
    """데이터를 한 번 불러 세션에 저장. chat 에서 쓸 session_id 와 요약을 리턴.

    Args:
        lotcd, start, end, fail_name: 데이터 필터 조건.
        column_guideline: 사용자 제공 컬럼 설명(선택). 첫 턴 프롬프트 프리픽스에 포함.
    """
    rows = await _fetch_rows(lotcd, start, end, fail_name)
    df = pd.DataFrame(rows)
    ns = _base_namespace()
    query = {"lotcd": lotcd, "start": start, "end": end, "fail_name": fail_name}
    ns["df"] = df
    ns["query"] = query
    ns["__summary__"] = _build_session_summary(df, query, column_guideline)
    ns["__column_guideline__"] = column_guideline or ""

    session_id = str(uuid.uuid4())
    with _lock:
        _SESSIONS[session_id] = ns

    # 숫자 컬럼이 뭐가 있는지 힌트를 같이 반환 — 프론트에서 첫 메시지로 보여주기 좋다.
    numeric_cols: list[str] = []
    if not df.empty:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

    return {
        "session_id": session_id,
        "rowcount": len(rows),
        "columns": list(df.columns),
        "numeric_columns": numeric_cols,
        "query": query,
        "has_column_guideline": bool(column_guideline and column_guideline.strip()),
    }


def get_namespace(session_id: str) -> dict[str, Any] | None:
    """세션 네임스페이스를 반환 (없으면 None)."""
    with _lock:
        return _SESSIONS.get(session_id)


def get_session_summary(session_id: str) -> str | None:
    """세션의 요약 프리픽스를 반환 (없으면 None).

    router.chat 이 첫 턴에만 HumanMessage 에 이 텍스트를 붙인다.
    """
    ns = get_namespace(session_id)
    if ns is None:
        return None
    return ns.get("__summary__")


def drop_session(session_id: str) -> bool:
    """세션 네임스페이스 해제."""
    with _lock:
        return _SESSIONS.pop(session_id, None) is not None
