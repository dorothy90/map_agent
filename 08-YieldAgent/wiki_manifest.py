"""Atomic portable success manifest for the incremental Wiki sync."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from wiki_sync import TripleSnapshot


SCHEMA_VERSION = 1


class ManifestError(RuntimeError):
    """Raised when an existing manifest cannot be trusted."""


def empty_manifest(index: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "index": index,
        "updated_at": None,
        "triples": {},
    }


def load_manifest(path: Path, index: str) -> dict[str, Any]:
    if not path.exists():
        return empty_manifest(index)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Wiki manifest is unreadable: {path}") from exc
    if not isinstance(manifest, dict):
        raise ManifestError(f"Wiki manifest root must be an object: {path}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"Unsupported Wiki manifest schema: {path}")
    if manifest.get("index") != index:
        raise ManifestError(f"Wiki manifest index does not match {index}: {path}")
    if not isinstance(manifest.get("triples"), dict):
        raise ManifestError(f"Wiki manifest triples must be an object: {path}")
    return manifest


def record_success(
    manifest: dict[str, Any],
    snapshot: TripleSnapshot,
    *,
    concept_id: str,
    concept_version: int,
    success_at: str,
) -> bool:
    entry = {
        "source_fingerprint": snapshot.source_fingerprint,
        "source_doc_ids": list(snapshot.source_doc_ids),
        "evidence_count": snapshot.evidence_count,
        "concept_id": concept_id,
        "concept_version": concept_version,
        "last_success_at": success_at,
    }
    triples = manifest.setdefault("triples", {})
    if triples.get(snapshot.key.canonical) == entry:
        return False
    triples[snapshot.key.canonical] = entry
    manifest["updated_at"] = success_at
    return True


def projection_needs_repair(manifest: dict[str, Any]) -> bool:
    projection = manifest.get("projection")
    return isinstance(projection, dict) and projection.get("status") in {
        "dirty",
        "failed",
    }


def set_projection_state(
    manifest: dict[str, Any],
    status: Literal["dirty", "failed", "clean"],
    updated_at: str,
    *,
    error: str | None = None,
) -> None:
    projection = {"status": status, "updated_at": updated_at}
    if error:
        projection["last_error"] = " ".join(error.split())[:500]
    manifest["projection"] = projection
    manifest["updated_at"] = updated_at


def _serialized(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def save_manifest(path: Path, manifest: dict[str, Any]) -> bool:
    content = _serialized(manifest)
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True
