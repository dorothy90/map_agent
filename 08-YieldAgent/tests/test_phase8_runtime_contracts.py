from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
from langchain_core.messages import AIMessage


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hitl_gate import (  # noqa: E402
    hitl_interrupt_payload,
    hitl_question_for_issue,
    hitl_selected_result_id,
    select_hitl_issues,
    should_abort_after_response,
)
from local_trace import (  # noqa: E402
    emit_runtime_detail,
    reset_runtime_terminal_logger_for_tests,
    reset_trace_context,
    reset_trace_sink_for_tests,
    set_trace_context,
)
from reference_resolver import resolve_references  # noqa: E402
from result_contracts import (  # noqa: E402
    RESULT_ENVELOPE_SCHEMA_VERSION,
    ResultContractError,
    attach_result_envelope,
    build_recent_result_index_entry,
    build_result_envelope,
    derive_summary_from_rows,
    prune_recent_results,
    validate_result_consistency,
    validate_result_envelope,
)
from task_normalizer_validator import (  # noqa: E402
    apply_report_refs_to_map_tasks,
    normalize_task_fields,
    validate_tasks,
)
from wads_tools import _sort_sql_result_rows, _wads_join_coverage  # noqa: E402


def _events(path: Path) -> list[dict]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _supervisor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    return importlib.import_module("supervisor")


def _envelope(
    result_id: str,
    *,
    source_agent: str = "wads_agent",
    rows: list[dict] | None = None,
    status: str = "success",
    artifact_refs: list[dict] | None = None,
    artifacts: list[dict] | None = None,
) -> dict:
    payload = build_result_envelope(
        source_agent=source_agent,
        kind="table" if rows else "summary",
        status=status,
        title=f"{source_agent}:{result_id}",
        summary=f"{source_agent} result {result_id}",
        rows=rows or [],
        artifact_refs=artifact_refs,
        artifacts=artifacts,
        provenance={"task_id": f"task_{result_id}", "task_goal": "phase8"},
    )
    payload["result_id"] = result_id
    return validate_result_envelope(payload).model_dump(mode="json")


def _recent(payload: dict) -> dict:
    return build_recent_result_index_entry(payload)


def test_result_envelope_schema_rows_and_artifact_payload_guards() -> None:
    rows = [
        {
            "lot_id": f"LOT{i:04d}",
            "parameter": f"PARAM{i}",
            "html": f"<html>secret-{i}</html>",
            "image_base64": "iVBOR" + ("A" * 1024),
        }
        for i in range(75)
    ]
    payload = _envelope(
        "result_schema",
        rows=rows,
        artifacts=[
            {
                "title": "full_html",
                "type": "html",
                "mime": "text/html",
                "data": "<html>artifact-secret</html>",
            }
        ],
    )

    assert payload["schema_version"] == RESULT_ENVELOPE_SCHEMA_VERSION
    assert len(payload["rows"]) == 50
    assert all("html" not in row and "image_base64" not in row for row in payload["rows"])
    assert payload["artifact_refs"]
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "artifact-secret" not in dumped
    assert "secret-0" not in dumped

    missing_version = dict(payload)
    missing_version.pop("schema_version")
    with pytest.raises(ResultContractError):
        validate_result_envelope(missing_version)

    mismatched_version = {**payload, "schema_version": "result-envelope/v999"}
    with pytest.raises(ResultContractError):
        validate_result_envelope(mismatched_version)

    with_payload_ref = {
        **payload,
        "artifact_refs": [{"artifact_id": "artifact_bad", "data": "<html>bad</html>"}],
    }
    with pytest.raises(ResultContractError):
        validate_result_envelope(with_payload_ref)


