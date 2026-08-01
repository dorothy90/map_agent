from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


WIKI_PURPOSE_TEMPLATE = (
    "# Wiki Purpose\n\n"
    "Yield failure history를 검토하고 탐색하는 사내 Knowledge Wiki입니다.\n"
)
WIKI_SCHEMA_TEMPLATE = (
    "# Wiki Schema\n\n"
    "Product → Product Fail → Cause Operation → Concept → Source\n"
)
WIKI_OVERVIEW_TEMPLATE = "# Wiki Overview\n\n"


class WikiConfigurationError(RuntimeError):
    """Raised when the configured Wiki Vault cannot be used safely."""


@dataclass(frozen=True)
class WikiPaths:
    root: Path
    configured: bool
    episodes: Path
    concepts: Path
    aliases: Path
    super_concepts: Path
    products: Path
    product_fails: Path
    operations: Path
    sources: Path
    reviews: Path
    attachments: Path
    lint_logs: Path
    state_dir: Path
    log: Path
    index: Path
    purpose: Path
    schema: Path
    overview: Path
    obsidian: Path
    graph_config: Path
    manifest: Path


def _enabled(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes"}


def resolve_wiki_paths(
    env: Mapping[str, str] | None = None,
    *,
    default_root: Path | None = None,
) -> WikiPaths:
    values = os.environ if env is None else env
    configured_value = (values.get("WIKI_VAULT_PATH") or "").strip()
    configured = bool(configured_value)
    if _enabled(values.get("WIKI_REQUIRE_EXTERNAL_VAULT")) and not configured:
        raise WikiConfigurationError(
            "WIKI_VAULT_PATH is required when WIKI_REQUIRE_EXTERNAL_VAULT=true"
        )
    fallback = default_root or (Path(__file__).resolve().parent / "wiki")
    root = Path(configured_value).expanduser() if configured else fallback
    root = root.resolve()
    state_dir = root / ".yield-wiki"
    return WikiPaths(
        root=root,
        configured=configured,
        episodes=root / "episodes",
        concepts=root / "concepts",
        aliases=root / "aliases",
        super_concepts=root / "super_concepts",
        products=root / "products",
        product_fails=root / "product_fails",
        operations=root / "operations",
        sources=root / "sources",
        reviews=root / "reviews",
        attachments=root / "attachments",
        lint_logs=root / "lint_logs",
        state_dir=state_dir,
        log=root / "log.md",
        index=root / "index.md",
        purpose=root / "purpose.md",
        schema=root / "schema.md",
        overview=root / "overview.md",
        obsidian=root / ".obsidian",
        graph_config=root / ".obsidian" / "graph.json",
        manifest=state_dir / "manifest.json",
    )


def _initialize_file(path: Path, content: str) -> None:
    if path.exists():
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def initialize_wiki_vault(paths: WikiPaths) -> None:
    for directory in (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.products,
        paths.product_fails,
        paths.operations,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
        paths.obsidian,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _initialize_file(paths.log, "# Wiki Operation Log\n\n")
    _initialize_file(paths.index, "# Wiki Index\n\n")
    _initialize_file(paths.purpose, WIKI_PURPOSE_TEMPLATE)
    _initialize_file(paths.schema, WIKI_SCHEMA_TEMPLATE)
    _initialize_file(paths.overview, WIKI_OVERVIEW_TEMPLATE)


def _managed_writer_directories(paths: WikiPaths) -> tuple[Path, ...]:
    return (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.products,
        paths.product_fails,
        paths.operations,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
        paths.obsidian,
    )


def _inode_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _direct_child_name(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise WikiConfigurationError(
            f"Wiki Vault managed path escapes root: {path}"
        ) from exc
    if len(relative.parts) != 1:
        raise WikiConfigurationError(
            f"Wiki Vault managed path is not a direct child: {path}"
        )
    return relative.name


def _validate_root_identity(root: Path, root_descriptor: int) -> None:
    try:
        path_info = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise WikiConfigurationError(f"Wiki Vault root is unavailable: {root}") from exc
    descriptor_info = os.fstat(root_descriptor)
    if not stat.S_ISDIR(path_info.st_mode) or _inode_identity(path_info) != _inode_identity(
        descriptor_info
    ):
        raise WikiConfigurationError(f"Wiki Vault root path changed: {root}")


def _validate_directory_identity(
    root: Path,
    root_descriptor: int,
    directory: Path,
    directory_descriptor: int,
) -> None:
    _validate_root_identity(root, root_descriptor)
    name = _direct_child_name(root, directory)
    try:
        entry_info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WikiConfigurationError(
            f"Wiki Vault managed directory is unavailable: {directory}"
        ) from exc
    descriptor_info = os.fstat(directory_descriptor)
    if (
        stat.S_ISLNK(entry_info.st_mode)
        or not stat.S_ISDIR(entry_info.st_mode)
        or _inode_identity(entry_info) != _inode_identity(descriptor_info)
    ):
        raise WikiConfigurationError(
            f"Wiki Vault managed directory is not stable: {directory}"
        )
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise WikiConfigurationError(
            f"Wiki Vault managed directory is unavailable: {directory}"
        ) from exc
    if resolved != root / name or not resolved.is_relative_to(root):
        raise WikiConfigurationError(
            f"Wiki Vault managed directory escapes root: {directory}"
        )


def _open_managed_directory(
    root: Path,
    root_descriptor: int,
    directory: Path,
) -> int:
    name = _direct_child_name(root, directory)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
    except OSError as exc:
        raise WikiConfigurationError(
            f"Wiki Vault managed directory is not safe: {directory}"
        ) from exc
    try:
        _validate_directory_identity(root, root_descriptor, directory, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _validate_writable_directory(
    root: Path,
    root_descriptor: int,
    directory: Path,
) -> None:
    directory_descriptor = _open_managed_directory(
        root, root_descriptor, directory
    )
    probe_name = f".write-probe-{uuid.uuid4().hex}"
    probe_descriptor = -1
    error: OSError | None = None
    try:
        probe_descriptor = os.open(
            probe_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.write(probe_descriptor, b"ok")
    except OSError as exc:
        error = exc
    finally:
        if probe_descriptor >= 0:
            os.close(probe_descriptor)
        try:
            os.unlink(probe_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if error is None:
                error = exc
        try:
            _validate_directory_identity(
                root,
                root_descriptor,
                directory,
                directory_descriptor,
            )
        finally:
            os.close(directory_descriptor)
    if error is not None:
        raise WikiConfigurationError(f"Wiki Vault path is not writable: {directory}") from error


def _validate_appendable_log(
    root: Path,
    root_descriptor: int,
    log: Path,
) -> None:
    name = _direct_child_name(root, log)
    _validate_root_identity(root, root_descriptor)
    try:
        entry_info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WikiConfigurationError(f"Wiki Vault log is unavailable: {log}") from exc
    if stat.S_ISLNK(entry_info.st_mode) or not stat.S_ISREG(entry_info.st_mode):
        raise WikiConfigurationError(f"Wiki Vault log is not a regular file: {log}")
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
    except OSError as exc:
        raise WikiConfigurationError(f"Wiki Vault log is not appendable: {log}") from exc
    try:
        descriptor_info = os.fstat(descriptor)
        current_info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or stat.S_ISLNK(current_info.st_mode)
            or _inode_identity(descriptor_info) != _inode_identity(entry_info)
            or _inode_identity(current_info) != _inode_identity(entry_info)
        ):
            raise WikiConfigurationError(f"Wiki Vault log path changed: {log}")
        _validate_root_identity(root, root_descriptor)
        if log.resolve(strict=True) != root / name:
            raise WikiConfigurationError(f"Wiki Vault log escapes root: {log}")
    finally:
        os.close(descriptor)


def validate_wiki_vault(paths: WikiPaths) -> None:
    root = paths.root
    if root.resolve(strict=True) != root:
        raise WikiConfigurationError(f"Wiki Vault root contains a symlink: {root}")
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise WikiConfigurationError(f"Wiki Vault root is not safe: {root}") from exc
    try:
        _validate_root_identity(root, root_descriptor)
        for directory in _managed_writer_directories(paths):
            _validate_writable_directory(root, root_descriptor, directory)
        _validate_appendable_log(root, root_descriptor, paths.log)
    finally:
        os.close(root_descriptor)
