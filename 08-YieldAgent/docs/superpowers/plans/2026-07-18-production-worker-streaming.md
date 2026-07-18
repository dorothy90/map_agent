# Production Worker and Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run LangGraph in Celery workers with durable claims, retry/lease recovery, Redis Stream events, reconnectable SSE, HITL resume, and cooperative cancellation.

**Architecture:** FastAPI dispatches only stable job IDs. Celery workers claim MongoDB jobs, compile the graph in worker processes, publish typed events to Redis Streams, and acknowledge after a terminal or HITL state is durable.

**Tech Stack:** Celery 5, Redis broker/Streams, MongoDB/Motor/PyMongo, LangGraph MongoDBSaver, FastAPI SSE, pytest

## Global Constraints

- Complete `2026-07-18-production-job-foundation.md` first.
- API processes must not execute LangGraph, Oracle, LLM, map, or PPT analysis.
- Initial workers use queue `analysis`, 3 replicas, prefork concurrency 4.
- Total job timeout is 30 minutes; transient infrastructure retries are limited to 2.
- Celery dispatch ID is `{job_id}:{run_sequence}`; MongoDB claim/lease prevents duplicate execution.
- SSE events expire after 24 hours and reconnect through `Last-Event-ID`.
- `WAITING_INPUT` holds same-session ownership for at most 24 hours.
- Preserve the existing structured `missing_param` dictionary resume and separate `plan_review` text resume.
- Cancellation is cooperative; bounded Oracle/LLM calls finish or time out.
- Use TDD, exact state transitions, and frequent commits.

---

### Task 1: Redis Job Event Store

**Files:**
- Create: `08-YieldAgent/job_events.py`
- Create: `08-YieldAgent/tests/integration/test_job_events.py`

**Interfaces:**
- Produces: `JobEventStore.publish(job_id, event) -> str`, `read(job_id, after_id, block_ms)`, `snapshot_event(job)`, `expire(job_id)`
- Consumes: async Redis client, Pydantic event dictionaries

- [ ] **Step 1: Write replay and expiry tests**

```python
@pytest.mark.asyncio
async def test_read_replays_after_last_event(redis_client):
    store = JobEventStore(redis_client, ttl_seconds=86_400, max_events=2_000)
    first = await store.publish("j1", {"type": "status", "message": "queued"})
    second = await store.publish("j1", {"type": "status", "message": "running"})
    events = await store.read("j1", after_id=first, block_ms=1)
    assert [event.id for event in events] == [second]
    assert events[0].data["message"] == "running"


@pytest.mark.asyncio
async def test_publish_sets_stream_ttl(redis_client):
    store = JobEventStore(redis_client, ttl_seconds=86_400, max_events=2_000)
    await store.publish("j1", {"type": "stream_end"})
    assert await redis_client.ttl("job:events:j1") > 0
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && TEST_REDIS_URL=redis://127.0.0.1:6380/0 ../.venv/bin/pytest tests/integration/test_job_events.py -q`
Expected: FAIL because `job_events` does not exist.

- [ ] **Step 3: Implement bounded Redis Streams**

