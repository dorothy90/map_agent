# YieldAgent Production Background Jobs Design

**Date:** 2026-07-18
**Status:** Approved
**Scope:** FastAPI backend, Celery workers, Redis, MongoDB, Oracle access, shared NAS, and the React job client contract

## 1. Goal

Make the current YieldAgent backend safe to run on an internally authenticated, horizontally scalable platform for about 20 concurrent users and a peak of 20 submitted analysis jobs.

The API must remain responsive while LangGraph, Oracle, LLM, map, and presentation work runs outside the API process. Jobs must survive API restarts, support SSE reconnection and HITL resume, prevent concurrent mutation of the same conversation session, and keep artifacts accessible from every replica.

## 2. Success Criteria

- Twenty jobs for different sessions are accepted quickly and receive stable `job_id` values.
- Initially, up to twelve jobs run concurrently; excess jobs remain queued.
- A second active job for the same owner and session returns `409 SESSION_BUSY`.
- API replica loss does not stop a running job.
- Worker loss causes a safely claimed job to resume or retry without duplicate final effects.
- SSE clients reconnect with `Last-Event-ID` and continue from retained events.
- Existing LangGraph `missing_param` interrupts resume through the approved `{slot: value}` contract.
- Job operations and artifacts are accessible only to the trusted platform identity that owns the job.
- Generated files are shared through the common NAS mount and are not embedded in MongoDB checkpoints.
- Production disables `/repl`, `/wiki`, debug traces, and local `file://` artifact URLs.
- End-to-end verification uses the real Oracle database, real LLM endpoint, and real map/PPT generation.

## 3. Non-goals

- Building login, logout, token issuance, or a new identity provider.
- Adding a new file-upload or document-ingestion API; the current product exposes no such route. Any future upload must use the same owner/job-scoped NAS rules and size/media validation.
- Enabling or productionizing `/repl` or `/wiki`.
- Migrating NAS artifacts to S3-compatible object storage in this change.
- Redesigning LangGraph routing, prompts, or semantic planner behavior.
- Adding keyword, regex, or phrase-specific rules to compensate for planner failures.
- Guaranteeing that all twenty submitted jobs execute simultaneously. The initial worker limit is twelve; the rest queue.

## 4. System Architecture

### 4.1 Components

1. The internal authentication gateway validates the user and injects a trusted `user_id` HTTP header. It must remove any client-supplied header with the same name before injection.
2. At least two stateless FastAPI replicas accept jobs, expose job state, stream events, resume HITL jobs, cancel jobs, and serve authorized artifacts.
3. Redis provides the Celery broker, short-lived SSE event streams, rate-limit counters, cancellation flags, and coordination locks.
4. Celery workers execute LangGraph and its Oracle, LLM, document, image, map, and presentation work.
5. MongoDB is the durable source of truth for job metadata, ownership, state transitions, attempts, artifact metadata, and LangGraph checkpoints.
6. A shared NAS mount stores uploads and generated artifacts at the identical absolute path in every API and worker replica.

### 4.2 Initial Deployment Size

- FastAPI: 2 replicas, one Uvicorn process per pod.
- Celery analysis workers: 3 replicas, prefork concurrency 4 per pod.
- Initial job execution capacity: 12.
- Queue admission ceiling: 100 non-terminal jobs globally.
- Per-user ceiling: 2 active or queued jobs across different sessions.

Worker replicas and concurrency may increase only after checking Oracle connection capacity, LLM rate limits, pod CPU/memory, and NAS throughput with the load test described below.

### 4.3 Queue Boundaries

The first implementation uses one `analysis` queue for full LangGraph runs. Splitting an individual dynamic `map_agent` node into another Celery task would add cross-task graph orchestration and is outside the minimum safe change.

Standalone export work that already has a stable input can use an `artifact` queue with lower concurrency. Further map isolation is allowed only after profiling shows a resource problem and a separate design is approved.

## 5. Trusted Identity and Ownership

The backend reads the platform header name from `PLATFORM_USER_ID_HEADER`, defaulting to `user_id` only when explicitly configured for that environment. The frontend must not create or choose this value. Deployment testing must prove that the gateway and ingress preserve the configured name because some proxies reject or normalize header names containing underscores.

Every job stores `owner_id` from the trusted header. Every status, event, resume, cancel, and artifact request loads the job and compares its owner before returning data or mutating state. Unknown jobs and jobs owned by another user both return `404` to avoid disclosing job existence.

