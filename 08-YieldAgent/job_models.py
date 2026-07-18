from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
ALLOWED = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.QUEUED,
        JobStatus.WAITING_INPUT,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.WAITING_INPUT: {JobStatus.QUEUED, JobStatus.CANCELLED},
}


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if current in TERMINAL:
        raise ValueError(f"terminal job cannot transition from {current}")
    if target not in ALLOWED[current]:
        raise ValueError(f"invalid job transition: {current} -> {target}")


class JobCreate(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    session_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("query", "session_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ResumeRequest(BaseModel):
    value: str | dict[str, Any]

    @field_validator("value")
    @classmethod
    def reject_empty_value(cls, value):
        if value == "" or value == {}:
            raise ValueError("resume value must not be empty")
        return value


class JobError(BaseModel):
    category: str
    message: str


class PublicArtifact(BaseModel):
    artifact_id: str
    artifact_type: str
    mime: str
    title: str
    agent: str = ""
    url: str


class JobSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    session_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: str = ""
    latest_interrupt: dict[str, Any] | None = None
    error: JobError | None = None
    artifacts: list[PublicArtifact] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def hide_artifact_storage_metadata(cls, value):
        if not isinstance(value, dict):
            return value
        public = dict(value)
        job_id = public.get("job_id", "")
        public["artifacts"] = [
            {
                "artifact_id": artifact["artifact_id"],
                "artifact_type": artifact["artifact_type"],
                "mime": artifact["mime"],
                "title": artifact["title"],
                "agent": artifact.get("agent", ""),
                "url": f"/jobs/{job_id}/artifacts/{artifact['artifact_id']}",
            }
            for artifact in public.get("artifacts", [])
        ]
        return public


class JobCreated(JobSnapshot):
    events_url: str
