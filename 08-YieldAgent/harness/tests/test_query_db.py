import asyncio
import csv
import io
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


class FakeCursor:
    def __init__(self, rows, columns=("ID", "NAME"), data_types=None):
        self.rows = rows
        self.columns = columns
        self.data_types = data_types or {}
        self.calls = []
        self.description = None
        self.closed = False

    def execute(self, sql, binds):
        self.calls.append((sql, dict(binds)))
        if "USER_TABLES" in sql:
            self._result = [("PEOPLE",)]
        elif "USER_TAB_COLUMNS" in sql:
            self._result = [
                (column, self.data_types.get(column, "VARCHAR2")) for column in self.columns
            ]
        else:
            self._result = list(self.rows)
            self.description = [(column,) for column in self.columns]
        return self

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def fetchmany(self, size):
        return list(self._result[:size])

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows, columns=("ID", "NAME"), data_types=None):
        self.fake_cursor = FakeCursor(rows, columns, data_types)
        self.closed = False
        self.cancelled = False
        self.call_timeout = None

    def cursor(self):
        return self.fake_cursor

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def context(tmp_path):
    from harness.config import IsolationSettings

    key = tmp_path / "key"
    known_hosts = tmp_path / "known_hosts"
    launcher = tmp_path / "launcher"
    for path in (key, known_hosts, launcher):
        path.write_text("fixture")
    home = tmp_path / "home"
    work_root = home / "work"
    analysis_venv = tmp_path / "venv"
    work_root.mkdir(parents=True)
    analysis_venv.mkdir()
    isolation = IsolationSettings(
        ssh_host="localhost", ssh_user="yieldrun", ssh_key=key,
        known_hosts=known_hosts, home=home, work_root=work_root,
        analysis_venv=analysis_venv, launcher=launcher, sandbox="auto",
    )
    return SimpleNamespace(
        settings=SimpleNamespace(isolation=isolation),
        run={"run_id": "run-1", "principal_id": "alice"},
    )


def run_query(
    monkeypatch, tmp_path, rows, payload, columns=("ID", "NAME"), data_types=None
):
    import harness.tools.query_db as query_db

    connection = FakeConnection(rows, columns, data_types)
    uploaded = {}

    async def upload(self, data, timeout=60):
        uploaded["data"] = data
        return str(self.results_dir / "query-fixture.csv")

    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.upload_bytes", upload)
    args = query_db.QueryDBInput.model_validate(payload)
    result = asyncio.run(query_db.query_db(args, context(tmp_path)))
    return connection, uploaded, result


def test_schema_rejects_unknown_fields_and_non_scalar_values():
    from harness.tools.query_db import QueryDBInput

    with pytest.raises(ValidationError):
        QueryDBInput.model_validate({"table": "people", "sql": "select 1 from dual"})
    with pytest.raises(ValidationError):
        QueryDBInput.model_validate({
            "table": "people",
            "filters": [{"column": "id", "op": "eq", "value": {"nested": 1}}],
        })
    value_schema = QueryDBInput.model_json_schema()["$defs"]["Comparison"]["properties"]["value"]
    assert {item["type"] for item in value_schema["anyOf"]} == {
        "string", "integer", "number", "boolean", "null",
    }


def test_metadata_allowlist_binds_values_and_orders_before_cap(monkeypatch, tmp_path):
    shaped = "x' OR 1=1 --"
    connection, _, result = run_query(monkeypatch, tmp_path, [(1, "Ada")], {
        "table": "people", "columns": ["name"],
        "filters": [{"column": "name", "op": "eq", "value": shaped}],
        "order_by": [{"column": "id", "direction": "desc"}], "limit": 1,
    })

    metadata, column_lookup, data = connection.fake_cursor.calls
    assert metadata[1] == {"table_name": "people"}
    assert column_lookup[1] == {"table_name": "PEOPLE"}
    assert shaped not in data[0]
    assert data[1]["value_0"] == shaped
    assert data[1]["row_limit"] == 2
    assert 'SELECT "NAME" FROM "PEOPLE"' in data[0]
    assert 'ORDER BY "ID" DESC' in data[0]
    assert data[0].index("ORDER BY") < data[0].index("ROWNUM")
    assert all(call[0].lstrip().upper().startswith("SELECT") for call in connection.fake_cursor.calls)
    assert result.tables[0].rows == [{"NAME": 1}]
    assert connection.call_timeout == 60_000
    assert connection.fake_cursor.closed and connection.closed


