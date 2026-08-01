from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_server


def _reload_store(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_VAULT_PATH", str(tmp_path / "YieldWiki"))
    import wiki_store

    return importlib.reload(wiki_store)


def test_concept_upsert_materializes_once_by_default_and_can_defer(
    tmp_path, monkeypatch
):
    wiki_store = _reload_store(tmp_path, monkeypatch)
    import wiki_materializer

    calls = []
    monkeypatch.setattr(
        wiki_materializer,
        "materialize_wiki",
        lambda paths, *, apply: calls.append((paths, apply)),
    )
    filters = {
        "product": "4SS",
        "fail_type": "EASY",
        "cause_oper": "PRE METAL CLN",
    }

    wiki_store.upsert_concept(filters)
    wiki_store.upsert_concept(filters, materialize=False)

    assert calls == [(wiki_store._PATHS, True)]


def test_super_concept_upsert_materializes_once_by_default_and_can_defer(
    tmp_path, monkeypatch
):
    wiki_store = _reload_store(tmp_path, monkeypatch)
    import wiki_materializer

    calls = []
    monkeypatch.setattr(
        wiki_materializer,
        "materialize_wiki",
        lambda paths, *, apply: calls.append((paths, apply)),
    )

    wiki_store.upsert_super_concept(
        "fail_type",
        "EASY",
        ["concept:4SS|PRE METAL CLN|EASY"],
        "body",
        0.5,
    )
    wiki_store.upsert_super_concept(
        "fail_type",
        "EASY",
        ["concept:4SS|PRE METAL CLN|EASY"],
        "body",
        0.5,
        materialize=False,
    )

    assert calls == [(wiki_store._PATHS, True)]


def test_bootstrap_process_triple_defers_concept_materialization(monkeypatch):
    import bootstrap_wiki_warmup as bootstrap

    captured = []
    monkeypatch.setattr(
        bootstrap,
        "fetch_docs_for_triple",
        lambda *args, **kwargs: [{"doc_id": "FH-1", "date": "2025-01-01"}],
    )
    monkeypatch.setattr(
        bootstrap,
        "synthesize_concept_from_docs",
        lambda *args: SimpleNamespace(
            body_markdown="body", confidence=0.8, citations=[]
        ),
    )
    monkeypatch.setattr(
        bootstrap.wiki_store,
        "upsert_concept",
        lambda *args, **kwargs: captured.append(kwargs),
    )

    status, _ = bootstrap.process_triple(
        {
            "product": "4SS",
            "fail_type": "EASY",
            "cause_oper": "PRE METAL CLN",
        },
        max_docs=5,
    )

    assert status == "ok"
    assert captured[0]["materialize"] is False


def test_bootstrap_apply_materializes_once_after_the_batch(monkeypatch):
    import bootstrap_wiki_warmup as bootstrap

    seeds = [
        {
            "product": "4SS",
            "fail_type": "EASY",
            "cause_oper": "PRE METAL CLN",
            "source": "test",
            "doc_count": 1,
        },
        {
            "product": "6SS",
            "fail_type": "HARD",
            "cause_oper": "STI CMP",
            "source": "test",
            "doc_count": 1,
        },
    ]
    calls = []
    monkeypatch.setattr(bootstrap, "load_foundations", lambda vault: [])
    monkeypatch.setattr(bootstrap, "fetch_opensearch_triples", lambda **kwargs: seeds)
    monkeypatch.setattr(bootstrap, "merge_seeds", lambda foundations, aggregated: aggregated)
    monkeypatch.setattr(bootstrap, "process_triple", lambda *args, **kwargs: ("ok", "ok"))
    monkeypatch.setattr(bootstrap.wiki_store, "counts", lambda: {})
    monkeypatch.setattr(
        bootstrap.wiki_store,
        "materialize_obsidian_wiki",
        lambda: calls.append(True) or SimpleNamespace(errors=()),
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["bootstrap_wiki_warmup.py", "--apply", "--no-lint"],
    )

    result = bootstrap.main()

    assert result == 0
    assert calls == [True]
