from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import pytest

from wiki_graph_models import EntityCandidate, RelationCandidate
from wiki_manifest import empty_manifest, load_manifest, record_success, save_manifest
from wiki_sync import (
    WikiSyncService,
    build_triple_snapshot,
    make_triple_key,
)


pytestmark = pytest.mark.no_server


def _doc(doc_id="FH-1", content="source"):
    return {
        "doc_id": doc_id,
        "content": content,
        "cause": "oxide damage",
        "action": "clean chamber",
        "comment": "confirmed",
        "date": "2026-08-01",
        "source_file": f"{doc_id}.pptx",
        "product": "4SS",
        "fail_type": "EASY(W)",
        "cause_oper": "PRE METAL CLN",
    }


def _snapshot(*documents):
    return build_triple_snapshot(
        make_triple_key("4SS", "EASY(W)", "PRE METAL CLN"),
        list(documents or (_doc(),)),
    )


class FakeScanner:
    def __init__(self, snapshots, fetched=None):
        self.snapshots = snapshots
        self.fetched = fetched or snapshots

    def scan(self):
        return self.snapshots

    def fetch_snapshot(
        self, product, fail_type, cause_oper, *, raw_fail_types=None
    ):
        key = make_triple_key(product, fail_type, cause_oper).canonical
        return self.fetched[key]


class InMemoryJobStore:
    def __init__(self):
        self.jobs = {}
        self.lock_owner = None
        self.enqueue_calls = 0

    def acquire_global_lock(self, owner, lease_seconds=900):
        if self.lock_owner not in (None, owner):
            return False
        self.lock_owner = owner
        return True

    def renew_global_lock(self, owner, lease_seconds=900):
        return self.lock_owner == owner

    def release_global_lock(self, owner):
        if self.lock_owner != owner:
            return False
        self.lock_owner = None
        return True

    def enqueue(self, snapshot, change_type):
        self.enqueue_calls += 1
        job_id = f"job:{snapshot.source_fingerprint}"
        if job_id in self.jobs:
            return job_id, False
        self.jobs[job_id] = {
            "_id": job_id,
            "triple_key": snapshot.key.canonical,
            "product": snapshot.key.product,
            "fail_type": snapshot.key.fail_type,
            "cause_oper": snapshot.key.cause_oper,
            "source_fingerprint": snapshot.source_fingerprint,
            "source_doc_ids": list(snapshot.source_doc_ids),
            "raw_fail_types": list(snapshot.raw_fail_types),
            "doc_count": snapshot.evidence_count,
            "change_type": change_type,
            "status": "pending",
            "attempts": 0,
        }
        return job_id, True

    def claim_next(self, owner, lease_seconds=900):
        candidates = [
            job
            for job in self.jobs.values()
            if job["status"] == "pending"
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda job: (
                0 if job["status"] == "failed" else 1,
                0 if job["change_type"] == "changed" else 1,
                -job["doc_count"],
                job["triple_key"],
            )
        )
        job = candidates[0]
        job["status"] = "running"
        job["lease_owner"] = owner
        job["attempts"] += 1
        return dict(job)

    def mark_succeeded(self, job_id, owner, *, concept_id, concept_version):
        job = self.jobs[job_id]
        if job.get("lease_owner") != owner:
            return False
        job.update(
            status="succeeded",
            concept_id=concept_id,
            concept_version=concept_version,
            lease_owner=None,
        )
        return True

    def mark_failed(self, job_id, owner, error, **kwargs):
        job = self.jobs[job_id]
        if job.get("lease_owner") != owner:
            return False
        job.update(status="failed", last_error=str(error), lease_owner=None)
        return True


@pytest.fixture
def store(tmp_path, monkeypatch):
    vault = tmp_path / "YieldWiki"
    monkeypatch.setenv("WIKI_VAULT_PATH", str(vault))
    import wiki_store

    module = importlib.reload(wiki_store)
    return module


def _synthesis():
    citation = SimpleNamespace(
        model_dump=lambda: {
            "episode_id": "",
            "doc_id": "FH-1",
            "source_file": "FH-1.pptx",
            "date": "2026-08-01",
            "natural_label": "",
            "download_url": "",
        }
    )
    return SimpleNamespace(
        body_markdown="## 합성 결과\n\n검증 본문",
        confidence=0.82,
        citations=[citation],
        entities=[
            EntityCandidate(
                canonical_name="Queue time 초과",
                entity_type="process_condition",
            )
        ],
        relations=[
            RelationCandidate(
                subject="Queue time 초과",
                predicate="causes",
                object="자연 산화",
                confidence=0.82,
                source_doc_ids=["FH-1"],
            )
        ],
    )