LangGraph `thread_id` is namespaced as `{owner_hash}:{session_id}`. A client session ID alone is never used as the checkpoint key, so identical session IDs from different users cannot share state.

The database may store the platform's opaque user identifier. Logs and metrics use a keyed hash of that identifier rather than the raw value. NAS directories use the same keyed hash.

Gateway header injection is a deployment prerequisite. If the platform cannot guarantee it, the backend must validate a signed platform token instead; accepting a frontend-supplied `user_id` is not an alternative.

Network policy must prevent clients from bypassing the gateway and reaching FastAPI directly. Otherwise a caller could forge the trusted header even if the gateway strips it correctly.

## 6. Job API

### 6.1 Endpoints

- `POST /jobs` creates a job and returns `202 Accepted` with `job_id`, `session_id`, `status`, and event URL.
- `GET /jobs/{job_id}` returns the authorized durable job snapshot.
- `GET /jobs/{job_id}/events` streams authorized SSE events.
- `POST /jobs/{job_id}/resume` resumes only a `WAITING_INPUT` job.
- `POST /jobs/{job_id}/cancel` requests cooperative cancellation.
- `GET /jobs/{job_id}/artifacts/{artifact_id}` streams an authorized artifact without exposing its NAS path.

The old `/chat/stream` frontend integration is replaced rather than preserved as a second execution path. A temporary compatibility endpoint is permitted only as a thin adapter that calls the same job service and contains no graph execution logic.

### 6.2 Job States

Durable states are:

