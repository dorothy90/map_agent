"""Live model follow-up evaluation with explicitly generated data only."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

from .run_live import evaluate
from .run_synthetic import synthetic_registry
from harness.tools.registry import ToolResult


AVAILABLE_DAY = date.today() - timedelta(days=12)


def registry():
    tools = synthetic_registry()

    async def daily(args, ctx):
        if args.unit != "daily":
            return ToolResult(status="error", summary="이 가상 평가는 일별 자료만 제공합니다.", data_origin="fixture")
        rows = []
        for offset in range(args.periods - 1, -1, -1):
            day = args.ref_date - timedelta(days=offset)
            row = {"week": day.isoformat(), "lotcount": "-", "wfCount": "-", "pt1c_PT1C": "-", "gms_fab": "-"}
            if day == AVAILABLE_DAY:
                row.update(lotcount=3, wfCount=75, pt1c_PT1C=.73, gms_fab=91.0)
            rows.append(row)
        html = "<h2>가상 평가용 수율표</h2><table><tr>" + "".join(f"<th>{c}</th>" for c in rows[0]) + "</tr>"
        html += "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row.values()) + "</tr>" for row in rows) + "</table>"
        return ToolResult(rows=rows, summary=f"가상 평가용 {args.lotcd} 일별 {len(rows)}개 기간 조회. 실제 생산 자료가 아닙니다.",
            scope={**args.model_dump(mode="json"), "value_units": {"PT1C": "source_value_unit_unspecified", "GMS": "percent"}},
            artifacts=[{"title": "가상 수율표", "mime": "text/html", "data": html}], data_origin="fixture")

    tools.tools["query_yield"].handler = daily
    return tools


if __name__ == "__main__":
    report = asyncio.run(evaluate([
        "가상 제품 DEMO의 최근 14일 일별 수율을 조회해줘.",
        f"왜 {AVAILABLE_DAY.month}월 {AVAILABLE_DAY.day}일밖에 안 나오니",
    ], Path("outputs/harness-followup-synthetic.json"), registry_factory=registry, data_origin="fixture"))
    raise SystemExit(0 if all(c["status"] == "completed" for c in report["cases"]) else 1)
