# Production Job Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add validated production configuration, trusted platform identity, durable job records, admission control, and `/jobs` creation/status APIs without executing analysis inside FastAPI.

**Architecture:** MongoDB owns job state and same-session exclusion. Redis provides atomic capacity counters. FastAPI reads only the gateway-injected identity header and delegates enqueueing through a narrow dispatcher interface completed in the next plan.

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, Motor/PyMongo, Redis, pytest, pytest-asyncio, real MongoDB/Redis integration services

## Global Constraints

- Work only in the isolated `codex/from-28cede8` worktree; preserve the user's original workspace.
- Trusted identity comes from the platform header configured by `PLATFORM_USER_ID_HEADER`; request JSON never supplies ownership.
- Same `(owner_id, session_id)` non-terminal work returns `409 SESSION_BUSY`.
- Per-user non-terminal limit is 2; global limit is 100.
- Durable states are `QUEUED`, `RUNNING`, `WAITING_INPUT`, `SUCCEEDED`, `FAILED`, `CANCELLED`.
- `/repl`, `/wiki`, debug traces, and legacy direct execution are disabled in production.
- Do not change LangGraph prompts, routing, HITL semantics, or add natural-language hardcoding.
- Namespace every LangGraph checkpoint thread as `{owner_hash}:{session_id}`; never use client `session_id` alone.
- Use TDD and commit after each task. Run commands from repository root unless a step says otherwise.
- Execute this plan before `2026-07-18-production-worker-streaming.md` and `2026-07-18-production-artifacts-frontend-operations.md`.

---

### Task 1: Production Settings and Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Create: `08-YieldAgent/settings.py`
- Create: `08-YieldAgent/tests/unit/test_settings.py`

**Interfaces:**
- Produces: `Settings`, `get_settings() -> Settings`, `reset_settings_cache() -> None`
- Consumes: environment variables only

- [ ] **Step 1: Write failing settings tests**

```python
# 08-YieldAgent/tests/unit/test_settings.py
import pytest
from pydantic import ValidationError
from settings import Settings


def test_production_requires_shared_services(tmp_path):
    with pytest.raises(ValidationError):
        Settings(environment="production", artifact_root=tmp_path)


def test_limits_and_header_are_loaded(tmp_path):
    settings = Settings(
        environment="test",
        mongo_uri="mongodb://mongo:27017",
        redis_url="redis://redis:6379/0",
        artifact_root=tmp_path,
        platform_user_id_header="X-Authenticated-User",
        user_job_limit=2,
        global_job_limit=100,
    )
    assert settings.platform_user_id_header == "X-Authenticated-User"
    assert settings.user_job_limit == 2
    assert settings.global_job_limit == 100
```

- [ ] **Step 2: Verify tests fail**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'settings'`.

- [ ] **Step 3: Add dependencies and settings implementation**

Add runtime dependencies `celery>=5.4,<6`, `redis>=5.2,<6`, `pydantic-settings>=2.7,<3`, and `prometheus-client>=0.21,<1` to `project.dependencies` and `requirements.txt`. Add `pytest>=8,<9` and `pytest-asyncio>=0.24,<1` to the `dev` dependency group in `pyproject.toml`. Add explicit environment keys to `.env.example` without credentials.

```python
# 08-YieldAgent/settings.py
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "yield_agent"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path("generated")
    platform_user_id_header: str = "user_id"
    owner_hash_key: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    user_job_limit: int = Field(default=2, ge=1)
    global_job_limit: int = Field(default=100, ge=1)
    enable_legacy_chat: bool = True
    enable_repl: bool = False
    enable_wiki: bool = False
    enable_local_trace: bool = False

    @model_validator(mode="after")
    def validate_production(self):
        if self.environment == "production":
            if "localhost" in self.mongo_uri or "localhost" in self.redis_url:
                raise ValueError("production requires non-local MongoDB and Redis")
            if not self.artifact_root.is_absolute():
                raise ValueError("production ARTIFACT_ROOT must be absolute")
            if not self.owner_hash_key:
                raise ValueError("production OWNER_HASH_KEY is required")
            if self.enable_legacy_chat or self.enable_repl or self.enable_wiki or self.enable_local_trace:
                raise ValueError("unsafe production routes or traces are enabled")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
