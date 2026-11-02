from pydantic import Field
from ..runtime.container import ContainerRuntime
from ..runtime.process import ProcessRuntime
from ..types import Contract, ResultPage, ResultTable
from .registry import ToolResult, ToolSpec


class PythonInput(Contract):
    code: str = Field(min_length=1, max_length=30000, description="tables[result_id][table_id]는 표별 전체 DataFrame이다. datasets[result_id]와 df(첫 입력)는 단일 표 결과에만 제공한다. 계산값은 emit_table로 출력해 셀 단위 근거로 저장한다. 도구 입력 인자는 자동으로 Python 변수가 되지 않는다.")
    input_result_ids: list[str] = Field(default_factory=list, max_length=10)


class ReadInput(Contract):
    result_id: str
    table_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)
    columns: list[str] | None = None


async def read_result(args, ctx):
    page = await ctx.store.read_result(ctx.run["principal_id"], ctx.run["session_id"], **args.model_dump())
    projection = ResultPage(table_id=page["table_id"], source_total_rows=page["total_rows"],
                            offset=page["offset"], limit=args.limit)
    complete = page["complete"] and not page["truncated"]
    reason = page["missing_reason"] or ("Only a page of the source table was read" if page["truncated"] else None)
    return ToolResult(tables=[ResultTable(table_id=page["table_id"], title=page["title"],
        rows=page["rows"], columns=page["columns"], units=page["units"], complete=complete, missing_reason=reason)],
        summary=projection.summary(len(page["rows"])), page=projection,
        scope=page["scope"], source_result_ids=[args.result_id], status="partial" if not complete else None,
        data_origin=page["provenance"].get("data_origin", "unknown"))


async def run_python(args, ctx):
    datasets = {}
    origins = []
    limitations = []
    for result_id in args.input_result_ids:
        obs = await ctx.store.observation(ctx.run["principal_id"], ctx.run["session_id"], result_id)
        if obs.status not in ("success", "partial", "empty"):
            raise ValueError("Cannot analyze an error result")
        origins.append(obs.provenance.get("data_origin"))
        datasets[result_id] = {}
        for table in obs.tables:
            rows = await ctx.store.rows(
                ctx.run["principal_id"], ctx.run["session_id"], result_id, table_id=table.table_id)
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
    artifacts = [{"title": f"분석 차트 {i+1}", "mime": "text/html", "data": html}
        for i, html in enumerate(result["plots"])]
    complete = result["status"] == "success" and not limitations
    reason = "; ".join(limitations) or (None if complete else "Python execution did not complete")
    tables = [ResultTable.model_validate({**table, "complete": complete, "missing_reason": reason})
        for table in result["tables"]]
    if not tables:
        tables = [ResultTable(table_id="output", title="Python output", rows=[{
            "stdout": result["stdout"], "stderr": result["stderr"], "error": result.get("error")}],
            complete=complete, missing_reason=reason)]
    return ToolResult(tables=tables, summary=result["stdout"][:2000] or str(result.get("error") or "Python 분석 실행"),
        status=("partial" if limitations else "success") if result["status"] == "success" else "error", artifacts=artifacts,
        source_result_ids=args.input_result_ids, data_origin="fixture" if "fixture" in origins else
            "unknown" if any(origin != "live" for origin in origins) else "live")


def register(registry):
    registry.add(ToolSpec("read_result", "이전 결과의 전체 원본을 페이지로 읽는다. result_id와 offset으로 표본 밖의 자료를 확인한다.", ReadInput, read_result, eager=True))
    registry.add(ToolSpec("run_python", "별도 Python 실행 환경으로 result_id의 전체 데이터를 분석한다. tables[result_id][table_id], datasets[id]와 df(단일 표 입력만), pd/np/px/go 사용. 표는 emit_table(df), 차트는 emit_plot(fig), 설명은 print(). 세션 변수는 유지되지 않는다.", PythonInput, run_python, read_only=False, timeout=70, effect='artifact', eager=True))
