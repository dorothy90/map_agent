---
type: Domain
title: Data Layer
description: Oracle, OpenSearch, MongoDB, and embedding API usage across the yield-agent system — tables, indices, connection patterns, and data quirks.
tags: [data-layer, oracle, opensearch, mongodb, embeddings]
openwiki:
  roles: [domain, integration]
  source_paths: [08-YieldAgent/common.py, 08-YieldAgent/yield_db.py, 08-YieldAgent/wads_tools.py, 08-YieldAgent/fail_history_tools.py, 08-YieldAgent/lot_history_tools.py, 08-YieldAgent/user_memory.py]
  symbols: [get_oracle_connection, get_llm, _fetch_periods, search_opensearch_with_mode, _fetch_weekly_sql, query_lot_history]
  test_paths: [08-YieldAgent/tests/verify_failtype_inherit.py, 08-YieldAgent/tests/verify_postwads_failtype.py]
  invariants: ["Oracle connections use a shared pool from common.get_oracle_connection.", "OpenSearch search uses hybrid BM25 + kNN vector mode via search_opensearch_with_mode.", "MongoDB is used for LangGraph checkpoints (MongoDBSaver), session history (motor), user profiles, and wiki job queue — not for domain data."]
  validation_commands: ["pytest tests/verify_*.py -q"]
---

# Data Layer

The yield-agent system reads domain data from Oracle, searches documents in OpenSearch, persists checkpoints and sessions in MongoDB, and calls an embedding API for vector search.

## Data sources

| Database | Used by | Tables / Indices | Purpose |
|---|---|---|---|
| Oracle | `yield_db` | `DF_DIE_TO_WF_YLD` | Weekly/monthly/daily yield (PT1H/PT1C params + GMS) |
| Oracle | `yield_db` | `DF_GMS_YIELD_WEEKLY`, `DF_GMS_YIELD_MONTHLY`, `DF_GMS_YIELD_DAILY` | GMS yield |
| Oracle | `wads_tools` | `DF_WADS_REPORT`, `DF_WADS_WF_LIST` | WADS degradation reports + wafer groupkeys |
| Oracle | `map_agent` | `LANGGRAPH_DATA` (env `ORACLE_TABLE`) | Wafer binmap/cummap JSON data (`map_val_json` CLOB) |
| Oracle | `lot_history_tools` | `DF_FDC_ALARM`, `DF_QTIME_OVER`, `DF_TROUBLE_LOT`, `DF_FUTURE_ACTION`, `DF_SAMPLE_SPLIT` | LOT comprehensive history |
| Oracle | `relation_tree_agent` | `DF_WADS_MAIN_OPER` | Main oper candidates per (lotcd, fail_type) |
| Oracle | `wt_resp_agent` | `DF_WADS_GOOD_BAD_LOT` | Good/bad LOT ID pairs per (lotcd, fail_type) |
| OpenSearch | `fail_history_tools` | `fail-history` (env `OPENSEARCH_INDEX`) | Hybrid BM25+kNN fail history document search |
| Embedding API | `fail_history_tools` | OpenRouter `qwen3-embedding-8b` (4096-dim) | Vector embeddings for kNN search |
| MongoDB | `agent_server` | `yield_agent` DB | LangGraph checkpoints (`MongoDBSaver`), session history (`motor`), user profiles |
| MongoDB | `wiki_job_store` | `yield_agent` DB | Wiki sync job queue + global lock |
| Mining API | `mining_agent` | (currently dummy `mining_dummy_api`) | Gini-based parameter contribution analysis |
| LLM APIs | all agents | OpenRouter models (GLM-4.7, etc.) | Analysis, SQL gen, synthesis, PPT design |

## Oracle connection

`common.get_oracle_connection` provides a shared connection factory used by all Oracle-reading agents. `common.to_user_message` translates `oracledb.DatabaseError` and connection errors into user-friendly Korean messages.

### Known Oracle quirks

- **WEEK column**: `"YYYY-WW"` format (e.g. `2026-06`); agent internals use `"2026-W06"` ISO format → conversion needed.
- **GMS tables**: `PERIOD_DATE` as `"YYYYWW"` / `"YYYYMM"` / `"YYYYMMDD"` (numeric strings).
- **CLOB columns**: output type handler for `DB_TYPE_LONG` conversion needed (used by `wads_agent`).
- **LOT_ID**: case conversion (`lot_id_variants`) needed.

## OpenSearch

`fail_history_tools.search_opensearch_with_mode` performs hybrid search (BM25 + kNN vector) against the `fail-history` index. The embedding API (OpenRouter `qwen3-embedding-8b`, 4096 dimensions) generates query vectors. Results are grouped by (product, fail_type, cause_oper) triples.

The [wiki system](../wiki/wiki-system.md) also uses OpenSearch: `wiki_sync.OpenSearchWikiScanner` scans by triple for incremental sync, and `wiki_evidence_enrichment` retrieves related docs from a secondary index via embeddings.

## MongoDB

MongoDB (`mongodb://localhost:27017`, db `yield_agent`) serves three purposes:

1. **LangGraph checkpoints** — `MongoDBSaver.from_conn_string` compiles the supervisor graph with checkpointing for interrupt/resume.
2. **Session history** — `motor` (async MongoDB client) stores `chat_turns` for session listing and history.
<!-- openwiki: broken internal link [../operations/user-memory.md] file "../operations/user-memory.md" does not exist. Fix the href or restore the target, then delete this comment. -->
3. **User profiles** — `user_memory.py` stores per-`user_id` preference text in the `user_profiles` collection. See [User Memory](../operations/user-memory.md).
4. **Wiki job queue** — `wiki_job_store.WikiJobStore` uses MongoDB for sync jobs and a global lock.

## LLM factory

`common.get_llm` returns a configured `ChatOpenAI` instance (OpenRouter backend). The module-level singleton `_model` in `orch_utils.py` is shared across planner, supervisor, and replanner. Agents may use different models for specific tasks (e.g. `WADS_SQL_GEN_MODEL` for SQL generation, GLM-4.7 for PPT design).

## When to consult this page

- Adding a new Oracle table or changing SQL queries.
- Modifying OpenSearch search behavior or embedding models.
- Changing MongoDB collections or connection configuration.

## Validation

```bash
pytest tests/verify_*.py -q
```

These verification scripts test specific data-flow properties (failtype inheritance, relation chains, post-WADS failtype, mining artifacts).
