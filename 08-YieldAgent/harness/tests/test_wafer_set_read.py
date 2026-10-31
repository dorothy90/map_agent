import json
from types import SimpleNamespace

import pytest

from harness.types import ResultTable
from harness.tools.registry import ToolResult


class Store:
    def __init__(self, result):
        self.result = result

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ("owner", "session", "source"):
            raise PermissionError("Result unavailable")
        table = self.result.tables[0]
        return SimpleNamespace(scope=self.result.scope, status="success", provenance={"data_origin": "fixture"},
            tables=[SimpleNamespace(**table.model_dump())])

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return json.loads(json.dumps(self.result.tables[0].rows))

    async def read_result(self, principal, session, **kwargs):
        rows = await self.rows(principal, session, kwargs["result_id"], kwargs.get("table_id"))
        offset, limit = kwargs["offset"], kwargs["limit"]
        return {"result_id": "source", "table_id": "facts", "title": "Facts", "columns": ["n"],
            "units": {}, "rows": rows[offset:offset + limit], "offset": offset, "total_rows": len(rows),
            "complete": True, "missing_reason": None, "truncated": offset > 0 or offset + limit < len(rows),
            "scope": self.result.scope, "provenance": {"data_origin": "fixture"}}


def fixture_result():
    from harness.wafer_set_core import table_version

    rows = [{"n": i} for i in range(5)]
    return ToolResult(tables=[ResultTable(table_id="facts", title="Facts", rows=rows,
        columns=["n"], complete=True)], scope={"handle_contract": "wafer-table/v1",
        "table_versions": {"facts": table_version(rows)}})


def context(result):
    return SimpleNamespace(store=Store(result), run={"principal_id": "owner", "session_id": "session"})


@pytest.mark.asyncio
async def test_handle_page_has_own_version_and_meta():
    from harness.tools import wafer_set_read as tool
    from harness.wafer_set_core import table_version

    source = fixture_result()
    original = source.scope["table_versions"]["facts"]
    args = tool.CompatibleReadInput(source={"result_id": "source", "table_id": "facts", "version": original},
                                    offset=1, limit=2)
    result = await tool.read_result(args, context(source))
    page = result.tables[0]
    assert page.table_id == "result_page" and page.rows == [{"n": 1}, {"n": 2}]
    assert result.scope["table_versions"]["result_page"] == table_version(page.rows)
    assert result.scope["table_versions"]["result_page"] != original
    assert result.tables[1].rows[0]["source_version"] == original
    assert not page.complete and result.status == "partial"


@pytest.mark.asyncio
async def test_handle_page_byte_limit_and_column_validation(monkeypatch):
    from harness.tools import wafer_set_read as tool

    source = fixture_result()
    monkeypatch.setattr(tool, "PAGE_BYTES", 20)
    result = await tool.read_result(tool.CompatibleReadInput(
        source={"result_id": "source", "table_id": "facts"}, limit=5), context(source))
    assert len(result.tables[0].rows) < 5
    assert result.tables[1].rows[0]["next_offset"] is not None
    with pytest.raises(ValueError, match="column"):
        await tool.read_result(tool.CompatibleReadInput(
            source={"result_id": "source", "table_id": "facts"}, columns=["bad"]), context(source))


@pytest.mark.asyncio
async def test_source_incomplete_and_page_limitations_are_distinct():
    from harness.tools import wafer_set_read as tool

    source = fixture_result()
    source.tables[0].complete = False
    source.tables[0].missing_reason = "limited"
    full = await tool.read_result(tool.CompatibleReadInput(
        source={"result_id": "source", "table_id": "facts"}, limit=5), context(source))
    assert [item["code"] for item in full.scope["limitations"]] == ["SOURCE_INCOMPLETE"]
    page = await tool.read_result(tool.CompatibleReadInput(
        source={"result_id": "source", "table_id": "facts"}, limit=2), context(source))
    assert [item["code"] for item in page.scope["limitations"]] == ["SOURCE_INCOMPLETE", "PAGE_ONLY"]


def test_legacy_fields_keep_original_schema_metadata():
    from harness.tools.wafer_set_read import CompatibleReadInput
    from harness.tools.python_tools import ReadInput

    old = ReadInput.model_json_schema()["properties"]
    new = CompatibleReadInput.model_json_schema()["properties"]
    for field in ("result_id", "table_id", "offset", "limit", "columns"):
        assert new[field].get("description") == old[field].get("description")


@pytest.mark.asyncio
async def test_legacy_read_delegates_unchanged():
    from harness.tools import wafer_set_read as tool

    source = fixture_result()
    calls = []
    async def legacy(args, ctx):
        calls.append(args.model_dump())
        return ToolResult(summary="legacy")
    args = tool.CompatibleReadInput(result_id="source", table_id="facts", offset=2, limit=1)
    result = await tool.dispatch_read(args, context(source), legacy_handler=legacy, legacy_schema=tool.ReadInput)
    assert result.summary == "legacy"
    assert calls == [{"result_id": "source", "table_id": "facts", "offset": 2, "limit": 1, "columns": None}]


def test_compatible_input_requires_exactly_one_path():
    from harness.tools.wafer_set_read import CompatibleReadInput

    with pytest.raises(ValueError):
        CompatibleReadInput()
    with pytest.raises(ValueError):
        CompatibleReadInput(result_id="source", source={"result_id": "source", "table_id": "facts"})

