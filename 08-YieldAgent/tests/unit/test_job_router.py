import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admission import GlobalLimitExceeded, UserLimitExceeded
from identity import get_platform_identity
from job_models import JobStatus
from job_repository import CreateJobResult, JobNotFound, SessionBusy
from job_router import router
from job_service import JobService, reconcile_admission
from settings import get_settings


class FakeRepository:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.idempotent: dict[tuple[str, str], dict] = {}
        self.insert_error: Exception | None = None
        self.race_job: dict | None = None
        self.operations: list[tuple] = []

    async def find_idempotent(self, owner_id, idempotency_key):
        self.operations.append(("find_idempotent", owner_id, idempotency_key))
        if idempotency_key is None:
            return None
        return self.idempotent.get((owner_id, idempotency_key))

    async def create_job(
        self, job_id, owner_id, owner_hash, session_id, query, idempotency_key
    ):
        self.operations.append(("create_job", job_id))
        if self.insert_error:
            raise self.insert_error
        if self.race_job is not None:
            return CreateJobResult(job=self.race_job, created=False)
        if any(
            job["owner_id"] == owner_id
            and job["session_id"] == session_id
            and job["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}
            for job in self.jobs.values()
        ):
            raise SessionBusy
        now = datetime.now(timezone.utc)
        job = {
            "job_id": job_id,
            "owner_id": owner_id,
            "owner_hash": owner_hash,
            "session_id": session_id,
            "query": query,
            "status": JobStatus.QUEUED.value,
            "run_sequence": 0,
            "created_at": now,
            "updated_at": now,
            "progress": "",
            "artifacts": [],
            "active_session_key": f"{owner_id}:{session_id}",
        }
        self.jobs[job_id] = job
        if idempotency_key is not None:
            self.idempotent[(owner_id, idempotency_key)] = job
        return CreateJobResult(job=job, created=True)

    async def mark_dispatch_requested(
        self, job_id, run_sequence, task_id, dispatch_requested_at
    ):
        self.operations.append(("mark_dispatch_requested", job_id, task_id))
        job = self.jobs[job_id]
        job.update(
            task_id=task_id,
            dispatch_requested_at=dispatch_requested_at,
            updated_at=dispatch_requested_at,
        )
        return job

    async def mark_dispatched(self, job_id, run_sequence, task_id, dispatched_at):
        self.operations.append(("mark_dispatched", job_id, task_id))
        job = self.jobs[job_id]
        job.update(dispatched_at=dispatched_at, updated_at=dispatched_at)
        return job

    async def transition(self, job_id, expected_status, target_status, updates=None):
        self.operations.append(("transition", job_id, str(target_status)))
        job = self.jobs[job_id]
        job.update(updates or {})
        job["status"] = JobStatus(target_status).value
        job["updated_at"] = datetime.now(timezone.utc)
        job.pop("active_session_key", None)
        return job

    async def get_owned(self, job_id, owner_id):
        job = self.jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            raise JobNotFound(job_id)
        return job


class FakeAdmission:
    def __init__(self):
        self.acquired: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []
        self.error: Exception | None = None

    async def acquire(self, owner_hash, job_id):
        if self.error:
            raise self.error
        self.acquired.append((owner_hash, job_id))

    async def release(self, owner_hash, job_id):
        self.released.append((owner_hash, job_id))


class FakeDispatcher:
    def __init__(self, repository):
        self.repository = repository
        self.calls: list[tuple[str, int]] = []
        self.error: Exception | None = None

    async def dispatch(self, job_id, run_sequence):
        job = self.repository.jobs[job_id]
        assert job["task_id"] == f"{job_id}:{run_sequence}"
        assert "dispatch_requested_at" in job
        assert "dispatched_at" not in job
        self.repository.operations.append(("dispatch", job_id, run_sequence))
        self.calls.append((job_id, run_sequence))
        if self.error:
            raise self.error
        return f"{job_id}:{run_sequence}"


@pytest.fixture(autouse=True)
def identity_settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def dependencies():
    repository = FakeRepository()
    admission = FakeAdmission()
    dispatcher = FakeDispatcher(repository)
    service = JobService(repository, admission, dispatcher)
    return repository, admission, dispatcher, service


@pytest.fixture
def client(dependencies):
    app = FastAPI()
    app.state.job_service = dependencies[-1]
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def identity_header():
    return {"X-Authenticated-User": "owner"}


def test_create_job_returns_202(client, identity_header):
    response = client.post(
        "/jobs",
        headers=identity_header,
        json={"query": "최근 4주 4SS 수율", "session_id": "s1"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert response.json()["events_url"].endswith("/events")


def test_busy_session_returns_stable_error(client, identity_header):
    body = {"query": "first", "session_id": "s1"}
    assert client.post("/jobs", headers=identity_header, json=body).status_code == 202
    response = client.post(
        "/jobs", headers=identity_header, json={**body, "query": "second"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SESSION_BUSY"


def test_other_owner_cannot_discover_job(client, identity_header):
    created = client.post(
        "/jobs", headers=identity_header, json={"query": "q", "session_id": "s1"}
    ).json()
    response = client.get(
        f"/jobs/{created['job_id']}",
        headers={"X-Authenticated-User": "other"},
    )
    assert response.status_code == 404


def test_idempotency_precheck_bypasses_full_admission(
    client, identity_header, dependencies
):
    _, admission, dispatcher, _ = dependencies
    body = {"query": "q", "session_id": "s1", "idempotency_key": "request-1"}
    first = client.post("/jobs", headers=identity_header, json=body)
    admission.error = UserLimitExceeded()

    repeated = client.post("/jobs", headers=identity_header, json=body)

    assert repeated.status_code == 202
    assert repeated.json()["job_id"] == first.json()["job_id"]
    assert len(dispatcher.calls) == 1


def test_idempotency_race_releases_candidate_without_dispatch(
    client, identity_header, dependencies
):
    repository, admission, dispatcher, _ = dependencies
    now = datetime.now(timezone.utc)
    repository.race_job = {
        "job_id": "original",
        "owner_id": "owner",
        "session_id": "s0",
        "status": "QUEUED",
        "created_at": now,
        "updated_at": now,
    }

    response = client.post(
        "/jobs",
        headers=identity_header,
        json={"query": "q", "session_id": "s1", "idempotency_key": "race"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "original"
    assert len(admission.acquired) == 1
    assert admission.released == admission.acquired
    assert dispatcher.calls == []


def test_insert_failure_releases_admission(client, identity_header, dependencies):
    repository, admission, _, _ = dependencies
    repository.insert_error = RuntimeError("mongo unavailable")

    with pytest.raises(RuntimeError, match="mongo unavailable"):
        client.post(
            "/jobs",
            headers=identity_header,
            json={"query": "q", "session_id": "s1"},
        )

    assert admission.released == admission.acquired


def test_dispatch_metadata_is_durable_before_send(
    client, identity_header, dependencies
):
    repository, _, dispatcher, _ = dependencies

    response = client.post(
        "/jobs", headers=identity_header, json={"query": "q", "session_id": "s1"}
    )

    job_id = response.json()["job_id"]
    assert dispatcher.calls == [(job_id, 0)]
    assert [operation[0] for operation in repository.operations[-3:]] == [
        "mark_dispatch_requested",
        "dispatch",
        "mark_dispatched",
    ]
    assert repository.jobs[job_id]["task_id"] == f"{job_id}:0"
    assert "dispatched_at" in repository.jobs[job_id]


def test_dispatch_failure_fails_job_and_releases_capacity(
    client, identity_header, dependencies
):
    repository, admission, dispatcher, _ = dependencies
    dispatcher.error = RuntimeError("broker unavailable")

    response = client.post(
        "/jobs", headers=identity_header, json={"query": "q", "session_id": "s1"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DISPATCH_UNAVAILABLE"
    failed = next(iter(repository.jobs.values()))
    assert failed["status"] == "FAILED"
    assert "active_session_key" not in failed
    assert admission.released == admission.acquired


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (UserLimitExceeded(), 429, "USER_JOB_LIMIT"),
        (GlobalLimitExceeded(), 503, "QUEUE_FULL"),
    ],
)
def test_admission_errors_are_stable(
    client, identity_header, dependencies, error, status, code
):
    dependencies[1].error = error
    response = client.post(
        "/jobs", headers=identity_header, json={"query": "q", "session_id": "s1"}
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_jobs_require_platform_identity(client):
    assert client.post("/jobs", json={"query": "q", "session_id": "s1"}).status_code == 401
    assert client.get("/jobs/unknown").status_code == 401


@pytest.mark.asyncio
async def test_startup_admission_reconciliation_runs_under_worker_lock():
    class Cursor:
        def __init__(self, jobs):
            self.iterator = iter(jobs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.iterator)
            except StopIteration:
                raise StopAsyncIteration

    class Jobs:
        def find(self, query, projection):
            assert set(query["status"]["$in"]) == {"QUEUED", "RUNNING", "WAITING_INPUT"}
            return Cursor(
                [
                    {"job_id": "j1", "owner_hash": "h1"},
                    {"job_id": "j2", "owner_hash": "h1"},
                    {"job_id": "j3", "owner_hash": "h2"},
                ]
            )

    class Lock:
        acquired = False

        async def acquire(self):
            self.acquired = True
            return True

        async def release(self):
            self.acquired = False

    class Redis:
        def __init__(self):
            self.created_lock = None

        def lock(self, name, timeout, blocking_timeout):
            assert name == "jobs:reconciler"
            assert timeout == 300
            self.created_lock = Lock()
            return self.created_lock

    class Admission:
        def __init__(self, redis):
            self.redis = redis
            self.counts = None

        async def reconcile(self, counts):
            assert self.redis.created_lock.acquired
            self.counts = counts

    repository = type("Repository", (), {"jobs": Jobs()})()
    redis = Redis()
    admission = Admission(redis)

    await reconcile_admission(repository, admission, redis)

    assert admission.counts == {"h1": {"j1", "j2"}, "h2": {"j3"}}
    assert redis.created_lock.acquired is False


def test_production_import_excludes_disabled_subsystems_and_blocks_legacy_routes():
    script = r'''
import asyncio
import sys

import httpx
import agent_server

for name in ("supervisor", "repl_agent.router", "wiki_router", "local_trace"):
    assert name not in sys.modules, name

paths = {route.path for route in agent_server.app.routes}
assert not any(path.startswith("/repl") for path in paths)
assert not any(path.startswith("/api/wiki") for path in paths)

async def verify():
    transport = httpx.ASGITransport(app=agent_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in (
            ("POST", "/chat/stream"),
            ("POST", "/session"),
            ("GET", "/session/s1/history"),
            ("GET", "/sessions"),
            ("GET", "/download/pptx/report.pptx"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 404, (path, response.status_code)

        assert (await client.post("/mining/tas", json={
            "lotcd": "4SS", "oper_det_desc": "x", "key_value": "y", "fail_name": "z"
        })).status_code == 401

        allowed = await client.options("/jobs", headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
        })
        assert allowed.headers["access-control-allow-origin"] == "https://frontend.example"
        development = await client.options("/jobs", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        })
        assert "access-control-allow-origin" not in development.headers

asyncio.run(verify())
print(agent_server.app.title)
'''
    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "MONGO_URI": "mongodb://mongo:27017",
        "REDIS_URL": "redis://redis:6379/0",
        "ARTIFACT_ROOT": "/tmp/yield-agent-artifacts",
        "OWNER_HASH_KEY": "test-key",
        "CORS_ORIGINS": '["https://frontend.example"]',
        "PLATFORM_USER_ID_HEADER": "X-Authenticated-User",
        "ENABLE_LEGACY_CHAT": "false",
        "ENABLE_REPL": "false",
        "ENABLE_WIKI": "false",
        "ENABLE_LOCAL_TRACE": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("Yield Agent Server")
