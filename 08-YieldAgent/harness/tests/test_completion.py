import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from harness.completion import validate_evidence
from harness.config import Settings
from harness.nodes import Nodes
from harness.testing import ScriptedModel
from harness.tools.registry import ToolRegistry
from harness.types import ToolObservation


def test_missing_source_rejects_answer_reference():
    issues = validate_evidence({"answer": "자료", "result_ids": ["none"]}, {}, {})
    assert any(i["code"] == "missing_evidence" for i in issues)


def test_nested_scope_accepts_matching_subset_and_rejects_conflicting_units():
    candidate = {"answer": "관측값", "result_ids": ["r"], "scope": {"units": {"metric_b": "ratio"}}}
    source = {"status": "success", "scope": {"units": {"metric_a": "percent", "metric_b": "ratio"}}}
    assert validate_evidence(candidate, {}, {"r": source}) == []
    source["scope"]["units"]["metric_b"] = "percent"
    assert any(i["code"] == "scope_mismatch" for i in validate_evidence(candidate, {}, {"r": source}))


def test_candidate_scope_cannot_override_user_constraint():
    candidate = {"answer": "자료", "result_ids": ["r"], "scope": {"product": "OTHER"}}
    source = {"status": "success", "scope": {"product": "OTHER"}}
    assert any(i["code"] == "scope_mismatch" for i in validate_evidence(candidate, {"constraints": {"product": "DEMO"}}, {"r": source}))


def test_empty_result_supports_no_data_but_error_result_cannot():
    candidate = {"answer": "조회 결과 0건", "result_ids": ["r"]}
    result = {"status": "empty", "total_rows": 0, "scope": {"lotcd": "DEMO"}}
    assert validate_evidence(candidate, {}, {"r": result}) == []
    assert any(i["code"] == "unusable_evidence" for i in validate_evidence(candidate, {}, {"r": {**result, "status": "error"}}))


async def review(candidate, *, verdict=None, obs=None, corrections=0, focus=False, failure=None):
    captured = []
    stored = ToolObservation(principal_id="p", session_id="s", run_id="r", invocation_id="i", result_id="data",
        tool_name="query", **(obs or {"status": "empty", "total_rows": 0}))
    async def observation(principal_id, session_id, result_id):
        if (principal_id, session_id, result_id) != ("p", "s", "data"):
            raise PermissionError("Result unavailable")
        return stored
    async def model_call(model, messages, **kwargs):
        if failure:
            raise failure
        captured.append(json.loads(messages[-1].content))
        return AIMessage(content="", tool_calls=[{"id": "v", "name": "submit_verdict",
            "args": verdict or {"accepted": True, "complete": True}}])
    context = SimpleNamespace(store=SimpleNamespace(observation=observation), model_call=model_call, settings=Settings())
    node = Nodes(ScriptedModel([]), ToolRegistry(), context, "")
    result = await node.verify({"principal_id": "p", "session_id": "s", "run_id": "r",
        "goal": {"original_request": "통계와 원인"}, "candidate": candidate, "corrections": corrections,
        "focus_result_ids": ["data"] if focus else [],
        "messages": [HumanMessage(content="통계와 원인")], "observations": [] if focus else [stored.model_dump(mode="json")]})
    return result, captured


def test_review_receives_empty_metadata_without_indexing_rows():
    result, captured = asyncio.run(review({"answer": "조회 결과 0건", "result_ids": ["data"]}))
    assert result["status"] == "completed"
    assert captured[0]["observations"][0]["total_rows"] == 0
    assert captured[0]["observations"][0]["preview_rows"] == []


def test_review_reads_each_owned_source_once_and_renders_stored_values():
    result, captured = asyncio.run(review({"answer": "계산한 표입니다", "result_ids": ["data", "data"]},
        obs={"status": "success", "total_rows": 1, "columns": ["x", "y"], "preview_rows": [{"x": 1, "y": 2}]}))
    assert result["status"] == "completed"
    assert len(captured[0]["observations"]) == 1
    assert captured[0]["observations"][0]["preview_rows"] == [{"x": 1, "y": 2}]
    assert "| 1 | 2 |" in result["answer"]


