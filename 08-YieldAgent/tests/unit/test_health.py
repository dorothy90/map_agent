import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from health_router import router
from identity import get_platform_identity


class RedisProbe:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    async def ping(self):
        self.calls += 1
        if self.error:
            raise self.error
        return True


class MongoProbe:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def command(self, name: str):
        assert name == "ping"
        if self.error:
            raise self.error
        return {"ok": 1}


def make_app(tmp_path: Path, *, redis_error=None, mongo_error=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.redis = RedisProbe(redis_error)
    app.state.motor_db = MongoProbe(mongo_error)
    app.state.settings = SimpleNamespace(artifact_root=tmp_path)
    app.dependency_overrides[get_platform_identity] = lambda: object()
    return app


@pytest.mark.asyncio
async def test_liveness_does_not_probe_dependencies(tmp_path):
    app = make_app(
        tmp_path,
        redis_error=RuntimeError("redis://user:password@secret-host"),
        mongo_error=RuntimeError("mongodb://user:password@secret-host"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert app.state.redis.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["redis", "mongo", "nas"])
async def test_readiness_is_sanitized_and_fails_for_each_dependency(tmp_path, failed):
    root = tmp_path / "artifacts"
    root.mkdir()
    redis_error = (
        RuntimeError("redis://user:password@secret-host")
        if failed == "redis"
        else None
    )
    mongo_error = (
        RuntimeError("mongodb://user:password@secret-host")
        if failed == "mongo"
        else None
    )
    if failed == "nas":
        root = tmp_path / "missing"
    app = make_app(root, redis_error=redis_error, mongo_error=mongo_error)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert set(body["components"]) == {"redis", "mongo", "nas"}
    assert body["components"][failed] == "failed"
    rendered = response.text
    assert "password" not in rendered
    assert "secret-host" not in rendered
    assert "redis://" not in rendered
    assert "mongodb://" not in rendered


@pytest.mark.asyncio
async def test_readiness_bounds_each_dependency_probe(tmp_path):
    class SlowRedis:
        async def ping(self):
            await asyncio.sleep(10)

    app = make_app(tmp_path)
    app.state.redis = SlowRedis()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["redis"] == "failed"


@pytest.mark.asyncio
async def test_dependencies_requires_platform_identity_and_is_sanitized(tmp_path):
    app = make_app(tmp_path)
    app.dependency_overrides.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/dependencies")

    assert response.status_code == 401

    app.dependency_overrides[get_platform_identity] = lambda: object()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json()["components"]["oracle"] == "worker_only"
    assert response.json()["components"]["llm"] == "worker_only"
    assert "://" not in response.text
