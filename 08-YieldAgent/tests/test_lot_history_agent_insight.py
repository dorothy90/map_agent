from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import lot_history_agent


pytestmark = pytest.mark.no_server


def _empty_sources():
    return {
        "fdc_alarm": [],
        "qtime_over": [],
        "trouble_lot": [],
        "future_action": [],
        "sample_split": [],
    }


def single_lot_storage():
    storage = {"LOT-A": _empty_sources()}
    storage["LOT-A"]["fdc_alarm"] = [
        {"lot_id": "LOT-A", "oper_id": "PHOTO", "alarm_level_cd": "HALT"}
    ]
    return storage


def multi_lot_storage():
    storage = {}
    for lot_id in ("LOT-A", "LOT-B"):
        sources = _empty_sources()
        sources["fdc_alarm"] = [
            {"lot_id": lot_id, "oper_id": "PHOTO", "alarm_level_cd": "HALT"}
        ]
        sources["trouble_lot"] = [
            {"lot_id": lot_id, "step_desc": "PHOTO", "delay_time": "01:00:00"}
        ]
        storage[lot_id] = sources
    return storage


def no_intersection_storage():
    storage = {}
    for lot_id, process in (("LOT-A", "PHOTO"), ("LOT-B", "ETCH")):
        sources = _empty_sources()
        sources["fdc_alarm"] = [
            {"lot_id": lot_id, "oper_id": process, "alarm_level_cd": "WATCH"}
        ]
        storage[lot_id] = sources
    return storage


@pytest.fixture
def multi_lot_history():
    return multi_lot_storage()


def valid_common_insight():
    return {
        "summary": "PHOTO 공정에서 두 LOT의 공통 알람이 확인되었습니다.",
        "process_insights": [
            {
                "process": "PHOTO",
                "summary": "두 LOT 모두 pressure 알람이 있습니다.",
                "common_patterns": [
                    {
                        "text": "동일한 알람이 반복되었습니다.",
                        "lot_ids": ["LOT-A", "LOT-B"],
                        "event_ids": ["LOT-A:fdc_alarm:0", "LOT-B:fdc_alarm:0"],
                    }
                ],
                "lot_differences": [
                    {
                        "text": "LOT-B에서 TROUBLE 이력이 추가로 확인되었습니다.",
                        "lot_ids": ["LOT-B"],
                        "event_ids": ["LOT-B:trouble_lot:0"],
                    }
                ],
                "hypotheses": [
                    {
                        "text": "설비 조건의 영향 가능성이 있습니다.",
                        "confidence": "low",
                        "lot_ids": ["LOT-A", "LOT-B"],
                        "event_ids": ["LOT-A:fdc_alarm:0", "LOT-B:fdc_alarm:0"],
                    }
                ],
                "recommended_checks": ["PHOTO 설비 조건을 비교 확인하십시오."],
            }
        ],
        "priority_processes": [
            {
                "process": "PHOTO",
                "reason": "두 LOT에서 공통 알람이 확인되었습니다.",
                "lot_ids": ["LOT-A", "LOT-B"],
                "event_ids": ["LOT-A:fdc_alarm:0", "LOT-B:fdc_alarm:0"],
            }
        ],
    }


def fake_tool_invoke(storage):
    def invoke(_tool_input):
        lot_history_agent._tool_payload_var.get()["lot_history"] = storage
        return "조회 완료"

    return invoke


def fake_query_tool(storage):
    return SimpleNamespace(invoke=fake_tool_invoke(storage))


def make_state(lot_ids):
    return {
        "lot_ids": lot_ids,
        "current_task_id": "task-4",
        "current_task_goal": "LOT 공통 공정 비교",
    }


def _sql_result(result):
    return next(
        message
        for message in result["messages"]
        if message.name == "lot_history_sql_result"
    )


