import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from job_models import JobStatus
from job_repository import (
    CreateJobResult,
    JobNotFound,
    JobRepository,
    SessionBusy,
    TransitionConflict,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.no_server]


async def test_only_one_active_job_per_owner_session(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    first = (
        await repo.create_job(
            "job-1", "owner-1", "hash-1", "session-1", "query", None
        )
    ).job
    with pytest.raises(SessionBusy):
        await repo.create_job(
            "job-2", "owner-1", "hash-1", "session-1", "other", None
        )
    assert first["status"] == "QUEUED"


async def test_idempotency_returns_current_original_job(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    first = (
        await repo.create_job(
            "job-1", "owner-1", "hash-1", "s1", "query", "request-1"
        )
    ).job
    result = await repo.create_job(
        "job-2", "owner-1", "hash-1", "s1", "query", "request-1"
    )
    again = result.job
    assert again["job_id"] == first["job_id"]
    assert result.created is False


async def test_different_owners_namespace_same_session_id(mongo_db):
    repo = JobRepository(mongo_db)
    first = (
        await repo.create_job("job-1", "owner-1", "hash-1", "s1", "query", None)
    ).job
    second = (
        await repo.create_job("job-2", "owner-2", "hash-2", "s1", "query", None)
    ).job
    assert first["thread_id"] == "hash-1:s1"
    assert second["thread_id"] == "hash-2:s1"


async def test_create_stores_required_fields_without_active_expiry(mongo_db):
    repo = JobRepository(mongo_db)
    result = await repo.create_job(
        "job-1", "owner-1", "hash-1", "s1", "query", None
    )

    assert isinstance(result, CreateJobResult)
    assert result.created is True
    assert result.job["run_sequence"] == 0
    assert result.job["attempt"] == 0
    assert result.job["cancel_requested"] is False
    assert "idempotency_key" not in result.job
    assert "artifact_expires_at" not in result.job
    assert "expires_at" not in result.job


async def test_active_key_canonical_hash_avoids_delimiter_collisions(mongo_db):
    repo = JobRepository(mongo_db)
    first = (
        await repo.create_job("job-1", "a:b", "h1", "c", "query", None)
    ).job
    second = (
        await repo.create_job("job-2", "a", "h2", "b:c", "query", None)
    ).job
    assert first["active_session_key"] != second["active_session_key"]


async def test_idempotency_is_owner_scoped(mongo_db):
    repo = JobRepository(mongo_db)
    first = (
        await repo.create_job("job-1", "owner-1", "h1", "s1", "query", "same")
    ).job
    second = (
        await repo.create_job("job-2", "owner-2", "h2", "s1", "query", "same")
    ).job
    assert first["job_id"] != second["job_id"]


async def test_missing_idempotency_key_is_not_indexed(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    first = (
        await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    ).job
    second = (
        await repo.create_job("job-2", "owner-1", "h1", "s2", "query", None)
    ).job
    assert first["job_id"] != second["job_id"]


async def test_concurrent_idempotency_race_returns_one_original_job(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    results = await asyncio.gather(
        repo.create_job("job-1", "owner-1", "h1", "s1", "query", "request-1"),
        repo.create_job("job-2", "owner-1", "h1", "s1", "query", "request-1"),
    )
    assert len({result.job["job_id"] for result in results}) == 1
    assert sorted(result.created for result in results) == [False, True]


async def test_get_owned_rejects_another_owner(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    with pytest.raises(JobNotFound):
        await repo.get_owned("job-1", "owner-2")


async def test_terminal_transition_releases_session_and_sets_expiries(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    await repo.transition("job-1", JobStatus.QUEUED, JobStatus.CANCELLED)

    stored = await repo.jobs.find_one({"job_id": "job-1"})
    assert stored["status"] == "CANCELLED"
    assert "active_session_key" not in stored
    grace = stored["expires_at"] - stored["artifact_expires_at"]
    assert grace.total_seconds() == pytest.approx(24 * 60 * 60, abs=1)


async def test_transition_requires_expected_current_status(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    with pytest.raises(TransitionConflict):
        await repo.transition("job-1", JobStatus.RUNNING, JobStatus.FAILED)


async def test_indexes_include_partial_idempotency_and_ttl(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    indexes = await repo.jobs.index_information()
    idempotency = next(
        value
        for value in indexes.values()
        if value["key"] == [("owner_id", 1), ("idempotency_key", 1)]
    )
    ttl = next(
        value for value in indexes.values() if value["key"] == [("expires_at", 1)]
    )
    assert idempotency["unique"] is True
    assert idempotency["partialFilterExpression"] == {
        "idempotency_key": {"$type": "string"}
    }
    assert ttl["expireAfterSeconds"] == 0


async def test_only_one_worker_claims_a_queued_job(mongo_db):
    repo = JobRepository(mongo_db)
    job = (
        await repo.create_job("job-1", "owner-1", "hash-1", "s1", "query", None)
    ).job
    first = await repo.claim(job["job_id"], "task-1", "worker-1", lease_seconds=60)
    second = await repo.claim(job["job_id"], "task-2", "worker-2", lease_seconds=60)
    assert first is not None
    assert second is None


async def test_renew_lease_requires_current_worker_and_task(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    claimed = await repo.claim("job-1", "task-1", "worker-1", lease_seconds=1)
    renewed = await repo.renew_lease(
        "job-1", "task-1", "worker-1", lease_seconds=60
    )
    rejected = await repo.renew_lease(
        "job-1", "task-other", "worker-1", lease_seconds=60
    )
    assert renewed["lease_expires_at"] > claimed["lease_expires_at"]
    assert rejected is None


async def test_reclaim_requires_expired_running_lease(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    await repo.claim("job-1", "task-1", "worker-1", lease_seconds=60)

    too_early = await repo.reclaim_expired(
        "job-1", "task-2", "worker-2", lease_seconds=60
    )
    await repo.jobs.update_one(
        {"job_id": "job-1"},
        {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    reclaimed = await repo.reclaim_expired(
        "job-1", "task-2", "worker-2", lease_seconds=60
    )
    assert too_early is None
    assert reclaimed["task_id"] == "task-2"
    assert reclaimed["worker_id"] == "worker-2"


async def test_waiting_input_remains_active_without_expiry(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    await repo.claim("job-1", "task-1", "worker-1", lease_seconds=60)
    waiting = await repo.transition(
        "job-1", JobStatus.RUNNING, JobStatus.WAITING_INPUT
    )
    assert "active_session_key" in waiting
    assert "artifact_expires_at" not in waiting
    assert "expires_at" not in waiting


async def test_mark_dispatched_accepts_same_sequence_already_running(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    requested_at = datetime.now(timezone.utc)
    await repo.mark_dispatch_requested("job-1", 0, "job-1:0", requested_at)
    await repo.claim("job-1", "job-1:0", "worker-1", lease_seconds=60)

    dispatched_at = datetime.now(timezone.utc)
    stored = await repo.mark_dispatched(
        "job-1", 0, "job-1:0", dispatched_at
    )

    assert stored["status"] == "RUNNING"
    assert stored["run_sequence"] == 0
    assert stored["dispatched_at"] == dispatched_at.replace(
        microsecond=dispatched_at.microsecond // 1000 * 1000
    )


async def test_mark_dispatched_accepts_same_sequence_terminal_without_task_id(
    mongo_db,
):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    requested_at = datetime.now(timezone.utc)
    await repo.mark_dispatch_requested("job-1", 0, "job-1:0", requested_at)
    await repo.transition("job-1", JobStatus.QUEUED, JobStatus.CANCELLED)

    dispatched_at = datetime.now(timezone.utc)
    stored = await repo.mark_dispatched(
        "job-1", 0, "job-1:0", dispatched_at
    )

    assert stored["status"] == "CANCELLED"
    assert "task_id" not in stored
    assert stored["dispatched_at"] == dispatched_at.replace(
        microsecond=dispatched_at.microsecond // 1000 * 1000
    )


async def test_mark_dispatched_rejects_stale_run_sequence(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.create_job("job-1", "owner-1", "h1", "s1", "query", None)
    await repo.jobs.update_one(
        {"job_id": "job-1"}, {"$set": {"run_sequence": 1}}
    )

    with pytest.raises(TransitionConflict):
        await repo.mark_dispatched(
            "job-1", 0, "job-1:0", datetime.now(timezone.utc)
        )

    stored = await repo.get("job-1")
    assert "dispatched_at" not in stored
