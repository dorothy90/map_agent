from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from artifact_context import artifact_scope
from artifact_store import ArtifactRef, ArtifactStore


def _store(tmp_path: Path, job_id: str) -> ArtifactStore:
    return ArtifactStore(tmp_path, owner_hash="owner", job_id=job_id)


def _assert_reference_only(artifacts: list[dict], store: ArtifactStore) -> None:
    for artifact in artifacts:
        assert "data" not in artifact
        ref = ArtifactRef.model_validate(artifact["artifact_ref"])
        assert store.open(ref).read()
        checkpoint = json.dumps(artifact).lower()
        assert "file://" not in checkpoint
        assert "base64" not in checkpoint
        assert not Path(ref.relative_path).is_absolute()


@pytest.fixture
def secondary_results(tmp_path, monkeypatch):
    import fail_history_agent
    import lot_history_agent
    import mining_agent
    import relation_tree_agent
    import wt_resp_agent

    class FakeLotHistoryTool:
        def invoke(self, _args):
            lot_history_agent._tool_payload_var.get()["lot_history"] = {
                "ABC1234": {
                    "fdc_alarm": [],
                    "qtime_over": [],
                    "trouble_lot": [],
                    "future_action": [],
                    "sample_split": [],
                }
            }
            return "1개 LOT 이력 조회 완료"

    class FakeMiningGraph:
        def invoke(self, *_args, **_kwargs):
            storage = mining_agent._mining_payload_var.get()
            storage["gini_rows"] = [{"PARAMETER": "VTH", "GINI": 0.8}]
            storage["meta"] = {
                "lot_cd": "4SS",
                "fail_name": "VTH",
                "mode": "default",
            }
            storage["sig"] = "sig"
            return {"messages": [AIMessage(content="mining 완료")]}

    monkeypatch.setattr(lot_history_agent, "query_lot_history", FakeLotHistoryTool())
    monkeypatch.setattr(relation_tree_agent, "_query_main_opers", lambda *args: ["PT1H"])
    monkeypatch.setattr(mining_agent, "_mining_graph", FakeMiningGraph())
    monkeypatch.setattr(
        wt_resp_agent, "_query_good_bad", lambda *args: (["GOOD001"], ["BAD0001"])
    )
    monkeypatch.setattr(
        fail_history_agent,
        "do_search",
        lambda **_kwargs: {
            "retrieval_mode": "wiki-first",
            "rendered_answer": "과거 이력 없음",
            "results": [],
        },
    )

    stores = {
        field: _store(tmp_path, field.removesuffix("_artifacts"))
        for field in (
            "fail_history_artifacts",
            "lot_history_artifacts",
            "relation_tree_artifacts",
            "mining_artifacts",
            "wt_resp_artifacts",
        )
    }
    outputs = {}
    with artifact_scope(stores["fail_history_artifacts"]):
        outputs["fail_history_artifacts"] = fail_history_agent.fail_history_agent_node(
            {"lotcd": "4SS", "messages": [HumanMessage(content="불량 이력")]}, {}
        )["fail_history_artifacts"]
    with artifact_scope(stores["lot_history_artifacts"]):
        outputs["lot_history_artifacts"] = lot_history_agent.lot_history_agent_node(
            {"lot_ids": ["ABC1234"]}, {}
        )["lot_history_artifacts"]
    with artifact_scope(stores["relation_tree_artifacts"]):
        outputs["relation_tree_artifacts"] = relation_tree_agent.relation_tree_agent_node(
            {
                "lotcd": "4SS",
                "fail_type": "VTH",
                "rt_groups": [
                    {"lotcd": "4SS", "parameter": "VTH", "lot_ids": ["ABC1234"]},
                    {"lotcd": "4SS", "parameter": "IDSAT", "lot_ids": ["DEF5678"]},
                ],
            },
            {},
        )["relation_tree_artifacts"]
    with artifact_scope(stores["mining_artifacts"]):
        outputs["mining_artifacts"] = mining_agent.mining_agent_node(
            {
                "lotcd": "4SS",
                "fail_type": "VTH",
                "group_good": ["GOOD001"],
                "group_bad": ["BAD0001"],
                "messages": [HumanMessage(content="분석")],
            },
            {},
        )["mining_artifacts"]
    with artifact_scope(stores["wt_resp_artifacts"]):
        outputs["wt_resp_artifacts"] = wt_resp_agent.wt_resp_agent_node(
            {"lotcd": "4SS", "fail_type": "VTH", "cause_oper": "PT1H"}, {}
        )["wt_resp_artifacts"]
    return outputs, stores


@pytest.mark.parametrize(
    "field, expected_titles",
    [
        ("fail_history_artifacts", []),
        ("lot_history_artifacts", ["lot_history_report"]),
        ("relation_tree_artifacts", ["relation_tree_VTH", "relation_tree_IDSAT"]),
        ("mining_artifacts", ["mining_gini"]),
        ("wt_resp_artifacts", ["wt_resp"]),
    ],
)
def test_active_secondary_artifact_fields_are_reference_only(
    secondary_results, field, expected_titles
):
    outputs, stores = secondary_results
    assert [artifact["title"] for artifact in outputs[field]] == expected_titles
    assert all(artifact["type"] == "html" for artifact in outputs[field])
    assert all(artifact["mime"] == "text/html" for artifact in outputs[field])
    _assert_reference_only(outputs[field], stores[field])
