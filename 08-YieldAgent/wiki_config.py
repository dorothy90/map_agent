from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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
    sources: Path
    reviews: Path
    attachments: Path
    lint_logs: Path
    state_dir: Path
    log: Path
    index: Path
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
        sources=root / "sources",
        reviews=root / "reviews",
        attachments=root / "attachments",
        lint_logs=root / "lint_logs",
        state_dir=state_dir,
        log=root / "log.md",
        index=root / "index.md",
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
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _initialize_file(paths.log, "# Wiki Operation Log\n\n")
    _initialize_file(paths.index, "# Wiki Index\n\n")


def _managed_writer_directories(paths: WikiPaths) -> tuple[Path, ...]:
    return (
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


def _validate_writable_directory(directory: Path) -> None:
    probe = directory / f".write-probe-{uuid.uuid4().hex}"
    error: OSError | None = None
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        error = exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError as exc:
            if error is None:
                error = exc
    if error is not None:
        raise WikiConfigurationError(
            f"Wiki Vault path is not writable: {directory}"
        ) from error


def validate_wiki_vault(paths: WikiPaths) -> None:
    for directory in _managed_writer_directories(paths):
        _validate_writable_directory(directory)
    try:
        descriptor = os.open(paths.log, os.O_WRONLY | os.O_APPEND)
    except OSError as exc:
        raise WikiConfigurationError(
            f"Wiki Vault log is not appendable: {paths.log}"
        ) from exc
    else:
        os.close(descriptor)