```

- [ ] **Step 4: Lock and verify dependencies**

Run: `uv lock && uv sync`
Expected: exit 0 and imports `celery`, `redis`, `pydantic_settings` succeed.

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_settings.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt .env.example uv.lock \
  08-YieldAgent/settings.py 08-YieldAgent/tests/unit/test_settings.py
git commit -m "feat(config): validate production settings"
```

### Task 2: Job Domain Model and Transition Guard

**Files:**
- Create: `08-YieldAgent/job_models.py`
- Create: `08-YieldAgent/tests/unit/test_job_models.py`

**Interfaces:**
- Produces: `JobStatus`, `JobCreate`, `JobSnapshot`, `JobCreated`, `ResumeRequest`, `JobError`, `assert_transition(current, target) -> None`, `is_terminal(status) -> bool`
- Consumes: `ArtifactType` from `models.py` only for response serialization

- [ ] **Step 1: Write transition and request tests**

```python
from pydantic import ValidationError
import pytest
from job_models import JobCreate, JobStatus, assert_transition, is_terminal


def test_waiting_input_can_resume_to_queue():
    assert_transition(JobStatus.WAITING_INPUT, JobStatus.QUEUED)


def test_terminal_state_cannot_transition():
    with pytest.raises(ValueError, match="terminal"):
        assert_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)


def test_create_rejects_blank_query():
    with pytest.raises(ValidationError):
        JobCreate(query="   ", session_id="session-1")


def test_terminal_statuses():
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.WAITING_INPUT)
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_job_models.py -q`
Expected: FAIL because `job_models` does not exist.

- [ ] **Step 3: Implement exact state contract**

```python
# 08-YieldAgent/job_models.py
from datetime import datetime
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
ALLOWED = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.QUEUED, JobStatus.WAITING_INPUT, JobStatus.SUCCEEDED,
        JobStatus.FAILED, JobStatus.CANCELLED,
    },
    JobStatus.WAITING_INPUT: {JobStatus.QUEUED, JobStatus.CANCELLED},
}


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if current in TERMINAL:
        raise ValueError(f"terminal job cannot transition from {current}")
    if target not in ALLOWED[current]:
        raise ValueError(f"invalid job transition: {current} -> {target}")


class JobCreate(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    session_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("query", "session_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ResumeRequest(BaseModel):
    value: str | dict[str, Any]

    @field_validator("value")
    @classmethod
    def reject_empty_value(cls, value):
        if value == "" or value == {}:
            raise ValueError("resume value must not be empty")
        return value


class JobError(BaseModel):
    category: str
    message: str


class JobSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_id: str
    session_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: str = ""
    latest_interrupt: dict[str, Any] | None = None
    error: JobError | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class JobCreated(JobSnapshot):
    events_url: str
```

- [ ] **Step 4: Verify pass**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_job_models.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/job_models.py 08-YieldAgent/tests/unit/test_job_models.py
git commit -m "feat(jobs): define durable job states"
```

### Task 3: Trusted Platform Identity

**Files:**
- Create: `08-YieldAgent/identity.py`
- Create: `08-YieldAgent/tests/unit/test_identity.py`

**Interfaces:**
- Produces: `PlatformIdentity(owner_id: str, owner_hash: str)`, `get_platform_identity(request: Request) -> PlatformIdentity`
- Consumes: `get_settings()` and secret `OWNER_HASH_KEY`

- [ ] **Step 1: Write header-only identity tests**

```python
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from identity import PlatformIdentity, get_platform_identity


