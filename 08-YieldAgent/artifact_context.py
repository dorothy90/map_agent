from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from artifact_store import ArtifactRef, ArtifactStore


_store: ContextVar[ArtifactStore | None] = ContextVar("artifact_store", default=None)
_saved_refs: ContextVar[tuple[ArtifactRef, ...]] = ContextVar("saved_artifact_refs", default=())
_drained_count: ContextVar[int] = ContextVar("drained_artifact_count", default=0)


@contextmanager
def artifact_scope(store: ArtifactStore) -> Iterator[ArtifactStore]:
    store_token = _store.set(store)
    refs_token = _saved_refs.set(())
    drained_token = _drained_count.set(0)
    try:
        yield store
    finally:
        _drained_count.reset(drained_token)
        _saved_refs.reset(refs_token)
        _store.reset(store_token)


def save_artifact(
    content: str | bytes,
    mime: str,
    title: str,
    agent: str,
    artifact_type: str,
) -> ArtifactRef:
    store = _require_store()
    if isinstance(content, str):
        ref = store.write_text(
            content,
            mime=mime,
            title=title,
            agent=agent,
            artifact_type=artifact_type,
        )
    elif isinstance(content, bytes):
        ref = store.write_bytes(
            content,
            mime=mime,
            title=title,
            agent=agent,
            artifact_type=artifact_type,
        )
    else:
        raise TypeError("artifact content must be str or bytes")
    _saved_refs.set((*_saved_refs.get(), ref))
    return ref


def drain_saved_refs() -> list[ArtifactRef]:
    _require_store()
    refs = _saved_refs.get()
    drained_count = _drained_count.get()
    _drained_count.set(len(refs))
    return list(refs[drained_count:])


def artifact_url(ref: ArtifactRef) -> str:
    """Return the authorized relative URL for an artifact in the active scope."""
    return _require_store().artifact_url(ref)


def _require_store() -> ArtifactStore:
    store = _store.get()
    if store is None:
        raise RuntimeError("artifact scope is not active")
    return store
