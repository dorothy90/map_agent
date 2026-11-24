"""Live model follow-up evaluation with explicitly generated data only."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

from .run_live import evaluate


AVAILABLE_DAY = date.today() - timedelta(days=12)


def daily_rows():
    rows = []
    for offset in range(13, -1, -1):
        day = date.today() - timedelta(days=offset)
        row = {"day": day.isoformat(), "lots": None, "wafers": None, "yield_pct": None}
        if day == AVAILABLE_DAY:
            row.update(lots=3, wafers=75, yield_pct=73.0)
        rows.append(row)
    return rows


if __name__ == "__main__":
    report = asyncio.run(evaluate([
        "가상 제품 DEMO의 최근 14일 일별 수율을 조회해줘.",
        f"왜 {AVAILABLE_DAY.month}월 {AVAILABLE_DAY.day}일밖에 안 나오니",
    ], Path("outputs/harness-followup-synthetic.json"), fixture_rows=daily_rows()))
    raise SystemExit(0 if all(c["status"] == "completed" for c in report["cases"]) else 1)
