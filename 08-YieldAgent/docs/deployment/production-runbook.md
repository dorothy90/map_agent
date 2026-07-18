# Yield Agent Production Runbook

## Workloads

Build the repository-root `Dockerfile` once and deploy the immutable image digest to
four workload types. Override the image command exactly as follows:

```text
API:     uvicorn agent_server:app --host 0.0.0.0 --port 8000
Worker:  celery -A celery_app.celery_app worker -Q analysis --concurrency=4 --loglevel=INFO
Beat:    celery -A celery_app.celery_app beat --loglevel=INFO
Cleanup: python artifact_cleanup.py --older-than-days 30
```

Initial sizing is 2 API replicas, 3 worker replicas, exactly 1 beat replica, and one
daily cleanup CronJob. Do not autoscale beat or run overlapping cleanup jobs. The API
container listens on TCP 8000. Worker, beat, and cleanup expose no client port.

Mount the shared NAS read/write at `/mnt/yield-agent` in API, worker, and cleanup
workloads. The mount path must be identical in every replica. Beat does not need the
mount. Do not use pod-local storage for uploads or artifacts.

Use a termination grace period of at least 2,100 seconds for workers. This exceeds the
1,740-second Celery soft limit and 1,800-second hard limit. Give API at least 30 seconds
to drain HTTP/SSE connections. Use a disruption budget so voluntary maintenance does
not stop all workers at once.

## Configuration and Secrets

Set these non-secret variables on every applicable workload:

| Variable | Production value or rule | Workload |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | all |
| `MONGO_DB` | dedicated database, normally `yield_agent` | all |
| `ARTIFACT_ROOT` | `/mnt/yield-agent`, absolute and identical | API, worker, cleanup |
| `PLATFORM_USER_ID_HEADER` | exact header injected by the gateway, normally `user_id` | API |
| `CORS_ORIGINS` | JSON list of exact frontend origins, no wildcard | API |
| `USER_JOB_LIMIT` | `2` initially | API, worker, beat |
| `GLOBAL_JOB_LIMIT` | `100` initially | API, worker, beat |
| `WORKER_LEASE_SECONDS` | `60` initially | worker, beat |
| `RECONCILE_INTERVAL_SECONDS` | `60` initially | beat |
| `ORACLE_POOL_MIN` | `1` initially | worker |
| `ORACLE_POOL_MAX` | capacity formula below; `4` initially | worker |
| `ORACLE_POOL_INCREMENT` | `1` initially | worker |
| `ENABLE_LEGACY_CHAT` | `false` | API |
| `ENABLE_REPL` | `false` | API |
| `ENABLE_WIKI` | `false` | API |
| `ENABLE_LOCAL_TRACE` | `false` | all |
| `LOG_LEVEL` | `INFO` | all |

Deliver these through platform Secrets, never an image layer, manifest, or ConfigMap:

| Secret variable | Purpose | Workload |
| --- | --- | --- |
| `MONGO_URI` | durable jobs and LangGraph checkpoints | all |
| `REDIS_URL` | broker, admission counters, cancellation, SSE streams | all |
| `OWNER_HASH_KEY` | HMAC key for stable owner hashes | API, worker |
| `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN` | business database | worker |
| `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY` | internal/approved LLM endpoint | worker |

`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` are optional Secret
settings when Langfuse tracing is enabled. Rotate `OWNER_HASH_KEY` only with an explicit
owner-hash migration plan because existing job ownership uses its derived value.

Before deployment, replace the example origin in `.env.example`. For example, if the
only frontend is `https://yield.internal.example`, set exactly:

```text
CORS_ORIGINS=["https://yield.internal.example"]
```

Do not add `*`, guessed aliases, or direct FastAPI origins.

## Required Services and Network Policy

- MongoDB must be persistent and reachable by API, worker, beat, and cleanup.
- Redis must use persistent storage with AOF enabled (`appendonly yes`). Confirm AOF
  after restart with `redis-cli CONFIG GET appendonly`; the returned value must be
  `yes`. Redis is a single logical deployment for the current Lua admission scripts.
