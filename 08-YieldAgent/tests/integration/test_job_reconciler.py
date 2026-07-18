from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController, GLOBAL_ACTIVE_KEY
from celery_app import celery_app
from job_events import JobEventStore
from job_reconciler import JobReconciler
from job_repository import JobRepository


pytestmark = pytest.mark.no_server


def test_celery_beat_runs_reconciler_every_minute():
    schedule = celery_app.conf.beat_schedule["reconcile-jobs-every-minute"]
    assert schedule["task"] == "yield_agent.reconcile_jobs"
    assert schedule["schedule"] == 60.0


class RecordingDispatcher:
    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    async def dispatch(self, job_id, run_sequence):
        self.calls.append((job_id, run_sequence))
        if self.hook is not None:
            await self.hook(job_id, run_sequence)
        return f"{job_id}:{run_sequence}"


@pytest_asyncio.fixture
async def infrastructure():
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    redis_url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    database = client[f"yield_agent_reconciler_{uuid.uuid4().hex}"]
    redis = Redis.from_url(redis_url, decode_responses=True)
    await client.admin.command("ping")
    await redis.ping()
    await redis.flushdb()
    try:
        yield database, redis
    finally:
        await redis.flushdb()
        await client.drop_database(database.name)
        client.close()
        await redis.aclose()


async def _insert_job(database, *, status, age_seconds=0, lease_expired=False):
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "owner_id": "owner-1",
        "owner_hash": "owner-hash",
        "session_id": f"session-{job_id}",
        "thread_id": f"owner-hash:session-{job_id}",
        "active_session_key": f"active-{job_id}",
        "query": "query",
        "status": status,
        "run_sequence": 7,
        "attempt": 1,
        "cancel_requested": False,
        "created_at": now - timedelta(seconds=age_seconds),
        "updated_at": now - timedelta(seconds=age_seconds),
    }
    if status == "RUNNING":
        job.update(
            task_id=f"{job_id}:7",
            worker_id="dead-worker",
            lease_expires_at=now
            + timedelta(seconds=-1 if lease_expired else 60),
        )
    if status == "QUEUED" and age_seconds:
        job["dispatched_at"] = now - timedelta(seconds=age_seconds)
    await database.jobs.insert_one(job)
    return job


def _reconciler(database, redis, dispatcher):
    return JobReconciler(
        JobRepository(database),
        AdmissionController(redis, user_limit=2, global_limit=100),
        dispatcher,
        JobEventStore(redis, ttl_seconds=86_400, max_events=2_000),
        redis,
    )


@pytest.mark.asyncio
async def test_expired_running_and_stale_queued_reenqueue_current_sequence(
    infrastructure,
):
    database, redis = infrastructure
    expired = await _insert_job(database, status="RUNNING", lease_expired=True)
    stale = await _insert_job(database, status="QUEUED", age_seconds=61)
    fresh = await _insert_job(database, status="QUEUED", age_seconds=30)
    dispatcher = RecordingDispatcher()

    result = await _reconciler(database, redis, dispatcher).run()

    assert sorted(dispatcher.calls) == sorted(
        [(expired["job_id"], 7), (stale["job_id"], 7)]
    )
    assert fresh["job_id"] not in {job_id for job_id, _ in dispatcher.calls}
    recovered = await database.jobs.find_one({"job_id": expired["job_id"]})
    assert recovered["status"] == "QUEUED"
    assert "lease_expires_at" not in recovered
    assert result.reenqueued == 2


@pytest.mark.asyncio
async def test_dispatch_reservation_tolerates_fast_worker_claim(infrastructure):
    database, redis = infrastructure
    stale = await _insert_job(database, status="QUEUED", age_seconds=61)
    repository = JobRepository(database)

    async def fast_claim(job_id, run_sequence):
        claimed = await repository.claim(
            job_id, f"{job_id}:{run_sequence}", "fast-worker", 60, run_sequence
        )
        assert claimed is not None

    dispatcher = RecordingDispatcher(fast_claim)
    await _reconciler(database, redis, dispatcher).run()

    stored = await database.jobs.find_one({"job_id": stale["job_id"]})
    assert stored["status"] == "RUNNING"
    assert "dispatched_at" in stored


@pytest.mark.asyncio
async def test_waiting_timeout_admission_rebuild_and_cancel_key_cleanup(
    infrastructure,
):
    database, redis = infrastructure
    waiting = await _insert_job(
        database, status="WAITING_INPUT", age_seconds=24 * 60 * 60 + 1
    )
    active = await _insert_job(database, status="QUEUED", age_seconds=30)
    terminal = await _insert_job(database, status="SUCCEEDED")
    await redis.sadd(GLOBAL_ACTIVE_KEY, "stale")
    await redis.set(f"job:cancel:{terminal['job_id']}", "1", ex=1800)
    await redis.set(f"job:cancel:{active['job_id']}", "1", ex=1800)

    result = await _reconciler(database, redis, RecordingDispatcher()).run()

    cancelled = await database.jobs.find_one({"job_id": waiting["job_id"]})
    assert cancelled["status"] == "CANCELLED"
    assert "active_session_key" not in cancelled
    assert await redis.smembers(GLOBAL_ACTIVE_KEY) == {active["job_id"]}
    assert not await redis.exists(f"job:cancel:{terminal['job_id']}")
    assert await redis.exists(f"job:cancel:{active['job_id']}")
    assert result.cancelled == 1
    assert result.cancel_keys_removed == 1


@pytest.mark.asyncio
async def test_lock_release_compares_owner_token(infrastructure):
    database, redis = infrastructure
    reconciler = _reconciler(database, redis, RecordingDispatcher())
    assert await reconciler.acquire_lock("owner-a") is True
    await redis.set("jobs:reconciler", "owner-b", ex=300)

    assert await reconciler.release_lock("owner-a") is False
    assert await redis.get("jobs:reconciler") == "owner-b"