def test_recent_results_prunes_rows_payloads_missing_results_and_retry_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    payloads = [
        _envelope(f"result_{i}", rows=[{"lot_id": f"LOT{i:04d}", "data": "<html>x</html>"} for _ in range(60)])
        for i in range(5)
    ]
    pruned = prune_recent_results(payloads)
    assert [entry["result_id"] for entry in pruned] == ["result_2", "result_3", "result_4"]
    assert all(len(entry["rows"]) == 50 for entry in pruned)
    assert "<html>" not in json.dumps(pruned, ensure_ascii=False)

    retry_old = _envelope("result_retry", rows=[{"lot_id": "OLD0001"}])
    retry_new = _envelope("result_retry", rows=[{"lot_id": "NEW0001"}])
    retry_index = prune_recent_results([retry_old, retry_new])
    assert [entry["result_id"] for entry in retry_index] == ["result_retry"]
    assert retry_index[0]["rows"][0]["lot_id"] == "NEW0001"
    assert resolve_references("첫 번째 lot", retry_index)["resolved_refs"]["lot_ids"] == ["NEW0001"]

    message_without_result = AIMessage(content="plain answer", name="wads_agent")
    message_with_result = AIMessage(
        content="structured answer",
        name="wads_agent",
        additional_kwargs={"result": payloads[-1]},
    )
    index = sup._build_recent_results_index([message_without_result, message_with_result])
    assert [entry["result_id"] for entry in index] == ["result_4"]


def test_wads_first_lot_map_reference_uses_ui_row_order_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    recent = [
        _recent(
            _envelope(
                "result_wads",
                rows=[
                    {"lot_id": "LOT0001", "parameter": "IOFF"},
                    {"lot_id": "LOT0002", "parameter": "VTH"},
                ],
            )
        )
    ]

    resolved = resolve_references("첫 번째 lot map", recent)["resolved_refs"]
    assert resolved["lot_ids"] == ["LOT0001"]
    assert resolve_references("두 번째 lot map", recent)["resolved_refs"]["lot_ids"] == ["LOT0002"]

    validation = validate_tasks(
        [
            {
                "task_id": "task_map",
                "agent": "map_agent",
                "params": {"map_oper": "PT1H"},
                "goal": "첫 번째 lot map",
            }
        ],
        resolved_refs=resolved,
    )
    assert validation["issues"] == []
    task = validation["tasks"][0]
    assert task["params"]["lot_ids"] == ["LOT0001"]

    command = sup.supervisor_node(
        {
            "step_count": 0,
            "task_plan": [task],
            "pending_tasks": [task],
            "messages": [],
            "hitl_responses": [],
        },
        {},
    )
    assert command.goto == "map_agent"
    assert command.update["lot_ids"] == ["LOT0001"]
    assert command.update["map_oper"] == "PT1H"


def test_first_parameter_reference_maps_to_fail_type_without_name_specific_logic() -> None:
    recent = [
        _recent(
            _envelope(
                "result_wads",
                rows=[
                    {"lot_id": "LOT0001", "parameter": "IOFF"},
                    {"lot_id": "LOT0002", "parameter": "VTH"},
                ],
            )
        )
    ]
    resolved = resolve_references("첫 번째 파라미터 fail history", recent)["resolved_refs"]
    assert resolved["parameters"] == ["IOFF"]

    validation = validate_tasks(
        [
            {
                "task_id": "task_fail",
                "agent": "fail_history_agent",
                "params": {},
                "goal": "첫 번째 파라미터 불량 이력",
            }
        ],
        resolved_refs=resolved,
    )
    assert validation["issues"] == []
    assert validation["tasks"][0]["params"]["fail_type"] == "IOFF"


