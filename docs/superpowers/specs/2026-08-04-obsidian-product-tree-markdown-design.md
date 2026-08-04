# Obsidian Product Tree Markdown Design

**Date:** 2026-08-04

## Goal

Generate a real Markdown-only `LOTCD → FAIL → OPER` projection that Obsidian's
native Graph view can display without the Concept, Source, Entity, Relation,
Review, and Super Concept links that belong to the full knowledge graph.

## Root Cause

The existing frontend does not render the full Wiki graph. Its
`product_tree` view creates a dedicated projection containing only product,
product-scoped fail, and triple-scoped operation leaves. The current Obsidian
Graph reads every internal link in the Vault, so the semantic knowledge graph
dominates the product hierarchy and Concept nodes become the visual hubs.

## Projection Layout

The materializer owns one direct child directory named `product_tree`. Files
remain flat so they stay inside the existing pinned mutation safety boundary:

```text
product_tree/
├── 4SS.md
├── 4SS__EASY.md
└── 4SS__EASY__PRE_METAL_CLN.md
```

Filenames use the existing stable filename sanitizer. Double underscores
separate canonical hierarchy components.

The graph topology is expressed only by parent-to-child Wiki links:

```text
4SS.md
└── [[product_tree/4SS__EASY|EASY]]

4SS__EASY.md
└── [[product_tree/4SS__EASY__PRE_METAL_CLN|PRE METAL CLN]]

4SS__EASY__PRE_METAL_CLN.md
└── generated Wiki body for concept:4SS|PRE METAL CLN|EASY
```

The leaf does not link to the canonical Concept, Sources, Entities, Relations,
Reviews, or Super Concepts. It copies the current generated Concept body,
converting any internal Wiki link to its visible plain-text label, and records
the canonical `concept_id` in frontmatter as non-link metadata. Normal prose,
tables, Mermaid blocks, and citation IDs remain unchanged. This keeps the
native Graph at exactly three tiers while the canonical files remain the source
of truth.

## Ownership and Incremental Behavior

All projection notes use `generated_by: yield-wiki-materializer` and distinct
node types:

- `product_tree_product`
- `product_tree_fail`
- `product_tree_oper`

The materializer writes them through `PinnedWikiMutation`, validates the exact
path implied by metadata, and deletes only stale files with matching generated
ownership. Manual or foreign files in `product_tree` cause a collision rather
than being overwritten or removed. Unchanged reruns perform no file writes.

## Obsidian Graph Configuration

New Vaults receive the native Graph search filter `path:product_tree` and
`showOrphans: false`. Existing `.obsidian/graph.json` remains operator-owned and
is not silently overwritten. During the authorized live Vault rollout, its
current JSON is backed up and only `search` and `showOrphans` are changed,
preserving all other Graph preferences.

Clearing the Graph search filter continues to expose the full semantic
knowledge graph.

## Sync Integration

No new scheduler or command is added. `bootstrap_wiki`, `sync_wiki`, and direct
materialization already converge through `materialize_obsidian_wiki`; extending
the materializer makes all three paths update `product_tree` automatically.

## Validation

Automated tests must prove:

- the exact three projection files and two hierarchy links are generated;
- the operation leaf contains the canonical Concept body but no internal links
  to Concept, Source, Entity, Relation, Review, or Super Concept notes;
- duplicate operation names under different triples remain separate files;
- stale generated projection files are deleted while foreign files are kept;
- a second materialization is byte-for-byte idempotent;
- new Vault Graph defaults select `path:product_tree` without overwriting an
  existing Graph configuration.

The live rollout must back up the Vault, run materialization, verify the three
tiers from actual Markdown, check a second run has zero changes, and leave the
backup available for recovery.
