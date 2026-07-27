from __future__ import annotations

import multiprocessing
import threading
from dataclasses import dataclass, field
from typing import Any

from repl_agent.runtime.base import ExecutionError, ExecutionResult, ExecutionStatus
from repl_agent.runtime.worker import worker_main


@dataclass
class _WorkerHandle:
    process: Any
    connection: Any
    active_run_id: str | None = None
    cancelled_run_id: str | None = None
    io_lock: threading.Lock = field(default_factory=threading.Lock)


class ProcessPythonRuntime:
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
        process = self._context.Process(target=worker_main, args=(child_connection, rows, query))
        handle = _WorkerHandle(process=process, connection=parent_connection)

        try:
            process.start()
            child_connection.close()
            if not parent_connection.poll(self._startup_timeout_seconds):
                raise RuntimeError("Python worker did not start before the startup timeout")
            ready = parent_connection.recv()
            if ready != {"type": "ready"}:
                raise RuntimeError("Python worker returned an invalid startup response")
        except BaseException:
            self._terminate(handle)
            parent_connection.close()
            child_connection.close()
            raise

        with self._handles_lock:
            if session_id in self._handles:
                self._terminate(handle)
                parent_connection.close()
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
            handle.active_run_id = run_id
            handle.cancelled_run_id = None
            try:
                handle.connection.send({"type": "execute", "run_id": run_id, "code": code})
                if not handle.connection.poll(timeout_seconds):
                    self._terminate(handle)
                    return self._terminal_result(
                        "timeout",
                        "execution_timeout",
                        "Python execution exceeded 60 seconds",
                    )
                try:
                    payload = handle.connection.recv()
                except (EOFError, OSError):
                    self._terminate(handle)
                    status: ExecutionStatus = (
                        "cancelled" if handle.cancelled_run_id == run_id else "runtime_lost"
                    )
                    error_code = (
                        "execution_cancelled"
                        if status == "cancelled"
                        else "worker_protocol_error"
                    )
                    return self._terminal_result(
                        status,
                        error_code,
                        "Python worker stopped before returning a result",
                    )
            except (BrokenPipeError, EOFError, OSError):
                self._terminate(handle)
                status = "cancelled" if handle.cancelled_run_id == run_id else "runtime_lost"
                error_code = (
                    "execution_cancelled" if status == "cancelled" else "worker_protocol_error"
                )
                return self._terminal_result(
                    status,
                    error_code,
                    "Python worker stopped before returning a result",
                )
            finally:
                handle.active_run_id = None

            return ExecutionResult.model_validate(payload)

    def cancel(self, session_id: str, run_id: str) -> bool:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        if handle is None or handle.active_run_id != run_id or not handle.process.is_alive():
            return False

        handle.cancelled_run_id = run_id
        self._terminate(handle)
        return True

    def is_alive(self, session_id: str) -> bool:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        return handle is not None and handle.process.is_alive()

    def close_session(self, session_id: str) -> None:
        with self._handles_lock:
            handle = self._handles.pop(session_id, None)
        if handle is None:
            return

        try:
            if handle.active_run_id is None and handle.process.is_alive():
                acquired = handle.io_lock.acquire(blocking=False)
                if acquired:
                    try:
                        if handle.active_run_id is None and handle.process.is_alive():
                            handle.connection.send({"type": "close"})
                            handle.process.join(timeout=2)
                    except (BrokenPipeError, EOFError, OSError):
                        pass
                    finally:
                        handle.io_lock.release()
            self._terminate(handle)
        finally:
            handle.connection.close()

    def close_all(self) -> None:
        with self._handles_lock:
            session_ids = list(self._handles)
        for session_id in session_ids:
            self.close_session(session_id)

    def _require_handle(self, session_id: str) -> _WorkerHandle:
        with self._handles_lock:
            handle = self._handles.get(session_id)
        if handle is None:
            raise KeyError(f"Unknown Python runtime session: {session_id}")
        return handle

    @staticmethod
    def _terminate(handle: _WorkerHandle) -> None:
        if handle.process.is_alive():
            handle.process.terminate()
        handle.process.join(timeout=2)
        if handle.process.is_alive():
            handle.process.kill()
            handle.process.join(timeout=2)

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