def build_client():
    app = FastAPI()
    @app.get("/whoami")
    def whoami(identity: PlatformIdentity = Depends(get_platform_identity)):
        return identity.model_dump()
    return TestClient(app)


def test_missing_gateway_header_is_401(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "test-key")
    assert build_client().get("/whoami").status_code == 401


def test_identity_comes_from_header(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "test-key")
    response = build_client().get("/whoami", headers={"X-Authenticated-User": "employee123"})
    assert response.status_code == 200
    assert response.json()["owner_id"] == "employee123"
    assert response.json()["owner_hash"] != "employee123"
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_identity.py -q`
Expected: FAIL because `identity` does not exist.

- [ ] **Step 3: Implement trusted extraction and keyed hash**

```python
# 08-YieldAgent/identity.py
import hashlib
import hmac
from fastapi import HTTPException, Request
from pydantic import BaseModel
from settings import get_settings


class PlatformIdentity(BaseModel):
    owner_id: str
    owner_hash: str


def get_platform_identity(request: Request) -> PlatformIdentity:
    settings = get_settings()
    owner_id = request.headers.get(settings.platform_user_id_header, "").strip()
    if not owner_id:
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    secret = settings.owner_hash_key
    if not secret:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONFIG_ERROR"})
    key = secret.get_secret_value()
    digest = hmac.new(key.encode(), owner_id.encode(), hashlib.sha256).hexdigest()[:24]
    return PlatformIdentity(owner_id=owner_id, owner_hash=digest)
```

In tests, clear `get_settings` cache before and after each environment change using an autouse fixture in `test_identity.py`.

- [ ] **Step 4: Verify pass**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_identity.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/identity.py 08-YieldAgent/tests/unit/test_identity.py
git commit -m "feat(auth): trust gateway user identity"
```

### Task 4: Durable MongoDB Job Repository

**Files:**
- Create: `08-YieldAgent/job_repository.py`
- Create: `08-YieldAgent/tests/integration/conftest.py`
- Create: `08-YieldAgent/tests/integration/test_job_repository.py`
- Create: `docker-compose.integration.yml`

**Interfaces:**
- Produces: `JobRepository.ensure_indexes()`, `find_idempotent`, `create_job`, `get_owned`, `transition`, `claim`, `renew_lease`, `release_terminal`
- Consumes: Motor database, `JobStatus`, UTC clock

- [ ] **Step 1: Add real MongoDB integration fixture and failing uniqueness test**

```python
# 08-YieldAgent/tests/integration/test_job_repository.py
import pytest
from job_repository import JobRepository, SessionBusy


@pytest.mark.asyncio
async def test_only_one_active_job_per_owner_session(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    first = (await repo.create_job("job-1", "owner-1", "hash-1", "session-1", "query", None)).job
    with pytest.raises(SessionBusy):
        await repo.create_job("job-2", "owner-1", "hash-1", "session-1", "other", None)
    assert first["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_idempotency_returns_current_original_job(mongo_db):
    repo = JobRepository(mongo_db)
    await repo.ensure_indexes()
    first = (await repo.create_job("job-1", "owner-1", "hash-1", "s1", "query", "request-1")).job
    again = (await repo.create_job("job-2", "owner-1", "hash-1", "s1", "query", "request-1")).job
    assert again["job_id"] == first["job_id"]


@pytest.mark.asyncio
async def test_different_owners_namespace_same_session_id(mongo_db):
    repo = JobRepository(mongo_db)
    first = (await repo.create_job("job-1", "owner-1", "hash-1", "s1", "query", None)).job
    second = (await repo.create_job("job-2", "owner-2", "hash-2", "s1", "query", None)).job
    assert first["thread_id"] == "hash-1:s1"
    assert second["thread_id"] == "hash-2:s1"
```

`docker-compose.integration.yml` must expose MongoDB only on `127.0.0.1:27028` and Redis only on `127.0.0.1:6380`, use named temporary test volumes, and enable Redis `appendonly yes`.

