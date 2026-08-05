---
type: Operations
title: Testing and Validation
description: Test layout, frameworks, and focused validation commands for the yield-agent, wiki, REPL, and Obsidian plugin subsystems.
tags: [testing, pytest, vitest, validation, e2e]
openwiki:
  roles: [testing, operations]
  source_paths: [08-YieldAgent/tests/conftest.py, 08-YieldAgent/tests/e2e_client.py, 08-YieldAgent/tests/wiki/conftest.py]
  symbols: [Session, TurnResult, run_turn, server_is_up, clear_wiki_modules]
  test_paths: [08-YieldAgent/tests/test_e2e_regression.py, 08-YieldAgent/tests/wiki/test_wiki_materializer.py, 08-YieldAgent/repl_agent/tests/test_router.py, 08-YieldAgent/repl_agent/tests/test_process_runtime.py]
  invariants: ["E2E regression tests require a live server on :8001.", "Wiki tests use clear_wiki_modules fixture to reload wiki_config/store/router with env.", "REPL tests use FakeRuntime/FakeAgent — no live LLM required.", "Obsidian plugin tests use Vitest + jsdom with a stubbed obsidian module."]
  validation_commands: ["pytest 08-YieldAgent/tests/ -q", "cd obsidian/plugin && npx vitest run"]
---

# Testing and Validation

## Test layout

| Directory | Framework | Scope |
|---|---|---|
| `08-YieldAgent/tests/` | pytest | E2E regression, user memory, verification scripts |
| `08-YieldAgent/tests/wiki/` | pytest | Wiki system (33 test files) |
| `08-YieldAgent/repl_agent/tests/` | pytest | REPL agent (router, session, runtime, worker, events) |
| `obsidian/plugin/tests/` | Vitest + jsdom | Obsidian plugin (api, view, main, install-vault) |

## E2E regression

`08-YieldAgent/tests/test_e2e_regression.py` pins behavior through the step-by-step node-consolidation refactor. The headline case is the "4SS 못잡음" regression: the planner must put "4SS" into the `lotcd` slot (NOT `unit`) and the turn must run `yield_agent` without a missing-제품코드 HITL block. Cases assert against the live server's `traces/*.jsonl` (structured outcome) plus SSE output. Requires server on `:8001`.

`tests/e2e_client.py` provides `Session`, `TurnResult`, `run_turn`, `server_is_up`, `VALID_UNITS`, `coerce_periods` — a React stand-in that sends dict resumes.

```bash
uvicorn 08-YieldAgent.agent_server:app --port 8001
pytest tests/test_e2e_regression.py -v
```

## Verification scripts

Standalone scripts that test specific data-flow properties:

| Script | What it verifies |
|---|---|
| `tests/verify_mining_artifact.py` | Mining agent artifact structure |
| `tests/verify_failtype_inherit.py` | Failtype inheritance through WADS chaining |
| `tests/verify_relation_chain.py` | Relation tree agent chaining |
| `tests/verify_postwads_failtype.py` | Post-WADS failtype selection |

```bash
pytest tests/verify_*.py -q
```

## Wiki tests

33 test files under `tests/wiki/`. Shared fixtures in `conftest.py`: `clear_wiki_modules` (reloads `wiki_config`/`wiki_store`/`wiki_router` with env). Key test files:

| Test file | Component |
|---|---|
| `test_wiki_materializer.py` (66KB) | Materializer + safe mutation (transactions, collisions, stale nodes, migration, hooks) |
| `test_wiki_store_external_vault.py` | wiki_store with external vault env |
| `test_wiki_sync_service.py` | WikiSyncService (apply/resume/recovery/lock) |
| `test_wiki_queue_graph.py` | wiki_queue concept synthesis with graph entities/relations |
| `test_wiki_queue_privacy.py` | wiki_queue privacy context |
| `test_wiki_summarizer.py` | Summarize/synthesize/citation grounding |
| `test_wiki_graph_projection.py` | Graph projection build/expand/cache |
| `test_wiki_evidence_enrichment.py` | Evidence retriever/judge/manifest/attach |
| `test_wiki_lint.py` | wiki_lint.scan |
| `test_wiki_plugin_router.py` | All plugin endpoints, auth, reviews |
| `test_wiki_plugin_chat.py` | Plugin chat with wiki context (privacy, tracing) |
| `test_wiki_runbook.py` | Validates `docs/wiki-deployment-procedure.md` references |

```bash
pytest tests/wiki/ -q
```

## REPL tests

| Test file | Component |
|---|---|
| `test_router.py` | End-to-end SSE contract via FastAPI TestClient (FakeRuntime, FakeAgent) |
| `test_session_store.py` | Session lifecycle with FakeRuntime/BlockingCancelRuntime |
| `test_process_runtime.py` | Real ProcessPythonRuntime (spawn processes, timeout, cancel, races) |
| `test_worker.py` | Namespace, persistence, bounded stdout, Plotly artifacts, error classification |
| `test_events.py` | to_tool_payload, error shape, sequence allocation |
| `test_agent_server_lifespan.py` | App shutdown ordering (wiki → repl → mongo) |
| `test_mock_routes.py` | Mock data filtering |

```bash
pytest 08-YieldAgent/repl_agent/tests/ -q
```

## Obsidian plugin tests

Vitest 3.2.7 + jsdom. `vitest.config.ts` aliases `obsidian` → `tests/obsidian-runtime.ts` (stub).

| Test file | Coverage |
|---|---|
| `tests/api.test.ts` | REST methods, bearer auth, SSE stream parsing, error mapping |
| `tests/view.test.ts` (49KB) | Tab switching, search, review CRUD, interrupt/resume, abort, connection state |
| `tests/main.test.ts` | Settings loading, view registration, ribbon, command |
| `tests/install-vault.test.ts` | Artifact copy, data.json preservation |

```bash
cd obsidian/plugin && npx vitest run
```

## Conditional expensive checks

- **E2E regression** (`test_e2e_regression.py`): requires a live server + Oracle + OpenSearch + LLM. Run only when changing orchestration behavior or after a planner/supervisor refactor.
- **Wiki integration** (`test_fail_history_wiki_graph.py`): requires OpenSearch + LLM. Run when changing the wiki-first gate or graph projection.
- **Plugin build** (`npm run build`): `tsc --noEmit` + esbuild bundle. Run when changing plugin TypeScript.
- **REPL process runtime** (`test_process_runtime.py`): spawns real OS processes. Run when changing runtime isolation.