def test_multiple_wads_results_or_demonstrative_lot_reference_goes_to_hitl() -> None:
    first = _recent(_envelope("result_a", rows=[{"lot_id": "LOT0001"}]))
    second = _recent(_envelope("result_b", rows=[{"lot_id": "LOT0002"}]))

    ambiguous = resolve_references("첫 번째 lot map", [first, second])
    assert ambiguous["issues"][0]["type"] == "ambiguous_reference"
    assert ambiguous["issues"][0]["reason"] == "target_result_required"
    assert ambiguous["issues"][0]["candidates"] == ["result_a", "result_b"]
    assert ambiguous["issues"][0]["candidate_options"][0]["label"].startswith("WADS 결과")
    assert "LOT0001" in ambiguous["issues"][0]["candidate_options"][0]["label"]
    question = hitl_question_for_issue(ambiguous["issues"][0])
    assert "번호로 답해주세요" in question
    assert "1. WADS 결과" in question
    assert "사용할 result_id" not in question
    payload = hitl_interrupt_payload(ambiguous["issues"][0])
    assert payload["options"][0]["value"] == "result_a"
    assert hitl_selected_result_id(ambiguous["issues"][0], "1번") == "result_a"
    assert not should_abort_after_response(ambiguous["issues"][0], "1번")
    assert should_abort_after_response(ambiguous["issues"][0], "아무거나")

    demonstrative = resolve_references("그 lot fail history", [first])
    assert demonstrative["issues"][0]["type"] == "ambiguous_reference"
    assert demonstrative["issues"][0]["reason"] == "ordinal_or_explicit_reference_required"

    validation = validate_tasks(
        [
            {
                "task_id": "task_fail",
                "agent": "fail_history_agent",
                "params": {},
                "goal": "그 lot fail history",
            }
        ],
        reference_issues=demonstrative["issues"],
    )
    hitl = select_hitl_issues(validation["issues"])
    assert hitl
    assert hitl[0]["type"] == "ambiguous_reference"


def test_confirmation_hitl_leaves_natural_response_to_supervisor_llm() -> None:
    issue = {
        "type": "high_cost",
        "blocking": True,
        "severity": "warning",
        "cost_units": 12,
    }

    payload = hitl_interrupt_payload(issue)

    assert "options" not in payload
    assert "진행할지 답해주세요" in payload["message"]
    assert not should_abort_after_response(issue, "확인")


def test_supervisor_llm_classifies_confirmation_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    issue = {
        "type": "high_cost",
        "blocking": True,
        "severity": "warning",
        "cost_units": 12,
    }
    calls: list[list[dict]] = []

    class FakeModel:
        def invoke(self, messages, config=None):
            calls.append(messages)
            return AIMessage(content='{"action":"approve","reason":"진행 의사"}')

    monkeypatch.setattr(sup, "_model", FakeModel())

    task_plan = [
        {
            "task_id": "task_1",
            "agent": "yield_agent",
            "params": {"lotcd": "4SS"},
            "goal": "4SS 수율 조회",
        }
    ]

    result = sup._review_hitl_confirmation(issue, "응 진행해", task_plan)

    assert result.action == "approve"
    assert calls
    assert "사용자 응답" in calls[0][1]["content"]
    assert "현재 계획" in calls[0][1]["content"]
    assert "응 진행해" in calls[0][1]["content"]


