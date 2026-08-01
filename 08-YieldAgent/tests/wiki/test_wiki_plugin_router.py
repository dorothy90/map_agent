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
async def test_plugin_health_accepts_matching_token(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(
        wiki_plugin_router,
        "plugin_dependency_status",
        lambda: {"backend": "ok", "opensearch": "ok", "llm": "configured"},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/health",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"]["opensearch"] == "ok"
