from __future__ import annotations

import argparse
import asyncio
import inspect
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from motor.motor_asyncio import AsyncIOMotorClient

from settings import get_settings


TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED")


class UnsafeArtifactPath(ValueError):
    pass


def _safe_component(value: object) -> str:
    if not isinstance(value, str):
        raise UnsafeArtifactPath("unsafe artifact path")
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\0" in value:
        raise UnsafeArtifactPath("unsafe artifact path")
    return value


class ArtifactCleanup:
    def __init__(
        self,
        jobs,
        artifact_root: str | Path,
        delete_checkpoint: Callable[[str], object],
    ):
        root = Path(artifact_root)
        if not root.is_absolute():
            raise ValueError("ARTIFACT_ROOT must be absolute")
        self.jobs = jobs
        self.root = root.resolve()
        self.delete_checkpoint = delete_checkpoint

    async def run(
        self,
        *,
        cutoff: datetime,
        completed_cutoff: datetime | None = None,
    ) -> dict[str, int]:
        query: dict = {
            "status": {"$in": list(TERMINAL_STATUSES)},
            "artifact_expires_at": {"$lte": cutoff},
            "cleanup_at": {"$exists": False},
        }
        if completed_cutoff is not None:
            query["updated_at"] = {"$lte": completed_cutoff}
        cursor = self.jobs.find(
            query,
            {"_id": 0, "job_id": 1, "owner_hash": 1, "thread_id": 1},
        )
        cleaned = failed = 0
        async for job in cursor:
            try:
                await self._clean_one(job)
            except Exception as exc:
                failed += 1
                message = (
                    "unsafe artifact path"
                    if isinstance(exc, UnsafeArtifactPath)
                    else "cleanup failed"
                )
                await self.jobs.update_one(
                    {"job_id": job.get("job_id")},
                    {"$set": {"cleanup_error": message}},
                )
            else:
                cleaned += 1
                await self.jobs.update_one(
                    {"job_id": job["job_id"]},
                    {
                        "$set": {"cleanup_at": datetime.now(timezone.utc)},
                        "$unset": {"cleanup_error": ""},
                    },
                )
        return {"cleaned": cleaned, "failed": failed}

    async def _clean_one(self, job: dict) -> None:
        owner_hash = _safe_component(job.get("owner_hash"))
        job_id = _safe_component(job.get("job_id"))
        thread_id = job.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise UnsafeArtifactPath("unsafe artifact path")

        candidate = self.root / "jobs" / owner_hash / job_id
        resolved = candidate.resolve(strict=False)
        expected = self.root / "jobs" / owner_hash / job_id
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise UnsafeArtifactPath("unsafe artifact path") from exc
        if resolved != expected:
            raise UnsafeArtifactPath("unsafe artifact path")

        if candidate.is_symlink():
            raise UnsafeArtifactPath("unsafe artifact path")
        if candidate.exists():
            await asyncio.to_thread(shutil.rmtree, candidate)

        result = self.delete_checkpoint(thread_id)
        if inspect.isawaitable(result):
            await result


async def _run_cli(older_than_days: int) -> dict[str, int]:
    settings = get_settings()
    if not settings.artifact_root.is_absolute():
        raise ValueError("ARTIFACT_ROOT must be absolute")

    from langgraph.checkpoint.mongodb import MongoDBSaver

    motor = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    now = datetime.now(timezone.utc)
    try:
        with MongoDBSaver.from_conn_string(
            settings.mongo_uri, db_name=settings.mongo_db
        ) as saver:
            return await ArtifactCleanup(
                motor[settings.mongo_db].jobs,
                settings.artifact_root,
                saver.delete_thread,
            ).run(
                cutoff=now,
                completed_cutoff=now - timedelta(days=older_than_days),
            )
    finally:
        motor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete expired job artifacts")
    parser.add_argument("--older-than-days", type=int, default=30)
    args = parser.parse_args()
    if args.older_than_days < 0:
        parser.error("--older-than-days must be non-negative")
    result = asyncio.run(_run_cli(args.older_than_days))
    print(f"cleaned={result['cleaned']} failed={result['failed']}")


if __name__ == "__main__":
    main()
