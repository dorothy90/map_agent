"""WADS report statistics and original-report retrieval with fixture data."""
import asyncio
from datetime import date
from pathlib import Path

from .run_live import evaluate
from .run_synthetic import synthetic_registry
from harness.tools.registry import ToolResult


def registry(product="DEMO", year=None, month=8):
    tools = synthetic_registry()
    year = year or date.today().year
    reports = [
        {"lotcd": product, "category": category, "parameter": parameter,
         "end_tm": f"{year}-{month:02}-{day:02} 09:00:00"}
        for day, category, parameter in ((2, "PT1H_TEST", "ALPHA"), (12, "PT1H_TEST", "ALPHA"),
                                         (14, "PT1C_TEST", "BETA"), (29, "PT1C_TEST", "GAMMA"))
    ]

    def selected(args):
        return [(i, r) for i, r in enumerate(reports) if args.lotcd == r["lotcd"]
            and args.start_tm.isoformat() <= r["end_tm"][:10] <= args.end_tm.isoformat()
            and (not args.category or r["category"].startswith(args.category))
            and (not args.parameter or r["parameter"] == args.parameter)]

    async def query(args, ctx):
        rows = [{**r, "groupkey": f"{product}LOT{i}-{j:02}"} for i, r in selected(args) for j in range((2, 1, 3, 1)[i])]
        return ToolResult(rows=rows, scope=args.model_dump(mode="json"), summary="가상 WADS 열화리포트와 웨이퍼 조인 목록. 실제 생산 자료가 아닙니다.", data_origin="fixture")

    async def originals(args, ctx):
        rows = [r.copy() for _, r in selected(args)]
        artifacts = [{"title": f"가상 WADS {r['parameter']} {r['end_tm'][:10]}", "mime": "text/html",
                      "data": f"<h1>가상 열화리포트</h1><p>{r['lotcd']} · {r['category']} · {r['parameter']} · {r['end_tm']}</p>"} for r in rows]
        return ToolResult(rows=rows, artifacts=artifacts, scope=args.model_dump(mode="json"), summary=f"가상 WADS 원본 보고서 {len(rows)}개", data_origin="fixture")

    tools.tools["query_wads"].handler = query
    tools.tools["get_wads_report"].handler = originals
    return tools


if __name__ == "__main__":
    report = asyncio.run(evaluate([
        f"가상 제품 DEMO의 {date.today().year}년 8월 열화리포트 통계좀 내줄래?",
        "열화리포트 보여달라고",
    ], Path("outputs/harness-wads-synthetic.json"), registry_factory=registry, data_origin="fixture"))
    calls = [{c["tool_name"] for c in case["calls"]} for case in report["cases"]]
    ok = all(c["status"] == "completed" for c in report["cases"])
    ok = ok and bool(calls[0] & {"query_wads", "get_wads_report"}) and "get_wads_report" in calls[1]
    ok = ok and not any(names & {"query_yield", "export_report"} for names in calls)
    raise SystemExit(0 if ok else 1)
