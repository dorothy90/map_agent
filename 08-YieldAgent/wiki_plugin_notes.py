from __future__ import annotations

import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

import frontmatter
from yaml import YAMLError

from models import PluginNoteLink, PluginRelatedResponse, PluginSourceResponse
from wiki_config import WikiPaths
from wiki_materializer import _stable_filename


class NoteNotFound(FileNotFoundError):
    pass


_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def resolve_markdown_path(paths: WikiPaths, note_path: str) -> Path:
    relative = PurePosixPath(note_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise NoteNotFound(note_path)
    if relative.suffix == "":
        relative = relative.with_suffix(".md")
    if relative.suffix.lower() != ".md":
        raise NoteNotFound(note_path)
    candidate = paths.root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise NoteNotFound(note_path) from exc
    if not resolved.is_relative_to(paths.root) or not resolved.is_file():
        raise NoteNotFound(note_path)
    return resolved


def extract_wikilinks(body: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1).strip() for match in _WIKILINK.finditer(body)}))


def _relative(paths: WikiPaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


def _load_note(path: Path) -> tuple[dict[str, Any], str]:
    try:
        post = frontmatter.load(path)
    except (OSError, ValueError, YAMLError) as exc:
        raise NoteNotFound(str(path)) from exc
    return dict(post.metadata), post.content or ""


def load_note_context(
    paths: WikiPaths, note_path: str, max_body_chars: int = 20000
) -> dict:
    path = resolve_markdown_path(paths, note_path)
    metadata, body = _load_note(path)
    return {
        "note_path": _relative(paths, path),
        "metadata": metadata,
        "body_markdown": body[:max_body_chars],
    }


def _note_link(paths: WikiPaths, path: Path) -> PluginNoteLink:
    metadata, _ = _load_note(path)
    return PluginNoteLink(
        path=_relative(paths, path),
        label=str(metadata.get("title") or path.stem),
        node_type=str(metadata.get("type") or ""),
    )


def _resolve_wikilinks(paths: WikiPaths, body: str) -> tuple[Path, ...]:
    resolved = []
    for target in extract_wikilinks(body):
        try:
            resolved.append(resolve_markdown_path(paths, target))
        except NoteNotFound:
            continue
    return tuple(sorted(set(resolved), key=lambda path: _relative(paths, path)))


def related_notes(paths: WikiPaths, note_path: str) -> PluginRelatedResponse:
    note = resolve_markdown_path(paths, note_path)
    _, body = _load_note(note)
    outgoing_paths = _resolve_wikilinks(paths, body)
    backlinks = []
    for candidate in paths.root.rglob("*"):
        if candidate.suffix.lower() != ".md":
            continue
        try:
            resolved = resolve_markdown_path(paths, _relative(paths, candidate))
        except (NoteNotFound, ValueError):
            continue
        if resolved == note:
            continue
        try:
            _, candidate_body = _load_note(resolved)
        except NoteNotFound:
            continue
        if note in _resolve_wikilinks(paths, candidate_body):
            backlinks.append(resolved)
    backlinks.sort(key=lambda path: _relative(paths, path))
    return PluginRelatedResponse(
        note_path=_relative(paths, note),
        outgoing=[_note_link(paths, path) for path in outgoing_paths],
        backlinks=[_note_link(paths, path) for path in backlinks],
    )


def read_source(paths: WikiPaths, doc_id: str) -> PluginSourceResponse:
    relative = PurePosixPath(doc_id)
    if not doc_id or relative.is_absolute() or ".." in relative.parts:
        raise NoteNotFound(doc_id)
    candidate = paths.sources / f"{_stable_filename(doc_id)}.md"
    try:
        candidate_mode = candidate.lstat().st_mode
    except OSError as exc:
        raise NoteNotFound(doc_id) from exc
    if candidate.parent != paths.sources or not stat.S_ISREG(candidate_mode):
        raise NoteNotFound(doc_id)
    source = resolve_markdown_path(paths, f"sources/{candidate.name}")
    if source != candidate:
        raise NoteNotFound(doc_id)
    metadata, _ = _load_note(source)
    source_doc_id = str(metadata.get("doc_id") or "").strip()
    if source_doc_id != doc_id or metadata.get("type") != "source":
        raise NoteNotFound(doc_id)
    page_num = metadata.get("page_num")
    return PluginSourceResponse(
        doc_id=source_doc_id,
        source_path=_relative(paths, source),
        source_file=str(metadata.get("source_file") or ""),
        date=str(metadata.get("date") or ""),
        page_num=page_num if page_num not in (None, "") else None,
        download_url=str(metadata.get("download_url") or ""),
        metadata=metadata,
    )
