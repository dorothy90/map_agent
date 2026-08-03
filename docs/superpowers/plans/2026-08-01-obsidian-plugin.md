# Yield Wiki Obsidian Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Fail History Wiki, OpenSearch, Agent SSE를 Obsidian Desktop의 Chat/Search/Review sidebar로 연결한다.

**Architecture:** FastAPI에 Bearer 인증을 적용한 `/api/wiki/plugin` adapter를 추가하고 기존 Wiki/OpenSearch/Agent session을 재사용한다. Obsidian Plugin은 REST에 `requestUrl`, Chat SSE에 desktop Node HTTP stream을 사용하며 Concept·Source Markdown을 직접 수정하지 않는다.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, python-frontmatter, OpenSearch client, pytest, TypeScript 5.7, Obsidian API 1.8.7, esbuild, Vitest, jsdom, Node HTTP/HTTPS

## Global Constraints

- 대상 worktree는 `/Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform`, branch는 `feat/obsidian-wiki-platform`이다.
- Backend 기본 URL은 `http://localhost:8001`, 실제 Vault는 `/Users/daehwankim/SYLDAIX/YieldWiki`다.
- Obsidian Desktop `1.9.14`에서 검증하고 `manifest.json`은 `minAppVersion: 1.8.7`, `isDesktopOnly: true`다.
- 모든 `/api/wiki/plugin/*` endpoint는 `OBSIDIAN_PLUGIN_API_TOKEN` Bearer 인증을 요구하며 token 미설정 시 fail-closed다.
- Plugin 설정은 `serverUrl`, `apiToken`만 local `data.json`에 저장한다. 마지막 대화는 인증된 Session API의 최신 항목으로 복구하며 token을 Markdown, log, build artifact, Git에 넣지 않는다.
- Plugin은 Concept·Source·MOC Markdown을 직접 쓰지 않는다. Review만 Backend가 기존 `reviews/*.md`에 쓴다.
- 검색은 Wiki를 생성하거나 sync job을 enqueue하지 않는다. `bootstrap_wiki`와 `sync_wiki`의 책임은 유지한다.
- 기존 `/chat/stream`, `/sessions`, `/api/wiki/*`와 기존 frontend는 제거하거나 계약을 깨지 않는다.
- 자연어 문구의 keyword/regex/special-case로 context나 Citation을 추론하지 않는다. 구조화 state와 canonical metadata만 사용한다.
- lint와 단위 테스트만으로 완료하지 않고 실제 OpenSearch, LLM, Backend, Obsidian Desktop E2E를 수행한다.

---

## File Map

### Backend

- Create `08-YieldAgent/wiki_plugin_auth.py`: Bearer token dependency
- Create `08-YieldAgent/wiki_plugin_search.py`: hybrid retrieval mode와 Concept 중심 grouping
- Create `08-YieldAgent/wiki_plugin_notes.py`: 안전한 Vault note 해석, related/backlink, Source lookup
- Create `08-YieldAgent/wiki_review_store.py`: Review Markdown optimistic concurrency와 history
- Create `08-YieldAgent/agent_sessions.py`: 기존/Plugin route가 공유하는 Mongo session read service
- Create `08-YieldAgent/wiki_plugin_router.py`: Plugin REST/SSE adapter
- Modify `08-YieldAgent/fail_history_tools.py`: 기존 검색 계약을 보존하는 mode-aware retrieval helper
- Modify `08-YieldAgent/models.py`: Plugin request/response, structured Citation 모델
- Modify `08-YieldAgent/query_state.py`: per-turn `wiki_context` state
- Modify `08-YieldAgent/node_planner.py`: current Wiki note를 structured context로 제공
- Modify `08-YieldAgent/agent_server.py`: Plugin router mount, shared Chat handler, additive Citation/session data

### Backend tests

- Create `08-YieldAgent/tests/wiki/test_wiki_plugin_auth.py`
- Create `08-YieldAgent/tests/wiki/test_wiki_plugin_search.py`
- Create `08-YieldAgent/tests/wiki/test_wiki_plugin_notes.py`
- Create `08-YieldAgent/tests/wiki/test_wiki_review_store.py`
- Create `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`
- Create `08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py`

### Obsidian Plugin

- Create `obsidian/plugin/manifest.json`: Plugin metadata
- Create `obsidian/plugin/package.json`, `package-lock.json`: exact build/test dependencies
- Create `obsidian/plugin/tsconfig.json`, `esbuild.config.mjs`: desktop Plugin build
- Create `obsidian/plugin/src/types.ts`: API/SSE types
- Create `obsidian/plugin/src/api.ts`: authenticated REST와 Node SSE client
- Create `obsidian/plugin/src/settings.ts`: local URL/token settings UI
- Create `obsidian/plugin/src/view.ts`: Chat/Search/Review sidebar
- Create `obsidian/plugin/src/main.ts`: Plugin lifecycle, command, right leaf activation
- Create `obsidian/plugin/styles.css`: Obsidian theme-variable-based styling
- Create `obsidian/plugin/scripts/install-vault.mjs`: build artifact만 Vault에 설치
- Create `obsidian/plugin/tests/api.test.ts`, `view.test.ts`, `install-vault.test.ts`
- Create `08-YieldAgent/docs/wiki-m4-e2e-results.md`: 실제 실행 결과와 미완료 항목

---

### Task 1: Plugin 인증과 Health 경계

**Files:**
- Create: `08-YieldAgent/wiki_plugin_auth.py`
- Create: `08-YieldAgent/wiki_plugin_router.py`
- Modify: `08-YieldAgent/agent_server.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_auth.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`

**Interfaces:**
- Produces: `require_plugin_token(credentials) -> None`
- Produces: `router = APIRouter(dependencies=[Depends(require_plugin_token)])`
- Produces: `GET /api/wiki/plugin/health -> {status, dependencies}`

- [ ] **Step 1: Write failing auth and health tests**

