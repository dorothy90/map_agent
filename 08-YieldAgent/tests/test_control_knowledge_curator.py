import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_curator import ControlKnowledgeCurator, write_disposition
from control_knowledge_models import KnowledgeCandidate
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False))


def _candidate(source="system_snapshot", page_type="Agent", subject="agents/wads-agent"):
    return KnowledgeCandidate.model_validate(
        {
            "source_kind": source,
            "subjects": [subject],
            "suggested_page_type": page_type,
            "summary": "structured change",
            "facts": [
                {
                    "name": "profile",
                    "value": {"output_contracts": ["contracts/result-envelope"]},
                    "source_path": "registry",
                },
                {
                    "name": "related_pages",
                    "value": [
                        "contracts/hitl-contracts",
                        "contracts/result-envelope",
                        "workflows/orchestration-graph",
                    ],
                    "source_path": "registry",
                },
            ],
            "evidence_refs": [
                {
                    "kind": "snapshot" if source == "system_snapshot" else "trace",
                    "ref": "ev_1",
                    "sha256": "a" * 64,
                }
            ],
        }
    )


AGENT_SECTIONS = [
    "Responsibility",
    "Boundaries",
    "Inputs",
    "Outputs",
    "Workflow Position",
    "Tools and External Systems",
    "HITL Contracts",
    "Verified Failure Modes",
    "Source Evidence",
    "Related Knowledge",
]


def _operational_body(title="WADS Agent"):
    return "\n\n".join(
        [
            f"# {title}",
            *[
                f"## {section}\n\nVerified content."
                for section in AGENT_SECTIONS
            ],
        ]
    ) + "\n"


def _agent_relations():
    return {
        "participates_in": ["[[workflows/orchestration-graph]]"],
        "uses_contract": ["[[contracts/result-envelope]]"],
        "uses_hitl_contract": ["[[contracts/hitl-contracts]]"],
    }


def _seed_relation_targets(root: Path) -> None:
    for page_id in (
        "contracts/result-envelope",
        "contracts/hitl-contracts",
        "workflows/orchestration-graph",
    ):
        path = root / "wiki" / f"{page_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\npage_id: {page_id}\n---\n# Seed\n", encoding="utf-8")


def _decision(page_type="Agent", subject="agents/wads-agent", action="create"):
    return {
        "action": action,
        "target_page_id": "" if action == "create" else subject,
        "rationale": "evidence changes the documented boundary",
        "draft": {
            "page_id": subject,
            "page_type": page_type,
            "title": "WADS Agent",
            "description": "WADS worker boundary",
            "body_markdown": _operational_body(),
            "relations": _agent_relations() if page_type == "Agent" else {},
            "evidence_refs": ["ev_1"],
        },
    }


def test_write_policy_is_exact_by_source_and_type():
    assert write_disposition("system_snapshot", "Agent") == "auto"
    assert write_disposition("runtime_observation", "Observation") == "auto"
    assert write_disposition("human_correction", "Runbook") == "review"
    assert write_disposition("system_snapshot", "Policy") == "review"


def test_snapshot_agent_page_is_written(tmp_path):
    _seed_relation_targets(tmp_path)
    store = ControlKnowledgeStore(tmp_path)
    llm = FakeLLM(_decision())
    curator = ControlKnowledgeCurator(store, llm)
    entry = curator.curate(_candidate())
    assert entry.action == "created"
    assert (tmp_path / "wiki/agents/wads-agent.md").exists()
    request_payload = json.loads(llm.calls[0][-1]["content"])
    assert request_payload["decision_schema"]["title"] == "CurationDecision"


def test_protected_change_creates_proposal(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    payload = _decision(
        page_type="Runbook", subject="runbooks/wads-agent-operations"
    )
    curator = ControlKnowledgeCurator(store, FakeLLM(payload))
    entry = curator.curate(
        _candidate(
            "human_correction", "Runbook", "runbooks/wads-agent-operations"
        )
    )
    assert entry.action == "proposal"
    assert list((tmp_path / "wiki/review_queue").glob("*.md"))
    assert not (tmp_path / "wiki/runbooks/wads-agent-operations.md").exists()


def test_no_change_writes_only_ledger(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    curator = ControlKnowledgeCurator(
        store, FakeLLM({"action": "no_change", "rationale": "same"})
    )
    entry = curator.curate(_candidate())
    assert entry.action == "no_change"
    assert store.ledger.exists()
    assert not (tmp_path / "wiki/agents/wads-agent.md").exists()


def test_missing_candidate_evidence_is_rejected(tmp_path):
    payload = _decision()
    payload["draft"]["evidence_refs"] = ["invented"]
    curator = ControlKnowledgeCurator(ControlKnowledgeStore(tmp_path), FakeLLM(payload))
    entry = curator.curate(_candidate())
    assert entry.action == "invalid_decision"


def test_operational_agent_draft_requires_every_section(tmp_path):
    _seed_relation_targets(tmp_path)
    payload = _decision()
    payload["draft"]["body_markdown"] = "# WADS Agent\n\n## Inputs\n\nlotcd\n"
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "invalid_decision"


def test_operational_agent_draft_accepts_all_sections(tmp_path):
    _seed_relation_targets(tmp_path)
    payload = _decision()
    payload["draft"]["body_markdown"] = _operational_body()
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "created"


def test_operational_agent_draft_requires_registry_relations(tmp_path):
    payload = _decision()
    payload["draft"]["body_markdown"] = _operational_body()
    payload["draft"]["relations"] = {}
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "invalid_decision"


def test_registry_drift_observation_is_auto_writable():
    assert write_disposition("registry_drift", "Observation") == "auto"
