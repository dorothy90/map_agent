---
type: Integration
title: Obsidian Plugin
description: The TypeScript Obsidian sidebar plugin (yield-wiki) for searching, chatting, and reviewing the failure-history wiki vault against the Python wiki_plugin_router backend.
tags: [obsidian, plugin, typescript, frontend]
openwiki:
  roles: [integration, delivery]
  source_paths: [obsidian/plugin/src/main.ts, obsidian/plugin/src/view.ts, obsidian/plugin/src/api.ts, obsidian/plugin/src/settings.ts, obsidian/plugin/src/types.ts]
  symbols: [YieldWikiPlugin, YieldWikiView, YIELD_WIKI_VIEW_TYPE, YieldWikiApi, ApiError, nodeSseStream, DEFAULT_SETTINGS]
  test_paths: [obsidian/plugin/tests/api.test.ts, obsidian/plugin/tests/view.test.ts, obsidian/plugin/tests/main.test.ts, obsidian/plugin/tests/install-vault.test.ts]
  invariants: ["Plugin calls only /api/wiki/plugin/* endpoints (wiki_plugin_router), not /api/wiki/* (wiki_router).", "Every request adds Authorization: Bearer <apiToken>; token is never in the URL.", "install-vault preserves data.json (user settings survive reinstalls)."]
  validation_commands: ["cd obsidian/plugin && npx vitest run"]
---

# Obsidian Plugin

The `yield-wiki` plugin (`obsidian/plugin/`) is an Obsidian sidebar plugin (right-leaf `ItemView`) that exposes three tabs — **Chat**, **Search**, and **Review** — against the Python [wiki plugin API](../wiki/wiki-api.md). It is desktop-only, min Obsidian 1.8.7.

