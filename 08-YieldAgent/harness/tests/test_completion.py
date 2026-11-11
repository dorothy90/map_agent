

from harness.completion import validate_evidence


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
