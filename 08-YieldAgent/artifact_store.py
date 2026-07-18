from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str
    relative_path: str
    artifact_type: str
    mime: str
    title: str
    agent: str = ""
    size: int = Field(ge=0)
    checksum: str


def _validate_component(value: str, name: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\0" in value:
        raise ValueError(f"{name} must be a safe path component")
    return value


class ArtifactStore:
    def __init__(self, root: str | Path, *, owner_hash: str, job_id: str):
        self.root = Path(root)
        self.owner_hash = _validate_component(owner_hash, "owner_hash")
        self.job_id = _validate_component(job_id, "job_id")
        self.job_root = self.root / "jobs" / self.owner_hash / self.job_id
        self.input_dir = self.job_root / "input"
        self.output_dir = self.job_root / "output"
        self.temp_dir = self.job_root / "temp"
        for directory in (self.input_dir, self.output_dir, self.temp_dir):
            directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            directory.chmod(0o750)

    def write_bytes(
        self,
        content: bytes,
        *,
        mime: str,
        title: str,
        agent: str = "",
        artifact_type: str = "html",
    ) -> ArtifactRef:
        artifact_id = str(uuid.uuid4())
        temp_path = self.temp_dir / str(uuid.uuid4())
        final_path = self.output_dir / artifact_id
        try:
            with temp_path.open("xb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temp_path, final_path)
        finally:
            temp_path.unlink(missing_ok=True)

        relative_path = final_path.relative_to(self.root).as_posix()
        return ArtifactRef(
            artifact_id=artifact_id,
            relative_path=relative_path,
            artifact_type=artifact_type,
            mime=mime,
            title=title,
            agent=agent,
            size=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )

    def write_text(
        self,
        content: str,
        *,
        mime: str,
        title: str,
        agent: str = "",
        artifact_type: str = "html",
    ) -> ArtifactRef:
        return self.write_bytes(
            content.encode("utf-8"),
            mime=mime,
            title=title,
            agent=agent,
            artifact_type=artifact_type,
        )

    def open(self, ref: ArtifactRef) -> BinaryIO:
        return self._resolve_ref(ref).open("rb")

    def artifact_url(self, ref: ArtifactRef) -> str:
        self._resolve_ref(ref)
        return f"/jobs/{self.job_id}/artifacts/{ref.artifact_id}"

    def _resolve_ref(self, ref: ArtifactRef) -> Path:
        artifact_id = _validate_component(ref.artifact_id, "artifact_id")
        expected_relative = (
            Path("jobs") / self.owner_hash / self.job_id / "output" / artifact_id
        ).as_posix()
        if ref.relative_path != expected_relative:
            raise ValueError("invalid artifact path")

        output_root = self.output_dir.resolve()
        candidate = (self.root / ref.relative_path).resolve()
        try:
            candidate.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("invalid artifact path") from exc
        return candidate
