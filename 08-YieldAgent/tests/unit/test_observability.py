from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest

from observability import Metrics, log_worker_event, router


def test_worker_log_has_correlation_fields_without_sensitive_values(caplog):
    caplog.set_level(logging.INFO, logger="yield_agent.worker")
    sensitive = {
        "owner_id": "employee-123",
        "query": "secret product query",
        "rows": [{"LOT": "sensitive-lot"}],
        "html": "<html>secret report</html>",
        "mongo_uri": "mongodb://user:password@secret-host/db",
        "redis_uri": "redis://:password@secret-host/0",
        "secret": "top-secret",
    }

    log_worker_event(
        "job_started",
        job_id="job-1",
        task_id="task-1",
        run_sequence=2,
        attempt=3,
        node="yield_agent",
        owner_hash="hash-1",
        **sensitive,
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload == {
        "event": "job_started",
        "job_id": "job-1",
        "task_id": "task-1",
        "run_sequence": 2,
        "attempt": 3,
        "node": "yield_agent",
        "owner_hash": "hash-1",
    }
    rendered = caplog.text
    for value in ("employee-123", "secret product query", "sensitive-lot", "secret report", "secret-host", "top-secret"):
        assert value not in rendered


def test_metric_labels_are_bounded_and_do_not_include_identifiers():
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    metrics.submission("job-123")
    metrics.retry("secret-query")
    metrics.llm_error("mongodb://secret")
    rendered = generate_latest(registry).decode()

    assert 'result="other"' in rendered
    assert 'kind="other"' in rendered
    assert 'category="other"' in rendered
    assert "job-123" not in rendered
    assert "secret-query" not in rendered
    assert "mongodb://secret" not in rendered


class AggregateCursor:
    async def to_list(self, length):
        assert length == 16
        return [{"_id": "QUEUED", "count": 2}, {"_id": "SUCCEEDED", "count": 4}]


class Jobs:
    def aggregate(self, pipeline):
        assert pipeline == [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        return AggregateCursor()


class Redis:
    async def llen(self, queue):
        assert queue == "analysis"
        return 7


def test_metrics_endpoint_refreshes_runtime_gauges(tmp_path, monkeypatch):
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    app = FastAPI()
    app.state.metrics = metrics
    app.state.motor_db = SimpleNamespace(jobs=Jobs())
    app.state.redis = Redis()
    app.state.settings = SimpleNamespace(artifact_root=tmp_path)
    app.include_router(router)
    monkeypatch.setattr(
        "observability.shutil.disk_usage",
        lambda _: SimpleNamespace(free=123456),
    )
    monkeypatch.setattr(
        "observability.get_oracle_pool_metrics",
        lambda: {"busy": 1, "open": 2, "max": 4},
    )

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "yield_job_queue_depth 7.0" in text
    assert 'yield_job_state_total{state="QUEUED"} 2.0' in text
    assert 'yield_job_state_total{state="SUCCEEDED"} 4.0' in text
    assert "yield_nas_free_bytes 123456.0" in text
    assert 'yield_oracle_pool_connections{kind="busy"} 1.0' in text
    assert 'yield_metrics_scope_info{process="api"} 1.0' in text
