from __future__ import annotations

import asyncio
import inspect
import math
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController
from artifact_context import artifact_scope
from artifact_store import ArtifactRef, ArtifactStore
from graph_job_runner import GraphRunRequest, GraphRunResult, run_graph
from job_events import JobEventStore
from job_failures import classify_failure
from job_models import JobStatus, is_terminal
from job_repository import JobRepository
from settings import Settings, get_settings
from observability import log_worker_event, metrics


LEASE_RENEW_SECONDS = 20
EVENT_TTL_SECONDS = 86_400
MAX_EVENTS = 2_000
CANCEL_KEY_PREFIX = "job:cancel:"
MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_SECONDS = (5, 20)

_test_graph_runner = None
if get_settings().environment == "test":
    from test_runtime import run_test_graph as _test_graph_runner

GraphRunner = Callable[..., Awaitable[GraphRunResult]]


class CooperativeCancellation(Exception):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    retry_after: int | None = None
    retry_kind: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status}


class WorkerRuntime:
    """Process-local graph plus delivery-local async infrastructure clients."""

    def __init__(
        self,
        settings: Settings | None = None,
        graph: Any | None = None,
        graph_runner: GraphRunner = run_graph,
    ):
        self.settings = settings or get_settings()
        self._graph = graph
        self._graph_runner = graph_runner
        self._saver_context = None

    def graph(self):
        if self._graph is None:
            from langgraph.checkpoint.mongodb import MongoDBSaver
            from supervisor import workflow

            self._saver_context = MongoDBSaver.from_conn_string(
                self.settings.mongo_uri,
                db_name=self.settings.mongo_db,
            )
            saver = self._saver_context.__enter__()
            self._graph = workflow.compile(checkpointer=saver)
        return self._graph

    def close(self) -> None:
        if self._saver_context is not None:
            self._saver_context.__exit__(None, None, None)
            self._saver_context = None
            self._graph = None
        from common import close_oracle_pool

        close_oracle_pool()

    async def execute_job(
        self,
        job_id: str,
        run_sequence: int,
        task_id: str,
        worker_id: str,
    ) -> ExecutionResult:
        if (
            not isinstance(run_sequence, int)
            or isinstance(run_sequence, bool)
            or run_sequence < 0
        ):
            return ExecutionResult(status="IGNORED")

        mongo = AsyncIOMotorClient(self.settings.mongo_uri, tz_aware=True)
        redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        repository = JobRepository(mongo[self.settings.mongo_db])
        event_store = JobEventStore(redis, EVENT_TTL_SECONDS, MAX_EVENTS)
        admission = AdmissionController(
            redis,
            self.settings.user_job_limit,
            self.settings.global_job_limit,
        )
        try:
            claimed = await self._claim(
                repository, job_id, run_sequence, task_id, worker_id
            )
            if isinstance(claimed, ExecutionResult):
                return claimed

            started = time.monotonic()
            created_at = claimed.get("created_at")
            if isinstance(created_at, datetime):
                metrics.queue_wait.observe(
                    max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
                )
            log_worker_event(
                "job_started",
                job_id=job_id,
                task_id=task_id,
                run_sequence=run_sequence,
                attempt=claimed.get("attempt", 0),
                node="graph",
                owner_hash=claimed.get("owner_hash", ""),
            )

            lease_task = asyncio.create_task(
                self._renew_lease(repository, job_id, task_id, worker_id)
            )
            try:
                request = GraphRunRequest(
                    job_id=job_id,
                    owner_id=claimed["owner_id"],
                    session_id=claimed["session_id"],
                    thread_id=claimed["thread_id"],
                    query=claimed["query"],
                    resume_value=claimed.get("resume_value"),
                )

                async def cancelled() -> bool:
                    if await redis.exists(f"{CANCEL_KEY_PREFIX}{job_id}"):
                        return True
                    current = await repository.get(job_id)
                    return (
                        current is None
                        or bool(current.get("cancel_requested"))
                        or current.get("status") == JobStatus.CANCELLED.value
                    )

                async def emit(event: dict[str, Any]) -> None:
                    if await cancelled():
                        raise CooperativeCancellation
                    await event_store.publish(job_id, event)
                    if await cancelled():
                        raise CooperativeCancellation

                async def persist_artifacts(
                    refs: list[ArtifactRef],
                ) -> list[ArtifactRef]:
                    stored = await repository.persist_artifacts_claimed(
                        job_id,
                        run_sequence,
                        task_id,
                        worker_id,
                        [ref.model_dump() for ref in refs],
                    )
                    return [ArtifactRef.model_validate(item) for item in stored]

                try:
                    execution_control = claimed.get("execution_control")
                    store = ArtifactStore(
                        self.settings.artifact_root,
                        owner_hash=claimed["owner_hash"],
                        job_id=job_id,
                    )
                    with artifact_scope(store):
                        if self.settings.environment == "test" and execution_control:
                            result = await _test_graph_runner(
                                execution_control, request, emit, cancelled
                            )
                        else:
                            runner_parameters = inspect.signature(
                                self._graph_runner
                            ).parameters
                            if "persist_artifacts" in runner_parameters:
                                result = await self._graph_runner(
                                    self.graph(),
                                    request,
                                    emit,
                                    cancelled,
                                    persist_artifacts=persist_artifacts,
                                )
                            else:
                                result = await self._graph_runner(
                                    self.graph(), request, emit, cancelled
                                )
                except CooperativeCancellation:
                    stored = await repository.complete_claimed(
                        job_id,
                        run_sequence,
                        task_id,
                        worker_id,
                        JobStatus.CANCELLED,
                    )
                except Exception as exc:
                    decision = classify_failure(exc)
                    attempt = int(claimed["attempt"])
                    if decision.retry and attempt <= MAX_TRANSIENT_RETRIES:
                        retry_after = RETRY_BACKOFF_SECONDS[attempt - 1]
                        stored = await repository.retry_claimed(
                            job_id,
                            run_sequence,
                            task_id,
                            worker_id,
                            retry_after,
                        )
                        await event_store.publish(
                            job_id,
                            {
                                "type": "status",
                                "status": JobStatus.QUEUED.value,
                                "message": "temporary failure; retry scheduled",
                                "attempt": attempt,
                                "retry_after_seconds": retry_after,
                            },
                        )
                        await event_store.publish(
                            job_id, event_store.snapshot_event(stored)
                        )
                        metrics.retry("failure")
                        metrics.observe_duration(
                            JobStatus.QUEUED.value, time.monotonic() - started
                        )
                        return ExecutionResult(
                            status=JobStatus.QUEUED.value,
                            retry_after=retry_after,
                            retry_kind="failure",
                        )
                    stored = await repository.complete_claimed(
                        job_id,
                        run_sequence,
                        task_id,
                        worker_id,
                        JobStatus.FAILED,
                        {
                            "error": {
                                "category": decision.category,
                                "message": decision.message,
                            }
                        },
                    )
                    await event_store.publish(
                        job_id,
                        {
                            "type": "error",
                            "category": decision.category,
                            "message": decision.message,
                        },
                    )
                else:
                    target = (
                        JobStatus.CANCELLED
                        if await cancelled()
                        else JobStatus(result.outcome)
                    )
                    updates: dict[str, Any] = {
                        "latest_interrupt": result.latest_interrupt
                    }
                    if result.final_result is not None:
                        updates["result"] = result.final_result
                    stored = await repository.complete_claimed(
                        job_id,
                        run_sequence,
                        task_id,
                        worker_id,
                        target,
                        updates,
                    )

                status = JobStatus(stored["status"])
                if is_terminal(status):
                    await admission.release(stored["owner_hash"], job_id)
                await event_store.publish(job_id, event_store.snapshot_event(stored))
                if status is JobStatus.FAILED:
                    await event_store.publish(job_id, {"type": "stream_end"})
                metrics.observe_duration(status.value, time.monotonic() - started)
                log_worker_event(
                    "job_finished",
                    job_id=job_id,
                    task_id=task_id,
                    run_sequence=run_sequence,
                    attempt=claimed.get("attempt", 0),
                    node="graph",
                    owner_hash=claimed.get("owner_hash", ""),
                )
                return ExecutionResult(status=status.value)
            finally:
                lease_task.cancel()
                with suppress(asyncio.CancelledError):
                    await lease_task
        finally:
            await redis.aclose()
            mongo.close()

    async def _claim(
        self,
        repository: JobRepository,
        job_id: str,
        run_sequence: int,
        task_id: str,
        worker_id: str,
    ) -> dict | ExecutionResult:
        job = await repository.get(job_id)
        if job is None or job.get("run_sequence") != run_sequence:
            return ExecutionResult(status="IGNORED")

        status = JobStatus(job["status"])
        if status is JobStatus.QUEUED:
            claimed = await repository.claim(
                job_id,
                task_id,
                worker_id,
                self.settings.worker_lease_seconds,
                run_sequence=run_sequence,
            )
        elif status is JobStatus.RUNNING:
            lease_expires_at = job.get("lease_expires_at")
            now = datetime.now(timezone.utc)
            if lease_expires_at is not None and lease_expires_at > now:
                remaining = max(
                    1, math.ceil((lease_expires_at - now).total_seconds())
                )
                return ExecutionResult(
                    status=JobStatus.RUNNING.value, retry_after=remaining
                )
            claimed = await repository.reclaim_expired(
                job_id,
                task_id,
                worker_id,
                self.settings.worker_lease_seconds,
                run_sequence=run_sequence,
            )
        else:
            return ExecutionResult(status="IGNORED")

        if claimed is not None:
            return claimed
        return await self._claim(repository, job_id, run_sequence, task_id, worker_id)

    async def _renew_lease(
        self,
        repository: JobRepository,
        job_id: str,
        task_id: str,
        worker_id: str,
    ) -> None:
        while True:
            await asyncio.sleep(
                min(LEASE_RENEW_SECONDS, self.settings.worker_lease_seconds / 3)
            )
            renewed = await repository.renew_lease(
                job_id, task_id, worker_id, self.settings.worker_lease_seconds
            )
            if renewed is None:
                metrics.lease("lost")
                return
            metrics.lease("renewed")
