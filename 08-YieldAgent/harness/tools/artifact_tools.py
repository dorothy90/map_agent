from ..runtime.domain_process import run_blocking
import json
from pathlib import Path
from pydantic import Field

from ..types import Contract
from .registry import ToolResult, ToolSpec
from .wads_tools import WadsReportInput as WadsInput, filters, report_id


async def get_wads_report(args, ctx):
    from wads_tools import _query_wads_data, _end_tm_expr, _report_key
    params = filters(args)
    origins = []
    if args.report_id:
        rows = await ctx.store.rows(ctx.run["principal_id"], ctx.run["session_id"], args.source_result_id, table_id="reports")
        match = next((row for row in rows if row.get("report_id") == args.report_id), None)
        if match is None:
            raise ValueError("Report ID not found in owned source result")
        params = {"exact_key": _report_key(match)}
        origins = [args.source_result_id]
    frame = await run_blocking(_query_wads_data, **params, row_limit=args.limit+1,
        columns=f"r.LOTCD, r.CATEGORY, r.PARAMETER, {_end_tm_expr('r')}, r.HTML")
    complete = len(frame) <= args.limit
    rows = json.loads(frame.iloc[:args.limit].to_json(orient="records", date_format="iso"))
    artifacts = []
    for row in rows:
        row["report_id"] = report_id(row)
        html_key = next((key for key in row if key.lower() == "html"), None)
        html = row.pop(html_key, None) if html_key else None
        if html:
            artifacts.append({"title": f"WADS {row['report_id']}", "mime": "text/html", "data": html})
    return ToolResult(rows=rows, artifacts=artifacts, source_result_ids=origins,
        scope=dict(args.model_dump(mode="json"), complete=complete, source="wads_original_html"),
        status="success" if complete else "partial",
        summary=f"WADS 원문 {len(artifacts)}개" + (" (최신 조회 상한 도달)" if not complete else ""))


class ExportInput(Contract):
    result_ids: list[str] = Field(min_length=1, max_length=10)
    title: str = Field(default="수율 분석 보고서", max_length=100)


async def export_report(args, ctx):
    from ..report import build_report_sections
    observations, tables, artifacts = [], {}, {}
    owner, session = ctx.run["principal_id"], ctx.run["session_id"]
    for result_id in dict.fromkeys(args.result_ids):
        obs = await ctx.store.observation(owner, session, result_id)
        observations.append(obs)
        if obs.status in ("error", "cancelled"):
            continue
        tables[result_id] = {}
        for table in obs.tables:
            tables[result_id][table.table_id] = await ctx.store.rows(owner, session, result_id, table_id=table.table_id)
        for artifact in obs.artifact_refs:
            aid = artifact["artifact_id"]
            artifacts[aid] = await ctx.store.artifact(owner, session, aid)
    sections = build_report_sections(observations, tables, artifacts)
    if not sections:
        raise ValueError("No successful stored content to export")
    used_ids = list(dict.fromkeys(r for section in sections for r in section["result_ids"]))
    partial = len(used_ids) != len(set(args.result_ids)) or any(s["analysis_status"] == "partial" for s in sections)
    payload = await run_blocking(_render_ppt, {"report_sections": sections, "title": args.title})
    origins = [o.provenance.get("data_origin", "unknown") for o in observations if o.result_id in used_ids]
    return ToolResult(artifacts=[{"title": args.title, "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "data": payload}],
        source_result_ids=used_ids, summary="선택한 자료를 순서대로 포함한 PPTX 생성",
        scope={"included_result_ids": used_ids, "omitted_result_ids": [r for r in args.result_ids if r not in used_ids], "complete": not partial},
        status="partial" if partial else "success", data_origin="fixture" if "fixture" in origins else "live" if all(o == "live" for o in origins) else "unknown")


def _render_ppt(state):
    from ppt_builder import YieldReportPPTBuilder
    payload, path = YieldReportPPTBuilder().build(state)
    Path(path).unlink(missing_ok=True)
    return payload


def register(registry):
    registry.add(ToolSpec("get_wads_report", "DB에 저장된 기존 WADS 열화리포트 원본을 제품·발행 기간·검사 단계·불량 항목으로 조회해 HTML 산출물로 보여준다. 각 행은 보고서 메타데이터(lotcd/category/parameter/end_tm)이며, 원문 열람과 보고서 목록 확인에 사용한다.", WadsInput, get_wads_report))
    registry.add(ToolSpec("export_report", "선택한 result_id의 모든 표·분석·WADS 원문·이미지를 요청 순서대로 PPTX에 보존한다. 분석 미실행과 이상 없음을 구분하며 지원하지 않는 파일은 명시적 오류를 반환한다.", ExportInput, export_report, read_only=False, effect="artifact"))
