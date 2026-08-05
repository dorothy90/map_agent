---
type: Reference
title: Wiki CLI Scripts
description: Command-line scripts for wiki sync, evidence enrichment, materialization, bootstrap warmup, super-concept generation, and vault migration.
tags: [wiki, cli, sync, enrichment, materialization, migration]
openwiki:
  roles: [operations, delivery]
  source_paths: [08-YieldAgent/sync_wiki.py, 08-YieldAgent/enrich_wiki.py, 08-YieldAgent/materialize_obsidian_wiki.py, 08-YieldAgent/bootstrap_wiki_warmup.py, 08-YieldAgent/make_super_concept.py, 08-YieldAgent/migrate_wiki_vault.py, 08-YieldAgent/migrate_v2_to_v3.py, 08-YieldAgent/enrich_extra_index.sh]
  symbols: [WikiSyncService, WikiEvidenceEnrichmentService, materialize_wiki, bootstrap_wiki_warmup]
  test_paths: [08-YieldAgent/tests/wiki/test_sync_wiki_cli.py, 08-YieldAgent/tests/wiki/test_enrich_wiki_cli.py, 08-YieldAgent/tests/wiki/test_materialize_obsidian_wiki_cli.py, 08-YieldAgent/tests/wiki/test_bootstrap_wiki_sync_metadata.py, 08-YieldAgent/tests/wiki/test_migrate_wiki_vault.py]
  invariants: ["All CLIs support --check (dry preview) and --apply modes.", "sync_wiki and enrich_wiki require --allow-external-llm to trigger LLM calls.", "bootstrap_wiki_warmup is idempotent — re-running replans with the latest raw docs."]
  validation_commands: ["pytest tests/wiki/test_sync_wiki_cli.py -q", "pytest tests/wiki/test_enrich_wiki_cli.py -q"]
---

# Wiki CLI Scripts

Command-line scripts drive the [wiki system](wiki-system.md) for sync, enrichment, materialization, and migration. All scripts use `wiki_config.resolve_wiki_paths` for vault path resolution and support `--check` (dry preview) and `--apply` modes where applicable.

## Script inventory

| Script | Invocation | Purpose |
|---|---|---|
| `sync_wiki.py` | `python sync_wiki.py --check` / `--apply --limit N` / `--resume --limit N` | Cron-ready incremental sync from OpenSearch → vault |
| `enrich_wiki.py` | `python enrich_wiki.py --check` / `--apply --allow-external-llm --source-index ...` | Evidence enrichment from a secondary OpenSearch index |
| `enrich_extra_index.sh` | `bash enrich_extra_index.sh` | Shell wrapper calling `enrich_wiki.py --apply` with `--source-index syld_gpt_2067627` |
| `materialize_obsidian_wiki.py` | `python materialize_obsidian_wiki.py --check` / `--apply` | Preview/apply Obsidian link materialization |
| `bootstrap_wiki_warmup.py` | `python bootstrap_wiki_warmup.py --dry-run` / `--apply [--top N --max-docs M]` | Direct synthesis: skip episodes, fetch raw docs per triple → LLM → `upsert_concept` |
| `make_super_concept.py` | `python make_super_concept.py --dry-run` / `--axis {fail_type,cause_oper,product} --value V --apply [--all]` | Cross-concept abstraction (super_concept generation) |
| `migrate_wiki_vault.py` | `python migrate_wiki_vault.py --source ... --target ... [--dry-run]` | Copy an external vault into the configured vault (symlink-safe, sha256-verified) |
| `migrate_v2_to_v3.py` | `python migrate_v2_to_v3.py --vault ... [--dry-run]` | Add v3 frontmatter fields to existing notes |
| `wiki_lint.py` | `python -m wiki_lint --vault ... [--json --log]` | Vault lint scan |

## sync_wiki.py — Incremental sync

Cron-ready script that scans OpenSearch by (product, fail_type, cause_oper) triples, fingerprints docs, compares against the manifest, and enqueues new/changed jobs to MongoDB. `WikiSyncService` claims jobs, calls `synthesize_concept_from_docs` (LLM), writes concepts via `upsert_concept`, materializes links, and updates the manifest. Source-removed triples are marked stale and a review is created.

- `--check`: dry run, prints the plan (new/changed/unchanged/removed).
- `--apply --limit N`: claims and processes up to N jobs.
- `--resume --limit N`: resumes interrupted materializations (repairs `dirty`/`failed` projection states even with no pending jobs).

## enrich_wiki.py — Evidence enrichment

Content-only enrichment that retrieves semantically related docs from a secondary OpenSearch index via embeddings, judges relevance via LLM (`EvidenceDecisionBatch`), attaches accepted evidence as `related_evidence` on concepts, and writes enrichment-owned `Source` notes (`EVD-...` IDs). Idempotent via `EvidenceManifestStore`.

Requires `--allow-external-llm` to trigger LLM calls. Optional `--product`, `--fail-type`, `--cause-oper` filters scope the run.

## materialize_obsidian_wiki.py — Link materialization

Runs `wiki_materializer.materialize_wiki(paths, apply=False/True)`. Preview (`--check`) shows what would be generated; `--apply` writes the derived notes, wikilinks, `index.md`, `overview.md`, and `.obsidian/graph.json`. Safe to re-run — generated notes are ownership-tagged and overwritten; operator-authored content is never touched.

## bootstrap_wiki_warmup.py — Bootstrap

Direct synthesis path that skips episodes: fetches raw docs per triple from OpenSearch, calls `synthesize_concept_from_docs` (LLM), and writes concepts via `upsert_concept`. Reads the `foundations.yaml` catalog first, then supplements with OpenSearch top-N. Idempotent — re-running replans with the latest raw docs. `--dry-run` previews; `--apply` writes. `--top N` limits triples; `--max-docs M` limits docs per triple.

## make_super_concept.py — Super-concept generation

Cross-concept abstraction. `--axis` selects the grouping axis (`fail_type`, `cause_oper`, or `product`); `--value V` selects the specific value; `--all` processes all values. Calls `wiki_summarizer.synthesize_super_concept`. `--dry-run` previews; `--apply` writes.

## migrate_wiki_vault.py — Vault migration

Copies an external vault into the configured vault. Symlink-safe (uses `O_NOFOLLOW`), sha256-verified. `--dry-run` previews the copy plan.

## migrate_v2_to_v3.py — Frontmatter migration

Adds v3 frontmatter fields (lifecycle, confidence, body_versions, evidence) to existing notes. `--dry-run` previews.

## wiki_lint.py — Vault integrity scan

`python -m wiki_lint` runs `wiki_lint.scan(vault)` and reports issues (orphan episodes, broken links, invalid frontmatter, alias asymmetry, duplicate concepts, high-priority gaps, low-confidence concepts, stale episodes, invalid relations, stale graph nodes). Also runs as a cron loop in `agent_server` (env `WIKI_LINT_CRON_HOURS`).

## Deployment procedure

See `08-YieldAgent/docs/wiki-deployment-procedure.md` for the full operator runbook covering prerequisites, dependency checks, and ordered execution of these scripts.

## When to consult this page

- Running wiki sync/enrichment/materialization.
- Adding a new CLI script or changing CLI flags.
- Setting up cron jobs for wiki maintenance.

## Validation

```bash
pytest tests/wiki/test_sync_wiki_cli.py -q
pytest tests/wiki/test_enrich_wiki_cli.py -q
pytest tests/wiki/test_materialize_obsidian_wiki_cli.py -q
pytest tests/wiki/test_bootstrap_wiki_sync_metadata.py -q
pytest tests/wiki/test_migrate_wiki_vault.py -q
```
