from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from redis import Redis

from admission import GLOBAL_ACTIVE_KEY, USER_ACTIVE_KEY_PREFIX
from artifact_context import artifact_url, save_artifact
from artifact_store import ArtifactRef, ArtifactStore
from job_router import router
from settings import Settings, get_settings
from worker_runtime import WorkerRuntime


pytestmark = pytest.mark.no_server


def _isolated_redis_url() -> str:
    parsed = urlsplit(
        os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
    )
    return urlunsplit(parsed._replace(path="/12", query="", fragment=""))


@pytest.fixture
def artifact_app(tmp_path, monkeypatch):
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    database_name = f"yield_agent_artifact_api_{uuid.uuid4().hex}"
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "artifact-owner-hash-key")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
        repository = __import__("job_repository").JobRepository(client[database_name])
        await client.admin.command("ping")
        await repository.ensure_indexes()
        app.state.job_repository = repository
        try:
            yield
        finally:
            await client.drop_database(database_name)
            client.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        yield app, client, tmp_path
    get_settings.cache_clear()


def _seed_artifact(app, client, root: Path, *, artifact_type="html", mime="text/html"):
    owner_id = "owner"
    owner_hash = "owner-hash"
    job_id = str(uuid.uuid4())
    store = ArtifactStore(root, owner_hash=owner_hash, job_id=job_id)
    content = b"<h1>safe report</h1>" if artifact_type == "html" else b"pptx-bytes"
    ref = store.write_bytes(
        content,
        mime=mime,
        title="quarterly report",
        agent="yield_agent",
        artifact_type=artifact_type,
    )
    now = datetime.now(timezone.utc)

    async def insert():
        await app.state.job_repository.jobs.insert_one(
            {
                "job_id": job_id,
                "owner_id": owner_id,
                "owner_hash": owner_hash,
                "session_id": "artifact-session",
                "thread_id": f"{owner_hash}:artifact-session",
                "query": "report",
                "status": "SUCCEEDED",
                "run_sequence": 0,
                "attempt": 1,
                "cancel_requested": False,
                "created_at": now,
                "updated_at": now,
                "artifacts": [ref.model_dump()],
            }
        )

    client.portal.call(insert)
    return job_id, ref, content


