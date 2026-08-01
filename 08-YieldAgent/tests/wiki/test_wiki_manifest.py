import json

import pytest

from wiki_manifest import (
    ManifestError,
    empty_manifest,
    load_manifest,
    record_success,
    save_manifest,
)
from wiki_sync import build_triple_snapshot, make_triple_key


pytestmark = pytest.mark.no_server


def _snapshot():
    key = make_triple_key("4SS", "EASY(W)", "PRE METAL CLN")
    return build_triple_snapshot(
        key,
        [
            {
                "doc_id": "FH-1",
                "content": "source",
                "product": "4SS",
                "fail_type": "EASY(W)",
                "cause_oper": "PRE METAL CLN",
            }
        ],
    )


def test_missing_manifest_returns_a_new_in_memory_document(tmp_path):
    path = tmp_path / ".yield-wiki" / "manifest.json"

    manifest = load_manifest(path, "fail-history")

    assert manifest == empty_manifest("fail-history")
    assert not path.exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"schema_version": 2, "index": "fail-history", "triples": {}}),
        json.dumps({"schema_version": 1, "index": "other", "triples": {}}),
        json.dumps({"schema_version": 1, "index": "fail-history", "triples": []}),
    ],
)
def test_manifest_corruption_or_contract_mismatch_fails_closed(tmp_path, content):
    path = tmp_path / "manifest.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(path, "fail-history")


def test_record_success_writes_the_approved_entry_fields():
    manifest = empty_manifest("fail-history")
    snapshot = _snapshot()

    changed = record_success(
        manifest,
        snapshot,
        concept_id="concept:4SS|PRE METAL CLN|EASY",
        concept_version=2,
        success_at="2026-08-01T00:00:00+00:00",
    )

    assert changed is True
    assert manifest["updated_at"] == "2026-08-01T00:00:00+00:00"
    assert manifest["triples"][snapshot.key.canonical] == {
        "source_fingerprint": snapshot.source_fingerprint,
        "source_doc_ids": ["FH-1"],
        "evidence_count": 1,
        "concept_id": "concept:4SS|PRE METAL CLN|EASY",
        "concept_version": 2,
        "last_success_at": "2026-08-01T00:00:00+00:00",
    }


def test_recording_identical_success_is_a_no_op():
    manifest = empty_manifest("fail-history")
    snapshot = _snapshot()
    args = {
        "concept_id": "concept:4SS|PRE METAL CLN|EASY",
        "concept_version": 2,
        "success_at": "2026-08-01T00:00:00+00:00",
    }

    assert record_success(manifest, snapshot, **args) is True
    assert record_success(manifest, snapshot, **args) is False


def test_save_is_atomic_and_skips_byte_identical_rewrite(tmp_path, monkeypatch):
    path = tmp_path / ".yield-wiki" / "manifest.json"
    manifest = empty_manifest("fail-history")
    replacements = []

    import wiki_manifest

    original_replace = wiki_manifest.os.replace

    def record_replace(source, destination):
        replacements.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr(wiki_manifest.os, "replace", record_replace)

    assert save_manifest(path, manifest) is True
    first_bytes = path.read_bytes()
    assert save_manifest(path, manifest) is False

    assert path.read_bytes() == first_bytes
    assert len(replacements) == 1
    source, destination = replacements[0]
    assert source.parent == destination.parent == path.parent
    assert not source.exists()
