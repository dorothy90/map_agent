import asyncio
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


class FakeCursor:
    def __init__(self, *, tables=(), columns=(), failure=None):
        self.tables = tables
        self.columns = columns
        self.failure = failure
        self.calls = []
        self.closed = False

    def execute(self, sql, binds=None):
        self.calls.append((sql, dict(binds or {})))
        if self.failure:
            raise self.failure
        self.result = list(self.columns) if "USER_TAB_COLUMNS" in sql else [
            (name,) for name in self.tables
        ]

    def fetchall(self):
        return list(self.result)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, **cursor_args):
        self.fake_cursor = FakeCursor(**cursor_args)
        self.call_timeout = None
        self.cancelled = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def run_list(monkeypatch, payload, **connection_args):
    import harness.tools.list_tables as module

    connection = FakeConnection(**connection_args)
    monkeypatch.setattr(module, "_connection", lambda: connection)
    result = asyncio.run(module.list_tables(
        module.ListTablesInput.model_validate(payload),
        SimpleNamespace(settings=SimpleNamespace(isolation=None)),
    ))
    return connection, result


def test_lists_tables_without_upload_or_isolation(monkeypatch):
    from harness.isolation.backend import IsolatedSSHBackend

    async def forbidden_upload(*args, **kwargs):
        raise AssertionError("catalog listing must not upload")

    monkeypatch.setattr(IsolatedSSHBackend, "upload_bytes", forbidden_upload)
    connection, result = run_list(
        monkeypatch, {}, tables=("LIVE_ONE", "LIVE_TWO")
    )

    rows = [{"TABLE_NAME": "LIVE_ONE"}, {"TABLE_NAME": "LIVE_TWO"}]
    assert result.tables[0].columns == ["TABLE_NAME"]
    assert result.tables[0].rows == rows
    assert result.scope["list_tables"] == {
        "columns": ["TABLE_NAME"], "rows": rows, "row_count": 2,
        "truncated": False,
    }
    assert result.summary == "결과 2건"
    assert connection.call_timeout == 60_000
    assert connection.fake_cursor.closed and connection.closed


def test_lists_columns_with_bound_table_name(monkeypatch):
    shaped = "x') OR 1=1 --"
    connection, result = run_list(
        monkeypatch, {"table": shaped},
        columns=(("ID", "NUMBER"), ("STAMP", "TIMESTAMP")),
    )

    sql, binds = connection.fake_cursor.calls[0]
    assert shaped not in sql
    assert binds == {"table_name": shaped}
    assert "JOIN USER_TABLES" in sql
    assert result.tables[0].rows == [
        {"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER"},
        {"COLUMN_NAME": "STAMP", "DATA_TYPE": "TIMESTAMP"},
    ]


@pytest.mark.parametrize("payload", [{}, {"table": "missing"}])
def test_empty_catalog_result(monkeypatch, payload):
    _, result = run_list(monkeypatch, payload)

    assert result.summary == "결과 0건"
    assert result.status == "empty"
    assert result.tables[0].rows == []
    assert result.scope["list_tables"]["rows"] == []


@pytest.mark.parametrize("payload", [
    {"mode": "list"}, {"table": ""}, {"columns": ["ID"]},
    {"filters": []}, {"order_by": []}, {"limit": 10},
])
def test_rejects_query_only_or_invalid_fields(payload):
    from harness.tools.list_tables import ListTablesInput

    with pytest.raises(ValidationError):
        ListTablesInput.model_validate(payload)


def test_query_failure_closes_resources(monkeypatch):
    import harness.tools.list_tables as module

    connection = FakeConnection(failure=RuntimeError("query failed"))
    monkeypatch.setattr(module, "_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="query failed"):
        asyncio.run(module.list_tables(module.ListTablesInput(), SimpleNamespace()))
    assert connection.fake_cursor.closed and connection.closed


def test_cancellation_interrupts_active_call(monkeypatch):
    import harness.tools.list_tables as module

    entered = threading.Event()
    released = threading.Event()
    connection = FakeConnection(tables=("ONE",))
    execute = connection.fake_cursor.execute

    def blocking_execute(sql, binds=None):
        entered.set()
        released.wait(2)
        if connection.cancelled:
            raise RuntimeError("cancelled")
        return execute(sql, binds)

    def cancel():
        connection.cancelled = True
        released.set()

    connection.fake_cursor.execute = blocking_execute
    connection.cancel = cancel
    monkeypatch.setattr(module, "_connection", lambda: connection)

    async def scenario():
        task = asyncio.create_task(module.list_tables(
            module.ListTablesInput(), SimpleNamespace()
        ))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if connection.closed:
                return
            await asyncio.sleep(.01)

    asyncio.run(scenario())
    assert connection.cancelled
    assert connection.fake_cursor.closed and connection.closed


def test_cancellation_during_connection_acquisition_closes_late_connection(monkeypatch):
    import harness.tools.list_tables as module

    acquired = threading.Event()
    release = threading.Event()
    connection = FakeConnection(tables=("ONE",))

    def late_connection():
        acquired.set()
        release.wait(2)
        return connection

    monkeypatch.setattr(module, "_connection", late_connection)

    async def scenario():
        task = asyncio.create_task(module.list_tables(
            module.ListTablesInput(), SimpleNamespace()
        ))
        assert await asyncio.to_thread(acquired.wait, 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if connection.closed:
                return
            await asyncio.sleep(.01)

    asyncio.run(scenario())
    assert connection.closed


def test_executor_store_bridge_returns_all_catalog_rows(monkeypatch):
    import harness.tools.list_tables as module
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.executor import ToolExecutor
    from harness.native_tools import domain_model_result
    from harness.tools.registry import ToolRegistry
    from harness.types import TableRef, ToolObservation

    run = {
        "principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1,
        "usage": {"active_seconds": 0},
    }

    class Collection:
        async def find_one(self, query):
            return None

        async def count_documents(self, query):
            return 0

        async def update_one(self, query, update):
            return None

    class Store:
        calls = Collection()
        runs = Collection()

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

        async def put_result(self, principal_id, session_id, observation, rows,
                             artifacts, *, tables=None):
            table = tables[0]
            return ToolObservation(
                **observation, principal_id=principal_id, session_id=session_id,
                result_id="store-result", columns=table.columns,
                preview_rows=table.rows[:10], total_rows=len(table.rows),
                tables=[TableRef(
                    table_id=table.table_id, title=table.title,
                    columns=table.columns, total_rows=len(table.rows),
                    preview_rows=table.rows[:10], complete=True,
                    data_ref="stored-blob",
                )],
            )

    names = tuple(f"TABLE_{index}" for index in range(25))
    monkeypatch.setattr(module, "_connection", lambda: FakeConnection(tables=names))
    registry = ToolRegistry()
    module.register(registry)
    executor = ToolExecutor(registry, ExecutionContext(Store(), Settings(), run))

    view = domain_model_result(asyncio.run(executor.execute({"name": "list_tables", "id": "c", "args": {}})))

    assert view["result_id"] == "store-result"
    assert view["row_count"] == 25
    assert view["rows"] == [{"TABLE_NAME": name} for name in names]


def test_query_db_model_schema_stays_query_only():
    from harness.tools.query_db import QueryDBInput, register
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register(registry)
    schema = registry.model_tools()[0]["function"]["parameters"]

    assert "table" in schema["required"]
    assert "mode" not in schema["properties"]
    with pytest.raises(ValidationError):
        QueryDBInput.model_validate({})
