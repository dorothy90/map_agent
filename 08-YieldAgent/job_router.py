import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from admission import GlobalLimitExceeded, UserLimitExceeded
from identity import PlatformIdentity, get_platform_identity
from job_models import JobCreate, JobCreated, JobSnapshot
from job_repository import JobNotFound, SessionBusy
from job_service import DispatchUnavailable, JobService


router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


@router.post("", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    body: JobCreate,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(get_job_service),
):
    try:
        job = await service.create(body, identity)
    except SessionBusy as exc:
        raise HTTPException(409, detail={"code": "SESSION_BUSY"}) from exc
    except UserLimitExceeded as exc:
        raise HTTPException(429, detail={"code": "USER_JOB_LIMIT"}) from exc
    except GlobalLimitExceeded as exc:
        raise HTTPException(503, detail={"code": "QUEUE_FULL"}) from exc
    except DispatchUnavailable as exc:
        raise HTTPException(503, detail={"code": "DISPATCH_UNAVAILABLE"}) from exc
    return JobCreated(**job, events_url=f"/jobs/{job['job_id']}/events")


@router.get("/{job_id}", response_model=JobSnapshot)
async def get_job(
    job_id: str,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(get_job_service),
):
    try:
        return JobSnapshot(**(await service.get(job_id, identity)))
    except JobNotFound as exc:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"}) from exc


def _format_sse(event) -> str:
    if event is None:
        return ": heartbeat\n\n"
    lines = []
    if event.id is not None:
        lines.append(f"id: {event.id}")
    lines.append(f"event: {event.data['type']}")
    payload = json.dumps(
        event.data, ensure_ascii=False, separators=(",", ":")
    )
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


@router.get("/{job_id}/events")
async def get_job_events(
    job_id: str,
    request: Request,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(get_job_service),
):
    try:
        job = await service.get(job_id, identity)
    except JobNotFound as exc:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"}) from exc

    last_event_id = request.headers.get("Last-Event-ID")

    async def body():
        async for event in service.stream_events(
            job_id,
            identity,
            last_event_id,
            request.is_disconnected,
            job=job,
        ):
            yield _format_sse(event)

    return StreamingResponse(
        body(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )
