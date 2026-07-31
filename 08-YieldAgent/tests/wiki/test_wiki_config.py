import os
import stat
from pathlib import Path

import pytest
import wiki_config

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


def test_initialize_creates_index_and_log_with_same_directory_atomic_create(
    tmp_path, monkeypatch
):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    original_link = wiki_config.os.link
    promotions = []

    def record_link(source, destination):
        promotions.append((Path(source), Path(destination)))
        original_link(source, destination)

    monkeypatch.setattr(wiki_config.os, "link", record_link)
    initialize_wiki_vault(paths)

    assert {destination for _, destination in promotions} == {paths.index, paths.log}
    assert all(source.parent == destination.parent for source, destination in promotions)


def test_initialize_preserves_file_created_during_promotion(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    original_link = wiki_config.os.link

    def create_winner(source, destination):
        destination = Path(destination)
        if destination == paths.log:
            destination.write_text("winner", encoding="utf-8")
            raise FileExistsError
        original_link(source, destination)

    monkeypatch.setattr(wiki_config.os, "link", create_winner)
    initialize_wiki_vault(paths)

    assert paths.log.read_text(encoding="utf-8") == "winner"


def test_validate_reports_unwritable_vault(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)

    def fail_write_text(self, *args, **kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(Path, "write_text", fail_write_text)
    with pytest.raises(WikiConfigurationError, match="not writable"):
        validate_wiki_vault(paths)


def test_validate_probes_every_managed_writer_directory(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    original_write_text = Path.write_text
    probed = []

    def record_write_text(self, *args, **kwargs):
        if self.name.startswith(".write-probe-"):
            probed.append(self.parent)
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", record_write_text)
    validate_wiki_vault(paths)

    assert set(probed) == {
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    }


def test_validate_rejects_read_only_managed_directory(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses POSIX mode permission checks")
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    original_mode = stat.S_IMODE(paths.concepts.stat().st_mode)
    paths.concepts.chmod(original_mode & ~0o222)
    try:
        with pytest.raises(WikiConfigurationError, match=str(paths.concepts)):
            validate_wiki_vault(paths)
    finally:
        paths.concepts.chmod(original_mode)


def test_validate_rejects_non_appendable_log_without_changing_content(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses POSIX mode permission checks")
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    paths.log.write_text("# retained log\n\nentry\n", encoding="utf-8")
    original_content = paths.log.read_bytes()
    original_mode = stat.S_IMODE(paths.log.stat().st_mode)
    paths.log.chmod(original_mode & ~0o222)
    try:
        with pytest.raises(WikiConfigurationError, match=str(paths.log)):
            validate_wiki_vault(paths)
        assert paths.log.read_bytes() == original_content
    finally:
        paths.log.chmod(original_mode)


def test_validate_attempts_probe_cleanup_when_writing_fails(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    cleanup_attempts = []

    def fail_write_text(self, *args, **kwargs):
        raise PermissionError("write failed")

    def fail_unlink(self, *args, **kwargs):
        cleanup_attempts.append(self)
        raise PermissionError("cleanup failed")

    monkeypatch.setattr(Path, "write_text", fail_write_text)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(WikiConfigurationError, match="not writable") as exc_info:
        validate_wiki_vault(paths)

    assert len(cleanup_attempts) == 1
    assert cleanup_attempts[0].parent == paths.episodes
    assert cleanup_attempts[0].name.startswith(".write-probe-")
    assert isinstance(exc_info.value.__cause__, PermissionError)
    assert str(exc_info.value.__cause__) == "write failed"


def test_validate_removes_write_probe(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    validate_wiki_vault(paths)
    for directory in (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    ):
        assert list(directory.glob(".write-probe-*")) == []


def test_agent_server_prepares_vault_before_queue_start():
    source = (Path(__file__).resolve().parents[2] / "agent_server.py").read_text(
        encoding="utf-8"
    )
    lifespan = source.split("async def lifespan", 1)[1]
    assert lifespan.index("initialize_wiki_vault") < lifespan.index(
        "await wiki_queue.start()"
    )
    assert lifespan.index("validate_wiki_vault") < lifespan.index(
        "await wiki_queue.start()"
    )
