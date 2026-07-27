import inspect
import multiprocessing
import os
import threading
import time

from repl_agent.runtime.process import ProcessPythonRuntime


ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": "3.5"}]
QUERY = {"lotcd": "P1", "start": "2026-01-01", "end": "2026-01-31", "fail_name": "OPEN"}


def malformed_worker(connection, rows, query):
    connection.send({"type": "ready"})
    connection.recv()
    connection.send({"not": "an execution result"})


class MalformedPayloadRuntime(ProcessPythonRuntime):
    _worker_target = staticmethod(malformed_worker)


class TimeoutBarrierRuntime(ProcessPythonRuntime):
    def __init__(self):
        super().__init__(startup_timeout_seconds=10)
        self.timeout_pending = threading.Event()
        self.release_timeout = threading.Event()

    def _before_timeout_arbitration(self, session_id, run_id):
        self.timeout_pending.set()
        assert self.release_timeout.wait(timeout=2)


class CompletionBarrierRuntime(ProcessPythonRuntime):
    def __init__(self):
        super().__init__(startup_timeout_seconds=10)
        self.completion_claimed = threading.Event()
        self.release_completion = threading.Event()

    def _after_completion_arbitration(self, session_id, run_id):
        self.completion_claimed.set()
        assert self.release_completion.wait(timeout=2)


class StartFailProcess:
    def start(self):
        raise OSError("spawn failed")

    def join(self, timeout=None):
        raise AssertionError("an unstarted process must not be joined")


class StartFailContext:
    def __init__(self):
        self.connections = []

    def Pipe(self, duplex=True):
        connections = multiprocessing.get_context("spawn").Pipe(duplex=duplex)
        self.connections.extend(connections)
        return connections

    def Process(self, target, args):
        return StartFailProcess()


def wait_for_active_run(runtime, session_id, run_id):
    deadline = time.monotonic() + 2
    handle = runtime._handles[session_id]
    while time.monotonic() < deadline:
        with handle.state_lock:
            if handle.active_run_id == run_id:
                return
        time.sleep(0.01)
    raise AssertionError(f"run did not become active: {run_id}")


def assert_pid_reaped(pid):
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    raise AssertionError(f"child process was not reaped: {pid}")


def test_execute_defaults_to_60_second_timeout():
    timeout = inspect.signature(ProcessPythonRuntime.execute).parameters["timeout_seconds"]

    assert timeout.default == 60


def test_process_runtime_preserves_state():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        process = runtime._handles["s1"].process
        pid = process.pid
        runtime.execute("s1", "r1", "value = 9", 2)
        result = runtime.execute("s1", "r2", "print(value)", 2)
        assert result.stdout == "9\n"
        assert runtime._handles["s1"].process is process
        assert runtime._handles["s1"].process.pid == pid
    finally:
        runtime.close_all()


def test_python_errors_keep_same_worker_usable():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        process = runtime._handles["s1"].process
        pid = process.pid

        syntax_error = runtime.execute("s1", "r1", "if True print('bad')", 2)
        runtime_error = runtime.execute("s1", "r2", "raise ValueError('bad')", 2)
        recovered = runtime.execute("s1", "r3", "print('still alive')", 2)

        assert syntax_error.error.code == "python_syntax_error"
        assert runtime_error.error.code == "python_runtime_error"
        assert recovered.stdout == "still alive\n"
        assert runtime._handles["s1"].process is process
        assert process.pid == pid
    finally:
        runtime.close_all()


def test_timeout_terminates_worker_and_loses_runtime():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        handle = runtime._handles["s1"]
        process = handle.process
        pid = process.pid
        result = runtime.execute("s1", "r1", "while True: pass", 0.2)
        assert result.status == "timeout"
        assert runtime.is_alive("s1") is False
        assert handle.exitcode is not None
        assert handle.process_closed is True
        assert_pid_reaped(pid)
        assert handle.connection.closed is True

        lost = runtime.execute("s1", "r2", "print('must not run')", 2)
        assert lost.status == "runtime_lost"
        assert runtime._handles["s1"].process is process
        assert runtime._handles["s1"].pid == pid
        assert runtime.cancel("s1", "r1") is False
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
        wait_for_active_run(runtime, "s1", "r1")
        assert runtime.cancel("s1", "r1") is True
        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert box["result"].status == "cancelled"
        assert runtime.is_alive("s1") is False
    finally:
        runtime.close_all()


