from collections import defaultdict
import csv
import json
import tempfile
from typing import Literal

from pydantic import Field
from ..isolation.backend import isolation_backend
from ..runtime.container import ContainerRuntime
from ..runtime.process import ProcessRuntime
from ..types import Contract, ResultPage, ResultTable
from ..native_verification import WAFER_MAP_CONTRACT_VERSION, normalize_wafer_map_tables
from .load_wafer_maps import map_measurements
from .registry import ToolResult, ToolSpec
from .wafer_group_report import wafer_group_report


class PythonInput(Contract):
    code: str = Field(min_length=1, max_length=30000, description="tables[result_id][table_id]는 표별 전체 DataFrame이다. datasets[result_id]와 df(첫 입력)는 단일 표 결과에만 제공한다. 계산값은 emit_table로 출력해 셀 단위 근거로 저장한다. 도구 입력 인자는 자동으로 Python 변수가 되지 않는다.")
    input_result_ids: list[str] = Field(default_factory=list, max_length=10)


class WaferReport(Contract):
    threshold_pct: float = Field(ge=0, le=100, description="사용자가 지정한 기준 수율(%)")
    delivery: Literal['complete_answer', 'section'] = Field(description="보고서가 요청 전체에 답하면 complete_answer, 추가 분석이나 다른 산출물도 요청되면 section.")


class ReadInput(Contract):
    result_id: str
    table_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)
    columns: list[str] | None = None
    export_for_python: bool = Field(default=False, description="Save all stored rows of the selected table and columns as a CSV for execute_code. Offset and limit apply only to the chat preview.")
    wafer_report: WaferReport | None = Field(default=None, description="단일 lot의 LOT_ID, PARAM_NM, WFID, VALUE(0~1) 전체 표에서 요약과 상세 표를 함께 표시한다.")


def _selected_map_measurements(tables, sources):
    """Resolve the plotted member IDs without reaggregating cumulative-map cells."""
    invalid = {"selection_status": "unverified", "preview_rows": []}
    groups = defaultdict(set)
    try:
        for row in tables["wafer_map_members"]:
            key = (row["source_result_id"], row["lot_id"], str(row["wf_id"]), row["measurement_id"])
            members = groups[row["selection_id"], row["group_id"]]
            if row["contract_version"] != WAFER_MAP_CONTRACT_VERSION or key in members:
                return invalid
            members.add(key)
        selected = set()
        for row in tables["wafer_map_manifest"]:
            members = groups.pop((row["selection_id"], row["group_id"]))
            if (row["contract_version"] != WAFER_MAP_CONTRACT_VERSION or len(members) != int(row["member_count"])
                    or {key[0] for key in members} != set(json.loads(row["source_result_ids_json"]))):
                return invalid
            selected.update(members)
    except (KeyError, TypeError, ValueError):
        return invalid
    return map_measurements(sources, members=selected) if selected and not groups else invalid


async def read_result(args, ctx):
    page = await ctx.store.read_result(ctx.run["principal_id"], ctx.run["session_id"],
                                       **args.model_dump(exclude={'wafer_report', 'export_for_python'}))
    projection = ResultPage(table_id=page["table_id"], source_total_rows=page["total_rows"],
                            offset=page["offset"], limit=args.limit)
    complete = page["complete"] and not page["truncated"]
    artifacts = []
    scope = dict(page["scope"])
    scope.pop("rendered_summary", None)
    if args.wafer_report is not None and page["total_rows"]:
        if not complete:
            raise ValueError("The wafer report requires a complete query and a full page starting at offset 0.")
        caption, report = wafer_group_report(page["rows"], args.wafer_report.threshold_pct)
        artifacts.append(report)
        scope['rendered_summary'] = caption
    if args.export_for_python:
        isolation = ctx.settings.isolation
        if isolation is None:
            raise ValueError("A configured Python workspace is required to export a table")
        backend = isolation_backend(isolation, ctx.run["run_id"], ctx.run["principal_id"])
        with tempfile.TemporaryFile(mode="w+", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=page["columns"], extrasaction="ignore")
            writer.writeheader()
            async for row in ctx.store.iter_table_rows(
                ctx.run["principal_id"], ctx.run["session_id"], args.result_id, page["table_id"]
            ):
                writer.writerow(row)
            output.seek(0)
            path = await backend.upload_file(output.buffer)
        scope["python_csv"] = {"path": path, "table_id": page["table_id"],
                               "row_count": page["total_rows"], "source_complete": page["complete"]}
    reason = page["missing_reason"] or ("Only a page of the source table was read" if page["truncated"] else None)
    return ToolResult(tables=[ResultTable(table_id=page["table_id"], title=page["title"],
        rows=page["rows"], columns=page["columns"], units=page["units"], complete=complete, missing_reason=reason)],
        summary=projection.summary(len(page["rows"])), page=projection, artifacts=artifacts,
        scope=scope, source_result_ids=[args.result_id], status="partial" if not complete else None,
        data_origin=page["provenance"].get("data_origin", "unknown"))