def test_confirmation_modify_updates_plan_and_returns_to_plan_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    original_tasks = [
        {
            "task_id": "task_1",
            "agent": "yield_agent",
            "params": {"lotcd": "4SS"},
            "goal": "4SS 수율 조회",
        },
        {
            "task_id": "task_2",
            "agent": "wads_agent",
            "params": {"lotcd": "4SS"},
            "goal": "4SS WADS 조회",
        },
    ]
    modified_tasks = [original_tasks[0]]
    issue = {
        "type": "high_cost",
        "blocking": True,
        "severity": "warning",
        "cost_units": 12,
    }

    class FakeModel:
        def invoke(self, messages, config=None):
            return AIMessage(
                content=json.dumps(
                    {"action": "modify", "tasks": modified_tasks},
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(sup, "_model", FakeModel())
    monkeypatch.setattr(sup, "interrupt", lambda payload: "WADS는 빼고 수율만 해")

    command = sup.hitl_gate_node(
        {
            "task_plan": original_tasks,
            "pending_tasks": original_tasks,
            "task_validation_issues": [issue],
            "messages": [],
        },
        {},
    )

    assert command.goto == "plan_review"
    assert command.update["task_plan"] == modified_tasks
    assert command.update["pending_tasks"] == modified_tasks
    assert command.update["response"] == ""


def test_missing_params_and_invalid_params_are_blocking_without_silent_defaulting() -> None:
    tasks = [
        {
            "task_id": "task_map",
            "agent": "map_agent",
            "params": {
                "map_lot_ids": ["LOT0001"],
                "map_oper": "PT2",
                "unexpected": "do-not-use",
            },
            "goal": "invalid map",
        }
    ]
    normalized, normalization_trace = normalize_task_fields(tasks)
    assert normalized[0]["params"]["lot_ids"] == ["LOT0001"]
    assert any(event["event"] == "field_alias_normalized" for event in normalization_trace)

    validation = validate_tasks(normalized)
    params = validation["tasks"][0]["params"]
    assert params == {"lot_ids": ["LOT0001"]}
    reasons = {issue["reason"] for issue in validation["issues"]}
    assert "map_oper_must_be_PT1H_or_PT1C" in reasons
    assert "required_param_missing" in reasons
    assert "param_not_allowed_for_agent" in reasons
    assert select_hitl_issues(validation["issues"])


def test_non_ascii_product_code_is_invalid_for_wads() -> None:
    validation = validate_tasks(
        [
            {
                "task_id": "task_wads",
                "agent": "wads_agent",
                "params": {"lotcd": "4ㄴㅁ"},
                "goal": "invalid product code",
            }
        ]
    )

    issues = validation["issues"]
    assert any(
        issue.get("type") == "invalid_param"
        and issue.get("param") == "lotcd"
        and issue.get("reason") == "lotcd_must_be_ascii_product_code"
        for issue in issues
    )


def test_hitl_response_reenters_supervisor_on_the_original_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    task = {
        "task_id": "task_map",
        "agent": "map_agent",
        "params": {"map_oper": "PT1H"},
        "goal": "map after HITL",
    }

    command = sup.supervisor_node(
        {
            "step_count": 0,
            "task_plan": [task],
            "pending_tasks": [task],
            "messages": [],
            "hitl_responses": [
                {
                    "issue_type": "missing_param",
                    "task_id": "task_map",
                    "agent": "map_agent",
                    "param": "lot_ids|groupkey",
                    "response": "LOT0007",
                }
            ],
        },
        {},
    )

    assert command.goto == "map_agent"
    assert command.update["current_task_id"] == "task_map"
    assert command.update["lot_ids"] == ["LOT0007"]
    assert command.update["pending_tasks"] == []


def test_partial_empty_result_traces_link_trace_turn_task_and_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    trace_path = tmp_path / "phase8_trace.jsonl"
    monkeypatch.setenv("LOCAL_TRACE_SINK", "file")
    monkeypatch.setenv("LOCAL_TRACE_PATH", str(trace_path))
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "0")
    reset_trace_sink_for_tests()
    tokens = set_trace_context("trace_phase8", "turn_phase8")

    message = AIMessage(content="empty WADS result", name="wads_agent")
    try:
        attach_result_envelope(
            message,
            source_agent="wads_agent",
            kind="table",
            status="empty",
            rows=[],
            provenance={"task_id": "task_empty", "task_goal": "empty result"},
        )
        result_id = message.additional_kwargs["result"]["result_id"]
        sup._emit_task_outcome_trace({"messages": [message]}, "task_empty")
    finally:
        reset_trace_context(tokens)

    events = _events(trace_path)
    assert {event["event_type"] for event in events} >= {"agent_result_enveloped", "task_completed"}
    assert all(event["trace_id"] == "trace_phase8" for event in events)
    assert all(event["turn_id"] == "turn_phase8" for event in events)
    completed = [event for event in events if event["event_type"] == "task_completed"][-1]
    assert completed["task_id"] == "task_empty"
    assert completed["result_id"] == result_id
    assert completed["payload"]["status"] == "empty"


def test_terminal_logging_redacts_by_default_and_workflow_has_no_fast_path_bypass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_TRACE_SINK", "none")
    monkeypatch.setenv("LOCAL_RUNTIME_LOG", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_VERBOSE", "1")
    monkeypatch.delenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", raising=False)
    reset_trace_sink_for_tests()
    reset_runtime_terminal_logger_for_tests()
    tokens = set_trace_context("trace_logs", "turn_logs")

    try:
        emit_runtime_detail(
            "message",
            {
                "query": "SELECT * FROM SECRET_TABLE WHERE LOTID='LOT0001'",
                "rows": [{"lot_id": "LOT0001", "value": 1} for _ in range(10)],
                "data": "<html>artifact body SECRET</html>",
                "content": "assistant answer with LOT0001",
            },
            task_id="task_log",
            result_id="result_log",
        )
    finally:
        reset_trace_context(tokens)

    captured = capsys.readouterr()
    assert "message" in captured.err
    assert "SECRET_TABLE" not in captured.err
    assert "LOT0001" not in captured.err
    assert "artifact body SECRET" not in captured.err
    assert "SELECT *" not in captured.err

    edges = sup.workflow.edges
    assert ("__start__", "reference_resolver") in edges
    assert ("reference_resolver", "rewrite") in edges
    assert ("rewrite", "planner") in edges
    assert ("planner", "task_normalizer_validator") in edges
    assert ("task_normalizer_validator_after_review", "hitl_gate") in edges
    assert ("hitl_gate", "supervisor") in edges
    assert ("__start__", "planner") not in edges
    assert ("planner", "supervisor") not in edges
    assert ("rewrite", "supervisor") not in edges


def test_empty_plan_fallback_uses_llm_for_natural_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)

    calls: list[list[dict]] = []

    class FakeModel:
        def invoke(self, messages, config=None):
            calls.append(messages)
            return AIMessage(content="안녕하세요. 어떤 분석을 도와드릴까요?")

    monkeypatch.setattr(sup, "_model", FakeModel())
    response = sup._llm_empty_plan_response(
        "안녕",
        planner_text="죄송합니다. 수율 분석, WADS 열화 리포트, 웨이퍼 맵, LOT 이력 등의 쿼리만 지원합니다.",
    )

    assert response == "안녕하세요. 어떤 분석을 도와드릴까요?"
    assert calls
    assert "딱딱한 거절문" in calls[0][0]["content"]
    assert "안녕" in calls[0][1]["content"]


def test_planner_empty_retry_uses_llm_instead_of_code_rule_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)

    calls: list[list[dict]] = []

    class FakeModel:
        def invoke(self, messages, config=None):
            calls.append(messages)
            return AIMessage(content=json.dumps({
                "tasks": [
                    {
                        "task_id": "task_1",
                        "agent": "wads_agent",
                        "params": {
                            "lotcd": "",
                            "wads_start_tm": "",
                            "wads_end_tm": "2026-05-10",
                            "fail_type": "",
                        },
                        "goal": "5월 10일 WADS 열화 리포트 조회",
                    }
                ]
            }, ensure_ascii=False))

    monkeypatch.setattr(sup, "_model", FakeModel())
    tasks, raw_retry = sup._planner_empty_plan_retry(
        [
            {"role": "system", "content": "planner schema"},
            {"role": "user", "content": "5월 10일 열화리포트 보여줘"},
        ],
        user_text="5월 10일 열화리포트 보여줘",
        previous_output='{"tasks": []}',
    )

    assert raw_retry
    assert calls
    assert "Re-evaluate the latest user query" in calls[0][-2]["content"]
    assert tasks == [
        {
            "task_id": "task_1",
            "agent": "wads_agent",
            "params": {
                "lotcd": "",
                "wads_start_tm": "",
                "wads_end_tm": "2026-05-10",
                "fail_type": "",
            },
            "goal": "5월 10일 WADS 열화 리포트 조회",
        }
    ]
    assert sup._detect_missing_global_params(tasks, {}) == []


