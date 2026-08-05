---
type: Operations
title: Agent Server
description: The FastAPI backend that compiles and runs the supervisor LangGraph, serves SSE chat, manages sessions, hosts the wiki API, and mounts the REPL router.
tags: [server, fastapi, lifespan, sse, sessions]
openwiki:
  roles: [architecture, operations, integration]
  source_paths: [08-YieldAgent/agent_server.py, 08-YieldAgent/models.py, 08-YieldAgent/agent_sessions.py]
  symbols: [lifespan, _chat_stream, app, ChatRequest, InternalChatRequest, PluginChatRequest, MongoDBSaver, StreamStartEvent, MessageEvent, ArtifactEvent, InterruptEvent, StreamEndEvent]
  test_paths: [08-YieldAgent/repl_agent/tests/test_agent_server_lifespan.py, 08-YieldAgent/tests/test_e2e_regression.py]
  invariants: ["Graph is compiled with MongoDBSaver checkpointer during lifespan startup.", "Artifacts are reset per turn via Overwrite([]) in _chat_stream.", "Shutdown ordering: wiki_queue.stop → close_all_sessions → mongo.close.", "Plugin chat injects wiki note context then delegates to the same _chat_stream handler."]
  validation_commands: ["uvicorn 08-YieldAgent.agent_server:app --port 8001", "pytest tests/test_e2e_regression.py -v"]
---

# Agent Server

`agent_server.py` is the FastAPI backend that compiles and runs the [supervisor LangGraph](../architecture/orchestration.md), serves the SSE chat protocol, manages sessions, hosts the [wiki API](../wiki/wiki-api.md), and mounts the [REPL router](../architecture/repl-agent.md).

## Lifespan

The `lifespan` context manager (line 159) handles startup and shutdown:

