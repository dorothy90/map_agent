from __future__ import annotations

import json
import io
from types import SimpleNamespace
import zipfile

import pytest

from harness.types import ResultTable
from harness.tools.registry import ToolResult
from harness.tools.wafer_set_tools import _version


class Store:
    def __init__(self, *, complete=True):
        self.rows_by_table = {"chosen": [{"n": 1}, {"n": 2}], "other": [{"n": 99}]}
        self.obs = SimpleNamespace(result_id="source", tool_name="fixture", summary="fixture",
            artifact_refs=[], status="success",
            scope={"handle_contract": "wafer-table/v1", "table_versions": {
                key: _version(value) for key, value in self.rows_by_table.items()}},
            tables=[ResultTable(table_id="chosen", title="Chosen", rows=self.rows_by_table["chosen"],
                columns=["n"], complete=complete, missing_reason=None if complete else "limited"),
                ResultTable(table_id="other", title="Other", rows=self.rows_by_table["other"],
                columns=["n"], complete=True)], provenance={"data_origin": "fixture"})
        self.items = {"source": (self.obs, self.rows_by_table)}

    def add(self, result_id, result):
        rows = {table.table_id: table.rows for table in result.tables}
        refs = [SimpleNamespace(**table.model_dump(), total_rows=len(table.rows))
                for table in result.tables]
        obs = SimpleNamespace(result_id=result_id, tool_name="run_python", summary=result.summary,
            artifact_refs=[], status=result.status or "success", scope=result.scope, tables=refs,
            provenance={"data_origin": result.data_origin})
        self.items[result_id] = (obs, rows)

    async def observation(self, principal, session, result_id):
        if (principal, session) != ("owner", "session") or result_id not in self.items:
            raise PermissionError("Result unavailable")
        return self.items[result_id][0]

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return json.loads(json.dumps(self.items[result_id][1][table_id]))


def context(*, complete=True):
    settings = SimpleNamespace(python_runtime="process", python_image="image", python_executable="python")
    return SimpleNamespace(store=Store(complete=complete), settings=settings,
        run={"principal_id": "owner", "session_id": "session"})


class Runtime:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, code, datasets, timeout=60):
        self.calls.append((code, datasets, timeout))
        return self.result


@pytest.mark.asyncio
async def test_handle_python_passes_only_selected_tables_and_adds_manifest(monkeypatch):
    from harness.tools import wafer_set_python as tool

    runtime = Runtime({"status": "success", "tables": [{"table_id": "derived", "title": "Derived",
        "rows": [{"value": 3}], "columns": ["value"], "units": {}, "complete": True,
        "missing_reason": None}], "plots": ["<div>plot</div>"], "stdout": "ok", "stderr": "", "error": None})
    monkeypatch.setattr(tool, "_runtime", lambda ctx: runtime)
    result = await tool.run_python(tool.CompatiblePythonInput(code="emit", sources=[{
        "result_id": "source", "table_id": "chosen"}],
        output_units=[{"table_id": "derived", "column": "value", "unit": "count"}]), context())
    assert set(runtime.calls[0][1]) == {"source"}
    assert set(runtime.calls[0][1]["source"]) == {"chosen"}
    assert result.tables[0].units == {"value": "count"}
    assert result.tables[-1].table_id == "execution_manifest"
    assert result.tables[-1].rows[0]["output_table_count"] == 1
    assert result.scope["unit_origin"] == "caller_declared"
    assert result.source_result_ids == ["source"]


@pytest.mark.asyncio
async def test_python_propagates_partial_input_and_does_not_promote_errors(monkeypatch):
    from harness.tools import wafer_set_python as tool

    successful = Runtime({"status": "success", "tables": [{"table_id": "derived", "title": "Derived",
        "rows": [{"value": 3}], "columns": ["value"], "units": {}, "complete": True,
        "missing_reason": None}], "plots": [], "stdout": "", "stderr": "", "error": None})
    monkeypatch.setattr(tool, "_runtime", lambda ctx: successful)
    partial = await tool.run_python(tool.CompatiblePythonInput(code="emit", sources=[{
        "result_id": "source", "table_id": "chosen"}]), context(complete=False))
    assert partial.status == "partial" and not partial.tables[0].complete
    failed = Runtime({"status": "error", "tables": [{"table_id": "derived", "title": "Derived",
        "rows": [{"value": 3}], "columns": ["value"], "units": {}, "complete": True,
        "missing_reason": None}], "plots": [], "stdout": "", "stderr": "bad", "error": {"type": "boom"}})
    monkeypatch.setattr(tool, "_runtime", lambda ctx: failed)
    error = await tool.run_python(tool.CompatiblePythonInput(code="emit", sources=[{
        "result_id": "source", "table_id": "chosen"}]), context())
    assert error.status == "error" and all(not table.complete for table in error.tables)


