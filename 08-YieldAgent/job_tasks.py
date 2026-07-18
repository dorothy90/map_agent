from __future__ import annotations

import asyncio
import os
import socket

from celery.exceptions import MaxRetriesExceededError
from celery.signals import worker_process_shutdown
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from admission import AdmissionController
from celery_app import celery_app
from celery_dispatcher import CeleryJobDispatcher
from job_events import JobEventStore
from job_reconciler import JobReconciler
from job_repository import JobRepository
from worker_runtime import WorkerRuntime


runtime = WorkerRuntime()


async def _execute_job(
    job_id: str, run_sequence: int, task_id: str, worker_id: str
):
    return await runtime.execute_job(job_id, run_sequence, task_id, worker_id)


@celery_app.task(bind=True, name="yield_agent.run_job")
def run_job(self, job_id: str, run_sequence: int) -> dict[str, str]:
    task_id = self.request.id or f"{job_id}:{run_sequence}"
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    result = asyncio.run(
        _execute_job(job_id, run_sequence, task_id, worker_id)
    )
    if result.retry_after is not None:
        max_retries = (
            max(3, self.request.retries + 1)
            if result.retry_kind == "failure"
            else 3
        )
        try:
            raise self.retry(countdown=result.retry_after, max_retries=max_retries)
        except MaxRetriesExceededError:
            return result.as_dict()
    return result.as_dict()


async def _reconcile_jobs():
    settings = runtime.settings
    mongo = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        reconciler = JobReconciler(
            JobRepository(mongo[settings.mongo_db]),
            AdmissionController(
                redis, settings.user_job_limit, settings.global_job_limit
            ),
            CeleryJobDispatcher(celery_app),
            JobEventStore(redis, ttl_seconds=86_400, max_events=2_000),
            redis,
        )
        return await reconciler.run()
    finally:
        await redis.aclose()
        mongo.close()


@celery_app.task(name="yield_agent.reconcile_jobs", ignore_result=True)
def reconcile_jobs() -> None:
    asyncio.run(_reconcile_jobs())


@worker_process_shutdown.connect
def close_worker_runtime(**_kwargs) -> None:
    runtime.close()