def test_multi_lot_common_process_calls_llm_once(monkeypatch, multi_lot_history):
    insight = valid_common_insight()
    analyzer = Mock(return_value=insight)
    monkeypatch.setattr(lot_history_agent, "analyze_common_process_history", analyzer)
    monkeypatch.setattr(
        lot_history_agent, "query_lot_history", fake_query_tool(multi_lot_history)
    )

    result = lot_history_agent.lot_history_agent_node(
        make_state(["LOT-A", "LOT-B"]), {}
    )

    analyzer.assert_called_once()
    assert "공통 공정 비교" in result["lot_history_artifacts"][0]["data"]
    structured = _sql_result(result).additional_kwargs["lot_history_result"]
    assert structured["common_process_insight"] == insight
    assert structured["common_processes"] == ["PHOTO"]
    envelope = result["messages"][-1].additional_kwargs["result"]
    assert [row["lot_id"] for row in envelope["rows"]] == ["LOT-A", "LOT-B"]
    assert envelope["metadata"] == {
        "row_count": 2,
        "artifact_count": 1,
        "common_process_count": 1,
        "insight_status": "success",
    }
    assert result["messages"][-1].content.startswith(insight["summary"])
    assert "LOT 이력 조회 결과 총 2개 LOT" in result["messages"][-1].content


@pytest.mark.parametrize(
    "lot_ids,storage,expected_status,expected_text",
    [
        (["LOT-A"], single_lot_storage(), "skipped_single_lot", None),
        (
            ["LOT-A", "LOT-B"],
            no_intersection_storage(),
            "empty_intersection",
            "공통 공정이 없습니다",
        ),
    ],
)
def test_comparison_is_skipped_without_multiple_lots_and_intersection(
    monkeypatch, lot_ids, storage, expected_status, expected_text
):
    analyzer = Mock()
    monkeypatch.setattr(lot_history_agent, "analyze_common_process_history", analyzer)
    monkeypatch.setattr(lot_history_agent, "query_lot_history", fake_query_tool(storage))

    result = lot_history_agent.lot_history_agent_node(make_state(lot_ids), {})

    analyzer.assert_not_called()
    html = result["lot_history_artifacts"][0]["data"]
    if expected_text is None:
        assert "공통 공정 비교" not in html
    else:
        assert expected_text in html
    assert _sql_result(result).additional_kwargs["lot_history_result"][
        "insight_status"
    ] == expected_status


def test_llm_failure_keeps_original_detail_html(monkeypatch, multi_lot_history):
    monkeypatch.setattr(
        lot_history_agent,
        "analyze_common_process_history",
        Mock(side_effect=ValueError("bad output")),
    )
    monkeypatch.setattr(
        lot_history_agent, "query_lot_history", fake_query_tool(multi_lot_history)
    )

    result = lot_history_agent.lot_history_agent_node(
        make_state(["LOT-A", "LOT-B"]), {}
    )
    html = result["lot_history_artifacts"][0]["data"]

    assert "비교 분석을 생성하지 못했습니다" in html
    assert "FDC ALARM" in html
    assert "TROUBLE LOT" in html
    envelope = result["messages"][-1].additional_kwargs["result"]
    assert len(envelope["rows"]) == 2
    assert envelope["metadata"]["insight_status"] == "analysis_failed"


def test_render_common_process_insight_escapes_all_llm_text():
    marker = '<script>alert("x")</script>'
    insight = valid_common_insight()
    insight["summary"] = marker
    process = insight["process_insights"][0]
    process["process"] = marker
    process["summary"] = marker
    process["common_patterns"][0]["text"] = marker
    process["lot_differences"][0]["text"] = marker
    process["hypotheses"][0]["text"] = marker
    process["hypotheses"][0]["confidence"] = marker
    process["recommended_checks"][0] = marker
    priority = insight["priority_processes"][0]
    priority["process"] = marker
    priority["reason"] = marker
    priority["lot_ids"][0] = marker

    html = lot_history_agent._render_common_process_insight(insight, "success")

    assert marker not in html
    assert '&lt;script&gt;alert(&quot;' in html


def test_default_html_render_preserves_legacy_output_without_insight_card():
    html = lot_history_agent._render_lot_history_html(single_lot_storage())

    assert "공통 공정 비교" not in html
