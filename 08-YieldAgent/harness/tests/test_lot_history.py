import asyncio
from contextlib import contextmanager, ExitStack
import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from harness.tools import lot_history as history


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    executed = []
    with sqlite3.connect(path) as connection:
        for source in history.SOURCES.values():
            connection.execute(f"CREATE TABLE {source.table} ({', '.join(source.columns.split())})")
        connection.executemany("INSERT INTO DF_FDC_ALARM (lot_id, transfer_tm) VALUES (?, ?)", [
            ("A", "2026-11-22 23:59:59"), ("A", "2026-11-23 00:00:00"),
            ("A", "2026-11-24 10:00:00"), ("C", "2026-11-30 00:00:00")])
        connection.execute("INSERT INTO DF_QTIME_OVER (lot_id, event_tm) VALUES ('B', '2026-11-24 01:00:00')")

    @contextmanager
    def open_connection():
        with sqlite3.connect(path) as db:
            db.set_trace_callback(executed.append)
            @contextmanager
            def cursor():
                cur = db.cursor()
                try:
                    yield cur
                finally:
                    cur.close()
            yield SimpleNamespace(cursor=cursor)

    monkeypatch.setattr(history, "_connection", open_connection)
    return path, executed


@contextmanager
def run(*, requested_at="2026-12-02T00:00:00Z", zone=None, **arguments):
    ctx = SimpleNamespace(settings=SimpleNamespace(timezone=None),
        run={"created_at": requested_at, "request_context": {"timezone": zone}})
    result = asyncio.run(history.query_lot_history(history.LotHistoryInput(**arguments), ctx))
    with ExitStack() as stack:
        for table in result.tables:
            if hasattr(table, "source"):
                stack.callback(table.source.close)
        yield result


def test_fdc_period_discovers_once_per_lot_and_binds_event_local_time(oracle):
    _, executed = oracle
    with run(categories=["fdc_alarm"], start_time="2026-11-22T15:00:00Z",
             end_time="2026-11-29T15:00:00Z") as result:
        assert result.status == "success"
        assert len(executed) == 1
        assert "DF_FDC_ALARM" in executed[0]
        assert result.tables[0].rows == [{"lot_id": "A", "fdc_alarm": 2}]
        assert result.scope["query_lot_history"]["start_time"] == "2026-11-23 00:00:00"
        assert result.scope["query_lot_history"]["executed_tables"] == ["DF_FDC_ALARM"]
        assert [json.loads(line)["transfer_tm"] for line in result.tables[1].source] == [
            "2026-11-23 00:00:00", "2026-11-24 10:00:00"]


def test_fdc_only_and_full_history_keep_distinct_zero_counts(oracle):
    _, executed = oracle
    with run(lot_ids=["B"], categories=["fdc_alarm"]) as result:
        assert len(executed) == 1 and "DF_FDC_ALARM" in executed[0]
        assert result.status == "empty"
        assert result.tables[0].rows == [{"lot_id": "B", "fdc_alarm": 0}]
    executed.clear()
    with run(lot_ids=["B", "A", "B"]) as result:
        assert len(executed) == 5
        assert result.tables[0].rows == [
            {"lot_id": "B", "fdc_alarm": 0, "qtime_over": 1, "trouble_lot": 0, "future_action": 0, "sample_split": 0},
            {"lot_id": "A", "fdc_alarm": 3, "qtime_over": 0, "trouble_lot": 0, "future_action": 0, "sample_split": 0},
        ]


def test_large_selection_and_event_details_survive_model_preview(oracle):
    path, _ = oracle
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO DF_FDC_ALARM (lot_id,transfer_tm) VALUES (?,?)",
                       ((f"D{i:04}", "2026-11-24 12:00:00") for i in range(1001)))
    with run(categories=["fdc_alarm"], start_time="2026-11-23", end_time="2026-11-30") as result:
        summary, detail = result.tables
        assert len(summary.rows) == 1002
        assert result.scope["table_projections"]["fdc_alarm"]["preview_policy"] == "read_result"
        assert "lot_summary" not in result.scope["table_projections"]
        assert detail.total_rows == 1003
        assert len(detail.rows) < detail.total_rows
        assert sum(1 for _ in detail.source) == detail.total_rows


def test_missing_source_propagates_instead_of_false_zero(oracle):
    path, _ = oracle
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE DF_TROUBLE_LOT")
    with pytest.raises(sqlite3.OperationalError, match="DF_TROUBLE_LOT"):
        with run(lot_ids=["A"]):
            pass


@pytest.mark.parametrize("arguments", [
    {}, {"categories": ["fdc_alarm"], "start_time": "2026-11-23"},
    {"categories": ["fdc_alarm"], "start_time": "2026-11-30", "end_time": "2026-11-23"},
    {"categories": ["sample_split"], "start_time": "2026-11-23", "end_time": "2026-11-30"},
])
def test_rejects_unbounded_discovery_or_unrepresentable_time_scope(arguments):
    with pytest.raises(ValidationError):
        history.LotHistoryInput(**arguments)


def test_observed_events_clip_future_calendar_bound_at_request(oracle):
    with run(categories=["fdc_alarm"], start_time="2026-11-23", end_time="2026-11-30",
             requested_at="2026-11-23T12:00:00Z") as result:
        scope = result.scope["query_lot_history"]
        assert scope["requested_bounds"]["end_time"] == "2026-11-30 00:00:00"
        assert scope["effective_bounds"] == {"start_time": "2026-11-23 00:00:00", "end_time": "2026-11-23 21:00:00"}
        assert result.tables[0].rows == [{"lot_id": "A", "fdc_alarm": 1}]
        assert scope["effective_bounds"]["end_time"] in result.summary


def test_explicit_future_and_unbounded_lot_lookups_keep_their_range(oracle):
    window = dict(categories=["fdc_alarm"], start_time="2026-11-23", end_time="2026-12-01",
                  requested_at="2026-11-25T00:00:00Z")
    with run(**window, lot_ids=["C"]) as result:
        assert result.status == "empty"
    with run(**window, include_future_events=True) as result:
        scope = result.scope["query_lot_history"]
        assert scope["effective_bounds"] == scope["requested_bounds"]
        assert {"lot_id": "C", "fdc_alarm": 1} in result.tables[0].rows
    with run(lot_ids=["C"], requested_at="2026-11-25T00:00:00Z") as result:
        assert result.tables[0].rows[0]["fdc_alarm"] == 1


def test_historical_window_uses_profile_zone_and_keeps_absolute_end(oracle):
    with run(categories=["fdc_alarm"], start_time="2026-11-23T05:00:00Z",
             end_time="2026-11-24T05:00:00Z", zone="America/New_York",
             requested_at="2026-11-25T12:00:00Z") as result:
        scope = result.scope["query_lot_history"]
        assert scope["effective_bounds"] == scope["requested_bounds"] == {
            "start_time": "2026-11-23 00:00:00", "end_time": "2026-11-24 00:00:00"}
