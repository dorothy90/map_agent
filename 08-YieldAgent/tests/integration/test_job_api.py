import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController
from job_models import JobStatus
from job_repository import JobRepository
from job_router import router
from job_service import JobService
from settings import get_settings


pytestmark = pytest.mark.no_server


@pytest.fixture(autouse=True)
def identity_settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "integration-owner-hash-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class RecordingDispatcher:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        self.calls.append((job_id, run_sequence))
        return f"{job_id}:{run_sequence}"


def _isolated_redis_url() -> str:
    parsed = urlsplit(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    )
    return urlunsplit(parsed._replace(path="/14", query="", fragment=""))


def test_job_api_foundation_flow_uses_real_mongo_and_redis():
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    mongo_db_name = f"yield_agent_job_api_{uuid.uuid4().hex}"
    redis_url = _isolated_redis_url()
    dispatcher = RecordingDispatcher()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mongo_client = AsyncIOMotorClient(mongo_uri)
        redis = Redis.from_url(redis_url, decode_responses=True)
        repository = JobRepository(mongo_client[mongo_db_name])
        admission = AdmissionController(redis, user_limit=2, global_limit=100)
        await mongo_client.admin.command("ping")
        await redis.ping()
        await redis.flushdb()
        await repository.ensure_indexes()
        app.state.job_repository = repository
        app.state.admission = admission
        app.state.job_service = JobService(repository, admission, dispatcher)
        try:
            yield
        finally:
            await redis.flushdb()
            await redis.aclose()
            await mongo_client.drop_database(mongo_db_name)
            mongo_client.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    headers = {"X-Authenticated-User": "integration-owner"}

    with TestClient(app) as client:
        idempotent_request = {
            "query": "최근 4주 4SS 수율",
            "session_id": "session-1",
            "idempotency_key": "request-1",
        }
        first = client.post("/jobs", headers=headers, json=idempotent_request)
        repeated = client.post("/jobs", headers=headers, json=idempotent_request)

        assert first.status_code == 202
        assert repeated.status_code == 202
        assert repeated.json()["job_id"] == first.json()["job_id"]
        assert dispatcher.calls == [(first.json()["job_id"], 0)]

        same_session = client.post(
            "/jobs",
            headers=headers,
            json={"query": "다른 요청", "session_id": "session-1"},
        )
        assert same_session.status_code == 409
        assert same_session.json()["detail"]["code"] == "SESSION_BUSY"

        second = client.post(
            "/jobs",
            headers=headers,
            json={"query": "두 번째 세션", "session_id": "session-2"},
        )
        assert second.status_code == 202

        at_limit = client.post(
            "/jobs",
            headers=headers,
            json={"query": "세 번째 세션", "session_id": "session-3"},
        )
        assert at_limit.status_code == 429
        assert at_limit.json()["detail"]["code"] == "USER_JOB_LIMIT"

        async def terminalize_and_release() -> None:
            job = await app.state.job_repository.get_owned(
                first.json()["job_id"], "integration-owner"
            )
            await app.state.job_repository.release_terminal(
                job["job_id"], JobStatus.QUEUED, JobStatus.CANCELLED
            )
            await app.state.admission.release(job["owner_hash"], job["job_id"])

        client.portal.call(terminalize_and_release)

        accepted_after_release = client.post(
            "/jobs",
            headers=headers,
            json={"query": "세 번째 세션", "session_id": "session-3"},
        )
        assert accepted_after_release.status_code == 202
        assert len(dispatcher.calls) == 3
