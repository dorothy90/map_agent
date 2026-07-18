import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController, GLOBAL_ACTIVE_KEY
from job_models import JobStatus
from job_repository import JobRepository
from job_router import router
from job_service import JobService
from settings import Settings, get_settings
from worker_runtime import WorkerRuntime


pytestmark = pytest.mark.no_server


class RecordingDispatcher:
    def __init__(self):
        self.calls = []
        self.error = None

    async def dispatch(self, job_id, run_sequence):
        self.calls.append((job_id, run_sequence))
        if self.error is not None:
            raise self.error
        return f"{job_id}:{run_sequence}"


def _redis_url():
    parsed = urlsplit(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    )
    return urlunsplit(parsed._replace(path="/11", query="", fragment=""))


@pytest.fixture(autouse=True)
def identity_settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "job-control-owner-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def control_app():
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    database_name = f"yield_agent_job_control_{uuid.uuid4().hex}"
    redis_url = _redis_url()

    @asynccontextmanager
    async def lifespan(app):
        mongo = AsyncIOMotorClient(mongo_uri, tz_aware=True)
        redis = Redis.from_url(redis_url, decode_responses=True)
        repository = JobRepository(mongo[database_name])
        admission = AdmissionController(redis, user_limit=2, global_limit=100)
        dispatcher = RecordingDispatcher()
        await mongo.admin.command("ping")
        await redis.ping()
        await redis.flushdb()
        await repository.ensure_indexes()
        app.state.redis = redis
        app.state.job_repository = repository
        app.state.admission = admission
        app.state.job_dispatcher = dispatcher
        app.state.job_service = JobService(repository, admission, dispatcher)
        app.state.worker_settings = Settings(
            environment="test",
            mongo_uri=mongo_uri,
            mongo_db=database_name,
            redis_url=redis_url,
        )
        try:
            yield
        finally:
            await redis.flushdb()
            await redis.aclose()
            await mongo.drop_database(database_name)
            mongo.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        yield app, client


