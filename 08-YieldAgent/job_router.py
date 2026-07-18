import hashlib
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError

from admission import GlobalLimitExceeded, UserLimitExceeded
from artifact_store import ArtifactRef, ArtifactStore
from identity import PlatformIdentity, get_platform_identity
from job_models import JobCreate, JobCreated, JobSnapshot, ResumeRequest
from job_repository import JobNotFound, JobRepository, SessionBusy, TransitionConflict
from job_service import DispatchUnavailable, JobService
from settings import get_settings


router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


def get_job_repository(request: Request) -> JobRepository:
    return request.app.state.job_repository


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


def _artifact_not_found() -> HTTPException:
    return HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})


def _download_filename(ref: ArtifactRef) -> str:
    stem = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in ref.title
    ).strip("._")[:80]
    extension = {
        "html": ".html",
        "image": ".png",
        "markdown": ".md",
        "pptx": ".pptx",
    }.get(ref.artifact_type, "")
    return f"{stem or 'artifact'}{extension}"


@router.get("/{job_id}/artifacts/{artifact_id}")
async def get_artifact(
    job_id: str,
    artifact_id: str,
    identity: PlatformIdentity = Depends(get_platform_identity),
    repository: JobRepository = Depends(get_job_repository),
):
    try:
        job = await repository.get_owned(job_id, identity.owner_id)
    except JobNotFound as exc:
        raise _artifact_not_found() from exc

    metadata = next(
        (
            artifact
            for artifact in job.get("artifacts", [])
            if artifact.get("artifact_id") == artifact_id
        ),
        None,
    )
    if metadata is None:
        raise _artifact_not_found()

    try:
        ref = ArtifactRef.model_validate(metadata)
        if ref.artifact_id != artifact_id:
            raise ValueError("artifact id mismatch")
        store = ArtifactStore(
            get_settings().artifact_root,
            owner_hash=job["owner_hash"],
            job_id=job_id,
        )
        with store.open(ref) as artifact:
            path = Path(artifact.name)
            digest = hashlib.sha256()
            actual_size = 0
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                actual_size += len(chunk)
                digest.update(chunk)
    except (KeyError, OSError, ValidationError, ValueError) as exc:
        raise _artifact_not_found() from exc

    if actual_size != ref.size or not hmac.compare_digest(
        digest.hexdigest(), ref.checksum
    ):
        raise _artifact_not_found()

    return FileResponse(
        path,
        media_type=ref.mime,
        filename=_download_filename(ref),
        content_disposition_type=(
            "attachment" if ref.artifact_type == "pptx" else "inline"
        ),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/{job_id}/resume",
    response_model=JobSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_job(
    job_id: str,
    body: ResumeRequest,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(get_job_service),
):
    try:
        return JobSnapshot(**(await service.resume(job_id, body, identity)))
    except JobNotFound as exc:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"}) from exc
    except TransitionConflict as exc:
        raise HTTPException(409, detail={"code": "JOB_NOT_WAITING"}) from exc
    except DispatchUnavailable as exc:
        raise HTTPException(503, detail={"code": "DISPATCH_UNAVAILABLE"}) from exc


@router.post("/{job_id}/cancel", response_model=JobSnapshot)
async def cancel_job(
    job_id: str,
    identity: PlatformIdentity = Depends(get_platform_identity),
    service: JobService = Depends(get_job_service),
):
    try:
        return JobSnapshot(**(await service.cancel(job_id, identity)))
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
