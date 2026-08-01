# Incremental Wiki Sync Implementation Plan

> **Execution:** Follow this plan task-by-task with TDD. Run each named RED test before implementation, then the focused GREEN suite. Commit each completed task independently.

**Goal:** Add a cron-ready `sync_wiki.py` that detects semantic source changes in the live `fail-history` OpenSearch index, persists resumable jobs in MongoDB, and incrementally updates the existing Obsidian Markdown Wiki through the existing LLM synthesizer and materializer.

**Architecture:** Keep `bootstrap_wiki_warmup.py` as the initial/recovery builder. Add pure snapshot/manifest primitives, a MongoDB lease-backed job store, and an orchestration service that scans OpenSearch with composite pagination. Only new or changed triples call the existing `synthesize_concept_from_docs()` and `wiki_store.upsert_concept()` path; source removals become stale Concepts plus deterministic Review notes.

**Tech Stack:** Python 3, pytest, OpenSearch Python client, PyMongo, python-frontmatter, existing Wiki synthesizer/materializer.

## Assumptions and success criteria

- The authoritative source index is `OPENSEARCH_INDEX` (default `fail-history`).
- Canonical Concept identity remains `product|cause_oper|normalized_fail_type`.
- `--limit` is a per-run batch size. Repeated cron runs process later pending jobs, never reprocess an already succeeded fingerprint, and become a zero-write/no-LLM operation only after the queue is drained.
- MongoDB is authoritative for execution state; `.yield-wiki/manifest.json` mirrors successful Vault state.
- A failed actual dependency check is a visible E2E failure, not silently converted to a mock pass.
- Completion requires focused tests, the full existing Wiki suite, and a live OpenSearch + MongoDB + free-LLM + external-Vault run on the top three triples.

---

## Task 1: Canonical snapshots and atomic manifest

**Files:**

- Create: `08-YieldAgent/wiki_sync.py`
- Create: `08-YieldAgent/wiki_manifest.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_sync_snapshot.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_manifest.py`

### Step 1: Write failing snapshot tests

Cover:

- `EASY(W)` and the existing suffix forms normalize to the existing canonical fail type.
- Product and cause operation remain exact metadata values.
- Fingerprints are independent of OpenSearch hit order.
- Changing only `embedding` does not change the fingerprint.
- Changing `content`, `cause`, `action`, `comment`, or source metadata changes it.
- Added document is `changed`; a missing prior document is `source_removed`.
- One document reports `single_source`; multiple report `multiple_sources`.

Run:

```bash
cd 08-YieldAgent
pytest -q tests/wiki/test_wiki_sync_snapshot.py
```

Expected: FAIL because `wiki_sync` contracts do not exist.

### Step 2: Implement the minimum pure snapshot API

Add immutable `TripleKey` and `TripleSnapshot` values plus:

```python
normalize_fail_type(value: str) -> str
make_triple_key(product: str, fail_type: str, cause_oper: str) -> TripleKey
build_triple_snapshot(key: TripleKey, documents: list[dict[str, Any]]) -> TripleSnapshot
classify_snapshot(snapshot: TripleSnapshot, previous: dict[str, Any] | None) -> str
find_removed_triples(current: dict[str, TripleSnapshot], manifest: dict[str, Any]) -> list[str]
```

Fingerprint exactly these fields as canonical JSON: `doc_id`, `content`, `cause`, `action`, `comment`, `date`, `source_file`, `product`, `fail_type`, `cause_oper`. Prefix SHA-256 values with `sha256:`. Preserve the raw fail type inside each fetched source document so it contributes to the fingerprint; do not infer semantics from content.

### Step 3: Write failing manifest tests

Cover missing-file initialization, schema/index validation, corrupted JSON fail-closed, atomic save, successful entry updates, and byte-stable no-op save.

Run:

```bash
pytest -q tests/wiki/test_wiki_manifest.py
```

Expected: FAIL because `wiki_manifest` does not exist.

### Step 4: Implement manifest operations

Add:

```python
empty_manifest(index: str) -> dict[str, Any]
load_manifest(path: Path, index: str) -> dict[str, Any]
record_success(... ) -> bool
save_manifest(path: Path, manifest: dict[str, Any]) -> bool
```

