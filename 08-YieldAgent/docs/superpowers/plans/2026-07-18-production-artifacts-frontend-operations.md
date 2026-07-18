# Production Artifacts, Frontend, and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all large artifacts to shared NAS, authorize artifact delivery, migrate React to the job API, add production health/resource controls, and prove the system with real 20-job and failure tests.

**Architecture:** Worker-scoped artifact context writes UUID files atomically to the shared mount and leaves only small references in LangGraph state. React creates jobs and reconnects to GET SSE. Operations use separate liveness/readiness, bounded Oracle pools, cleanup, metrics, and process-level failure tests.

**Tech Stack:** Python/FastAPI, shared POSIX NAS, React 19/Vite/Vitest, Oracle pool, Docker Compose integration environment, httpx load harness

## Global Constraints

- Complete both earlier production plans first.
- NAS root is identical and absolute in every API and worker replica.
- Artifact paths are never accepted from request input or exposed in responses.
- Files use UUID names, temporary write plus atomic rename, checksum, size, MIME type, and owner/job metadata.
- MongoDB and LangGraph checkpoints contain references, not HTML/image/PPT bytes or base64 payloads.
- Artifact and job metadata retention is 30 days; SSE retention is 24 hours.
- Frontend never supplies `user_id`; gateway identity remains authoritative.
- Production starts with API replicas 2 and worker replicas 3 at concurrency 4.
- End-to-end completion requires real Oracle, real LLM, real map generation, and real PPT generation.
- Use TDD and frequent commits.

---

### Task 1: Atomic NAS Artifact Store

**Files:**
- Create: `08-YieldAgent/artifact_store.py`
- Create: `08-YieldAgent/artifact_context.py`
- Create: `08-YieldAgent/tests/unit/test_artifact_store.py`

**Interfaces:**
- Produces: `ArtifactRef`, `ArtifactStore.write_bytes/write_text/open`, `artifact_scope(store)`, `save_artifact(content, mime, title, agent, artifact_type) -> ArtifactRef`, `drain_saved_refs() -> list[ArtifactRef]`
- Consumes: absolute `ARTIFACT_ROOT`, owner hash, job ID

- [ ] **Step 1: Write atomic/path-safety tests**

```python
def test_write_uses_job_directory_and_checksum(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")
    ref = store.write_text("<h1>ok</h1>", mime="text/html", title="yield")
    assert ref.relative_path.startswith("jobs/u123/j123/output/")
    assert ref.size == len(b"<h1>ok</h1>")
    assert store.open(ref).read() == b"<h1>ok</h1>"
    assert not list((tmp_path / "jobs/u123/j123/temp").iterdir())


def test_open_rejects_escape(tmp_path):
    store = ArtifactStore(tmp_path, owner_hash="u123", job_id="j123")
    with pytest.raises(ValueError, match="artifact path"):
        store.open(ArtifactRef(artifact_id="a", relative_path="../../secret", mime="text/plain", size=1, checksum="x", title="x", artifact_type="markdown"))
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_artifact_store.py -q`
Expected: FAIL because artifact modules are absent.

- [ ] **Step 3: Implement immutable references and atomic writes**

`ArtifactRef` contains `artifact_id`, `relative_path`, `artifact_type`, `mime`, `title`, `agent`, `size`, and SHA-256 `checksum`. `ArtifactStore` validates owner hash/job ID as safe fixed-format identifiers, creates `input/output/temp` with mode `0750`, writes a UUID temporary file using exclusive create, flushes and `os.fsync`, then `os.replace`s into `output`. It exposes `artifact_url(ref) -> str` for relative `/jobs/{job_id}/artifacts/{artifact_id}` URLs; it never exposes the NAS path.

`artifact_scope` binds one store and a saved-reference registry with `ContextVar`. `save_artifact` appends each immutable reference to that registry. `drain_saved_refs` returns only references not yet persisted to job metadata. `save_artifact` raises a clear runtime error outside worker scope instead of falling back to local project directories.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_artifact_store.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/artifact_store.py 08-YieldAgent/artifact_context.py \
  08-YieldAgent/tests/unit/test_artifact_store.py