def _service(store, snapshots, jobs=None, synthesize=None, materialize=None, fetched=None):
    jobs = jobs or InMemoryJobStore()
    synthesize = synthesize or (lambda concept_id, docs: _synthesis())
    materialize = materialize or (lambda: SimpleNamespace(errors=()))
    return WikiSyncService(
        scanner=FakeScanner(snapshots, fetched=fetched),
        job_store=jobs,
        manifest_path=store._PATHS.manifest,
        index="fail-history",
        synthesize=synthesize,
        wiki_store=store,
        materialize=materialize,
        now=lambda: "2026-08-01T00:00:00+00:00",
        owner_factory=lambda: "worker-1",
    ), jobs


def test_new_concept_records_sync_metadata_manifest_and_materializes_once(store):
    snapshot = _snapshot(_doc("FH-1"), _doc("FH-2"))
    snapshots = {snapshot.key.canonical: snapshot}
    synthesis_calls = []
    materialize_calls = []
    service, jobs = _service(
        store,
        snapshots,
        synthesize=lambda concept_id, docs: synthesis_calls.append(
            (concept_id, docs)
        )
        or _synthesis(),
        materialize=lambda: materialize_calls.append(True)
        or SimpleNamespace(errors=()),
    )

    result = service.apply(limit=10)

    assert result.status == "completed"
    assert result.succeeded == 1
    assert len(synthesis_calls) == 1
    assert materialize_calls == [True]
    concept = store.read_node("concept:4SS|PRE METAL CLN|EASY")
    metadata = concept["frontmatter"]
    assert metadata["source_fingerprint"] == snapshot.source_fingerprint
    assert metadata["source_doc_ids"] == ["FH-1", "FH-2"]
    assert metadata["evidence_count"] == 2
    assert metadata["evidence_scope"] == "multiple_sources"
    assert metadata["sync_job_id"].startswith("job:sha256:")
    assert metadata["entities"] == [
        {"canonical_name": "Queue time 초과", "entity_type": "process_condition"}
    ]
    assert metadata["relations"][0]["predicate"] == "causes"
    assert metadata["body_versions"][-1]["entities"] == metadata["entities"]
    assert metadata["body_versions"][-1]["relations"] == metadata["relations"]
    manifest = load_manifest(store._PATHS.manifest, "fail-history")
    assert manifest["triples"][snapshot.key.canonical]["source_fingerprint"] == snapshot.source_fingerprint
    assert next(iter(jobs.jobs.values()))["status"] == "succeeded"


def test_matching_concept_fingerprint_repairs_manifest_without_llm(store):
    snapshot = _snapshot()
    store.upsert_concept(
        filters={
            "product": snapshot.key.product,
            "fail_type": snapshot.key.fail_type,
            "cause_oper": snapshot.key.cause_oper,
        },
        synthesized_body="existing body",
        sync_metadata={
            "source_fingerprint": snapshot.source_fingerprint,
            "source_doc_ids": list(snapshot.source_doc_ids),
            "evidence_count": snapshot.evidence_count,
            "evidence_scope": snapshot.evidence_scope,
            "sync_job_id": "earlier-job",
        },
        materialize=False,
    )
    calls = []
    materialize_calls = []
    service, jobs = _service(
        store,
        {snapshot.key.canonical: snapshot},
        synthesize=lambda *args: calls.append(args),
        materialize=lambda: materialize_calls.append(True)
        or SimpleNamespace(errors=()),
    )

    result = service.apply(limit=10)

    assert result.recovered == 1
    assert calls == []
    assert materialize_calls == [True]
    assert load_manifest(store._PATHS.manifest, "fail-history")["triples"][
        snapshot.key.canonical
    ]["source_fingerprint"] == snapshot.source_fingerprint
    assert next(iter(jobs.jobs.values()))["status"] == "succeeded"


