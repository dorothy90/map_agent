"""REPL agent 용 mock 데이터 라우터.

실제 배포에서는 사내 API 를 바라볼 예정이지만, MVP 단계에선 같은 FastAPI 프로세스
안에 이 mock 라우터를 두고 로컬 CSV 를 LOTCD / fail_name / end_tm 범위로 필터링해
JSON 으로 반환한다. fetch_data tool 은 `DATA_API_BASE_URL` 환경변수만 바꾸면
프로덕션 API 로 스위치된다.

스키마 (generate_sample.py 와 일치):
    lotcd, lot_id, wf_id, fail_name, fail_value, oper, legend, legend_value, end_tm

필터:
- lotcd 완전 매칭
- fail_name 완전 매칭
- start <= end_tm[:10] <= end  (YYYY-MM-DD 문자열 사전순 비교, ISO-8601 이라 안전)

성능:
100k 행 CSV 를 매 호출마다 읽으면 느리니, 모듈 레벨 캐시에 한 번만 로드하고
파일 mtime 이 바뀌면 자동으로 재파싱 (generate_sample.py 재실행 감지).
"""

from __future__ import annotations

import csv
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_DATA_DIR = Path(__file__).parent / "data"
_CSV = _DATA_DIR / "sample.csv"

_CACHE: list[dict[str, str]] = []
_CACHE_MTIME: float = 0.0


def _load_all() -> list[dict[str, str]]:
    """sample.csv 전체를 캐시해 dict 리스트로 반환. mtime 변경 시 자동 무효화."""
    global _CACHE, _CACHE_MTIME
    if not _CSV.exists():
        return []
    mtime = _CSV.stat().st_mtime
    if not _CACHE or mtime != _CACHE_MTIME:
        with _CSV.open(newline="", encoding="utf-8") as f:
            _CACHE = list(csv.DictReader(f))
        _CACHE_MTIME = mtime
    return _CACHE


@router.get("/data")
def get_data(lotcd: str, start: str, end: str, fail_name: str) -> dict:
    """LOTCD / fail_name / end_tm 범위로 필터된 행을 JSON 으로 반환.

    start/end 는 YYYY-MM-DD 문자열이며, 각 행의 `end_tm` 앞 10자(날짜 부분) 와
    `start <= end_tm[:10] <= end` 로 비교한다.
    """
    query = {"lotcd": lotcd, "start": start, "end": end, "fail_name": fail_name}
    all_rows = _load_all()
    if not all_rows:
        return {"rows": [], "query": query}

    filtered = [
        r
        for r in all_rows
        if r.get("lotcd") == lotcd
        and r.get("fail_name") == fail_name
        and start <= r.get("end_tm", "")[:10] <= end
    ]
    return {"rows": filtered, "query": query}