- [ ] **Step 2: Start services and verify failure**

Run: `docker compose -f docker-compose.integration.yml up -d mongo redis`
Expected: both services healthy.

Run: `cd 08-YieldAgent && TEST_MONGO_URI=mongodb://127.0.0.1:27028 ../.venv/bin/pytest tests/integration/test_job_repository.py -q`
Expected: FAIL because `JobRepository` does not exist.

- [ ] **Step 3: Implement repository with authoritative indexes**

Implement these exact MongoDB fields and indexes in `job_repository.py`:

```python
from dataclasses import dataclass

ACTIVE_STATUSES = {"QUEUED", "RUNNING", "WAITING_INPUT"}

@dataclass(frozen=True)
class CreateJobResult:
    job: dict
    created: bool

class SessionBusy(Exception):
    pass

class JobNotFound(Exception):
    pass

class TransitionConflict(Exception):
    pass

class JobRepository:
    def __init__(self, database):
        self.jobs = database.jobs

    async def ensure_indexes(self) -> None:
        await self.jobs.create_index("job_id", unique=True)
        await self.jobs.create_index("active_session_key", unique=True, sparse=True)
        await self.jobs.create_index(
            [("owner_id", 1), ("idempotency_key", 1)],
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
        )
        await self.jobs.create_index([("owner_id", 1), ("created_at", -1)])
        await self.jobs.create_index("expires_at", expireAfterSeconds=0)
```

`create_job(job_id, owner_id, owner_hash, session_id, query, idempotency_key)` first checks an owner-scoped idempotency key, then inserts the supplied UUID job. Compute `active_session_key` as SHA-256 of canonical JSON array `[owner_id, session_id]` so delimiter characters cannot collide. Store `thread_id=f"{owner_hash}:{session_id}"`, `run_sequence=0`, `attempt=0`, `cancel_requested=False`, and UTC timestamps. Omit `idempotency_key` entirely when the request has none. Return `CreateJobResult(job: dict, created: bool)`. Convert duplicate `active_session_key` to `SessionBusy`; on an idempotency race, return the original job with `created=False`.

`transition` must include both expected current status and job ID in its update filter. Terminal transitions use one update that sets the terminal state, `artifact_expires_at=now+30 days`, and `expires_at=now+31 days`, then unsets `active_session_key`, lease, and worker fields. Active jobs never receive either expiry, so MongoDB TTL cannot delete queued, running, or waiting work. The one-day metadata grace lets cleanup remove NAS files and the LangGraph checkpoint before MongoDB TTL removes the job record.

- [ ] **Step 4: Add claim and lease tests, then implementation**

```python
@pytest.mark.asyncio
async def test_only_one_worker_claims_a_queued_job(mongo_db):
    repo = JobRepository(mongo_db)
    job = (await repo.create_job("job-1", "owner-1", "hash-1", "s1", "query", None)).job
    first = await repo.claim(job["job_id"], "task-1", "worker-1", lease_seconds=60)
    second = await repo.claim(job["job_id"], "task-2", "worker-2", lease_seconds=60)
    assert first is not None
    assert second is None
```

`claim` performs one `find_one_and_update` from `QUEUED` to `RUNNING`. Expired `RUNNING` lease recovery is a separate method `reclaim_expired(job_id, task_id, worker_id, lease_seconds)` whose filter requires `lease_expires_at < now`.

- [ ] **Step 5: Run repository integration tests**

Run: `cd 08-YieldAgent && TEST_MONGO_URI=mongodb://127.0.0.1:27028 ../.venv/bin/pytest tests/integration/test_job_repository.py -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.integration.yml 08-YieldAgent/job_repository.py \
  08-YieldAgent/tests/integration/conftest.py \
  08-YieldAgent/tests/integration/test_job_repository.py
git commit -m "feat(jobs): persist guarded job state"
```

### Task 5: Redis Admission Control

