from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from job_dispatcher import JobDispatcher
from job_events import JobEventStore
from job_models import JobCreate, JobStatus, ResumeRequest, is_terminal
from job_repository import ACTIVE_STATUSES


RECONCILER_LOCK_KEY = "jobs:reconciler"
RECONCILER_LOCK_SECONDS = 300
CANCEL_MIRROR_TTL_SECONDS = 1_800


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


@dataclass(frozen=True)
class StreamEvent:
    id: str | None
    data: dict[str, Any]


def _ends_stream(event: StreamEvent) -> bool:
    event_type = event.data.get("type")
    if event_type == "stream_end":
        return True
    if event_type != "job_snapshot":
        return False
    try:
        return is_terminal(JobStatus(event.data.get("status")))
    except ValueError:
        return False


class JobService:
    def __init__(
        self,
        repository,
        admission,
        dispatcher: JobDispatcher,
        event_store: JobEventStore | None = None,
    ):
        self.repository = repository
        self.admission = admission
        self.dispatcher = dispatcher
        self.event_store = event_store

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

    async def resume(self, job_id: str, request: ResumeRequest, identity) -> dict:
        job = await self.repository.resume_owned(
            job_id, identity.owner_id, request.value
        )
        run_sequence = job["run_sequence"]
        task_id = f"{job_id}:{run_sequence}"
        requested_at = datetime.now(timezone.utc)
        job = await self.repository.mark_dispatch_requested(
            job_id, run_sequence, task_id, requested_at
        )
        try:
            await self.dispatcher.dispatch(job_id, run_sequence)
        except Exception as exc:
            await self.repository.rollback_resume(
                job_id, identity.owner_id, run_sequence
            )
            raise DispatchUnavailable from exc
        return await self.repository.mark_dispatched(
            job_id, run_sequence, task_id, datetime.now(timezone.utc)
        )

    async def cancel(self, job_id: str, identity) -> dict:
        job = await self.repository.request_cancel_owned(
            job_id, identity.owner_id
        )
        redis = self.admission.redis
        await redis.set(
            f"job:cancel:{job_id}", "1", ex=CANCEL_MIRROR_TTL_SECONDS
        )
        if JobStatus(job["status"]) is JobStatus.CANCELLED:
            await self.admission.release(job["owner_hash"], job_id)
        return job

    async def stream_events(
        self,
        job_id: str,
        identity,
        last_event_id: str | None,
        is_disconnected: Callable[[], Awaitable[bool]],
        job: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent | None]:
        if self.event_store is None:
            raise RuntimeError("job event store is not configured")

        owned_job = job or await self.get(job_id, identity)
        snapshot = StreamEvent(
            id=None,
            data=dict(self.event_store.snapshot_event(owned_job)),
        )
        yield snapshot
        if _ends_stream(snapshot) or await is_disconnected():
            return

        retained = await self.event_store.read(job_id, after_id="0-0", block_ms=1)
        retained_ids = [event.id for event in retained]
        if last_event_id is not None and last_event_id in retained_ids:
            replay_from = retained_ids.index(last_event_id) + 1
            initial_events = retained[replay_from:]
            cursor = last_event_id
        else:
            initial_events = []
            cursor = retained[-1].id if retained else "0-0"

        for stored in initial_events:
            if await is_disconnected():
                return
            event = StreamEvent(id=stored.id, data=dict(stored.data))
            cursor = stored.id
            yield event
            if _ends_stream(event):
                return

        while not await is_disconnected():
            events = await self.event_store.read(
                job_id, after_id=cursor, block_ms=15_000
            )
            if await is_disconnected():
                return
            if not events:
                yield None
                continue
            for stored in events:
                event = StreamEvent(id=stored.id, data=dict(stored.data))
                cursor = stored.id
                yield event
                if _ends_stream(event):
                    return
