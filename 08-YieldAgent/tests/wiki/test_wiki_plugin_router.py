import httpx
import pytest
from fastapi import FastAPI
from wiki_config import initialize_wiki_vault, resolve_wiki_paths

import wiki_plugin_router
from models import PluginRelatedResponse, PluginSearchResponse, PluginSourceResponse

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


@pytest.mark.anyio
async def test_plugin_related_requires_token_and_maps_missing_note_to_404(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized = await client.get("/api/wiki/plugin/related/concepts/A.md")
        response = await client.get(
            "/api/wiki/plugin/related/concepts/A.md",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 404
    assert response.json()["detail"] == "Wiki 노트를 찾을 수 없습니다."


@pytest.mark.anyio
async def test_plugin_related_returns_navigation(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(
        wiki_plugin_router,
        "related_notes",
        lambda *args: PluginRelatedResponse(note_path="concepts/A.md"),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/related/concepts/A.md",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 200
    assert response.json()["note_path"] == "concepts/A.md"


@pytest.mark.anyio
async def test_plugin_source_maps_missing_source_to_404(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/sources/FH-404",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Source를 찾을 수 없습니다."


@pytest.mark.anyio
async def test_plugin_source_maps_invalid_source_metadata_to_404(monkeypatch, app, tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    (paths.sources / "FH-1.md").write_text(
        "---\n"
        "doc_id: FH-other\n"
        "type: source\n"
        "---\n"
        "# Source\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/sources/FH-1",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Source를 찾을 수 없습니다."


@pytest.mark.anyio
async def test_plugin_source_returns_metadata(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(
        wiki_plugin_router,
        "read_source",
        lambda *args: PluginSourceResponse(doc_id="FH-1", source_path="sources/FH-1.md"),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/plugin/sources/FH-1",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert response.status_code == 200
    assert response.json()["source_path"] == "sources/FH-1.md"


@pytest.mark.anyio
async def test_plugin_navigation_routes_read_the_configured_vault(monkeypatch, app, tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    (paths.concepts / "A.md").write_text("[[sources/FH-1|FH-1]]", encoding="utf-8")
    (paths.sources / "FH-1.md").write_text(
        "---\n"
        "doc_id: FH-1\n"
        "type: source\n"
        "download_url: https://internal/FH-1.pptx\n"
        "---\n"
        "# Source\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        related = await client.get(
            "/api/wiki/plugin/related/concepts/A.md",
            headers={"Authorization": "Bearer correct-token"},
        )
        source = await client.get(
            "/api/wiki/plugin/sources/FH-1",
            headers={"Authorization": "Bearer correct-token"},
        )

    assert related.status_code == 200
    assert related.json()["outgoing"][0]["path"] == "sources/FH-1.md"
    assert source.status_code == 200
    assert source.json()["download_url"] == "https://internal/FH-1.pptx"


@pytest.mark.anyio
async def test_plugin_review_routes_persist_to_configured_vault(
    monkeypatch, app, tmp_path
):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/wiki/plugin/reviews",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "target_concept_id": "concept:A",
                "reviewer": "operator-1",
                "comment": "확인 필요",
            },
        )
        listed = await client.get(
            "/api/wiki/plugin/reviews?status=pending",
            headers={"Authorization": "Bearer correct-token"},
        )
        updated = await client.patch(
            f"/api/wiki/plugin/reviews/{created.json()['id']}",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "status": "approved",
                "reviewer": "operator-2",
                "comment": "근거 확인",
                "expected_version": 1,
            },
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [review["id"] for review in listed.json()] == [created.json()["id"]]
    assert updated.status_code == 200
    assert updated.json()["status"] == "approved"
    assert updated.json()["version"] == 2


@pytest.mark.anyio
async def test_plugin_review_update_maps_missing_and_conflict(
    monkeypatch, app, tmp_path
):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.patch(
            "/api/wiki/plugin/reviews/review:missing",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "status": "approved",
                "reviewer": "operator-1",
                "expected_version": 1,
            },
        )
        created = await client.post(
            "/api/wiki/plugin/reviews",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "target_concept_id": "concept:A",
                "reviewer": "operator-1",
                "comment": "확인 필요",
            },
        )
        conflict = await client.patch(
            f"/api/wiki/plugin/reviews/{created.json()['id']}",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "status": "rejected",
                "reviewer": "operator-2",
                "expected_version": 7,
            },
        )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Review를 찾을 수 없습니다."
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Review가 다른 사용자에 의해 변경되었습니다."


@pytest.mark.anyio
async def test_plugin_review_update_rejects_resolved_status(monkeypatch, app, tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "resolve_wiki_paths", lambda: paths)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/api/wiki/plugin/reviews/review:any",
            headers={"Authorization": "Bearer correct-token"},
            json={
                "status": "resolved",
                "reviewer": "operator-1",
                "expected_version": 1,
            },
        )

    assert response.status_code == 422