@pytest.mark.asyncio
async def test_legacy_python_adds_generic_handle_contract_without_changing_payload():
    from harness.tools import wafer_set_python as tool

    calls = []
    async def legacy(args, ctx):
        calls.append(args.model_dump())
        return ToolResult(tables=[ResultTable(table_id="numbers", title="Numbers",
            rows=[{"number": 1}], columns=["number"], complete=True)], summary="legacy",
            artifacts=[{"title": "code", "mime": "text/plain", "data": "x"}],
            source_result_ids=["source"], status="success", data_origin="fixture")
    args = tool.CompatiblePythonInput(code="print(1)", input_result_ids=["source"])
    result = await tool.dispatch_python(args, context(), legacy_handler=legacy, legacy_schema=tool.PythonInput)
    assert result.summary == "legacy"
    assert result.scope["handle_contract"] == "wafer-table/v1"
    assert result.scope["table_versions"]["numbers"] == _version([{"number": 1}])
    assert result.scope["schema_fingerprint"] and result.scope["limitations"] == []
    assert result.tables[0].rows == [{"number": 1}]
    assert result.artifacts[0]["title"] == "code"
    assert result.source_result_ids == ["source"] and result.data_origin == "fixture"
    assert calls == [{"code": "print(1)", "input_result_ids": ["source"]}]


@pytest.mark.asyncio
async def test_legacy_python_table_is_readable_by_facts_and_render_view(monkeypatch):
    from harness.tools import wafer_set_facts as facts, wafer_set_python as python, wafer_set_views as views
    from harness.wafer_set_sources import SourceBatch

    rows = [{"lot_id": "L1", "wf_id": 1}]
    async def legacy(args, ctx):
        return ToolResult(tables=[ResultTable(table_id="members", title="Members", rows=rows,
            columns=list(rows[0]), complete=True)], status="success", data_origin="fixture")
    ctx = context()
    result = await python.dispatch_python(python.CompatiblePythonInput(code="emit",
        input_result_ids=["source"]), ctx, legacy_handler=legacy)
    ctx.store.add("python", result)
    async def inline(function, *args, **kwargs): return function(*args, **kwargs)
    monkeypatch.setattr(facts, "run_blocking", inline)
    monkeypatch.setattr(facts, "fetch_source", lambda *args: SourceBatch(
        [{"lot_id": "L1", "wf_id": 1, "value": 3}], 1, 0, 0, 0, 10, True, None))
    read = await facts.read_facts(facts.ReadFactsInput(source_id="wafer_parameter",
        members={"result_id": "python", "table_id": "members"}), ctx)
    rendered = await views.render_view(views.RenderViewInput(
        source={"result_id": "python", "table_id": "members"}, view_type="pivot_table"), ctx)
    assert read.tables[0].rows[0]["lot_id"] == "L1"
    assert rendered.tables[0].rows[0]["source_table_id"] == "members"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "handle"])
async def test_python_error_summary_starts_with_exception_and_is_model_visible(monkeypatch, path):
    from harness.tools import wafer_set_python as tool
    from harness.types import ToolObservation

    failure = {"status": "error", "tables": [], "plots": [], "stdout": "", "stderr": "",
        "error": {"type": "ZeroDivisionError", "message": "division by zero"}}
    if path == "legacy":
        async def legacy(args, ctx):
            return ToolResult(tables=[ResultTable(table_id="output", title="Python output",
                rows=[{"error": failure["error"]}], columns=["error"], complete=False,
                missing_reason="Python execution did not complete")], summary="None", status="error")
        result = await tool.dispatch_python(tool.CompatiblePythonInput(code="1/0"), context(),
            legacy_handler=legacy)
    else:
        monkeypatch.setattr(tool, "_runtime", lambda ctx: Runtime(failure))
        result = await tool.dispatch_python(tool.CompatiblePythonInput(code="1/0",
            sources=[{"result_id": "source", "table_id": "chosen"}]), context(),
            legacy_handler=None)
    assert result.status == "error"
    assert result.summary.startswith("ZeroDivisionError: division by zero")
    observation = ToolObservation(principal_id="owner", session_id="session", run_id="run",
        invocation_id="call", result_id="result", tool_name="run_python", status="error",
        summary=result.summary)
    assert observation.model_view()["summary"].startswith("ZeroDivisionError: division by zero")


def test_flag_off_keeps_legacy_python_registration(monkeypatch):
    from harness.tools.python_tools import PythonInput, run_python
    from harness.tools.registry import domain_registry

    monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS", raising=False)
    spec = domain_registry().tools["run_python"]
    assert spec.schema is PythonInput and spec.handler is run_python


