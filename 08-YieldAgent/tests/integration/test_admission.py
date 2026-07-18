import asyncio
import os

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from admission import AdmissionController, GlobalLimitExceeded, UserLimitExceeded


@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0"),
        decode_responses=True,
    )
    await client.ping()
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.mark.asyncio
async def test_user_limit_is_atomic(redis_client):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)

    results = await asyncio.gather(
        *(admission.acquire("u1", f"j{index}") for index in range(3)),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 2
    assert sum(isinstance(result, UserLimitExceeded) for result in results) == 1
    assert await admission.global_count() == 2


@pytest.mark.asyncio
async def test_global_limit_is_atomic(redis_client):
    admission = AdmissionController(redis_client, user_limit=100, global_limit=2)

    results = await asyncio.gather(
        *(admission.acquire(f"u{index}", f"j{index}") for index in range(3)),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 2
    assert sum(isinstance(result, GlobalLimitExceeded) for result in results) == 1
    assert await admission.global_count() == 2


@pytest.mark.asyncio
async def test_repeated_acquire_is_idempotent_at_limits(redis_client):
    admission = AdmissionController(redis_client, user_limit=1, global_limit=1)

    await admission.acquire("u1", "j1")
    await admission.acquire("u1", "j1")

    assert await admission.global_count() == 1


@pytest.mark.asyncio
async def test_release_is_idempotent(redis_client):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)
    await admission.acquire("u1", "j1")

    await admission.release("u1", "j1")
    await admission.release("u1", "j1")

    assert await admission.global_count() == 0


@pytest.mark.asyncio
async def test_reconcile_rebuilds_active_job_sets(redis_client):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)
    await admission.acquire("stale-user", "stale-job")

    await admission.reconcile({"u1": {"j1", "j2"}, "u2": {"j3"}})

    assert await admission.global_count() == 3
    assert await redis_client.smembers("jobs:active:user:u1") == {"j1", "j2"}
    assert await redis_client.smembers("jobs:active:user:u2") == {"j3"}
    assert not await redis_client.exists("jobs:active:user:stale-user")


@pytest.mark.asyncio
async def test_reconcile_has_no_scan_then_exec_race(redis_client, monkeypatch):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)
    await admission.acquire("stale-user", "stale-job")
    original_scan_iter = redis_client.scan_iter

    async def scan_then_concurrent_acquire(*args, **kwargs):
        async for key in original_scan_iter(*args, **kwargs):
            yield key
        await admission.acquire("racing-user", "racing-job")

    monkeypatch.setattr(redis_client, "scan_iter", scan_then_concurrent_acquire)

    await admission.reconcile({"u1": {"j1"}})

    assert await redis_client.smembers("jobs:active:global") == {"j1"}
    assert {
        key async for key in original_scan_iter(match="jobs:active:user:*")
    } == {"jobs:active:user:u1"}
