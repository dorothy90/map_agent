"""MongoDB-backed jobs and leases for incremental Wiki synchronization."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from wiki_sync import TripleSnapshot


GLOBAL_LOCK_ID = "incremental-wiki-sync"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _job_id(snapshot: TripleSnapshot) -> str:
    value = f"{snapshot.key.canonical}|{snapshot.source_fingerprint}"
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _safe_error(error: object, limit: int = 500) -> str:
    return " ".join(str(error).split())[:limit]


class WikiJobStore:
    def __init__(
        self,
        database: Database,
        *,
        jobs_collection: str = "wiki_sync_jobs",
        locks_collection: str = "wiki_sync_locks",
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.database = database
        self.jobs = database[jobs_collection]
        self.locks = database[locks_collection]
        self._now = now

    @classmethod
    def from_env(cls) -> "WikiJobStore":
        load_dotenv(override=False)
        client = MongoClient(
            os.getenv("MONGO_URI", "mongodb://localhost:27017"),
            serverSelectionTimeoutMS=2000,
            tz_aware=True,
        )
        client.admin.command("ping")
        return cls(client[os.getenv("MONGO_DB", "yield_agent")])

    def ensure_indexes(self) -> None:
        self.jobs.create_index(
            [
                ("status", ASCENDING),
                ("next_retry_at", ASCENDING),
                ("lease_until", ASCENDING),
                ("claim_priority", ASCENDING),
                ("doc_count", DESCENDING),
                ("triple_key", ASCENDING),
            ],
            name="wiki_sync_claim",
        )
        self.locks.create_index(
            [("lease_until", ASCENDING)],
            name="wiki_sync_lock_lease",
        )

    def enqueue(
        self,
        snapshot: TripleSnapshot,
        change_type: str,
    ) -> tuple[str, bool]:
        if change_type not in {"new", "changed"}:
            raise ValueError(f"Unsupported Wiki sync job type: {change_type}")
        job_id = _job_id(snapshot)
        now = self._now()
        result = self.jobs.update_one(
            {"_id": job_id},
            {
                "$setOnInsert": {
                    "triple_key": snapshot.key.canonical,
                    "product": snapshot.key.product,
                    "fail_type": snapshot.key.fail_type,
                    "cause_oper": snapshot.key.cause_oper,
                    "source_fingerprint": snapshot.source_fingerprint,
                    "source_doc_ids": list(snapshot.source_doc_ids),
                    "doc_count": snapshot.evidence_count,
                    "change_type": change_type,
                    "status": "pending",
                    "attempts": 0,
                    "claim_priority": 1 if change_type == "changed" else 2,
                    "lease_owner": None,
                    "lease_until": None,
                    "next_retry_at": None,
                    "last_error": None,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        return job_id, result.upserted_id is not None

    def claim_next(self, owner: str, lease_seconds: int = 900) -> dict[str, Any] | None:
        now = self._now()
        lease_until = now + timedelta(seconds=lease_seconds)
        return self.jobs.find_one_and_update(
            {
                "$or": [
                    {"status": "pending"},
                    {
                        "status": "failed",
                        "next_retry_at": {"$lte": now},
                    },
                    {
                        "status": "running",
                        "lease_until": {"$lte": now},
                    },
                ]
            },
            {
                "$set": {
                    "status": "running",
                    "claim_priority": 0,
                    "lease_owner": owner,
                    "lease_until": lease_until,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[
                ("claim_priority", ASCENDING),
                ("doc_count", DESCENDING),
                ("triple_key", ASCENDING),
            ],
            return_document=ReturnDocument.AFTER,
        )

    def mark_succeeded(
        self,
        job_id: str,
        owner: str,
        *,
        concept_id: str,
        concept_version: int,
    ) -> bool:
        now = self._now()
        result = self.jobs.update_one(
            {"_id": job_id, "status": "running", "lease_owner": owner},
            {
                "$set": {
                    "status": "succeeded",
                    "concept_id": concept_id,
                    "concept_version": concept_version,
                    "lease_owner": None,
                    "lease_until": None,
                    "next_retry_at": None,
                    "last_error": None,
                    "updated_at": now,
                    "succeeded_at": now,
                }
            },
        )
        return result.modified_count == 1

    def mark_failed(
        self,
        job_id: str,
        owner: str,
        error: object,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: int = 60,
    ) -> bool:
        job = self.jobs.find_one(
            {"_id": job_id, "status": "running", "lease_owner": owner},
            {"attempts": 1},
        )
        if job is None:
            return False
        terminal = int(job.get("attempts", 0)) >= max_attempts
        now = self._now()
        result = self.jobs.update_one(
            {
                "_id": job_id,
                "status": "running",
                "lease_owner": owner,
                "attempts": job.get("attempts", 0),
            },
            {
                "$set": {
                    "status": "terminal_failed" if terminal else "failed",
                    "claim_priority": 0,
                    "lease_owner": None,
                    "lease_until": None,
                    "next_retry_at": None
                    if terminal
                    else now + timedelta(seconds=retry_delay_seconds),
                    "last_error": _safe_error(error),
                    "updated_at": now,
                }
            },
        )
        return result.modified_count == 1

    def acquire_global_lock(self, owner: str, lease_seconds: int = 900) -> bool:
        now = self._now()
        try:
            result = self.locks.update_one(
                {
                    "_id": GLOBAL_LOCK_ID,
                    "$or": [
                        {"lease_until": {"$lte": now}},
                        {"owner": owner},
                    ],
                },
                {
                    "$set": {
                        "owner": owner,
                        "lease_until": now + timedelta(seconds=lease_seconds),
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            return False
        return result.matched_count == 1 or result.upserted_id is not None

    def renew_global_lock(self, owner: str, lease_seconds: int = 900) -> bool:
        now = self._now()
        result = self.locks.update_one(
            {
                "_id": GLOBAL_LOCK_ID,
                "owner": owner,
                "lease_until": {"$gt": now},
            },
            {
                "$set": {
                    "lease_until": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                }
            },
        )
        return result.modified_count == 1

    def release_global_lock(self, owner: str) -> bool:
        return self.locks.delete_one(
            {"_id": GLOBAL_LOCK_ID, "owner": owner}
        ).deleted_count == 1