```python
def test_plugin_routes_fail_closed_when_token_is_unset(monkeypatch, app):
    monkeypatch.delenv("OBSIDIAN_PLUGIN_API_TOKEN", raising=False)
    response = TestClient(app).get("/api/wiki/plugin/health")
    assert response.status_code == 401


def test_plugin_routes_reject_wrong_token(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    response = TestClient(app).get(
        "/api/wiki/plugin/health",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_plugin_health_accepts_matching_token(monkeypatch, app):
    monkeypatch.setenv("OBSIDIAN_PLUGIN_API_TOKEN", "correct-token")
    monkeypatch.setattr(wiki_plugin_router, "plugin_dependency_status", lambda: {
        "backend": "ok", "opensearch": "ok", "llm": "configured",
    })
    response = TestClient(app).get(
        "/api/wiki/plugin/health",
        headers={"Authorization": "Bearer correct-token"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"]["opensearch"] == "ok"
```

- [ ] **Step 2: Run tests and verify the route does not exist yet**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_auth.py tests/wiki/test_wiki_plugin_router.py`

Expected: FAIL because `wiki_plugin_auth` and `wiki_plugin_router` do not exist.

- [ ] **Step 3: Implement timing-safe fail-closed authentication**

```python
# wiki_plugin_auth.py
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def require_plugin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    configured = os.getenv("OBSIDIAN_PLUGIN_API_TOKEN", "")
    supplied = credentials.credentials if credentials else ""
    valid_scheme = bool(credentials and credentials.scheme.lower() == "bearer")
    if not configured or not valid_scheme or not secrets.compare_digest(configured, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Obsidian Plugin 인증에 실패했습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

```python
# wiki_plugin_router.py
import os

from fastapi import APIRouter, Depends

from wiki_plugin_auth import require_plugin_token

router = APIRouter(dependencies=[Depends(require_plugin_token)])


def plugin_dependency_status() -> dict[str, str]:
    try:
        from fail_history_tools import _get_opensearch_client
        opensearch = "ok" if _get_opensearch_client().ping() else "unavailable"
    except Exception:
        opensearch = "unavailable"
    llm = "configured" if os.getenv("OPENROUTER_API_KEY", "") else "unconfigured"
    return {"backend": "ok", "opensearch": opensearch, "llm": llm}


@router.get("/health")
def plugin_health() -> dict:
    return {"status": "ok", "dependencies": plugin_dependency_status()}
```

Mount it in `agent_server.py` without changing the existing wiki router:

```python
from wiki_plugin_router import router as wiki_plugin_router

app.include_router(wiki_plugin_router, prefix="/api/wiki/plugin", tags=["wiki-plugin"])
```

- [ ] **Step 4: Run focused tests**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_auth.py tests/wiki/test_wiki_plugin_router.py`

Expected: all tests PASS; missing, wrong, and unset token return `401`.

- [ ] **Step 5: Commit authentication boundary**

```bash
git add 08-YieldAgent/wiki_plugin_auth.py 08-YieldAgent/wiki_plugin_router.py 08-YieldAgent/agent_server.py 08-YieldAgent/tests/wiki/test_wiki_plugin_auth.py 08-YieldAgent/tests/wiki/test_wiki_plugin_router.py
git commit -m "feat: secure Obsidian plugin API"
```

---

### Task 2: Concept 중심 OpenSearch 검색

**Files:**
- Create: `08-YieldAgent/wiki_plugin_search.py`
- Modify: `08-YieldAgent/fail_history_tools.py`
- Modify: `08-YieldAgent/models.py`
- Modify: `08-YieldAgent/wiki_plugin_router.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_search.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`

**Interfaces:**
- Produces: `search_opensearch_with_mode(..., allow_embedding_fallback: bool) -> tuple[list[dict], str]`
- Produces: `search_wiki(query, product, fail_type, cause_oper, limit, paths) -> PluginSearchResponse`
- Consumes: `wiki_sync.make_triple_key(product, fail_type, cause_oper)`

- [ ] **Step 1: Write failing retrieval-mode and grouping tests**

```python
def test_embedding_failure_uses_bm25_and_marks_fallback(monkeypatch):
    monkeypatch.setattr(fail_history_tools, "_get_embedding", lambda query: (_ for _ in ()).throw(RuntimeError("embedding offline")))
    monkeypatch.setattr(fail_history_tools, "_search_bm25", lambda **kwargs: [{"doc_id": "FH-1"}])
    results, mode = fail_history_tools.search_opensearch_with_mode(
        "oxide", product="4SS", fail_type="EASY", cause_oper="PRE METAL CLN",
        top_k=5, allow_embedding_fallback=True,
    )
    assert results == [{"doc_id": "FH-1"}]
    assert mode == "bm25_fallback"


def test_search_groups_hits_under_materialized_concept(tmp_path, monkeypatch):
    paths = make_vault_with_concept(tmp_path, "4SS", "EASY", "PRE METAL CLN")
    monkeypatch.setattr(wiki_plugin_search, "search_opensearch_with_mode", lambda *args, **kwargs: ([
        {"doc_id": "FH-1", "product": "4SS", "fail_type": "EASY(W)", "cause_oper": "PRE METAL CLN", "content": "oxide", "score": 88.0},
        {"doc_id": "FH-2", "product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN", "content": "clean", "score": 72.0},
    ], "hybrid"))
    result = wiki_plugin_search.search_wiki("oxide", "4SS", "EASY", "PRE METAL CLN", 20, paths)
    assert len(result.results) == 1
    assert result.results[0].concept_status == "materialized"
    assert result.results[0].concept_path == "concepts/4SS_PRE_METAL_CLN_EASY.md"
    assert [item.doc_id for item in result.results[0].evidence] == ["FH-1", "FH-2"]


def test_search_without_concept_is_read_only(tmp_path, monkeypatch):
    paths = make_empty_vault(tmp_path)
    before = snapshot(paths.root)
    monkeypatch.setattr(wiki_plugin_search, "search_opensearch_with_mode", lambda *args, **kwargs: ([
        {"doc_id": "FH-9", "product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN", "content": "source", "score": 50.0},
    ], "hybrid"))
    result = wiki_plugin_search.search_wiki("source", "4SS", "EASY", "PRE METAL CLN", 20, paths)
    assert result.results[0].concept_status == "source_only"
    assert snapshot(paths.root) == before
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_search.py`

Expected: FAIL because mode-aware search and Plugin grouping do not exist.

- [ ] **Step 3: Refactor existing search without changing its return contract**

Implement `_search_bm25(...)` from the existing BM25 query construction and add:

```python
def search_opensearch_with_mode(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
    *,
    allow_embedding_fallback: bool = False,
) -> tuple[List[Dict[str, Any]], str]:
    try:
        embedding = _get_embedding(_expand_acronyms(query))
    except Exception:
        if not allow_embedding_fallback:
            raise
        return _search_bm25(
            query=query, product=product, fail_type=fail_type,
            cause_oper=cause_oper, top_k=top_k,
        ), "bm25_fallback"
    return _search_opensearch_with_embedding(
        query=query, embedding=embedding, product=product,
        fail_type=fail_type, cause_oper=cause_oper, top_k=top_k,
    )


def _search_opensearch(query: str, product: str = "", fail_type: str = "", cause_oper: str = "", top_k: int = 5) -> List[Dict[str, Any]]:
    results, _ = search_opensearch_with_mode(
        query, product, fail_type, cause_oper, top_k,
        allow_embedding_fallback=False,
    )
    return results
```

`_search_opensearch_with_embedding`은 hybrid 성공 시 `hybrid`, 기존 hybrid search-phase 오류 후 BM25 성공 시 `bm25_fallback`을 반환한다. OpenSearch 연결 오류는 삼키지 않는다.

- [ ] **Step 4: Implement typed Concept grouping**

Add Pydantic contracts to `models.py`:

```python
class PluginEvidence(BaseModel):
    doc_id: str = ""
    content: str = ""
    cause: str = ""
    action: str = ""
    comment: str = ""
    source_file: str = ""
    date: str = ""
    score: float = 0.0
    source_path: str | None = None
    download_url: str = ""


class PluginSearchResult(BaseModel):
    concept_id: str | None = None
    concept_path: str | None = None
    concept_status: Literal["materialized", "source_only"]
    product: str
    fail_type: str
    cause_oper: str
    retrieval_mode: Literal["hybrid", "bm25_fallback"]
    score: float
    evidence: list[PluginEvidence] = Field(default_factory=list)


class PluginSearchResponse(BaseModel):
    query: str
    retrieval_mode: Literal["hybrid", "bm25_fallback"]
    results: list[PluginSearchResult] = Field(default_factory=list)
```

In `wiki_plugin_search.py`, build the Concept map by scanning `paths.concepts`, normalize triples only through `make_triple_key`, group hits by `TripleKey.canonical`, preserve descending score order, and attach a Source path only when the generated Source Markdown actually exists.

- [ ] **Step 5: Add authenticated search route and error mapping**

```python
@router.get("/search", response_model=PluginSearchResponse)
def plugin_search(
    q: str = Query(..., min_length=1),
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    limit: int = Query(20, ge=1, le=100),
) -> PluginSearchResponse:
    try:
        return search_wiki(q, product, fail_type, cause_oper, limit, resolve_wiki_paths())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="OpenSearch 검색에 실패했습니다.") from exc