def test_wads_sql_summary_is_grounded_in_rows_not_llm_text() -> None:
    rows = [
        {"parameter": "GATE_OX(G)", "detection_count": 51},
        {"parameter": "ISB_CMOS(7)", "detection_count": 36},
        {"parameter": "LATCH(N)", "detection_count": 34},
    ]

    summary = derive_summary_from_rows(
        source_agent="wads_agent",
        rows=rows,
        fallback="가장 많이 검출된 파라미터는 EASY입니다.",
        title="wads_result",
    )

    assert "GATE_OX(G) 51건" in summary
    assert "ISB_CMOS(7) 36건" in summary
    assert "EASY" not in summary

    envelope = build_result_envelope(
        source_agent="wads_agent",
        kind="table",
        status="success",
        title="wads_result",
        summary=summary,
        rows=rows,
    )
    assert validate_result_consistency(envelope, message_content=summary) == []


def test_wads_non_count_summary_uses_unique_parameters() -> None:
    summary = derive_summary_from_rows(
        source_agent="wads_agent",
        rows=[
            {"parameter": "DIBL(D)", "groupkey": "4SA.01"},
            {"parameter": "DIBL(D)", "groupkey": "4SA.02"},
            {"parameter": "IGATE(P)", "groupkey": "4SA.03"},
            {"parameter": "JUNCTION(J)", "groupkey": "4SA.04"},
        ],
        fallback="",
    )

    assert summary == "WADS 조회 결과 총 4건이 조회되었습니다. 주요 파라미터는 DIBL(D), IGATE(P), JUNCTION(J)입니다."


