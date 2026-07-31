from pathlib import Path

import pytest

from wiki_config import (
    WikiConfigurationError,
    initialize_wiki_vault,
    resolve_wiki_paths,
    validate_wiki_vault,
)


pytestmark = pytest.mark.no_server


def test_resolve_paths_from_explicit_environment(tmp_path):
    root = tmp_path / "YieldWiki"
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(root)})
    assert paths.root == root.resolve()
    assert paths.concepts == root.resolve() / "concepts"
    assert paths.sources == root.resolve() / "sources"
    assert paths.reviews == root.resolve() / "reviews"
    assert paths.configured is True


def test_require_external_rejects_missing_path(tmp_path):
    with pytest.raises(WikiConfigurationError, match="WIKI_VAULT_PATH"):
        resolve_wiki_paths(
            {"WIKI_REQUIRE_EXTERNAL_VAULT": "true"},
            default_root=tmp_path / "repo-wiki",
        )


def test_initialize_creates_complete_m1_layout(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    expected = (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    )
    assert all(path.is_dir() for path in expected)
    assert paths.index.read_text(encoding="utf-8") == "# Wiki Index\n\n"
    assert paths.log.read_text(encoding="utf-8") == "# Wiki Operation Log\n\n"


def test_validate_reports_unwritable_vault(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)

    def fail_write_text(self, *args, **kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(Path, "write_text", fail_write_text)
    with pytest.raises(WikiConfigurationError, match="not writable"):
        validate_wiki_vault(paths)
