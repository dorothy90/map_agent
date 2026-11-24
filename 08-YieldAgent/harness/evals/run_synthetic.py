"""Use the configured model to read and calculate a synthetic weekly result via stock Hermes."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

from .run_live import evaluate


def weekly_rows():
    rows = []
    for i, value in enumerate((93.0, 90.0, 86.0, 84.0)):
        year, week, _ = (date.today() - timedelta(weeks=3-i)).isocalendar()
        rows.append({"week": f"{year}-W{week:02}", "lots": 10, "wafers": 250, "yield_pct": value})
    return rows


if __name__ == "__main__":
    report = asyncio.run(evaluate([
        "저장된 DEMO 제품의 최근 4주 수율을 표로 보여주고 Python으로 감소폭을 계산해줘."
    ], Path("outputs/harness-synthetic-model.json"), fixture_rows=weekly_rows()))
    case = report["cases"][0]
    successes = {result["tool_name"] for result in case["observations"] if result["status"] == "success"}
    assert {"read_result", "execute_code"}.issubset(successes), successes
    assert case["status"] == "completed" and case["native_engine"]["entrypoint"] == "stock_chat"
