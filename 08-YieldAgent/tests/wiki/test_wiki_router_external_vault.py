import importlib

import frontmatter
import httpx
import pytest
from fastapi import FastAPI


pytestmark = pytest.mark.no_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_node_endpoint_reads_explicit_external_vault(tmp_path, monkeypatch):
    vault = tmp_path / "YieldWiki"
    concepts = vault / "concepts"
    concepts.mkdir(parents=True)
    post = frontmatter.Post(
        content="## 외부 Vault 본문",
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        updated="2026-07-31T00:00:00",
    )
    (concepts / "4SS_PRE_METAL_CLN_EASY.md").write_text(
        frontmatter.dumps(post), encoding="utf-8"
    )
    monkeypatch.setenv("WIKI_VAULT_PATH", str(vault))

    import wiki_router

    wiki_router = importlib.reload(wiki_router)
    app = FastAPI()
    app.include_router(wiki_router.router, prefix="/api/wiki")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/wiki/node/concept:4SS%7CPRE%20METAL%20CLN%7CEASY"
        )

    assert response.status_code == 200
    assert response.json()["body_markdown"] == "## 외부 Vault 본문"
    assert wiki_router._VAULT == vault.resolve()