def test_wads_join_coverage_flags_report_without_wafer_groupkey() -> None:
    df = pd.DataFrame(
        [
            {
                "lotcd": "4SS",
                "category": "PT1H_TEST",
                "parameter": "EASY(W)",
                "end_tm": "2026-05-31 10:00:00",
                "groupkey": None,
            }
        ]
    )

    stats = _wads_join_coverage(df)

    assert stats["report_rows"] == 1
    assert stats["joined_rows"] == 1
    assert stats["wafer_rows"] == 0
    assert stats["unique_groupkeys"] == 0
    assert stats["missing_groupkey_rows"] == 1
    assert stats["join_missing"] is True


def test_report_ordinal_refs_fan_out_to_report_groupkeys_for_cummap() -> None:
    recent = [
        _recent(
            _envelope(
                "result_wads_reports",
                rows=[
                    {
                        "report_index": 1,
                        "lotcd": "4SA",
                        "category": "PT1C_TEST",
                        "parameter": "LEAK_ID(L)",
                        "end_tm": "2026-05-10 17:10:26",
                        "map_oper": "PT1C",
                        "groupkeys": ["4SAHORD.01", "4SAHORD.07"],
                        "lot_ids": ["4SAHORD"],
                        "wf_ids": ["01", "07"],
                    },
                    {
                        "report_index": 2,
                        "lotcd": "4SA",
                        "category": "PT1H_TEST",
                        "parameter": "ION(O)",
                        "end_tm": "2026-05-10 18:10:26",
                        "map_oper": "PT1H",
                        "groupkeys": ["4SAXYZ1.03", "4SAXYZ1.05"],
                        "lot_ids": ["4SAXYZ1"],
                        "wf_ids": ["03", "05"],
                    },
                ],
            )
        )
    ]

    resolved = resolve_references("1,2번째 리포트에 있는 wafer들 각각 cummap 보여줘", recent)["resolved_refs"]
    tasks = [
        {
            "task_id": "task_wads",
            "agent": "wads_agent",
            "params": {"wads_end_tm": "2026-05-10"},
            "goal": "이전 리포트 재조회",
        },
        {
            "task_id": "task_map",
            "agent": "map_agent",
            "params": {"map_type": "cummap"},
            "goal": "1,2번째 리포트 wafer cummap",
        }
    ]
    expanded, trace = apply_report_refs_to_map_tasks(tasks, resolved)
    validation = validate_tasks(expanded, resolved_refs=resolved)

    assert [report["ordinal"] for report in resolved["reports"]] == [1, 2]
    assert [task["task_id"] for task in expanded] == ["task_map_r1", "task_map_r2"]
    assert expanded[0]["params"]["groupkey"] == "4SAHORD.01,4SAHORD.07"
    assert expanded[0]["params"]["map_oper"] == "PT1C"
    assert expanded[1]["params"]["groupkey"] == "4SAXYZ1.03,4SAXYZ1.05"
    assert expanded[1]["params"]["map_oper"] == "PT1H"
    assert all(task["params"]["map_type"] == "cummap" for task in expanded)
    assert validation["issues"] == []
    assert "lot_ids" not in validation["tasks"][0]["params"]
    assert "lot_ids" not in validation["tasks"][1]["params"]
    assert [event["event"] for event in trace] == [
        "resolved_report_ref_fanout",
        "resolved_report_ref_fanout",
        "resolved_report_ref_removed_redundant_source_task",
    ]


