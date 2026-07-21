from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_models import (
    CurationDecision,
    KnowledgeCandidate,
    PageDraft,
    PageType,
    candidate_fingerprint,
)

pytestmark = pytest.mark.no_server


def _candidate(**updates):
    payload = {
        "source_kind": "system_snapshot",
        "subjects": ["contracts/result-envelope"],
        "suggested_page_type": "Contract",
        "summary": "Result contract snapshot changed",
        "facts": [
            {
                "name": "schema_version",
                "value": "result-envelope/v1",
                "source_path": "result_contracts.RESULT_ENVELOPE_SCHEMA_VERSION",
            }
        ],
        "evidence_refs": [
            {"kind": "snapshot", "ref": "snapshot_1", "sha256": "a" * 64}
        ],
    }
    payload.update(updates)
    return KnowledgeCandidate.model_validate(payload)


def test_candidate_rejects_payload_fields_recursively():
    with pytest.raises(ValidationError, match="forbidden payload key"):
        _candidate(
            facts=[
                {
                    "name": "bad",
                    "value": {"nested": {"rows": [{"lot_id": "4SS0001"}]}},
                    "source_path": "result.rows",
                }
            ]
        )


def test_candidate_requires_exact_system_scope():
    with pytest.raises(ValidationError):
        _candidate(scope="tenant-a")


def test_fingerprint_ignores_candidate_identity_and_time():
    first = _candidate(candidate_id="candidate_a")
    second = _candidate(candidate_id="candidate_b")
    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_page_draft_requires_h1_equal_to_title():
    with pytest.raises(ValidationError, match="first H1"):
        PageDraft(
            page_id="agents/wads-agent",
            page_type=PageType.agent,
            title="WADS Agent",
            description="WADS worker boundary",
            body_markdown="# Different title\n",
        )


def test_no_change_cannot_carry_draft():
    draft = PageDraft(
        page_id="agents/wads-agent",
        page_type="Agent",
        title="WADS Agent",
        description="WADS worker boundary",
        body_markdown="# WADS Agent\n\nReads task-scoped parameters.\n",
    )
    with pytest.raises(ValidationError):
        CurationDecision(action="no_change", rationale="same", draft=draft)


def test_update_requires_draft_and_evidence():
    with pytest.raises(ValidationError):
        CurationDecision(
            action="update",
            target_page_id="agents/wads-agent",
            rationale="changed",
        )