**Startup:**
1. `resolve_wiki_paths` → `initialize_wiki_vault` → `validate_wiki_vault` — prepares the Obsidian vault.
2. `AsyncIOMotorClient` — async MongoDB client for session history (`motor_db`).
3. `wiki_queue.set_summarizer(wiki_summarize_fn)` + `wiki_queue.start()` — starts the [wiki queue](../wiki/wiki-system.md#wiki_queuepy) workers.
4. `WIKI_LINT_CRON_HOURS` > 0 → starts the wiki lint cron loop.
5. `MongoDBSaver.from_conn_string` — compiles `workflow.compile(checkpointer=checkpointer)` as `app.state.graph`.

**Shutdown (finally block):**
1. Cancel lint cron task.
2. `wiki_queue.stop(timeout=10)`.
3. `close_all_sessions()` — reaps all REPL worker processes.
4. Close `MongoDBSaver` context (releases sync Mongo connection).
5. `motor_client.close()` — closes async Mongo.

Shutdown ordering is verified by `repl_agent/tests/test_agent_server_lifespan.py`.

## Routers mounted

| Router | Prefix | Source | Auth |
|---|---|---|---|
| `repl_router` | `/repl` | `repl_agent.router` | none |
| `wiki_router` | `/api/wiki` | `wiki_router.router` | none (internal) |
| `wiki_plugin_router` | `/api/wiki/plugin` | `wiki_plugin_router.router` | bearer token |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/chat/stream` | Main chat SSE endpoint (public `ChatRequest`) |
| POST | `/session` | Create session |
| GET | `/sessions` | Session list |
| GET | `/session/{id}/history` | Session history |
| DELETE | `/session/{id}` | Delete session |
| GET | `/download/pptx/{filename}` | Download generated PPTX |
| POST | `/mining/tas` | Mining TAS trigger (stub) |

The wiki plugin chat endpoint (`/api/wiki/plugin/chat`) delegates to `app.state.chat_stream_handler`, which is `plugin_chat_stream` — the same `_chat_stream` function with an `InternalChatRequest` that carries wiki note context.

## Chat stream (`_chat_stream`)

The core chat handler (line 463):

1. Resolves the compiled graph and motor DB from `app.state`.
2. Sets up `base_config` with `thread_id=session_id`, `recursion_limit=30`.
3. Creates `trace_id` and `turn_id` (preserved across resume).
4. **Resume intent classification**: if `resume_value` is for a `task_confirm` or `postwads_choice` interrupt, calls `_resume_is_interrupt_answer` to classify whether the input is an answer or a new intent. New intent → drains the gate via `Command(resume="")` and processes the fresh query.
5. **Resume path**: `stream_input = Command(resume=request.resume_value)`.
6. **Fresh turn path**: `stream_input` is a dict with `HumanMessage` + per-turn `Overwrite([])` resets for all artifact lists, `past_steps`, `step_count`, and scratchpad fields (`resolved_refs`, `canonical_request`, `hitl_issues`, etc.).
7. Streams the graph via `astream`, converting LangGraph events to SSE events (`StreamStartEvent`, `NodeCompleteEvent`, `MessageEvent`, `ArtifactEvent`, `InterruptEvent`, `TokenEvent`, `ThinkingEvent`, `StatusEvent`, `SuggestionEvent`, `ErrorEvent`, `StreamEndEvent`).
8. On turn completion, persists the chat turn to `motor_db.chat_turns` and triggers user-memory background flush.

## SSE event types

Defined in `models.py`. See [React Frontend](../integrations/react-frontend.md) for the TypeScript mirroring.

| Event | Type field | Key fields |
|---|---|---|
| `StreamStartEvent` | `stream_start` | `session_id`, `query` |
| `NodeCompleteEvent` | `node_complete` | `node`, `step`, `elapsed` |
| `MessageEvent` | `message` | `role`, `agent`, `content`, `citations`, `step` |
| `TokenEvent` | `token` | `content`, `agent`, `node` |
| `ThinkingEvent` | `thinking` | `content`, `agent`, `node` |
| `StatusEvent` | `status` | `message`, `node` |
| `ArtifactEvent` | `artifact` | `artifact_id`, `artifact_type`, `mime`, `title`, `agent`, `data` |
| `SuggestionEvent` | `suggestion` | `content`, `step` |
| `InterruptEvent` | `interrupt` | `type`, `fields`, `param`, `message`, `route`, `options` |
| `ErrorEvent` | `error` | `message`, `node` |
| `StreamEndEvent` | `stream_end` | `total_steps`, `elapsed` |

`ArtifactType` is `html | image | markdown | pptx`.

## Request models

- `ChatRequest` — public chat contract. `extra="forbid"` (no internal context from clients). Fields: `query`, `session_id`, `resume_value` (`str | dict | None`), `user_id`.
- `InternalChatRequest` — private envelope with `wiki_context: WikiNoteContext | None`, constructed after the plugin adapter reads the vault.
- `PluginChatRequest` — plugin chat contract with `current_note_id`.

## Session management

Sessions are LangGraph threads identified by UUID. `agent_sessions.py` provides `list_session_summaries`, `load_session_history`, and `citations_from_fail_history_results`. Session history is persisted in MongoDB `chat_turns` collection (via `motor`).

## CORS

`CORSMiddleware` allows origins: `http://localhost:3000`, `http://localhost:5173` (dev) + `WIKI_FRONTEND_ORIGINS` env (comma-separated, for production cross-origin).

## When to consult this page

- Changing the lifespan startup/shutdown ordering.
- Adding or modifying SSE event types.
- Changing the chat stream resume/fresh-turn logic.
- Adding new HTTP endpoints.

## Validation

```bash
# Start server
uvicorn 08-YieldAgent.agent_server:app --port 8001

# E2E regression
pytest tests/test_e2e_regression.py -v

# Lifespan ordering
pytest 08-YieldAgent/repl_agent/tests/test_agent_server_lifespan.py -q
```
