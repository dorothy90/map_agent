import os

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from job_events import JobEventStore


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
async def test_read_replays_after_last_event(redis_client):
    store = JobEventStore(redis_client, ttl_seconds=86_400, max_events=2_000)
    first = await store.publish("j1", {"type": "status", "message": "queued"})
    second = await store.publish("j1", {"type": "status", "message": "running"})
    events = await store.read("j1", after_id=first, block_ms=1)
    assert [event.id for event in events] == [second]
    assert events[0].data["message"] == "running"


@pytest.mark.asyncio
async def test_publish_sets_stream_ttl(redis_client):
    store = JobEventStore(redis_client, ttl_seconds=86_400, max_events=2_000)
    await store.publish("j1", {"type": "stream_end"})
    assert await redis_client.ttl("job:events:j1") > 0


@pytest.mark.asyncio
async def test_publish_rejects_payloads_larger_than_256_kib(redis_client):
    store = JobEventStore(redis_client, ttl_seconds=86_400, max_events=2_000)

    with pytest.raises(ValueError, match="256 KiB"):
        await store.publish("j1", {"type": "token", "content": "x" * (256 * 1024)})

    assert not await redis_client.exists("job:events:j1")
