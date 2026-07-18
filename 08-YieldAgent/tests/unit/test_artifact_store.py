import asyncio
import hashlib
import os
import stat

import pytest
from pydantic import ValidationError

from artifact_context import artifact_scope, drain_saved_refs, save_artifact
from artifact_store import ArtifactRef, ArtifactStore


def test_write_uses_job_directory_and_checksum(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")

    ref = store.write_text("<h1>ok</h1>", mime="text/html", title="yield")

    assert ref.relative_path.startswith("jobs/u123/j123/output/")
    assert ref.size == len(b"<h1>ok</h1>")
    assert ref.checksum == hashlib.sha256(b"<h1>ok</h1>").hexdigest()
    with store.open(ref) as artifact:
        assert artifact.read() == b"<h1>ok</h1>"
    assert not list((tmp_path / "jobs/u123/j123/temp").iterdir())
    for name in ("input", "output", "temp"):
        mode = stat.S_IMODE((tmp_path / "jobs/u123/j123" / name).stat().st_mode)
        assert mode == 0o750


def test_artifact_ref_is_immutable():
    ref = ArtifactRef(
        artifact_id="a",
        relative_path="jobs/u/j/output/a",
        artifact_type="markdown",
        mime="text/plain",
        title="x",
        agent="yield_agent",
        size=1,
        checksum="x",
    )

    with pytest.raises(ValidationError):
        ref.title = "changed"


@pytest.mark.parametrize("component", ["", ".", "..", "a/b", "a\\b", "a\0b"])
def test_store_rejects_unsafe_path_components(tmp_path, component):
    with pytest.raises(ValueError, match="safe path component"):
        ArtifactStore(tmp_path, owner_hash=component, job_id="j123")


def test_store_accepts_current_hash_and_uuid_formats(tmp_path):
    store = ArtifactStore(
        tmp_path,
        owner_hash="d4e5f6_ABC-123",
        job_id="123e4567-e89b-12d3-a456-426614174000",
    )

    assert store.job_id == "123e4567-e89b-12d3-a456-426614174000"


def test_open_rejects_escape(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")
    ref = ArtifactRef(
        artifact_id="a",
        relative_path="../../secret",
        mime="text/plain",
        size=1,
        checksum="x",
        title="x",
        artifact_type="markdown",
    )

    with pytest.raises(ValueError, match="artifact path"):
        store.open(ref)


def test_open_rejects_reference_for_another_artifact(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")
    ref = store.write_text("safe", mime="text/plain", title="safe")
    forged = ref.model_copy(
        update={"relative_path": ref.relative_path.replace(ref.artifact_id, "other")}
    )

    with pytest.raises(ValueError, match="artifact path"):
        store.open(forged)


def test_failed_replace_removes_temporary_file(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")

    def fail_replace(source, destination):
        raise OSError("NAS unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="NAS unavailable"):
        store.write_bytes(b"data", mime="application/octet-stream", title="data")

    assert not list((tmp_path / "jobs/u123/j123/temp").iterdir())


def test_artifact_url_does_not_expose_nas_path(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")
    ref = store.write_text("safe", mime="text/plain", title="safe")

    assert store.artifact_url(ref) == f"/jobs/j123/artifacts/{ref.artifact_id}"
    assert str(tmp_path) not in store.artifact_url(ref)


def test_scope_saves_and_drains_only_new_references(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")

    with artifact_scope(store):
        first = save_artifact("one", "text/plain", "one", "yield_agent", "markdown")
        assert drain_saved_refs() == [first]
        assert drain_saved_refs() == []
        second = save_artifact(b"two", "text/plain", "two", "map_agent", "image")
        assert drain_saved_refs() == [second]


def test_save_artifact_requires_worker_scope():
    with pytest.raises(RuntimeError, match="artifact scope"):
        save_artifact("data", "text/plain", "x", "yield_agent", "markdown")


def test_context_scope_isolated_between_concurrent_tasks(tmp_path):
    async def save_for(job_id):
        store = ArtifactStore(tmp_path, owner_hash="u123", job_id=job_id)
        with artifact_scope(store):
            saved = save_artifact(job_id, "text/plain", job_id, "yield_agent", "markdown")
            await asyncio.sleep(0)
            return saved, drain_saved_refs()

    async def run():
        return await asyncio.gather(save_for("job-a"), save_for("job-b"))

    results = asyncio.run(run())

    assert results[0][1] == [results[0][0]]
    assert results[1][1] == [results[1][0]]
    assert results[0][0] != results[1][0]
