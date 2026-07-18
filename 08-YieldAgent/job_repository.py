from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from job_models import JobStatus, assert_transition, is_terminal


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


def _active_session_key(owner_id: str, session_id: str) -> str:
    canonical = json.dumps(
        [owner_id, session_id], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

    async def find_idempotent(
        self, owner_id: str, idempotency_key: str | None
    ) -> dict | None:
        if idempotency_key is None:
            return None
        return await self.jobs.find_one(
            {"owner_id": owner_id, "idempotency_key": idempotency_key}
        )

    async def create_job(
        self,
        job_id: str,
        owner_id: str,
        owner_hash: str,
        session_id: str,
        query: str,
        idempotency_key: str | None,
    ) -> CreateJobResult:
        existing = await self.find_idempotent(owner_id, idempotency_key)
        if existing is not None:
            return CreateJobResult(job=existing, created=False)

        now = datetime.now(timezone.utc)
        job = {
            "job_id": job_id,
            "owner_id": owner_id,
            "owner_hash": owner_hash,
            "session_id": session_id,
            "thread_id": f"{owner_hash}:{session_id}",
            "active_session_key": _active_session_key(owner_id, session_id),
            "query": query,
            "status": JobStatus.QUEUED.value,
            "run_sequence": 0,
            "attempt": 0,
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
        }
        if idempotency_key is not None:
            job["idempotency_key"] = idempotency_key

        try:
            await self.jobs.insert_one(job)
        except DuplicateKeyError as exc:
            existing = await self.find_idempotent(owner_id, idempotency_key)
            if existing is not None:
                return CreateJobResult(job=existing, created=False)
            key_pattern = (exc.details or {}).get("keyPattern", {})
            if "active_session_key" in key_pattern:
                raise SessionBusy(f"session already has an active job: {session_id}") from exc
            raise
        return CreateJobResult(job=job, created=True)

    async def get_owned(self, job_id: str, owner_id: str) -> dict:
        job = await self.jobs.find_one({"job_id": job_id, "owner_id": owner_id})
        if job is None:
            raise JobNotFound(job_id)
        return job

    async def claim(
        self, job_id: str, task_id: str, worker_id: str, lease_seconds: int
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        return await self.jobs.find_one_and_update(
            {"job_id": job_id, "status": JobStatus.QUEUED.value},
            {
                "$set": {
                    "status": JobStatus.RUNNING.value,
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def reclaim_expired(
        self, job_id: str, task_id: str, worker_id: str, lease_seconds: int
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        return await self.jobs.find_one_and_update(
            {
                "job_id": job_id,
                "status": JobStatus.RUNNING.value,
                "lease_expires_at": {"$lt": now},
            },
            {
                "$set": {
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def renew_lease(
        self, job_id: str, task_id: str, worker_id: str, lease_seconds: int
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        return await self.jobs.find_one_and_update(
            {
                "job_id": job_id,
                "status": JobStatus.RUNNING.value,
                "task_id": task_id,
                "worker_id": worker_id,
            },
            {
                "$set": {
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def transition(
        self,
        job_id: str,
        expected_status: JobStatus | str,
        target_status: JobStatus | str,
        updates: dict[str, Any] | None = None,
    ) -> dict:
        current = JobStatus(expected_status)
        target = JobStatus(target_status)
        assert_transition(current, target)

        now = datetime.now(timezone.utc)
        set_fields = dict(updates or {})
        set_fields.update({"status": target.value, "updated_at": now})
        update: dict[str, dict[str, Any]] = {"$set": set_fields}
        if is_terminal(target):
            set_fields.update(
                {
                    "artifact_expires_at": now + timedelta(days=30),
                    "expires_at": now + timedelta(days=31),
                }
            )
            update["$unset"] = {
                "active_session_key": "",
                "lease_expires_at": "",
                "task_id": "",
                "worker_id": "",
            }

        job = await self.jobs.find_one_and_update(
            {"job_id": job_id, "status": current.value},
            update,
            return_document=ReturnDocument.AFTER,
        )
        if job is not None:
            return job
        if await self.jobs.find_one({"job_id": job_id}, {"_id": 1}) is None:
            raise JobNotFound(job_id)
        raise TransitionConflict(
            f"job {job_id} is not in expected status {current.value}"
        )

    async def release_terminal(
        self,
        job_id: str,
        expected_status: JobStatus | str,
        target_status: JobStatus | str,
        updates: dict[str, Any] | None = None,
    ) -> dict:
        target = JobStatus(target_status)
        if not is_terminal(target):
            raise ValueError(f"release target must be terminal: {target.value}")
        return await self.transition(job_id, expected_status, target, updates)
