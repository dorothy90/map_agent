from __future__ import annotations

import contextlib
import io
import json
import time
import traceback
import uuid
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import scipy
import statsmodels.api as sm

from repl_agent.runtime.base import ExecutionError, ExecutionResult, PlotArtifact


class BoundedTextBuffer(io.TextIOBase):
    def __init__(self, limit: int = 50_000) -> None:
        self.limit = limit
        self.truncated = False
        self._parts: list[str] = []
        self._length = 0

    def write(self, text: str) -> int:
        remaining = self.limit - self._length
        if len(text) > remaining:
            self.truncated = True
        if remaining > 0:
            part = text[:remaining]
            self._parts.append(part)
            self._length += len(part)
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._parts)


def build_namespace(rows: list[dict[str, Any]], query: dict[str, str]) -> dict[str, Any]:
    return {
        "df": pd.DataFrame(rows),
        "query": query,
        "pd": pd,
        "np": np,
        "px": px,
        "go": go,
        "sm": sm,
        "scipy": scipy,
    }


def execute_code(namespace: dict[str, Any], code: str) -> ExecutionResult:
    started = time.perf_counter()
    stdout = BoundedTextBuffer()
    stderr = BoundedTextBuffer()
    plots: list[PlotArtifact] = []
    error: ExecutionError | None = None

    def emit_plot(fig: Any) -> None:
        plots.append(
            PlotArtifact(
                artifact_id=str(uuid.uuid4()),
                spec=json.loads(fig.to_json()),
            )
        )

    namespace["emit_plot"] = emit_plot
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace)  # noqa: S102 -- isolated process, not a security sandbox
    except SyntaxError as exc:
        error = ExecutionError(
            code="python_syntax_error",
            exception_name=type(exc).__name__,
            message=str(exc),
            traceback=traceback.format_exc(),
        )
    except Exception as exc:
        error = ExecutionError(
            code="python_runtime_error",
            exception_name=type(exc).__name__,
            message=str(exc),
            traceback=traceback.format_exc(),
        )

    return ExecutionResult(
        status="error" if error else "success",
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
        error=error,
        plots=plots,
        execution_time_ms=int((time.perf_counter() - started) * 1_000),
    )


def worker_main(connection: Any, rows: list[dict[str, Any]], query: dict[str, str]) -> None:
    namespace = build_namespace(rows, query)
    connection.send({"type": "ready"})

    while True:
        command = connection.recv()
        if isinstance(command, dict) and command.get("type") == "close":
            return
        if (
            isinstance(command, dict)
            and command.get("type") == "execute"
            and "run_id" in command
            and "code" in command
        ):
            result = execute_code(namespace, command["code"])
        else:
            result = ExecutionResult(
                status="error",
                error=ExecutionError(
                    code="worker_protocol_error",
                    exception_name="WorkerProtocolError",
                    message="Unknown worker command",
                ),
                execution_time_ms=0,
            )
        connection.send(result.model_dump(mode="json"))