async def run_python(args, ctx):
    datasets = {}
    origins = []
    limitations = []
    map_sources = {}
    for result_id in args.input_result_ids:
        obs = await ctx.store.observation(ctx.run["principal_id"], ctx.run["session_id"], result_id)
        if obs.status not in ("success", "partial", "empty"):
            raise ValueError("Cannot analyze an error result")
        origins.append(obs.provenance.get("data_origin"))
        datasets[result_id] = {}
        for table in obs.tables:
            rows = await ctx.store.rows(
                ctx.run["principal_id"], ctx.run["session_id"], result_id, table_id=table.table_id)
            if obs.tool_name == "load_wafer_maps" and table.table_id == "wafer_map_dies":
                map_sources[result_id] = rows
            datasets[result_id][table.table_id] = {"rows": rows,
                "columns": list(dict.fromkeys([*table.columns, *(key for row in rows for key in row)]))}
            if not table.complete:
                limitations.append(f"{result_id}/{table.table_id}: {table.missing_reason or 'Incomplete source'}")
    if getattr(ctx.settings, 'no_yield_tools', False):
        if ctx.settings.isolation is None:
            raise RuntimeError("isolated Python backend is not configured")
        runtime = ProcessRuntime(isolation=ctx.settings.isolation, run_id=ctx.run["run_id"],
                                 principal_id=ctx.run["principal_id"])
    else:
        runtime = (ContainerRuntime(ctx.settings.python_image) if ctx.settings.python_runtime == "docker"
            else ProcessRuntime(ctx.settings.python_executable))
    result = await runtime.execute(args.code, datasets)
    try:
        return _python_result(args, result, map_sources, limitations, origins)
    except BaseException:
        for item in result.get("generated_files", []):
            item["data"].close()
        raise


def _python_result(args, result, map_sources, limitations, origins):
    scope = {"python_workspace": result["workspace"]} if "workspace" in result else {}
    renamed = {}
    if map_sources:
        raw_tables = result['tables']
        names = [table['table_id'] for table in raw_tables]
        if len(names) == len(set(names)):
            normalized, renamed = normalize_wafer_map_tables({table['table_id']: table['rows'] for table in raw_tables})
            if 'wafer_map_members' in normalized and result['status'] == 'success':
                scope['map_measurements'] = _selected_map_measurements(normalized, map_sources)
        result['tables'] = [{**table,
            'table_id': renamed.get(table['table_id'], table['table_id']),
            'title': renamed.get(table['table_id'], table['title'])} for table in raw_tables]
    artifacts = [{"title": f"분석 차트 {i+1}", "mime": "text/html", "data": html}
        for i, html in enumerate(result["plots"])]
    artifacts.extend(result.get("generated_files", []))
    complete = result["status"] == "success" and not limitations
    reason = "; ".join(limitations) or (None if complete else "Python execution did not complete")
    tables = [ResultTable.model_validate({**table, "complete": complete, "missing_reason": reason})
        for table in result["tables"]]
    if not tables:
        tables = [ResultTable(table_id="output", title="Python output", rows=[{
            "stdout": result["stdout"], "stderr": result["stderr"], "error": result.get("error")}],
            complete=complete, missing_reason=reason)]
    summary = result["stdout"][:2000] or str(result.get("error") or "Python 분석 실행")
    if renamed:
        summary += '\n근거 표 이름 자동 보정: ' + ', '.join(f'{old} → {new}' for old, new in renamed.items())
    return ToolResult(tables=tables, summary=summary,
        scope=scope,
        status=("partial" if limitations else "success") if result["status"] == "success" else "error", artifacts=artifacts,
        source_result_ids=args.input_result_ids, data_origin="fixture" if "fixture" in origins else
            "unknown" if any(origin != "live" for origin in origins) else "live")


def register(registry):
    registry.add(ToolSpec("read_result", "이전 결과의 전체 원본을 페이지로 읽는다. result_id와 offset으로 표본 밖의 자료를 확인한다.", ReadInput, read_result, eager=True))
    registry.add(ToolSpec("run_python", "별도 Python 실행 환경으로 result_id의 전체 데이터를 분석한다. tables[result_id][table_id], datasets[id]와 df(단일 표 입력만), pd/np/px/go 사용. 표는 emit_table(df), 차트는 emit_plot(fig). 최종 다운로드 파일은 publish_directory / 파일명 경로에 저장한다. 성공하면 폴더 바로 아래의 각 파일이 첨부된다. 답변에는 도구가 반환한 MEDIA 참조를 사용한다. 세션 변수는 유지되지 않는다.", PythonInput, run_python, read_only=False, timeout=70, effect='artifact', eager=True))
