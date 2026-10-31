from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace
import zipfile

import pytest

from harness.tools.wafer_set_tools import _version


class ChainStore:
    def __init__(self):
        self.items = {}
        self.artifacts = {}

    def add(self, result_id, result, tool_name):
        refs = [SimpleNamespace(table_id=table.table_id, title=table.title,
            columns=table.columns, units=table.units, complete=table.complete,
            missing_reason=table.missing_reason, total_rows=len(table.rows)) for table in result.tables]
        artifact_refs = []
        for index, artifact in enumerate(result.artifacts):
            artifact_id = f"{result_id}-a{index}"
            ref = {"artifact_id": artifact_id, "title": artifact["title"], "mime": artifact["mime"]}
            artifact_refs.append(ref)
            data = artifact["data"].encode() if isinstance(artifact["data"], str) else artifact["data"]
            self.artifacts[artifact_id] = (ref, data)
        self.items[result_id] = (SimpleNamespace(result_id=result_id, tool_name=tool_name,
            summary=result.summary, status=result.status or "success", scope=copy.deepcopy(result.scope),
            tables=refs, artifact_refs=artifact_refs, provenance={"data_origin": "fixture"}),
            {table.table_id: copy.deepcopy(table.rows) for table in result.tables})

    async def observation(self, principal, session, result_id):
        if (principal, session) != ("owner", "session") or result_id not in self.items:
            raise PermissionError("Result unavailable")
        return self.items[result_id][0]

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return copy.deepcopy(self.items[result_id][1][table_id])

    async def artifact(self, principal, session, artifact_id):
        if (principal, session) != ("owner", "session") or artifact_id not in self.artifacts:
            raise PermissionError("Artifact unavailable")
        return self.artifacts[artifact_id]


def ppt_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<slide/>")
    return output.getvalue()


def test_family_manifest_is_exact_and_live_safe():
    from harness.tools.wafer_set_register import (WAFER_SET_TOOL_NAMES,
        LIVE_EVAL_TOOL_NAMES, TOOL_EFFECTS)

    assert WAFER_SET_TOOL_NAMES == {
        "describe_sources", "list_values", "inspect_coverage", "select_wafers",
        "read_facts", "join_facts", "filter_set", "make_cohorts", "rank_members",
        "compute_metrics", "aggregate_metrics", "reduce_maps", "compare_metrics",
        "render_maps", "render_view", "export_report", "read_result", "run_python",
    }
    assert WAFER_SET_TOOL_NAMES - LIVE_EVAL_TOOL_NAMES == {"export_report", "run_python"}
    assert TOOL_EFFECTS["render_view"] == "artifact"
    assert TOOL_EFFECTS["export_report"] == TOOL_EFFECTS["run_python"] == "artifact"


def test_opt_in_registry_has_36_unique_names_and_compatible_effects(monkeypatch):
    from harness.tools.registry import domain_registry

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    tools = domain_registry().tools
    assert len(tools) == 36
    assert len(tools) == len(set(tools))
    assert tools["render_view"].effect == "artifact"
    assert tools["export_report"].effect == "artifact"
    assert tools["run_python"].effect == "artifact"


@pytest.mark.asyncio
async def test_aggregate_render_export_handle_chain(monkeypatch):
    from harness.types import ResultTable
    from harness.tools.registry import ToolResult
    from harness.tools import wafer_set_aggregate, wafer_set_export, wafer_set_views

    store = ChainStore()
    metric_rows = [{"metric_id": "yield", "definition_id": "d", "value": value,
        "numerator": numerator, "denominator": denominator, "unit": "percent",
        "measurement_ref": name, "member_id": name, "lot_id": name, "wf_id": 1,
        "better_when": "higher", "aggregation_level": "measurement"}
        for name, value, numerator, denominator in (("a", 50, 1, 2), ("b", 100, 8, 8))]
    source = ToolResult(tables=[ResultTable(table_id="metrics", title="Metrics", rows=metric_rows,
        columns=list(metric_rows[0]), complete=True)], scope={"handle_contract": "wafer-table/v1",
        "table_versions": {"metrics": _version(metric_rows)}})
    store.add("source", source, "compute_metrics")
    ctx = SimpleNamespace(store=store, run={"principal_id": "owner", "session_id": "session"})
    aggregate = await wafer_set_aggregate.aggregate_metrics(
        wafer_set_aggregate.AggregateMetricsInput(sources=[{"result_id": "source",
            "table_id": "metrics"}], operation="ratio_of_sums"), ctx)
    assert aggregate.tables[0].rows[0]["value"] == 90
    store.add("aggregate", aggregate, "aggregate_metrics")
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(wafer_set_views, "run_blocking", inline)
    view = await wafer_set_views.render_view(wafer_set_views.RenderViewInput(
        source={"result_id": "aggregate", "table_id": "aggregates"}, view_type="chart",
        chart={"kind": "bar", "x": "metric_id", "y": "value"}), ctx)
    assert view.artifacts[0]["data"].startswith(b"\x89PNG")
    store.add("view", view, "render_view")
    monkeypatch.setattr(wafer_set_export, "run_blocking", inline)
    monkeypatch.setattr(wafer_set_export, "_render_ppt", lambda state: ppt_bytes())
    exported = await wafer_set_export.export_report(wafer_set_export.CompatibleExportInput(
        sources=[{"result_id": "aggregate", "table_id": "aggregates"}],
        artifacts=[{"result_id": "view", "artifact_ids": ["view-a0"]}]), ctx)
    assert exported.tables[0].rows[-1]["slide_count"] == 1
    assert exported.source_result_ids == ["aggregate", "view"]
