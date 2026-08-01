import httpx
import pytest
from fastapi import FastAPI

import wiki_plugin_router
from models import PluginSearchResponse

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


@pytest.mark.anyio
async def test_plugin_search_requires_token_and_returns_grouped_results(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(
        wiki_plugin_router,
        "search_wiki",
        lambda *args, **kwargs: PluginSearchResponse(
            query="oxide",
            retrieval_mode="hybrid",
            results=[],
        ),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized = await client.get("/api/wiki/plugin/search?q=oxide")
        response = await client.get(
            "/api/wiki/plugin/search?q=oxide&limit=20",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["query"] == "oxide"
    assert response.json()["retrieval_mode"] == "hybrid"


@pytest.mark.anyio
async def test_plugin_search_maps_backend_failure_to_502(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")

    def fail_search(*args, **kwargs):
        raise RuntimeError("OpenSearch unavailable")

    monkeypatch.setattr(wiki_plugin_router, "search_wiki", fail_search)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/search?q=oxide",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "OpenSearch 검색에 실패했습니다."
