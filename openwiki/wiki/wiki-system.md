---
type: Architecture
title: Wiki System
description: The Obsidian-backed failure-history knowledge wiki — vault storage, incremental sync from OpenSearch, LLM synthesis, materialization of wikilinks, evidence enrichment, linting, and graph projection.
tags: [wiki, obsidian, vault, sync, materialization, enrichment, knowledge-graph]
openwiki:
  roles: [architecture, domain, operations]
  source_paths: [08-YieldAgent/wiki_config.py, 08-YieldAgent/wiki_store.py, 08-YieldAgent/wiki_sync.py, 08-YieldAgent/wiki_materializer.py, 08-YieldAgent/wiki_summarizer.py, 08-YieldAgent/wiki_queue.py, 08-YieldAgent/wiki_safe_mutation.py, 08-YieldAgent/wiki_graph_projection.py, 08-YieldAgent/wiki_evidence_enrichment.py, 08-YieldAgent/wiki_lint.py]
  symbols: [WikiPaths, upsert_concept, materialize_wiki, WikiSyncService, WikiQueue, synthesize_concept, PinnedWikiMutation, WikiGraphProjection, WikiEvidenceEnrichmentService, scan]
  test_paths: [08-YieldAgent/tests/wiki/test_wiki_materializer.py, 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py, 08-YieldAgent/tests/wiki/test_wiki_sync_service.py, 08-YieldAgent/tests/wiki/test_wiki_queue_graph.py]
  invariants: ["The vault is the single source of truth — notes are standard Obsidian Markdown with YAML frontmatter.", "All writes go through PinnedWikiMutation (O_NOFOLLOW, atomic rename, fsync, tombstone quarantine).", "Generated notes are ownership-tagged; operator edits are conflict-detected via generated_body_sha256 mismatch.", "Episodes are immutable (content-addressed); concepts are mutable rollups with body_versions capped at 5."]
  validation_commands: ["pytest tests/wiki/test_wiki_materializer.py -q", "pytest tests/wiki/test_wiki_store_external_vault.py -q"]
---

# Wiki System

The wiki system is an **Obsidian-backed knowledge wiki** for semiconductor yield failure history. It ingests raw fail-history documents from OpenSearch, synthesizes them via LLM into Markdown notes with YAML frontmatter, stores them on disk as an Obsidian vault, and serves them over HTTP. The vault is the single source of truth; all reads resolve through filesystem scans of frontmatter.

## Architecture

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    OS[OpenSearch<br/>fail-history index] --> Scanner[wiki_sync<br/>OpenSearchWikiScanner]
    Scanner --> Snapshot[TripleSnapshot<br/>fingerprint + doc_ids]
    Snapshot --> Plan[plan_sync<br/>new/changed/unchanged/removed]
    Plan --> JobStore[wiki_job_store<br/>MongoDB queue]
    JobStore --> Sync[WikiSyncService]
    Sync --> Sum[wiki_summarizer<br/>synthesize_concept_from_docs]
    Sum --> Store[wiki_store.upsert_concept]
    Store --> Mat[wiki_materializer<br/>render Obsidian wikilinks]
    Mat --> Vault[(Obsidian Vault)]
    Vault --> Router[wiki_router<br/>/api/wiki/graph /node]
    Vault --> Plugin[wiki_plugin_router<br/>/api/wiki/plugin/...]
    Vault --> Projection[wiki_graph_projection<br/>read-only graph cache]
    Search[fail_history_tools do_search] --> Gate[wiki_store.lookup_concept_body<br/>wiki-first gate]
    Gate --> Search
    Search --> Queue[wiki_queue<br/>episode summarize + concept synthesis]
    Queue --> Store
