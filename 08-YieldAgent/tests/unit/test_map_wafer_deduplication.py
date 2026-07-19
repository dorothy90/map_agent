from datetime import datetime, timezone

import map_agent
from map_agent import _latest_wafer_rows


def test_latest_wafer_rows_keeps_newest_end_tm_per_wafer():
    old = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 1),
        "map_val_json": "old",
    }
    newest = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 2),
        "map_val_json": "new",
    }
    other = {
        "lot_id": "4SS123",
        "wf_id": 2,
        "end_tm": datetime(2026, 7, 1),
        "map_val_json": "other",
    }

    assert _latest_wafer_rows([old, other, newest]) == [newest, other]


def test_latest_wafer_rows_prefers_timestamp_over_missing_end_tm():
    missing = {"lot_id": "4SS123", "wf_id": 1, "end_tm": None}
    dated = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 1),
    }

    assert _latest_wafer_rows([missing, dated]) == [dated]


def test_latest_wafer_rows_handles_aware_timestamp_after_missing_end_tm():
    missing = {"lot_id": "4SS123", "wf_id": 1, "end_tm": None}
    aware = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 1, tzinfo=timezone.utc),
    }

    assert _latest_wafer_rows([missing, aware]) == [aware]


def test_query_wafer_data_applies_latest_row_reducer(monkeypatch):
    columns = [
        "lot_id",
        "wf_id",
        "map_val_json",
        "fab_id",
        "lot_cd",
        "start_tm",
        "end_tm",
    ]
    rows = [
        ("4SS123", 1, "old", "F", "4SS", None, datetime(2026, 7, 1)),
        ("4SS123", 2, "other", "F", "4SS", None, datetime(2026, 7, 1)),
        ("4SS123", 1, "new", "F", "4SS", None, datetime(2026, 7, 2)),
    ]

    class FakeCursor:
        description = [(name,) for name in columns]

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return rows

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(map_agent, "_get_oracle_connection_common", FakeConnection)

    result = map_agent._query_wafer_data(lot_ids="4SS123")

    assert [(row["wf_id"], row["map_val_json"]) for row in result] == [
        (1, "new"),
        (2, "other"),
    ]


def test_show_wafer_map_uses_deduped_rows_for_cummap_count_and_list(monkeypatch):
    old = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 1),
        "map_val_json": "old",
    }
    newest = {
        "lot_id": "4SS123",
        "wf_id": 1,
        "end_tm": datetime(2026, 7, 2),
        "map_val_json": "new",
    }
    other = {
        "lot_id": "4SS123",
        "wf_id": 2,
        "end_tm": datetime(2026, 7, 1),
        "map_val_json": "other",
    }
    deduped = _latest_wafer_rows([old, other, newest])
    rendered = {}

    monkeypatch.setattr(map_agent, "_query_wafer_data", lambda **kwargs: deduped)

    def fake_visualize(rows, **kwargs):
        rendered["rows"] = rows
        return "cummap_test.png", 100.0

    monkeypatch.setattr(map_agent, "_visualize_cummap", fake_visualize)

    result, render_rows = map_agent.show_wafer_map(
        lot_id="4SS123",
        map_type="cummap",
    )

    assert rendered["rows"] == [newest, other]
    assert "Wafer 수: 2개" in result
    assert render_rows == [
        ("4SS123", 1, "2026-07-02 00:00:00"),
        ("4SS123", 2, "2026-07-01 00:00:00"),
    ]