def test_report_ref_map_tasks_prioritize_groupkey_and_skip_plan_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    recent = [
        _recent(
            _envelope(
                "result_wads_reports",
                rows=[
                    {
                        "report_index": 1,
                        "map_oper": "PT1C",
                        "groupkeys": ["4SAHORD.01", "4SAHORD.07"],
                    },
                    {
                        "report_index": 2,
                        "map_oper": "PT1H",
                        "groupkeys": ["4SAXYZ1.03", "4SAXYZ1.05"],
                    },
                ],
            )
        )
    ]
    resolved = resolve_references("1,2번째 리포트에 있는 wafer들 각각 cummap 보여줘", recent)["resolved_refs"]
    tasks = [
        {
            "task_id": "task_1",
            "agent": "map_agent",
            "params": {
                "groupkey": ["4SAHORD.01", "4SAHORD.07"],
                "map_type": "cummap",
                "map_oper": "PT1C",
            },
            "goal": "Report 1 wafers cummap visualization",
        },
        {
            "task_id": "task_2",
            "agent": "map_agent",
            "params": {
                "groupkey": ["4SAXYZ1.03", "4SAXYZ1.05"],
                "map_type": "cummap",
                "map_oper": "PT1H",
            },
            "goal": "Report 2 wafers cummap visualization",
        },
    ]

    expanded, _ = apply_report_refs_to_map_tasks(tasks, resolved)
    validation = validate_tasks(expanded, resolved_refs=resolved)
    validated_tasks = validation["tasks"]

    assert validation["issues"] == []
    assert validated_tasks[0]["params"]["groupkey"] == "4SAHORD.01,4SAHORD.07"
    assert validated_tasks[1]["params"]["groupkey"] == "4SAXYZ1.03,4SAXYZ1.05"
    assert all("lot_ids" not in task["params"] for task in validated_tasks)

    monkeypatch.setattr(sup, "interrupt", lambda payload: pytest.fail("plan_review should not interrupt"))
    update = sup.plan_review_node(
        {
            "task_plan": validated_tasks,
            "resolved_refs": resolved,
            "task_validation_issues": [],
        },
        {},
    )

    assert update["task_plan"] == validated_tasks
    assert update["pending_tasks"] == validated_tasks


def test_chained_param_resolution_does_not_broaden_groupkey_map_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sup = _supervisor(monkeypatch, tmp_path)
    message = AIMessage(
        content="WADS result",
        name="wads_sql_result",
        additional_kwargs={
            "wads_result": {
                "lot_ids": ["4SAH0RD", "4SASDUM"],
                "groupkeys": ["4SAH0RD.01", "4SASDUM.11"],
            }
        },
    )

    params = sup._resolve_chained_params(
        {
            "task_id": "task_map",
            "agent": "map_agent",
            "params": {"groupkey": "4SAH0RD.01,4SAH0RD.07", "map_oper": "PT1C"},
            "goal": "report cummap",
        },
        {"messages": [message]},
    )

    assert params["groupkey"] == "4SAH0RD.01,4SAH0RD.07"
    assert "lot_ids" not in params