```

- [ ] **Step 6: Run focused and existing Fail History tests**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_search.py tests/wiki/test_wiki_plugin_router.py tests/wiki/test_wiki_sync_scanner.py`

Expected: all tests PASS; old `_search_opensearch` still returns `list[dict]`.

- [ ] **Step 7: Commit search adapter**

```bash
git add 08-YieldAgent/fail_history_tools.py 08-YieldAgent/models.py 08-YieldAgent/wiki_plugin_search.py 08-YieldAgent/wiki_plugin_router.py 08-YieldAgent/tests/wiki/test_wiki_plugin_search.py 08-YieldAgent/tests/wiki/test_wiki_plugin_router.py
git commit -m "feat: add Concept-first plugin search"
```

---

### Task 3: 안전한 Note, Related, Source API

**Files:**
- Create: `08-YieldAgent/wiki_plugin_notes.py`
- Modify: `08-YieldAgent/models.py`
- Modify: `08-YieldAgent/wiki_plugin_router.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_notes.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`

**Interfaces:**
- Produces: `load_note_context(paths, note_path, max_body_chars=20000) -> dict`
- Produces: `related_notes(paths, note_path) -> PluginRelatedResponse`
- Produces: `read_source(paths, doc_id) -> PluginSourceResponse`

- [ ] **Step 1: Write failing safe-path and graph tests**

```python
def test_note_context_rejects_vault_escape(paths):
    with pytest.raises(NoteNotFound):
        load_note_context(paths, "../secret.md")


def test_note_context_rejects_symlink_escape(paths, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (paths.concepts / "escape.md").symlink_to(outside)
    with pytest.raises(NoteNotFound):
        load_note_context(paths, "concepts/escape.md")


def test_related_uses_wikilinks_and_backlinks(paths):
    write_note(paths.root / "concepts/A.md", "[[sources/FH-1|FH-1]]")
    write_note(paths.root / "operations/OP.md", "[[concepts/A|A]]")
    write_note(paths.root / "sources/FH-1.md", "source")
    result = related_notes(paths, "concepts/A.md")
    assert [item.path for item in result.outgoing] == ["sources/FH-1.md"]
    assert [item.path for item in result.backlinks] == ["operations/OP.md"]


def test_source_returns_only_existing_values(paths):
    write_source(paths.sources / "FH-1.md", doc_id="FH-1", download_url="https://internal/FH-1.pptx")
    result = read_source(paths, "FH-1")
    assert result.source_path == "sources/FH-1.md"
    assert result.download_url == "https://internal/FH-1.pptx"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_notes.py`

Expected: FAIL because `wiki_plugin_notes` does not exist.

- [ ] **Step 3: Implement canonical Markdown resolution**

Add the response contracts to `models.py`:

```python
class PluginNoteLink(BaseModel):
    path: str
    label: str
    node_type: str = ""


class PluginRelatedResponse(BaseModel):
    note_path: str
    outgoing: list[PluginNoteLink] = Field(default_factory=list)
    backlinks: list[PluginNoteLink] = Field(default_factory=list)


class PluginSourceResponse(BaseModel):
    doc_id: str
    source_path: str
    source_file: str = ""
    date: str = ""
    page_num: int | None = None
    download_url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
```

