---
type: Reference
title: map_agent OpenWiki Quickstart
description: Entry point for the map_agent semiconductor yield multi-agent system knowledge base. Covers what the system does, how it is organized, and where to go next.
tags: [quickstart, overview, yield-agent, wiki, obsidian]
---

# map_agent OpenWiki Quickstart

This repository implements **0group8-YieldAgent**, a Korean-language conversational multi-agent system for semiconductor fab **yield analysis**. A user asks natural-language questions about product yield, wafer degradation (WADS), wafer maps, LOT history, and failure history. A LangGraph plan-and-execute supervisor plans canonical tasks, dispatches them to domain agents, and streams results back over SSE. Alongside the agent, the repository ships an **Obsidian-backed failure-history knowledge wiki** that synthesizes OpenSearch documents into Markdown notes, an **Obsidian plugin** for browsing/chatting the wiki, a **React frontend** for the chat UI, and a **REPL verification agent** for numeric hypothesis testing.

## What lives where

| Area | Directory | Summary |
|---|---|---|
| Agent backend (Python) | `08-YieldAgent/` | LangGraph supervisor graph, domain agents, FastAPI server, data layer, wiki engine, REPL agent |
| React chat frontend | `08-YieldAgent/yield_frontend/` | Vite + React 19 app consuming the SSE protocol from `/chat/stream` |
| Obsidian plugin (TypeScript) | `obsidian/plugin/` | Obsidian sidebar plugin for wiki search, chat, and review |
| Obsidian vault (managed) | `08-YieldAgent/wiki/` | The materialized wiki vault (concepts, episodes, graph); default vault root |
| Existing design docs & plans | `08-YieldAgent/docs/`, `08-YieldAgent/S2_PLAN.md`, root `*.md` | Plans, deployment procedures, E2E results |

## Run commands

```bash
# Backend (FastAPI + LangGraph + wiki + REPL), port 8001
uvicorn 08-YieldAgent.agent_server:app --port 8001

# Streamlit fallback UI (legacy), port 8501
streamlit run 08-YieldAgent/app.py

# React chat frontend (dev), proxies /session and /chat to :8001
cd 08-YieldAgent/yield_frontend && npm run dev

# Obsidian plugin build + install into a vault
cd obsidian/plugin && npm run build && npm run install:vault -- --vault <abs-vault-path>
```

## Task routing table

