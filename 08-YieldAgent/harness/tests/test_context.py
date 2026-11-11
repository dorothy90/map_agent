import json


def test_context_preserves_previous_result_summary_and_artifact_references():
    from harness.context import build_context
    from harness.types import ToolObservation
    obs = ToolObservation(principal_id="p", session_id="s", run_id="old", invocation_id="i", result_id="r", tool_name="query",
        summary="이전 조회의 요약", total_rows=14, artifact_refs=[{"artifact_id": "a", "title": "표"}], provenance={"data_origin": "fixture"})
    metadata = json.loads(build_context({"goal": {}, "observations": [obs.model_dump(mode="json")]}, "")[1].content)
    result = metadata["result_index"][0]
    assert result["summary"] == obs.summary
    assert result["total_rows"] == 14
    assert result["artifact_refs"] == [{"artifact_id": "a", "title": "표",
        "result_id": "r", "mime": "application/octet-stream",
        "href": "/harness/artifacts/a?session_id=s",
        "markdown": "[표](/harness/artifacts/a?session_id=s)"}]
    assert result["data_origin"] == "fixture"