- `QUEUED`
- `RUNNING`
- `WAITING_INPUT`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`

Allowed normal transitions are:

```text
QUEUED -> RUNNING
QUEUED -> CANCELLED
RUNNING -> WAITING_INPUT
RUNNING -> SUCCEEDED
RUNNING -> FAILED
RUNNING -> CANCELLED
RUNNING -> QUEUED       # approved transient retry
WAITING_INPUT -> QUEUED # user resume
WAITING_INPUT -> CANCELLED
```

`cancel_requested` is a separate boolean while a running task is finishing its current bounded external call. A queued job may become `CANCELLED` immediately.

### 6.3 Same-session Exclusion

Only one non-terminal job may own a given `(owner_id, session_id)` pair.

MongoDB is authoritative: an `active_session_key` is present only on non-terminal jobs and has a unique sparse index. Job creation that conflicts returns `409` with code `SESSION_BUSY`. Redis may mirror this as a fast lock, but correctness must not depend on Redis surviving a restart.

Terminal transitions remove `active_session_key` atomically with the state update. `WAITING_INPUT` remains active and continues to reserve the session.

### 6.4 Idempotency and Worker Claims

`POST /jobs` accepts an optional client-generated idempotency key scoped to the owner. Repeating a create request returns the original job in its current state rather than enqueueing another one.

Each Celery dispatch uses a unique task ID derived from `job_id` and `run_sequence`. A stable `job_id` alone is not used as the Celery task ID because HITL resume dispatches the same job more than once.

Before graph execution, a worker conditionally claims a `QUEUED` job in MongoDB and writes its task ID, worker identity, attempt, lease expiry, and `RUNNING` state. The worker renews a short lease while running. Redelivered work may claim a `RUNNING` job only after the prior lease expires. If a redelivery arrives before expiry, it retries after the remaining lease interval instead of acknowledging and losing the job. A periodic reconciler re-enqueues expired non-terminal leases as a final recovery path. This claim is the duplicate-execution guard; Celery task IDs alone are not.

## 7. Celery Execution and Failure Policy

Workers use late acknowledgement and reject work when a worker process is lost. Redis broker visibility timeout must exceed the 30-minute job limit plus shutdown margin.

Automatic retry is limited to explicitly classified transient infrastructure failures, such as a connection reset or temporary upstream unavailability. Invalid input, deterministic Oracle query errors, semantic LLM output failures, and programming errors are recorded as `FAILED` without automatic retry. Maximum transient retries: two.

External operations have their own timeouts shorter than the job limit. A hard worker time limit is the final backstop, not the normal cancellation mechanism.

Jobs waiting for user input expire after 24 hours. The reconciler transitions an expired `WAITING_INPUT` job to `CANCELLED`, releases its active session, and publishes the terminal state.

Cancellation is cooperative:

1. The API sets durable `cancel_requested` in MongoDB and a short-lived Redis cancellation flag.
2. Workers check cancellation before and after each graph node and bounded external call.
3. A blocking Oracle or LLM call is allowed to finish or time out; it is not killed mid-protocol.
4. The worker records `CANCELLED`, releases the active session, and removes temporary files.

## 8. HITL Resume

When LangGraph emits the existing structured `missing_param` interrupt, the worker persists the checkpoint, stores the interrupt payload, transitions the job to `WAITING_INPUT`, publishes the event, and returns successfully so the Celery task is acknowledged.

`POST /jobs/{job_id}/resume` accepts the existing canonical `{slot: value}` dictionary. It conditionally changes `WAITING_INPUT` to `QUEUED`, increments `run_sequence`, and dispatches a new Celery task using the same LangGraph `thread_id`.

The current `plan_review` text resume remains a separate contract. No positional parsing or new natural-language hardcoding is introduced.

## 9. SSE and Reconnection

Each job has a Redis Stream containing typed events. Stream IDs are the SSE event IDs. The API sends a durable job snapshot first, then reads new events. A reconnecting client passes the standard `Last-Event-ID` header and receives retained events after that ID.

Event streams expire after 24 hours and use a bounded maximum length. MongoDB retains final status, the latest interrupt, error summary, and artifact metadata after Redis events expire. When a requested event ID is no longer retained, the API sends a fresh snapshot and continues from the current stream tail rather than pretending the complete event history is available.

SSE disconnect only stops the API reader. It does not cancel the Celery job.

## 10. MongoDB Data

The job record contains at least:

- `job_id`, `owner_id`, `session_id`, and LangGraph `thread_id`
- state, timestamps, progress summary, and `active_session_key`
- request envelope needed to start or resume work
- `run_sequence`, attempt, current Celery task ID, worker lease, and retry summary
- `cancel_requested`
- latest HITL interrupt payload
- normalized final result and error category/message
- artifact metadata references
- retention expiry

Indexes cover job ID, owner plus creation time, retention expiry, idempotency key, and unique active session ownership. Artifact and checkpoint cleanup starts after 30 days. Job metadata receives a one-day TTL grace window so cleanup can still find its files and checkpoint before MongoDB removes the record.

Large HTML, image bytes, base64 payloads, PPT files, and raw document bytes must not be stored in the job record or LangGraph checkpoint. This prevents MongoDB's document-size limit and excessive checkpoint growth.

## 11. NAS Artifact Storage

The root comes from `ARTIFACT_ROOT` and is mounted identically on every replica:

```text
{ARTIFACT_ROOT}/jobs/{owner_hash}/{job_id}/input/
{ARTIFACT_ROOT}/jobs/{owner_hash}/{job_id}/output/
{ARTIFACT_ROOT}/jobs/{owner_hash}/{job_id}/temp/
```

File names are server-generated UUIDs. Writers create files in the job's temporary directory, flush and close them, then atomically rename them into the final directory. Artifact metadata stores the relative path, MIME type, byte size, checksum, creation time, and logical artifact type.

The API resolves only validated metadata-relative paths under `ARTIFACT_ROOT`; request paths are never joined directly. Uploads enforce configured size limits, accepted media types, and safe names. Generated files are returned with controlled `Content-Disposition` and MIME headers.

A platform CronJob removes terminal job directories and LangGraph checkpoints older than 30 days. It uses MongoDB retention metadata and never deletes a directory belonging to an active job. MongoDB TTL removes the cleaned job record after the grace window. NAS usage and cleanup failures generate alerts.

## 12. Configuration and Health

All service addresses, credentials, pool sizes, queue names, timeouts, limits, and mount paths come from environment variables. Credentials are delivered through platform Secrets. Existing hardcoded MongoDB or Oracle configuration is removed only where required for this deployment.

Production settings disable `/repl`, `/wiki`, local debug trace files, verbose payload logging, and `file://` URLs.

Health endpoints separate concerns:

- Liveness proves only that the API process event loop is responsive.
- API readiness checks required Redis, MongoDB, and NAS access before receiving traffic.
- Worker startup/dependency probes check Oracle and LLM reachability because only workers execute analysis. API readiness does not create or consume Oracle connections.
- Dependency status reports Oracle and LLM latency/errors without exposing credentials.
- Worker heartbeat and queue depth are monitored outside the FastAPI liveness endpoint.

## 13. Resource and Backpressure Rules

