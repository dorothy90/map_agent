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


def get_oracle_connection() -> oracledb.Connection:
    """Oracle thin-mode 연결 생성"""
    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN,
    )


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

HIGHER_IS_BETTER: set[str] = {"VTH", "IDSAT"}

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
