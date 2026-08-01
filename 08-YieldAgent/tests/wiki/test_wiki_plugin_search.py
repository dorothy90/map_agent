import pytest
import frontmatter

import fail_history_tools
import wiki_plugin_search
from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


def make_vault_with_concept(tmp_path, product, fail_type, cause_oper):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    (paths.concepts / "4SS_PRE_METAL_CLN_EASY.md").write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="## Analysis\n",
                id="concept:4SS|PRE METAL CLN|EASY",
                type="concept",
                product=product,
                fail_type=fail_type,
                cause_oper=cause_oper,
            )
        ),
        encoding="utf-8",
    )
    return paths


def make_empty_vault(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    return paths


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_embedding_failure_uses_bm25_and_marks_fallback(monkeypatch):
    monkeypatch.setattr(
        fail_history_tools,
        "_get_embedding",
        lambda query: (_ for _ in ()).throw(RuntimeError("embedding offline")),
    )
    monkeypatch.setattr(
        fail_history_tools,
        "_search_bm25",
        lambda **kwargs: [{"doc_id": "FH-1"}],
        raising=False,
    )

    assert hasattr(fail_history_tools, "search_opensearch_with_mode")
    results, mode = fail_history_tools.search_opensearch_with_mode(
        "oxide",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        top_k=5,
        allow_embedding_fallback=True,
    )

    assert results == [{"doc_id": "FH-1"}]
    assert mode == "bm25_fallback"


def test_legacy_search_keeps_list_result_contract(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fail_history_tools,
        "search_opensearch_with_mode",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ([{"doc_id": "FH-1"}], "hybrid"),
    )

    result = fail_history_tools._search_opensearch("oxide", top_k=5)

    assert result == [{"doc_id": "FH-1"}]
    assert calls[0][1]["allow_embedding_fallback"] is False


def test_search_groups_hits_under_materialized_concept(tmp_path, monkeypatch):
    paths = make_vault_with_concept(tmp_path, "4SS", "EASY", "PRE METAL CLN")
    monkeypatch.setattr(
        wiki_plugin_search,
        "search_opensearch_with_mode",
        lambda *args, **kwargs: (
            [
                {
                    "doc_id": "FH-1",
                    "product": "4SS",
                    "fail_type": "EASY(W)",
                    "cause_oper": "PRE METAL CLN",
                    "content": "oxide",
                    "score": 88.0,
                },
                {
                    "doc_id": "FH-2",
                    "product": "4SS",
                    "fail_type": "EASY",
                    "cause_oper": "PRE METAL CLN",
                    "content": "clean",
                    "score": 72.0,
                },
            ],
            "hybrid",
        ),
    )

    result = wiki_plugin_search.search_wiki(
        "oxide", "4SS", "EASY", "PRE METAL CLN", 20, paths
    )

    assert len(result.results) == 1
    assert result.results[0].concept_status == "materialized"
    assert result.results[0].concept_path == "concepts/4SS_PRE_METAL_CLN_EASY.md"
    assert [item.doc_id for item in result.results[0].evidence] == ["FH-1", "FH-2"]


def test_search_without_concept_is_read_only(tmp_path, monkeypatch):
    paths = make_empty_vault(tmp_path)
    before = snapshot(paths.root)
    monkeypatch.setattr(
        wiki_plugin_search,
        "search_opensearch_with_mode",
        lambda *args, **kwargs: (
            [
                {
                    "doc_id": "FH-9",
                    "product": "4SS",
                    "fail_type": "EASY",
                    "cause_oper": "PRE METAL CLN",
                    "content": "source",
                    "score": 50.0,
                },
            ],
            "hybrid",
        ),
    )

    result = wiki_plugin_search.search_wiki(
        "source", "4SS", "EASY", "PRE METAL CLN", 20, paths
    )

    assert result.results[0].concept_status == "source_only"
    assert snapshot(paths.root) == before


def test_evidence_source_path_requires_materialized_source(tmp_path, monkeypatch):
    paths = make_empty_vault(tmp_path)
    (paths.sources / "FH-1.md").write_text("# Source\n", encoding="utf-8")
    monkeypatch.setattr(
        wiki_plugin_search,
        "search_opensearch_with_mode",
        lambda *args, **kwargs: (
            [
                {
                    "doc_id": "FH-1",
                    "product": "4SS",
                    "fail_type": "EASY",
                    "cause_oper": "PRE METAL CLN",
                    "score": 50.0,
                },
                {
                    "doc_id": "FH-2",
                    "product": "4SS",
                    "fail_type": "EASY",
                    "cause_oper": "PRE METAL CLN",
                    "score": 40.0,
                },
            ],
            "hybrid",
        ),
    )

    result = wiki_plugin_search.search_wiki(
        "source", "4SS", "EASY", "PRE METAL CLN", 20, paths
    )

    assert result.results[0].evidence[0].source_path == "sources/FH-1.md"
    assert result.results[0].evidence[1].source_path is None


def test_evidence_source_path_uses_materializer_filename(tmp_path, monkeypatch):
    paths = make_empty_vault(tmp_path)
    (paths.sources / "FH_1.md").write_text("# Source\n", encoding="utf-8")
    monkeypatch.setattr(
        wiki_plugin_search,
        "search_opensearch_with_mode",
        lambda *args, **kwargs: (
            [
                {
                    "doc_id": "FH:1",
                    "product": "4SS",
                    "fail_type": "EASY",
                    "cause_oper": "PRE METAL CLN",
                    "score": 50.0,
                },
            ],
            "hybrid",
        ),
    )

    result = wiki_plugin_search.search_wiki(
        "source", "4SS", "EASY", "PRE METAL CLN", 20, paths
    )

    assert result.results[0].evidence[0].source_path == "sources/FH_1.md"