def _create_waiting(app, client, *, session_id="s1", interrupt_type="missing_param"):
    created = client.post(
        "/jobs",
        headers={"X-Authenticated-User": "owner"},
        json={"query": "query", "session_id": session_id},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    async def wait_for_input():
        repository = app.state.job_repository
        await repository.transition(job_id, JobStatus.QUEUED, JobStatus.RUNNING)
        return await repository.transition(
            job_id,
            JobStatus.RUNNING,
            JobStatus.WAITING_INPUT,
            {"latest_interrupt": {"type": interrupt_type}},
        )

    return client.portal.call(wait_for_input)


@pytest.mark.parametrize(
    "interrupt_type,resume_value",
    [
        ("missing_param", {"lotcd": "4SS"}),
        ("plan_review", "승인"),
    ],
)
def test_resume_preserves_contract_thread_and_dispatches_once(
    control_app, interrupt_type, resume_value
):
    app, client = control_app
    waiting = _create_waiting(app, client, interrupt_type=interrupt_type)

    response = client.post(
        f"/jobs/{waiting['job_id']}/resume",
        headers={"X-Authenticated-User": "owner"},
        json={"value": resume_value},
    )

    assert response.status_code == 202
    stored = client.portal.call(app.state.job_repository.get, waiting["job_id"])
    assert stored["status"] == "QUEUED"
    assert stored["run_sequence"] == 1
    assert stored["thread_id"] == waiting["thread_id"]
    assert stored["resume_value"] == resume_value
    assert app.state.job_dispatcher.calls[-1] == (waiting["job_id"], 1)
    assert app.state.job_dispatcher.calls.count((waiting["job_id"], 1)) == 1
    assert "lease_expires_at" not in stored
    assert "worker_id" not in stored


def test_resume_validation_state_and_ownership(control_app):
    app, client = control_app
    waiting = _create_waiting(app, client)
    headers = {"X-Authenticated-User": "owner"}

    assert client.post(
        f"/jobs/{waiting['job_id']}/resume", headers=headers, json={"value": {}}
    ).status_code == 422

    other = client.post(
        f"/jobs/{waiting['job_id']}/resume",
        headers={"X-Authenticated-User": "other"},
        json={"value": {"lotcd": "4SS"}},
    )
    assert other.status_code == 404

    async def make_running():
        return await app.state.job_repository.transition(
            waiting["job_id"], JobStatus.WAITING_INPUT, JobStatus.QUEUED
        )

    client.portal.call(make_running)
    running = client.post(
        f"/jobs/{waiting['job_id']}/resume",
        headers=headers,
        json={"value": {"lotcd": "4SS"}},
    )
    assert running.status_code == 409
    assert running.json()["detail"]["code"] == "JOB_NOT_WAITING"


def test_resume_dispatch_failure_rolls_back_only_matching_sequence(control_app):
    app, client = control_app
    waiting = _create_waiting(app, client)
    app.state.job_dispatcher.error = RuntimeError("broker unavailable")

    response = client.post(
        f"/jobs/{waiting['job_id']}/resume",
        headers={"X-Authenticated-User": "owner"},
        json={"value": {"lotcd": "4SS"}},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DISPATCH_UNAVAILABLE"
    stored = client.portal.call(app.state.job_repository.get, waiting["job_id"])
    assert stored["status"] == "WAITING_INPUT"
    assert stored["run_sequence"] == 1
    assert "resume_value" not in stored


def test_queued_cancel_is_terminal_releases_session_and_is_idempotent(control_app):
    app, client = control_app
    created = client.post(
        "/jobs",
        headers={"X-Authenticated-User": "owner"},
        json={"query": "query", "session_id": "cancel-queued"},
    ).json()
    headers = {"X-Authenticated-User": "owner"}

    first = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
    second = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "CANCELLED"
    stored = client.portal.call(app.state.job_repository.get, created["job_id"])
    assert stored["cancel_requested"] is True
    assert "active_session_key" not in stored
    assert client.portal.call(app.state.admission.global_count) == 0
    ttl = client.portal.call(app.state.redis.ttl, f"job:cancel:{created['job_id']}")
    assert 0 < ttl <= 1800


def test_running_cancel_sets_durable_flag_then_mirror_and_hides_owner(control_app):
    app, client = control_app
    created = client.post(
        "/jobs",
        headers={"X-Authenticated-User": "owner"},
        json={"query": "query", "session_id": "cancel-running"},
    ).json()

    async def mark_running():
        return await app.state.job_repository.claim(
            created["job_id"], f"{created['job_id']}:0", "worker", 60, run_sequence=0
        )

    client.portal.call(mark_running)
    hidden = client.post(
        f"/jobs/{created['job_id']}/cancel",
        headers={"X-Authenticated-User": "other"},
    )
    assert hidden.status_code == 404

    response = client.post(
        f"/jobs/{created['job_id']}/cancel",
        headers={"X-Authenticated-User": "owner"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    stored = client.portal.call(app.state.job_repository.get, created["job_id"])
    assert stored["cancel_requested"] is True
    assert stored["status"] == "RUNNING"
    assert client.portal.call(app.state.redis.exists, f"job:cancel:{created['job_id']}")
    assert client.portal.call(app.state.redis.scard, GLOBAL_ACTIVE_KEY) == 1


@pytest.mark.parametrize("cancel_source", ["redis", "mongo"])
def test_worker_callback_observes_fast_and_durable_cancellation(
    control_app, cancel_source
):
    app, client = control_app
    created = client.post(
        "/jobs",
        headers={"X-Authenticated-User": "owner"},
        json={"query": "query", "session_id": f"worker-{cancel_source}"},
    ).json()

    class CancellingRunner:
        async def __call__(self, graph, request, emit, cancelled):
            if cancel_source == "mongo":
                mongo = AsyncIOMotorClient(
                    app.state.worker_settings.mongo_uri, tz_aware=True
                )
                try:
                    await mongo[app.state.worker_settings.mongo_db].jobs.update_one(
                        {"job_id": request.job_id},
                        {"$set": {"cancel_requested": True}},
                    )
                finally:
                    mongo.close()
            assert await cancelled() is True
            await emit({"type": "status", "message": "must not publish"})
            raise AssertionError("cancel-aware emit must stop the runner")

    if cancel_source == "redis":
        async def set_cancel_mirror():
            await app.state.redis.set(
                f"job:cancel:{created['job_id']}", "1", ex=1800
            )

        client.portal.call(set_cancel_mirror)
    runtime = WorkerRuntime(
        settings=app.state.worker_settings,
        graph=object(),
        graph_runner=CancellingRunner(),
    )

    result = asyncio.run(
        runtime.execute_job(
            created["job_id"], 0, f"{created['job_id']}:0", "worker"
        )
    )

    assert result.status == "CANCELLED"
    stored = client.portal.call(app.state.job_repository.get, created["job_id"])
    assert stored["status"] == "CANCELLED"
    assert client.portal.call(app.state.admission.global_count) == 0
