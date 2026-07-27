from __future__ import annotations

import asyncio
import threading

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


class BlockingCancelRuntime(FakeRuntime):
    def __init__(self, result: bool):
        super().__init__()
        self.result = result
        self.cancel_started = threading.Event()
        self.release_cancel = threading.Event()

    def cancel(self, session_id, run_id):
        self.cancelled.append((session_id, run_id))
        self.cancel_started.set()
        assert self.release_cancel.wait(timeout=5)
        return self.result


class BlockingCreateRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.create_started = threading.Event()
        self.release_create = threading.Event()

    def create_session(self, session_id, rows, query):
        self.created.append(session_id)
        self.create_started.set()
        assert self.release_create.wait(timeout=5)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    async def fetch_rows(lotcd, start, end, fail_name):
        return ROWS

    session_store._SESSIONS.clear()
    getattr(session_store, "_CANCELLATIONS", {}).clear()
    monkeypatch.setattr(session_store, "_fetch_rows", fetch_rows)
    yield
    session_store._SESSIONS.clear()
    getattr(session_store, "_CANCELLATIONS", {}).clear()


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


def test_create_session_keeps_event_loop_responsive_during_worker_start(monkeypatch):
    class SlowRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.create_started = threading.Event()
            self.release_create = threading.Event()

        def create_session(self, session_id, rows, query):
            self.created.append(session_id)
            self.create_started.set()
            assert self.release_create.wait(timeout=2)

    fake = SlowRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    async def scenario():
        task = asyncio.create_task(session_store.create_session(
            "L12345", "2026-02-01", "2026-04-30", "OPEN"
        ))
        await asyncio.to_thread(fake.create_started.wait, 1)
        await asyncio.sleep(0)
        responsive_before_release = not fake.release_create.is_set()
        fake.release_create.set()
        await task
        return responsive_before_release

    assert asyncio.run(scenario()) is True


def test_cancelled_create_waits_for_worker_start_and_closes_unpublished_runtime(monkeypatch):
    fake = BlockingCreateRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    async def scenario():
        task = asyncio.create_task(session_store.create_session(
            "L12345", "2026-02-01", "2026-04-30", "OPEN"
        ))
        await asyncio.to_thread(fake.create_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        retained_ownership = not task.done()
        fake.release_create.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return retained_ownership

    assert asyncio.run(scenario()) is True
    assert len(fake.created) == 1
    assert fake.closed == fake.created
    assert session_store.get_session(fake.created[0]) is None


def test_repeated_cancellation_cannot_abandon_starting_worker(monkeypatch):
    fake = BlockingCreateRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    async def scenario():
        task = asyncio.create_task(session_store.create_session(
            "L12345", "2026-02-01", "2026-04-30", "OPEN"
        ))
        await asyncio.to_thread(fake.create_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        retained_ownership = not task.done()
        fake.release_create.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return retained_ownership

    assert asyncio.run(scenario()) is True
    assert fake.closed == fake.created
    assert session_store.get_session(fake.created[0]) is None


def test_cancellation_takes_precedence_when_worker_start_later_fails(monkeypatch):
    class FailingBlockedRuntime(BlockingCreateRuntime):
        def create_session(self, session_id, rows, query):
            self.created.append(session_id)
            self.create_started.set()
            assert self.release_create.wait(timeout=5)
            raise RuntimeError("worker startup failed")

    fake = FailingBlockedRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)

    async def scenario():
        task = asyncio.create_task(session_store.create_session(
            "L12345", "2026-02-01", "2026-04-30", "OPEN"
        ))
        await asyncio.to_thread(fake.create_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        fake.release_create.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert fake.closed == []
    assert session_store.get_session(fake.created[0]) is None


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


def test_cancel_reservation_blocks_next_run_until_runtime_is_lost(monkeypatch):
    fake = BlockingCancelRuntime(result=True)
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_id = info["session_id"]
    session_store.begin_run(session_id, "run-1")
    result: list[bool] = []
    cancel_thread = threading.Thread(
        target=lambda: result.append(session_store.cancel_run("run-1"))
    )

    cancel_thread.start()
    assert fake.cancel_started.wait(timeout=5)
    try:
        assert session_store.finish_run(session_id, "run-1") is True
        with pytest.raises(session_store.SessionStateError) as exc:
            session_store.begin_run(session_id, "run-2")
        assert exc.value.code == "session_busy"
    finally:
        fake.release_cancel.set()
        cancel_thread.join(timeout=5)

    assert not cancel_thread.is_alive()
    assert result == [True]
    assert session_store.get_session(session_id).status == "runtime_lost"
    assert session_store.get_session(session_id).active_run_id is None


def test_idle_cancel_wins_over_completion_deferred_by_reservation(monkeypatch):
    fake = BlockingCancelRuntime(result=False)
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_id = info["session_id"]
    session_store.begin_run(session_id, "run-1")
    result: list[bool] = []
    cancel_thread = threading.Thread(
        target=lambda: result.append(session_store.cancel_run("run-1"))
    )

    cancel_thread.start()
    assert fake.cancel_started.wait(timeout=5)
    try:
        assert session_store.finish_run(session_id, "run-1") is True
        with pytest.raises(session_store.SessionStateError) as exc:
            session_store.begin_run(session_id, "run-2")
        assert exc.value.code == "session_busy"
    finally:
        fake.release_cancel.set()
        cancel_thread.join(timeout=5)

    assert not cancel_thread.is_alive()
    assert result == [True]
    assert fake.closed == [session_id]
    assert session_store.get_session(session_id).status == "runtime_lost"
    assert session_store.get_session(session_id).active_run_id is None


def test_cancel_closes_idle_runtime_and_marks_session_lost(monkeypatch):
    fake = FakeRuntime()
    fake.cancel = lambda session_id, run_id: False
    monkeypatch.setattr(session_store, "_runtime", fake)
    info = _create_session()
    session_id = info["session_id"]
    session_store.begin_run(session_id, "run-1")

    assert session_store.cancel_run("run-1") is True
    assert fake.closed == [session_id]
    record = session_store.get_session(session_id)
    assert record.status == "runtime_lost"
    assert record.active_run_id is None


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


def test_close_all_during_create_prevents_late_publication(monkeypatch):
    fake = BlockingCreateRuntime()
    monkeypatch.setattr(session_store, "_runtime", fake)
    errors: list[BaseException] = []

    def create_in_thread():
        try:
            _create_session()
        except BaseException as exc:
            errors.append(exc)

    create_thread = threading.Thread(target=create_in_thread)
    create_thread.start()
    assert fake.create_started.wait(timeout=5)

    session_store.close_all_sessions()
    fake.release_create.set()
    create_thread.join(timeout=5)

    assert not create_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], session_store.SessionStateError)
    assert errors[0].code == "session_closed"
    assert session_store.get_session(fake.created[0]) is None
    assert fake.close_all_calls == 1
    assert fake.closed == [fake.created[0]]