| Change area / intent | Wiki page | Source entry points | Important symbols | Focused tests | Validation |
|---|---|---|---|---|---|
| Add/modify an agent node or routing | [Orchestration](architecture/orchestration.md) | `supervisor.py`, `node_planner.py`, `node_supervisor.py`, `node_replanner.py` | `workflow`, `planner_node`, `supervisor_node`, `replanner_node`, `should_end` | `tests/test_e2e_regression.py` | `pytest tests/test_e2e_regression.py -k <case>` (server must be up) |
| Change graph state or slot schema | [State & Contracts](architecture/state-and-contracts.md) | `query_state.py`, `canonical_request.py` | `YieldQueryState`, `CanonicalRequestItem`, `AGENT_SLOT_RULES` | `tests/test_e2e_regression.py` | `pytest tests/test_e2e_regression.py` |
| Change HITL interrupt behavior | [HITL Contracts](architecture/hitl-contracts.md) | `node_supervisor.py`, `node_plan_review.py`, `agent_server.py` | `interrupt`, `InterruptEvent`, `_resume_is_interrupt_answer` | `tests/test_e2e_regression.py`, `tests/test_confirm_edit.py` | `pytest tests/test_e2e_regression.py` |
| Modify a domain agent or artifact | [Domain Agents](domain/agents.md) | `yield_query_agent.py`, `wads_agent.py`, `map_agent.py`, `fail_history_agent.py`, ... | `*_agent_node`, `attach_result_envelope` | `tests/verify_*.py`, `tests/test_e2e_regression.py` | `pytest tests/test_e2e_regression.py` |
| Change data source / SQL / OpenSearch | [Data Layer](domain/data-layer.md) | `yield_db.py`, `wads_tools.py`, `fail_history_tools.py`, `lot_history_tools.py` | `_fetch_*`, `search_opensearch_with_mode` | `tests/verify_*.py` | `pytest tests/verify_*.py` |
| Wiki storage / materialization / sync | [Wiki System](wiki/wiki-system.md) | `wiki_store.py`, `wiki_materializer.py`, `wiki_sync.py` | `upsert_concept`, `materialize_wiki`, `WikiSyncService` | `tests/wiki/test_wiki_materializer.py`, `test_wiki_store_external_vault.py` | `pytest tests/wiki/test_wiki_materializer.py -q` |
| Wiki HTTP endpoints | [Wiki API](wiki/wiki-api.md) | `wiki_router.py`, `wiki_plugin_router.py` | `router`, `get_graph`, `get_node` | `tests/wiki/test_wiki_plugin_router.py`, `test_fail_history_wiki_graph.py` | `pytest tests/wiki/test_wiki_plugin_router.py -q` |
| Wiki CLI / enrichment scripts | [Wiki CLI](wiki/wiki-cli.md) | `sync_wiki.py`, `enrich_wiki.py`, `bootstrap_wiki_warmup.py` | CLI `--check`/`--apply` flags | `tests/wiki/test_sync_wiki_cli.py`, `test_enrich_wiki_cli.py` | `pytest tests/wiki/test_sync_wiki_cli.py -q` |
| Obsidian plugin | [Obsidian Plugin](integrations/obsidian-plugin.md) | `obsidian/plugin/src/main.ts`, `view.ts`, `api.ts` | `YieldWikiPlugin`, `YieldWikiView`, `YieldWikiApi` | `obsidian/plugin/tests/*.test.ts` | `cd obsidian/plugin && npx vitest run` |
| React frontend | [React Frontend](integrations/react-frontend.md) | `yield_frontend/src/App.tsx`, `lib/stream.ts` | `App`, `streamChat`, `RealStreamEvent` | (no test runner configured) | `npm run build` (tsc + vite) |
| REPL verification agent | [REPL Agent](architecture/repl-agent.md) | `repl_agent/router.py`, `session_store.py`, `runtime/` | `router`, `create_session`, `ProcessPythonRuntime` | `repl_agent/tests/test_*.py` | `pytest repl_agent/tests/ -q` |
| FastAPI server / SSE / lifespan | [Agent Server](operations/agent-server.md) | `agent_server.py`, `models.py` | `lifespan`, `_chat_stream`, `ChatRequest` | `repl_agent/tests/test_agent_server_lifespan.py` | `pytest repl_agent/tests/test_agent_server_lifespan.py -q` |
| Observability / tracing | [Observability](operations/observability.md) | `local_trace.py`, `lf_utils.py` | `emit_trace_event`, `set_trace_context`, `lf_callbacks` | `tests/wiki/test_runtime_dependencies.py` | `pytest tests/wiki/test_runtime_dependencies.py -q` |
| User preference memory | [Observability](operations/observability.md) | `user_memory.py` | `get_profile`, `update_profile_from_feedback` | `tests/test_user_memory.py` | `pytest tests/test_user_memory.py -q` |
| Testing strategy | [Testing](operations/testing.md) | `tests/`, `tests/wiki/`, `repl_agent/tests/` | — | — | `pytest tests/ -q` |

## Major concepts

- [Orchestration graph](architecture/orchestration.md) — the plan-and-execute LangGraph supervisor that routes natural-language requests to domain agents.
- [State and contracts](architecture/state-and-contracts.md) — `YieldQueryState`, canonical request schema, slot rules, and result envelopes.
- [HITL contracts](architecture/hitl-contracts.md) — structured interrupt/resume protocol for missing parameters and plan approval.
- [Domain agents](domain/agents.md) — yield, WADS, map, fail history, lot history, relation tree, mining, wt_resp, and PPT export agents.
- [Data layer](domain/data-layer.md) — Oracle, OpenSearch, MongoDB, and embedding API usage.
- [Wiki system](wiki/wiki-system.md) — the Obsidian-backed knowledge wiki with vault storage, sync, materialization, and enrichment.
- [Wiki API](wiki/wiki-api.md) — FastAPI routers serving the graph and plugin endpoints.
- [Wiki CLI](wiki/wiki-cli.md) — command-line scripts for sync, enrichment, materialization, and migration.
- [Obsidian plugin](integrations/obsidian-plugin.md) — the TypeScript sidebar plugin for wiki search, chat, and review.
- [React frontend](integrations/react-frontend.md) — the Vite/React chat UI.
- [REPL agent](architecture/repl-agent.md) — the isolated-process data verification agent.
- [Agent server](operations/agent-server.md) — FastAPI backend, SSE protocol, and lifespan management.
- [Observability](operations/observability.md) — local JSONL tracing, Langfuse integration, and user preference memory.
- [Testing](operations/testing.md) — test organization and validation strategy.

## Backlog

None. All discovered subsystems have dedicated pages.
