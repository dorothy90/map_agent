import os
from pathlib import Path
import sys

import frontmatter
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_models import KnowledgeCandidate, PageDraft
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


def _candidate():
    return KnowledgeCandidate.model_validate(
        {
            "source_kind": "system_snapshot",
            "subjects": ["agents/wads-agent"],
            "suggested_page_type": "Agent",
            "summary": "WADS agent snapshot",
            "facts": [
                {
                    "name": "slot_keys",
                    "value": ["lotcd"],
                    "source_path": "canonical_request.AGENT_SLOT_RULES.wads_agent",
                }
            ],
            "evidence_refs": [
                {"kind": "snapshot", "ref": "snapshot_1", "sha256": "a" * 64}
            ],
        }
    )


def _draft(body=""):
    return PageDraft(
        page_id="agents/wads-agent",
        page_type="Agent",
        title="WADS Agent",
        description="WADS worker contract",
        body_markdown=body or "# WADS Agent\n\nReads task-scoped parameters.\n",
        evidence_refs=["snapshot_1"],
    )


def test_candidate_is_immutable_and_deduplicated(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    first = store.save_candidate(_candidate())
    second = store.save_candidate(_candidate())
    assert first == second
    assert len(list((tmp_path / "raw/candidates").glob("*.json"))) == 1


def test_page_update_preserves_unknown_metadata_and_increments_version(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    path = tmp_path / "wiki/agents/wads-agent.md"
    post = frontmatter.load(path)
    post.metadata["custom_field"] = "keep-me"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    store.write_page(_draft("# WADS Agent\n\nUses ResultEnvelope.\n"), _candidate())
    updated = frontmatter.load(path)
    assert updated.metadata["custom_field"] == "keep-me"
    assert updated.metadata["version"] == 2
    assert "Uses ResultEnvelope" in updated.content


def test_identical_write_does_not_increment_version(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    store.write_page(_draft(), _candidate())
    assert frontmatter.load(tmp_path / "wiki/agents/wads-agent.md").metadata["version"] == 1


def test_replace_failure_preserves_existing_page(tmp_path, monkeypatch):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    path = tmp_path / "wiki/agents/wads-agent.md"
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        store.write_page(_draft("# WADS Agent\n\nChanged.\n"), _candidate())
    assert path.read_text(encoding="utf-8") == before


def test_proposal_does_not_modify_canonical_page(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    proposal = store.write_proposal(_draft(), _candidate(), rationale="human review")
    assert proposal.parent.name == "review_queue"
    assert not (tmp_path / "wiki/agents/wads-agent.md").exists()