git commit -m "feat(storage): write atomic NAS artifacts"
```

### Task 2: Move Yield, WADS, and Map Artifacts to NAS

**Files:**
- Modify: `08-YieldAgent/yield_query_agent.py`
- Modify: `08-YieldAgent/yield_viz.py`
- Modify: `08-YieldAgent/wads_agent.py`
- Modify: `08-YieldAgent/map_agent.py`
- Modify: `08-YieldAgent/query_state.py`
- Create: `08-YieldAgent/tests/unit/test_primary_artifact_refs.py`

**Interfaces:**
- Produces: artifact dictionaries whose `data` is absent and whose `artifact_ref` contains `ArtifactRef.model_dump()`
- Consumes: `save_artifact`, current worker artifact scope

- [ ] **Step 1: Write artifact-reference tests**

For each agent, monkeypatch Oracle/LLM input at the closest existing pure boundary, run inside `artifact_scope`, and assert:

```python
artifact = result["yield_artifacts"][0]
assert "data" not in artifact
assert artifact["artifact_ref"]["relative_path"].startswith("jobs/")
assert artifact["artifact_ref"]["size"] > 0
```

For map output, decode no base64 in the returned state and verify both HTML and referenced PNG files exist on NAS.

- [ ] **Step 2: Verify tests fail**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_primary_artifact_refs.py -q`
Expected: FAIL because artifacts still contain inline `data` or `file://`.

- [ ] **Step 3: Replace local/inline writes**

- `yield_viz.py`: return generated HTML/image bytes to caller; remove import-time `generated` directory creation and `file://` return.
- `yield_query_agent.py`: call `save_artifact` for every HTML/image artifact and place only `artifact_ref` in state.
- `wads_agent.py`: persist each HTML/markdown artifact before returning state.
- `map_agent.py`: stop returning base64 data URLs; read generated PNG bytes, persist them, build HTML using `artifact_url(image_ref)`, persist the HTML, and delete only the temporary renderer file.
- `query_state.py`: document the reference-only artifact invariant.

Do not change artifact titles, semantic tags, agent names, or planner-visible messages.

- [ ] **Step 4: Verify checkpoint-size invariant**

Add a test that serializes the node update containing a representative map and asserts it remains below 256 KiB while NAS contains the image bytes.

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_primary_artifact_refs.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/yield_query_agent.py 08-YieldAgent/yield_viz.py \
  08-YieldAgent/wads_agent.py 08-YieldAgent/map_agent.py \
  08-YieldAgent/query_state.py 08-YieldAgent/tests/unit/test_primary_artifact_refs.py
git commit -m "refactor(storage): externalize primary artifacts"
```

### Task 3: Move Remaining Agent and PPT Artifacts to NAS

**Files:**
- Modify: `08-YieldAgent/fail_history_agent.py`
- Modify: `08-YieldAgent/lot_history_agent.py`
- Modify: `08-YieldAgent/relation_tree_agent.py`
- Modify: `08-YieldAgent/mining_agent.py`
- Modify: `08-YieldAgent/ppt_export_agent.py`
- Modify: `08-YieldAgent/ppt_builder.py`
- Create: `08-YieldAgent/tests/unit/test_secondary_artifact_refs.py`
- Create: `08-YieldAgent/tests/unit/test_ppt_artifact_refs.py`

**Interfaces:**
- Produces: reference-only artifacts across every active graph agent
- Consumes: `ArtifactStore.open` for PPT source resolution and `save_artifact` for output

- [ ] **Step 1: Write reference-only tests for every remaining active artifact field**

Parameterize over `fail_history_artifacts`, `lot_history_artifacts`, `relation_tree_artifacts`, and `mining_artifacts`. Assert no `data` field contains HTML, base64, absolute paths, or `file://`.

- [ ] **Step 2: Persist remaining HTML artifacts**

Replace inline artifact construction only at each agent's final artifact boundary. Keep existing result envelopes and row data unchanged. Exclude unused `wt_resp_agent.py` unless repository routing proves it is active; if active, add it to the parameterized test and migrate it in the same commit.

- [ ] **Step 3: Make PPT input resolver read references**

Replace `_resolve_artifact_data` with a resolver that accepts `ArtifactStore` and reads only validated `artifact_ref` objects. `YieldReportPPTBuilder` writes to an in-memory buffer and returns bytes without writing `OUTPUT_DIR`. `ppt_export_agent` saves the final bytes as MIME `application/vnd.openxmlformats-officedocument.presentationml.presentation` and returns only its reference.

- [ ] **Step 4: Verify all active artifacts and commit**

Run:

```bash
cd 08-YieldAgent
../.venv/bin/pytest tests/unit/test_secondary_artifact_refs.py \
  tests/unit/test_ppt_artifact_refs.py tests/unit/test_primary_artifact_refs.py -q
```

Expected: all tests pass; `rg 'file://' *.py` finds no active production artifact flow.

```bash
git add 08-YieldAgent/fail_history_agent.py 08-YieldAgent/lot_history_agent.py \
  08-YieldAgent/relation_tree_agent.py 08-YieldAgent/mining_agent.py \
  08-YieldAgent/ppt_export_agent.py 08-YieldAgent/ppt_builder.py \
  08-YieldAgent/tests/unit/test_secondary_artifact_refs.py \
  08-YieldAgent/tests/unit/test_ppt_artifact_refs.py
git commit -m "refactor(storage): externalize all artifacts"
```

### Task 4: Authorized Artifact Delivery

**Files:**
- Modify: `08-YieldAgent/job_repository.py`
- Modify: `08-YieldAgent/job_router.py`
- Modify: `08-YieldAgent/graph_job_runner.py`
- Modify: `08-YieldAgent/job_tasks.py`
- Create: `08-YieldAgent/tests/integration/test_artifact_api.py`

**Interfaces:**
- Produces: artifact metadata persistence and `GET /jobs/{job_id}/artifacts/{artifact_id}`
- Consumes: owner-checked job, artifact store, reference-only graph events

- [ ] **Step 1: Write authorization and traversal tests**

Test owner download success, other owner `404`, unknown artifact `404`, MIME/filename headers, and an artifact metadata record with `../../` path rejected before filesystem access.

- [ ] **Step 2: Persist metadata during event translation**

The worker artifact scope records every saved reference, including images referenced from generated HTML. Before publishing an artifact event, the runner drains new references and stores their metadata in the owning job using an idempotent update keyed by `artifact_id`, then publishes an SSE artifact event containing:

```json
{
  "type": "artifact",
  "artifact_id": "uuid",
  "artifact_type": "html",
  "mime": "text/html",
  "title": "yield_table",
  "agent": "yield_agent",
  "url": "/jobs/01J2JOBEXAMPLE/artifacts/01J2ARTIFACTEXAMPLE"
}
```

Never publish `relative_path` or bytes.

- [ ] **Step 3: Implement streaming response**

The route owner-checks the job, selects metadata by exact artifact ID, resolves beneath `ARTIFACT_ROOT`, rechecks size/checksum metadata availability, and returns `FileResponse` with controlled `Content-Disposition`. HTML is `inline`; PPT is `attachment`; add `X-Content-Type-Options: nosniff`.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_artifact_api.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/job_repository.py 08-YieldAgent/job_router.py \
  08-YieldAgent/graph_job_runner.py 08-YieldAgent/job_tasks.py \
  08-YieldAgent/tests/integration/test_artifact_api.py
git commit -m "feat(api): serve owned NAS artifacts"
```

### Task 5: React Job Client and Types

**Files:**
- Modify: `08-YieldAgent/yield_frontend/package.json`
- Modify: `08-YieldAgent/yield_frontend/package-lock.json`
- Create: `08-YieldAgent/yield_frontend/src/lib/jobs.ts`
- Create: `08-YieldAgent/yield_frontend/src/lib/jobs.test.ts`
- Modify: `08-YieldAgent/yield_frontend/src/types.ts`
- Modify: `08-YieldAgent/yield_frontend/vite.config.ts`
- Delete after migration: `08-YieldAgent/yield_frontend/src/lib/stream.ts`

**Interfaces:**
- Produces: `createJob`, `getJob`, `streamJobEvents`, `resumeJob`, `cancelJob`, `JobStatus`, URL-based `RealArtifact`
- Consumes: new `/jobs` API; sends no identity header

- [ ] **Step 1: Add Vitest and write client tests**

Add `vitest`, `jsdom`, and `@testing-library/react` as dev dependencies and script `"test": "vitest run"`.

Test `POST /jobs` body, no `user_id` header/body, `Last-Event-ID` on reconnect, resume body containing a concrete dictionary value, cancellation, `409 SESSION_BUSY`, and incremental SSE parsing of `id`, `event`, and `data` lines split across chunks.

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent/yield_frontend && npm test`
Expected: FAIL because `jobs.ts` is absent.

- [ ] **Step 3: Implement job client**

Use `fetch` for all endpoints and the existing `ReadableStream` parser pattern. Export:

```typescript
export type JobStatus = "QUEUED" | "RUNNING" | "WAITING_INPUT" | "SUCCEEDED" | "FAILED" | "CANCELLED";
export async function createJob(query: string, sessionId: string, idempotencyKey: string): Promise<JobSnapshot>;
export async function* streamJobEvents(jobId: string, lastEventId?: string): AsyncGenerator<StoredJobEvent>;
export async function resumeJob(jobId: string, value: string | Record<string, unknown>): Promise<JobSnapshot>;
export async function cancelJob(jobId: string): Promise<JobSnapshot>;
```

Update artifact type from `data` to `url`. Add `/jobs` to Vite proxy. Remove `stream.ts` only after no imports remain.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent/yield_frontend && npm test && npm run build`
Expected: tests pass and TypeScript/Vite build succeeds.

```bash
git add 08-YieldAgent/yield_frontend
git commit -m "feat(frontend): add reconnectable job client"
```

### Task 6: React Job Lifecycle UI

**Files:**
- Modify: `08-YieldAgent/yield_frontend/src/App.tsx`
- Modify: `08-YieldAgent/yield_frontend/src/components/Artifacts.tsx`
- Create: `08-YieldAgent/yield_frontend/src/components/JobStatus.tsx`
- Create: `08-YieldAgent/yield_frontend/src/App.test.tsx`

**Interfaces:**
- Produces: queued/running/waiting/terminal UI, automatic reconnect, cancellation, URL artifacts
- Consumes: job client and existing `HitlCard`

- [ ] **Step 1: Write lifecycle UI tests**

Test submit stores `job_id` in `sessionStorage`, queued state renders, interrupted job renders existing `HitlCard`, resume calls `resumeJob`, remount reconnects to stored job, cancel button calls `cancelJob`, and URL artifacts render without fetching bytes into React state.

- [ ] **Step 2: Replace direct chat stream state**

`App.tsx` creates a job, stores `{jobId, sessionId, lastEventId}`, consumes events, updates the saved event ID after every event, and reconnects on mount. It sets `busy` from job status rather than connection lifetime. `WAITING_INPUT` is not busy but keeps the same active job for resume. A new query after a terminal state creates a new job in the same session.

- [ ] **Step 3: Render artifact URLs safely**

`Artifacts.tsx` uses iframe `src={url}` for HTML, `<img src={url}>` for images, fetch/render only for markdown, and download links for PPT. Remove `srcDoc` and base64 assumptions. Set iframe sandbox to `allow-scripts allow-popups` without `allow-same-origin`; Plotly scripts run in an opaque origin and cannot read application credentials.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent/yield_frontend && npm test && npm run build`
Expected: all tests and build pass.

```bash
git add 08-YieldAgent/yield_frontend/src/App.tsx \
  08-YieldAgent/yield_frontend/src/components/Artifacts.tsx \
  08-YieldAgent/yield_frontend/src/components/JobStatus.tsx \
  08-YieldAgent/yield_frontend/src/App.test.tsx
git commit -m "feat(frontend): run durable job lifecycle"
```

### Task 7: Health, Oracle Pool Budget, and Production Route Policy

**Files:**
- Create: `08-YieldAgent/health_router.py`
- Modify: `08-YieldAgent/common.py`
- Modify: `08-YieldAgent/agent_server.py`
- Modify: `08-YieldAgent/settings.py`
- Create: `08-YieldAgent/tests/unit/test_health.py`
- Create: `08-YieldAgent/tests/unit/test_oracle_pool.py`

**Interfaces:**
- Produces: `/health/live`, `/health/ready`, `/health/dependencies`, env-sized Oracle pool
- Consumes: app Redis/Mongo/NAS clients; worker-only Oracle/LLM probes

- [ ] **Step 1: Write health tests**

Test liveness succeeds with dependencies down; readiness returns `503` when Redis, MongoDB, or NAS probe fails; readiness response exposes component names but no URI or credentials. Test production route flags leave `/repl`, `/api/wiki`, `/chat/stream`, and `/download/pptx` unavailable.

- [ ] **Step 2: Write Oracle pool tests**

Monkeypatch `oracledb.create_pool`, set `ORACLE_POOL_MIN=1`, `ORACLE_POOL_MAX=4`, `ORACLE_POOL_INCREMENT=1`, and assert exact arguments. Verify API app import does not call `create_pool`; worker dependency probe does.