Serialize one JSON payload field with `XADD job:events:{job_id} MAXLEN ~ 2000`, then refresh `EXPIRE 86400`. `read` uses `XREAD` from the supplied ID and returns typed `StoredEvent(id: str, data: dict)`. Reject event payloads larger than 256 KiB so artifacts cannot leak into Redis.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && TEST_REDIS_URL=redis://127.0.0.1:6380/0 ../.venv/bin/pytest tests/integration/test_job_events.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/job_events.py 08-YieldAgent/tests/integration/test_job_events.py
git commit -m "feat(stream): persist replayable job events"
```

### Task 2: Celery Application and Dispatcher

**Files:**
- Create: `08-YieldAgent/celery_app.py`
- Create: `08-YieldAgent/celery_dispatcher.py`
- Modify: `08-YieldAgent/job_service.py`
- Create: `08-YieldAgent/tests/unit/test_celery_dispatcher.py`

**Interfaces:**
- Produces: `celery_app`, `CeleryJobDispatcher.dispatch(job_id, run_sequence) -> task_id`
- Consumes: settings and task name `yield_agent.run_job`

- [ ] **Step 1: Write dispatch identity test**

```python
@pytest.mark.asyncio
async def test_dispatch_uses_job_and_sequence(fake_celery):
    dispatcher = CeleryJobDispatcher(fake_celery)
    task_id = await dispatcher.dispatch("job-1", 3)
    assert task_id == "job-1:3"
    assert fake_celery.sent == [
        ("yield_agent.run_job", ["job-1", 3], "job-1:3", "analysis")
    ]
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_celery_dispatcher.py -q`
Expected: FAIL because dispatcher is absent.

- [ ] **Step 3: Configure Celery safety settings**

Create Celery with Redis broker, no Celery result backend, JSON-only serialization, `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, soft limit 1740 seconds, hard limit 1800 seconds, broker visibility timeout 2100 seconds, and default queue `analysis`. `CeleryJobDispatcher` calls `send_task` through `asyncio.to_thread` so FastAPI's event loop does not block.

- [ ] **Step 4: Wire the production dispatcher and verify**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_celery_dispatcher.py tests/unit/test_job_router.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/celery_app.py 08-YieldAgent/celery_dispatcher.py \
  08-YieldAgent/job_service.py 08-YieldAgent/tests/unit/test_celery_dispatcher.py
git commit -m "feat(worker): dispatch jobs through Celery"
```

### Task 3: Extract HTTP-independent Graph Runner

**Files:**
- Create: `08-YieldAgent/graph_job_runner.py`
- Modify: `08-YieldAgent/agent_server.py`
- Create: `08-YieldAgent/tests/unit/test_graph_job_runner.py`

**Interfaces:**
- Produces: `GraphRunRequest`, `GraphRunResult`, `run_graph(graph, request, emit, cancelled) -> GraphRunResult`
- Consumes: compiled LangGraph, existing SSE Pydantic events, existing trace helpers

- [ ] **Step 1: Write fake-graph event translation tests**

```python
async def async_false():
    return False


async def async_true():
    return True


async def async_emit(event):
    return None


@pytest.mark.asyncio
async def test_interrupt_returns_waiting_input(fake_graph):
    emitted = []
    async def emit(event):
        emitted.append(event)
    fake_graph.streams({"__interrupt__": [{"value": {
        "interrupt_type": "missing_param", "param": "lotcd",
        "message": "제품코드", "route": "yield_agent", "fields": []
    }}]})
    result = await run_graph(
        fake_graph,
        GraphRunRequest(job_id="j1", owner_id="u1", session_id="s1", thread_id="h1:s1", query="q"),
        emit,
        async_false,
    )
    assert result.outcome == "WAITING_INPUT"
    assert emitted[-1]["type"] == "interrupt"


@pytest.mark.asyncio
async def test_cancellation_stops_between_events(fake_graph):
    fake_graph.streams({"custom": {"kind": "status", "message": "started"}})
    request = GraphRunRequest(job_id="j1", owner_id="u1", session_id="s1", thread_id="h1:s1", query="q")
    result = await run_graph(fake_graph, request, async_emit, async_true)
    assert result.outcome == "CANCELLED"
```

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_graph_job_runner.py -q`
Expected: FAIL because the runner is absent.

- [ ] **Step 3: Move graph execution out of the route**

Move the graph input construction, `Command` with the supplied resume value, trace context, `astream` mode handling, message/token/status/interrupt translation, and final chat turn normalization from `chat_stream` into `run_graph`. The runner receives awaitable `emit(dict)` and `cancelled()` callbacks and never imports FastAPI or `StreamingResponse`.

Use the durable job's namespaced `thread_id`, not raw `session_id`, in LangGraph configuration. Set graph state `user_id` from trusted `GraphRunRequest.owner_id`; remove `ChatRequest.user_id` from the new job path.

Use these result types:

