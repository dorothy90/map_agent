import json


from harness.types import FinalCandidate, ToolObservation


def observation(**updates):
    return ToolObservation(**({"principal_id": "p", "session_id": "s", "run_id": "run",
        "invocation_id": "i", "result_id": "stats", "tool_name": "calculation",
        "scope": {"product": "DEMO", "period": "2026-08"}, "total_rows": 2,
        "columns": ["parameter", "reports"],
        "preview_rows": [{"parameter": "ALPHA", "reports": 2}, {"parameter": "BETA", "reports": 1}]} | updates))


def test_finish_references_results_without_repeating_cell_values():
    candidate = FinalCandidate(answer="집계 결과입니다.", result_ids=["stats"], scope={"product": "DEMO"})
    schema = FinalCandidate.model_json_schema()
    assert "claims" not in schema["properties"]
    assert "coverage" not in schema["properties"]
    assert candidate.result_ids == ["stats"]


def test_model_view_bounds_error_text_without_changing_stored_data():
    obs = observation(status="error", summary="trace " * 10000,
        preview_rows=[{"error": "trace " * 10000}], error={"safe_message": "trace " * 10000})
    assert len(json.dumps(obs.model_view())) < 7000
    assert len(obs.summary) == 60000
    assert len(obs.preview_rows[0]["error"]) == 60000


def test_history_keeps_original_turns_and_selected_sources_not_all_inherited_results():
    from harness.context import session_context
    previous = [{"run_id": "old", "query": "DEMO 8월 보고서", "answer": "조회했습니다.",
        "status": "completed", "result_ids": ["stats"],
        "observations": [observation(run_id="old").model_dump(), observation(result_id="irrelevant", run_id="other").model_dump()]}]
    history, results, focus = session_context(previous)
    assert history[0].content == "DEMO 8월 보고서"
    assert history[1].content == "조회했습니다."
    assert [o["result_id"] for o in results] == ["stats"]
    assert focus == ["stats"]