```

## Vault layout

The vault root is `WIKI_VAULT_PATH` (env), defaulting to `08-YieldAgent/wiki/`. Managed directories:

| Directory | Contents |
|---|---|
| `episodes/` | Immutable episode snapshots (content-addressed) |
| `concepts/` | Mutable concept rollups (synthesized from episodes) |
| `super_concepts/` | Cross-concept abstractions |
| `aliases/` | Symmetric alias pairs |
| `products/`, `product_fails/`, `operations/`, `product_tree/` | Derived/generated axis nodes |
| `entities/`, `relations/` | Graph entities and relations (generated) |
| `sources/` | Source notes (enrichment-owned) |
| `reviews/` | Operator review notes |
| `.obsidian/` | Obsidian config (graph.json) |
| `.yield-wiki/` | State: `manifest.json`, `evidence-manifest.json`, `reviews.lock` |

## Core components

### wiki_config.py

Vault path resolution and safe-path validation. `WikiPaths` is a frozen dataclass of all vault paths. `resolve_wiki_paths(env, default_root)` resolves from environment. `initialize_wiki_vault` creates directories; `validate_wiki_vault` checks for symlink/directory-swap races via `O_NOFOLLOW` and inode checks. Foundation for all other wiki modules.

### wiki_safe_mutation.py

Descriptor-pinned, race-safe file mutations. `PinnedWikiMutation` is a context manager providing `snapshot()`, `replace_text()`, `delete()`, `open_lock_file()`, and `list_paths()`. Resists symlink swaps and TOCTOU races using `O_NOFOLLOW`, `renameat2`/`renameatx_np` (RENAME_NOREPLACE), fsync, and tombstone quarantining. Optimistic concurrency via `FileSnapshot` (device+inode+mtime+sha256).

### wiki_store.py

Core note storage. Key functions:

- `upsert_episode()` — writes immutable episode snapshots.
- `upsert_concept()` — writes mutable concept rollups with `body_versions` (capped at 5). Detects operator edits via `generated_body_sha256` mismatch, raising `ConceptEditConflict` and creating a pending review instead of overwriting.
- `upsert_alias()`, `upsert_super_concept()` — alias and super-concept management.
- `lookup_concept_body()` — the **wiki-first/wiki-assisted gate** used by `fail_history_tools.do_search`.
- `read_node()` — node detail + backlinks.
- `compute_evidence_diversity()`, `update_concept_evidence()` — evidence management.
- `materialize_obsidian_wiki()` — lazy-imports and calls `wiki_materializer`.

Module-level singleton `_PATHS = resolve_wiki_paths()` at import time.

### wiki_sync.py

Incremental sync contracts. `TripleKey` (product, fail_type, cause_oper), `TripleSnapshot` (fingerprinted group of docs for one triple), `SyncPlan` (new/changed/source_removed/unchanged). `OpenSearchWikiScanner` performs composite aggregation + doc fetch + dedup. `WikiSyncService` coordinates scanner → job store → synthesis → store → materialize → manifest, with a global lock via job store.

### wiki_summarizer.py

LLM-based summarization/synthesis with three modes:

- `summarize()` — single search → episode condensation.
- `synthesize_concept()` — accumulated N-episode → concept body synthesis with citations/entities/relations.
- `synthesize_concept_from_docs()` — direct raw-docs → concept synthesis (bootstrap path).
- `synthesize_super_concept()` — cross-concept abstraction.

Uses LangChain `with_structured_output`. Enforces source-citation grounding via `restrict_concept_synthesis_sources` and `validate_body_source_citations`.

### wiki_queue.py

Async two-stage in-process queue (summarize → persist) with 2 workers, 3-retry exponential backoff, 64KB payload guard, evidence-diversity-triggered concept synthesis, and privacy context propagation. Module-level singleton `wiki_queue`. Started/stopped in `agent_server.lifespan`. `summarize_enqueue` is called from `fail_history_tools.do_search`.

### wiki_materializer.py

Deterministically renders Obsidian wikilinks from concept frontmatter. Generates derived notes (`products/`, `product_fails/`, `operations/`, `product_tree/` 3-tier, `entities/`, `relations/`, `sources/`, `index.md`, `overview.md`, `.obsidian/graph.json`). Appends managed `<!-- yield-wiki:knowledge-links -->` blocks to concepts/super-concepts. Handles stale graph node marking, migration of legacy hash-only filenames to readable names, owner validation (`generated_by`), and safe deletion of orphaned generated notes. Supports `--check` (dry preview) and `--apply`.

### wiki_graph_projection.py

Read-only, one-hop projection of materialized graph frontmatter into an in-memory `WikiGraphProjection` (concepts/entities/relations/sources + reverse indexes). Thread-safe cached by vault path fingerprint. `expand_concepts()` returns a bounded `GraphContext` for retrieval augmentation. Used by `fail_history_tools` to build graph context for search results.

### wiki_evidence_enrichment.py

Content-only evidence enrichment. Reads concept snapshots, retrieves related docs from a secondary OpenSearch index via embeddings, judges relevance via structured LLM output (`EvidenceDecisionBatch`), attaches accepted evidence as `related_evidence` on concepts, and writes enrichment-owned `Source` notes. Idempotent via `EvidenceManifestStore`.

### wiki_lint.py

Vault integrity scanner. Detects: orphan episodes, broken links, invalid frontmatter, alias asymmetry, duplicate concepts, high-priority gaps (from `foundations.yaml`), low-confidence concepts, stale episodes, invalid relations, stale graph nodes. Writes results to `lint_logs/`. Runs as a cron loop in `agent_server` (env `WIKI_LINT_CRON_HOURS`).

### wiki_manifest.py

Atomic portable success manifest (JSON at `.yield-wiki/manifest.json`). Tracks per-triple source fingerprint + concept id/version. Manages projection state (`dirty`/`failed`/`clean`) for materialization recovery.

### wiki_job_store.py

MongoDB-backed job queue and global lock for incremental sync. Jobs keyed by `sha256(triple|fingerprint)`. Lease-based claiming with exponential backoff retry, terminal failure after max attempts.

### wiki_review_store.py

Operator review CRUD stored as Markdown notes in `reviews/`. Uses `fcntl.flock` for mutual exclusion. Versioned updates with history block, markdown escaping of user input.

## Enrichment paths

**Path A — Incremental sync** (`sync_wiki.py`): Scans OpenSearch by triples → fingerprints docs → compares against manifest → enqueues new/changed jobs to MongoDB → `WikiSyncService` claims jobs → `synthesize_concept_from_docs` (LLM) → `upsert_concept` → materialize → update manifest. See [Wiki CLI](wiki-cli.md).

**Path B — Search-time queue** (`wiki_queue.py`): `fail_history_tools.do_search` checks `lookup_concept_body` for the wiki-first gate. If the gate passes, the synthesized concept body is returned (0 LLM calls). Otherwise, raw results are enqueued → summarize worker → persist worker → evidence diversity check → `concept_synthesis` task if ≥2 episodes and diversity ≥0.3.

**Path C — Evidence enrichment** (`enrich_wiki.py`): Retrieves semantically related docs from a secondary OpenSearch index, judges relevance via LLM, attaches `related_evidence` to concepts, writes `Source` notes. See [Wiki CLI](wiki-cli.md).

## Safety model

- Every write goes through `PinnedWikiMutation` (descriptor-pinned, `O_NOFOLLOW`, atomic `renameat2` RENAME_NOREPLACE, fsync, tombstone quarantine).
- Generated notes are ownership-tagged (`generated_by: yield-wiki-materializer` or `yield-wiki-evidence-enricher`); the materializer can safely overwrite or delete them.
- Operator edits to concept bodies are detected via `generated_body_sha256` mismatch → `ConceptEditConflict` → pending review (no silent overwrite).
- Episodes are immutable (content-addressed by `_episode_key` hash). Sync jobs are idempotent by `sha256(triple|fingerprint)`. Bootstrap is idempotent (re-run replans with latest raw).

## When to consult this page

- Changing vault storage, concept upsert, or the wiki-first gate.
- Modifying sync, materialization, or enrichment behavior.
- Adding new generated note types or graph entities/relations.

## Validation

```bash
pytest tests/wiki/test_wiki_materializer.py -q
pytest tests/wiki/test_wiki_store_external_vault.py -q
pytest tests/wiki/test_wiki_sync_service.py -q
pytest tests/wiki/test_wiki_queue_graph.py -q
```
