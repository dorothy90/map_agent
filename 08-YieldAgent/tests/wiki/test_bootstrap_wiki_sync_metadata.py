from types import SimpleNamespace

import pytest

import bootstrap_wiki_warmup as bootstrap
from wiki_manifest import load_manifest


pytestmark = pytest.mark.no_server


def _triple():
    return {
        "product": "4SS",
        "fail_type": "EASY(W)",
        "cause_oper": "PRE METAL CLN",
        "source": "test",
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply", "--product", "4SS"],
        ["--apply", "--product", "4SS", "--fail-type", "EASY"],
        ["--apply", "--cause-oper", "PRE METAL CLN"],
    ],
)
def test_exact_bootstrap_filters_are_all_or_none(monkeypatch, argv):
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap_wiki_warmup.py", *argv])

    with pytest.raises(SystemExit):
        bootstrap.main()


def test_exact_bootstrap_runs_only_the_requested_triple(monkeypatch):
    processed = []
    monkeypatch.setattr(
        bootstrap.sys,
        "argv",
        [
            "bootstrap_wiki_warmup.py",
            "--apply",
            "--product",
            "4SS",
            "--fail-type",
            "EASY",
            "--cause-oper",
            "PRE METAL CLN",
            "--no-lint",
        ],
    )
    monkeypatch.setattr(
        bootstrap,
        "fetch_opensearch_triples",
        lambda **kwargs: pytest.fail("aggregation must not run for exact bootstrap"),
    )
    monkeypatch.setattr(
        bootstrap,
        "process_triple",
        lambda triple, max_docs: processed.append(triple) or ("ok", "ok"),
    )
    monkeypatch.setattr(bootstrap.wiki_store, "counts", lambda: {})
    monkeypatch.setattr(
        bootstrap.wiki_store,
        "materialize_obsidian_wiki",
        lambda: SimpleNamespace(errors=()),
    )

    assert bootstrap.main() == 0
    assert processed == [
        {
            "product": "4SS",
            "fail_type": "EASY",
            "cause_oper": "PRE METAL CLN",
            "priority": "exact",
            "source": "operator",
        }
    ]


def test_successful_bootstrap_records_shared_fingerprint_metadata_and_manifest(
    tmp_path, monkeypatch
):
    documents = [
        {
            "doc_id": "FH-1",
            "content": "source",
            "cause": "cause",
            "action": "action",
            "comment": "comment",
            "date": "2026-08-01",
            "source_file": "FH-1.pptx",
            "product": "4SS",
            "fail_type": "EASY(W)",
            "cause_oper": "PRE METAL CLN",
        }
    ]
    captured = []
    manifest_path = tmp_path / ".yield-wiki" / "manifest.json"
    monkeypatch.setattr(bootstrap, "_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(
        bootstrap, "fetch_docs_for_triple", lambda *args, **kwargs: documents
    )
    monkeypatch.setattr(
        bootstrap,
        "synthesize_concept_from_docs",
        lambda *args: SimpleNamespace(
            body_markdown="body", confidence=0.8, citations=[]
        ),
    )

    def upsert(*args, **kwargs):
        captured.append(kwargs)
        return "4SS|PRE METAL CLN|EASY", "created"

    monkeypatch.setattr(bootstrap.wiki_store, "upsert_concept", upsert)
    monkeypatch.setattr(
        bootstrap.wiki_store,
        "read_node",
        lambda node_id: {"frontmatter": {"version": 1}},
    )

    status, _ = bootstrap.process_triple(_triple(), max_docs=15)

    assert status == "ok"
    metadata = captured[0]["sync_metadata"]
    assert metadata["source_doc_ids"] == ["FH-1"]
    assert metadata["evidence_count"] == 1
    assert metadata["evidence_scope"] == "single_source"
    assert metadata["sync_job_id"].startswith("bootstrap:sha256:")
    manifest = load_manifest(manifest_path, bootstrap._OPENSEARCH_INDEX)
    assert manifest["triples"]["4SS|PRE METAL CLN|EASY"][
        "source_fingerprint"
    ] == metadata["source_fingerprint"]