def test_grouped_aggregates_are_computed_before_result_cap(monkeypatch, tmp_path):
    connection, uploaded, result = run_query(
        monkeypatch,
        tmp_path,
        [("A", Decimal("3.5"), 2)],
        {
            "table": "people",
            "group_by": ["name"],
            "aggregates": [
                {"function": "avg", "column": "id", "alias": "average_id"},
                {"function": "count", "alias": "row_count"},
            ],
            "order_by": [{"column": "average_id", "direction": "desc"}],
            "limit": 1,
        },
    )

    sql, binds = connection.fake_cursor.calls[-1]
    assert sql.startswith(
        'SELECT * FROM (SELECT "NAME", AVG("ID") AS "average_id", '
        'COUNT(*) AS "row_count" FROM "PEOPLE" GROUP BY "NAME" '
        'ORDER BY "average_id" DESC)'
    )
    assert sql.index("GROUP BY") < sql.index("ORDER BY") < sql.index("ROWNUM")
    assert binds == {"row_limit": 2}
    assert result.tables[0].columns == ["NAME", "average_id", "row_count"]
    assert result.tables[0].rows == [
        {"NAME": "A", "average_id": "3.5", "row_count": 2}
    ]
    assert uploaded["data"].decode().splitlines()[0] == "NAME,average_id,row_count"


def test_count_distinct_uses_validated_column_and_preserves_alias_order_and_cap(
    monkeypatch, tmp_path
):
    connection, _, result = run_query(
        monkeypatch,
        tmp_path,
        [(2,)],
        {
            "table": "people",
            "aggregates": [
                {"function": "count_distinct", "column": "id", "alias": "unique_ids"}
            ],
            "order_by": [{"column": "unique_ids", "direction": "desc"}],
            "limit": 1,
        },
    )

    sql, binds = connection.fake_cursor.calls[-1]
    assert sql == (
        'SELECT * FROM (SELECT COUNT(DISTINCT "ID") AS "unique_ids" '
        'FROM "PEOPLE" ORDER BY "unique_ids" DESC) WHERE ROWNUM <= :row_limit'
    )
    assert binds == {"row_limit": 2}
    assert result.tables[0].rows == [{"unique_ids": 2}]


def test_global_aggregate_and_aggregate_projection_validation(monkeypatch, tmp_path):
    _, _, result = run_query(
        monkeypatch,
        tmp_path,
        [(4,)],
        {
            "table": "people",
            "aggregates": [{"function": "count", "alias": "total"}],
        },
    )
    assert result.tables[0].rows == [{"total": 4}]

    import harness.tools.query_db as query_db

    for payload, message in [
        (
            {
                "table": "people",
                "columns": ["id"],
                "group_by": ["name"],
                "aggregates": [{"function": "count", "alias": "total"}],
            },
            "grouped",
        ),
        (
            {
                "table": "people",
                "group_by": ["name"],
                "aggregates": [{"function": "count", "alias": "name"}],
            },
            "alias",
        ),
        (
            {
                "table": "people",
                "aggregates": [{"function": "avg", "alias": "average"}],
            },
            "column",
        ),
        (
            {
                "table": "people",
                "aggregates": [
                    {"function": "count_distinct", "alias": "unique_ids"}
                ],
            },
            "column",
        ),
        (
            {
                "table": "people",
                "aggregates": [
                    {
                        "function": "count_distinct",
                        "column": "missing",
                        "alias": "unique_ids",
                    }
                ],
            },
            "Unknown column",
        ),
    ]:
        connection = FakeConnection([])
        monkeypatch.setattr(query_db, "_connection", lambda: connection)
        with pytest.raises(ValueError, match=message):
            query_db._query(
                query_db.QueryDBInput.model_validate(payload),
                threading.Event(),
                {},
                threading.Lock(),
            )


