"""Actual configured model + fixture domain data. Not live DB acceptance."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

from harness.tools.registry import domain_registry, ToolResult
from .run_live import evaluate


def synthetic_registry():
    registry = domain_registry()
    for name, spec in registry.tools.items():
        if name in ("run_python", "read_result", "recall_session"):
            continue
        async def fixture(args, ctx, name=name):
            rows = []
            if name == "query_yield":
                for i, value in enumerate((93.0, 90.0, 86.0, 84.0)):
                    year, week, _ = (date.today() - timedelta(weeks=3-i)).isocalendar()
                    rows.append({"week": f"{year}-W{week:02}", "lotcount": 10, "wfCount": 250, "yield": value})
            return ToolResult(rows=rows, summary="명시적으로 생성한 가상 평가 데이터. 실제 생산 자료가 아닙니다.", data_origin="fixture", scope=args.model_dump(mode="json"))
        spec.handler = fixture
    return registry


if __name__ == "__main__":
    report = asyncio.run(evaluate([
        "가상 제품 DEMO의 최근 4주 수율을 표로 보여주고 Python 계산으로 감소폭을 검산해줘. 원인은 근거와 가설을 구분해서 설명해줘."
    ], Path("outputs/harness-synthetic-model.json"), registry_factory=synthetic_registry, data_origin="fixture"))
    calls = {call["tool_name"] for call in report["cases"][0]["calls"]}
    assert {"query_yield", "run_python"}.issubset(calls), calls
    assert report["cases"][0]["status"] in ("completed", "partial")