def test_resume_repairs_materialization_after_concept_persistence_without_synthesis(
    store,
):
    from wiki_materializer import materialize_wiki

    snapshot = _snapshot()
    jobs = InMemoryJobStore()
    synthesis_calls = []
    synthesis = _synthesis()
    synthesis.entities.append(
        EntityCandidate(
            canonical_name="자연 산화",
            entity_type="failure_mechanism",
        )
    )

    def crash_after_partial_materialization():
        report = materialize_wiki(store._PATHS, apply=True)
        assert report.errors == ()
        projection_paths = (
            *store._PATHS.entities.glob("*.md"),
            *store._PATHS.relations.glob("*.md"),
        )
        for path in projection_paths:
            path.unlink()
        raise RuntimeError("crash after Concept persistence")

    first_service, _ = _service(
        store,
        {snapshot.key.canonical: snapshot},
        jobs=jobs,
        synthesize=lambda *args: synthesis_calls.append(args) or synthesis,
        materialize=crash_after_partial_materialization,
    )

    with pytest.raises(RuntimeError, match="crash after Concept persistence"):
        first_service.apply(limit=10)

    concept = store.read_node("concept:4SS|PRE METAL CLN|EASY")
    assert concept is not None
    assert next(iter(jobs.jobs.values()))["status"] == "succeeded"
    assert len(synthesis_calls) == 1
    assert list(store._PATHS.entities.glob("*.md")) == []
    assert list(store._PATHS.relations.glob("*.md")) == []

    resumed_service, _ = _service(
        store,
        {snapshot.key.canonical: snapshot},
        jobs=jobs,
        synthesize=lambda *args: (_ for _ in ()).throw(
            AssertionError("resume must not synthesize")
        ),
        materialize=lambda: materialize_wiki(store._PATHS, apply=True),
    )

    resumed = resumed_service.resume(limit=10)

    assert resumed.status == "completed"
    assert resumed.materialized is True
    assert resumed.failed == 0
    assert len(synthesis_calls) == 1
    entity_posts = [
        frontmatter.load(path) for path in store._PATHS.entities.glob("*.md")
    ]
    assert {post.metadata["canonical_name"] for post in entity_posts} == {
        "Queue time 초과",
        "자연 산화",
    }
    relation_path = next(store._PATHS.relations.glob("*.md"))
    relation_post = frontmatter.load(relation_path)
    assert relation_post.metadata["predicate"] == "causes"
    assert relation_post.metadata["source_doc_ids"] == ["FH-1"]
    assert "[[sources/FH-1|FH-1]]" in relation_post.content


def test_next_normal_apply_repairs_failed_materialization_without_synthesis(store):
    from wiki_materializer import materialize_wiki

    snapshot = _snapshot()
    jobs = InMemoryJobStore()
    synthesis_calls = []
    synthesis = _synthesis()
    synthesis.entities.append(
        EntityCandidate(
            canonical_name="자연 산화",
            entity_type="failure_mechanism",
        )
    )

    def fail_after_concept_write():
        report = materialize_wiki(store._PATHS, apply=True)
        assert report.errors == ()
        for path in (
            *store._PATHS.entities.glob("*.md"),
            *store._PATHS.relations.glob("*.md"),
        ):
            path.unlink()
        raise RuntimeError("projection write failed")

    first_service, _ = _service(
        store,
        {snapshot.key.canonical: snapshot},
        jobs=jobs,
        synthesize=lambda *args: synthesis_calls.append(args) or synthesis,
        materialize=fail_after_concept_write,
    )

    with pytest.raises(RuntimeError, match="projection write failed"):
        first_service.apply(limit=10)

    failed_manifest = load_manifest(store._PATHS.manifest, "fail-history")
    assert failed_manifest["projection"]["status"] == "failed"
    assert len(synthesis_calls) == 1
    assert list(store._PATHS.entities.glob("*.md")) == []
    assert list(store._PATHS.relations.glob("*.md")) == []

    repairing_service, _ = _service(
        store,
        {snapshot.key.canonical: snapshot},
        jobs=jobs,
        synthesize=lambda *args: (_ for _ in ()).throw(
            AssertionError("normal repair must not synthesize")
        ),
        materialize=lambda: materialize_wiki(store._PATHS, apply=True),
    )

    repaired = repairing_service.apply(limit=10)

    assert repaired.status == "completed"
    assert repaired.unchanged == 1
    assert repaired.materialized is True
    assert repaired.failed == 0
    assert len(synthesis_calls) == 1
    assert load_manifest(store._PATHS.manifest, "fail-history")["projection"][
        "status"
    ] == "clean"
    assert len(list(store._PATHS.entities.glob("*.md"))) == 2
    assert len(list(store._PATHS.relations.glob("*.md"))) == 1