**Files:**
- Create: `08-YieldAgent/admission.py`
- Create: `08-YieldAgent/tests/integration/test_admission.py`

**Interfaces:**
- Produces: `AdmissionController.acquire(owner_hash, job_id)`, `release(owner_hash, job_id)`, `reconcile(job_counts)`
- Consumes: Redis async client, configured limits

- [ ] **Step 1: Write atomic limit tests against real Redis**

```python
@pytest.mark.asyncio
async def test_user_limit_is_atomic(redis_client):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)
    await admission.acquire("u1", "j1")
    await admission.acquire("u1", "j2")
    with pytest.raises(UserLimitExceeded):
        await admission.acquire("u1", "j3")


@pytest.mark.asyncio
async def test_release_is_idempotent(redis_client):
    admission = AdmissionController(redis_client, user_limit=2, global_limit=100)
    await admission.acquire("u1", "j1")
    await admission.release("u1", "j1")
    await admission.release("u1", "j1")
    assert await admission.global_count() == 0
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && TEST_REDIS_URL=redis://127.0.0.1:6380/0 ../.venv/bin/pytest tests/integration/test_admission.py -q`
Expected: FAIL because `admission` does not exist.

- [ ] **Step 3: Implement Lua-backed acquire/release**

Use Redis sets `jobs:active:global` and `jobs:active:user:{owner_hash}`. One Lua script checks `SCARD`, adds `job_id` to both sets, and returns `USER_LIMIT`, `GLOBAL_LIMIT`, or `OK`. Release removes from both sets in one script. A repeated acquire/release for the same job is idempotent.

Expose exceptions `UserLimitExceeded` and `GlobalLimitExceeded`; do not use Redis counters that can go below zero.

- [ ] **Step 4: Verify pass**

Run: `cd 08-YieldAgent && TEST_REDIS_URL=redis://127.0.0.1:6380/0 ../.venv/bin/pytest tests/integration/test_admission.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/admission.py 08-YieldAgent/tests/integration/test_admission.py
git commit -m "feat(jobs): enforce admission limits"
```

### Task 6: Job Creation and Status API

**Files:**
- Create: `08-YieldAgent/job_dispatcher.py`
- Create: `08-YieldAgent/job_service.py`
- Create: `08-YieldAgent/job_router.py`
- Modify: `08-YieldAgent/agent_server.py`
- Create: `08-YieldAgent/tests/unit/test_job_router.py`

**Interfaces:**
- Produces: `JobDispatcher.dispatch(job_id, run_sequence)`, `JobService.create/get`, FastAPI `/jobs` routes
- Consumes: identity, repository, admission controller, settings

- [ ] **Step 1: Write API contract tests with injected fakes**

```python
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
    response = client.post("/jobs", headers=identity_header, json={**body, "query": "second"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SESSION_BUSY"


def test_other_owner_cannot_discover_job(client, identity_header):
    created = client.post("/jobs", headers=identity_header, json={"query": "q", "session_id": "s1"}).json()
    response = client.get(
        f"/jobs/{created['job_id']}",
        headers={"X-Authenticated-User": "other"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_job_router.py -q`
Expected: FAIL because routes are absent.

- [ ] **Step 3: Implement dispatcher protocol and service compensation**

```python
# 08-YieldAgent/job_dispatcher.py
from typing import Protocol

class JobDispatcher(Protocol):
    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        raise NotImplementedError
```

`JobService.create` first calls `find_idempotent(owner_id, idempotency_key)` when a key exists and immediately returns an existing job. Otherwise it generates a candidate job ID, acquires admission for that ID, and calls the repository. This precheck lets a repeated request return its original even when the owner is already at the active-job limit. If a simultaneous idempotency race makes the repository return `created=False`, release the candidate admission and return the original job without dispatch. Before broker send, store deterministic `task_id` and `dispatch_requested_at` on the queued job; after send, store `dispatched_at`. If insert fails, release admission. If dispatch returns an error, transition the inserted job to `FAILED`, release both session ownership and admission, then return `503 DISPATCH_UNAVAILABLE`. A process crash between durable insert and broker send is recovered by the queued-job reconciler in the worker plan.

