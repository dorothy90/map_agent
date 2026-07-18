from __future__ import annotations

import json
import logging
import shutil
from typing import Any

import httpx

from fastapi import APIRouter, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from common import get_oracle_pool_metrics


JOB_STATES = ("QUEUED", "RUNNING", "WAITING_INPUT", "SUCCEEDED", "FAILED", "CANCELLED")
SUBMISSION_RESULTS = ("accepted", "idempotent", "busy", "user_limit", "global_limit", "dispatch_failed")
RETRY_KINDS = ("failure", "lease", "reconcile")
CANCEL_RESULTS = ("requested", "terminal")
LEASE_RESULTS = ("renewed", "lost")
LLM_ERROR_CATEGORIES = ("timeout", "transport", "rate_limit", "server", "other")
JOB_OUTCOMES = ("QUEUED", "SUCCEEDED", "FAILED", "CANCELLED", "WAITING_INPUT", "IGNORED")


def _bounded(value: str, allowed: tuple[str, ...]) -> str:
    return value if value in allowed else "other"


class Metrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY):
        self.registry = registry
        self.submissions = Counter(
            "yield_job_submissions_total", "Job submissions", ["result"], registry=registry
        )
        self.queue_depth = Gauge(
            "yield_job_queue_depth", "Jobs in the analysis broker queue", registry=registry
        )
        self.queue_wait = Histogram(
            "yield_job_queue_wait_seconds", "Time from creation to worker claim", registry=registry
        )
        self.state_total = Gauge(
            "yield_job_state_total", "Durable jobs by state", ["state"], registry=registry
        )
        self.duration = Histogram(
            "yield_job_duration_seconds", "Worker job duration", ["outcome"], registry=registry
        )
        self.retries = Counter(
            "yield_job_retries_total", "Job retries", ["kind"], registry=registry
        )
        self.cancellations = Counter(
            "yield_job_cancellations_total", "Job cancellation operations", ["result"], registry=registry
        )
        self.sse_reconnects = Counter(
            "yield_job_sse_reconnects_total", "SSE requests with Last-Event-ID", registry=registry
        )
        self.leases = Counter(
            "yield_worker_lease_renewals_total", "Worker lease renewals", ["result"], registry=registry
        )
        self.llm_errors = Counter(
            "yield_llm_errors_total", "LLM failures", ["category"], registry=registry
        )
        self.nas_free = Gauge(
            "yield_nas_free_bytes", "Free bytes on the configured artifact mount", registry=registry
        )
        self.oracle_pool = Gauge(
            "yield_oracle_pool_connections", "Process-local Oracle pool values", ["kind"], registry=registry
        )
        self.scope = Gauge(
            "yield_metrics_scope_info", "Metrics are process-local unless multiprocess collection is configured", ["process"], registry=registry
        )
        self.scope.labels(process="api").set(1)
        for state in JOB_STATES:
            self.state_total.labels(state=state).set(0)

    def submission(self, result: str) -> None:
        self.submissions.labels(result=_bounded(result, SUBMISSION_RESULTS)).inc()

    def retry(self, kind: str) -> None:
        self.retries.labels(kind=_bounded(kind, RETRY_KINDS)).inc()

    def cancellation(self, result: str) -> None:
        self.cancellations.labels(result=_bounded(result, CANCEL_RESULTS)).inc()

    def lease(self, result: str) -> None:
        self.leases.labels(result=_bounded(result, LEASE_RESULTS)).inc()

    def llm_error(self, category: str) -> None:
        self.llm_errors.labels(category=_bounded(category, LLM_ERROR_CATEGORIES)).inc()

    def observe_duration(self, outcome: str, seconds: float) -> None:
        self.duration.labels(outcome=_bounded(outcome, JOB_OUTCOMES)).observe(seconds)


def record_llm_error(exc: Exception) -> None:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        category = "timeout"
    elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        category = "rate_limit"
    elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        category = "server"
    elif isinstance(exc, (ConnectionError, httpx.TransportError)):
        category = "transport"
    else:
        category = "other"
    metrics.llm_error(category)


metrics = Metrics()
router = APIRouter(tags=["operations"])
_worker_logger = logging.getLogger("yield_agent.worker")
_LOG_FIELDS = ("job_id", "task_id", "run_sequence", "attempt", "node", "owner_hash")


def log_worker_event(event: str, **fields: Any) -> None:
    payload = {"event": event}
    payload.update({name: fields[name] for name in _LOG_FIELDS if name in fields})
    _worker_logger.info(json.dumps(payload, separators=(",", ":"), default=str))


async def refresh_runtime_gauges(request: Request, current: Metrics) -> None:
    rows = await request.app.state.motor_db.jobs.aggregate(
        [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    ).to_list(length=16)
    counts = {row["_id"]: row["count"] for row in rows if row.get("_id") in JOB_STATES}
    for state in JOB_STATES:
        current.state_total.labels(state=state).set(counts.get(state, 0))
    current.queue_depth.set(await request.app.state.redis.llen("analysis"))
    current.nas_free.set(shutil.disk_usage(request.app.state.settings.artifact_root).free)
    pool = get_oracle_pool_metrics()
    for kind in ("busy", "open", "max"):
        current.oracle_pool.labels(kind=kind).set(pool[kind])


@router.get("/metrics")
async def prometheus_metrics(request: Request) -> Response:
    current = getattr(request.app.state, "metrics", metrics)
    await refresh_runtime_gauges(request, current)
    return Response(
        content=generate_latest(current.registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
