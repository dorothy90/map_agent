"""Handle-aware compatibility adapter for isolated Python execution."""
from __future__ import annotations

import ast
import hashlib
import json
import math

from pydantic import Field, model_validator

from ..runtime.container import ContainerRuntime
from ..runtime.process import ProcessRuntime
from ..types import Contract, ResultTable
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, json_size, resolve_many
from .python_tools import PythonInput

MAX_INPUT_ROWS = 250_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_TABLES = 10
MAX_OUTPUT_ROWS = 250_000
MAX_PROTOCOL_BYTES = 20 * 1024 * 1024


class OutputUnit(Contract):
    table_id: str = Field(min_length=1, max_length=128, description="출력 표 식별자")
    column: str = Field(min_length=1, max_length=128, description="단위를 적용할 출력 열")
    unit: str = Field(min_length=1, max_length=100, description="호출자가 선언한 단위")


class CompatiblePythonInput(Contract):
    code: str = Field(min_length=1, max_length=30000, description="tables[result_id][table_id]는 표별 전체 DataFrame이다. datasets[result_id]와 df(첫 입력)는 단일 표 결과에만 제공한다. 계산값은 emit_table로 출력해 셀 단위 근거로 저장한다. 도구 입력 인자는 자동으로 Python 변수가 되지 않는다.")
    input_result_ids: list[str] = Field(default_factory=list, max_length=10)
    sources: list[TableHandle] | None = Field(default=None, min_length=1, max_length=10,
        description="실행 환경에 전달할 저장 표 핸들")
    output_units: list[OutputUnit] = Field(default_factory=list, max_length=100,
        description="출력 열에 적용할 호출자 선언 단위")

    @model_validator(mode="after")
    def compatible_paths(self):
        if self.sources is not None and self.input_result_ids:
            raise ValueError("sources and input_result_ids cannot be combined")
        if self.sources is None and self.output_units:
            raise ValueError("output_units require sources")
        keys = [(item.table_id, item.column) for item in self.output_units]
        if len(keys) != len(set(keys)):
            raise ValueError("output_units must be unique")
        return self


def _runtime(ctx):
    return (ContainerRuntime(ctx.settings.python_image) if ctx.settings.python_runtime == "docker"
        else ProcessRuntime(ctx.settings.python_executable))


def _scalar_rows(rows):
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Python output rows must be objects")
        for value in row.values():
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Python output cells must be scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Python output cells must be finite")


def _python_error(result):
    for table in result.tables:
        for row in table.rows:
            error = row.get("error")
            if isinstance(error, dict):
                return error
            if isinstance(error, str):
                try:
                    parsed = json.loads(error)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed
    try:
        parsed = ast.literal_eval(result.summary)
    except (SyntaxError, ValueError):
        parsed = None
    return parsed if isinstance(parsed, dict) else None


def _with_handle_contract(result):
    summary = result.summary
    if result.status == "error":
        error = _python_error(result)
        if error:
            kind = str(error.get("type") or "PythonError")
            message = str(error.get("message") or "").strip()
            prefix = f"{kind}: {message}" if message else kind
            if summary in ("", "None", str(error)):
                summary = prefix
            elif not summary.startswith(prefix):
                summary = f"{prefix}\n{summary}"
    wrapped = build_result(result.tables, source_result_ids=result.source_result_ids,
        limitations=result.scope.get("limitations", []), scope=result.scope,
        summary=summary, status=result.status, data_origin=result.data_origin,
        artifacts=result.artifacts)
    wrapped.source_result_ids = result.source_result_ids
    wrapped.status = result.status
    return wrapped


