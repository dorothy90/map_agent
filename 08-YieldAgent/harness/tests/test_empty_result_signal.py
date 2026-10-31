import asyncio
import json
import pytest


def _table(table_id, rows):
    from harness.types import ResultTable

    return ResultTable(table_id=table_id, title=table_id, rows=rows, complete=True)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param({"rows": []}, "ZERO", id="empty-rows"),
        pytest.param({"rows": [{"items": []}]}, "ZERO", id="empty-list-wrapper"),
        pytest.param({"rows": [{"items": [], "warnings": [], "limitations": []}]}, "ZERO",
                     id="empty-list-wrapper-with-metadata"),
        pytest.param({"rows": [{"count": 0}]}, "ZERO", id="count-zero"),
        pytest.param({"rows": [{"rows": 0}]}, "ZERO", id="rows-count-zero"),
        pytest.param({"rows": [{"member_count": 0}]}, "ZERO", id="map-handle-member-count-zero"),
        pytest.param({"rows": [{"handle": "h", "members": [], "warnings": []}]}, "ZERO",
                     id="map-handle-empty-members"),
        pytest.param({"tables": [_table("primary", [])]}, "ZERO", id="empty-table"),
        pytest.param({"rows": [{"value": 0}]}, "NONZERO", id="metric-value-zero"),
        pytest.param({"rows": [{"enabled": False, "note": None}]}, "NONZERO", id="false-null-cells"),
        pytest.param(
            {"tables": [_table("primary", [{"value": 1}]), _table("audit", [])]},
            "NONZERO",
            id="empty-auxiliary-table",
        ),
        pytest.param({"artifacts": [{"title": "report", "data": b"x"}]}, "UNKNOWN", id="artifact-only"),
        pytest.param({"rows": [{"warnings": [], "limitations": []}]}, "NONZERO",
                     id="metadata-row-is-still-a-row"),
        pytest.param({"rows": [{"value": 1}]}, "NONZERO", id="non-empty"),
    ],
)
def test_classify_primary_structure_cases(result, expected):
    from harness.primary_result import PrimaryResult, classify_primary
    from harness.tools.registry import ToolResult

    assert classify_primary(ToolResult(**result)) is PrimaryResult[expected]


@pytest.mark.parametrize(
    ("tool_name", "tables", "artifacts", "expected"),
    [
        ("describe_sources", [_table("sources", []), _table("columns", [{"column": "lot_id"}])], [], "ZERO"),
        ("list_values", [_table("values", []), _table("value_summary", [{"count": 0}])], [], "ZERO"),
        ("inspect_coverage", [_table("coverage", [{"check": "rows", "denominator": 0, "value": None}])], [], "ZERO"),
        ("select_wafers", [_table("wafers", []), _table("selection_summary", [{"wafer_count": 0}])], [], "ZERO"),
        ("read_facts", [_table("facts", []), _table("read_audit", [{"returned_rows": 0}])], [], "ZERO"),
        ("join_facts", [_table("joined", []), _table("join_audit", [{"output_rows": 0}])], [], "ZERO"),
        ("filter_set", [_table("members", []), _table("filter_audit", [{"output_rows": 0}])], [], "ZERO"),
        ("make_cohorts", [_table("cohorts", [{"cohort_id": "c", "member_count": 0}]), _table("memberships", [])], [], "ZERO"),
        ("rank_members", [_table("ranked", []), _table("selected", [])], [], "ZERO"),
        ("compute_metrics", [_table("metrics", []), _table("metric_definitions", [{"metric_id": "yield"}])], [], "ZERO"),
        ("aggregate_metrics", [_table("aggregates", []), _table("aggregation_audit", [{"output_rows": 0}])], [], "ZERO"),
        ("reduce_maps", [_table("map_cells", []), _table("map_summary", [{"cell_count": 0}])], [], "ZERO"),
        ("compare_metrics", [_table("comparisons", []), _table("comparison_audit", [{"output_rows": 0}])], [], "ZERO"),
        ("render_maps", [_table("rendered_wafers", [])], [], "ZERO"),
        ("render_view", [_table("render_manifest", [{"rows_used": 0, "artifact_index": 0}])], [{"title": "empty view", "data": b"png"}], "ZERO"),
        ("export_report", [_table("export_manifest", [{"item_type": "output", "output_artifact_index": 0}])], [{"title": "report", "data": b"pptx"}], "NONZERO"),
        ("read_result", [_table("result_page", []), _table("page_meta", [{"returned_rows": 0}])], [], "ZERO"),
        ("run_python", [_table("output", []), _table("execution_manifest", [{"output_table_count": 1}])], [{"title": "code", "data": b"pass"}], "ZERO"),
    ],
)
def test_classifier_covers_each_wafer_set_tool_shape(tool_name, tables, artifacts, expected):
    from harness.primary_result import PrimaryResult, classify_primary
    from harness.tools.registry import ToolResult

    result = ToolResult(tables=tables, artifacts=artifacts, summary=tool_name)
    assert classify_primary(result) is PrimaryResult[expected]


def test_python_log_only_is_unknown_and_empty_export_manifest_is_zero():
    from harness.primary_result import PrimaryResult, classify_primary
    from harness.tools.registry import ToolResult

    log_only = ToolResult(tables=[
        _table("output", [{"stdout": "", "stderr": "", "error": None}]),
        _table("execution_manifest", [{"output_table_count": 0}]),
    ])
    empty_export = ToolResult(tables=[_table("export_manifest", [])])
    assert classify_primary(log_only) is PrimaryResult.UNKNOWN
    assert classify_primary(empty_export) is PrimaryResult.ZERO


