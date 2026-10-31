from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from harness.types import ResultTable
from harness.tools.registry import ToolResult
from harness.tools.wafer_set_tools import _version


class Store:
    def __init__(self, result):
        self.result = result

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ("owner", "session", "source"):
            raise PermissionError("Result unavailable")
        return SimpleNamespace(result_id=result_id, status="success", scope=self.result.scope,
            tables=self.result.tables, provenance={"data_origin": "fixture"}, artifact_refs=[])

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return json.loads(json.dumps(next(t.rows for t in self.result.tables if t.table_id == table_id)))


def source_result(rows, *, complete=True):
    table = ResultTable(table_id="values", title="Values", rows=rows,
        columns=list(rows[0]) if rows else ["x", "y"], complete=complete,
        missing_reason=None if complete else "limited")
    return ToolResult(tables=[table], scope={"handle_contract": "wafer-table/v1",
        "table_versions": {"values": _version(rows)}})


def context(result):
    return SimpleNamespace(store=Store(result), run={"principal_id": "owner", "session_id": "session"})


@pytest.mark.asyncio
async def test_chart_preserves_source_and_emits_hashed_png(monkeypatch):
    from harness.tools import wafer_set_views as tool

    rows = [{"x": 1, "y": 2, "series": "a"}, {"x": 2, "y": 3, "series": "a"}]
    source = source_result(rows)
    original = copy.deepcopy(source.tables[0].rows)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", inline)
    result = await tool.render_view(tool.RenderViewInput(source={"result_id": "source",
        "table_id": "values"}, view_type="chart",
        chart={"kind": "line", "x": "x", "y": "y", "series": "series"}), context(source))
    assert source.tables[0].rows == original
    assert len(result.artifacts) == 1 and result.artifacts[0]["data"].startswith(b"\x89PNG")
    manifest = result.tables[0].rows[0]
    assert manifest["source_rows"] == manifest["rows_used"] == 2
    assert manifest["omitted_rows"] == 0
    assert manifest["artifact_content_sha256"] == tool.sha256_bytes(result.artifacts[0]["data"])
    assert result.scope["table_versions"]["render_manifest"] == _version(result.tables[0].rows)


@pytest.mark.asyncio
async def test_pivot_html_escapes_cells_and_has_no_external_content(monkeypatch):
    from harness.tools import wafer_set_views as tool

    source = source_result([{"name": "<script>alert(1)</script>", "value": 3}])
    result = await tool.render_view(tool.RenderViewInput(source={"result_id": "source",
        "table_id": "values"}, view_type="pivot_table", emit_html=True), context(source))
    html = result.artifacts[0]["data"]
    assert "&lt;script&gt;" in html and "<script" not in html.lower()
    assert "http://" not in html.lower() and "https://" not in html.lower()
    assert result.tables[0].rows[0]["rows_used"] == 1


@pytest.mark.asyncio
async def test_render_refuses_sampling_and_invalid_chart_columns(monkeypatch):
    from harness.tools import wafer_set_views as tool

    source = source_result([{"x": i, "y": i} for i in range(3)])
    with pytest.raises(ValueError, match="row limit"):
        await tool.render_view(tool.RenderViewInput(source={"result_id": "source",
            "table_id": "values"}, view_type="chart", max_rows=2,
            chart={"kind": "scatter", "x": "x", "y": "y"}), context(source))
    with pytest.raises(ValueError, match="column"):
        await tool.render_view(tool.RenderViewInput(source={"result_id": "source",
            "table_id": "values"}, view_type="chart",
            chart={"kind": "bar", "x": "x", "y": "missing"}), context(source))


def test_view_contract_requires_matching_configuration():
    from harness.tools.wafer_set_views import RenderViewInput

    with pytest.raises(ValueError):
        RenderViewInput(source={"result_id": "source", "table_id": "values"}, view_type="chart")
    with pytest.raises(ValueError):
        RenderViewInput(source={"result_id": "source", "table_id": "values"},
                        view_type="pivot_table", chart={"kind": "histogram", "x": "x"})
    with pytest.raises(ValueError):
        RenderViewInput(source={"result_id": "source", "table_id": "values"},
                        view_type="chart", chart={"kind": "heatmap", "x": "x"})