Use same-directory temporary files and `os.replace`. Return `False` and avoid replacing the file when serialized bytes are unchanged.

### Step 5: Verify and commit

```bash
pytest -q tests/wiki/test_wiki_sync_snapshot.py tests/wiki/test_wiki_manifest.py
git add 08-YieldAgent/wiki_sync.py 08-YieldAgent/wiki_manifest.py 08-YieldAgent/tests/wiki/test_wiki_sync_snapshot.py 08-YieldAgent/tests/wiki/test_wiki_manifest.py docs/superpowers/specs/2026-08-01-incremental-wiki-sync-design.md docs/superpowers/plans/2026-08-01-incremental-wiki-sync.md
git commit -m "feat: add Wiki sync snapshots"
```

---

## Task 2: MongoDB job, retry, lease, and global lock

**Files:**

- Create: `08-YieldAgent/wiki_job_store.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_job_store.py`

### Step 1: Write failing integration tests

Use the configured real MongoDB with unique test collection names and guaranteed cleanup. Cover:

- deterministic `_id` and duplicate enqueue suppression;
- priority ordering: retry/expired, changed, new, then document count and key;
- atomic claim and active lease exclusion;
- expired lease reclaim;
- retry delay and terminal failure on attempt three;
- only the owner can renew/release the global lock;
- an unexpired global lock rejects a second runner.

Run:

```bash
cd 08-YieldAgent
pytest -q tests/wiki/test_wiki_job_store.py
```

Expected: FAIL because `wiki_job_store` does not exist.

### Step 2: Implement the store

Add `WikiJobStore` with injected `Database`, collection names, and clock. Provide `from_env()` using the repository's Mongo configuration without logging credentials. Implement:

```python
ensure_indexes()
enqueue(snapshot, change_type) -> tuple[str, bool]
claim_next(owner, lease_seconds) -> dict[str, Any] | None
mark_succeeded(job_id, owner, concept_id, concept_version)
mark_failed(job_id, owner, error, max_attempts=3, retry_delay_seconds=60)
acquire_global_lock(owner, lease_seconds) -> bool
renew_global_lock(owner, lease_seconds) -> bool
release_global_lock(owner) -> bool
```

Use a single `find_one_and_update` for claims. Store only source identifiers/fingerprint and metadata, not full document content or embeddings. Sanitize stored exception text to a bounded one-line message.

### Step 3: Verify and commit

```bash
pytest -q tests/wiki/test_wiki_job_store.py
git add 08-YieldAgent/wiki_job_store.py 08-YieldAgent/tests/wiki/test_wiki_job_store.py
git commit -m "feat: persist Wiki sync jobs"
```

---

## Task 3: Complete OpenSearch scanner and change planner

**Files:**

- Modify: `08-YieldAgent/wiki_sync.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_sync_scanner.py`

### Step 1: Write failing scanner tests

With a fake OpenSearch client, cover:

- composite aggregation follows every `after_key` page;
- exact product/fail/cause document filtering;
- `_source` excludes `embedding` and includes all fingerprint/synthesis fields;
- raw triples that normalize to one canonical key are merged without losing documents;
- pagination or fetch errors propagate before any plan is returned;
- the plan counts and lists `new`, `changed`, `source_removed`, and `unchanged` correctly.

Run:

```bash
pytest -q tests/wiki/test_wiki_sync_scanner.py
```

Expected: FAIL because scanner/planner APIs are absent.

### Step 2: Implement scanner and planner

Add `OpenSearchWikiScanner` with injected client/index and:

```python
scan() -> dict[str, TripleSnapshot]
fetch_snapshot(product: str, fail_type: str, cause_oper: str) -> TripleSnapshot
plan_sync(snapshots, manifest) -> SyncPlan
```

Use composite pagination until `after_key` is absent. Fetch each raw triple using exact keyword filters, merge by canonical key, deduplicate by stable document identity, and sort deterministically. Do not use vector search or request embeddings.

### Step 3: Verify and commit

```bash
pytest -q tests/wiki/test_wiki_sync_snapshot.py tests/wiki/test_wiki_sync_scanner.py
git add 08-YieldAgent/wiki_sync.py 08-YieldAgent/tests/wiki/test_wiki_sync_scanner.py
git commit -m "feat: plan OpenSearch Wiki changes"
```

