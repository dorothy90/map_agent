import pytest


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.binds = None
        self.closed = False

    def execute(self, sql, binds):
        self.sql = sql
        self.binds = binds

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows):
        self.cursor_value = FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


def test_query_uses_binds_normalizes_process_and_closes_resources():
    from symbol_bin_db import load_symbol_patterns

    connection = FakeConnection([('P', 'PT1H', 'one', '^A.$'), ('P', 'PT1H', 'two', '^.B$')])
    result = load_symbol_patterns('P', 'PT1H TEST', ['two', 'one'], connection_factory=lambda: connection)
    assert result == {'one': '^A.$', 'two': '^.B$'}
    assert ':lot_cd' in connection.cursor_value.sql
    assert ':process' in connection.cursor_value.sql
    assert ':param_0' in connection.cursor_value.sql
    assert connection.cursor_value.binds == {
        'lot_cd': 'P', 'process': 'PT1H', 'param_0': 'two', 'param_1': 'one'
    }
    assert connection.cursor_value.closed
    assert connection.closed


@pytest.mark.parametrize('process', ['PT2H', '', None])
def test_invalid_process_is_rejected_before_connecting(process):
    from symbol_bin_db import load_symbol_patterns

    called = False
    def connect():
        nonlocal called
        called = True
    with pytest.raises(ValueError, match='process'):
        load_symbol_patterns('P', process, ['one'], connection_factory=connect)
    assert not called


def test_missing_and_duplicate_parameters_are_errors_and_connection_closes():
    from symbol_bin_db import load_symbol_patterns

    missing = FakeConnection([('P', 'PT1C', 'one', '^A.$')])
    with pytest.raises(ValueError, match='Missing'):
        load_symbol_patterns('P', 'PT1C', ['one', 'two'], connection_factory=lambda: missing)
    assert missing.closed

    duplicate = FakeConnection([('P', 'PT1C', 'one', '^A.$'), ('P', 'PT1C', 'one', '^B.$')])
    with pytest.raises(ValueError, match='Duplicate'):
        load_symbol_patterns('P', 'PT1C', ['one'], connection_factory=lambda: duplicate)
    assert duplicate.closed


def test_invalid_regex_and_connection_errors_propagate():
    from symbol_bin_db import load_symbol_patterns

    invalid = FakeConnection([('P', 'PT1H', 'one', '[')])
    with pytest.raises(ValueError, match='one'):
        load_symbol_patterns('P', 'PT1H', ['one'], connection_factory=lambda: invalid)
    assert invalid.closed

    def fail():
        raise ConnectionError('offline')
    with pytest.raises(ConnectionError, match='offline'):
        load_symbol_patterns('P', 'PT1H', ['one'], connection_factory=fail)

