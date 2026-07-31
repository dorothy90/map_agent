import os
import stat
from dataclasses import replace
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
    original_open = wiki_config.os.open

    def fail_probe_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith(".write-probe-"):
            raise PermissionError("read-only share")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(wiki_config.os, "open", fail_probe_open)
    with pytest.raises(WikiConfigurationError, match="not writable"):
        validate_wiki_vault(paths)


def test_validate_probes_every_managed_writer_directory(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    original_open = wiki_config.os.open
    probed = []

    def record_probe_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith(".write-probe-"):
            info = os.fstat(dir_fd)
            probed.append((info.st_dev, info.st_ino))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(wiki_config.os, "open", record_probe_open)
    validate_wiki_vault(paths)

    expected_directories = {
        path
        for path in (
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
    }
    assert set(probed) == {
        (path.stat().st_dev, path.stat().st_ino) for path in expected_directories
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


def test_validate_rejects_symlinked_managed_directory_without_writing_outside(
    tmp_path,
):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    outside = tmp_path / "outside-concepts"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("retained", encoding="utf-8")
    before_entries = sorted(path.name for path in outside.iterdir())
    before_mtime = outside.stat().st_mtime_ns
    paths.concepts.rmdir()
    paths.concepts.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WikiConfigurationError, match=str(paths.concepts)):
        validate_wiki_vault(paths)

    assert sorted(path.name for path in outside.iterdir()) == before_entries
    assert outside.stat().st_mtime_ns == before_mtime
    assert sentinel.read_text(encoding="utf-8") == "retained"


def test_validate_rejects_symlinked_log_without_opening_outside_file(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    outside_log = tmp_path / "outside-log.md"
    outside_log.write_text("outside retained\n", encoding="utf-8")
    before_content = outside_log.read_bytes()
    before_stat = outside_log.stat()
    paths.log.unlink()
    paths.log.symlink_to(outside_log)

    with pytest.raises(WikiConfigurationError, match=str(paths.log)):
        validate_wiki_vault(paths)

    after_stat = outside_log.stat()
    assert outside_log.read_bytes() == before_content
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size


def test_validate_rejects_managed_path_outside_resolved_root_without_probe(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    outside = tmp_path / "outside-managed"
    outside.mkdir()
    before_mtime = outside.stat().st_mtime_ns
    escaped_paths = replace(paths, concepts=outside)

    with pytest.raises(WikiConfigurationError, match=str(outside)):
        validate_wiki_vault(escaped_paths)

    assert list(outside.iterdir()) == []
    assert outside.stat().st_mtime_ns == before_mtime


def test_validate_attempts_probe_cleanup_when_writing_fails(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    cleanup_attempts = []
    original_open = wiki_config.os.open

    def fail_probe_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith(".write-probe-"):
            raise PermissionError("write failed")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def fail_unlink(path, *, dir_fd=None):
        info = os.fstat(dir_fd)
        cleanup_attempts.append((str(path), info.st_dev, info.st_ino))
        raise PermissionError("cleanup failed")

    monkeypatch.setattr(wiki_config.os, "open", fail_probe_open)
    monkeypatch.setattr(wiki_config.os, "unlink", fail_unlink)
    with pytest.raises(WikiConfigurationError, match="not writable") as exc_info:
        validate_wiki_vault(paths)

    assert len(cleanup_attempts) == 1
    probe_name, device, inode = cleanup_attempts[0]
    episodes_info = paths.episodes.stat()
    assert probe_name.startswith(".write-probe-")
    assert (device, inode) == (episodes_info.st_dev, episodes_info.st_ino)
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
