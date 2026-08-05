---
type: Reference
title: Source Map
description: Top-level directory and source-file inventory for the map_agent repository, mapping each area to its canonical documentation page and key entry points.
tags: [source-map, inventory, navigation]
openwiki:
  roles: [repository]
  source_paths: [08-YieldAgent/, obsidian/]
---

# Source Map

## Repository root

| Path | Purpose | Documentation |
|---|---|---|
| `AGENTS.md` | Behavioral guidelines for LLM coding agents + OpenWiki section | — |
| `CLAUDE.md` | OpenWiki section for Claude agents | — |
| `pyproject.toml` | Python project manifest (name `langgraph-v1-tutorial`, dependencies) | [Agent Server](operations/agent-server.md) |
| `requirements.txt` | Pinned Python dependencies | — |
| `uv.lock` | uv lockfile | — |
| `.env.example` | Example env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, LANGSMITH_API_KEY, TAVILY_API_KEY) | [Agent Server](operations/agent-server.md) |
| `.github/workflows/openwiki-update.yml` | Scheduled OpenWiki GitHub Actions workflow | — |
| `bitbucket-pipelines.yml` | Bitbucket Pipelines port of OpenWiki workflow | — |
| `obsidian/` | Obsidian plugin source | [Obsidian Plugin](integrations/obsidian-plugin.md) |

## `08-YieldAgent/` — Orchestration

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `supervisor.py` | `workflow` (StateGraph builder) | Graph assembly: nodes, edges, `should_end` | [Orchestration](architecture/orchestration.md) |
| `node_planner.py` | `planner_node` | LLM canonicalization → task plan | [Orchestration](architecture/orchestration.md) |
| `node_supervisor.py` | `supervisor_node`, `resolve_time_range` | Task dispatch, HITL, time-range resolution | [Orchestration](architecture/orchestration.md) |
| `node_replanner.py` | `replanner_node` | Plan-and-execute replan + chained-input fill | [Orchestration](architecture/orchestration.md) |
| `node_plan_review.py` | `plan_review_node` | Multi-task plan approval HITL | [HITL Contracts](architecture/hitl-contracts.md) |
| `query_state.py` | `YieldQueryState`, `CanonicalRequestItem`, `TimeRange`, `PlanReviewResult` | Graph state + LLM output schemas | [State & Contracts](architecture/state-and-contracts.md) |
| `canonical_request.py` | `AGENT_SLOT_RULES`, `build_task_from_canonical_request` | Slot schemas + task building | [State & Contracts](architecture/state-and-contracts.md) |
| `task_normalizer_validator.py` | `apply_ordinal_ref`, `FIELD_ALIASES` | Ordinal reference resolution | [State & Contracts](architecture/state-and-contracts.md) |
| `recent_results.py` | `_recent_results_update`, `_recent_results_prompt_context` | K=10 result index + prompt context | [State & Contracts](architecture/state-and-contracts.md) |
| `wads_context.py` | `_resolve_chained_params`, `_latest_wads_result` | WADS → downstream chaining bridge | [Orchestration](architecture/orchestration.md) |
| `result_contracts.py` | `build_result_envelope`, `attach_result_envelope` | Result envelope contracts | [State & Contracts](architecture/state-and-contracts.md) |
| `orch_utils.py` | `_model`, `_AGENT_NAMES`, `_is_placeholder_or_empty` | Shared orchestration utilities | [Orchestration](architecture/orchestration.md) |
| `prompts.py` | `CANONICAL_PLANNER_SYSTEM_PROMPT`, `REPLANNER_SYSTEM_PROMPT` | Centralized prompts | [Orchestration](architecture/orchestration.md) |
| `common.py` | `get_llm`, `get_oracle_connection`, `to_user_message` | Shared utilities (Oracle pool, dates, constants) | [Data Layer](domain/data-layer.md) |

## `08-YieldAgent/` — Server & frontend

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `agent_server.py` | `lifespan`, `_chat_stream`, `app` | FastAPI backend | [Agent Server](operations/agent-server.md) |
| `app.py` | — | Streamlit UI (alternative frontend) | — |
| `models.py` | `ChatRequest`, `MessageEvent`, `ArtifactEvent`, `InterruptEvent` | SSE event + request models | [Agent Server](operations/agent-server.md) |
| `agent_sessions.py` | `list_session_summaries`, `load_session_history` | Session history persistence | [Agent Server](operations/agent-server.md) |
| `lf_utils.py` | `lf_callbacks`, `set_lf_capture_disabled` | Langfuse integration | [Observability](operations/observability.md) |
| `local_trace.py` | `emit_trace_event`, `make_trace_id`, `new_turn_id` | Local JSONL tracing | [Observability](operations/observability.md) |
| `user_memory.py` | `get_profile`, `update_profile_from_feedback` | User preference memory (MongoDB) | [Observability](operations/observability.md) |
| `yield_frontend/` | — | Vite + React 19 chat frontend | [React Frontend](integrations/react-frontend.md) |

