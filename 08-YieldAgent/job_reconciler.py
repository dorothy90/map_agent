from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from job_models import JobStatus
from job_repository import ACTIVE_STATUSES, TransitionConflict
from observability import metrics


LOCK_KEY = "jobs:reconciler"
LOCK_SECONDS = 300
QUEUED_STALE_SECONDS = 60
WAITING_INPUT_TTL_SECONDS = 24 * 60 * 60
CANCEL_KEY_PREFIX = "job:cancel:"

_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class ReconcileResult:
    acquired: bool
    reenqueued: int = 0
    cancelled: int = 0
    cancel_keys_removed: int = 0


class JobReconciler:
    def __init__(self, repository, admission, dispatcher, event_store, redis):
        self.repository = repository
        self.admission = admission
        self.dispatcher = dispatcher
        self.event_store = event_store
        self.redis = redis

    async def acquire_lock(self, owner_token: str) -> bool:
        return bool(
            await self.redis.set(
                LOCK_KEY, owner_token, nx=True, ex=LOCK_SECONDS
            )
        )

    async def release_lock(self, owner_token: str) -> bool:
        return bool(await self.redis.eval(_COMPARE_DELETE, 1, LOCK_KEY, owner_token))

    async def run(self) -> ReconcileResult:
        owner_token = uuid.uuid4().hex
        if not await self.acquire_lock(owner_token):
            return ReconcileResult(acquired=False)
        try:
            return await self._run_locked()
        finally:
            await self.release_lock(owner_token)

    async def _run_locked(self) -> ReconcileResult:
        now = datetime.now(timezone.utc)
        expired = await self.repository.reserve_expired_running(now, now)
        stale = await self.repository.reserve_stale_queued(
            now - timedelta(seconds=QUEUED_STALE_SECONDS), now
        )

        reenqueued = 0
        seen: set[tuple[str, int]] = set()
        for job in [*expired, *stale]:
            key = (job["job_id"], job["run_sequence"])
            if key in seen:
                continue
            seen.add(key)
            try:
                await self.dispatcher.dispatch(*key)
            except Exception:
                # The reservation ages out after one minute for a later pass.
                continue
            try:
                marked = await self.repository.mark_dispatched(
                    job["job_id"],
                    job["run_sequence"],
                    f"{job['job_id']}:{job['run_sequence']}",
                    datetime.now(timezone.utc),
                )
            except TransitionConflict:
                # A fast completion/resume can advance the sequence after dispatch.
                continue
            await self.event_store.publish(
                job["job_id"],
                self.event_store.snapshot_event(marked),
            )
            metrics.retry("reconcile")
            reenqueued += 1

        cancelled = await self.repository.cancel_stale_waiting(
            now - timedelta(seconds=WAITING_INPUT_TTL_SECONDS), now
        )
        for job in cancelled:
            await self.event_store.publish(
                job["job_id"], self.event_store.snapshot_event(job)
            )
            await self.event_store.publish(job["job_id"], {"type": "stream_end"})

        counts: dict[str, set[str]] = defaultdict(set)
        cursor = self.repository.jobs.find(
            {"status": {"$in": sorted(ACTIVE_STATUSES)}},
            {"_id": 0, "job_id": 1, "owner_hash": 1},
        )
        async for job in cursor:
            counts[job["owner_hash"]].add(job["job_id"])
        await self.admission.reconcile(dict(counts))

        removed = await self._remove_terminal_cancel_keys()
        return ReconcileResult(
            acquired=True,
            reenqueued=reenqueued,
            cancelled=len(cancelled),
            cancel_keys_removed=removed,
        )

    async def _remove_terminal_cancel_keys(self) -> int:
        keys = [key async for key in self.redis.scan_iter(f"{CANCEL_KEY_PREFIX}*")]
        if not keys:
            return 0
        keys = [key.decode() if isinstance(key, bytes) else key for key in keys]
        job_ids = [key.removeprefix(CANCEL_KEY_PREFIX) for key in keys]
        terminal_ids = {
            job["job_id"]
            async for job in self.repository.jobs.find(
                {
                    "job_id": {"$in": job_ids},
                    "status": {
                        "$in": [
                            JobStatus.SUCCEEDED.value,
                            JobStatus.FAILED.value,
                            JobStatus.CANCELLED.value,
                        ]
                    },
                },
                {"_id": 0, "job_id": 1},
            )
        }
        terminal_keys = [f"{CANCEL_KEY_PREFIX}{job_id}" for job_id in terminal_ids]
        if terminal_keys:
            await self.redis.delete(*terminal_keys)
        return len(terminal_keys)
