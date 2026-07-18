"""Explicit process-test controls. Imported only when ENVIRONMENT=test."""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from graph_job_runner import GraphRunRequest, GraphRunResult
from identity import PlatformIdentity, get_platform_identity
from job_models import JobCreate, JobCreated
from job_service import JobService
from settings import get_settings


TestControl = Literal["succeed", "block_once"]


class TestJobCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    control: TestControl


router = APIRouter(prefix="/__test", include_in_schema=False)


class TestJobDispatcher:
    """Give the SSE client time to observe the durable QUEUED snapshot."""

    def __init__(self, celery):
        self.celery = celery

    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        task_id = f"{job_id}:{run_sequence}"
        await asyncio.to_thread(
            self.celery.send_task,
            "yield_agent.run_job",
            args=[job_id, run_sequence],
            task_id=task_id,
            queue="analysis",
            countdown=1,
        )
        return task_id


def _service(request: Request) -> JobService:
    return request.app.state.job_service


@router.post("/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_test_job(
    body: TestJobCreate,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(_service),
):
    job = await service.create(
        JobCreate(query="process integration control", session_id=body.session_id),
        identity,
        execution_control=body.control,
    )
    return JobCreated(**job, events_url=f"/jobs/{job['job_id']}/events")


@router.get("/process")
async def process_identity():
    return {"process": f"{socket.gethostname()}:{os.getpid()}"}


async def run_test_graph(
    control: TestControl,
    request: GraphRunRequest,
    emit,
    cancelled,
) -> GraphRunResult:
    settings = get_settings()
    if settings.environment != "test":
        raise RuntimeError("test graph is disabled")

    process = f"{socket.gethostname()}:{os.getpid()}"
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        if control == "succeed":
            await asyncio.sleep(1)
        elif await redis.set(
            f"job:test:block-seen:{request.job_id}", "1", nx=True, ex=300
        ):
            await emit(
                {
                    "type": "status",
                    "status": "RUNNING",
                    "execution_process": process,
                }
            )
            while not await cancelled():
                await asyncio.sleep(0.25)
            return GraphRunResult(outcome="CANCELLED")

        await emit(
            {
                "type": "status",
                "status": "RUNNING",
                "execution_process": process,
            }
        )
        await asyncio.sleep(0.5)
        return GraphRunResult(
            outcome="SUCCEEDED",
            final_result={"execution_process": process},
        )
    finally:
        await redis.aclose()