## `08-YieldAgent/` — Domain agents

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `yield_query_agent.py` | `yield_agent_node` | Yield query + anomaly detection + LLM analysis | [Domain Agents](domain/agents.md) |
| `yield_db.py` | `_fetch_periods`, `_fetch_wafer_scatter`, `_fetch_lot_sql` | Oracle SQL for yield | [Data Layer](domain/data-layer.md) |
| `yield_viz.py` | `_build_table`, `_build_scatter_html`, `_detect_anomalies` | Yield visualization + anomaly detection | [Domain Agents](domain/agents.md) |
| `wads_agent.py` | `wads_agent_node` | WADS degradation report agent | [Domain Agents](domain/agents.md) |
| `wads_tools.py` | `wads_query_data`, `wads_get_html_report`, `wads_query_sql` | WADS @tool functions | [Domain Agents](domain/agents.md) |
| `map_agent.py` | `map_agent_node` | Wafer map (binmap/cummap) visualization | [Domain Agents](domain/agents.md) |
| `wafer_zones.py` | `WAFER_ZONES`, `compute_zone_deltas`, `worst_zone` | Static 9-zone wafer die map | [Domain Agents](domain/agents.md) |
| `fail_history_agent.py` | `fail_history_agent_node` | OpenSearch fail history search | [Domain Agents](domain/agents.md) |
| `fail_history_tools.py` | `do_search`, `search_opensearch_with_mode` | OpenSearch hybrid search tools | [Domain Agents](domain/agents.md) |
| `lot_history_agent.py` | `lot_history_agent_node` | LOT comprehensive history (5 Oracle tables) | [Domain Agents](domain/agents.md) |
| `relation_tree_agent.py` | `relation_tree_agent_node` | Relation tree (main_oper candidates) | [Domain Agents](domain/agents.md) |
| `mining_agent.py` | `mining_agent_node`, `mining_analysis` | Gini-based parameter mining | [Domain Agents](domain/agents.md) |
| `wt_resp_agent.py` | `wt_resp_agent_node` | WT response analysis (good/bad groups) | [Domain Agents](domain/agents.md) |
| `ppt_export_agent.py` | `ppt_export_node` | PPTX export node | [Domain Agents](domain/agents.md) |
| `ppt_builder.py` | `YieldReportPPTBuilder` | PPTX builder pipeline | [Domain Agents](domain/agents.md) |
| `ppt_renderer.py` | `render_presentation` | python-pptx renderer | [Domain Agents](domain/agents.md) |
| `ppt_llm_designer.py` | `generate_extra_slide`, `PresentationDesign` | GLM-4.7 slide design | [Domain Agents](domain/agents.md) |
| `mining_dummy_api.py` | `fetch_mining_dataframes` | Mock mining API | [Domain Agents](domain/agents.md) |

## `08-YieldAgent/repl_agent/` — REPL agent

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `router.py` | `router` | FastAPI REPL router | [REPL Agent](architecture/repl-agent.md) |
| `agent.py` | `get_agent` | create_agent lazy singleton | [REPL Agent](architecture/repl-agent.md) |
| `session_store.py` | `create_session`, `begin_run`, `cancel_run`, `close_session` | Session lifecycle | [REPL Agent](architecture/repl-agent.md) |
| `runtime/process.py` | `ProcessPythonRuntime` | Isolated worker process | [REPL Agent](architecture/repl-agent.md) |
| `runtime/worker.py` | `worker_main` | Child process entry | [REPL Agent](architecture/repl-agent.md) |
| `events.py` | `EventEmitter`, `ExecutionResult`, `PlotArtifact` | SSE event model | [REPL Agent](architecture/repl-agent.md) |
| `tools.py` | `run_python` | Code execution tool | [REPL Agent](architecture/repl-agent.md) |

