"""
공통 유틸리티 — 모든 에이전트에서 공유
========================================
- Oracle 연결 팩토리
- 날짜 유틸리티
- 공유 상수 (PARA_COLUMNS, BIN_CATEGORY_MAP 등)
- timed 데코레이터
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from datetime import date, timedelta
from typing import Any, Type

import httpx
from pydantic import BaseModel

import oracledb
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.common")


def to_user_message(exc: Exception) -> str:
    """기술적 예외를 사용자 친화적 한국어 메시지로 변환."""
    msg = str(exc).lower()
    if (
        isinstance(exc, (oracledb.DatabaseError, oracledb.OperationalError))
        or "ora-" in msg
    ):
        return "DB 서버 연결에 실패했습니다. 네트워크/VPN 상태를 확인하세요."
    if (
        isinstance(exc, (ConnectionError, TimeoutError, OSError))
        or "connection refused" in msg
        or "timed out" in msg
    ):
        if "9200" in msg or "opensearch" in msg or "elasticsearch" in msg:
            return (
                "이력 검색 서비스에 일시적 문제가 있습니다. 잠시 후 다시 시도해 주세요."
            )
        return "서버 연결에 실패했습니다. 네트워크 상태를 확인하거나 잠시 후 다시 시도해 주세요."
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code == 429:
            return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        if 500 <= exc.response.status_code < 600:
            return "LLM 서비스에 일시적 문제가 있습니다. 잠시 후 다시 시도해 주세요."
    if "json" in msg and ("parse" in msg or "decode" in msg):
        return "응답 파싱에 실패했습니다. 다시 시도해 주세요."
    return f"오류가 발생했습니다: {exc}"


def is_transient_error(exc: Exception) -> bool:
    """LangGraph RetryPolicy가 재시도해야 할 일시 장애 분류.

    OpenRouter LLM 호출과 Oracle/OpenSearch 연결에서 흔한 socket/timeout 및
    HTTP 5xx를 transient로 표시. ValueError·TypeError 등 코드 버그는 False.
    supervisor RetryPolicy(retry_on=...)와 worker try/except 양쪽에서 공유한다.
    """
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return 500 <= exc.response.status_code < 600
    return False


# ============================================================
# Oracle 연결 설정
# ============================================================
ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

_pool: oracledb.ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_oracle_pool() -> oracledb.ConnectionPool:
    """Oracle 커넥션 풀 싱글턴 (lazy init, thin-mode, thread-safe)"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # double-checked locking
                _pool = oracledb.create_pool(
                    user=ORACLE_USER,
                    password=ORACLE_PASSWORD,
                    dsn=ORACLE_DSN,
                    min=2,
                    max=10,
                    increment=1,
                )
                logger.info("Oracle 커넥션 풀 생성 (min=2, max=10)")
    return _pool


def get_oracle_connection() -> oracledb.Connection:
    """풀에서 커넥션 획득 (conn.close() 시 풀에 반환)"""
    pool = _get_oracle_pool()
    logger.info(
        "Oracle 커넥션 획득 시도 (pool: busy=%d, open=%d, max=%d)",
        pool.busy,
        pool.opened,
        pool.max,
    )
    conn = pool.acquire()
    logger.info("Oracle 커넥션 획득 완료")
    return conn


