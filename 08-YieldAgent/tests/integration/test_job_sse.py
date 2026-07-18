import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController
from job_events import JobEventStore
from job_models import JobStatus
from job_repository import JobRepository
from job_router import router
from job_service import JobService
from settings import get_settings


pytestmark = pytest.mark.no_server


class RecordingDispatcher:
    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        return f"{job_id}:{run_sequence}"


def _isolated_redis_url() -> str:
    parsed = urlsplit(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    )
    return urlunsplit(parsed._replace(path="/13", query="", fragment=""))


@pytest.fixture(autouse=True)
def identity_settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "sse-owner-hash-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def running_job_app():
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    mongo_db_name = f"yield_agent_job_sse_{uuid.uuid4().hex}"
    redis_url = _isolated_redis_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mongo_client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
        redis = Redis.from_url(redis_url, decode_responses=True)
        repository = JobRepository(mongo_client[mongo_db_name])
        admission = AdmissionController(redis, user_limit=2, global_limit=100)
        event_store = JobEventStore(redis, ttl_seconds=86_400, max_events=2_000)
        await mongo_client.admin.command("ping")
        await redis.ping()
        await redis.flushdb()
        await repository.ensure_indexes()
        app.state.job_service = JobService(
            repository, admission, RecordingDispatcher(), event_store
        )
        app.state.job_repository = repository
        app.state.job_event_store = event_store
        try:
            yield
        finally:
            await redis.flushdb()
            await redis.aclose()
            await mongo_client.drop_database(mongo_db_name)
            mongo_client.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        created = client.post(
            "/jobs",
            headers={"X-Authenticated-User": "owner"},
            json={"query": "최근 4주 4SS 수율", "session_id": "sse-session"},
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]

        async def mark_running():
            await app.state.job_repository.transition(
                job_id, JobStatus.QUEUED, JobStatus.RUNNING
            )

        client.portal.call(mark_running)
        yield app, client, job_id


def _sse_messages(body: str) -> list[dict]:
    messages = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        data = next(line[6:] for line in lines if line.startswith("data: "))
        messages.append(
            {
                "id": next(
                    (line[4:] for line in lines if line.startswith("id: ")), None
                ),
                "event": next(line[7:] for line in lines if line.startswith("event: ")),
                "data": json.loads(data),
            }
        )
    return messages


def test_sse_replays_only_events_strictly_after_retained_id(running_job_app):
    app, client, job_id = running_job_app

    async def seed_events():
        first = await app.state.job_event_store.publish(
            job_id, {"type": "status", "message": "queued"}
        )
        second = await app.state.job_event_store.publish(
            job_id, {"type": "status", "message": "running"}
        )
        third = await app.state.job_event_store.publish(
            job_id, {"type": "stream_end"}
        )
        return first, second, third

    first, second, third = client.portal.call(seed_events)
    response = client.get(
        f"/jobs/{job_id}/events",
        headers={
            "X-Authenticated-User": "owner",
            "Last-Event-ID": first,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    messages = _sse_messages(response.text)
    assert [message["event"] for message in messages] == [
        "job_snapshot",
        "status",
        "stream_end",
    ]
    assert messages[0]["id"] is None
    assert messages[0]["data"]["status"] == "RUNNING"
    assert [message["id"] for message in messages[1:]] == [second, third]
    assert "queued" not in response.text


def test_sse_hides_job_from_other_owner(running_job_app):
    _, client, job_id = running_job_app

    response = client.get(
        f"/jobs/{job_id}/events",
        headers={"X-Authenticated-User": "other"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.parametrize("last_event_id", [None, "trimmed-or-unknown-id"])
def test_missing_last_event_id_starts_at_current_tail(
    running_job_app, last_event_id
):
    app, client, job_id = running_job_app

    async def exercise_stream():
        await app.state.job_event_store.publish(
            job_id, {"type": "status", "message": "historical"}
        )
        stream = app.state.job_service.stream_events(
            job_id,
            SimpleNamespace(owner_id="owner"),
            last_event_id,
            never_disconnected,
        )
        snapshot = await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.05)
        assert not pending.done()
        new_id = await app.state.job_event_store.publish(
            job_id, {"type": "stream_end"}
        )
        event = await pending
        await stream.aclose()
        return snapshot, event, new_id

    async def never_disconnected():
        return False

    snapshot, event, new_id = client.portal.call(exercise_stream)
    assert snapshot.data["type"] == "job_snapshot"
    assert event.id == new_id
    assert event.data == {"type": "stream_end"}


def test_disconnect_does_not_change_durable_job_status(running_job_app):
    app, client, job_id = running_job_app

    async def disconnect_after_snapshot():
        disconnected = False

        async def is_disconnected():
            return disconnected

        stream = app.state.job_service.stream_events(
            job_id,
            SimpleNamespace(owner_id="owner"),
            None,
            is_disconnected,
        )
        snapshot = await anext(stream)
        disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        return snapshot, await app.state.job_repository.get_owned(job_id, "owner")

    snapshot, stored = client.portal.call(disconnect_after_snapshot)
    assert snapshot.data["status"] == "RUNNING"
    assert stored["status"] == JobStatus.RUNNING.value


def test_idle_stream_uses_fifteen_second_heartbeat(running_job_app):
    app, client, job_id = running_job_app

    async def exercise_heartbeat():
        store = app.state.job_event_store
        original_read = store.read
        blocks = []

        async def read(job_id, after_id, block_ms):
            blocks.append(block_ms)
            if block_ms == 1:
                return await original_read(job_id, after_id, block_ms)
            return []

        checks = 0

        async def is_disconnected():
            nonlocal checks
            checks += 1
            return checks >= 4

        store.read = read
        try:
            stream = app.state.job_service.stream_events(
                job_id,
                SimpleNamespace(owner_id="owner"),
                None,
                is_disconnected,
            )
            await anext(stream)
            heartbeat = await anext(stream)
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            return heartbeat, blocks
        finally:
            store.read = original_read

    heartbeat, blocks = client.portal.call(exercise_heartbeat)
    assert heartbeat is None
    assert blocks == [1, 15_000]


def test_terminal_snapshot_ends_stream_without_waiting(running_job_app):
    app, client, job_id = running_job_app

    async def mark_succeeded():
        await app.state.job_repository.transition(
            job_id, JobStatus.RUNNING, JobStatus.SUCCEEDED
        )

    client.portal.call(mark_succeeded)
    response = client.get(
        f"/jobs/{job_id}/events",
        headers={"X-Authenticated-User": "owner"},
    )

    assert response.status_code == 200
    messages = _sse_messages(response.text)
    assert len(messages) == 1
    assert messages[0]["event"] == "job_snapshot"
    assert messages[0]["data"]["status"] == "SUCCEEDED"