async def run_python(args: CompatiblePythonInput, ctx):
    resolved = await resolve_many(ctx, args.sources)
    datasets = {}
    input_rows = 0
    for handle, table in zip(args.sources, resolved):
        input_rows += len(table.rows)
        datasets.setdefault(handle.result_id, {})[handle.table_id] = {
            "rows": table.rows, "columns": table.columns}
    input_bytes = json_size(datasets)
    if input_rows > MAX_INPUT_ROWS or input_bytes > MAX_INPUT_BYTES:
        raise ValueError("Selected Python input exceeds the size limit")
    runtime = _runtime(ctx)
    result = await runtime.execute(args.code, datasets, timeout=60)
    if json_size(result) > MAX_PROTOCOL_BYTES:
        raise ValueError("Python output exceeds the protocol limit")
    raw_tables = result.get("tables", [])
    if len(raw_tables) > MAX_OUTPUT_TABLES:
        raise ValueError("Python output table count exceeds limit")
    names = [table.get("table_id") for table in raw_tables]
    if "execution_manifest" in names or len(names) != len(set(names)):
        raise ValueError("Python output table names are duplicate or reserved")
    if len(result.get("plots", [])) > 3:
        raise ValueError("Python plot count exceeds limit")
    runtime_success = result.get("status") == "success"
    input_complete = all(table.complete for table in resolved)
    limitations = [table.missing_reason or "Incomplete source" for table in resolved if not table.complete]
    reason = "; ".join(limitations) or (None if runtime_success else "Python execution did not complete")
    units = {(item.table_id, item.column): item.unit for item in args.output_units}
    tables = []
    output_rows = 0
    for raw in raw_tables:
        rows = raw.get("rows", [])
        _scalar_rows(rows)
        output_rows += len(rows)
        if output_rows > MAX_OUTPUT_ROWS:
            raise ValueError("Python output row count exceeds limit")
        columns = list(dict.fromkeys([*raw.get("columns", []),
            *(key for row in rows for key in row)]))
        declared = {column: unit for (table_id, column), unit in units.items()
                    if table_id == raw.get("table_id")}
        unknown = [column for column in declared if column not in columns]
        if unknown:
            raise ValueError(f"Unknown output unit column: {unknown[0]}")
        complete = runtime_success and input_complete
        tables.append(ResultTable(table_id=raw.get("table_id"),
            title=raw.get("title") or raw.get("table_id"), rows=rows, columns=columns,
            units=declared, complete=complete, missing_reason=None if complete else reason))
    if not tables:
        log = {"stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""),
            "error": json.dumps(result.get("error"), ensure_ascii=False) if result.get("error") else None}
        tables.append(ResultTable(table_id="output", title="Python output", rows=[log],
            columns=list(log), complete=runtime_success and input_complete,
            missing_reason=None if runtime_success and input_complete else reason))
    undeclared = [(table_id, column) for table_id, column in units
                  if not any(table.table_id == table_id for table in tables)]
    if undeclared:
        raise ValueError(f"Unknown output unit table: {undeclared[0][0]}")
    output_bytes = json_size([table.model_dump() for table in tables])
    manifest = {"code_sha256": hashlib.sha256(args.code.encode()).hexdigest(),
        "runtime": "docker" if ctx.settings.python_runtime == "docker" else "process",
        "input_handles_json": json.dumps([item.model_dump() for item in args.sources],
            sort_keys=True, ensure_ascii=False), "input_rows": input_rows,
        "input_bytes": input_bytes, "input_complete": input_complete,
        "output_table_count": len(raw_tables), "output_bytes": output_bytes,
        "execution_status": result.get("status", "error"),
        "limitations_json": json.dumps(limitations, ensure_ascii=False)}
    tables.append(ResultTable(table_id="execution_manifest", title="Execution manifest",
        rows=[manifest], columns=list(manifest), complete=runtime_success and input_complete,
        missing_reason=None if runtime_success and input_complete else reason))
    artifacts = [{"title": "분석 코드", "mime": "text/plain", "data": args.code}]
    artifacts.extend({"title": f"분석 차트 {index + 1}", "mime": "text/html", "data": plot}
        for index, plot in enumerate(result.get("plots", [])))
    origins = {table.observation.provenance.get("data_origin", "unknown") for table in resolved}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    status = "error" if not runtime_success else "partial" if not input_complete else "success"
    return build_result(tables, source_result_ids=[item.result_id for item in args.sources],
        scope={"input_complete": input_complete,
            "unit_origin": "caller_declared" if args.output_units else "unspecified"},
        status=status, summary=result.get("stdout", "")[:2000] or
            str(result.get("error") or "Python 분석 실행"), data_origin=origin,
        artifacts=artifacts)


async def dispatch_python(args: CompatiblePythonInput, ctx, *, legacy_handler,
                          legacy_schema=PythonInput):
    if args.sources is None:
        legacy = legacy_schema.model_validate({"code": args.code,
            "input_result_ids": args.input_result_ids})
        result = await legacy_handler(legacy, ctx)
    else:
        result = await run_python(args, ctx)
    return _with_handle_contract(result)