def test_changed_source_resynthesizes_existing_concept_and_restores_active(store):
    previous = _snapshot(_doc(content="old"))
    current = _snapshot(_doc(content="new"))
    store.upsert_concept(
        filters={
            "product": previous.key.product,
            "fail_type": previous.key.fail_type,
            "cause_oper": previous.key.cause_oper,
        },
        synthesized_body="old body",
        sync_metadata={
            "source_fingerprint": previous.source_fingerprint,
            "source_doc_ids": list(previous.source_doc_ids),
            "evidence_count": previous.evidence_count,
            "evidence_scope": previous.evidence_scope,
            "sync_job_id": "old-job",
        },
        materialize=False,
    )
    store.mark_concept_stale(
        {
            "product": previous.key.product,
            "fail_type": previous.key.fail_type,
            "cause_oper": previous.key.cause_oper,
        },
        ["FH-removed"],
        "2026-07-31T00:00:00+00:00",
    )
    manifest = empty_manifest("fail-history")
    record_success(
        manifest,
        previous,
        concept_id=f"concept:{previous.key.canonical}",
        concept_version=1,
        success_at="2026-07-31T00:00:00+00:00",
    )
    save_manifest(store._PATHS.manifest, manifest)
    calls = []
    service, _ = _service(
        store,
        {current.key.canonical: current},
        synthesize=lambda *args: calls.append(args) or _synthesis(),
    )

    result = service.apply(limit=10)

    concept = store.read_node("concept:4SS|PRE METAL CLN|EASY")
    assert result.changed == 1
    assert result.succeeded == 1
    assert len(calls) == 1
    assert concept["body"] == "## 합성 결과\n\n검증 본문"
    assert concept["frontmatter"]["status"] == "active"
    assert concept["frontmatter"]["source_fingerprint"] == current.source_fingerprint


def test_refetched_fingerprint_mismatch_fails_without_synthesis(store):
    planned = _snapshot(_doc(content="planned"))
    current = _snapshot(_doc(content="changed after scan"))
    calls = []
    service, jobs = _service(
        store,
        {planned.key.canonical: planned},
        fetched={planned.key.canonical: current},
        synthesize=lambda *args: calls.append(args),
    )

    result = service.apply(limit=10)

    assert result.failed == 1
    assert calls == []
    job = next(iter(jobs.jobs.values()))
    assert job["status"] == "failed"
    assert "fingerprint changed" in job["last_error"]
    assert store.read_node("concept:4SS|PRE METAL CLN|EASY") is None


def test_source_removal_marks_stale_and_never_overwrites_review(store):
    previous = _snapshot(_doc("FH-1"), _doc("FH-2"))
    current = _snapshot(_doc("FH-1"))
    store.upsert_concept(
        filters={
            "product": previous.key.product,
            "fail_type": previous.key.fail_type,
            "cause_oper": previous.key.cause_oper,
        },
        synthesized_body="existing body",
        sync_metadata={
            "source_fingerprint": previous.source_fingerprint,
            "source_doc_ids": list(previous.source_doc_ids),
            "evidence_count": previous.evidence_count,
            "evidence_scope": previous.evidence_scope,
            "sync_job_id": "old-job",
        },
        materialize=False,
    )
    manifest = empty_manifest("fail-history")
    record_success(
        manifest,
        previous,
        concept_id=f"concept:{previous.key.canonical}",
        concept_version=1,
        success_at="2026-07-31T00:00:00+00:00",
    )
    save_manifest(store._PATHS.manifest, manifest)
    materialize_calls = []
    service, jobs = _service(
        store,
        {current.key.canonical: current},
        materialize=lambda: materialize_calls.append(True)
        or SimpleNamespace(errors=()),
    )

    first = service.apply(limit=10)

    assert first.source_removed == 1
    assert jobs.jobs == {}
    assert store.read_node("concept:4SS|PRE METAL CLN|EASY")["frontmatter"][
        "status"
    ] == "stale"
    review_paths = list(store._PATHS.reviews.glob("*.md"))
    assert len(review_paths) == 1
    review = frontmatter.load(review_paths[0])
    assert review.metadata["missing_doc_ids"] == ["FH-2"]
    assert review.metadata["status"] == "pending"
    review_paths[0].write_text(
        review_paths[0].read_text(encoding="utf-8") + "\n운영자 검토 의견\n",
        encoding="utf-8",
    )
    operator_content = review_paths[0].read_text(encoding="utf-8")

    second = service.apply(limit=10)

    assert second.source_removed == 1
    assert review_paths[0].read_text(encoding="utf-8") == operator_content
    assert materialize_calls == [True]


def test_succeeded_fingerprint_is_not_enqueued_or_synthesized_again(store):
    snapshot = _snapshot()
    calls = []
    materialize_calls = []
    jobs = InMemoryJobStore()
    service, _ = _service(
        store,
        {snapshot.key.canonical: snapshot},
        jobs=jobs,
        synthesize=lambda *args: calls.append(args) or _synthesis(),
        materialize=lambda: materialize_calls.append(True)
        or SimpleNamespace(errors=()),
    )

    first = service.apply(limit=10)
    second = service.apply(limit=10)

    assert first.succeeded == 1
    assert second.unchanged == 1
    assert len(calls) == 1
    assert jobs.enqueue_calls == 1
    assert materialize_calls == [True]