- Oracle and the LLM endpoint are reachable from workers only.
- The NAS is reachable from API, worker, and cleanup at the identical absolute path.
- Only the authenticated gateway may reach FastAPI TCP 8000. Deny direct client,
  frontend-pod, and public ingress access to the FastAPI service. Permit API egress to
  MongoDB, Redis, and NAS; worker egress to those plus Oracle and LLM; beat egress to
  MongoDB and Redis; cleanup egress to MongoDB and NAS.

The authentication gateway must remove every client-supplied instance of the configured
identity header before injecting the authenticated platform identity. Verify both an
underscore name (`user_id`) and the actual ingress-normalized name end to end. A request
through the gateway with a forged header must still resolve to the logged-in platform
user; a request that bypasses the gateway must be blocked at the network layer.

## Probes and Capacity

Use `GET /health/live` for liveness and `GET /health/ready` for API readiness. Readiness
checks Redis, MongoDB, and NAS and must leave the pod out of service on HTTP 503.
`GET /health/dependencies` requires platform identity and returns sanitized status;
Oracle and LLM are worker-only probes. Monitor worker heartbeat separately from API
health. Scrape `GET /metrics` only from the internal monitoring network.

Obtain the Oracle connection allowance `B` from the database team after reserving
connections for all other applications and maintenance. With `R` worker replicas, `C`
Celery processes per replica, and process-local `ORACLE_POOL_MAX=P`, require:

```text
R × C × P <= B
```

At the initial `R=3`, `C=4`, `P=4`, the worst-case budget is 48 Oracle connections.
Reduce `P`, replicas, or concurrency if the approved budget is lower. Increase worker
capacity only after measuring memory, CPU, queue wait, Oracle occupancy, LLM throttling,
and NAS latency.

Alert on sustained queue depth/wait, job failures/retries, lease expiry, missing worker
heartbeats, Celery task latency, Redis or MongoDB probe failure, Oracle pool saturation
or errors, LLM timeouts/rate limits, NAS free space/latency, cleanup failures, API 5xx,
and readiness failure. Never put raw identities, queries, rows, artifact IDs, session
IDs, or credentials in metric labels.

## Deploy

1. Verify Secrets, exact CORS origin, gateway stripping/injection, network policy,
   Redis AOF, MongoDB persistence, NAS mount, Oracle budget, and LLM rate limits.
2. Build and scan one image, then pin its digest for every workload.
3. Deploy MongoDB/Redis dependencies and verify persistence and connectivity.
4. Deploy the cleanup CronJob definition without starting an ad-hoc cleanup.
5. Deploy workers, confirm heartbeat and worker-only Oracle/LLM/NAS probes.
6. Deploy exactly one beat replica and confirm one reconciler schedule.
7. Deploy API replicas; wait for `/health/ready` on both before routing traffic.
8. Exercise one authenticated job, SSE reconnect, artifact download, and owner-isolation
   check through the gateway. Then enable frontend traffic and watch alerts/metrics.

## Drain and Rollback

For a planned worker rollout, stop new job admission at the gateway, scale no workers
down yet, and wait until queued/running/waiting jobs reach terminal states or are
explicitly cancelled. Stop beat only after new admission is closed. Replace workers one
at a time within the 2,100-second grace period, then restore beat and admission. Do not
purge the Redis queue or delete MongoDB jobs/checkpoints during a drain.

For rollback, first route the frontend to the previous compatible API while blocking
new admission to the failed version. Keep the current workers and beat alive until every
already accepted job terminates or is explicitly cancelled; queued work must remain in
Redis. Deploy the previous worker image only with a verified MongoDB/checkpoint/event
schema compatibility path. Roll back API replicas, verify readiness and ownership, then
restore admission. A rollback never deletes NAS artifacts, checkpoints, or durable job
records; retention cleanup remains the only deletion path.
