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
import logging
import os
import threading
import time
from datetime import date, timedelta

import oracledb
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.common")

# ============================================================
# Oracle 연결 설정
# ============================================================
ORACLE_USER     = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN      = os.getenv("ORACLE_DSN")

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
    logger.info("Oracle 커넥션 획득 시도 (pool: busy=%d, open=%d, max=%d)", pool.busy, pool.opened, pool.max)
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
    "VTH", "IDSAT", "IDLIN", "IOFF", "ION", "IGATE", "IDDQ",
    "VMIN", "FMAX", "TPD", "GM_MAX", "SS", "DIBL",
    "RON", "RDS_ON", "BVDS",
    "LEAK_ID", "LEAK_IG",
    "CGB", "CGS", "CGD", "CDS",
    "RSH", "RD", "RS",
]

PT1C_COLUMNS: list[str] = ["PT1C", "CFTA"]

GMS_COLUMNS: list[str] = ["cum0", "cum2", "fab", "prb", "pnt"]

GMS_HIGHER_IS_BETTER: set[str] = set(GMS_COLUMNS)  # gms는 전부 높을수록 좋음

HIGHER_IS_BETTER: set[str] = {"VTH", "IDSAT", "PT1C", "CFTA"}

# bin_value ↔ bin_category 매핑
# A = Pass(양품), B~Z = 25개 PARA_COLUMNS에 1:1 대응하는 fail bin
BIN_CATEGORY_MAP: dict[str, str] = {
    "A": "PASS",
    "B": "VTH",    "C": "IDSAT",   "D": "IDLIN",  "E": "ION",
    "F": "IGATE",  "G": "IDDQ",    "H": "IOFF",   "I": "VMIN",
    "J": "FMAX",   "K": "TPD",     "L": "GM_MAX", "M": "SS",
    "N": "DIBL",   "O": "RON",     "P": "RDS_ON", "Q": "BVDS",
    "R": "LEAK_ID","S": "LEAK_IG", "T": "CGB",    "U": "CGS",
    "V": "CGD",    "W": "CDS",     "X": "RSH",    "Y": "RD",
    "Z": "RS",
}

CATEGORY_TO_BIN: dict[str, str] = {v: k for k, v in BIN_CATEGORY_MAP.items() if v != "PASS"}


# ============================================================
# timed 데코레이터
# ============================================================
def get_llm(model: str | None = None, temperature: float = 0) -> "ChatOpenAI":
    """LLM 팩토리 — 모든 에이전트에서 동일 설정으로 ChatOpenAI 생성"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model or os.getenv("DEFAULT_MODEL", "gpt-oss-120b"),
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


def emit_sse(config: dict | None, kind: str, event) -> None:
    """[DEPRECATED] stream_event()로 대체됨. 하위 호환용 래퍼."""
    stream_event(kind, event)


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