- [ ] **Step 3: Implement probes and pool settings**

Liveness performs no network I/O. Readiness uses bounded 1-second probes for Redis ping, MongoDB ping, and NAS readability/writability according to workload. `/health/dependencies` is protected by platform identity or internal network policy and reports sanitized Oracle/LLM status.

Replace Oracle hardcoded `min=2,max=10,increment=1` with validated settings. Add `close_oracle_pool()` for worker shutdown and metrics accessors for busy/open/max.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_health.py tests/unit/test_oracle_pool.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/health_router.py 08-YieldAgent/common.py \
  08-YieldAgent/agent_server.py 08-YieldAgent/settings.py \
  08-YieldAgent/tests/unit/test_health.py 08-YieldAgent/tests/unit/test_oracle_pool.py
git commit -m "feat(ops): add health and pool controls"
```

### Task 8: Cleanup, Structured Logging, and Metrics

**Files:**
- Create: `08-YieldAgent/artifact_cleanup.py`
- Create: `08-YieldAgent/observability.py`
- Modify: `08-YieldAgent/job_tasks.py`
- Modify: `08-YieldAgent/agent_server.py`
- Create: `08-YieldAgent/tests/unit/test_artifact_cleanup.py`
- Create: `08-YieldAgent/tests/unit/test_observability.py`

**Interfaces:**
- Produces: safe cleanup CLI, correlated sanitized logs, Prometheus-compatible metrics endpoint
- Consumes: terminal job metadata and artifact store

- [ ] **Step 1: Write cleanup safety tests**

Create expired terminal, recent terminal, and active job directories. Assert cleanup removes only expired terminal output, leaves active/recent paths, records missing paths idempotently, and refuses any resolved directory outside `ARTIFACT_ROOT`.

- [ ] **Step 2: Write log-redaction tests**

Capture one worker log and assert it contains job ID, task ID, run sequence, attempt, node, and owner hash but excludes raw owner ID, query, Oracle rows, HTML, Redis/Mongo URI, and secrets.

- [ ] **Step 3: Implement CLI and metrics**

`python artifact_cleanup.py --older-than-days 30` queries only terminal jobs whose `artifact_expires_at` passed, validates every directory, deletes it, calls the checkpointer's thread deletion for that job, and marks cleanup time/error in MongoDB. Platform CronJob runs this command daily. Job metadata remains available until the 31-day MongoDB TTL fallback.

Expose metrics for submissions, queue depth, queue wait, state totals, duration, retries, cancellations, SSE reconnects, worker leases, Oracle pool, LLM errors, and NAS free bytes. Use bounded labels; never label by raw user, session, query, or artifact ID.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_artifact_cleanup.py tests/unit/test_observability.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/artifact_cleanup.py 08-YieldAgent/observability.py \
  08-YieldAgent/job_tasks.py 08-YieldAgent/agent_server.py \
  08-YieldAgent/tests/unit/test_artifact_cleanup.py \
  08-YieldAgent/tests/unit/test_observability.py
git commit -m "feat(ops): clean and observe production jobs"
```

### Task 9: Container and Platform Runbook

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `08-YieldAgent/docs/deployment/production-runbook.md`
- Modify: `.env.example`
- Modify: `08-YieldAgent/AGENTS.md`

**Interfaces:**
- Produces: one image runnable as API, worker, beat, or cleanup workload
- Consumes: platform Secrets, shared NAS mount, persistent Redis/MongoDB

- [ ] **Step 1: Build one non-root image**

Use Python 3.11 slim, install locked dependencies, copy application after dependencies for cache reuse, create non-root user, and leave command configurable by platform. Do not bake `.env`, credentials, generated files, tests, or local traces into the image.

- [ ] **Step 2: Document exact workload commands**

```text
API:     uvicorn agent_server:app --host 0.0.0.0 --port 8000
Worker:  celery -A celery_app.celery_app worker -Q analysis --concurrency=4 --loglevel=INFO
Beat:    celery -A celery_app.celery_app beat --loglevel=INFO
Cleanup: python artifact_cleanup.py --older-than-days 30
```

The runbook lists every required environment variable, Secret, port, mount, Redis AOF requirement, exact CORS origins, gateway header-strip/injection check, network policy that blocks direct client access to FastAPI, readiness path, shutdown grace period longer than the worker soft limit, initial replicas, exactly one beat replica, Oracle connection formula, alerts, deploy order, drain procedure, and rollback that leaves workers alive until accepted jobs terminate.

