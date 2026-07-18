from __future__ import annotations

import asyncio
import os
import socket

from celery.exceptions import MaxRetriesExceededError
from celery.signals import worker_process_shutdown

from celery_app import celery_app
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
        try:
            raise self.retry(countdown=result.retry_after, max_retries=3)
        except MaxRetriesExceededError:
            return result.as_dict()
    return result.as_dict()


@worker_process_shutdown.connect
def close_worker_runtime(**_kwargs) -> None:
    runtime.close()
