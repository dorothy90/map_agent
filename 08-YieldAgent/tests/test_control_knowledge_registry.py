from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canonical_request import AGENT_SLOT_RULES
from control_knowledge_registry import (
    AGENT_CONTROL_PROFILES,
    AgentControlProfile,
    validate_agent_registry,
)
from models import HITL_CONTRACT_IDS, HITLContractId, InterruptEvent
from query_state import YieldQueryState
from result_contracts import ResultKind
from supervisor import workflow

pytestmark = pytest.mark.no_server


def _issues(profiles=AGENT_CONTROL_PROFILES):
    return validate_agent_registry(
        profiles=profiles,
        agent_slot_rules=AGENT_SLOT_RULES,
        graph_nodes=set(workflow.nodes),
        state_fields=set(YieldQueryState.__annotations__),
        result_kinds={item.value for item in ResultKind},
        hitl_contract_ids=set(HITL_CONTRACT_IDS),
        module_root=Path(__file__).resolve().parent.parent,
    )


def test_registry_covers_exact_canonical_agents():
    assert set(AGENT_CONTROL_PROFILES) == set(AGENT_SLOT_RULES)
    assert not _issues()


def test_runtime_interrupt_contract_uses_registry_hitl_ids():
    assert {item.value for item in HITLContractId} == set(HITL_CONTRACT_IDS)
    schema = InterruptEvent.model_json_schema()
    ref = schema["properties"]["interrupt_type"]["$ref"]
    enum_name = ref.rsplit("/", 1)[-1]
    assert set(schema["$defs"][enum_name]["enum"]) == set(HITL_CONTRACT_IDS)


def test_registry_profiles_have_operational_evidence():
    for agent_id, profile in AGENT_CONTROL_PROFILES.items():
        assert profile.agent_id == agent_id
        assert profile.responsibility
        assert profile.boundaries
        assert profile.source_refs
        assert profile.output_contracts
        assert profile.hitl_contracts
        assert profile.failure_modes


def test_slot_drift_is_reported_for_only_affected_agent():
    changed = dict(AGENT_CONTROL_PROFILES)
    profile = changed["yield_agent"]
    changed["yield_agent"] = profile.model_copy(
        update={"optional_slots": [*profile.optional_slots, "invented_slot"]}
    )
    issues = _issues(changed)
    assert [(issue.agent_id, issue.code) for issue in issues] == [
        ("yield_agent", "slot_mismatch")
    ]


def test_missing_profile_is_reported_as_registry_drift():
    changed = dict(AGENT_CONTROL_PROFILES)
    changed.pop("map_agent")
    issues = _issues(changed)
    assert ("map_agent", "missing_profile") in {
        (issue.agent_id, issue.code) for issue in issues
    }


def test_missing_tool_and_artifact_are_reported(tmp_path):
    profile = AgentControlProfile(
        agent_id="yield_agent",
        title="Yield Agent",
        responsibility="Yield analysis",
        boundaries=["Does not own WADS retrieval"],
        source_module="yield_query_agent",
        required_slots=["lotcd"],
        optional_slots=["periods", "ref_date", "time_range", "unit"],
        required_any_slots=[],
        output_contracts=["contracts/result-envelope"],
        result_kinds=["table", "summary"],
        artifact_channels=["missing_artifacts"],
        tool_modules=["missing_tool"],
        external_systems=["Oracle", "LLM"],
        hitl_contracts=["missing_param", "plan_review"],
        failure_modes=[
            {
                "code": "upstream_unavailable",
                "effect": "Returns an error envelope without changing other agents",
                "source_refs": ["yield_query_agent.py:216"],
            }
        ],
        source_refs=["yield_query_agent.py", "canonical_request.py:6"],
    )
    issues = validate_agent_registry(
        profiles={"yield_agent": profile},
        agent_slot_rules={"yield_agent": AGENT_SLOT_RULES["yield_agent"]},
        graph_nodes={"yield_agent"},
        state_fields=set(YieldQueryState.__annotations__),
        result_kinds={item.value for item in ResultKind},
        hitl_contract_ids=set(HITL_CONTRACT_IDS),
        module_root=tmp_path,
    )
    assert {issue.code for issue in issues} == {
        "missing_artifact_channel",
        "missing_source_module",
        "missing_source_ref",
        "missing_tool_module",
    }


def test_out_of_range_evidence_line_is_reported():
    changed = dict(AGENT_CONTROL_PROFILES)
    profile = changed["yield_agent"]
    failure = profile.failure_modes[0].model_copy(
        update={"source_refs": ["yield_query_agent.py:999999"]}
    )
    changed["yield_agent"] = profile.model_copy(
        update={"failure_modes": [failure, *profile.failure_modes[1:]]}
    )
    issues = _issues(changed)
    assert ("yield_agent", "invalid_source_line") in {
        (issue.agent_id, issue.code) for issue in issues
    }
