from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from job_dispatcher import JobDispatcher
from job_models import JobCreate, JobStatus
from job_repository import ACTIVE_STATUSES


RECONCILER_LOCK_KEY = "jobs:reconciler"
RECONCILER_LOCK_SECONDS = 300


async def reconcile_admission(repository, admission, redis) -> None:
    lock = redis.lock(
        RECONCILER_LOCK_KEY,
        timeout=RECONCILER_LOCK_SECONDS,
        blocking_timeout=RECONCILER_LOCK_SECONDS,
    )
    if not await lock.acquire():
        raise RuntimeError("could not acquire admission reconciliation lock")
    try:
        counts: dict[str, set[str]] = defaultdict(set)
        cursor = repository.jobs.find(
            {"status": {"$in": sorted(ACTIVE_STATUSES)}},
            {"_id": 0, "job_id": 1, "owner_hash": 1},
        )
        async for job in cursor:
            counts[job["owner_hash"]].add(job["job_id"])
        await admission.reconcile(dict(counts))
    finally:
        await lock.release()


class DispatchUnavailable(Exception):
    pass


class JobService:
    def __init__(self, repository, admission, dispatcher: JobDispatcher):
        self.repository = repository
        self.admission = admission
        self.dispatcher = dispatcher

    async def create(self, request: JobCreate, identity) -> dict:
        if request.idempotency_key is not None:
            existing = await self.repository.find_idempotent(
                identity.owner_id, request.idempotency_key
            )
            if existing is not None:
                return existing

        job_id = str(uuid.uuid4())
        await self.admission.acquire(identity.owner_hash, job_id)
        try:
            result = await self.repository.create_job(
                job_id=job_id,
                owner_id=identity.owner_id,
                owner_hash=identity.owner_hash,
                session_id=request.session_id,
                query=request.query,
                idempotency_key=request.idempotency_key,
            )
        except Exception:
            await self.admission.release(identity.owner_hash, job_id)
            raise

        if not result.created:
            await self.admission.release(identity.owner_hash, job_id)
            return result.job

        run_sequence = result.job["run_sequence"]
        task_id = f"{job_id}:{run_sequence}"
        requested_at = datetime.now(timezone.utc)
        job = await self.repository.mark_dispatch_requested(
            job_id, run_sequence, task_id, requested_at
        )
        try:
            await self.dispatcher.dispatch(job_id, run_sequence)
        except Exception as exc:
            try:
                await self.repository.transition(
                    job_id,
                    JobStatus.QUEUED,
                    JobStatus.FAILED,
                    {
                        "error": {
                            "category": "dispatch",
                            "message": "job dispatch unavailable",
                        }
                    },
                )
            finally:
                await self.admission.release(identity.owner_hash, job_id)
            raise DispatchUnavailable from exc

        dispatched_at = datetime.now(timezone.utc)
        job = await self.repository.mark_dispatched(
            job_id, run_sequence, task_id, dispatched_at
        )
        return job

    async def get(self, job_id: str, identity) -> dict:
        return await self.repository.get_owned(job_id, identity.owner_id)
