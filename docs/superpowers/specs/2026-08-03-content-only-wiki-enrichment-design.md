# Content-only OpenSearch Wiki Enrichment Design

**Date:** 2026-08-03
**Status:** Approved for written specification; implementation pending review

## 1. Goal

Use the existing `syld_gpt_2067627` OpenSearch index to expand the Obsidian Wiki with related source material even though the index has no `product`, `fail_type`, or `cause_oper` metadata.

An operator must be able to perform the complete enrichment with one command:

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

The command searches candidate evidence, validates semantic relevance, records incremental state, attaches approved evidence to existing Concepts, and materializes the resulting Obsidian Markdown graph.

## 2. Confirmed source index

The source index is read-only:

```text
index: syld_gpt_2067627
document count observed during design: 12
text field: page_content
vector field: embedding
vector dimension: 4096
vector engine: FAISS HNSW
similarity: cosine
source fields: source_file, page_num, download_url
embedding model: qwen/qwen3-embedding-8b
```

The embedding model was confirmed from the producer repository rather than inferred from vector dimension alone.

The enrichment process must never update, delete, or add documents in `syld_gpt_2067627`.

## 3. Scope

### Included

- Expand already-materialized triple Concepts.
- Retrieve semantically similar `page_content` chunks.
- Use structured LLM output to accept or reject each candidate.
- Store accepted chunks as generated Source Markdown.
- Add Obsidian links between Concepts and accepted Sources.
- Skip unchanged Concept/source pairs on later runs.
- Preview changes without Vault or sidecar writes.
- Preserve existing Concept bodies, citations, entities, relations, and manual edits.

### Excluded

- Inferring missing `product`, `fail_type`, or `cause_oper` values for a source document.
- Creating new triple Concepts from metadata-free documents.
- Re-synthesizing existing Concept bodies.
- Treating vector similarity alone as proof of relevance.
- Mutating the existing `citations` list.
- Changing the existing frontend.
- Adding Neo4j.

New Concept discovery may be designed later as a separate milestone after this enrichment path is proven.

## 4. Chosen approach

Use **Concept-driven related evidence**.

Each existing Concept is the search anchor. Its canonical metadata and generated body form a retrieval query. Candidate chunks are fetched from the content-only index and evaluated against that Concept. Accepted chunks become `related_evidence`, not authoritative `citations`.

This keeps two meanings separate:

- `citations`: evidence actually used by the Concept synthesizer to support body claims.
- `related_evidence`: independently discovered material that may help exploration but does not rewrite the Concept.

### Rejected alternatives

1. **Append candidates directly to `citations`.** This falsely implies the current body cites material it has never used.
2. **Generate new Concepts from source chunks.** The source lacks the required triple metadata and would force semantic inference outside this milestone.

## 5. Components and files

### New production files

#### `08-YieldAgent/enrich_wiki.py`

Cron-ready CLI entry point.

Arguments:

- exactly one of `--check` or `--apply`
- `--vault PATH`, required unless `WIKI_VAULT_PATH` is configured
- `--source-index`, default `syld_gpt_2067627`
- `--limit`, positive integer limiting Concept jobs per run
- optional exact triple selector: `--product`, `--fail-type`, and `--cause-oper` must be supplied together
- `--allow-external-llm`, required for any operation that sends company data to the configured external embedding or chat provider

The CLI prints one bounded JSON result and returns non-zero when any job completes with an error.

#### `08-YieldAgent/wiki_evidence_enrichment.py`

Contains the focused enrichment service:

- Concept enumeration and immutable snapshot creation
- query embedding
- OpenSearch k-NN candidate retrieval
- structured relevance judgment
- content hashing and stable source identity
- incremental pair-state planning
- accepted-evidence attachment
- owned Source Markdown rendering
- final materialization orchestration

### Existing production files changed

#### `08-YieldAgent/wiki_store.py`

Add safe operations that update only the generated `related_evidence` metadata of a Concept and write enrichment-owned Source Markdown. They must use the existing pinned/snapshot-safe mutation conventions, preserve the complete Concept body and unrelated frontmatter, reject source-note ownership collisions, and remain idempotent.

#### `08-YieldAgent/wiki_materializer.py`

Read `related_evidence`, validate that each enrichment-owned Source Markdown file exists with the expected owner, and render a separate `Related Evidence` section in the existing managed Concept block. Existing `Sources` generated from citations remain unchanged.

### New tests

- `08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py`
- `08-YieldAgent/tests/wiki/test_enrich_wiki_cli.py`

Existing materializer and store tests will receive only focused additions where required.

## 6. Data model

### Stable source identity

The OpenSearch `_id` currently contains a local producer path, so it must not become an Obsidian filename or visible canonical identifier.

```text
evidence source id = EVD-<first 20 hex chars of SHA-256(source_index + NUL + source _id)>
```

The full raw `_id` is used only when reading the source index during a run. It is not written to generated Markdown, logs, CLI output, or LLM prompts.

### Concept `related_evidence`

Accepted items are stored as a sorted list with approved fields only:

```yaml
related_evidence:
  - doc_id: EVD-0123456789abcdef0123
    source_index: syld_gpt_2067627
    source_file: example.pptx
    page_num: 3
    content_sha256: <sha256>
    relevance: 0.91
    relation: supporting_context
```

`source_file` is reduced to its basename. Local absolute paths are never persisted.

### Incremental manifest

State is stored in the Vault at:

```text
.yield-wiki/evidence-manifest.json
```

It records, per Concept/source pair:

- Concept snapshot hash
- source content hash
- retrieval model/version
- judgment model/version
- accepted or rejected decision
- confidence and relation for accepted items
- bounded sanitized failure state

The manifest does not contain page content, Concept body text, embeddings, prompt text, or raw OpenSearch IDs.

