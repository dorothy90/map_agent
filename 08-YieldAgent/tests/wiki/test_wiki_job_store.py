from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from dotenv import load_dotenv
from pymongo import MongoClient

from wiki_job_store import WikiJobStore
from wiki_sync import build_triple_snapshot, make_triple_key


pytestmark = pytest.mark.no_server


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _snapshot(
    product="4SS",
    fail_type="EASY(W)",
    cause_oper="PRE METAL CLN",
    doc_count=1,
    content="source",
):
    key = make_triple_key(product, fail_type, cause_oper)
    documents = [
        {
            "doc_id": f"FH-{index}",
            "content": f"{content}-{index}",
            "product": product,
            "fail_type": fail_type,
            "cause_oper": cause_oper,
        }
        for index in range(doc_count)
    ]
    return build_triple_snapshot(key, documents)


@pytest.fixture
def mongo_store():
    load_dotenv(override=False)
    client = MongoClient(
        os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=2000,
        tz_aware=True,
    )
    client.admin.command("ping")
    suffix = uuid.uuid4().hex
    database = client[os.getenv("MONGO_DB", "yield_agent")]
    clock = MutableClock()
    store = WikiJobStore(
        database,
        jobs_collection=f"wiki_sync_jobs_test_{suffix}",
        locks_collection=f"wiki_sync_locks_test_{suffix}",
        now=clock,
    )
    store.ensure_indexes()
    try:
        yield store, clock
    finally:
        database.drop_collection(store.jobs.name)
        database.drop_collection(store.locks.name)
        client.close()


def test_enqueue_is_deterministic_and_does_not_reset_existing_job(mongo_store):
    store, _ = mongo_store
    snapshot = _snapshot()

    first_id, first_created = store.enqueue(snapshot, "new")
    second_id, second_created = store.enqueue(snapshot, "new")

    assert first_id == second_id
    assert first_created is True
    assert second_created is False
    assert store.jobs.count_documents({}) == 1
    job = store.jobs.find_one({"_id": first_id})
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    assert job["source_doc_ids"] == ["FH-0"]


def test_claim_prioritizes_changed_then_new_by_document_count_and_key(mongo_store):
    store, _ = mongo_store
    new_large = _snapshot(product="NEW", doc_count=5)
    changed_small = _snapshot(product="CHANGED", doc_count=1)
    new_small_b = _snapshot(product="B", doc_count=1)
    new_small_a = _snapshot(product="A", doc_count=1)
    for snapshot, change_type in (
        (new_large, "new"),
        (changed_small, "changed"),
        (new_small_b, "new"),
        (new_small_a, "new"),
    ):
        store.enqueue(snapshot, change_type)

    claimed = [store.claim_next("worker", lease_seconds=30) for _ in range(4)]

    assert [job["triple_key"] for job in claimed] == [
        changed_small.key.canonical,
        new_large.key.canonical,
        new_small_a.key.canonical,
        new_small_b.key.canonical,
    ]
    assert all(job["status"] == "running" for job in claimed)
    assert all(job["attempts"] == 1 for job in claimed)


def test_active_lease_excludes_other_worker_and_expired_lease_is_reclaimed(mongo_store):
    store, clock = mongo_store
    store.enqueue(_snapshot(), "new")

    first = store.claim_next("worker-1", lease_seconds=30)
    assert first is not None
    assert store.claim_next("worker-2", lease_seconds=30) is None

    clock.advance(31)
    reclaimed = store.claim_next("worker-2", lease_seconds=30)

    assert reclaimed["_id"] == first["_id"]
    assert reclaimed["lease_owner"] == "worker-2"
    assert reclaimed["attempts"] == 2


def test_retry_is_claimed_before_pending_and_third_failure_is_terminal(mongo_store):
    store, clock = mongo_store
    retry_snapshot = _snapshot(product="RETRY")
    pending_snapshot = _snapshot(product="PENDING")
    retry_id, _ = store.enqueue(retry_snapshot, "new")
    store.enqueue(pending_snapshot, "changed")

    first = store.claim_next("worker", lease_seconds=30)
    assert first["_id"] != retry_id
    store.mark_succeeded(
        first["_id"], "worker", concept_id="concept:pending", concept_version=1
    )

    retry_job = store.claim_next("worker", lease_seconds=30)
    assert retry_job["_id"] == retry_id
    store.mark_failed(retry_id, "worker", "temporary\nsecret details", retry_delay_seconds=10)
    assert store.claim_next("worker", lease_seconds=30) is None

    clock.advance(11)
    second = store.claim_next("worker", lease_seconds=30)
    assert second["_id"] == retry_id
    store.mark_failed(retry_id, "worker", "temporary", retry_delay_seconds=10)
    clock.advance(11)
    third = store.claim_next("worker", lease_seconds=30)
    store.mark_failed(retry_id, "worker", "final")

    saved = store.jobs.find_one({"_id": retry_id})
    assert third["attempts"] == 3
    assert saved["status"] == "terminal_failed"
    assert saved["next_retry_at"] is None
    assert saved["last_error"] == "final"
    assert store.claim_next("worker", lease_seconds=30) is None


def test_retryable_failed_job_has_priority_over_new_pending_job(mongo_store):
    store, clock = mongo_store
    failed_id, _ = store.enqueue(_snapshot(product="FAILED"), "new")
    claimed = store.claim_next("worker", lease_seconds=30)
    assert claimed["_id"] == failed_id
    store.mark_failed(failed_id, "worker", "retry", retry_delay_seconds=10)
    store.enqueue(_snapshot(product="PENDING"), "changed")
    clock.advance(11)

    assert store.claim_next("worker", lease_seconds=30)["_id"] == failed_id


def test_expired_third_crash_becomes_terminal_without_a_fourth_claim(mongo_store):
    store, clock = mongo_store
    job_id, _ = store.enqueue(_snapshot(product="CRASH"), "new")

    for attempt in range(1, 4):
        claimed = store.claim_next(f"worker-{attempt}", lease_seconds=30)
        assert claimed["attempts"] == attempt
        clock.advance(31)

    assert store.claim_next("worker-4", lease_seconds=30) is None
    saved = store.jobs.find_one({"_id": job_id})
    assert saved["status"] == "terminal_failed"
    assert saved["lease_owner"] is None
    assert saved["lease_until"] is None
    assert saved["last_error"] == "lease expired after maximum attempts"


def test_global_lock_is_owned_renewed_and_reclaimed_after_expiry(mongo_store):
    store, clock = mongo_store

    assert store.acquire_global_lock("worker-1", lease_seconds=30) is True
    assert store.acquire_global_lock("worker-2", lease_seconds=30) is False
    assert store.renew_global_lock("worker-2", lease_seconds=30) is False
    assert store.release_global_lock("worker-2") is False
    assert store.renew_global_lock("worker-1", lease_seconds=60) is True

    clock.advance(61)
    assert store.acquire_global_lock("worker-2", lease_seconds=30) is True
    assert store.release_global_lock("worker-1") is False
    assert store.release_global_lock("worker-2") is True