---

## Task 4: Sync Concept writes and source-removal Reviews

**Files:**

- Modify: `08-YieldAgent/wiki_store.py`
- Modify: `08-YieldAgent/wiki_sync.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_sync_service.py`

### Step 1: Write failing temporary-Vault integration tests

Inject scanner, job store, synthesizer, and materializer boundaries. Cover:

- `new` and `changed` jobs call the existing synthesizer once and write Concept sync metadata;
- source metadata includes fingerprint, sorted document IDs, evidence count/scope, and job ID;
- existing Concept with the target fingerprint repairs manifest/job without an LLM call;
- mismatched current OpenSearch fingerprint is not synthesized as the stale job target;
- source removal marks the Concept stale and creates one deterministic Review;
- rerunning the same removal never overwrites operator-edited Review content;
- materializer runs once per batch only when Vault files changed;
- a succeeded fingerprint is not enqueued or synthesized again.

Run:

```bash
pytest -q tests/wiki/test_wiki_sync_service.py
```

Expected: FAIL because service/store sync operations are absent.

### Step 2: Extend store surgically

Add an optional keyword-only `sync_metadata` mapping to `upsert_concept()`. Write only the approved keys and restore `status: active` on a successful re-synthesis. Add focused helpers:

```python
mark_concept_stale(filters, missing_doc_ids, detected_at) -> tuple[str, bool]
create_source_removal_review(filters, previous_doc_ids, current_doc_ids, detected_at) -> tuple[str, bool]
```

The Review filename is a deterministic hash of canonical key and previous/current ID sets. If it exists, return without writing it.

### Step 3: Implement orchestration

Add `WikiSyncService` with `check()`, `apply(limit)`, and `resume(limit)`. Processing order:

1. acquire global lease;
2. for apply, scan and enqueue new/changed jobs, then record removals;
3. claim at most `limit` jobs;
4. refetch and verify the target fingerprint;
5. recover from matching Concept metadata or call the existing synthesizer/upsert path;
6. atomically update manifest, then mark Mongo job succeeded;
7. run the existing M2 materializer once if Vault changed;
8. release only the owned lock in `finally`.

`resume` skips the full scan/enqueue phase but refetches each claimed job's source before synthesis. Errors are recorded for retry and do not expose document bodies or credentials.

### Step 4: Verify and commit

```bash
pytest -q tests/wiki/test_wiki_store_external_vault.py tests/wiki/test_wiki_sync_service.py
git add 08-YieldAgent/wiki_store.py 08-YieldAgent/wiki_sync.py 08-YieldAgent/tests/wiki/test_wiki_sync_service.py
git commit -m "feat: incrementally update Wiki concepts"
```

---

## Task 5: CLI, bootstrap interoperability, and runbook

**Files:**

- Create: `08-YieldAgent/sync_wiki.py`
- Modify: `08-YieldAgent/bootstrap_wiki_warmup.py`
- Modify: `08-YieldAgent/docs/wiki-deployment-procedure.md`
- Create: `08-YieldAgent/tests/wiki/test_sync_wiki_cli.py`
- Create: `08-YieldAgent/tests/wiki/test_bootstrap_wiki_sync_metadata.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_runbook.py`

### Step 1: Write failing CLI/bootstrap tests

Cover:

- exactly one of `--check`, `--apply`, `--resume` is required;
- `--limit` is a positive integer accepted only for apply/resume;
- check is read-only and prints deterministic counts/targets;
- already-running exits without writes;
- apply/resume exit nonzero on dependency or materialization failure;
- bootstrap exact filters are all-or-none;
- bootstrap uses shared canonical snapshot code and records sync metadata/manifest;
- the runbook documents initial bootstrap, normal cron command, resume, check, source-removal review, and recovery.

Run:

```bash
pytest -q tests/wiki/test_sync_wiki_cli.py tests/wiki/test_bootstrap_wiki_sync_metadata.py tests/wiki/test_wiki_runbook.py
```

Expected: FAIL before CLI/wiring exists.

### Step 2: Add the CLI

Keep argument parsing thin. Construct the actual OpenSearch scanner, Mongo job store, manifest path, existing synthesizer/store/materializer dependencies, then print a concise JSON-compatible summary. Return nonzero for invalid dependencies or failed work; `already_running` is a clean no-op result.

