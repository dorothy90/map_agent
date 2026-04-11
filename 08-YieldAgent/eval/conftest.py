"""
평가 프레임워크 공통 Fixtures
============================
- frozen_date: 날짜 의존 테스트를 위한 고정 날짜
- load_golden: 골든 데이터셋 JSON 로더
- pytest markers: requires_db, requires_opensearch, slow, multi_turn
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

# 08-YieldAgent 모듈을 임포트할 수 있도록 경로 추가
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


# ── Markers ──────────────────────────────────────────────────
def pytest_configure(config):
    config.addinivalue_line("markers", "requires_db: test needs Oracle DB connection")
    config.addinivalue_line("markers", "requires_opensearch: test needs OpenSearch")
    config.addinivalue_line("markers", "slow: test makes LLM API calls")
    config.addinivalue_line("markers", "multi_turn: multi-turn / interrupt / handoff scenario")


# ── Frozen Date ──────────────────────────────────────────────
FROZEN_DATE = date(2026, 4, 11)


@pytest.fixture(scope="session")
def frozen_date() -> date:
    """평가에서 사용하는 고정 날짜 (2026-04-11)."""
    return FROZEN_DATE


@pytest.fixture(scope="session")
def frozen_date_strs(frozen_date) -> dict[str, str]:
    """SUPERVISOR_SYSTEM_PROMPT 템플릿에 주입할 날짜 문자열들."""
    return {
        "today": frozen_date.strftime("%Y년 %m월 %d일 (%A)"),
        "today_yyyymmdd": frozen_date.strftime("%Y%m%d"),
        "today_yyyy_mm_dd": frozen_date.strftime("%Y-%m-%d"),
    }


# ── Golden Dataset Loader ────────────────────────────────────
DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def load_golden(filename: str) -> list[dict]:
    """datasets/ 폴더에서 골든 데이터셋 JSON 로드."""
    path = DATASETS_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def routing_golden() -> list[dict]:
    return load_golden("routing_golden.json")


@pytest.fixture(scope="session")
def planner_golden() -> list[dict]:
    return load_golden("planner_golden.json")


@pytest.fixture(scope="session")
def rewrite_golden() -> list[dict]:
    return load_golden("rewrite_golden.json")
