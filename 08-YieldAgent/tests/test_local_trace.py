from __future__ import annotations

import json
from pathlib import Path
import sys

from langchain_core.messages import AIMessage


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_trace import (  # noqa: E402
    emit_runtime_detail,
    emit_trace_event,
    preview_text,
    redact_trace_payload,
    reset_runtime_terminal_logger_for_tests,
    reset_trace_context,
    reset_trace_sink_for_tests,
    set_trace_context,
)
from result_contracts import attach_result_envelope, prune_recent_results  # noqa: E402


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_redaction_removes_raw_query_and_payload() -> None:
    payload = redact_trace_payload({
        "query": "select * from secret_table where lotid = 'ABC1234'",
        "html": "<html><body>secret</body></html>",
        "value": "4SS2DPD",
        "metadata": {"row_count": 10},
        "safe": "planner",
    })

    assert payload["safe"] == "planner"
    assert payload["query"]["redacted"] is True
    assert payload["html"]["redacted"] is True
    assert payload["value"]["redacted"] is True
    assert payload["metadata"]["row_count"] == 10
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "secret_table" not in dumped
    assert "ABC1234" not in dumped
    assert "4SS2DPD" not in dumped


def test_jsonl_writer_and_result_events(monkeypatch, tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("LOCAL_TRACE_SINK", "file")
    monkeypatch.setenv("LOCAL_TRACE_PATH", str(trace_path))
    reset_trace_sink_for_tests()
    tokens = set_trace_context("trace_test", "turn_test")

    try:
        emit_trace_event(
            "validation_issue",
            source="unit",
            task_id="task_1",
            payload={"type": "missing_param", "query": "raw user query"},
        )

        message = AIMessage(content="visible answer", name="unit_agent")
        attach_result_envelope(
            message,
            source_agent="unit_agent",
            kind="table",
            status="success",
            rows=[{"lot_id": "ABC1234", "value": 1}],
            provenance={"task_id": "task_1"},
        )

        entries = [
            {
                "result_id": f"result_{i}",
                "source_agent": "unit_agent",
                "kind": "table",
                "title": "unit",
                "created_at": "2026-01-01T00:00:00+00:00",
                "columns": [],
                "rows": [],
                "artifact_refs": [],
            }
            for i in range(4)
        ]
        pruned = prune_recent_results(entries)
    finally:
        reset_trace_context(tokens)

    assert len(pruned) == 3
    events = _events(trace_path)
    event_types = {event["event_type"] for event in events}
    assert "validation_issue" in event_types
    assert "agent_result_enveloped" in event_types
    assert "result_pruned" in event_types
    assert all(event["trace_id"] == "trace_test" for event in events)
    assert all(event["turn_id"] == "turn_test" for event in events)

    dumped = json.dumps(events, ensure_ascii=False)
    assert "raw user query" not in dumped
    assert "ABC1234" not in dumped


def test_terminal_runtime_log_is_compact_and_redacted(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_abcdef123456", "turn_12")

    try:
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            task_id="task_1",
            payload={
                "target": "wads_agent",
                "params": {
                    "lot_ids": {"type": "list", "present": True, "count": 2, "sha256": "x"},
                    "period": {"type": "str", "present": True, "length": 2, "sha256": "y"},
                },
                "query": "select * from secret_table",
            },
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "[turn=12 trace=abcdef12 task=task_1] dispatch wads_agent" in captured.err
    assert "lot_ids:2" in captured.err
    assert "secret_table" not in captured.err


def test_default_runtime_log_shows_question_plan_and_parsed_params(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_flow123456", "turn_flow")

    try:
        emit_trace_event(
            "user_turn_started",
            source="agent_server",
            payload={
                "resume": False,
                "question_preview": preview_text("4SS EASY 검출 lot list 보여줘"),
            },
        )
        emit_trace_event(
            "rewrite_output",
            source="rewrite",
            payload={
                "input_preview": "EASY 보여줘",
                "rewritten_preview": "4SS EASY 검출 lot list 보여줘",
                "changed": True,
            },
        )
        emit_trace_event(
            "planner_output",
            source="planner",
            payload={
                "status": "ok",
                "task_count": 1,
                "task_flow": "task_1:wads_agent{fail_type=EASY, lotcd=4SS}",
            },
        )
        emit_trace_event(
            "workflow_node",
            source="planner",
            payload={
                "node": "planner",
                "step": 0,
                "keys": ["task_plan", "pending_tasks"],
                "task_plan_count": 1,
                "pending_task_count": 1,
            },
        )
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            task_id="task_1",
            payload={
                "target": "wads_agent",
                "task_goal_preview": "4SS EASY 검출 lot list 조회",
                "params": {
                    "lotcd": {"type": "str", "present": True, "preview": "4SS"},
                    "fail_type": {"type": "str", "present": True, "preview": "EASY"},
                },
            },
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert 'question="4SS EASY 검출 lot list 보여줘"' in captured.err
    assert 'rewrite changed=True "EASY 보여줘" -> "4SS EASY 검출 lot list 보여줘"' in captured.err
    assert "planner -> 1 tasks task_1:wads_agent{fail_type=EASY, lotcd=4SS}" in captured.err
    assert "node planner done" in captured.err
    assert 'dispatch wads_agent goal="4SS EASY 검출 lot list 조회"' in captured.err
    assert "lotcd=4SS" in captured.err
    assert "fail_type=EASY" in captured.err


def test_runtime_log_shows_hitl_resume_context_and_answer(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_hitl123456", "turn_hitl")

    try:
        emit_trace_event(
            "user_turn_started",
            source="agent_server",
            payload={
                "resume": True,
                "question_preview": "4ㄴㅁ",
                "resume_preview": "4ㄴㅁ",
                "pending_interrupt": {
                    "interrupt_type": "missing_param",
                    "param": "lotcd",
                    "route": "wads_agent",
                },
            },
        )
        emit_runtime_detail(
            "hitl.response",
            {
                "issue": {
                    "type": "missing_param",
                    "agent": "wads_agent",
                    "param": "lotcd",
                    "reason": "required_param_missing",
                },
                "user_response": "4ㄴㅁ",
                "record": {"response": "4ㄴㅁ"},
            },
            task_id="task_1",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert 'HITL resume answer="4ㄴㅁ"' in captured.err
    assert "param=lotcd" in captured.err
    assert "route=wads_agent" in captured.err
    assert 'hitl.response type=missing_param' in captured.err
    assert 'answer="4ㄴㅁ"' in captured.err


def test_verbose_runtime_detail_shows_raw_local_values(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_DETAIL", "raw")
    monkeypatch.setenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_verbose123", "turn_verbose")

    try:
        emit_runtime_detail(
            "planner.raw",
            {
                "raw_text": '{"tasks":[{"task_id":"task_1","agent":"wads_agent","params":{"lotcd":"4SS"}}]}',
                "query": "4SS WADS 보여줘",
            },
            task_id="task_1",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "planner.raw" in captured.err
    assert "4SS WADS 보여줘" in captured.err
    assert '"agent": "wads_agent"' in captured.err


def test_verbose_runtime_detail_is_compact_by_default(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    monkeypatch.delenv("LOCAL_RUNTIME_DETAIL", raising=False)
    monkeypatch.setenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_compact123", "turn_compact")

    try:
        emit_runtime_detail(
            "message",
            {
                "node": "wads_agent",
                "agent": "wads_agent",
                "content": "very long answer with LOT0001 and table body",
                "additional_kwargs": {
                    "result": {
                        "result_id": "result_abc123",
                        "kind": "table",
                        "status": "success",
                        "rows": [{"lot_id": "LOT0001"} for _ in range(30)],
                        "artifact_refs": [{"artifact_id": "a1"}],
                    }
                },
            },
            task_id="task_1",
            result_id="result_abc123",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "message node=wads_agent agent=wads_agent" in captured.err
    assert "content_len=" in captured.err
    assert "result=abc123 kind=table status=success rows=30 artifacts=1" in captured.err
    assert "very long answer" not in captured.err
    assert "LOT0001" not in captured.err


def test_wads_runtime_detail_shows_join_coverage(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_wads_join", "turn_wads_join")

    try:
        emit_runtime_detail(
            "wads.query_data",
            {
                "row_count": 1,
                "filters": {"lotcd": "4SS", "parameter": "EASY"},
                "columns": ["lotcd", "category", "parameter", "end_tm", "groupkey"],
                "sample": [{"lotcd": "4SS", "groupkey": ""}],
                "join_coverage": {
                    "report_rows": 1,
                    "joined_rows": 1,
                    "wafer_rows": 0,
                    "unique_groupkeys": 0,
                    "missing_groupkey_rows": 1,
                    "join_missing": True,
                },
            },
            task_id="task_wads_join",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "wads.query_data rows=1 reports=1 joined=1 wafer_rows=0 groupkeys=0" in captured.err
    assert "missing_groupkey_rows=1 join_missing=True" in captured.err


def test_wads_runtime_detail_shows_sql_and_binds(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", "1")
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_wads_sql", "turn_wads_sql")

    try:
        emit_runtime_detail(
            "wads.sql",
            {
                "context": "_query_wads_data",
                "sql": "SELECT r.LOTCD FROM DF_WADS_REPORT r WHERE UPPER(r.LOTCD) LIKE UPPER(:lotcd)",
                "binds": {"lotcd": "%4SS%"},
                "bind_keys": ["lotcd"],
                "join_wafers": True,
            },
            task_id="task_wads_sql",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "wads.sql context=_query_wads_data" in captured.err
    assert "binds={lotcd=%4SS%}" in captured.err
    assert "SELECT r.LOTCD FROM DF_WADS_REPORT r" in captured.err