@pytest.mark.parametrize(
    ("data_type", "value", "expression", "bound"),
    [
        ("DATE", "2026-01-02", "TO_DATE(:value_0, 'YYYY-MM-DD')", "2026-01-02"),
        (
            "TIMESTAMP(6)",
            "2026-01-02T03:04:05.123456",
            "TO_TIMESTAMP(:value_0, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF6')",
            "2026-01-02T03:04:05.123456",
        ),
        (
            "TIMESTAMP(6) WITH TIME ZONE",
            "2026-01-02T03:04:05.123456+09:00",
            "TO_TIMESTAMP_TZ(:value_0, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF6TZH:TZM')",
            "2026-01-02T03:04:05.123456+09:00",
        ),
    ],
)
def test_temporal_filters_use_explicit_oracle_conversions(
    monkeypatch, tmp_path, data_type, value, expression, bound
):
    connection, _, _ = run_query(
        monkeypatch,
        tmp_path,
        [],
        {
            "table": "people",
            "filters": [
                {"column": "stamp", "op": "ge", "value": value},
                {"column": "stamp", "op": "lt", "value": value},
            ],
        },
        columns=("STAMP",),
        data_types={"STAMP": data_type},
    )
    sql, binds = connection.fake_cursor.calls[-1]
    assert f'"STAMP" >= {expression}' in sql
    assert f'"STAMP" < {expression.replace("value_0", "value_1")}' in sql
    assert binds["value_0"] == bound


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("DATE", "01/02/2026"),
        ("TIMESTAMP(6)", "2026-01-02T03:04:05.1234567"),
        ("TIMESTAMP(6) WITH TIME ZONE", "2026-01-02T03:04:05"),
        ("TIMESTAMP(6) WITH TIME ZONE", "2026-01-02T03:04:05+99:99"),
    ],
)
def test_temporal_filters_reject_ambiguous_values(monkeypatch, data_type, value):
    import harness.tools.query_db as query_db

    connection = FakeConnection([], ("STAMP",), {"STAMP": data_type})
    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    args = query_db.QueryDBInput.model_validate({
        "table": "people",
        "filters": [{"column": "stamp", "op": "ge", "value": value}],
    })
    with pytest.raises(ValueError, match="ISO|offset|precision"):
        query_db._query(args, threading.Event(), {}, threading.Lock())


@pytest.mark.parametrize(
    ("rows", "limit", "truncated", "status", "summary"),
    [
        ([(1, "a"), (2, "b")], 2, False, None, "결과 2건"),
        ([(1, "a"), (2, "b"), (3, "c")], 2, True, None, "결과 2건"),
        ([], 2, False, "empty", "결과 0건"),
    ],
)
def test_exact_limit_overflow_and_zero_rows(
    monkeypatch, tmp_path, rows, limit, truncated, status, summary
):
    _, uploaded, result = run_query(
        monkeypatch, tmp_path, rows, {"table": "people", "limit": limit}
    )

    assert result.status == status
    assert result.summary == summary
    assert len(result.tables[0].rows) == min(len(rows), limit)
    assert result.tables[0].columns == ["ID", "NAME"]
    assert result.tables[0].complete is not truncated
    assert result.scope["query_db"]["truncated"] is truncated
    assert result.scope["query_db"]["row_count"] == min(len(rows), limit)
    assert uploaded["data"].decode().splitlines()[0] == "ID,NAME"


def test_scalar_serialization_and_csv_escaping(monkeypatch, tmp_path):
    values = (
        Decimal("1.25"), date(2026, 1, 2), datetime(2026, 1, 2, 3, 4, 5),
        'comma, quote " and\nnewline', None,
    )
    columns = ("AMOUNT", "DAY", "STAMP", "TEXT", "EMPTY")
    _, uploaded, result = run_query(
        monkeypatch, tmp_path, [values], {"table": "people"}, columns=columns
    )

    row = result.tables[0].rows[0]
    assert row == {
        "AMOUNT": "1.25", "DAY": "2026-01-02", "STAMP": "2026-01-02T03:04:05",
        "TEXT": 'comma, quote " and\nnewline', "EMPTY": None,
    }
    parsed = list(csv.DictReader(io.StringIO(uploaded["data"].decode())))
    assert parsed[0]["TEXT"] == values[3]