If either snapshot hash or a model version changes, the pair becomes eligible for re-evaluation. Unchanged rejected pairs are skipped just like accepted pairs.

## 7. Retrieval and judgment

### Query construction

The query uses structured Concept data:

- product
- fail type
- cause operation
- Concept body

The body is bounded before embedding. No keyword lists, regex semantic rules, product-specific branches, or failure-log phrases are introduced.

### Candidate retrieval

- Embed the bounded Concept query using `qwen/qwen3-embedding-8b`.
- Query the `embedding` k-NN field in `syld_gpt_2067627`.
- Fetch a small bounded candidate set per Concept.
- Read only `_id`, `page_content`, `source_file`, `page_num`, and `download_url`.
- Similarity determines candidates only; it never authorizes a Wiki link.

### Structured relevance decision

For each candidate, the configured chat model returns a validated schema:

```text
relevant: boolean
confidence: number from 0 to 1
relation: supporting_context | possible_cause | possible_action | contradiction
reason: bounded text grounded only in the supplied Concept and candidate
```

Only `relevant=true` and confidence meeting the configured fixed operational threshold are attached. The threshold is numeric safety policy, not a natural-language semantic rule.

The model must abstain when the supplied content does not establish a relationship. It must not invent the missing triple metadata.

## 8. Generated Markdown

Accepted evidence produces one generated file:

```text
sources/EVD-0123456789abcdef0123.md
```

The note contains:

- sanitized source basename
- page number when present
- source content copied from OpenSearch
- generated backlinks to every accepted Concept
- `generated_by: yield-wiki-evidence-enricher` ownership metadata so the existing materializer can validate it without taking ownership

The enrichment service writes this note through the existing pinned mutation boundary before attaching it to a Concept. The note itself is the only Vault copy of `page_content`; neither Concept frontmatter nor the incremental manifest duplicates the content. The existing materializer owns the link projection but does not overwrite or delete enrichment-owned Source notes.

The related Concept managed block contains a distinct section:

```markdown
- Related Evidence:
  - [[sources/EVD-0123456789abcdef0123|example.pptx · p.3]]
```

Because both sides contain Wiki links, Obsidian Graph View shows the connection without a plugin-specific graph implementation.

## 9. Command behavior

### Preview

```bash
uv run --frozen python enrich_wiki.py --check --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

`--check` performs read-only discovery and reports Concepts and source documents eligible for evaluation. It does not call the external LLM because that would both incur cost and transmit data. It does not write the Vault or manifest.

### Apply

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

`--apply` performs retrieval and judgment, safely writes accepted Source notes and Concept relationships, writes the manifest atomically, then calls the existing Obsidian materializer once after all successful Concept updates.

The explicit `--allow-external-llm` flag documents that Concept data and bounded candidate page content are sent to the configured external provider. Without the flag, apply fails before any external call or mutation.

### Exact-scope E2E

The initial live E2E is restricted to the previously approved company-data scenario:

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki \
  --product 4SS --fail-type EASY --cause-oper 'PRE METAL CLN'
```

No other company Concept or source content will be sent externally during implementation verification without separate authorization.

## 10. Safety and failure handling

- Validate the external Vault before any write.
- Accept one exact source index name only; reject aliases, wildcards, and comma-separated multi-index expressions.
- Treat the source index as read-only at the client boundary.
- Validate embedding dimension before k-NN search.
- Bound all LLM input, error messages, JSON output, and persisted reasons.
- Never log source page content, Concept body text, embeddings, prompts, tokens, or raw source `_id` values.
- Write the manifest atomically.
- Use pinned file mutation to prevent concurrent or stale Concept overwrites.
- Give enrichment Source notes a distinct generated owner and reject collisions with manual or materializer-owned notes.
- If a Concept changes after planning, do not attach evidence; leave it eligible for the next run.
- If some jobs fail, preserve successful jobs and return `completed_with_errors`.
- Run materialization once only when at least one Concept changed.
- Existing generated files unrelated to this run are not deleted.

## 11. Verification criteria

### Automated

- CLI validation and JSON result tests
- source identity and path-redaction tests
- k-NN request shape and embedding-dimension tests
- structured relevance validation and abstention tests
- unrelated candidate rejection test
- accepted evidence attachment test
- unchanged accepted/rejected pair skip tests
- source content change and Concept change re-evaluation tests
- Concept body, citations, manual edits, entities, and relations preservation tests
- concurrent Concept mutation rejection test
- materializer Concept-to-Source backlink tests
- no-write `--check` test
- external-call opt-in test
- existing Wiki and plugin test suites

### Live end-to-end

1. Back up and hash the live Vault.
2. Run the exact approved `4SS / EASY / PRE METAL CLN` command against the real OpenSearch index and real external embedding/judgment models.
3. Confirm unrelated source documents are rejected rather than linked.
4. If any evidence is accepted, verify generated Source Markdown and bidirectional Obsidian links.
5. Run the same command again and verify unchanged pairs are skipped and no Markdown changes occur.
6. Run Wiki lint/materializer checks and the relevant complete automated suites.
7. Open the live Vault in Obsidian and verify the graph connection when an accepted source exists.

If the real 12-document index contains no relevant document for the approved Concept, a zero-link result is the correct outcome. The implementation must not lower safety criteria or manufacture a relationship merely to make the graph grow.

## 12. Success definition

The milestone is complete when:

- the one-line apply command is available;
- the original source index is unchanged;
- related material can be represented separately from citations;
- irrelevant material does not enter the Wiki;
- reruns are incremental and idempotent;
- existing Concept content and manual edits remain intact;
- actual OpenSearch, external model, Vault materialization, and Obsidian behavior have been verified for the authorized scope.