- [ ] **Step 3: Verify image and disabled routes**

Run: `docker build -t yield-agent:production-test .`
Expected: build succeeds and image runs as non-root.

Run: `docker compose -f docker-compose.integration.yml up -d --build`
Expected: API readiness succeeds; `/repl`, `/api/wiki`, `/chat/stream`, and `/download/pptx/x` return `404` in production settings.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore .env.example \
  08-YieldAgent/docs/deployment/production-runbook.md 08-YieldAgent/AGENTS.md
git commit -m "docs(ops): add production deployment runbook"
```

### Task 10: Real 20-job and Failure End-to-End Gate

**Files:**
- Create: `08-YieldAgent/tests/production/concurrent_jobs.py`
- Create: `08-YieldAgent/tests/production/failure_recovery.py`
- Create: `08-YieldAgent/tests/production/verify_real_dependencies.py`
- Create: `08-YieldAgent/docs/deployment/acceptance-results.md`

**Interfaces:**
- Produces: measured production acceptance evidence
- Consumes: staging gateway URL, real Oracle, real LLM, shared NAS, platform replica controls

- [ ] **Step 1: Implement real dependency scenario**

`verify_real_dependencies.py` submits these user scenarios through `/jobs`, follows SSE to terminal/HITL, answers structured forms, downloads artifacts, validates non-zero checksums, and records job IDs and timings without recording result contents:

```text
최근 4주 4SS 수율 보여주고 열화 원인 알려줘
원인 관계도 보여줘
PPT 리포트로 정리해줘
```

Require `PRODUCTION_E2E_BASE_URL`, two authenticated platform test sessions supplied outside Git as `E2E_USER_A_COOKIE` and `E2E_USER_B_COOKIE`, and explicit `RUN_REAL_E2E=1`; otherwise exit non-zero rather than skip. Use both sessions to prove one user receives `404` for the other's job and artifact, and verify a client-supplied `user_id` header cannot replace the gateway identity.

- [ ] **Step 2: Implement 20-job concurrency gate**

`concurrent_jobs.py` creates 20 distinct sessions, submits one real analysis job per session with `httpx.AsyncClient`, asserts all submissions return within 2 seconds with unique job IDs, observes no more than configured worker capacity running, waits for terminal states, and reports p50/p95 queue wait and execution time. It then submits two jobs concurrently to one session and asserts exactly one `202` and one `409 SESSION_BUSY`.

- [ ] **Step 3: Implement local process failure gate**

`failure_recovery.py` uses Docker Compose commands to:

1. submit a long-running real job;
2. restart only the API and verify SSE reconnect plus continued worker execution;
3. stop the worker, wait past the short lease, restart it, and verify one terminal result/checksum per artifact;
4. restart Redis and verify queued job recovery with AOF;
5. remount the test NAS read-only and verify a controlled `FAILED` artifact error.

- [ ] **Step 4: Run actual acceptance**

Run:

```bash
cd 08-YieldAgent
test -n "$PRODUCTION_E2E_BASE_URL"
test -n "$E2E_USER_A_COOKIE"
test -n "$E2E_USER_B_COOKIE"
RUN_REAL_E2E=1 ../.venv/bin/python tests/production/verify_real_dependencies.py
RUN_REAL_E2E=1 ../.venv/bin/python tests/production/concurrent_jobs.py
../.venv/bin/python tests/production/failure_recovery.py
```

Expected: the environment already contains the deployed staging URL, all scripts exit 0, and acceptance results record hostname and timestamp without credentials.

- [ ] **Step 5: Record measured results and final regression**

Write replica count, worker concurrency, Oracle pool maximum, timestamps, job IDs, pass/fail, p50/p95 timings, observed retries, and failure-recovery durations to `acceptance-results.md`. Do not include questions, DB rows, HTML, identity values, or secrets.

Run:

```bash
cd 08-YieldAgent
../.venv/bin/pytest tests/unit tests/integration -q
cd yield_frontend && npm test && npm run build
```

Expected: all tests pass without unexpected skips and frontend build succeeds.

- [ ] **Step 6: Commit**

```bash
git add 08-YieldAgent/tests/production 08-YieldAgent/docs/deployment/acceptance-results.md
git commit -m "test(ops): verify production concurrency"
```