```python
def resolve_markdown_path(paths: WikiPaths, note_path: str) -> Path:
    relative = PurePosixPath(note_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise NoteNotFound(note_path)
    if relative.suffix == "":
        relative = relative.with_suffix(".md")
    if relative.suffix.lower() != ".md":
        raise NoteNotFound(note_path)
    candidate = paths.root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise NoteNotFound(note_path) from exc
    if not resolved.is_relative_to(paths.root) or not resolved.is_file():
        raise NoteNotFound(note_path)
    return resolved
```

Parse only Markdown wikilink syntax, never natural-language names:

```python
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def extract_wikilinks(body: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1).strip() for match in _WIKILINK.finditer(body)}))
```

Resolve outgoing links and scan Markdown files for backlinks. Return only existing `.md` targets and Vault-relative POSIX paths.

- [ ] **Step 4: Add Related and Source routes**

```python
@router.get("/related/{note_path:path}", response_model=PluginRelatedResponse)
def plugin_related(note_path: str) -> PluginRelatedResponse:
    try:
        return related_notes(resolve_wiki_paths(), note_path)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Wiki 노트를 찾을 수 없습니다.") from exc


@router.get("/sources/{doc_id}", response_model=PluginSourceResponse)
def plugin_source(doc_id: str) -> PluginSourceResponse:
    try:
        return read_source(resolve_wiki_paths(), doc_id)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Source를 찾을 수 없습니다.") from exc
```

- [ ] **Step 5: Run note, router, and external Vault tests**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_notes.py tests/wiki/test_wiki_plugin_router.py tests/wiki/test_wiki_router_external_vault.py tests/wiki/test_wiki_store_external_vault.py`

Expected: all tests PASS and no file outside the temporary Vault is read.

- [ ] **Step 6: Commit note APIs**

```bash
git add 08-YieldAgent/wiki_plugin_notes.py 08-YieldAgent/models.py 08-YieldAgent/wiki_plugin_router.py 08-YieldAgent/tests/wiki/test_wiki_plugin_notes.py 08-YieldAgent/tests/wiki/test_wiki_plugin_router.py
git commit -m "feat: expose safe Wiki note navigation"
```

---

### Task 4: Review Markdown optimistic concurrency

**Files:**
- Create: `08-YieldAgent/wiki_review_store.py`
- Modify: `08-YieldAgent/models.py`
- Modify: `08-YieldAgent/wiki_plugin_router.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_review_store.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`

**Interfaces:**
- Produces: `WikiReviewStore.list(status=None) -> list[PluginReview]`
- Produces: `WikiReviewStore.create(request) -> PluginReview`
- Produces: `WikiReviewStore.update(review_id, request) -> PluginReview`
- Raises: `ReviewNotFound`, `ReviewConflict`

- [ ] **Step 1: Write failing persistence and conflict tests**

```python
def test_existing_m3_review_defaults_to_version_one(store, paths):
    write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    review = store.list(status="pending")[0]
    assert review.version == 1
    assert review.review_type == "source_removal"


def test_update_appends_history_and_preserves_metadata(store, paths):
    write_existing_source_removal_review(paths.reviews / "source_removal_a.md", extra={"missing_doc_ids": ["FH-1"]})
    updated = store.update("review:source-removal:a", PluginReviewUpdate(
        status="approved", reviewer="operator-1", comment="근거 확인", expected_version=1,
    ))
    assert updated.version == 2
    post = frontmatter.load(paths.reviews / "source_removal_a.md")
    assert post.metadata["missing_doc_ids"] == ["FH-1"]
    assert post.metadata["history"][-1]["to_status"] == "approved"
    assert "<!-- yield-wiki:review-history:start -->" in post.content


def test_stale_expected_version_does_not_write(store, paths):
    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    before = path.read_bytes()
    with pytest.raises(ReviewConflict):
        store.update("review:source-removal:a", PluginReviewUpdate(
            status="rejected", reviewer="operator-2", comment="재검토", expected_version=7,
        ))
    assert path.read_bytes() == before
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_review_store.py`

Expected: FAIL because `WikiReviewStore` does not exist.

- [ ] **Step 3: Implement locked atomic Review updates**

Use `fcntl.flock` on `paths.state_dir / "reviews.lock"` and the existing temp-file/`os.replace` pattern. Inside the exclusive lock, locate the Review by frontmatter `id`, reload it, compare `expected_version`, append a typed history object, increment version, render the managed history block, and atomically replace the same file.

Add the exact API contracts to `models.py`:

```python
ReviewStatus = Literal["pending", "approved", "rejected", "resolved"]


class PluginReviewHistory(BaseModel):
    changed_at: str
    from_status: ReviewStatus
    to_status: ReviewStatus
    reviewer: str
    comment: str = ""


class PluginReview(BaseModel):
    id: str
    review_type: str
    status: ReviewStatus
    target_concept_id: str
    version: int
    created: str
    updated: str
    body_markdown: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    history: list[PluginReviewHistory] = Field(default_factory=list)


class PluginReviewCreate(BaseModel):
    target_concept_id: str
    reviewer: str
    comment: str
    review_type: str = "operator_feedback"


