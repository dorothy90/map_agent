from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from artifact_cleanup import ArtifactCleanup


class AsyncCursor:
    def __init__(self, values):
        self._values = iter(values)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._values)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class Jobs:
    def __init__(self, jobs):
        self.jobs = jobs
        self.query = None
        self.updates = []

    def find(self, query, projection):
        self.query = query
        cutoff = query["artifact_expires_at"]["$lte"]
        selected = [
            job
            for job in self.jobs
            if job["status"] in query["status"]["$in"]
            and job.get("artifact_expires_at", cutoff + timedelta(days=1)) <= cutoff
            and "cleanup_at" not in job
        ]
        return AsyncCursor(selected)

    async def update_one(self, query, update):
        self.updates.append((query, update))


class Checkpoints:
    def __init__(self):
        self.deleted = []

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_terminal_job(tmp_path):
    now = datetime.now(timezone.utc)
    jobs = [
        _job("expired", "SUCCEEDED", now - timedelta(seconds=1)),
        _job("recent", "FAILED", now + timedelta(days=1)),
        _job("active", "RUNNING", now - timedelta(days=1)),
    ]
    collection = Jobs(jobs)
    checkpoints = Checkpoints()
    for job in jobs:
        path = tmp_path / "jobs" / job["owner_hash"] / job["job_id"]
        path.mkdir(parents=True)
        (path / "result.html").write_text(job["job_id"])

    result = await ArtifactCleanup(
        collection, tmp_path, checkpoints.delete_thread
    ).run(cutoff=now)

    assert result == {"cleaned": 1, "failed": 0}
    assert not (tmp_path / "jobs" / "owner" / "expired").exists()
    assert (tmp_path / "jobs" / "owner" / "recent").exists()
    assert (tmp_path / "jobs" / "owner" / "active").exists()
    assert set(collection.query["status"]["$in"]) == {
        "SUCCEEDED", "FAILED", "CANCELLED"
    }
    assert checkpoints.deleted == ["owner:expired-session"]
    assert "$set" in collection.updates[0][1]
    assert "cleanup_at" in collection.updates[0][1]["$set"]


@pytest.mark.asyncio
async def test_missing_job_directory_is_idempotent_success(tmp_path):
    now = datetime.now(timezone.utc)
    collection = Jobs([_job("missing", "CANCELLED", now - timedelta(days=1))])
    checkpoints = Checkpoints()

    result = await ArtifactCleanup(
        collection, tmp_path, checkpoints.delete_thread
    ).run(cutoff=now)

    assert result == {"cleaned": 1, "failed": 0}
    assert checkpoints.deleted == ["owner:missing-session"]
    assert "cleanup_error" in collection.updates[0][1]["$unset"]


@pytest.mark.asyncio
async def test_cleanup_refuses_external_symlink_and_records_safe_error(tmp_path):
    now = datetime.now(timezone.utc)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    job = _job("escaped", "SUCCEEDED", now - timedelta(days=1))
    link = tmp_path / "jobs" / "owner" / "escaped"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    collection = Jobs([job])

    result = await ArtifactCleanup(collection, tmp_path, lambda _: None).run(cutoff=now)

    assert result == {"cleaned": 0, "failed": 1}
    assert marker.read_text() == "keep"
    update = collection.updates[0][1]
    assert "cleanup_at" not in update.get("$set", {})
    assert update["$set"]["cleanup_error"] == "unsafe artifact path"


@pytest.mark.asyncio
async def test_cleanup_refuses_symlinked_owner_directory(tmp_path):
    now = datetime.now(timezone.utc)
    outside = tmp_path.parent / f"{tmp_path.name}-owner-outside"
    escaped_job = outside / "escaped-owner"
    escaped_job.mkdir(parents=True)
    marker = escaped_job / "keep.txt"
    marker.write_text("keep")
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    (jobs_root / "owner").symlink_to(outside, target_is_directory=True)
    collection = Jobs([_job("escaped-owner", "SUCCEEDED", now - timedelta(days=1))])

    result = await ArtifactCleanup(collection, tmp_path, lambda _: None).run(cutoff=now)

    assert result == {"cleaned": 0, "failed": 1}
    assert marker.read_text() == "keep"
    assert collection.updates[0][1]["$set"]["cleanup_error"] == "unsafe artifact path"


@pytest.mark.asyncio
async def test_cleanup_does_not_follow_nested_external_symlink(tmp_path):
    now = datetime.now(timezone.utc)
    outside = tmp_path.parent / f"{tmp_path.name}-nested-outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    job = _job("nested", "SUCCEEDED", now - timedelta(days=1))
    directory = tmp_path / "jobs" / "owner" / "nested"
    directory.mkdir(parents=True)
    (directory / "external").symlink_to(outside, target_is_directory=True)
    collection = Jobs([job])

    result = await ArtifactCleanup(collection, tmp_path, lambda _: None).run(cutoff=now)

    assert result == {"cleaned": 1, "failed": 0}
    assert marker.read_text() == "keep"
    assert not directory.exists()


@pytest.mark.asyncio
async def test_checkpoint_failure_records_generic_error_and_retries_later(tmp_path):
    now = datetime.now(timezone.utc)
    job = _job("checkpoint", "FAILED", now - timedelta(days=1))
    directory = tmp_path / "jobs" / "owner" / "checkpoint"
    directory.mkdir(parents=True)
    collection = Jobs([job])

    def fail_checkpoint(_thread_id):
        raise RuntimeError("mongodb://secret-host/password")

    result = await ArtifactCleanup(collection, tmp_path, fail_checkpoint).run(cutoff=now)

    assert result == {"cleaned": 0, "failed": 1}
    assert collection.updates[0][1]["$set"]["cleanup_error"] == "cleanup failed"
    assert "cleanup_at" not in collection.updates[0][1]["$set"]


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["../escape", "a/b", "..", ""])
async def test_cleanup_rejects_unsafe_job_components(tmp_path, component):
    now = datetime.now(timezone.utc)
    job = _job("job", "FAILED", now - timedelta(days=1))
    job["owner_hash"] = component
    collection = Jobs([job])

    result = await ArtifactCleanup(collection, tmp_path, lambda _: None).run(cutoff=now)

    assert result == {"cleaned": 0, "failed": 1}
    assert collection.updates[0][1]["$set"]["cleanup_error"] == "unsafe artifact path"


def _job(job_id, status, expires_at):
    return {
        "job_id": job_id,
        "owner_hash": "owner",
        "thread_id": f"owner:{job_id}-session",
        "status": status,
        "artifact_expires_at": expires_at,
    }