# ============================================================
# 날짜 유틸리티
# ============================================================
def iso_week_str(d: date) -> str:
    """date → ISO 주차 문자열 (예: 2026-W06)"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(d: date) -> date:
    """date가 속한 주의 월요일 반환"""
    return d - timedelta(days=d.isoweekday() - 1)


def get_4_week_dates(ref: date) -> list[date]:
    """기준 날짜가 속한 주부터 과거 4주의 월요일 리스트 (오래된순)

    예: ref가 2026-W06이면 → [W03 월요일, W04 월요일, W05 월요일, W06 월요일]
    """
    monday = week_monday(ref)
    return [monday - timedelta(weeks=i) for i in range(3, -1, -1)]


# ============================================================
# 공유 상수
# ============================================================

# 파라미터 컬럼 순서 (pt1h/pt1c PARAM 컬럼 값)
PARA_COLUMNS: list[str] = [
    "VTH",
    "IDSAT",
    "IDLIN",
    "IOFF",
    "ION",
    "IGATE",
    "IDDQ",
    "VMIN",
    "FMAX",
    "TPD",
    "GM_MAX",
    "SS",
    "DIBL",
    "RON",
    "RDS_ON",
    "BVDS",
    "LEAK_ID",
    "LEAK_IG",
    "CGB",
    "CGS",
    "CGD",
    "CDS",
    "RSH",
    "RD",
    "RS",
]

PT1C_COLUMNS: list[str] = ["PT1C", "CFTA"]

GMS_COLUMNS: list[str] = ["cum0", "cum2", "fab", "prb", "pnt"]

GMS_HIGHER_IS_BETTER: set[str] = set(GMS_COLUMNS)  # gms는 전부 높을수록 좋음

HIGHER_IS_BETTER: set[str] = {"VTH", "IDSAT", "PT1C", "CFTA"}

# bin_value ↔ bin_category 매핑
# A = Pass(양품), B~Z = 25개 PARA_COLUMNS에 1:1 대응하는 fail bin
BIN_CATEGORY_MAP: dict[str, str] = {
    "A": "PASS",
    "B": "VTH",
    "C": "IDSAT",
    "D": "IDLIN",
    "E": "ION",
    "F": "IGATE",
    "G": "IDDQ",
    "H": "IOFF",
    "I": "VMIN",
    "J": "FMAX",
    "K": "TPD",
    "L": "GM_MAX",
    "M": "SS",
    "N": "DIBL",
    "O": "RON",
    "P": "RDS_ON",
    "Q": "BVDS",
    "R": "LEAK_ID",
    "S": "LEAK_IG",
    "T": "CGB",
    "U": "CGS",
    "V": "CGD",
    "W": "CDS",
    "X": "RSH",
    "Y": "RD",
    "Z": "RS",
}

CATEGORY_TO_BIN: dict[str, str] = {
    v: k for k, v in BIN_CATEGORY_MAP.items() if v != "PASS"
}


# ============================================================
# timed 데코레이터
# ============================================================
def get_llm(model: str | None = None, temperature: float = 0) -> "ChatOpenAI":
    """LLM 팩토리 — 모든 에이전트에서 동일 설정으로 ChatOpenAI 생성"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model="z-ai/glm-5.1",
        base_url=os.getenv("OPENROUTER_BASE_URL"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=temperature,
    )


def stream_event(kind: str, event) -> None:
    """LangGraph stream_mode='custom' 채널로 이벤트 전송.

    get_stream_writer()는 LangGraph 노드 실행 컨텍스트에서 자동 바인딩됩니다.
    CLI/테스트 등 스트리밍 컨텍스트 없이 실행될 경우 무시합니다.
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        payload = event.model_dump() if hasattr(event, "model_dump") else event
        writer({"kind": kind, **payload})
    except Exception:
        pass  # 스트리밍 실패가 메인 로직을 중단하면 안 됨


def html_escape(s: Any) -> str:
    """HTML 특수문자 이스케이프"""
    txt = "" if s is None else str(s)
    return (
        txt.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def extract_suggestion(text: str) -> tuple[str, str]:
    """[SUGGESTION: ...] 패턴을 추출하고 본문에서 제거.

    Returns:
        (cleaned_text, suggestion)
    """
    match = re.search(r"\[SUGGESTION:\s*(.*?)\]", text)
    suggestion = match.group(1).strip() if match else ""
    cleaned = re.sub(r"\[SUGGESTION:.*?\]", "", text).strip()
    return cleaned, suggestion


def extract_json_from_llm(raw_text: str, model_class: Type[BaseModel]) -> BaseModel:
    """LLM 응답에서 <think> 태그 제거 후 JSON 추출 → Pydantic 모델 파싱.

    Raises:
        ValueError: JSON을 찾을 수 없는 경우
    """
    from json_repair import repair_json

    # <think> 태그 제거 (닫는 태그 없는 경우도 처리)
    clean = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    clean = re.sub(r"<think>.*", "", clean, flags=re.DOTALL).strip()

    # JSON 블록 추출 (```json ... ``` 또는 { ... })
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\{.*\})", clean, re.DOTALL)
    # clean에서 못 찾으면 raw_text 전체에서 시도 (think 안에 JSON이 있는 경우)
    if not json_match:
        json_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if not json_match:
        raise ValueError(
            f"No JSON found in LLM response (len={len(raw_text)}): {raw_text[:300]}"
        )

    data = json.loads(repair_json(json_match.group(1)))
    return model_class(**data)


def lot_id_variants(lot_id: str) -> list[str]:
    """첫 글자 4↔T 변환하여 [원본, 변환본] 반환.

    반도체 lot ID가 테이블에 따라 4 또는 T로 저장되므로 양방향 조회 필요.
    """
    if not lot_id:
        return [lot_id]
    first = lot_id[0]
    if first == "4":
        return [lot_id, "T" + lot_id[1:]]
    elif first == "T":
        return [lot_id, "4" + lot_id[1:]]
    return [lot_id]


def timed(func):
    """함수 실행 시간을 측정하는 데코레이터"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.info("▶ %s 시작", func.__name__)
        result = func(*args, **kwargs)
        logger.info("◀ %s 완료 (%.2fs)", func.__name__, time.time() - start)
        return result

    return wrapper
