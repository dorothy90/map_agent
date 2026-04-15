"""Mock 라우터 필터링 동작 검증 (LLM 불필요).

스키마: lotcd, lot_id, wf_id, fail_name, fail_value, oper, legend, legend_value, end_tm
- LOTCD / fail_name / end_tm 범위 조합으로 올바른 행만 돌려주는지
- 없는 LOTCD 는 빈 배열
- 필수 파라미터 누락 시 FastAPI 가 422 로 거절
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from repl_agent.router import router as repl_router

EXPECTED_COLUMNS = {
    "lotcd",
    "lot_id",
    "wf_id",
    "fail_name",
    "fail_value",
    "oper",
    "legend",
    "legend_value",
    "end_tm",
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(repl_router, prefix="/repl")
    return TestClient(app)


def test_health():
    c = _client()
    r = c.get("/repl/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "repl_agent"}


def test_mock_filter_by_lotcd_and_fail():
    """L12345 + OPEN 으로 필터 시 모든 행이 조건 매칭되고 스키마가 새 9 컬럼인지."""
    c = _client()
    r = c.get(
        "/repl/mock/data",
        params={
            "lotcd": "L12345",
            "start": "2026-02-01",
            "end": "2026-04-30",
            "fail_name": "OPEN",
        },
    )
    assert r.status_code == 200
    data = r.json()
    rows = data["rows"]
    # sample.csv 는 seed=42 고정 → L12345 OPEN 이 최소 1 wafer 는 있어야 함 (2000 rows)
    assert len(rows) > 0, "L12345+OPEN 필터 결과가 비어있음"
    assert data["query"]["lotcd"] == "L12345"
    for row in rows:
        assert row["lotcd"] == "L12345"
        assert row["fail_name"] == "OPEN"
        assert set(row.keys()) == EXPECTED_COLUMNS


def test_mock_filter_end_tm_range():
    """end_tm 범위 필터가 정확히 동작하는지."""
    c = _client()
    # 2개월 전체 범위
    full = c.get(
        "/repl/mock/data",
        params={
            "lotcd": "L12345",
            "start": "2026-02-01",
            "end": "2026-04-30",
            "fail_name": "OPEN",
        },
    ).json()["rows"]
    # 좁은 범위 (1주)
    narrow = c.get(
        "/repl/mock/data",
        params={
            "lotcd": "L12345",
            "start": "2026-03-01",
            "end": "2026-03-07",
            "fail_name": "OPEN",
        },
    ).json()["rows"]
    assert len(narrow) <= len(full), "좁은 범위가 전체 범위보다 많을 수 없음"
    for row in narrow:
        assert "2026-03-01" <= row["end_tm"][:10] <= "2026-03-07"


def test_mock_filter_short_fail_name():
    c = _client()
    r = c.get(
        "/repl/mock/data",
        params={
            "lotcd": "L12345",
            "start": "2026-02-01",
            "end": "2026-04-30",
            "fail_name": "SHORT",
        },
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert all(row["fail_name"] == "SHORT" for row in rows)


def test_mock_missing_lotcd_returns_empty():
    c = _client()
    r = c.get(
        "/repl/mock/data",
        params={
            "lotcd": "NOPE",
            "start": "2026-02-01",
            "end": "2026-04-30",
            "fail_name": "OPEN",
        },
    )
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_mock_missing_query_param_422():
    c = _client()
    r = c.get("/repl/mock/data", params={"lotcd": "L12345"})
    assert r.status_code == 422


def test_mock_cache_hit():
    """같은 요청 2회 호출 시 내부 캐시가 동작해 동일한 결과를 반환하는지."""
    c = _client()
    params = {
        "lotcd": "L12345",
        "start": "2026-02-01",
        "end": "2026-04-30",
        "fail_name": "OPEN",
    }
    r1 = c.get("/repl/mock/data", params=params).json()
    r2 = c.get("/repl/mock/data", params=params).json()
    assert len(r1["rows"]) == len(r2["rows"])
    assert r1["rows"][:5] == r2["rows"][:5]
