"""세션 데이터 요약과 격리 프로세스 런타임의 수명 주기를 관리한다.

`POST /repl/session` 이 이 모듈의 `create_session` 을 호출해 데이터를 적재하고,
실제 DataFrame 및 실행 네임스페이스는 공유 프로세스 런타임이 소유한다.

DATA_API_BASE_URL 이 설정되면 httpx 로 외부 API 를 탄다 — 없으면 같은 프로세스의
mock 라우터 함수를 직접 호출한다. 둘 다 반환 스키마는 `{rows, query}`.

세션 생성 시 df.shape/dtypes/head 요약을 문자열로 만들어 레코드에 저장한다.
router.chat 이 첫 턴에만 이 요약을 HumanMessage 에 prefix 로 넣는다.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx
import pandas as pd

from .mock.routes import get_data as _mock_get_data
from .runtime import runtime as _runtime

_DATA_BASE = os.environ.get("DATA_API_BASE_URL")

SessionStatus = Literal["ready", "running", "runtime_lost"]


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    summary: str
    status: SessionStatus = "ready"
    active_run_id: str | None = None


class SessionStateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _CancellationReservation:
    record: SessionRecord
    completion_status: SessionStatus | None = None


_SESSIONS: dict[str, SessionRecord] = {}
_CANCELLATIONS: dict[str, _CancellationReservation] = {}
_lifecycle_generation = 0
_lock = threading.Lock()


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
    query = {"lotcd": lotcd, "start": start, "end": end, "fail_name": fail_name}
    session_id = str(uuid.uuid4())
    record = SessionRecord(
        session_id=session_id,
        summary=_build_session_summary(df, query, column_guideline),
    )

    with _lock:
        creation_generation = _lifecycle_generation

    _runtime.create_session(session_id, rows, query)
    with _lock:
        publish = creation_generation == _lifecycle_generation
        if publish:
            _SESSIONS[session_id] = record
    if not publish:
        _runtime.close_session(session_id)
        raise SessionStateError(
            "session_closed", "Session creation was interrupted by runtime shutdown"
        )

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


def get_session(session_id: str) -> SessionRecord | None:
    """현재 세션 수명 주기 레코드를 반환한다."""
    with _lock:
        return _SESSIONS.get(session_id)


def get_session_summary(session_id: str) -> str | None:
    """세션의 요약 프리픽스를 반환 (없으면 None).

    router.chat 이 첫 턴에만 HumanMessage 에 이 텍스트를 붙인다.
    """
    record = get_session(session_id)
    if record is None:
        return None
    return record.summary


def begin_run(session_id: str, run_id: str) -> None:
    """준비된 세션을 실행 중으로 원자적으로 전환한다."""
    with _lock:
        record = _SESSIONS.get(session_id)
        if record is None:
            raise SessionStateError(
                "session_not_found", f"Unknown session: {session_id}"
            )
        if record.status == "runtime_lost":
            raise SessionStateError(
                "runtime_lost", f"Session runtime is no longer available: {session_id}"
            )
        if record.status == "running":
            raise SessionStateError(
                "session_busy", f"Session already has an active run: {session_id}"
            )
        _SESSIONS[session_id] = replace(
            record, status="running", active_run_id=run_id
        )


def finish_run(
    session_id: str,
    run_id: str,
    runtime_lost: bool = False,
) -> bool:
    """일치하는 실행만 종료하고 세션을 준비 또는 손실 상태로 전환한다."""
    with _lock:
        record = _SESSIONS.get(session_id)
        if record is None or record.active_run_id != run_id:
            return False
        status: SessionStatus = "runtime_lost" if runtime_lost else "ready"
        reservation = _CANCELLATIONS.get(session_id)
        if reservation is not None and reservation.record is record:
            if reservation.completion_status != "runtime_lost":
                reservation.completion_status = status
            return True
        _SESSIONS[session_id] = replace(
            record, status=status, active_run_id=None
        )
        return True


def mark_runtime_lost(session_id: str, run_id: str) -> bool:
    """일치하는 실행의 런타임을 파괴적 종료 상태로 표시한다."""
    return finish_run(session_id, run_id, runtime_lost=True)


def cancel_run(run_id: str) -> bool:
    """활성 실행을 찾아 런타임을 취소하고 해당 세션을 손실 상태로 만든다."""
    with _lock:
        match = next(
            (
                record
                for record in _SESSIONS.values()
                if record.status == "running" and record.active_run_id == run_id
            ),
            None,
        )
        if match is None or match.session_id in _CANCELLATIONS:
            return False
        reservation = _CancellationReservation(record=match)
        _CANCELLATIONS[match.session_id] = reservation

    try:
        cancelled = _runtime.cancel(match.session_id, run_id)
    except BaseException:
        _resolve_cancellation(reservation, cancelled=False)
        raise
    _resolve_cancellation(reservation, cancelled=cancelled)
    return cancelled


def _resolve_cancellation(
    reservation: _CancellationReservation,
    cancelled: bool,
) -> None:
    """같은 레코드 세대에만 취소 결과 또는 보류된 완료를 반영한다."""
    session_id = reservation.record.session_id
    with _lock:
        if _CANCELLATIONS.get(session_id) is not reservation:
            return
        current = _SESSIONS.get(session_id)
        if current is reservation.record:
            if cancelled:
                _SESSIONS[session_id] = replace(
                    current, status="runtime_lost", active_run_id=None
                )
            elif reservation.completion_status is not None:
                _SESSIONS[session_id] = replace(
                    current,
                    status=reservation.completion_status,
                    active_run_id=None,
                )
        del _CANCELLATIONS[session_id]


def close_session(session_id: str) -> bool:
    """세션 레코드와 런타임을 한 번만 닫는다."""
    with _lock:
        record = _SESSIONS.pop(session_id, None)
        _CANCELLATIONS.pop(session_id, None)
    if record is None:
        return False
    _runtime.close_session(session_id)
    return True


def close_all_sessions() -> None:
    """모든 런타임을 닫고 세션 레코드를 비운다."""
    global _lifecycle_generation
    with _lock:
        _lifecycle_generation += 1
        _SESSIONS.clear()
        _CANCELLATIONS.clear()
    _runtime.close_all()