def test_contradictory_python_manifest_and_invalid_coverage_are_unknown():
    from harness.primary_result import PrimaryResult, classify_primary
    from harness.tools.registry import ToolResult

    contradictory = ToolResult(tables=[
        _table("output", []),
        _table("execution_manifest", [{"output_table_count": 2}]),
    ])
    invalid_coverage = ToolResult(tables=[
        _table("coverage", [{"check": "rows", "value": None}]),
    ])
    assert classify_primary(contradictory) is PrimaryResult.UNKNOWN
    assert classify_primary(invalid_coverage) is PrimaryResult.UNKNOWN


def _observation(*, status="success", provenance=None):
    from harness.types import ToolObservation

    return ToolObservation(
        principal_id="p",
        session_id="s",
        run_id="run",
        invocation_id="call",
        result_id="result",
        tool_name="select_wafers",
        status=status,
        provenance=provenance or {},
    )


@pytest.mark.parametrize("status", ["success", "partial", "empty"])
def test_model_view_exposes_empty_signal_only_for_enabled_wafer_set(monkeypatch, status):
    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    view = _observation(status=status,
                        provenance={"tool_group": "wafer_set", "empty_result": True}).model_view()
    assert view["empty_result"] is True
    assert view["empty_result_message"] == "결과 0건"


@pytest.mark.parametrize("status", ["error", "cancelled"])
def test_model_view_excludes_empty_signal_for_unsuccessful_statuses(monkeypatch, status):
    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    view = _observation(status=status, provenance={"tool_group": "wafer_set", "empty_result": True}).model_view()
    assert "empty_result" not in view
    assert "empty_result_message" not in view


def test_legacy_group_and_flag_off_model_views_are_unchanged(monkeypatch):
    baseline = _observation(provenance={"data_origin": "fixture"}).model_view()
    baseline_bytes = json.dumps(baseline, ensure_ascii=False, separators=(",", ":")).encode()

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    legacy = _observation(provenance={"data_origin": "fixture", "tool_group": "legacy", "empty_result": True})
    assert json.dumps(legacy.model_view(), ensure_ascii=False, separators=(",", ":")).encode() == baseline_bytes

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "0")
    wafer = _observation(provenance={"data_origin": "fixture", "tool_group": "wafer_set", "empty_result": True})
    assert json.dumps(wafer.model_view(), ensure_ascii=False, separators=(",", ":")).encode() == baseline_bytes


def test_old_observation_without_empty_result_still_validates():
    from harness.types import ToolObservation

    old = _observation().model_dump(mode="json")
    old["provenance"] = {"data_origin": "live"}
    assert ToolObservation.model_validate(old).model_view()["data_origin"] == "live"


def test_executor_stores_zero_judgement_only_for_wafer_set(monkeypatch):
    import harness.executor as executor_module
    from harness.config import Settings
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.tools.registry import ToolRegistry, ToolResult, ToolSpec
    from harness.types import Contract, ToolObservation

    class Input(Contract):
        pass

    class Collection:
        async def find_one(self, query):
            return None

        async def count_documents(self, query):
            return 0

        async def update_one(self, query, update):
            return None

    class Store:
        def __init__(self):
            self.calls = Collection()
            self.runs = Collection()

        async def fence(self, run_id, epoch):
            return run

        async def claim_invocation(self, *args, **kwargs):
            return None

        async def reserve_tool_call(self, *args, **kwargs):
            return None

        async def event(self, *args, **kwargs):
            return None

        async def finish_invocation(self, *args, **kwargs):
            return None

        async def put_result(self, principal_id, session_id, observation, rows, artifacts, **kwargs):
            return ToolObservation(**observation, principal_id=principal_id, session_id=session_id,
                                   result_id=observation["invocation_id"])

    async def scenario():
        store = Store()
        registry = ToolRegistry()

        async def empty_handler(args, ctx):
            return ToolResult(tables=[_table("members", [])], status="success")

        async def error_handler(args, ctx):
            return ToolResult(tables=[_table("members", [])], status="error")

        async def cancelled_handler(args, ctx):
            return ToolResult(tables=[_table("members", [])], status="cancelled")

        registry.add(ToolSpec("wafer", "fixture", Input, empty_handler, group="wafer_set"))
        registry.add(ToolSpec("legacy", "fixture", Input, empty_handler, group="legacy"))
        registry.add(ToolSpec("error", "fixture", Input, error_handler, group="wafer_set"))
        registry.add(ToolSpec("cancelled", "fixture", Input, cancelled_handler, group="wafer_set"))
        executor = ToolExecutor(registry, ExecutionContext(store, Settings(), run))

        wafer = await executor.execute({"name": "wafer", "id": "wafer", "args": {}})
        legacy = await executor.execute({"name": "legacy", "id": "legacy", "args": {}})
        error = await executor.execute({"name": "error", "id": "error", "args": {}})
        cancelled = await executor.execute({"name": "cancelled", "id": "cancelled", "args": {}})
        assert wafer.provenance["tool_group"] == "wafer_set"
        assert wafer.provenance["empty_result"] is True
        assert "tool_group" not in legacy.provenance
        assert "empty_result" not in legacy.provenance
        assert "empty_result" not in error.provenance
        assert "empty_result" not in cancelled.provenance

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    calls = []
    real_classifier = executor_module.classify_primary
    monkeypatch.setattr(executor_module, "classify_primary",
                        lambda result: calls.append(result) or real_classifier(result))
    run = {"run_id": "run", "epoch": 1, "principal_id": "p", "session_id": "s", "usage": {}}
    asyncio.run(scenario())
    assert len(calls) == 1
