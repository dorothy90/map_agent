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
                {"name": "agent", "value": "wads_agent", "source_path": "registry"}
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
            "body_markdown": "# WADS Agent\n\nStructured facts only.\n",
            "relations": {},
            "evidence_refs": ["ev_1"],
        },
    }


def test_write_policy_is_exact_by_source_and_type():
    assert write_disposition("system_snapshot", "Agent") == "auto"
    assert write_disposition("runtime_observation", "Observation") == "auto"
    assert write_disposition("human_correction", "Runbook") == "review"
    assert write_disposition("system_snapshot", "Policy") == "review"


def test_snapshot_agent_page_is_written(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    curator = ControlKnowledgeCurator(store, FakeLLM(_decision()))
    entry = curator.curate(_candidate())
    assert entry.action == "created"
    assert (tmp_path / "wiki/agents/wads-agent.md").exists()


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