```python
@dataclass(frozen=True)
class GraphRunRequest:
    job_id: str
    owner_id: str
    session_id: str
    thread_id: str
    query: str
    resume_value: str | dict[str, Any] | None = None

@dataclass(frozen=True)
class GraphRunResult:
    outcome: Literal["SUCCEEDED", "WAITING_INPUT", "CANCELLED"]
    latest_interrupt: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
```

The legacy `/chat/stream` adapter may call this runner while enabled, but it must not retain a separate copy of graph traversal logic.

- [ ] **Step 4: Verify runner and legacy regression shape**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_graph_job_runner.py -q`
Expected: all tests pass.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/graph_job_runner.py 08-YieldAgent/agent_server.py \
  08-YieldAgent/tests/unit/test_graph_job_runner.py
git commit -m "refactor(graph): extract background runner"
```

### Task 4: Worker Runtime, Claims, and Durable Completion

**Files:**
- Create: `08-YieldAgent/worker_runtime.py`
- Create: `08-YieldAgent/job_tasks.py`
- Modify: `08-YieldAgent/job_repository.py`
- Create: `08-YieldAgent/tests/integration/test_job_task.py`

**Interfaces:**
- Produces: Celery task `yield_agent.run_job(job_id, run_sequence)`, process-local `WorkerRuntime`, lease renewal
- Consumes: graph runner, task-local async Mongo/Redis clients, process-local MongoDBSaver

- [ ] **Step 1: Write eager-worker completion test**

```python
def test_worker_claims_runs_and_completes(job_fixture, fake_runner):
    result = run_job.apply(args=[job_fixture["job_id"], 0]).get()
    stored = job_collection.find_one({"job_id": job_fixture["job_id"]})
    assert result["status"] == "SUCCEEDED"
    assert stored["status"] == "SUCCEEDED"
    assert "active_session_key" not in stored
```

Also test duplicate delivery while a live lease exists performs no graph call, expired lease can be reclaimed, and `WAITING_INPUT` remains session-active.

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_job_task.py -q`
Expected: FAIL because task/runtime are absent.

- [ ] **Step 3: Implement process-local runtime**

`WorkerRuntime` creates one `MongoDBSaver`/compiled workflow per Celery child process. It initializes lazily after fork, not at module import, and closes the saver client on `worker_process_shutdown`. Each Celery delivery calls one async `_execute_job`; that coroutine creates Motor and `redis.asyncio` clients on its own event loop, uses the existing async `JobRepository` and `JobEventStore`, and closes both clients before returning. This avoids sharing loop-bound clients across repeated `asyncio.run` calls.

The sync Celery task calls `asyncio.run(_execute_job(job_id, run_sequence))`. `_execute_job` validates `run_sequence`, claims the job, starts an async lease-renewal task, awaits `run_graph`, persists the returned state before acknowledgement, cancels and awaits lease renewal in `finally`, and releases Redis admission only on terminal states.

- [ ] **Step 4: Implement safe redelivery behavior**

If the same run is already `RUNNING` with an unexpired lease, raise `self.retry(countdown=max(1, remaining_lease_seconds), max_retries=3)`. If those retries expire, acknowledge and let the periodic reconciler recover the lease. If the lease expired, call `reclaim_expired`. If the job is terminal or has another `run_sequence`, acknowledge without running.

- [ ] **Step 5: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_job_task.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/worker_runtime.py 08-YieldAgent/job_tasks.py \
  08-YieldAgent/job_repository.py 08-YieldAgent/tests/integration/test_job_task.py
git commit -m "feat(worker): execute claimed graph jobs"
```

### Task 5: Reconnectable SSE Endpoint

**Files:**
- Modify: `08-YieldAgent/job_router.py`
- Modify: `08-YieldAgent/job_service.py`
- Create: `08-YieldAgent/tests/integration/test_job_sse.py`

**Interfaces:**
- Produces: `GET /jobs/{job_id}/events`
- Consumes: owned job snapshot and `JobEventStore`

- [ ] **Step 1: Write replay/ownership tests**

