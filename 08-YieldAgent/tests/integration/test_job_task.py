from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from celery.exceptions import Retry
from pymongo import MongoClient
from redis import Redis

from admission import GLOBAL_ACTIVE_KEY, USER_ACTIVE_KEY_PREFIX
from graph_job_runner import GraphRunResult
from job_tasks import run_job
from settings import Settings
from worker_runtime import WorkerRuntime


pytestmark = pytest.mark.no_server


@pytest.fixture
def infrastructure(monkeypatch):
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    redis_url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    database_name = f"yield_agent_job_task_{uuid.uuid4().hex}"
    mongo = MongoClient(mongo_uri, tz_aware=True)
    redis = Redis.from_url(redis_url, decode_responses=True)
    mongo.admin.command("ping")
    redis.ping()
    redis.flushdb()

    settings = Settings(
        environment="test",
        mongo_uri=mongo_uri,
        mongo_db=database_name,
        redis_url=redis_url,
    )
    try:
        yield mongo[database_name], redis, settings
    finally:
        redis.flushdb()
        redis.close()
        mongo.drop_database(database_name)
        mongo.close()


def _insert_job(database, redis, *, status="QUEUED", lease_expires_at=None):
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    owner_hash = "owner-hash"
    document = {
        "job_id": job_id,
        "owner_id": "owner-1",
        "owner_hash": owner_hash,
        "session_id": f"session-{job_id}",
        "thread_id": f"{owner_hash}:session-{job_id}",
        "active_session_key": f"active-{job_id}",
        "query": "query",
        "status": status,
        "run_sequence": 0,
        "attempt": 0,
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
    }
    if status == "RUNNING":
        document.update(
            task_id="prior-task",
            worker_id="prior-worker",
            lease_expires_at=lease_expires_at,
        )
    database.jobs.insert_one(document)
    redis.sadd(GLOBAL_ACTIVE_KEY, job_id)
    redis.sadd(f"{USER_ACTIVE_KEY_PREFIX}{owner_hash}", job_id)
    return document


class FakeRunner:
    def __init__(self, outcome="SUCCEEDED"):
        self.outcome = outcome
        self.calls = 0

    async def __call__(self, graph, request, emit, cancelled):
        self.calls += 1
        await emit({"type": "status", "message": "running"})
        if self.outcome == "RAISE":
            raise RuntimeError("sensitive backend detail")
        if self.outcome == "WAITING_INPUT":
            return GraphRunResult(
                outcome="WAITING_INPUT",
                latest_interrupt={"type": "missing_param", "fields": []},
            )
        return GraphRunResult(
            outcome=self.outcome,
            final_result={"answer": "done"},
        )


class TransientRunner:
    def __init__(self):
        self.calls = 0

    async def __call__(self, graph, request, emit, cancelled):
        self.calls += 1
        raise TimeoutError("private upstream details")


def _install_runtime(monkeypatch, settings, runner):
    import job_tasks

    runtime = WorkerRuntime(settings=settings, graph=object(), graph_runner=runner)
    monkeypatch.setattr(job_tasks, "runtime", runtime)
    return runtime


def test_worker_claims_runs_and_completes(infrastructure, monkeypatch):
    database, redis, settings = infrastructure
    job = _insert_job(database, redis)
    runner = FakeRunner()
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 0], throw=True).get()

    stored = database.jobs.find_one({"job_id": job["job_id"]})
    assert result["status"] == "SUCCEEDED"
    assert stored["status"] == "SUCCEEDED"
    assert stored["result"] == {"answer": "done"}
    assert "active_session_key" not in stored
    assert redis.scard(GLOBAL_ACTIVE_KEY) == 0
    assert runner.calls == 1


def test_worker_failure_is_sanitized_and_releases_admission(
    infrastructure, monkeypatch
):
    database, redis, settings = infrastructure
    job = _insert_job(database, redis)
    runner = FakeRunner("RAISE")
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 0], throw=True).get()

    stored = database.jobs.find_one({"job_id": job["job_id"]})
    assert result == {"status": "FAILED"}
    assert stored["status"] == "FAILED"
    assert stored["error"] == {
        "category": "worker",
        "message": "job execution failed",
    }
    assert "sensitive backend detail" not in str(stored)
    assert "active_session_key" not in stored
    assert redis.scard(GLOBAL_ACTIVE_KEY) == 0