- The API never executes LangGraph, Oracle analysis, LLM analysis, map rendering, or PPT generation inline.
- Per-user admission allows at most two non-terminal jobs across different sessions.
- Same-session admission remains one job regardless of the per-user allowance.
- Global admission rejects new work with `503 QUEUE_FULL` when 100 non-terminal jobs already exist.
- Oracle pool sizes are calculated from total worker processes and the database team's allowed connection budget; defaults are not multiplied blindly across replicas.
- LLM, Oracle, upload, artifact generation, and total job timeouts are independent.
- Worker concurrency changes require measuring memory, CPU, Oracle pool occupancy, LLM throttling, queue wait time, and NAS latency.

Redis performs global and per-user admission updates atomically. These counters provide backpressure, not ownership correctness. The periodic reconciler rebuilds drifted counters from MongoDB after crashes or Redis recovery.

## 14. Observability

Structured logs correlate `job_id`, `session_id`, Celery task ID, run sequence, attempt, node name, and hashed owner ID. Raw user questions, database rows, generated HTML, and secrets are excluded by default.

Metrics and alerts cover:

- submission rate, queue depth, and queue wait time
- running jobs, job duration, state counts, cancellation, failure, and retry rates
- worker heartbeat and lease expiry
- SSE connections and reconnects
- Oracle pool occupancy and timeout/error rates
- LLM latency, timeout, throttle, and error rates
- NAS latency, free space, and cleanup failures
- Redis and MongoDB connectivity

## 15. Frontend Contract Changes

The React client:

1. creates a job with `POST /jobs`;
2. persists the returned `job_id` and `session_id` locally;
3. displays `QUEUED`, `RUNNING`, and `WAITING_INPUT` states;
4. connects to the job event endpoint and reconnects with `Last-Event-ID`;
5. renders the existing structured `missing_param.fields` form and resumes with `{slot: value}`;
6. supports cancellation;
7. retrieves artifacts only through authorized artifact URLs;
8. never generates or sends a selectable `user_id`.

## 16. Verification

Unit and integration tests cover state transition guards, owner isolation, active session uniqueness, idempotency, worker claim/lease behavior, retry classification, cancellation, SSE replay, path validation, and retention.

The production-like end-to-end environment then verifies:

1. Twenty jobs for different sessions are submitted concurrently and all receive job IDs.
2. A second job for the same session returns `409 SESSION_BUSY`.
3. An API pod is terminated during execution; the job continues and SSE reconnects.
4. A worker pod is terminated; the task is redelivered after lease expiry without duplicate final artifacts.
5. A job reaches `WAITING_INPUT`, the client disconnects, and dictionary resume continues the same checkpoint.
6. Queued, running, and waiting jobs cancel correctly.
7. One user cannot read, resume, cancel, or download another user's job.
8. Redis restarts with persistence enabled and queued work remains recoverable.
9. Missing/read-only/full NAS conditions produce controlled failures and alerts.
10. Real Oracle queries, real LLM calls, real map output, and real PPT output complete.
11. The cleanup CronJob removes only expired terminal artifacts.
12. Scaling API and worker replicas does not create duplicate graph execution or artifacts.

Baseline test commands must use a dedicated port and controlled service URLs so an unrelated local server cannot satisfy the tests.

## 17. Rollout

1. Add durable job models, indexes, configuration validation, and trusted-owner extraction behind disabled production routes.
2. Add Redis/Celery execution, worker claims, status transitions, and tests.
3. Add SSE replay, HITL resume, cancellation, and frontend integration.
4. Move artifacts to NAS-safe paths and add authorized download and cleanup.
5. Add production health, metrics, limits, and disabled-route settings.
6. Run real dependency and 20-job failure/load tests in staging.
7. Enable new `/jobs` routes for the frontend, observe queue and failure metrics, then remove direct graph execution from `/chat/stream`.

Rollback stops new job admission and returns the frontend to the previous API version. Existing workers remain deployed until accepted jobs reach terminal states or are explicitly cancelled; queued work is not discarded during rollback.

## 18. Deployment Prerequisites

- The gateway can inject a trusted `user_id` header and strip client copies.
- Network policy exposes FastAPI only through that trusted gateway.
- The ingress preserves the configured identity header; this is verified end to end rather than assumed for an underscore-containing name.
- API and worker workloads can run the same image with different commands.
- Both workloads can reach persistent Redis, MongoDB, Oracle, LLM endpoints, and the shared NAS mount.
- Redis persistence/AOF and required network policies are enabled.
- Oracle connection budget and LLM concurrency/rate limits are documented before final worker sizing.
- The platform supports at least two API replicas, multiple worker replicas, Secrets, CronJobs, logs, metrics, and graceful termination.
