from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

ExecutionStatus = Literal["success", "error", "timeout", "cancelled", "runtime_lost"]

_PUBLIC_ERROR_MESSAGES = {
    "python_syntax_error": "Python code has invalid syntax.",
    "python_runtime_error": "Python code execution failed.",
    "execution_timeout": "Python execution timed out. Start a new session.",
    "execution_cancelled": "Python execution was cancelled. Start a new session.",
    "worker_protocol_error": "Python runtime is no longer available. Start a new session.",
}


class ExecutionError(BaseModel):
    code: str
    exception_name: str
    message: str
    traceback: str = ""


class PlotArtifact(BaseModel):
    artifact_id: str
    kind: Literal["plotly"] = "plotly"
    mime_type: Literal["application/vnd.plotly.v1+json"] = "application/vnd.plotly.v1+json"
    spec: dict[str, Any]


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: ExecutionError | None = None
    plots: list[PlotArtifact] = Field(default_factory=list)
    execution_time_ms: int

    def to_tool_payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude={"plots": True, "error": {"traceback"}})
        error = payload.get("error")
        if isinstance(error, dict):
            error["message"] = _PUBLIC_ERROR_MESSAGES.get(
                str(error.get("code")), "Python execution failed."
            )
        return payload


class PythonRuntime(Protocol):
    def create_session(self, session_id: str, rows: list[dict[str, Any]], query: dict[str, str]) -> None: ...
    def execute(self, session_id: str, run_id: str, code: str, timeout_seconds: float) -> ExecutionResult: ...
    def cancel(self, session_id: str, run_id: str) -> bool: ...
    def close_session(self, session_id: str) -> None: ...
    def close_all(self) -> None: ...