def test_lob_is_read_before_connection_closes(monkeypatch, tmp_path):
    payload = '{"map": ["A1", "B2"]}'
    connection = FakeConnection([], ("MAP_VAL_JSON",))

    class FakeLOB:
        def read(self):
            assert not connection.closed
            return payload

    connection.fake_cursor.rows = [(FakeLOB(),)]
    uploaded = {}

    import harness.tools.query_db as query_db

    async def upload(self, data, timeout=60):
        uploaded["data"] = data
        return str(self.results_dir / "query-lob.csv")

    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.upload_bytes", upload)
    result = asyncio.run(query_db.query_db(
        query_db.QueryDBInput(table="people"), context(tmp_path)
    ))

    assert result.tables[0].rows == [{"MAP_VAL_JSON": payload}]
    assert list(csv.DictReader(io.StringIO(uploaded["data"].decode()))) == [
        {"MAP_VAL_JSON": payload}
    ]
    assert connection.closed


def test_unsupported_result_scalar_closes_resources(monkeypatch, tmp_path):
    import harness.tools.query_db as query_db

    connection = FakeConnection([(object(),)], ("VALUE",))
    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    args = query_db.QueryDBInput(table="people")

    with pytest.raises(TypeError, match="Unsupported database value"):
        asyncio.run(query_db.query_db(args, context(tmp_path)))
    assert connection.fake_cursor.closed and connection.closed


