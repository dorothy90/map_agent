import json
from types import SimpleNamespace

import pytest

from harness.types import ResultTable
from harness.tools.registry import ToolResult


class Store:
    def __init__(self, result, *, status="success"):
        self.result = result
        self.status = status

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ("owner", "session", "source"):
            raise PermissionError("Result unavailable")
        return SimpleNamespace(
            scope=self.result.scope,
            tables=[SimpleNamespace(**table.model_dump()) for table in self.result.tables],
            status=self.status,
            provenance={"data_origin": "fixture"},
        )

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        table = next(table for table in self.result.tables if table.table_id == table_id)
        return json.loads(json.dumps(table.rows))


def context(result, *, status="success"):
    return SimpleNamespace(
        store=Store(result, status=status),
        run={"principal_id": "owner", "session_id": "session", "run_id": "run", "epoch": 3},
    )


def source_result(rows=None, *, complete=True, declared=None):
    from harness.wafer_set_core import table_version

    rows = rows or [{"value": 1}, {"value": 2}]
    version = table_version(rows) if declared is None else declared
    return ToolResult(
        tables=[ResultTable(table_id="facts", title="Facts", rows=rows, columns=["value"],
                            complete=complete, missing_reason=None if complete else "limited")],
        scope={"handle_contract": "wafer-table/v1", "table_versions": {"facts": version}},
    )


@pytest.mark.asyncio
async def test_resolve_handle_checks_owner_contract_status_and_versions():
    from harness.wafer_set_contracts import TableHandle
    from harness.wafer_set_core import resolve_handle, table_version

    result = source_result()
    handle = TableHandle(result_id="source", table_id="facts", version=table_version(result.tables[0].rows))
    resolved = await resolve_handle(context(result), handle)
    assert resolved.rows == result.tables[0].rows
    assert resolved.actual_version == handle.version
    assert resolved.complete

    with pytest.raises(ValueError, match="version"):
        await resolve_handle(context(result), handle.model_copy(update={"version": "wrong"}))
    with pytest.raises(ValueError, match="declared"):
        await resolve_handle(context(source_result(declared="wrong")),
                             handle.model_copy(update={"version": None}))
    bad = source_result()
    bad.scope["handle_contract"] = "other"
    with pytest.raises(ValueError, match="contract"):
        await resolve_handle(context(bad), handle)
    with pytest.raises(ValueError, match="status"):
        await resolve_handle(context(result, status="error"), handle)
    other = context(result)
    other.run["principal_id"] = "other"
    with pytest.raises(PermissionError):
        await resolve_handle(other, handle)


def test_build_result_versions_round_trip_and_provenance():
    from harness.wafer_set_core import build_result, table_version

    table = ResultTable(table_id="out", title="Output", rows=[{"x": "값", "n": 1}],
                        columns=["x", "n"], units={"n": "count"}, complete=False,
                        missing_reason="SOURCE_INCOMPLETE")
    result = build_result([table], source_result_ids=["source"],
                          limitations=[{"code": "SOURCE_INCOMPLETE", "table_id": "out"}])
    assert result.status == "partial"
    assert result.source_result_ids == ["source"]
    assert result.scope["handle_contract"] == "wafer-table/v1"
    assert result.scope["table_versions"]["out"] == table_version(json.loads(json.dumps(table.rows)))


def test_byte_lru_is_bounded_and_namespaced():
    from harness.wafer_set_cache import ByteLRU

    cache = ByteLRU(max_bytes=5, max_entries=2, ttl_seconds=60)
    cache.put(("p", "s", "r", 1, "a"), b"123")
    cache.put(("p", "s", "r", 1, "b"), b"456")
    assert cache.get(("p", "s", "r", 1, "a")) is None
    assert cache.get(("p", "s", "r", 1, "b")) == b"456"
    assert cache.get(("p", "s", "r", 2, "b")) is None