def test_unfinished_requested_work_is_partial_while_known_results_remain_visible():
    result, _ = asyncio.run(review({"answer": "통계는 확인했고 원인은 확인하지 못했습니다.", "result_ids": ["data"]},
        verdict={"accepted": True, "complete": False},
        obs={"status": "success", "total_rows": 1, "columns": ["reports"], "preview_rows": [{"reports": 3}]}))
    assert result["status"] == "partial"
    assert "| 3 |" in result["answer"]


def test_repeated_narrative_rejection_returns_data_without_unverified_claim():
    result, _ = asyncio.run(review({"answer": "근거 없는 수치 9999", "result_ids": ["data"]}, corrections=1,
        verdict={"accepted": False, "complete": False, "issues": ["수치 불일치"]},
        obs={"status": "success", "summary": "확인되지 않은 설명 9999", "total_rows": 1, "columns": ["reports"], "preview_rows": [{"reports": 3}]}))
    assert result["status"] == "partial"
    assert "9999" not in result["answer"]
    assert "| 3 |" in result["answer"]


def test_foreign_source_is_not_used_or_disclosed_even_when_state_contains_data():
    result, captured = asyncio.run(review({"answer": "외부 자료", "result_ids": ["foreign"]}, corrections=1))
    assert result["status"] == "partial"
    assert captured == []
    assert result["result_ids"] == []
    assert result["validation_issues"][0]["code"] == "missing_evidence"


def test_native_followup_renders_review_selected_prior_result():
    result, _ = asyncio.run(review({"answer": "이전 표입니다."}, focus=True,
        verdict={"accepted": True, "complete": True, "result_ids": ["data"]},
        obs={"status": "success", "total_rows": 1, "columns": ["reports"], "preview_rows": [{"reports": 3}]}))
    assert result["status"] == "completed"
    assert "| 3 |" in result["answer"]
    assert result["result_ids"] == ["data"]


def test_native_greeting_does_not_repeat_previous_table():
    result, _ = asyncio.run(review({"answer": "안녕하세요."}, focus=True,
        verdict={"accepted": True, "complete": True},
        obs={"status": "success", "total_rows": 1, "columns": ["reports"], "preview_rows": [{"reports": 3}]}))
    assert result["answer"] == "안녕하세요."
    assert result["result_ids"] == []


def test_review_outage_keeps_selected_previous_table():
    result, _ = asyncio.run(review({"answer": "이전 표", "result_ids": ["data"]}, focus=True, failure=TimeoutError(),
        obs={"status": "success", "total_rows": 1, "columns": ["reports"], "preview_rows": [{"reports": 3}]}))
    assert result["status"] == "partial"
    assert "| 3 |" in result["answer"]
    assert result["result_ids"] == ["data"]


def test_mixed_scope_does_not_require_yield_period_fields_on_wads():
    candidate = {"answer": "수율과 검출 통계", "result_ids": ["yield", "wads"],
                 "scope": {"lotcd": "4SS", "unit": "weekly", "periods": 4}}
    sources = {
        "yield": {"status": "success", "scope": {"lotcd": "4SS", "unit": "weekly", "periods": 4}},
        "wads": {"status": "success", "scope": {"lotcd": "4SS", "start_tm": "2026-08-16", "end_tm": "2026-09-12"}},
    }
    assert validate_evidence(candidate, {}, sources) == []


def test_unmapped_scope_claim_is_left_to_semantic_review():
    assert validate_evidence(
        {'answer': '자료', 'result_ids': ['r'], 'scope': {'lotcd': '4SS'}}, {},
        {'r': {'status': 'success', 'scope': {}}}) == []


def test_action_only_candidate_continues_with_semantic_feedback():
    result, _ = asyncio.run(review({'answer': '웨이퍼별 자료를 더 살펴보겠습니다.'},
        verdict={'action': 'continue', 'complete': False,
                 'issues': ['최저 웨이퍼 목록을 조회하고 계산해야 합니다.']}))
    assert result.get('status') not in ('completed', 'partial', 'failed')
    assert result['candidate'] == {}
    assert '최저 웨이퍼' in result['messages'][-1].content


def test_explicit_useful_partial_can_finish():
    result, _ = asyncio.run(review({'answer': '수율은 확인했고 원인은 자료 부족으로 확정하지 못했습니다.'},
        verdict={'action': 'finish', 'complete': False, 'issues': []}))
    assert result['status'] == 'partial'