def test_connection_still_closes_when_cursor_cleanup_fails(monkeypatch):
    import harness.tools.query_db as query_db

    connection = FakeConnection([(1, "Ada")])
    active = {}
    lock = threading.Lock()

    def failed_close():
        connection.fake_cursor.closed = True
        raise RuntimeError("cursor close failed")

    connection.fake_cursor.close = failed_close
    monkeypatch.setattr(query_db, "_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="cursor close failed"):
        query_db._query(
            query_db.QueryDBInput(table="people"), threading.Event(), active, lock
        )
    assert connection.closed
    assert active == {}


def test_transfer_failure_has_no_successful_path(monkeypatch, tmp_path):
    import harness.tools.query_db as query_db
    from harness.isolation.backend import IsolationError

    connection = FakeConnection([(1, "Ada")])

    async def failed(*args, **kwargs):
        raise IsolationError("upload failed")

    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.upload_bytes", failed)

    with pytest.raises(IsolationError, match="upload failed"):
        asyncio.run(query_db.query_db(query_db.QueryDBInput(table="people"), context(tmp_path)))
    assert connection.closed


def test_cancellation_closes_connection_acquired_late(monkeypatch, tmp_path):
    import harness.tools.query_db as query_db

    acquired = threading.Event()
    release = threading.Event()
    connection = FakeConnection([(1, "Ada")])

    def late_connection():
        acquired.set()
        release.wait(2)
        return connection

    monkeypatch.setattr(query_db, "_connection", late_connection)

    async def scenario():
        task = asyncio.create_task(
            query_db.query_db(query_db.QueryDBInput(table="people"), context(tmp_path))
        )
        await asyncio.to_thread(acquired.wait, 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if connection.closed:
                break
            await asyncio.sleep(.01)

    asyncio.run(scenario())
    assert connection.closed


def test_cancellation_interrupts_active_oracle_call(monkeypatch, tmp_path):
    import harness.tools.query_db as query_db

    entered = threading.Event()
    released = threading.Event()
    connection = FakeConnection([(1, "Ada")])
    original_execute = connection.fake_cursor.execute

    def blocking_execute(sql, binds):
        if "USER_TABLES" in sql:
            entered.set()
            released.wait(2)
            if connection.cancelled:
                raise RuntimeError("cancelled")
        return original_execute(sql, binds)

    def cancel():
        connection.cancelled = True
        released.set()

    connection.fake_cursor.execute = blocking_execute
    connection.cancel = cancel
    monkeypatch.setattr(query_db, "_connection", lambda: connection)

    async def scenario():
        task = asyncio.create_task(
            query_db.query_db(query_db.QueryDBInput(table="people"), context(tmp_path))
        )
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if connection.closed:
                break
            await asyncio.sleep(.01)

    asyncio.run(scenario())
    assert connection.cancelled and connection.closed


def test_native_tool_returns_only_query_facts_from_stored_observation():
    from harness.native_tools import domain_model_result
    from harness.types import ToolObservation, TableRef

    observation = ToolObservation(
        principal_id="p", session_id="s", run_id="r", invocation_id="call",
        result_id="store-result-id", tool_name="query_db", status="success",
        summary="결과 1건", scope={"query_db": {
            "columns": ["ID"], "row_count": 1, "truncated": False,
            "path": "/remote/query.csv",
        }}, columns=["ID"], preview_rows=[{"ID": 1}], total_rows=1,
        data_ref="stored-blob",
        tables=[TableRef(table_id="default", title="Query", columns=["ID"], total_rows=1,
                         preview_rows=[{"ID": 1}], complete=True, data_ref="stored-blob")],
    )

    view = domain_model_result(observation)

    assert view == {
        "result_id": "store-result-id", "table_id": "default",
        "columns": ["ID"], "row_count": 1,
        "truncated": False, "path": "/remote/query.csv", "status": "success",
        "summary": "결과 1건",
    }
    assert "preview_rows" not in view


def test_executor_stores_capped_evidence_and_projects_store_result_id(monkeypatch, tmp_path):
    import harness.tools.query_db as query_db
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.executor import ToolExecutor
    from harness.native_tools import domain_model_result
    from harness.tools.registry import ToolRegistry
    from harness.types import TableRef, ToolObservation

    run = {
        "principal_id": "p", "session_id": "s", "run_id": "run-1", "epoch": 1,
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
        def __init__(self):
            self.calls = Collection()
            self.runs = Collection()
            self.tables = None
            self.saved = None

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

        async def put_result(self, principal_id, session_id, observation, rows, artifacts,
                             *, tables=None):
            self.tables = tables
            table = tables[0]
            self.saved = ToolObservation(
                **observation, principal_id=principal_id, session_id=session_id,
                result_id="store-supplied-id", columns=table.columns,
                preview_rows=table.rows, total_rows=len(table.rows),
                tables=[TableRef(
                    table_id=table.table_id, title=table.title, columns=table.columns,
                    total_rows=len(table.rows), preview_rows=table.rows,
                    complete=table.complete, missing_reason=table.missing_reason,
                    data_ref="stored-blob",
                )],
            )
            return self.saved

        async def observation(self, principal_id, session_id, result_id):
            assert result_id == self.saved.result_id
            return self.saved

        async def rows(self, principal_id, session_id, result_id, table_id=None):
            assert result_id == self.saved.result_id
            assert table_id == self.saved.tables[0].table_id
            return self.tables[0].rows

    connection = FakeConnection([(1, "a"), (2, "b"), (3, "c")])

    async def upload(self, data, timeout=60):
        return str(self.results_dir / "query-stored.csv")

    monkeypatch.setattr(query_db, "_connection", lambda: connection)
    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.upload_bytes", upload)
    registry = ToolRegistry()
    query_db.register(registry)
    store = Store()
    isolation = context(tmp_path).settings.isolation
    execution = ExecutionContext(
        store, Settings(no_yield_tools=True, isolation=isolation), run
    )

    view = domain_model_result(asyncio.run(ToolExecutor(registry, execution).execute({
        "name": "query_db", "id": "call", "args": {"table": "people", "limit": 2},
    })))

    assert view["result_id"] == "store-supplied-id"
    assert view["table_id"] == "query_db"
    assert view["row_count"] == 2 and view["truncated"] is True
    assert store.tables[0].rows == [{"ID": 1, "NAME": "a"}, {"ID": 2, "NAME": "b"}]
    assert store.tables[0].complete is False

    from harness.tools.python_tools import PythonInput, run_python

    python_context = SimpleNamespace(
        store=store, run={"principal_id": "p", "session_id": "s"},
        settings=Settings(),
    )
    analyzed = asyncio.run(run_python(PythonInput(
        input_result_ids=[view["result_id"]],
        code=f"emit_table(tables[{view['result_id']!r}][{view['table_id']!r}], name='copied')",
    ), python_context))
    assert analyzed.status == "partial"
    assert analyzed.tables[0].rows == store.tables[0].rows


def test_query_db_registers_seven_day_absolute_path_lifetime():
    from harness.tools.query_db import register
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register(registry)

    description = registry.tools["query_db"].description
    assert "absolute" in description.lower()
    assert "7일" in description
    assert "tables[result_id][table_id]" in description