def test_cancel_wins_when_claimed_before_timeout_arbitration():
    runtime = TimeoutBarrierRuntime()
    runtime.create_session("s1", ROWS, QUERY)
    box = {}
    thread = threading.Thread(
        target=lambda: box.setdefault(
            "result", runtime.execute("s1", "r1", "while True: pass", 0.05)
        )
    )
    try:
        thread.start()
        assert runtime.timeout_pending.wait(timeout=2)
        assert runtime.cancel("s1", "r1") is True
        runtime.release_timeout.set()
        thread.join(timeout=2)

        assert thread.is_alive() is False
        assert box["result"].status == "cancelled"
        assert runtime.cancel("s1", "r1") is False
    finally:
        runtime.release_timeout.set()
        runtime.close_all()


def test_completed_run_wins_before_cancel_can_kill_idle_worker():
    runtime = CompletionBarrierRuntime()
    runtime.create_session("s1", ROWS, QUERY)
    box = {}
    thread = threading.Thread(
        target=lambda: box.setdefault(
            "result", runtime.execute("s1", "r1", "print('done')", 2)
        )
    )
    try:
        thread.start()
        assert runtime.completion_claimed.wait(timeout=2)
        assert runtime.cancel("s1", "r1") is False
        assert runtime.is_alive("s1") is True
        runtime.release_completion.set()
        thread.join(timeout=2)

        assert thread.is_alive() is False
        assert box["result"].status == "success"
    finally:
        runtime.release_completion.set()
        runtime.close_all()


def test_unexpected_worker_exit_becomes_runtime_lost():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    try:
        handle = runtime._handles["s1"]
        result = runtime.execute("s1", "r1", "raise SystemExit(3)", 2)
        assert result.status == "runtime_lost"
        assert runtime.is_alive("s1") is False
        assert handle.exitcode is not None
        assert handle.process_closed is True
        assert_pid_reaped(handle.pid)
        assert handle.connection.closed is True
    finally:
        runtime.close_all()


def test_malformed_worker_payload_loses_runtime_and_closes_ipc():
    runtime = MalformedPayloadRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    handle = runtime._handles["s1"]
    try:
        result = runtime.execute("s1", "r1", "print('ignored')", 2)

        assert result.status == "runtime_lost"
        assert result.error.code == "worker_protocol_error"
        assert handle.exitcode is not None
        assert handle.process_closed is True
        assert_pid_reaped(handle.pid)
        assert handle.connection.closed is True
        assert runtime.execute("s1", "r2", "print('not recreated')", 2).status == "runtime_lost"
        assert runtime._handles["s1"] is handle
    finally:
        runtime.close_all()


def test_close_session_reaps_child_process():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    runtime.create_session("s1", ROWS, QUERY)
    handle = runtime._handles["s1"]
    pid = handle.process.pid
    assert pid is not None
    runtime.close_session("s1")
    runtime.close_session("s1")
    assert runtime.is_alive("s1") is False
    assert handle.exitcode is not None
    assert handle.process_closed is True
    assert_pid_reaped(pid)


def test_start_failure_closes_pipes_without_joining_unstarted_process():
    runtime = ProcessPythonRuntime(startup_timeout_seconds=10)
    context = StartFailContext()
    runtime._context = context

    try:
        runtime.create_session("s1", ROWS, QUERY)
    except OSError as exc:
        assert str(exc) == "spawn failed"
    else:
        raise AssertionError("create_session must preserve the spawn failure")

    assert all(connection.closed for connection in context.connections)