- [ ] **Step 4: Add router and application dependencies**

`job_router.py` exposes `POST /jobs` and `GET /jobs/{job_id}`. Put repository, Redis client, admission controller, dispatcher, and service on `app.state` during lifespan; tests override dependencies with in-memory fakes. Before readiness becomes true, rebuild Redis admission sets from MongoDB non-terminal jobs under the same reconciler lock used by workers. This closes the capacity gap after Redis recovery.

Replace hardcoded `MONGO_URI` and `MONGO_DB` in `agent_server.py` with `get_settings()`. Move `supervisor`, REPL, wiki, and local-trace initialization imports behind their feature flags. Compile the LangGraph checkpointer in API lifespan only when `enable_legacy_chat` is true; production API replicas must not compile or own a graph. Start wiki queue/lint tasks only when `enable_wiki` is true. Include `/repl` and `/api/wiki` routers only when their flags are true. Add an HTTP middleware that returns `404` for `/chat/stream`, `/session`, `/sessions`, and `/download/pptx` when `enable_legacy_chat` is false; this avoids a large legacy refactor before frontend migration. Require trusted platform identity on `/mining/tas`. In production, configure CORS from an exact `CORS_ORIGINS` list, exclude development origins, and never use wildcard origins.

- [ ] **Step 5: Verify API tests and existing import safety**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_job_router.py tests/unit/test_identity.py tests/unit/test_job_models.py -q`
Expected: all tests pass.

Run: `cd 08-YieldAgent && ENVIRONMENT=test ../.venv/bin/python -c 'import agent_server; print(agent_server.app.title)'`
Expected: `Yield Agent Server` without connecting to Oracle.

- [ ] **Step 6: Commit**

```bash
git add 08-YieldAgent/job_dispatcher.py 08-YieldAgent/job_service.py \
  08-YieldAgent/job_router.py 08-YieldAgent/agent_server.py \
  08-YieldAgent/tests/unit/test_job_router.py
git commit -m "feat(api): accept durable analysis jobs"
```

### Task 7: Foundation Integration Gate

**Files:**
- Create: `08-YieldAgent/tests/integration/test_job_api.py`
- Modify: `08-YieldAgent/AGENTS.md`

**Interfaces:**
- Consumes: completed foundation interfaces
- Produces: verified owner/session/idempotency/admission behavior for the worker plan

- [ ] **Step 1: Add real-service API integration scenario**

The test must create the FastAPI app with real MongoDB and Redis but a recording dispatcher. It submits the same idempotency key twice, verifies one dispatch, verifies a same-session conflict, fills the per-user limit with a different session, verifies the third active session returns `429 USER_JOB_LIMIT`, terminalizes one job, releases admission, and verifies a new job is accepted.

- [ ] **Step 2: Run the complete foundation gate**

Run:

```bash
docker compose -f docker-compose.integration.yml up -d mongo redis
cd 08-YieldAgent
TEST_MONGO_URI=mongodb://127.0.0.1:27028 \
TEST_REDIS_URL=redis://127.0.0.1:6380/0 \
../.venv/bin/pytest tests/unit tests/integration/test_job_repository.py \
  tests/integration/test_admission.py tests/integration/test_job_api.py -q
```

Expected: all foundation tests pass with no skips.

- [ ] **Step 3: Document new module ownership**

Add the new settings, identity, repository, admission, service, dispatcher, and router files to `08-YieldAgent/AGENTS.md`. Record that MongoDB owns same-session correctness and Redis admission counters are reconciled.

- [ ] **Step 4: Commit**

```bash
git add 08-YieldAgent/tests/integration/test_job_api.py 08-YieldAgent/AGENTS.md
git commit -m "test(jobs): verify foundation integration"
```