## `08-YieldAgent/` — Wiki system

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `wiki_config.py` | `WikiPaths`, `resolve_wiki_paths`, `initialize_wiki_vault` | Vault path resolution | [Wiki System](wiki/wiki-system.md) |
| `wiki_store.py` | `upsert_concept`, `upsert_episode`, `lookup_concept_body` | Note storage | [Wiki System](wiki/wiki-system.md) |
| `wiki_safe_mutation.py` | `PinnedWikiMutation`, `FileSnapshot` | Race-safe file mutations | [Wiki System](wiki/wiki-system.md) |
| `wiki_sync.py` | `OpenSearchWikiScanner`, `WikiSyncService` | Incremental sync | [Wiki System](wiki/wiki-system.md) |
| `wiki_summarizer.py` | `summarize`, `synthesize_concept`, `synthesize_concept_from_docs` | LLM summarization/synthesis | [Wiki System](wiki/wiki-system.md) |
| `wiki_queue.py` | `WikiQueue`, `wiki_queue` | Search-time enrichment queue | [Wiki System](wiki/wiki-system.md) |
| `wiki_materializer.py` | `materialize_wiki` | Obsidian link rendering | [Wiki System](wiki/wiki-system.md) |
| `wiki_graph_projection.py` | `WikiGraphProjection`, `build_graph_projection` | Read-only graph cache | [Wiki System](wiki/wiki-system.md) |
| `wiki_graph_models.py` | `EntityCandidate`, `RelationCandidate`, `RelationPredicate` | Graph models | [Wiki System](wiki/wiki-system.md) |
| `wiki_evidence_enrichment.py` | `WikiEvidenceEnrichmentService` | Evidence enrichment | [Wiki System](wiki/wiki-system.md) |
| `wiki_lint.py` | `scan` | Vault integrity scanner | [Wiki System](wiki/wiki-system.md) |
| `wiki_router.py` | `router`, `get_graph` | Graph API router | [Wiki API](wiki/wiki-api.md) |
| `wiki_plugin_router.py` | `router` | Plugin API router | [Wiki API](wiki/wiki-api.md) |
| `wiki_plugin_auth.py` | `require_plugin_token` | Bearer token auth | [Wiki API](wiki/wiki-api.md) |
| `wiki_plugin_notes.py` | `resolve_markdown_path`, `load_note_context` | Note path resolution | [Wiki API](wiki/wiki-api.md) |
| `wiki_plugin_search.py` | `search_wiki` | Plugin search | [Wiki API](wiki/wiki-api.md) |
| `wiki_review_store.py` | `WikiReviewStore` | Review CRUD | [Wiki API](wiki/wiki-api.md) |
| `wiki_job_store.py` | `WikiJobStore` | MongoDB job queue | [Wiki System](wiki/wiki-system.md) |
| `wiki_manifest.py` | `load_manifest`, `save_manifest` | Sync manifest | [Wiki System](wiki/wiki-system.md) |

## `08-YieldAgent/` — Wiki CLI scripts

| File | Purpose | Documentation |
|---|---|---|
| `sync_wiki.py` | Incremental sync CLI | [Wiki CLI](wiki/wiki-cli.md) |
| `enrich_wiki.py` | Evidence enrichment CLI | [Wiki CLI](wiki/wiki-cli.md) |
| `materialize_obsidian_wiki.py` | Materialization CLI | [Wiki CLI](wiki/wiki-cli.md) |
| `bootstrap_wiki_warmup.py` | Bootstrap warmup CLI | [Wiki CLI](wiki/wiki-cli.md) |
| `make_super_concept.py` | Super-concept CLI | [Wiki CLI](wiki/wiki-cli.md) |
| `migrate_wiki_vault.py` | Vault migration CLI | [Wiki CLI](wiki/wiki-cli.md) |

## `obsidian/plugin/` — Obsidian plugin

| File | Symbol | Purpose | Documentation |
|---|---|---|---|
| `src/main.ts` | `YieldWikiPlugin` | Plugin lifecycle | [Obsidian Plugin](integrations/obsidian-plugin.md) |
| `src/view.ts` | `YieldWikiView`, `YIELD_WIKI_VIEW_TYPE` | Sidebar UI | [Obsidian Plugin](integrations/obsidian-plugin.md) |
| `src/api.ts` | `YieldWikiApi`, `nodeSseStream` | HTTP client + SSE | [Obsidian Plugin](integrations/obsidian-plugin.md) |
| `src/settings.ts` | `YieldWikiSettingTab`, `DEFAULT_SETTINGS` | Settings UI | [Obsidian Plugin](integrations/obsidian-plugin.md) |
| `src/types.ts` | `PluginSettings`, `SseEvent`, `PluginReview` | Type definitions | [Obsidian Plugin](integrations/obsidian-plugin.md) |
| `tests/` | — | Vitest test suite | [Testing](operations/testing.md) |
