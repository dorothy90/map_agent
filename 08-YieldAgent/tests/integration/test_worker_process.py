from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from pymongo import MongoClient
from redis import Redis


API_URL = os.environ.get("JOB_API_URL", "http://127.0.0.1:18000")
MONGO_URI = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
MONGO_DB = os.environ.get("TEST_MONGO_DB", "yield_agent_process_test")
USER_HEADER = "X-Authenticated-User"
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker-compose.integration.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]


def _submit(control: str) -> dict:
    response = httpx.post(
        f"{API_URL}/__test/jobs",
        headers={USER_HEADER: f"process-owner-{uuid.uuid4().hex}"},
        json={
            "session_id": f"process-session-{uuid.uuid4().hex}",
            "control": control,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _sse_events(job_id: str, owner: str) -> list[dict]:
    events: list[dict] = []
    with httpx.stream(
        "GET",
        f"{API_URL}/jobs/{job_id}/events",
        headers={USER_HEADER: owner},
        timeout=20,
    ) as response:
        response.raise_for_status()
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif not line and data_lines:
                event = json.loads("\n".join(data_lines))
                events.append(event)
                data_lines = []
                if event.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    break
    return events


def _wait_for_job(collection, job_id: str, predicate, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = collection.find_one({"job_id": job_id})
        if job is not None and predicate(job):
            return job
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not reach expected state")


def _compose(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*COMPOSE, *args],
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _worker_started_at() -> str:
    container_id = _compose("ps", "-q", "worker").stdout.strip()
    return subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", container_id],
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    ).stdout.strip()


def test_job_runs_in_worker_and_streams_all_states():
    owner = f"process-owner-{uuid.uuid4().hex}"
    response = httpx.post(
        f"{API_URL}/__test/jobs",
        headers={USER_HEADER: owner},
        json={
            "session_id": f"process-session-{uuid.uuid4().hex}",
            "control": "succeed",
        },
        timeout=10,
    )
    response.raise_for_status()
    job_id = response.json()["job_id"]

    events = _sse_events(job_id, owner)
    states = [event.get("status") for event in events if event.get("status")]
    assert states == ["QUEUED", "RUNNING", "SUCCEEDED"]

    api_process = httpx.get(f"{API_URL}/__test/process", timeout=5).json()
    execution = next(event for event in events if event.get("execution_process"))
    assert execution["execution_process"] != api_process["process"]


def test_killed_worker_is_recovered_once_by_lease_reconciler():
    mongo = MongoClient(MONGO_URI, tz_aware=True)
    redis = Redis.from_url(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0"),
        decode_responses=True,
    )
    collection = mongo[MONGO_DB].jobs
    created = _submit("block_once")
    job_id = created["job_id"]
    try:
        first = _wait_for_job(
            collection,
            job_id,
            lambda job: job["status"] == "RUNNING" and job["attempt"] == 1,
        )
        assert first["worker_id"]
        first_started_at = _worker_started_at()

        _compose("kill", "-s", "KILL", "worker")
        _compose("up", "-d", "worker")
        restarted_at = _worker_started_at()

        recovered = _wait_for_job(
            collection,
            job_id,
            lambda job: job["status"] == "SUCCEEDED",
            timeout=40,
        )
        assert recovered["attempt"] == 2
        assert restarted_at != first_started_at
        assert recovered["result"]["execution_process"]

        terminal_events = [
            json.loads(fields["payload"])
            for _, fields in redis.xrange(f"job:events:{job_id}")
            if json.loads(fields["payload"]).get("status")
            in {"SUCCEEDED", "FAILED", "CANCELLED"}
        ]
        assert len(terminal_events) == 1
        assert terminal_events[0]["status"] == "SUCCEEDED"
    finally:
        _compose("up", "-d", "worker")
        redis.close()
        mongo.close()