def test_wads_sql_ranking_rows_follow_sql_order_before_ui_summary_and_reference() -> None:
    rows = [
        {"parameter": "GATE_OX(G)", "detection_count": 51},
        {"parameter": "ISB_CMOS(7)", "detection_count": 36},
        {"parameter": "LATCH(N)", "detection_count": 34},
        {"parameter": "BVDS(B)", "detection_count": 25},
        {"parameter": "FMAX(X)", "detection_count": 21},
        {"parameter": "CONTACT(C)", "detection_count": 19},
        {"parameter": "EASY(W)", "detection_count": 19},
        {"parameter": "DIBL(D)", "detection_count": 8},
    ]

    result_rows, sort_info = _sort_sql_result_rows(
        rows,
        query_description="최근 일주일간 열화검출 파라미터 오름차순으로 표로 정리",
        sql=(
            "SELECT PARAMETER, COUNT(*) AS DETECTION_COUNT "
            "FROM DF_WADS_REPORT GROUP BY PARAMETER "
            "ORDER BY DETECTION_COUNT DESC"
        ),
    )
    summary = derive_summary_from_rows(
        source_agent="wads_agent",
        rows=result_rows,
        fallback="",
        sort_direction=sort_info["direction"],
    )
    recent = [_recent(_envelope("result_wads_sorted", rows=result_rows))]
    resolved = resolve_references("첫 번째 파라미터 리포트 보여줘", recent)["resolved_refs"]

    assert sort_info == {"key": "detection_count", "direction": "desc"}
    assert [row["parameter"] for row in result_rows[:3]] == ["GATE_OX(G)", "ISB_CMOS(7)", "LATCH(N)"]
    assert "GATE_OX(G) 51건" in summary
    assert "ISB_CMOS(7) 36건" in summary
    assert "LATCH(N) 34건" in summary
    assert "BVDS(B) 25건, CONTACT(C) 19건, DIBL(D) 8건" not in summary
    assert resolved["parameters"] == ["GATE_OX(G)"]


def test_wads_sql_ranking_without_order_by_does_not_infer_sort_from_request() -> None:
    rows = [
        {"parameter": "BVDS(B)", "detection_count": 25},
        {"parameter": "GATE_OX(G)", "detection_count": 51},
        {"parameter": "DIBL(D)", "detection_count": 8},
    ]

    result_rows, sort_info = _sort_sql_result_rows(
        rows,
        query_description="최근 일주일간 열화검출 파라미터 많이 검출된 순으로 표로 정리",
        sql="SELECT PARAMETER, COUNT(*) AS DETECTION_COUNT FROM DF_WADS_REPORT GROUP BY PARAMETER",
    )

    assert result_rows == rows
    assert sort_info == {}


def test_wads_summary_mismatch_is_detected() -> None:
    envelope = build_result_envelope(
        source_agent="wads_agent",
        kind="table",
        status="success",
        title="wads_result",
        summary="가장 많이 검출된 파라미터는 EASY입니다.",
        rows=[
            {"parameter": "GATE_OX(G)", "detection_count": 51},
            {"parameter": "ISB_CMOS(7)", "detection_count": 36},
        ],
    )

    warnings = validate_result_consistency(envelope, message_content=envelope["summary"])
    assert any(warning["reason"] == "summary_missing_top_parameter" for warning in warnings)


def test_first_parameter_reference_uses_semantic_table_when_other_recent_results_exist() -> None:
    lot_result = _recent(_envelope("result_lot", rows=[{"lot_id": "LOT0001"}]))
    parameter_result = _recent(
        _envelope(
            "result_param",
            rows=[
                {"parameter": "GATE_OX(G)", "detection_count": 51},
                {"parameter": "ISB_CMOS(7)", "detection_count": 36},
            ],
        )
    )

    resolved = resolve_references("첫 번째 파라미터 리포트 보여줘", [lot_result, parameter_result])["resolved_refs"]

    assert resolved["result_id"] == "result_param"
    assert resolved["parameters"] == ["GATE_OX(G)"]
    assert resolved["parameter"]["row"]["detection_count"] == 51


def test_resolved_parameter_overrides_planner_hallucinated_fail_type_and_wads_lotcd_is_optional() -> None:
    validation = validate_tasks(
        [
            {
                "task_id": "task_wads",
                "agent": "wads_agent",
                "params": {"fail_type": "EASY"},
                "goal": "첫 번째 파라미터 리포트 조회",
            }
        ],
        resolved_refs={
            "parameters": ["GATE_OX(G)"],
            "parameter": {
                "value": "GATE_OX(G)",
                "row": {"parameter": "GATE_OX(G)", "detection_count": 51},
            },
        },
    )

    assert validation["issues"] == []
    assert validation["tasks"][0]["params"]["fail_type"] == "GATE_OX(G)"
    assert any(event["event"] == "resolved_ref_overrode" for event in validation["trace"])