### Step 3: Connect bootstrap

Replace the local fail-type normalizer with the shared function. Add `--product`, `--fail-type`, and `--cause-oper` as an all-or-none exact selection. On every successful Concept write, include the same sync metadata and atomically record manifest success. Preserve the existing default bootstrap behavior and existing `--skip-existing` semantics.

### Step 4: Document cron-ready operation

Add an example such as:

```cron
*/10 * * * * cd /path/to/08-YieldAgent && /path/to/python sync_wiki.py --apply --limit 10 >> /path/to/logs/wiki-sync.log 2>&1
```

Clearly state that M3 provides the command but does not install cron, that batch runs continue the remaining queue, and that operators inspect pending source-removal Reviews before exact bootstrap recovery.

### Step 5: Verify and commit

```bash
pytest -q tests/wiki/test_sync_wiki_cli.py tests/wiki/test_bootstrap_wiki_sync_metadata.py tests/wiki/test_wiki_runbook.py
pytest -q tests/wiki
git add 08-YieldAgent/sync_wiki.py 08-YieldAgent/bootstrap_wiki_warmup.py 08-YieldAgent/docs/wiki-deployment-procedure.md 08-YieldAgent/tests/wiki
git commit -m "feat: add cron-ready Wiki sync CLI"
```

---

## Task 6: Actual dependency and end-to-end verification

**Files:**

- Create only if useful for evidence: `08-YieldAgent/docs/wiki-m3-e2e-results.md`

### Step 1: Verify regressions before live writes

```bash
cd 08-YieldAgent
pytest -q tests/wiki
python sync_wiki.py --check
```

Capture a hash/tree snapshot of `/Users/daehwankim/SYLDAIX/YieldWiki` and relevant Mongo collection counts before and after `--check`; verify both are unchanged. Confirm the live scan reports 505 documents and 390 canonical triples, or explicitly report current live values if the source changed.

### Step 2: Back up and run top-three live batch

Create a timestamped recoverable copy of the external Vault. Run:

```bash
python sync_wiki.py --apply --limit 3
```

This must use the configured live OpenSearch, real MongoDB, and approved free LLM endpoint. Verify three claimed jobs reach `succeeded`, matching Concept frontmatter and manifest entries exist, and the Markdown materializer emits valid Product/Fail/Operation/Source links.

### Step 3: Verify incremental idempotency correctly

Run `--check` and verify the first three fingerprints are `unchanged`. A second limited apply may process the next pending batch by design; it must not call the LLM again for those first three. Once the pending queue is drained in a controlled test collection/index or focused top-three fixture, rerun and verify zero LLM calls and byte-identical Markdown.

### Step 4: Verify crash recovery

For a succeeded test target, remove only its manifest entry while keeping the Concept fingerprint. Resume/re-enqueue that same target and verify the manifest/job are repaired without an LLM call or Concept body rewrite. Restore/retain the consistent successful state.

### Step 5: Final verification and commit evidence

```bash
pytest -q tests/wiki
git status --short
```

Record exact commands, actual counts, free model/provider, Concept IDs, Mongo states, manifest result, graph link lint, and any environmental caveats. Do not claim Obsidian visual verification unless it was actually opened and inspected.

```bash
git add 08-YieldAgent/docs/wiki-m3-e2e-results.md
git commit -m "docs: verify incremental Wiki sync"
```

## Final acceptance checklist

- [ ] Existing bootstrap remains the initial/recovery path.
- [ ] Sync scans all OpenSearch triples through composite pagination.
- [ ] Only new/changed fingerprints use the existing LLM synthesizer.
- [ ] Embedding-only updates do not schedule synthesis.
- [ ] Removed evidence creates stale status and a non-overwritten Review.
- [ ] Mongo job leases, retry, resume, and global writer lock work against real MongoDB.
- [ ] Concept fingerprint repairs manifest crash gaps without LLM.
- [ ] Materialization occurs once per changed batch.
- [ ] `--check` is demonstrably read-only.
- [ ] Existing frontends, API shapes, mappings, and embeddings are unchanged.
- [ ] Full Wiki test suite passes.
- [ ] Actual top-three OpenSearch → MongoDB → free LLM → Vault → Markdown graph flow passes.
