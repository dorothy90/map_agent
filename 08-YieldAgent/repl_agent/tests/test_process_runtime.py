import inspect
import threading
import time

from repl_agent.runtime.process import ProcessPythonRuntime


ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": "3.5"}]
QUERY = {"lotcd": "P1", "start": "2026-01-01", "end": "2026-01-31", "fail_name": "OPEN"}


def test_execute_defaults_to_60_second_timeout():
    timeout = inspect.signature(ProcessPythonRuntime.execute).parameters["timeout_seconds"]

    assert timeout.default == 60


def test_process_runtime_preserves_state():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        runtime.execute("s1", "r1", "value = 9", 2)
        result = runtime.execute("s1", "r2", "print(value)", 2)
        assert result.stdout == "9\n"
    finally:
        runtime.close_all()


def test_timeout_terminates_worker_and_loses_runtime():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        result = runtime.execute("s1", "r1", "while True: pass", 0.2)
        assert result.status == "timeout"
        assert runtime.is_alive("s1") is False
    finally:
        runtime.close_all()


def test_cancel_terminates_actual_running_worker():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    box = {}
    thread = threading.Thread(
        target=lambda: box.setdefault(
            "result", runtime.execute("s1", "r1", "while True: pass", 10)
        )
    )
    try:
        thread.start()
        time.sleep(0.2)
        assert runtime.cancel("s1", "r1") is True
        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert box["result"].status == "cancelled"
        assert runtime.is_alive("s1") is False
    finally:
        runtime.close_all()


def test_unexpected_worker_exit_becomes_runtime_lost():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        result = runtime.execute("s1", "r1", "raise SystemExit(3)", 2)
        assert result.status == "runtime_lost"
        assert runtime.is_alive("s1") is False
    finally:
        runtime.close_all()


def test_close_session_reaps_child_process():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    runtime.close_session("s1")
    runtime.close_session("s1")
    assert runtime.is_alive("s1") is False