Create three stored events, request with `Last-Event-ID` equal to the first, and assert only later events appear after the initial `job_snapshot`. Request as another owner and assert `404`. Disconnect the HTTP client and assert MongoDB job status remains `RUNNING`.

- [ ] **Step 2: Verify failure**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_job_sse.py -q`
Expected: FAIL with route `404`.

- [ ] **Step 3: Implement SSE formatting and heartbeat**

The endpoint verifies ownership before creating `StreamingResponse`. Emit:

```text
id: 1721325600000-0
event: status
data: {"type":"status","message":"running"}

```

Send `: heartbeat\n\n` after 15 seconds without an event. Respect `request.is_disconnected()`. On missing/trimmed `Last-Event-ID`, send current `job_snapshot` and start at Redis stream tail. Add `Cache-Control: no-cache`, `X-Accel-Buffering: no`, and `Content-Type: text/event-stream`.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_job_sse.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/job_router.py 08-YieldAgent/job_service.py \
  08-YieldAgent/tests/integration/test_job_sse.py
git commit -m "feat(stream): reconnect job event SSE"
```

### Task 6: HITL Resume and Cooperative Cancellation

**Files:**
- Modify: `08-YieldAgent/job_router.py`
- Modify: `08-YieldAgent/job_service.py`
- Modify: `08-YieldAgent/job_tasks.py`
- Create: `08-YieldAgent/tests/integration/test_job_control.py`

**Interfaces:**
- Produces: `POST /jobs/{job_id}/resume`, `POST /jobs/{job_id}/cancel`
- Consumes: `ResumeRequest`, repository conditional transitions, Celery dispatcher

- [ ] **Step 1: Write resume contract tests**

Test that a `WAITING_INPUT` `missing_param` job accepts `{"value":{"lotcd":"4SS"}}`, increments `run_sequence`, keeps the same `thread_id`, and dispatches once. Test that an empty dictionary returns `422`; a `RUNNING` resume returns `409 JOB_NOT_WAITING`; another owner receives `404`.

- [ ] **Step 2: Write cancellation tests**

Test queued cancellation immediately stores `CANCELLED` and releases admission/session ownership. Running cancellation stores `cancel_requested=True`; a fake runner observing the flag returns `CANCELLED`; repeated cancellation is idempotent.

- [ ] **Step 3: Implement conditional control paths**

Resume uses one MongoDB update filtered by owner, job ID, and `WAITING_INPUT`, sets `QUEUED`, stores `resume_value`, increments `run_sequence`, clears the prior worker lease, then dispatches. If dispatch fails, restore `WAITING_INPUT` only when the new sequence is still queued.

Cancel updates MongoDB first and mirrors `job:cancel:{job_id}` in Redis with a 30-minute TTL. Worker checks durable state before and after each graph event/node boundary; Redis is only a low-latency mirror.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/integration/test_job_control.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/job_router.py 08-YieldAgent/job_service.py \
  08-YieldAgent/job_tasks.py 08-YieldAgent/tests/integration/test_job_control.py
git commit -m "feat(jobs): resume and cancel graph work"
```

### Task 7: Retry Classification and Expired-job Reconciler

**Files:**
- Create: `08-YieldAgent/job_failures.py`
- Create: `08-YieldAgent/job_reconciler.py`
- Modify: `08-YieldAgent/job_tasks.py`
- Modify: `08-YieldAgent/common.py`
- Create: `08-YieldAgent/tests/unit/test_job_failures.py`
- Create: `08-YieldAgent/tests/integration/test_job_reconciler.py`

**Interfaces:**
- Produces: `classify_failure(exc) -> FailureDecision`, scheduled task `yield_agent.reconcile_jobs`
- Consumes: existing `is_transient_error`, repository, dispatcher, admission

- [ ] **Step 1: Write exact retry matrix tests**

```python
@pytest.mark.parametrize("exc,retry", [
    (TimeoutError("upstream"), True),
    (ConnectionError("reset"), True),
    (ValueError("bad planner output"), False),
    (TypeError("bug"), False),
])
def test_retry_classification(exc, retry):
    assert classify_failure(exc).retry is retry