## Architecture

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    O[Obsidian host] --> P[YieldWikiPlugin<br/>main.ts]
    P --> V[YieldWikiView<br/>view.ts]
    V --> API[YieldWikiApi<br/>api.ts]
    API -->|Bearer auth| BE[wiki_plugin_router<br/>/api/wiki/plugin/*]
    BE --> Graph[wiki_store<br/>vault]
    BE --> Chat[agent_server<br/>_chat_stream]
```

## Key files

| File | Export | Role |
|---|---|---|
| `src/main.ts` | `YieldWikiPlugin` | Obsidian `Plugin` lifecycle: loads/saves settings, registers view, ribbon icon ("microscope"), command ("Yield Wiki 열기"), settings tab |
| `src/view.ts` | `YieldWikiView`, `YIELD_WIKI_VIEW_TYPE` | Sidebar UI: three-tab renderer, SSE chat consumer, search, review, related-notes, connection state machine |
| `src/api.ts` | `YieldWikiApi`, `ApiError`, `nodeSseStream` | HTTP client to `${serverUrl}/api/wiki/plugin/*`; SSE stream consumer for `/chat` |
| `src/settings.ts` | `YieldWikiSettingTab`, `DEFAULT_SETTINGS` | Settings UI tab + connection test |
| `src/types.ts` | All shared interfaces | Pure type module: `PluginSettings`, `ChatRequest`, `SseEvent`, `PluginReview`, `PluginSearchResponse`, etc. |

## Settings model

`PluginSettings` has two fields:
- `serverUrl: string` — default `http://localhost:8001`
- `apiToken: string` — default `""`

`loadSettings` reads only these two keys (extra keys dropped). The settings tab renders two text inputs (API token is password-type) plus a "연결 테스트" button calling `api.health()`.

## Backend communication

All calls go to `${serverUrl}/api/wiki/plugin${path}` via `pluginUrl()`. Every request adds `Authorization: Bearer ${apiToken}` (token never in URL — asserted by test). The REST transport is pluggable (`RestTransport`); production uses `obsidian.requestUrl`. Errors are normalized to `ApiError` with codes `unauthorized`(401) / `not_found`(404) / `conflict`(409) / `bad_gateway`(502) / `http_error`.

### Endpoint mapping

| Plugin method | Backend route |
|---|---|
| `health()` | `GET /api/wiki/plugin/health` |
| `streamChat()` / `nodeSseStream` | `POST /api/wiki/plugin/chat` (SSE) |
| `listSessions()`, `getSession()` | `GET /api/wiki/plugin/sessions`, `/sessions/{id}` |
| `search()` | `GET /api/wiki/plugin/search` (q, product, fail_type, cause_oper, limit) |
| `related()` | `GET /api/wiki/plugin/related/{note_path:path}` |
| `listReviews()`, `createReview()`, `updateReview()` | `GET/POST/PATCH /api/wiki/plugin/reviews` |

SSE streaming (`nodeSseStream`) uses Node `http`/`https` directly, POST with `Accept: text/event-stream`, Bearer auth, JSON body. Frames split on `\n\n`; each `data:` line JSON-parsed into `SseEvent`. Supports `AbortSignal`.

## View model

`YieldWikiView extends ItemView` with `getViewType() = "yield-wiki-view"`, icon "microscope". Three tabs: `chat` | `search` | `review`.

- **Chat**: `chatEntries` discriminated union (user | assistant | artifact | suggestion | error | interrupt). Streaming appends SSE events; `chatAbort` + `chatGeneration` guard against stale updates. `contextEnabled` toggles sending the active note as `current_note_id`. Handles interrupt (HITL) with `resume_value` answer flow.
- **Search**: builds `URLSearchParams`, shows `retrieval_mode` badge and evidence with download links.
- **Review**: lists by status, create form, approve/reject with `expected_version` optimistic concurrency; `reviewInFlight` Set prevents double-submit.
- **Related notes**: triggered on active-file change (`file-open` event); shows outgoing + backlinks.
- **Connection state**: `checking` | `connected` | `unauthorized` | `offline`.

## Build setup

- **esbuild** (`esbuild.config.mjs`): entry `src/main.ts`, bundles to `main.js`, `external: ["obsidian"]`, `format: "cjs"`, `platform: "node"`, `target: es2022.
- **TypeScript** (`tsconfig.json`): `strict`, `noEmit`, `types: ["node","obsidian","vitest/globals"]`.
- **npm scripts**: `build` = `tsc --noEmit && node esbuild.config.mjs production`; `test` = `vitest run`; `install:vault` = `node scripts/install-vault.mjs`.
- Dev deps only (no runtime deps) — the bundle is self-contained except `obsidian`.

## install-vault script

`scripts/install-vault.mjs` copies `main.js`, `manifest.json`, `styles.css` into `<vault>/.obsidian/plugins/yield-wiki/`. Validates: vault path is absolute, vault contains `.obsidian`, all three artifacts exist. Deliberately does **not** touch `data.json` (user settings survive reinstalls — asserted by test). CLI: `npm run install:vault -- --vault <abs-vault-path>`.

## Testing

**Framework**: Vitest 3.2.7, jsdom for DOM tests. `vitest.config.ts` aliases `obsidian` → `tests/obsidian-runtime.ts` (stub module).

| Test file | Coverage |
|---|---|
| `tests/api.test.ts` | `YieldWikiApi` REST methods (bearer auth, URL encoding, error mapping), `nodeSseStream` against local HTTP server |
| `tests/view.test.ts` | Tab switching, search filters, review CRUD with version conflict, interrupt/resume chat flow, citation rendering, abort on close, file-open related-notes, connection-state transitions |
| `tests/main.test.ts` | `onload` settings loading, view registration, ribbon icon, command, `onunload` |
| `tests/install-vault.test.ts` | Artifact copy, `data.json` preservation, non-absolute path rejection |

## When to consult this page

- Adding a new plugin tab or SSE event handling.
- Changing the backend endpoint contract.
- Modifying settings or auth flow.

## Validation

```bash
cd obsidian/plugin && npx vitest run
cd obsidian/plugin && npm run build
```
