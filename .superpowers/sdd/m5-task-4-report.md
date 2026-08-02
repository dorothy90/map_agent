# M5 Task 4 — Read-only Wiki Graph projection

## Scope

- Base: `4fc9cd161185029e9f9a21ca89d49da00e6a3e99`
- Branch: `feat/obsidian-wiki-platform`
- Added a frozen, frontmatter-derived `WikiGraphProjection` with exact-ID indexes and one-hop expansion.
- Added typed `GraphRelation` and `GraphContext` output models.
- Added no graph database, NetworkX dependency, background watcher, or natural-language matching.

## RED evidence

### Initial missing projection contract

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py
```

Observed result: `5 failed in 0.31s`.

- Four tests failed with `ModuleNotFoundError: No module named 'wiki_graph_projection'`.
- One test failed with `ImportError: cannot import name 'GraphContext' from 'wiki_graph_models'`.

### Source-only one-hop adjacency

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py
```

Observed result: `1 failed, 6 passed in 0.35s`.

- `test_related_concept_can_share_only_a_canonical_source` expected `[SEED, RELATED]` but received only `[SEED]` before Concept-to-Source adjacency was added.

### Malformed-note isolation

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py
```

Observed result: `1 failed, 7 passed in 0.18s`.

- `test_malformed_note_does_not_hide_valid_projection_records` raised `yaml.parser.ParserError` before malformed individual notes were isolated.

## GREEN evidence

### Focused projection and model tests

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py tests/wiki/test_wiki_graph_models.py
```

Observed result: `10 passed in 0.13s`.

Covered active/stale filtering, body-text non-traversal, exact seed and Source IDs, Source-backed edge validation, shared Entity and Source one-hop discovery, de-duplication, all three expansion bounds, empty missing-seed behavior, independent Entity/Source symlink rejection through the canonical resolver, frozen records/maps, malformed-note isolation, and path/size/`mtime_ns` cache invalidation.

### Materializer-to-projection integration

Executed a temporary real Vault flow through `materialize_wiki(..., apply=True)` and `build_graph_projection(...)`.

Observed result:

```json
{"materialized_entities": 2, "materialized_relations": 1, "source_doc_ids": ["FH-E2E"], "cache_reused": true}
```

### Full Wiki suite

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki
```

Observed result: `211 passed in 8.58s`.

## Self-review

- Projection records are frozen dataclasses and all exposed indexes/adjacency maps are read-only mapping proxies with tuple values.
- Traversal uses only validated frontmatter; Relation body text cannot activate or alter an edge.
- Active Relations require an exact existing Concept, two exact active Entity IDs, a valid typed predicate/confidence, and every exact Source `doc_id`.
- Cache fingerprint input is the canonical relative path, byte size, and `mtime_ns` of accepted Concept/Entity/Relation/Source Markdown.
- The cache keeps only the latest fingerprint/projection per Vault root and adds no watcher or background thread.
- Existing brainstorm files were left untouched and untracked.

## Concerns

None blocking for Task 4. Invalid or unsafe individual notes are intentionally omitted from this read-only projection so valid graph records remain available; Wiki lint remains the operator-visible validation surface.

## Review fix — origin-local validation and bounded Relation evidence

### RED

Command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py
```

Observed result: `4 failed, 8 passed in 0.45s`.

- Both parameterizations of `test_relation_requires_both_entities_to_belong_to_origin_concept` returned the invalid Relation when either its subject or object Entity omitted the origin Concept from `source_concept_ids`.
- `test_relation_requires_sources_cited_by_its_origin_concept` returned a Relation whose Source existed globally but was cited only by another Concept.
- `test_relation_source_ids_are_trimmed_to_the_context_source_bound` returned `['FH-1', 'FH-2']` inside the Relation while the bounded top-level context contained only `['FH-1']`.

### Fix

- Relation admission now requires the origin Concept to occur in both endpoint Entity `source_concept_ids` lists.
- Every Relation Source must both exist canonically and occur in the origin Concept's exact citation-derived `source_doc_ids`.
- Expansion computes the bounded Source set before producing output Relations, trims each Relation to that set, and omits Relations left without bounded Source evidence.
- The existing general bounds test now gives the seed Concept exact citations for its generated Relations, preventing a vacuous pass under the stricter validation.

### GREEN

Focused command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_projection.py tests/wiki/test_wiki_graph_models.py
```

Observed result: `14 passed in 0.17s`.

Full Wiki command:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki
```

Observed result: `215 passed in 9.76s`.