```

Add an Oracle operational-error fixture whose error code is documented as transient and a deterministic SQL error fixture whose code is not. Do not classify all `oracledb.DatabaseError` values as retryable.

- [ ] **Step 2: Implement bounded retries**

On a retryable failure with `attempt < 2`, atomically set `RUNNING` back to `QUEUED`, increment attempt, clear lease, publish retry status, and call `self.retry` with backoff 5 then 20 seconds. Otherwise store sanitized `FAILED`, release session and admission, and publish `error` plus `stream_end`.

- [ ] **Step 3: Implement reconciliation**

Every minute, one Celery beat task guarded by Redis lock `jobs:reconciler`:

- re-enqueues expired `RUNNING` leases using the current run sequence;
- re-enqueues `QUEUED` jobs whose `dispatched_at` is missing or stale for more than one minute;
- cancels `WAITING_INPUT` older than 24 hours;
- rebuilds Redis admission sets from MongoDB non-terminal jobs;
- releases stale cancellation keys for terminal jobs.

Use a five-minute lock with owner token and compare-delete release.

- [ ] **Step 4: Verify and commit**

Run: `cd 08-YieldAgent && ../.venv/bin/pytest tests/unit/test_job_failures.py tests/integration/test_job_reconciler.py -q`
Expected: all tests pass.

```bash
git add 08-YieldAgent/job_failures.py 08-YieldAgent/job_reconciler.py \
  08-YieldAgent/job_tasks.py 08-YieldAgent/common.py \
  08-YieldAgent/tests/unit/test_job_failures.py \
  08-YieldAgent/tests/integration/test_job_reconciler.py
git commit -m "feat(worker): recover expired jobs safely"
```

### Task 8: Worker Failure Integration Gate

**Files:**
- Modify: `docker-compose.integration.yml`
- Create: `08-YieldAgent/tests/integration/test_worker_process.py`
- Modify: `08-YieldAgent/AGENTS.md`

**Interfaces:**
- Produces: verified separate API/worker execution and redelivery behavior
- Consumes: all worker and streaming components

- [ ] **Step 1: Add API and worker services to integration Compose**

Use the repository image with separate commands:

```yaml
api:
  command: uvicorn agent_server:app --host 0.0.0.0 --port 8000
worker:
  command: celery -A celery_app.celery_app worker -Q analysis --concurrency=1 --loglevel=INFO
beat:
  command: celery -A celery_app.celery_app beat --loglevel=INFO
```

Mount a named shared test volume at `/mnt/yield-agent` and use test MongoDB/Redis URLs.

- [ ] **Step 2: Verify worker discovery**

Run: `docker compose -f docker-compose.integration.yml up -d --build`
Expected: API, worker, beat, MongoDB, and Redis healthy.

Run: `docker compose -f docker-compose.integration.yml exec worker celery -A celery_app.celery_app inspect ping`
Expected: one worker responds `pong`.

- [ ] **Step 3: Run process-boundary integration test**

The test submits a deterministic fake-graph job enabled only by `ENVIRONMENT=test`, observes `QUEUED`, `RUNNING`, and `SUCCEEDED` over SSE, and confirms the API PID never records graph execution. It then submits a blocking fake job, stops the worker container, restarts it, waits for lease recovery, and verifies one terminal result.

Run: `cd 08-YieldAgent && JOB_API_URL=http://127.0.0.1:18000 ../.venv/bin/pytest tests/integration/test_worker_process.py -q`
Expected: all tests pass without skips.

- [ ] **Step 4: Document commands and commit**

Add API, worker, beat commands and queue/state ownership to `AGENTS.md`.

```bash
git add docker-compose.integration.yml 08-YieldAgent/tests/integration/test_worker_process.py \
  08-YieldAgent/AGENTS.md
git commit -m "test(worker): verify process recovery"
```

## Primary References

- [Celery Redis broker and visibility timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [Celery configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
- [Redis XADD](https://redis.io/docs/latest/commands/xadd/)
- [Redis XREAD](https://redis.io/docs/latest/commands/xread/)
