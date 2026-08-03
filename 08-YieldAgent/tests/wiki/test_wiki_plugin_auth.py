import httpx
import pytest
from fastapi import FastAPI

import wiki_plugin_router

pytestmark = pytest.mark.no_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(wiki_plugin_router.router, prefix="/api/wiki/plugin")
    return app


@pytest.mark.anyio
async def test_plugin_routes_fail_closed_when_token_is_unset(monkeypatch, app):
    monkeypatch.delenv("OBSIDIAN_PLUGIN_API_TOKEN", raising=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/wiki/plugin/health")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_plugin_routes_reject_wrong_token(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/health",
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert response.status_code == 401
