import csv
import tempfile
from typing import Literal

from pydantic import Field
from ..isolation.backend import isolation_backend
from ..types import Contract, ResultPage, ResultTable
from .registry import ToolResult, ToolSpec
from .wafer_group_report import wafer_group_report


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
        backend = isolation_backend(isolation, ctx.run["run_id"], ctx.run["principal_id"], ctx.run.get("session_id"))
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


def register(registry):
    registry.add(ToolSpec("read_result", "이전 결과의 전체 원본을 페이지로 읽는다. result_id와 offset으로 표본 밖의 자료를 확인한다.", ReadInput, read_result, eager=True))
