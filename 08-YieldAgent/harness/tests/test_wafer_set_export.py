from __future__ import annotations

import copy
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import pytest

from harness.types import ResultTable
from harness.tools.registry import ToolResult
from harness.tools.wafer_set_tools import _version


def ppt_bytes(slides=1):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for index in range(1, slides + 1):
            archive.writestr(f"ppt/slides/slide{index}.xml", "<slide/>")
    return output.getvalue()


class Store:
    def __init__(self):
        rows = [{"n": 1}, {"n": 2}]
        self.rows_by_table = {"keep": rows, "drop": [{"secret": "unused"}]}
        self.artifacts = {"a1": ({"artifact_id": "a1", "title": "chart", "mime": "image/png"}, b"png")}
        self.obs = SimpleNamespace(result_id="source", tool_name="source_tool", summary="summary",
            status="success", scope={"handle_contract": "wafer-table/v1", "complete": True,
                "table_versions": {key: _version(value) for key, value in self.rows_by_table.items()}},
            tables=[ResultTable(table_id="keep", title="Keep", rows=rows, columns=["n"], complete=True),
                ResultTable(table_id="drop", title="Drop", rows=self.rows_by_table["drop"], columns=["secret"], complete=True)],
            artifact_refs=[{"artifact_id": "a1", "title": "chart", "mime": "image/png"}],
            provenance={"data_origin": "fixture"})

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ("owner", "session", "source"):
            raise PermissionError("Result unavailable")
        return self.obs

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return json.loads(json.dumps(self.rows_by_table[table_id]))

    async def artifact(self, principal, session, artifact_id):
        if (principal, session) != ("owner", "session") or artifact_id not in self.artifacts:
            raise PermissionError("Artifact unavailable")
        return self.artifacts[artifact_id]


def context():
    return SimpleNamespace(store=Store(), run={"principal_id": "owner", "session_id": "session"})


@pytest.mark.asyncio
async def test_handle_export_includes_only_selected_content_and_manifest(monkeypatch):
    from harness.tools import wafer_set_export as tool

    captured = {}
    def sections(observations, tables, artifacts):
        captured.update(observations=copy.deepcopy(observations), tables=copy.deepcopy(tables),
                        artifacts=copy.deepcopy(artifacts))
        return [{"result_ids": ["source"], "analysis_status": "not_run", "kind": "table",
            "title": "Keep", "table_ids": ["keep"], "content": {"rows": tables["source"]["keep"]}}]
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(tool, "build_report_sections", sections)
    monkeypatch.setattr(tool, "run_blocking", inline)
    monkeypatch.setattr(tool, "_render_ppt", lambda state: ppt_bytes())
    result = await tool.export_report(tool.CompatibleExportInput(
        sources=[{"result_id": "source", "table_id": "keep"}],
        artifacts=[{"result_id": "source", "artifact_ids": ["a1"]}], title="Report"), context())
    assert set(captured["tables"]["source"]) == {"keep"}
    assert [t.table_id for t in captured["observations"][0].tables] == ["keep"]
    assert [a["artifact_id"] for a in captured["observations"][0].artifact_refs] == ["a1"]
    manifest = result.tables[0].rows
    assert {row["item_type"] for row in manifest} == {"table", "artifact", "output"}
    output = manifest[-1]
    assert output["output_sha256"] == hashlib.sha256(result.artifacts[0]["data"]).hexdigest()
    assert output["slide_count"] == 1
    assert result.source_result_ids == ["source"]


@pytest.mark.asyncio
async def test_export_rejects_unowned_artifact_and_raw_payload(monkeypatch):
    from harness.tools import wafer_set_export as tool

    ctx = context()
    with pytest.raises(ValueError, match="artifact"):
        await tool.export_report(tool.CompatibleExportInput(
            sources=[{"result_id": "source", "table_id": "keep"}],
            artifacts=[{"result_id": "source", "artifact_ids": ["missing"]}]), ctx)
    ctx.store.rows_by_table["map_payloads"] = [{"map_val_json": "{}"}]
    ctx.store.obs.tables.append(ResultTable(table_id="map_payloads", title="Raw", rows=[{"map_val_json": "{}"}],
        columns=["map_val_json"], complete=True))
    ctx.store.obs.scope["table_versions"]["map_payloads"] = _version(ctx.store.rows_by_table["map_payloads"])
    with pytest.raises(ValueError, match="payload"):
        await tool.export_report(tool.CompatibleExportInput(
            sources=[{"result_id": "source", "table_id": "map_payloads"}]), ctx)


@pytest.mark.asyncio
async def test_legacy_export_delegates_unchanged():
    from harness.tools import wafer_set_export as tool

    calls = []
    async def legacy(args, ctx):
        calls.append(args.model_dump())
        return ToolResult(summary="legacy")
    args = tool.CompatibleExportInput(result_ids=["source"], title="Old")
    result = await tool.dispatch_export(args, context(), legacy_handler=legacy, legacy_schema=tool.ExportInput)
    assert result.summary == "legacy"
    assert calls == [{"result_ids": ["source"], "title": "Old"}]


def test_export_requires_exactly_one_path():
    from harness.tools.wafer_set_export import CompatibleExportInput

    with pytest.raises(ValueError):
        CompatibleExportInput()
    with pytest.raises(ValueError):
        CompatibleExportInput(result_ids=["a"], sources=[{"result_id": "a", "table_id": "t"}])
