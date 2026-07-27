from repl_agent.events import EventEmitter, RunStarted, ToolResult
from repl_agent.runtime.base import ExecutionError, ExecutionResult, PlotArtifact


def test_execution_result_excludes_plots_from_tool_json():
    result = ExecutionResult(
        status="success",
        stdout="42\n",
        plots=[PlotArtifact(artifact_id="p1", spec={"data": [], "layout": {}})],
        execution_time_ms=7,
    )
    payload = result.to_tool_payload()
    assert payload["status"] == "success"
    assert payload["stdout"] == "42\n"
    assert "plots" not in payload


def test_execution_error_has_stable_public_shape():
    error = ExecutionError(
        code="python_runtime_error",
        exception_name="ValueError",
        message="bad value",
        traceback="Traceback...",
    )
    assert error.model_dump()["code"] == "python_runtime_error"


def test_tool_payload_omits_internal_traceback_and_diagnostic_paths():
    secret_path = "/srv/private/app.py"
    result = ExecutionResult(
        status="error",
        stderr="safe stderr",
        error=ExecutionError(
            code="python_runtime_error",
            exception_name="ValueError",
            message="Invalid analysis code.",
            traceback=f'Traceback (most recent call last):\n  File "{secret_path}"\nSECRET_TOKEN=hidden',
        ),
        execution_time_ms=1,
    )

    payload = result.to_tool_payload()
    serialized = str(payload)

    assert payload["error"] == {
        "code": "python_runtime_error",
        "exception_name": "ValueError",
        "message": "Python code execution failed.",
    }
    assert "Traceback" not in serialized
    assert secret_path not in serialized
    assert "SECRET_TOKEN" not in serialized


def test_event_emitter_allocates_monotonic_sequence():
    emitter = EventEmitter(run_id="run-1", thread_id="thread-1")
    first = emitter.build(RunStarted)
    second = emitter.build(ToolResult, tool_call_id="tool-1", result={"status": "success"})
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.type == "RUN_STARTED"
    assert second.model_dump()["tool_call_id"] == "tool-1"
