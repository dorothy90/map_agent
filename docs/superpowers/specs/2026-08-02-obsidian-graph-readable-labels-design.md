# Obsidian Graph Readable Labels Design

**Date:** 2026-08-02  
**Milestone:** M5 UI completion  
**Scope:** Readable Entity and Relation filenames plus live Citation-open verification

## 1. Goal

Make generated Entity and Relation nodes understandable in Obsidian Graph view without weakening the deterministic graph identity, collision protection, stale-note handling, or Graph-assisted RAG contracts introduced in M5. Complete the remaining Desktop acceptance check by opening a canonical Source citation from the Obsidian Plugin while the real Backend is running.

## 2. Current behavior

Entity and Relation Markdown filenames are the 64-character SHA-256 suffix of their canonical graph IDs. The full IDs are stable and safe, but Obsidian Graph view displays the basename, so users see hashes instead of domain terms.

The Plugin already renders citations carrying a canonical `source_path` and opens that path with Obsidian's `workspace.openLinkText`. Automated coverage exists; the remaining work is a real Desktop verification against a running authenticated Backend.

## 3. Considered approaches

### 3.1 Natural label plus short hash — selected

- Entity: `Plasma Damage--a31f08c2.md`
- Relation: `Plasma Damage causes Oxide Defect--70bd12e4.md`

This makes Graph nodes readable while retaining a deterministic collision suffix.

### 3.2 Natural label only

This is the most readable form, but different canonical values can normalize to the same filename. It is rejected because collision detection would turn valid graph data into a materialization error.

### 3.3 Hash filename with frontmatter alias

This preserves all current paths, but Obsidian Graph view does not reliably use aliases as node labels. It does not solve the observed UI problem and is rejected.

## 4. Filename contract

The canonical graph ID remains unchanged:

```text
entity:sha256:<64 hex characters>
relation:sha256:<64 hex characters>
```

Only the generated Markdown path changes.

```text
entities/<sanitized canonical_name>--<first 8 hash characters>.md
relations/<sanitized subject predicate object>--<first 8 hash characters>.md
```

Sanitization preserves Unicode letters, numbers, spaces, `_`, and `-`. Characters forbidden or unsafe in a cross-platform Vault filename, control characters, and path separators are replaced with `_`. Repeated replacement characters are collapsed and leading or trailing whitespace and dots are removed. The readable prefix is truncated on a Unicode boundary so the complete UTF-8 filename stays below 180 bytes. If the readable portion becomes empty, the node type (`entity` or `relation`) is used. The hash suffix is always present.

The short hash is for filename collision resistance and display only. Frontmatter IDs, relation endpoint IDs, origin Concept IDs, fingerprints, cache identity, and Graph RAG lookup continue to use the full canonical IDs.

## 5. Materialization and migration

The materializer calculates all new paths before writing and applies its existing collision and Vault containment checks. All generated wikilinks in Concept, Entity, Relation, overview, and index notes use the new paths.

Before writing, the materializer scans generated Entity and Relation notes by their full frontmatter `id`. At most one existing materializer-owned path may claim an ID. If the same active ID exists at its old hash-only path, that path is treated as a path migration, not a semantic removal.

On the first successful materialization:

1. readable files are created;
2. old hash-only files for the same active full IDs are deleted only after their readable replacements are written;
3. genuinely removed Entity and Relation IDs retain the existing stale-note behavior;
4. generated links are rewritten with readable paths and readable Relation link labels;
5. a second identical run produces no file changes.

User-owned Markdown is never renamed or deleted. A target collision with a note not owned by `yield-wiki-materializer`, an ID mismatch, a symlink escape, or a path outside the configured Vault remains a fatal no-write error.

## 6. Citation Desktop verification

Citation behavior is not redesigned. The acceptance run uses the existing implementation:

```text
Plugin Chat → authenticated Backend SSE → canonical source_path
→ Citation button → workspace.openLinkText → Source Markdown note
```

The Backend must be running with the external `SYLDAIX/YieldWiki` Vault and valid Plugin authentication. The check passes only when a real Wiki-grounded answer renders at least one Citation button and clicking it opens the matching `sources/<doc_id>.md` note in Obsidian Desktop.

No new download authorization, embedding call, external database, or Plugin-side index is introduced.

## 7. Error handling

- Empty readable label: use the node type fallback plus the hash suffix.
- Filename collision: fail before changing any file.
- Duplicate generated notes claiming the same full ID: fail before changing any file.
- Existing user-owned target: fail before changing any file.
- Missing or invalid Citation `source_path`: do not fabricate a path; preserve current non-clickable rendering.
- Backend unavailable during Desktop verification: report the verification as blocked, not passed.

## 8. Testing and acceptance

Automated tests must prove:

- English, Korean, spaces, and unsafe characters produce deterministic readable paths.
- Two labels with the same sanitized prefix remain distinct through the hash suffix.
- Entity and Relation frontmatter retain full canonical IDs.
- all generated wikilinks target the readable files.
- the first migration removes obsolete managed hash-only paths without touching user files.
- the second materialization is idempotent.
- stale graph notes and Graph projection continue to work.
- existing Wiki, confirm-edit, user-memory, and Plugin tests remain green.
- the Plugin production build succeeds.

Real acceptance must prove:

1. the actual `4SS / EASY / PRE METAL CLN` graph displays readable Entity and Relation node names in Obsidian;
2. a real authenticated Wiki-grounded Chat response includes canonical citations;
3. clicking a Citation opens the corresponding Source note;
4. the materializer check mode reports no pending changes after the applied run.

## 9. Non-goals

- Changing Entity or Relation semantic extraction
- Changing embeddings or the OpenSearch schema
- Adding Neo4j or another graph database
- Redesigning the Plugin Chat or Citation UI
- Translating relation predicates for display
- Changing the existing React frontend
- Starting M6 ingestion automation