class PluginReviewUpdate(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer: str
    comment: str = ""
    expected_version: int = Field(ge=1)
```

```python
@contextmanager
def _review_lock(paths: WikiPaths):
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with (paths.state_dir / "reviews.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
```

Only `approved` and `rejected` are accepted by `PluginReviewUpdate`; `resolved` remains readable but M4 does not set it. Store reviewer and comment as frontmatter data, then render escaped plain text from that structured history.

- [ ] **Step 4: Add Review routes and exact error mapping**

```python
@router.get("/reviews", response_model=list[PluginReview])
def plugin_reviews(status: ReviewStatus | None = None) -> list[PluginReview]:
    return WikiReviewStore(resolve_wiki_paths()).list(status=status)


@router.post("/reviews", response_model=PluginReview, status_code=201)
def create_plugin_review(body: PluginReviewCreate) -> PluginReview:
    return WikiReviewStore(resolve_wiki_paths()).create(body)


@router.patch("/reviews/{review_id:path}", response_model=PluginReview)
def update_plugin_review(review_id: str, body: PluginReviewUpdate) -> PluginReview:
    try:
        return WikiReviewStore(resolve_wiki_paths()).update(review_id, body)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Review를 찾을 수 없습니다.") from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail="Review가 다른 사용자에 의해 변경되었습니다.") from exc
```

- [ ] **Step 5: Run Review and M3 regression tests**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_review_store.py tests/wiki/test_wiki_plugin_router.py tests/wiki/test_wiki_sync_service.py`

Expected: all tests PASS; M3 source-removal Review is still created and readable.

- [ ] **Step 6: Commit Review API**

```bash
git add 08-YieldAgent/wiki_review_store.py 08-YieldAgent/models.py 08-YieldAgent/wiki_plugin_router.py 08-YieldAgent/tests/wiki/test_wiki_review_store.py 08-YieldAgent/tests/wiki/test_wiki_plugin_router.py
git commit -m "feat: add concurrent Wiki review API"
```

---

### Task 5: 현재 노트 Chat context, Citation, Session adapter

**Files:**
- Modify: `08-YieldAgent/models.py`
- Modify: `08-YieldAgent/query_state.py`
- Modify: `08-YieldAgent/node_planner.py`
- Create: `08-YieldAgent/agent_sessions.py`
- Modify: `08-YieldAgent/agent_server.py`
- Modify: `08-YieldAgent/wiki_plugin_router.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_router.py`

**Interfaces:**
- Produces: `PluginChatRequest(query, session_id, user_id, current_note_id, resume_value)`
- Produces: internal `ChatRequest.wiki_context: dict[str, Any] | None`
- Produces: additive `CitationData` on `MessageEvent` and `HistoryMessage`
- Consumes: `request.app.state.chat_stream_handler(ChatRequest, Request)`

- [ ] **Step 1: Write failing structured context and Citation tests**

```python
def test_plugin_chat_resolves_note_before_calling_shared_stream(client, token_headers, app, paths):
    write_concept(paths.concepts / "A.md", body="oxide evidence", concept_id="concept:A")
    captured = {}

    async def fake_stream(body, request):
        captured["body"] = body
        return StreamingResponse(iter(["data: {\"type\":\"stream_end\"}\n\n"]), media_type="text/event-stream")

    app.state.chat_stream_handler = fake_stream
    response = client.post("/api/wiki/plugin/chat", headers=token_headers, json={
        "query": "이 이슈의 원인은?", "session_id": "session-1",
        "user_id": "operator-1", "current_note_id": "concepts/A.md",
    })
    assert response.status_code == 200
    assert captured["body"].wiki_context["path"] == "concepts/A.md"
    assert captured["body"].wiki_context["body"] == "oxide evidence"


def test_planner_receives_wiki_context_as_structured_system_context(monkeypatch):
    captured = capture_planner_messages(monkeypatch)
    planner_node(make_state(
        user_text="원인은?",
        wiki_context={"id": "concept:A", "path": "concepts/A.md", "metadata": {"product": "4SS"}, "body": "oxide"},
    ), {})
    system_messages = [m["content"] for m in captured if m["role"] == "system"]
    assert any('"path": "concepts/A.md"' in content and '"body": "oxide"' in content for content in system_messages)


def test_message_event_carries_citations_from_structured_results():
    citations = citations_from_fail_history_results([
        {"doc_id": "FH-1", "source_file": "FH-1.pptx", "download_url": "https://internal/FH-1.pptx"}
    ], source_paths={"FH-1": "sources/FH-1.md"})
    assert citations == [CitationData(
        doc_id="FH-1", label="FH-1", source_path="sources/FH-1.md",
        download_url="https://internal/FH-1.pptx",
    )]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_chat.py tests/wiki/test_wiki_plugin_router.py`

Expected: FAIL because Plugin Chat and structured `wiki_context` do not exist.

- [ ] **Step 3: Add typed context to the existing graph path**

Add `wiki_context: dict` to `YieldQueryState`. On a fresh turn, `chat_stream` puts `request.wiki_context or {}` into `stream_input`; on resume it preserves checkpoint state. The Plugin route resolves `current_note_id` with `load_note_context` and passes the result in an internal `ChatRequest`.

Add the request contracts to `models.py` without making the Plugin-only path required for existing clients:

```python
class ChatRequest(BaseModel):
    query: str
    session_id: str
    resume_value: str | dict[str, Any] | None = None
    user_id: str = ""
    wiki_context: dict[str, Any] | None = None


class PluginChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    resume_value: str | dict[str, Any] | None = None
    user_id: str = ""
    current_note_id: str | None = None
```

In `node_planner.py`, serialize only the structured envelope:

```python
wiki_context = state.get("wiki_context") or {}
if wiki_context:
    meta_parts.append(
        "Current Wiki note:\n" + json.dumps(
            wiki_context, ensure_ascii=False, sort_keys=True, default=str
        )
    )
```

Do not add product/fail/cause keyword extraction or a Korean phrase table.

- [ ] **Step 4: Reuse the existing Chat handler through app state**

```python
@router.post("/chat")
async def plugin_chat(body: PluginChatRequest, request: Request):
    context = None
    if body.current_note_id:
        try:
            context = load_note_context(resolve_wiki_paths(), body.current_note_id)
        except NoteNotFound as exc:
            raise HTTPException(status_code=404, detail="현재 Wiki 노트를 찾을 수 없습니다.") from exc
    chat_body = ChatRequest(
        query=body.query,
        session_id=body.session_id,
        user_id=body.user_id,
        resume_value=body.resume_value,
        wiki_context=context,
    )
    return await request.app.state.chat_stream_handler(chat_body, request)
```

After defining the existing `chat_stream`, register it without changing `/chat/stream`:

```python
app.state.chat_stream_handler = chat_stream
```

- [ ] **Step 5: Add structured Citation to existing SSE and history**

Add `CitationData` and a default-empty `citations` field to `MessageEvent` and `HistoryMessage`. Build citations only from `node_state["fail_history_results"]`; de-duplicate by `doc_id`, attach a Source path only if its materialized Markdown exists, and retain the raw `download_url` only when present. Store the same structured list in Mongo `turn_messages` so session reload preserves links.

```python
class CitationData(BaseModel):
    doc_id: str
    label: str
    source_path: str | None = None
    download_url: str = ""


class MessageEvent(BaseModel):
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    agent: str
    content: str
    citations: list[CitationData] = Field(default_factory=list)
    step: int = 0
```

This is an additive response change; existing frontend fields and routes remain intact.

- [ ] **Step 6: Add authenticated Session adapters using existing Mongo data**

Add `GET /sessions` and `GET /sessions/{session_id}` to `wiki_plugin_router.py`. Move the current Mongo aggregation and history mapping into a dependency-neutral `agent_sessions.py`; both old and Plugin routes call the same helpers so `wiki_plugin_router` never imports `agent_server`.

```python
async def list_session_summaries(db) -> list[SessionSummary]:
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$session_id", "last_query": {"$first": "$query"},
            "turn_count": {"$sum": 1}, "updated_at": {"$first": "$timestamp"},
        }},
        {"$sort": {"updated_at": -1}},
        {"$limit": 50},
    ]
    return [SessionSummary(
        session_id=doc["_id"], last_query=doc.get("last_query", ""),
        turn_count=doc.get("turn_count", 0),
        updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
    ) async for doc in db.chat_turns.aggregate(pipeline)]


async def load_session_history(db, session_id: str) -> SessionHistory:
    turns: list[HistoryMessage] = []
    async for doc in db.chat_turns.find({"session_id": session_id}, {"_id": 0}).sort("timestamp", 1):
        timestamp = doc.get("timestamp", datetime.now(timezone.utc))
        turns.append(HistoryMessage(role="user", content=doc.get("query", ""), timestamp=timestamp))
        for message in doc.get("messages", []):
            turns.append(HistoryMessage(**message, timestamp=timestamp))
    return SessionHistory(session_id=session_id, turns=turns)
```

- [ ] **Step 7: Run Chat, session, planner, and regression tests**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_plugin_chat.py tests/wiki/test_wiki_plugin_router.py tests/test_confirm_edit.py tests/test_user_memory.py`

Expected: all selected tests PASS; existing `ChatRequest` without `wiki_context` remains valid.

- [ ] **Step 8: Commit Chat integration**

```bash
git add 08-YieldAgent/models.py 08-YieldAgent/query_state.py 08-YieldAgent/node_planner.py 08-YieldAgent/agent_sessions.py 08-YieldAgent/agent_server.py 08-YieldAgent/wiki_plugin_router.py 08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py 08-YieldAgent/tests/wiki/test_wiki_plugin_router.py
git commit -m "feat: connect Obsidian context to agent chat"
```

---

### Task 6: Obsidian Plugin scaffold, API client, installer

**Files:**
- Create: `obsidian/plugin/manifest.json`
- Create: `obsidian/plugin/package.json`
- Create: `obsidian/plugin/package-lock.json`
- Create: `obsidian/plugin/tsconfig.json`
- Create: `obsidian/plugin/esbuild.config.mjs`
- Create: `obsidian/plugin/src/types.ts`
- Create: `obsidian/plugin/src/api.ts`
- Create: `obsidian/plugin/scripts/install-vault.mjs`
- Test: `obsidian/plugin/tests/api.test.ts`
- Test: `obsidian/plugin/tests/install-vault.test.ts`

**Interfaces:**
- Produces: `YieldWikiApi.rest<T>(path, init) -> Promise<T>`
- Produces: `YieldWikiApi.streamChat(body, onEvent, signal) -> Promise<void>`
- Produces: `npm run install:vault -- --vault <absolute-vault-path>`

- [ ] **Step 1: Scaffold exact desktop build configuration**

Create `manifest.json`:

```json
{
  "id": "yield-wiki",
  "name": "Yield Wiki",
  "version": "0.1.0",
  "minAppVersion": "1.8.7",
  "description": "Search, chat, and review the Yield failure-history Wiki.",
  "author": "Yield Agent",
  "isDesktopOnly": true
}
```

Create `package.json` with scripts `build`, `test`, `test:watch`, and `install:vault`. Pin `obsidian` to `1.8.7`; use TypeScript `^5.7.2`, esbuild `^0.25.0`, Vitest `^3.2.7`, jsdom `^26.1.0`, and `@types/node` `^22.0.0`. Run `npm install` once to generate `package-lock.json`.

- [ ] **Step 2: Write failing REST, incremental SSE, and installer tests**

```typescript
it('adds bearer auth without exposing the token in the URL', async () => {
  const request = vi.fn().mockResolvedValue({ status: 200, json: { status: 'ok' } });
  const api = new YieldWikiApi({ serverUrl: 'http://localhost:8001', apiToken: 'secret' }, request, fakeStream);
  await api.health();
  expect(request).toHaveBeenCalledWith(expect.objectContaining({
    url: 'http://localhost:8001/api/wiki/plugin/health',
    headers: { Authorization: 'Bearer secret' },
  }));
  expect(request.mock.calls[0][0].url).not.toContain('secret');
});


it('delivers each SSE event before stream completion', async () => {
  const received: string[] = [];
  const stream = makeLocalSseServer([
    'data: {"type":"token","content":"첫"}\n\n',
    'data: {"type":"token","content":"번째"}\n\n',
  ]);
  const api = new YieldWikiApi(stream.settings, fakeRest, nodeSseStream);
  await api.streamChat(chatBody, event => received.push(event.type));
  expect(received).toEqual(['token', 'token']);
});


it('installs artifacts and preserves data.json', async () => {
  const vault = await makeTemporaryVault({ dataJson: '{"apiToken":"keep"}' });
  await installVault(vault);
  expect(await readPluginFile(vault, 'main.js')).not.toBe('');
  expect(await readPluginFile(vault, 'data.json')).toBe('{"apiToken":"keep"}');
});
```

- [ ] **Step 3: Run tests and verify failure**

Run: `cd obsidian/plugin && npm test`

Expected: FAIL because the client and installer do not exist.

- [ ] **Step 4: Implement REST and Node SSE transports**

`rest` uses Obsidian `requestUrl` with `throw: false`, maps `401`, `404`, `409`, and `502` into a typed `ApiError`, and never logs the request headers.

`nodeSseStream` selects `node:http` or `node:https` from the configured URL, writes the JSON body, forwards `Authorization`, and parses complete SSE frames from a rolling buffer:

```typescript
function consumeSseBuffer(buffer: string, emit: (event: SseEvent) => void): string {
  const frames = buffer.split(/\r?\n\r?\n/);
  const remainder = frames.pop() ?? '';
  for (const frame of frames) {
    const data = frame.split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n');
    if (data) emit(JSON.parse(data) as SseEvent);
  }
  return remainder;
}
```

On `AbortSignal`, destroy the request. Reject non-2xx responses with `ApiError`; do not automatically resend Chat or resume requests.

- [ ] **Step 5: Implement explicit Vault installer**

The installer requires an absolute `--vault` argument, verifies `<vault>/.obsidian` exists, requires built `main.js`, and copies only `main.js`, `manifest.json`, `styles.css` to `.obsidian/plugins/yield-wiki`. It never deletes or overwrites `data.json`.

- [ ] **Step 6: Build and run Plugin tests**

Run: `cd obsidian/plugin && npm run build && npm test`

Expected: TypeScript and esbuild succeed, all Vitest tests PASS, and `main.js` exists.

- [ ] **Step 7: Commit Plugin infrastructure**

```bash
git add obsidian/plugin/manifest.json obsidian/plugin/package.json obsidian/plugin/package-lock.json obsidian/plugin/tsconfig.json obsidian/plugin/esbuild.config.mjs obsidian/plugin/src/types.ts obsidian/plugin/src/api.ts obsidian/plugin/scripts/install-vault.mjs obsidian/plugin/tests/api.test.ts obsidian/plugin/tests/install-vault.test.ts
git commit -m "feat: scaffold desktop Obsidian plugin"
```

---

### Task 7: Chat, Search, Review sidebar UI

**Files:**
- Create: `obsidian/plugin/src/settings.ts`
- Create: `obsidian/plugin/src/view.ts`
- Create: `obsidian/plugin/src/main.ts`
- Create: `obsidian/plugin/styles.css`
- Test: `obsidian/plugin/tests/view.test.ts`

**Interfaces:**
- Produces: `YieldWikiPlugin.settings: YieldWikiSettings`
- Produces: `YieldWikiView extends ItemView`
- Consumes: `YieldWikiApi`, `app.workspace.getActiveFile()`, `workspace.openLinkText()`

- [ ] **Step 1: Write failing view behavior tests**

```typescript
it('shows Chat, Search, and Review tabs', async () => {
  const view = createTestView();
  await view.onOpen();
  expect(tabLabels(view.containerEl)).toEqual(['Chat', 'Search', 'Review']);
});


it('sends the active Markdown path only when note context is enabled', async () => {
  const { view, api } = createTestView({ activeFile: 'concepts/4SS_PRE_METAL_CLN_EASY.md' });
  await submitChat(view, '원인은?');
  expect(api.streamChat).toHaveBeenCalledWith(expect.objectContaining({
    current_note_id: 'concepts/4SS_PRE_METAL_CLN_EASY.md',
  }), expect.any(Function), expect.any(AbortSignal));
});


it('labels keyword fallback and opens a Concept result', async () => {
  const { view, api, workspace } = createTestView();
  api.search.mockResolvedValue(searchResponse({ retrieval_mode: 'bm25_fallback' }));
  await submitSearch(view, 'oxide');
  expect(view.containerEl.textContent).toContain('키워드 검색으로 대체됨');
  clickConceptResult(view);
  expect(workspace.openLinkText).toHaveBeenCalledWith(
    'concepts/4SS_PRE_METAL_CLN_EASY', '', false,
  );
});


it('reloads a Review after a version conflict without resending the update', async () => {
  const { view, api } = createTestView();
  api.updateReview.mockRejectedValue(new ApiError(409, 'conflict'));
  await approveFirstReview(view);
  expect(api.updateReview).toHaveBeenCalledTimes(1);
  expect(api.listReviews).toHaveBeenCalledTimes(2);
  expect(view.containerEl.textContent).toContain('다른 사용자가 먼저 변경했습니다');
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd obsidian/plugin && npm test -- view.test.ts`

Expected: FAIL because the view and settings do not exist.

- [ ] **Step 3: Implement local settings and connection status**

Use the Obsidian 1.8-compatible imperative `PluginSettingTab.display()` API. Defaults are:

```typescript
export const DEFAULT_SETTINGS: YieldWikiSettings = {
  serverUrl: 'http://localhost:8001',
  apiToken: '',
};
```

Render Server URL, masked API token, and a connection-test button. Save with `plugin.saveData`; do not place settings in a Vault Markdown file. On view open, call `GET /sessions` and restore the most recently updated session when one exists.

- [ ] **Step 4: Implement one ItemView with three tabs**

Register `yield-wiki-view`, add a right-sidebar ribbon/command, and render tabs inside one `ItemView`. Keep state in the view instance:

```typescript
type ActiveTab = 'chat' | 'search' | 'review';

class YieldWikiView extends ItemView {
  private activeTab: ActiveTab = 'chat';
  private chatAbort?: AbortController;
  private chatEvents: SseEvent[] = [];
  private searchResults: PluginSearchResult[] = [];
  private reviews: PluginReview[] = [];
}
```

Chat concatenates `token` events during generation, replaces/finalizes with `message`, shows `status`, `thinking`, `interrupt`, `error`, and `stream_end`, and exposes a manual retry button after transport failure. It never automatically resends a state-changing resume.

Search renders Concept-first cards, evidence snippets, Source links, and the `source_only` label. Review renders pending items, reviewer/comment inputs, state history, and conflict refresh.

- [ ] **Step 5: Implement safe internal and external navigation**

```typescript
async function openVaultPath(app: App, path: string): Promise<void> {
  const linkText = path.replace(/\.md$/i, '');
  await app.workspace.openLinkText(linkText, '', false);
}
```

Render original PPT/PDF as a normal external anchor with `target="_blank"` and `rel="noopener noreferrer"`. Render no anchor when neither Source path nor URL exists.

- [ ] **Step 6: Style with Obsidian variables only**

Use scoped `.yield-wiki-*` classes and variables such as `--background-primary`, `--background-secondary`, `--text-normal`, `--text-muted`, `--interactive-accent`, and `--background-modifier-border`. Do not modify global Obsidian selectors.

- [ ] **Step 7: Run Plugin tests and production build**

Run: `cd obsidian/plugin && npm test && npm run build`

Expected: all tests PASS and the production bundle contains no literal configured API token.

- [ ] **Step 8: Commit sidebar UI**

```bash
git add obsidian/plugin/src/settings.ts obsidian/plugin/src/view.ts obsidian/plugin/src/main.ts obsidian/plugin/styles.css obsidian/plugin/tests/view.test.ts
git commit -m "feat: add Yield Wiki Obsidian sidebar"
```

---

### Task 8: Full regression, real installation, and E2E evidence

**Files:**
- Create: `08-YieldAgent/docs/wiki-m4-e2e-results.md`
- Modify only if verification finds an M4 defect: files introduced or explicitly modified in Tasks 1–7

**Interfaces:**
- Consumes: Backend at `http://localhost:8001`
- Consumes: OpenSearch configured by the existing Fail History environment
- Consumes: LLM configured by the existing Agent environment
- Consumes: Vault `/Users/daehwankim/SYLDAIX/YieldWiki`

- [ ] **Step 1: Run the complete automated regression suite**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/wiki
pytest -q tests/test_confirm_edit.py tests/test_user_memory.py -m no_server
cd ../obsidian/plugin
npm test
npm run build
```

Expected: all commands PASS. Existing frontend directories do not appear in `git diff --name-only d52e7aa..HEAD`.

- [ ] **Step 2: Configure a non-committed local token and start Backend**

Set `OBSIDIAN_PLUGIN_API_TOKEN` only in the local process environment together with the existing `WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki`, OpenSearch, MongoDB, and LLM configuration. Start:

```bash
cd 08-YieldAgent
uvicorn agent_server:app --host 127.0.0.1 --port 8001
```

Expected: `/health` and authenticated `/api/wiki/plugin/health` both return `200`; an incorrect token returns `401`.

- [ ] **Step 3: Verify real Search against OpenSearch**

Call authenticated Search with query `oxide` and filters `product=4SS`, `fail_type=EASY`, `cause_oper=PRE METAL CLN`.

Expected: response returns either `hybrid` with real vector retrieval or clearly marked `bm25_fallback`; the materialized result opens `concepts/4SS_PRE_METAL_CLN_EASY.md`; the request creates no Wiki files or sync jobs.

- [ ] **Step 4: Install artifacts into the actual Vault**

Run:

```bash
cd obsidian/plugin
npm run install:vault -- --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

Expected: `.obsidian/plugins/yield-wiki/{main.js,manifest.json,styles.css}` exist and any existing `data.json` checksum is unchanged.

- [ ] **Step 5: Exercise the real Obsidian Desktop flow**

In Obsidian `1.9.14`:

1. Enable `Yield Wiki` under Community plugins.
2. Enter `http://localhost:8001` and the local token, then verify connection status.
3. Search `oxide` with `4SS / EASY / PRE METAL CLN` and open the Concept.
4. Confirm Related displays outgoing Source and incoming MOC/backlink notes.
5. Enable current-note context and ask `이 이슈의 주요 원인과 조치는 무엇인가?`.
6. Confirm token events appear before `stream_end` and a structured Citation opens `sources/FH-000238.md` or another returned real Source.
7. Approve one pending Review with reviewer/comment, reload it, then verify version/history in the Markdown.
8. Restart Obsidian and confirm settings and the last session restore.
9. Try a wrong token, stop the embedding dependency, stop OpenSearch, and stop the LLM one at a time; verify the distinct UI states from the design.

Expected: all nine scenarios behave as specified. A missing external dependency is recorded as an incomplete E2E item, not converted into a mock success.

- [ ] **Step 6: Record evidence and run final diff checks**

Write `wiki-m4-e2e-results.md` with timestamp, Obsidian version, Backend commit, OpenSearch index, retrieval mode, real query, returned Concept/Source IDs, Chat session ID, Review ID/version, failure-path results, and any incomplete external dependency.

Run:

```bash
git diff --check
git status --short
git diff --name-only d52e7aa..HEAD
rg -n "OBSIDIAN_PLUGIN_API_TOKEN|apiToken" obsidian/plugin/main.js 08-YieldAgent/docs/wiki-m4-e2e-results.md
```

Expected: no whitespace errors, no unrelated frontend changes, and no secret value in tracked files or bundle. The symbol names may occur, but the configured token value must not.

- [ ] **Step 7: Commit E2E evidence**

```bash
git add 08-YieldAgent/docs/wiki-m4-e2e-results.md
git commit -m "docs: verify Obsidian plugin end to end"
```

---

## Final Acceptance Checklist

- [ ] Authenticated Plugin API fails closed without a server token.
- [ ] Search is Concept-first, read-only, and labels BM25 fallback honestly.
- [ ] Related/backlink results come from materialized wikilinks.
- [ ] Current note arrives at the planner as structured context, without phrase rules.
- [ ] Existing Agent SSE is reused and includes additive structured Citations.
- [ ] Review writes are locked, atomic, versioned, and append-only in history.
- [ ] Plugin source stays in the repository; only three build artifacts install into the Vault.
- [ ] `data.json` and its token remain local and survive reinstall.
- [ ] Existing frontends and public API contracts remain intact.
- [ ] Real OpenSearch, LLM, Backend, and Obsidian Desktop E2E evidence is recorded.
