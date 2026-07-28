from __future__ import annotations

import multiprocessing
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from repl_agent.runtime.base import ExecutionError, ExecutionResult, ExecutionStatus
from repl_agent.runtime.worker import worker_main

TerminalStatus = Literal["timeout", "cancelled", "runtime_lost"]


@dataclass
class _WorkerHandle:
    process: Any
    connection: Any
    active_run_id: str | None = None
    terminal_run_id: str | None = None
    terminal_status: TerminalStatus | None = None
    lost: bool = False
    started: bool = False
    pid: int | None = None
    exitcode: int | None = None
    process_closed: bool = False
    shutdown_started: bool = False
    io_lock: threading.Lock = field(default_factory=threading.Lock)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    shutdown_complete: threading.Event = field(default_factory=threading.Event)


class ProcessPythonRuntime:
    _worker_target = staticmethod(worker_main)

    def __init__(self, startup_timeout_seconds: float = 10) -> None:
        self._startup_timeout_seconds = startup_timeout_seconds
        self._context = multiprocessing.get_context("spawn")
        self._handles: dict[str, _WorkerHandle] = {}
        self._handles_lock = threading.Lock()

    def create_session(
        self,
        session_id: str,
        rows: list[dict[str, Any]],
        query: dict[str, str],
    ) -> None:
        with self._handles_lock:
            if session_id in self._handles:
                raise ValueError(f"Python runtime session already exists: {session_id}")

        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._worker_target,
            args=(child_connection, rows, query),
        )
        handle = _WorkerHandle(process=process, connection=parent_connection)

        try:
            process.start()
            handle.started = True
            handle.pid = process.pid
            child_connection.close()
            if not parent_connection.poll(self._startup_timeout_seconds):
                raise RuntimeError("Python worker did not start before the startup timeout")
            ready = parent_connection.recv()
            if ready != {"type": "ready"}:
                raise RuntimeError("Python worker returned an invalid startup response")
        except BaseException:
            if handle.started:
                self._shutdown(handle)
            else:
                parent_connection.close()
            child_connection.close()
            raise

        with self._handles_lock:
            if session_id in self._handles:
                self._shutdown(handle)
                raise ValueError(f"Python runtime session already exists: {session_id}")
            self._handles[session_id] = handle

    def execute(
        self,
        session_id: str,
        run_id: str,
        code: str,
        timeout_seconds: float = 60,
    ) -> ExecutionResult:
        handle = self._require_handle(session_id)
        with handle.io_lock:
            if not self._begin_run(handle, run_id):
                self._shutdown(handle)
                return self._terminal_result(
                    "runtime_lost",
                    "worker_protocol_error",
                    "Python worker is no longer available",
                )

            try:
                handle.connection.send({"type": "execute", "run_id": run_id, "code": code})
                has_payload = handle.connection.poll(timeout_seconds)
            except (BrokenPipeError, EOFError, OSError):
                return self._finish_terminal(handle, run_id, "runtime_lost")

            if not has_payload:
                self._before_timeout_arbitration(session_id, run_id)
                return self._finish_terminal(handle, run_id, "timeout")

            try:
                payload = handle.connection.recv()
            except (EOFError, OSError):
                return self._finish_terminal(handle, run_id, "runtime_lost")

            try:
                if not isinstance(payload, dict):
                    raise TypeError("worker payload must be a dictionary")
                result = ExecutionResult.model_validate(payload)
            except (TypeError, ValidationError):
                return self._finish_terminal(handle, run_id, "runtime_lost")

            terminal_status = self._complete_run(handle, run_id)
            if terminal_status is not None:
                self._shutdown(handle)
                return self._result_for_status(terminal_status)
            self._after_completion_arbitration(session_id, run_id)
            return result

    def cancel(self, session_id: str, run_id: str) -> bool:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        if handle is None:
            return False

        with handle.state_lock:
            if (
                handle.lost
                or handle.active_run_id != run_id
                or handle.terminal_status is not None
            ):
                return False
            handle.active_run_id = None
            handle.terminal_run_id = run_id
            handle.terminal_status = "cancelled"
            handle.lost = True

        self._shutdown(handle)
        return True

    def is_alive(self, session_id: str) -> bool:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        if handle is None:
            return False
        with handle.state_lock:
            if handle.lost or handle.process_closed:
                return False
            return handle.process.is_alive()

    def close_session(self, session_id: str) -> None:
        with self._handles_lock:
            handle = self._handles.pop(session_id, None)
        if handle is None:
            return

        with handle.state_lock:
            idle = handle.active_run_id is None and not handle.lost
            if not idle:
                handle.lost = True

        if idle and handle.process.is_alive():
            acquired = handle.io_lock.acquire(blocking=False)
            if acquired:
                try:
                    with handle.state_lock:
                        still_idle = handle.active_run_id is None and not handle.lost
                    if still_idle and handle.process.is_alive():
                        try:
                            handle.connection.send({"type": "close"})
                            handle.process.join(timeout=2)
                        except (BrokenPipeError, EOFError, OSError):
                            pass
                finally:
                    handle.io_lock.release()
        self._shutdown(handle)

    def close_all(self) -> None:
        with self._handles_lock:
            session_ids = list(self._handles)
        for session_id in session_ids:
            self.close_session(session_id)

    def _before_timeout_arbitration(self, session_id: str, run_id: str) -> None:
        pass

    def _after_completion_arbitration(self, session_id: str, run_id: str) -> None:
        pass

    def _begin_run(self, handle: _WorkerHandle, run_id: str) -> bool:
        with handle.state_lock:
            if handle.lost or handle.process_closed or not handle.process.is_alive():
                handle.lost = True
                return False
            handle.active_run_id = run_id
            handle.terminal_run_id = None
            handle.terminal_status = None
            return True

    def _complete_run(
        self,
        handle: _WorkerHandle,
        run_id: str,
    ) -> TerminalStatus | None:
        with handle.state_lock:
            if handle.terminal_run_id == run_id and handle.terminal_status is not None:
                return handle.terminal_status
            if handle.active_run_id == run_id:
                handle.active_run_id = None
                return None
            handle.lost = True
            return "runtime_lost"

    def _finish_terminal(
        self,
        handle: _WorkerHandle,
        run_id: str,
        proposed_status: TerminalStatus,
    ) -> ExecutionResult:
        with handle.state_lock:
            if handle.terminal_run_id == run_id and handle.terminal_status is not None:
                status = handle.terminal_status
            else:
                status = proposed_status
                handle.active_run_id = None
                handle.terminal_run_id = run_id
                handle.terminal_status = status
                handle.lost = True

        self._shutdown(handle)
        return self._result_for_status(status)

    def _shutdown(self, handle: _WorkerHandle) -> None:
        with handle.state_lock:
            owner = not handle.shutdown_started
            if owner:
                handle.shutdown_started = True

        if not owner:
            handle.shutdown_complete.wait()
            return

        try:
            if handle.started:
                if handle.process.is_alive():
                    handle.process.terminate()
                handle.process.join(timeout=2)
                if handle.process.is_alive():
                    handle.process.kill()
                    handle.process.join(timeout=2)
                handle.exitcode = handle.process.exitcode
                handle.process.close()
                handle.process_closed = True
        finally:
            handle.connection.close()
            handle.shutdown_complete.set()

    def _require_handle(self, session_id: str) -> _WorkerHandle:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        if handle is None:
            raise KeyError(f"Unknown Python runtime session: {session_id}")
        return handle

    @classmethod
    def _result_for_status(cls, status: TerminalStatus) -> ExecutionResult:
        if status == "timeout":
            return cls._terminal_result(
                status,
                "execution_timeout",
                "Python execution exceeded 60 seconds",
            )
        if status == "cancelled":
            return cls._terminal_result(
                status,
                "execution_cancelled",
                "Python worker stopped before returning a result",
            )
        return cls._terminal_result(
            status,
            "worker_protocol_error",
            "Python worker stopped before returning a result",
        )

    @staticmethod
    def _terminal_result(
        status: ExecutionStatus,
        code: str,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            status=status,
            error=ExecutionError(
                code=code,
                exception_name="PythonRuntimeError",
                message=message,
            ),
            execution_time_ms=0,
        )
