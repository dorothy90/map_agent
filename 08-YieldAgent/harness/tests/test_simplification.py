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


def test_result_reference_validation_rejects_missing_errors_and_wrong_scope():
    from harness.completion import validate_evidence
    candidate = {"answer": "자료", "result_ids": ["stats"], "scope": {"product": "DEMO"}}
    obs = observation().model_dump()
    assert validate_evidence(candidate, {}, {"stats": obs}) == []
    assert validate_evidence(candidate, {}, {})[0]["code"] == "missing_evidence"
    assert validate_evidence(candidate, {}, {"stats": {**obs, "status": "error"}})[0]["code"] == "unusable_evidence"
    assert any(i["code"] == "scope_mismatch" for i in validate_evidence(candidate, {}, {"stats": {**obs, "scope": {"product": "OTHER"}}}))


def test_failed_narrative_keeps_stored_table_and_scope():
    from harness.completion import render_results
    rendered = render_results([observation().model_dump()])
    assert "ALPHA" in rendered and "BETA" in rendered
    assert "2" in rendered and "1" in rendered
    assert "DEMO" in rendered and "2026-08" in rendered
    assert "stats" in rendered


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


def test_calculation_display_preserves_partial_input_scope():
    from harness.completion import render_results
    source = observation(result_id="raw", status="partial").model_dump()
    calc = observation(scope={}, source_result_ids=["raw"]).model_dump()
    rendered = render_results([calc], source_index={"raw": source})
    assert "DEMO" in rendered and "2026-08" in rendered
    assert "일부 자료" in rendered
    assert calc["scope"] == {} and calc["status"] == "success"
