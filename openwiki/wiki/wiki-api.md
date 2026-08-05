---
type: API
title: Wiki HTTP API
description: FastAPI routers serving the Obsidian vault graph API (wiki_router) and the plugin API (wiki_plugin_router) with bearer-token auth, search, chat, and review endpoints.
tags: [wiki, api, fastapi, plugin, endpoints]
openwiki:
  roles: [integration, architecture]
  source_paths: [08-YieldAgent/wiki_router.py, 08-YieldAgent/wiki_plugin_router.py, 08-YieldAgent/wiki_plugin_auth.py, 08-YieldAgent/wiki_plugin_notes.py, 08-YieldAgent/wiki_plugin_search.py]
  symbols: [router, get_graph, get_node, get_trip_docs, get_doc, require_plugin_token, search_wiki, plugin_dependency_status, WikiReviewStore]
  test_paths: [08-YieldAgent/tests/wiki/test_wiki_plugin_router.py, 08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py, 08-YieldAgent/tests/wiki/test_wiki_plugin_search.py, 08-YieldAgent/tests/wiki/test_wiki_router_external_vault.py]
  invariants: ["wiki_router is mounted at /api/wiki (unauthenticated, internal).", "wiki_plugin_router is mounted at /api/wiki/plugin (bearer-token auth via OBSIDIAN_PLUGIN_API_TOKEN).", "Graph API has a 5s TTL memory cache."]
  validation_commands: ["pytest tests/wiki/test_wiki_plugin_router.py -q", "pytest tests/wiki/test_wiki_router_external_vault.py -q"]
---

# Wiki HTTP API

Two FastAPI routers are mounted in `agent_server.py` (lines 232–233):

- `wiki_router` → prefix `/api/wiki` (unauthenticated, internal graph API)
- `wiki_plugin_router` → prefix `/api/wiki/plugin` (bearer-token auth, for the [Obsidian plugin](../integrations/obsidian-plugin.md))

## Graph API (`/api/wiki`)

Defined in `wiki_router.py`. Performs a full-vault frontmatter scan and returns graphology v0.25-compatible JSON.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/wiki/graph` | Graph JSON. Supports `view=product_tree` for a 3-tier product→fail→oper view merging vault concepts + OpenSearch aggregation. 5s TTL memory cache. |
| GET | `/api/wiki/trip-docs` | OpenSearch raw docs for a (product, fail_type, cause_oper) triple |
| GET | `/api/wiki/doc/{doc_id}` | Single OpenSearch raw doc by doc_id |
| GET | `/api/wiki/node/{node_id}` | Node detail + backlinks (e.g. `concept:4SS|STI CMP|EASY(W)`) |

Key symbols: `router`, `get_graph()`, `get_trip_docs()`, `get_doc()`, `get_node()`, `_scan_nodes()`, `_build_graph()`, `_build_product_tree()`.

## Plugin API (`/api/wiki/plugin`)

Defined in `wiki_plugin_router.py`. Bearer-token protected via `wiki_plugin_auth.require_plugin_token` (constant-time comparison via `secrets.compare_digest`, env `OBSIDIAN_PLUGIN_API_TOKEN`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Dependency status (OpenSearch ping, LLM config) |
| POST | `/chat` | Chat with current-note wiki context injected (SSE) |
| GET | `/sessions` | Session list |
| GET | `/sessions/{id}` | Session history |
| GET | `/search` | OpenSearch search grouped by triple, enriched with wiki concept paths. Params: `q`, `product`, `fail_type`, `cause_oper`, `limit` (default 20) |
| GET | `/related/{note_path}` | Outgoing wikilinks + backlinks for a note |
| GET | `/sources/{doc_id}` | Source note metadata |
| GET | `/reviews` | List reviews by status (default `pending`) |
| POST | `/reviews` | Create a review |
| PATCH | `/reviews/{id}` | Update a review (approve/reject with `expected_version` optimistic concurrency; 409 on conflict) |

### Chat endpoint

`POST /api/wiki/plugin/chat` accepts a `PluginChatRequest` (query, session_id, resume_value, user_id, current_note_id). The router resolves the active note's body/metadata via `wiki_plugin_notes.load_note_context` + `resolve_wiki_paths`, constructs an `InternalChatRequest` with the wiki context, and delegates to `request.app.state.chat_stream_handler` — the same `_chat_stream` function used by the public `/chat/stream` endpoint. This means the plugin gets the full multi-agent supervisor graph with the note context injected.

### Search endpoint

`wiki_plugin_search.search_wiki` queries OpenSearch via `search_opensearch_with_mode`, groups hits by triple key, and attaches materialized wiki concept paths/status. Read-only — no materialization.

### Reviews

`wiki_review_store.WikiReviewStore` persists reviews as Markdown notes in `reviews/`. Uses `fcntl.flock` for mutual exclusion. Versioned updates with history block (`<!-- yield-wiki:review-history -->`). The 409 `ReviewConflict` on version mismatch maps to the plugin's `conflict` error code.

## Plugin notes

`wiki_plugin_notes.py` provides safe note path resolution (no `..`, no symlinks, `.md` only, must stay within vault root), wikilink extraction, backlink computation (full vault rglob), and source note reading.

## Auth

`wiki_plugin_auth.py` — Bearer token dependency. The token is validated from `OBSIDIAN_PLUGIN_API_TOKEN` env var. Every plugin request must include `Authorization: Bearer <token>`.

## When to consult this page

- Adding or changing a wiki API endpoint.
- Modifying plugin auth or chat context injection.
- Changing the review CRUD contract.

## Validation

```bash
pytest tests/wiki/test_wiki_plugin_router.py -q
pytest tests/wiki/test_wiki_plugin_chat.py -q
pytest tests/wiki/test_wiki_plugin_search.py -q
pytest tests/wiki/test_wiki_router_external_vault.py -q
```
