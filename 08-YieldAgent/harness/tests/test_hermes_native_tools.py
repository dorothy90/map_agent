import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.tools.registry import domain_registry
from harness.types import ToolObservation


@pytest.mark.parametrize("scope", [
    {"lot_population": {"table_id": "lot_population", "source_result_id": "captured"}},
    {"contract": "yield-report-scope/v1", "default_table_id": "lot_population"},
])
def test_report_population_values_require_reading_the_lot_table(scope):
    from harness.native_tools import domain_model_result
    from harness.types import ResultPage, TableRef

    score = {"lot_id": "sample-lot", "pt1h_mean": .625}
    lot = TableRef(table_id="lot_population", title="Lot Population", columns=list(score),
                   total_rows=1, preview_rows=[score], complete=True, data_ref="lot-rows")
    period = TableRef(table_id="yield_long", title="Period values", columns=["value"],
                      total_rows=1, preview_rows=[{"value": .82}], complete=True, data_ref="period-rows")
    report = ToolObservation(principal_id="p", session_id="s", run_id="r", invocation_id="i",
                             result_id="report", tool_name="render_yield_table", scope=scope, tables=[period, lot])
    response = domain_model_result(report)
    assert response["tables"][0]["preview_rows"] == [{"value": .82}]
    assert response["tables"][1]["table_id"] == "lot_population"
    assert response["tables"][1]["preview_rows"] == []
    assert response["tables"][1]["preview_policy"] == "read_result"
    assert ".625" not in json.dumps(response)
    assert report.tables[1].preview_rows == [score]

    page = report.model_copy(update=dict(tool_name="read_result", tables=[lot], columns=lot.columns,
        preview_rows=[score], page=ResultPage(table_id=lot.table_id, source_total_rows=1, offset=0, limit=20)))
    result = domain_model_result(page)
    assert result["preview_rows"] == [score]
    assert result["page"]["visible_rows"] == 1 and result["page"]["next_offset"] is None
    metadata = result["tables"][0]
    assert metadata["group_by"] == ["lot_id"]
    assert metadata["aggregations"]["pt1h_mean"] == "mean"
    assert metadata["units"]["pt1h_mean"] == "fraction"

    identities = [{"lot_id": score["lot_id"]}]
    selected = lot.model_copy(update=dict(columns=["lot_id"], preview_rows=identities))
    filtered = page.model_copy(update=dict(columns=selected.columns, preview_rows=identities, tables=[selected]))
    metadata = domain_model_result(filtered)["tables"][0]
    assert metadata["group_by"] == ["lot_id"]
    assert metadata["aggregations"] == {} and metadata["units"] == {}


def test_model_view_applies_declared_table_contract_without_a_tool_or_table_name_rule():
    from harness.types import TableRef

    rows = [{"site": "A", "load": 17}]
    table = TableRef(table_id="support", title="Load", columns=list(rows[0]), total_rows=1,
                     preview_rows=rows, complete=True, data_ref="saved")
    contract = {"group_by": ["site"], "aggregations": {"load": "sum"}, "units": {"load": "kW"},
                "preview_policy": "read_result"}
    result = ToolObservation(principal_id="p", session_id="s", run_id="r", invocation_id="i",
        result_id="r", tool_name="example", scope={"table_projections": {"support": contract}},
        tables=[table], columns=table.columns, preview_rows=rows, total_rows=1)
    view = result.model_view()
    assert view["preview_rows"] == [] and view["truncated"]
    assert view["tables"][0]["units"] == {"load": "kW"}
    assert view["tables"][0]["aggregations"] == {"load": "sum"}
    assert view["tables"][0]["group_by"] == ["site"]
    assert view["tables"][0]["preview_policy"] == "read_result"
    assert result.preview_rows == rows and result.scope["table_projections"]["support"] == contract


def test_native_registry_dispatch_uses_authenticated_executor_and_links(monkeypatch):
    from harness.native_tools import register_yield_tools

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "engine"))
    from tools.registry import ToolRegistry

    async def scenario():
        loop = asyncio.get_running_loop()
        registry = ToolRegistry()
        calls = []

        async def execute(call):
            assert asyncio.get_running_loop() is loop
            calls.append(call)
            return ToolObservation(
                principal_id="user", session_id="session", run_id="run", result_id="result",
                tool_name=call["name"], invocation_id=call["id"],
                artifact_refs=[{"artifact_id": "map", "mime": "text/html", "title": "wafer"}],
            )

        executor = SimpleNamespace(registry=domain_registry(), execute=execute)
        names = register_yield_tools(registry, executor, loop, invocation_id=lambda: "hermes-call")
        assert set(names) == set(registry.get_tool_names_for_toolset("yield")) == {
            "list_tables", "query_db", "read_result", "render_yield_table", "load_wafer_maps",
        }
        args = {"result_id": "previous"}
        response = json.loads(await asyncio.to_thread(registry.dispatch, "read_result", args))
        assert calls == [{"name": "read_result", "id": "hermes-call", "args": args}]
        assert response["artifact_refs"][0]["href"] == "/harness/artifacts/map?session_id=session"
        assert "principal_id" not in response
        with pytest.raises(ValueError, match="already registered"):
            register_yield_tools(registry, executor, loop)

        async def failed(call):
            raise ConnectionError("database connection details")

        executor.execute = failed
        error = json.loads(await asyncio.to_thread(registry.dispatch, "read_result", args))
        assert error == {"status": "error", "error": "ConnectionError"}

        subset = ToolRegistry()
        assert register_yield_tools(subset, executor, loop, allowed=set()) == ["read_result"]
        assert subset.get_entry("query_db") is None
        executor.registry.tools["query_db"].effect = "persistent_write"
        assert register_yield_tools(ToolRegistry(), executor, loop, allowed={"query_db"}) == ["read_result"]
        with pytest.raises(ValueError, match="unknown"):
            register_yield_tools(ToolRegistry(), executor, loop, allowed={"unregistered_tool"})

    asyncio.run(scenario())