def test_transient_failure_retries_twice_with_bounded_backoff(
    infrastructure, monkeypatch
):
    database, redis, settings = infrastructure
    job = _insert_job(database, redis)
    runner = TransientRunner()
    _install_runtime(monkeypatch, settings, runner)

    with pytest.raises(Retry) as first:
        run_job.apply(args=[job["job_id"], 0], throw=True)
    first_job = database.jobs.find_one({"job_id": job["job_id"]})
    assert first.value.when == 5
    assert first_job["status"] == "QUEUED"
    assert first_job["attempt"] == 1

    with pytest.raises(Retry) as second:
        run_job.apply(args=[job["job_id"], 0], throw=True)
    second_job = database.jobs.find_one({"job_id": job["job_id"]})
    assert second.value.when == 20
    assert second_job["status"] == "QUEUED"
    assert second_job["attempt"] == 2

    result = run_job.apply(args=[job["job_id"], 0], throw=True).get()
    stored = database.jobs.find_one({"job_id": job["job_id"]})
    assert result == {"status": "FAILED"}
    assert stored["attempt"] == 3
    assert stored["error"] == {
        "category": "infrastructure",
        "message": "job execution failed",
    }
    assert "private upstream details" not in str(stored)
    assert redis.scard(GLOBAL_ACTIVE_KEY) == 0

    events = [
        json.loads(fields["payload"])
        for _, fields in redis.xrange(f"job:events:{job['job_id']}")
    ]
    retry_delays = [
        event["retry_after_seconds"]
        for event in events
        if event["type"] == "status" and "retry_after_seconds" in event
    ]
    assert retry_delays == [5, 20]
    assert events[-2]["type"] == "job_snapshot"
    assert events[-1] == {"type": "stream_end"}


def test_live_lease_retries_without_graph_call(infrastructure, monkeypatch):
    database, redis, settings = infrastructure
    job = _insert_job(
        database,
        redis,
        status="RUNNING",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    runner = FakeRunner()
    _install_runtime(monkeypatch, settings, runner)

    with pytest.raises(Retry):
        run_job.apply(args=[job["job_id"], 0], throw=True)

    assert runner.calls == 0
    assert database.jobs.find_one({"job_id": job["job_id"]})["status"] == "RUNNING"


def test_live_lease_is_acknowledged_after_retry_budget(infrastructure, monkeypatch):
    database, redis, settings = infrastructure
    job = _insert_job(
        database,
        redis,
        status="RUNNING",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    runner = FakeRunner()
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 0], retries=3, throw=True).get()

    assert result == {"status": "RUNNING"}
    assert runner.calls == 0


def test_expired_lease_is_reclaimed(infrastructure, monkeypatch):
    database, redis, settings = infrastructure
    job = _insert_job(
        database,
        redis,
        status="RUNNING",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    runner = FakeRunner()
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 0], throw=True).get()

    stored = database.jobs.find_one({"job_id": job["job_id"]})
    assert result["status"] == "SUCCEEDED"
    assert stored["attempt"] == 1
    assert runner.calls == 1


def test_waiting_input_keeps_session_and_admission(infrastructure, monkeypatch):
    database, redis, settings = infrastructure
    job = _insert_job(database, redis)
    runner = FakeRunner("WAITING_INPUT")
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 0], throw=True).get()

    stored = database.jobs.find_one({"job_id": job["job_id"]})
    assert result["status"] == "WAITING_INPUT"
    assert stored["status"] == "WAITING_INPUT"
    assert stored["active_session_key"] == job["active_session_key"]
    assert stored["latest_interrupt"]["type"] == "missing_param"
    assert "lease_expires_at" not in stored
    assert redis.sismember(GLOBAL_ACTIVE_KEY, job["job_id"])


def test_other_run_sequence_is_acknowledged_without_graph_call(
    infrastructure, monkeypatch
):
    database, redis, settings = infrastructure
    job = _insert_job(database, redis)
    runner = FakeRunner()
    _install_runtime(monkeypatch, settings, runner)

    result = run_job.apply(args=[job["job_id"], 1], throw=True).get()

    assert result == {"status": "IGNORED"}
    assert runner.calls == 0
    assert database.jobs.find_one({"job_id": job["job_id"]})["status"] == "QUEUED"
