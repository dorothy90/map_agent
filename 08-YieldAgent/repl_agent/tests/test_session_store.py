from __future__ import annotations

import asyncio

import pytest

from repl_agent import session_store


ROWS = [
    {
        "lotcd": "L12345",
        "lot_id": "LOT-1",
        "wf_id": "WF-1",
        "fail_name": "OPEN",
        "fail_value": 1.5,
        "oper": "OP-1",
        "legend": "equipment",
        "legend_value": "Equip1",
        "end_tm": "2026-03-01 12:00:00",
    }
]


class FakeRuntime:
    def __init__(self):
        self.created: list[str] = []
        self.cancelled: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self.close_all_calls = 0

    def create_session(self, session_id, rows, query):
        self.created.append(session_id)

    def execute(self, session_id, run_id, code, timeout_seconds):
        raise AssertionError("not used by session-store tests")

    def cancel(self, session_id, run_id):
        self.cancelled.append((session_id, run_id))
        return True

    def close_session(self, session_id):
        self.closed.append(session_id)

    def close_all(self):
        self.close_all_calls += 1


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    async def fetch_rows(lotcd, start, end, fail_name):
        return ROWS

    session_store._SESSIONS.clear()
    monkeypatch.setattr(session_store, "_fetch_rows", fetch_rows)
    yield
    session_store._SESSIONS.clear()


def _create_session() -> dict:
    return asyncio.run(
        session_store.create_session(
            "L12345", "2026-02-01", "2026-04-30", "OPEN"
        )
    )


def test_create_session_publishes_record_after_runtime_ack(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    info = _create_session()

    record = session_store.get_session(info["session_id"])
    assert isinstance(record, session_store.SessionRecord)
    assert record.status == "ready"
    assert record.active_run_id is None
    assert fake.created == [info["session_id"]]
    assert info["rowcount"] == 1
    assert info["numeric_columns"] == ["fail_value"]
    assert "df.shape: (1, 9)" in session_store.get_session_summary(info["session_id"])


def test_create_session_failure_does_not_publish_record(monkeypatch):
    class FailingRuntime(FakeRuntime):
        def create_session(self, session_id, rows, query):
            self.created.append(session_id)
            raise RuntimeError("worker startup failed")

    fake = FailingRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    with pytest.raises(RuntimeError, match="worker startup failed"):
        _create_session()

    assert session_store.get_session(fake.created[0]) is None


def test_begin_run_rejects_second_run(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_store.begin_run(info["session_id"], "run-1")

    with pytest.raises(session_store.SessionStateError) as exc:
        session_store.begin_run(info["session_id"], "run-2")

    assert exc.value.code == "session_busy"


def test_finish_run_only_clears_matching_active_run(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_id = info["session_id"]
    session_store.begin_run(session_id, "run-1")

    assert session_store.finish_run(session_id, "run-other") is False
    assert session_store.get_session(session_id).active_run_id == "run-1"
    assert session_store.finish_run(session_id, "run-1") is True
    assert session_store.get_session(session_id).status == "ready"
    assert session_store.get_session(session_id).active_run_id is None


def test_mark_runtime_lost_only_for_matching_active_run(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_id = info["session_id"]
    session_store.begin_run(session_id, "run-1")

    assert session_store.mark_runtime_lost(session_id, "run-other") is False
    assert session_store.get_session(session_id).status == "running"
    assert session_store.mark_runtime_lost(session_id, "run-1") is True
    assert session_store.get_session(session_id).status == "runtime_lost"
    assert session_store.get_session(session_id).active_run_id is None


def test_cancel_marks_runtime_lost(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_store.begin_run(info["session_id"], "run-1")

    assert session_store.cancel_run("run-1") is True
    assert fake.cancelled == [(info["session_id"], "run-1")]
    assert session_store.get_session(info["session_id"]).status == "runtime_lost"


def test_close_session_is_idempotent(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()

    assert session_store.close_session(info["session_id"]) is True
    assert session_store.close_session(info["session_id"]) is False
    assert fake.closed == [info["session_id"]]


def test_close_all_sessions_clears_records(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    first = _create_session()
    second = _create_session()

    session_store.close_all_sessions()

    assert fake.close_all_calls == 1
    assert session_store.get_session(first["session_id"]) is None
    assert session_store.get_session(second["session_id"]) is None
