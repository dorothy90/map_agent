import pytest

pytestmark = pytest.mark.no_server

from lot_history_tools import _parse_lot_ids, _query_lots


_ROWS_BY_TABLE = {
    "DF_FDC_ALARM": [
        ("4SS", "LOT-A", "20260719010000", "1000", "EQ-1", "ITEM-1", "W", 1, 0, 2),
        ("4SS", "LOT-B", "20260719020000", "1000", "EQ-2", "ITEM-2", "W", 2, 0, 3),
    ],
    "DF_QTIME_OVER": [
        ("4SS", "LOT-A", "1000", "1100", 10, 12, 2, "20260719030000"),
        ("4SS", "LOT-B", "1000", "1100", 10, 11, 1, "20260719040000"),
    ],
    "DF_TROUBLE_LOT": [
        ("4SS", "LOT-A", "20260719050000", "step-a", "EQ-1", 3, "contents-a", "H1"),
        ("4SS", "LOT-B", "20260719060000", "step-b", "EQ-2", 4, "contents-b", "H2"),
    ],
    "DF_FUTURE_ACTION": [
        ("LOT-A", "20260719070000", "1200", "action-a", "Y", "AREA-A"),
        ("LOT-B", "20260719080000", "1200", "action-b", "Y", "AREA-B"),
    ],
    "DF_SAMPLE_SPLIT": [
        ("LOT-A", "event-a", "1300", "oper-a", "01", "SPLIT-A", 1),
        ("LOT-B", "event-b", "1300", "oper-b", "02", "SPLIT-B", 2),
    ],
}


class FakeHistoryCursor:
    def __init__(self):
        self.executions = []
        self._rows = []
        self.closed = False

    def execute(self, sql, binds):
        self.executions.append((sql, binds))
        self._rows = next(rows for table, rows in _ROWS_BY_TABLE.items() if table in sql)

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class FakeHistoryConnection:
    def __init__(self):
        self.cursor_instance = FakeHistoryCursor()

    def cursor(self):
        return self.cursor_instance


@pytest.fixture
def fake_history_conn():
    return FakeHistoryConnection()


def test_parse_lot_ids_strips_and_deduplicates_in_order():
    assert _parse_lot_ids(" LOT-A,LOT-B,LOT-A ,, LOT-C ") == [
        "LOT-A", "LOT-B", "LOT-C"
    ]


def test_query_lots_executes_once_per_table_and_keeps_empty_lot(fake_history_conn):
    result = _query_lots(fake_history_conn, ["LOT-A", "LOT-B", "LOT-EMPTY"])

    assert len(fake_history_conn.cursor_instance.executions) == 5
    for sql, binds in fake_history_conn.cursor_instance.executions:
        assert " IN (:lot_0, :lot_1, :lot_2)" in sql
        assert binds == {
            "lot_0": "LOT-A", "lot_1": "LOT-B", "lot_2": "LOT-EMPTY"
        }
        assert "LOT-A" not in sql

    assert list(result) == ["LOT-A", "LOT-B", "LOT-EMPTY"]
    assert set(result["LOT-EMPTY"]) == {
        "fdc_alarm", "qtime_over", "trouble_lot", "future_action", "sample_split"
    }
    assert all(rows == [] for rows in result["LOT-EMPTY"].values())
    assert result["LOT-A"]["fdc_alarm"][0]["lot_id"] == "LOT-A"
    assert fake_history_conn.cursor_instance.closed