def test_owner_downloads_verified_html_inline(artifact_app):
    app, client, root = artifact_app
    job_id, ref, content = _seed_artifact(app, client, root)

    response = client.get(
        f"/jobs/{job_id}/artifacts/{ref.artifact_id}",
        headers={"X-Authenticated-User": "owner"},
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == 'inline; filename="quarterly_report.html"'
    assert response.headers["x-content-type-options"] == "nosniff"


def test_pptx_download_uses_controlled_attachment_filename(artifact_app):
    app, client, root = artifact_app
    job_id, ref, content = _seed_artifact(
        app,
        client,
        root,
        artifact_type="pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    response = client.get(
        f"/jobs/{job_id}/artifacts/{ref.artifact_id}",
        headers={"X-Authenticated-User": "owner"},
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-disposition"] == 'attachment; filename="quarterly_report.pptx"'


def test_other_owner_and_unknown_artifact_are_both_hidden(artifact_app):
    app, client, root = artifact_app
    job_id, ref, _ = _seed_artifact(app, client, root)

    other = client.get(
        f"/jobs/{job_id}/artifacts/{ref.artifact_id}",
        headers={"X-Authenticated-User": "other"},
    )
    unknown = client.get(
        f"/jobs/{job_id}/artifacts/unknown",
        headers={"X-Authenticated-User": "owner"},
    )

    assert other.status_code == 404
    assert unknown.status_code == 404
    assert other.json() == unknown.json()


def test_forged_traversal_is_rejected_before_file_open(artifact_app, monkeypatch):
    app, client, root = artifact_app
    job_id, ref, _ = _seed_artifact(app, client, root)

    async def forge():
        await app.state.job_repository.jobs.update_one(
            {"job_id": job_id},
            {"$set": {"artifacts.0.relative_path": "../../secret"}},
        )

    client.portal.call(forge)
    opened = False
    original_open = Path.open

    def track_open(self, *args, **kwargs):
        nonlocal opened
        opened = True
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    response = client.get(
        f"/jobs/{job_id}/artifacts/{ref.artifact_id}",
        headers={"X-Authenticated-User": "owner"},
    )

    assert response.status_code == 404
    assert opened is False


@pytest.mark.parametrize("field,value", [("size", 1), ("checksum", "0" * 64)])
def test_metadata_integrity_mismatch_is_not_served(artifact_app, field, value):
    app, client, root = artifact_app
    job_id, ref, _ = _seed_artifact(app, client, root)

    async def corrupt():
        await app.state.job_repository.jobs.update_one(
            {"job_id": job_id}, {"$set": {f"artifacts.0.{field}": value}}
        )

    client.portal.call(corrupt)
    response = client.get(
        f"/jobs/{job_id}/artifacts/{ref.artifact_id}",
        headers={"X-Authenticated-User": "owner"},
    )
    assert response.status_code == 404


class ArtifactGraph:
    def __init__(self):
        self.values = {}

    async def aget_state(self, _config):
        return SimpleNamespace(values=self.values, tasks=[])

    async def astream(self, stream_input, *, config, stream_mode):
        image = save_artifact(
            b"png-child",
            "image/png",
            "wafer image",
            "map_agent",
            "image",
        )
        html = save_artifact(
            f'<img src="{artifact_url(image)}">',
            "text/html",
            "wafer map",
            "map_agent",
            "html",
        )
        self.values = {"user_id": "owner", "memory_feedback": []}
        yield "updates", {
            "map_agent": {
                "step_count": 1,
                "map_artifacts": [
                    {
                        "type": "html",
                        "mime": "text/html",
                        "title": "wafer map",
                        "agent": "map_agent",
                        "artifact_ref": html.model_dump(),
                    }
                ],
            }
        }


@pytest.fixture
def worker_infrastructure(tmp_path):
    mongo_uri = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028")
    redis_url = _isolated_redis_url()
    database_name = f"yield_agent_artifact_worker_{uuid.uuid4().hex}"
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
        artifact_root=tmp_path,
    )
    try:
        yield mongo[database_name], redis, settings
    finally:
        redis.flushdb()
        redis.close()
        mongo.drop_database(database_name)
        mongo.close()


def test_worker_persists_all_refs_before_reference_only_events(worker_infrastructure):
    database, redis, settings = worker_infrastructure
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "owner_id": "owner",
        "owner_hash": "owner-hash",
        "session_id": "artifact-worker-session",
        "thread_id": "owner-hash:artifact-worker-session",
        "active_session_key": "artifact-worker-active",
        "query": "wafer map",
        "status": "QUEUED",
        "run_sequence": 0,
        "attempt": 0,
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
    }
    database.jobs.insert_one(job)
    redis.sadd(GLOBAL_ACTIVE_KEY, job_id)
    redis.sadd(f"{USER_ACTIVE_KEY_PREFIX}owner-hash", job_id)
    runtime = WorkerRuntime(settings=settings, graph=ArtifactGraph())

    result = __import__("asyncio").run(
        runtime.execute_job(job_id, 0, f"{job_id}:0", "worker-1")
    )

    stored = database.jobs.find_one({"job_id": job_id})
    assert result.status == "SUCCEEDED"
    assert len(stored["artifacts"]) == 2
    assert {item["artifact_type"] for item in stored["artifacts"]} == {"html", "image"}
    events = [
        json.loads(fields["payload"])
        for _, fields in redis.xrange(f"job:events:{job_id}")
    ]
    artifact_events = [event for event in events if event["type"] == "artifact"]
    assert {event["artifact_id"] for event in artifact_events} == {
        item["artifact_id"] for item in stored["artifacts"]
    }
    assert all(
        set(event) == {"type", "artifact_id", "artifact_type", "mime", "title", "agent", "url"}
        for event in artifact_events
    )
    assert all("relative_path" not in json.dumps(event) for event in artifact_events)
    assert all("checksum" not in json.dumps(event) for event in artifact_events)
    assert "relative_path" not in json.dumps(events)
    assert "checksum" not in json.dumps(events)


@pytest.mark.asyncio
async def test_artifact_metadata_batch_is_claim_safe_and_idempotent(mongo_db):
    from job_repository import JobRepository, TransitionConflict

    repository = JobRepository(mongo_db)
    now = datetime.now(timezone.utc)
    await mongo_db.jobs.insert_one(
        {
            "job_id": "job-1",
            "owner_id": "owner",
            "owner_hash": "owner-hash",
            "status": "RUNNING",
            "run_sequence": 0,
            "task_id": "task-1",
            "worker_id": "worker-1",
            "created_at": now,
            "updated_at": now,
        }
    )
    metadata = ArtifactRef(
        artifact_id="artifact-1",
        relative_path="jobs/owner-hash/job-1/output/artifact-1",
        artifact_type="html",
        mime="text/html",
        title="report",
        agent="yield_agent",
        size=2,
        checksum=hashlib.sha256(b"ok").hexdigest(),
    ).model_dump()

    first = await repository.persist_artifacts_claimed(
        "job-1", 0, "task-1", "worker-1", [metadata, metadata]
    )
    second = await repository.persist_artifacts_claimed(
        "job-1", 0, "task-1", "worker-1", [metadata]
    )

    assert first == [metadata]
    assert second == []
    assert (await repository.get("job-1"))["artifacts"] == [metadata]
    with pytest.raises(TransitionConflict):
        await repository.persist_artifacts_claimed(
            "job-1", 0, "other-task", "worker-1", [metadata]
        )