NO_WAFER_HANDLE_CASES = [
    ("list_values", {"source": {"result_id": "python", "table_id": "numbers"}, "field": "lot_id"}),
    ("inspect_coverage", {"source": {"result_id": "python", "table_id": "numbers"},
        "columns": ["lot_id"]}),
    ("read_facts", {"source_id": "wafer_parameter",
        "members": {"result_id": "python", "table_id": "numbers"}}),
    ("join_facts", {"left": {"result_id": "python", "table_id": "numbers"},
        "right": {"result_id": "python", "table_id": "numbers"}, "relation_id": "wafer_identity",
        "expected_cardinality": "one_to_one"}),
    ("filter_set", {"source": {"result_id": "python", "table_id": "numbers"},
        "predicate": {"items": [{"column": "lot_id", "op": "eq", "value": "L"}]},
        "key_columns": ["lot_id"]}),
    ("make_cohorts", {"source": {"result_id": "python", "table_id": "numbers"},
        "key_columns": ["lot_id"], "group_by": ["lot_id"]}),
    ("rank_members", {"source": {"result_id": "python", "table_id": "numbers"},
        "order_by": "yield_percent", "direction": "desc", "top_n": 1}),
    ("compute_metrics", {"source": {"result_id": "python", "table_id": "numbers"},
        "metrics": [{"metric_id": "yield", "mode": "stored_selection", "quantity": "pass_rate"}]}),
    ("aggregate_metrics", {"sources": [{"result_id": "python", "table_id": "numbers"}],
        "operation": "mean"}),
    ("reduce_maps", {"source": {"result_id": "python", "table_id": "numbers"},
        "metrics": [{"metric_id": "yield", "mode": "bin", "quantity": "pass_rate",
                     "bin_side": "left_bin", "pass_bin": "1"}]}),
    ("compare_metrics", {"baseline": {"result_id": "python", "table_id": "numbers"},
        "current": {"result_id": "python", "table_id": "numbers"}, "keys": ["lot_id"],
        "value_column": "number"}),
    ("render_maps", {"source": {"result_id": "python", "table_id": "numbers"},
        "view": "bin_value"}),
    ("render_view", {"source": {"result_id": "python", "table_id": "numbers"},
        "view_type": "chart", "chart": {"kind": "scatter", "x": "lot_id", "y": "wf_id"}}),
    pytest.param("export_report", {"sources": [{"result_id": "python", "table_id": "numbers"}]},
        marks=pytest.mark.xfail(strict=True, reason="export_report intentionally exports generic tables")),
    pytest.param("read_result", {"source": {"result_id": "python", "table_id": "numbers"}},
        marks=pytest.mark.xfail(strict=True, reason="read_result intentionally reads generic tables")),
    pytest.param("run_python", {"sources": [{"result_id": "python", "table_id": "numbers"}],
        "code": "print(len(df))"}, marks=pytest.mark.xfail(strict=True,
        reason="run_python intentionally analyzes generic tables")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,arguments", NO_WAFER_HANDLE_CASES,
                         ids=[case.values[0] if hasattr(case, "values") else case[0]
                              for case in NO_WAFER_HANDLE_CASES])
async def test_no_wafer_columns_are_not_silently_treated_as_wafers(
        monkeypatch, tool_name, arguments):
    from harness.runtime.domain_process import DomainError
    from harness.tools import python_tools, wafer_set_export, wafer_set_python
    from harness.tools.registry import domain_registry

    ctx = context()
    monkeypatch.setattr(python_tools, "ProcessRuntime", lambda executable: Runtime({
        "status": "success", "tables": [{"table_id": "numbers", "title": "Numbers",
            "rows": [{"number": 1}], "columns": ["number"]}], "plots": [],
        "stdout": "", "stderr": "", "error": None}))
    output = await wafer_set_python.dispatch_python(wafer_set_python.CompatiblePythonInput(
        code="emit_table(pd.DataFrame({'number': [1]}), 'numbers')"), ctx,
        legacy_handler=python_tools.run_python)
    ctx.store.add("python", output)
    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    monkeypatch.setattr(wafer_set_python, "_runtime", lambda ctx: Runtime(
        {"status": "success", "tables": [], "plots": [], "stdout": "1\n", "stderr": "", "error": None}))
    async def inline(function, *args, **kwargs): return function(*args, **kwargs)
    report = io.BytesIO()
    with zipfile.ZipFile(report, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<slide/>")
    monkeypatch.setattr(wafer_set_export, "run_blocking", inline)
    monkeypatch.setattr(wafer_set_export, "build_report_sections", lambda *args: [{
        "result_ids": ["python"], "analysis_status": "not_run", "kind": "table",
        "title": "Numbers", "table_ids": ["numbers"], "content": {"rows": [{"number": 1}]}}])
    monkeypatch.setattr(wafer_set_export, "_render_ppt", lambda state: report.getvalue())
    with pytest.raises((ValueError, DomainError)) as caught:
        await domain_registry().invoke(tool_name, arguments, ctx)
    message = str(caught.value).lower()
    assert any(word in message for word in
               ("column", "field", "identity", "required", "payload", "compute_metrics", "key"))


def test_python_contract_rejects_mixed_paths_and_duplicate_units():
    from harness.tools.wafer_set_python import CompatiblePythonInput

    with pytest.raises(ValueError):
        CompatiblePythonInput(code="x", input_result_ids=["a"],
            sources=[{"result_id": "a", "table_id": "t"}])
    with pytest.raises(ValueError):
        CompatiblePythonInput(code="x", sources=[{"result_id": "a", "table_id": "t"}],
            output_units=[{"table_id": "t", "column": "x", "unit": "u"},
                          {"table_id": "t", "column": "x", "unit": "u"}])
