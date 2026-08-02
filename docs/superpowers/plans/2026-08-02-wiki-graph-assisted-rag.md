# Wiki Graph-Assisted RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing incremental Markdown Wiki with automatically generated Entity and Relation notes, then use their Source-backed one-hop graph as evidence in Fail History Agent answers.

**Architecture:** The current Concept synthesis call returns typed entities and relations alongside body, confidence, and citations. Concept frontmatter persists that complete result; the existing atomic materializer projects it into Entity and Relation Markdown. A new read-only graph service expands canonical Concepts by one hop and returns Source-backed evidence, which `do_search()` merges with current OpenSearch results before answer generation and structured Citation emission.

**Tech Stack:** Python 3.11+, Pydantic v2, python-frontmatter, FastAPI, OpenSearch, LangChain structured output, pytest, Obsidian Markdown

## Global Constraints

- Work only in `/Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform` on `feat/obsidian-wiki-platform`.
- The live Vault is `/Users/daehwankim/SYLDAIX/YieldWiki`; tests use temporary Vaults.
- The Vault remains the Graph source of truth. Do not add Neo4j, Memgraph, NetworkX persistence, or a second vector store.
- Do not re-embed existing OpenSearch documents or change the `fail-history` index mapping.
- Do not add a second relation-extraction LLM call. Extend the existing Concept synthesis contract.
- Relations are auto-published after structural and Source-reference validation. Do not build relation approval UI or endpoints.
- Only `causes`, `contributes_to`, `resolved_by`, `prevents`, and `associated_with` are valid relation predicates.
- Confidence is recorded but never used as an auto-publication threshold.
- Agent retrieval uses active, Source-backed relations and expands at most one hop.
- Do not infer entities, relations, or citations with natural-language keywords, regexes, phrase lists, or answer-text parsing.
- Existing public `/chat/stream`, Plugin authentication, existing frontends, and existing OpenSearch fallback behavior remain compatible.
- Raw Concept, Entity, Relation, and Source bodies must not enter default local or remote traces.
- Complete with real OpenSearch, real LLM, real Vault, Backend, and Obsidian Desktop verification. Mock success alone is not completion.

---

## File Map

### Create

- `08-YieldAgent/wiki_graph_models.py`: Entity/Relation synthesis contracts, normalized identities, and shared Graph response models.
- `08-YieldAgent/wiki_graph_projection.py`: immutable Vault graph projection, one-hop expansion, exact Source evidence lookup.
- `08-YieldAgent/tests/wiki/test_wiki_graph_models.py`: schema and stable identity tests.
- `08-YieldAgent/tests/wiki/test_wiki_graph_projection.py`: active/stale traversal, bounds, and Source validation tests.
- `08-YieldAgent/docs/wiki-m5-e2e-results.md`: real execution evidence and remaining external blockers.

### Modify

- `08-YieldAgent/wiki_summarizer.py`: add entities/relations to `ConceptSynthesis` and both Concept prompts.
- `08-YieldAgent/wiki_store.py`: persist entities/relations with each synthesized Concept version.
- `08-YieldAgent/wiki_sync.py`: pass structured graph output into Concept storage and recover it without another LLM call.
- `08-YieldAgent/bootstrap_wiki_warmup.py`: pass the same structured graph output during exact bootstrap.
- `08-YieldAgent/wiki_config.py`: add and validate `entities/` and `relations/` managed paths.
- `08-YieldAgent/wiki_materializer.py`: materialize Entity/Relation notes, active/stale state, managed links, counts, and audit-safe errors.
- `08-YieldAgent/materialize_obsidian_wiki.py`: display non-fatal Relation warnings separately from fatal errors.
- `08-YieldAgent/fail_history_tools.py`: seed Graph lookup, merge exact Source evidence, and expose typed graph context.
- `08-YieldAgent/fail_history_agent.py`: provide Graph evidence to synthesis as untrusted evidence and state conflicts explicitly.
- `08-YieldAgent/wiki_plugin_notes.py`: reuse the canonical Source resolver from Graph evidence paths where applicable.
- `08-YieldAgent/wiki_lint.py`: validate generated Entity/Relation frontmatter and Source references.
- Existing focused tests under `08-YieldAgent/tests/wiki/` for summarizer, store, sync, materializer, Fail History, Plugin Citation, and trace boundaries.

---

### Task 1: Typed Entity and Relation synthesis contract

**Files:**
- Create: `08-YieldAgent/wiki_graph_models.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_graph_models.py`
- Modify: `08-YieldAgent/wiki_summarizer.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_summarizer.py`

**Interfaces:**
- Produces: `RelationPredicate`
- Produces: `EntityCandidate(canonical_name, entity_type)`
- Produces: `RelationCandidate(subject, predicate, object, confidence, source_doc_ids)`
- Produces: `normalize_entity_name(value: str) -> str`
- Extends: `ConceptSynthesis.entities`, `ConceptSynthesis.relations`

- [ ] **Step 1: Write failing schema tests**

Add tests that construct the real Pydantic models:

```python
def test_relation_contract_normalizes_names_and_rejects_unknown_predicate():
    from pydantic import ValidationError
    from wiki_graph_models import RelationCandidate

    relation = RelationCandidate(
        subject="  Queue\u3000time 초과  ",
        predicate="causes",
        object="자연 산화",
        confidence=0.82,
        source_doc_ids=["FH-9003-EXTRA", "FH-9003-EXTRA"],
    )
    assert relation.subject == "Queue time 초과"
    assert relation.source_doc_ids == ["FH-9003-EXTRA"]
    with pytest.raises(ValidationError):
        RelationCandidate(
            subject="A", predicate="maybe_causes", object="B",
            confidence=0.5, source_doc_ids=["FH-1"],
        )


def test_concept_synthesis_defaults_graph_fields_for_old_provider_output():
    from wiki_summarizer import ConceptSynthesis

    result = ConceptSynthesis(
        body_markdown="body", confidence=0.8, citations=[]
    )
    assert result.entities == []
    assert result.relations == []
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_graph_models.py tests/wiki/test_wiki_summarizer.py
```

Expected: collection fails because `wiki_graph_models` and the new fields do not exist.

- [ ] **Step 3: Implement the minimal contracts**

Create `wiki_graph_models.py` with a `str, Enum` predicate, NFKC plus whitespace normalization, non-empty validators, confidence bounds, and stable order-preserving `source_doc_ids` de-duplication. Use Pydantic validators; do not use phrase parsing.

```python
class RelationPredicate(str, Enum):
    causes = "causes"
    contributes_to = "contributes_to"
    resolved_by = "resolved_by"
    prevents = "prevents"
    associated_with = "associated_with"


def normalize_entity_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class EntityCandidate(BaseModel):
    canonical_name: str
    entity_type: str


class RelationCandidate(BaseModel):
    subject: str
    predicate: RelationPredicate
    object: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_doc_ids: list[str] = Field(default_factory=list)
```

Extend `ConceptSynthesis` with default-empty typed lists. Update both `_SYNTHESIZE_SYSTEM` and `_SYNTHESIZE_FROM_DOCS_SYSTEM` to require only evidence-present entities and one of the five predicates, with every relation carrying cited `doc_id` values. Do not change the number of model invocations.

- [ ] **Step 4: Verify GREEN and invocation count**

Run the focused tests and assert the fake structured model is invoked once for `synthesize_concept_from_docs()`.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/wiki_graph_models.py 08-YieldAgent/wiki_summarizer.py 08-YieldAgent/tests/wiki/test_wiki_graph_models.py 08-YieldAgent/tests/wiki/test_wiki_summarizer.py
git commit -m "feat: extract typed Wiki relations"
```

---

### Task 2: Persist graph output in Concept state

**Files:**
- Modify: `08-YieldAgent/wiki_store.py`
- Modify: `08-YieldAgent/wiki_sync.py`
- Modify: `08-YieldAgent/bootstrap_wiki_warmup.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_sync_service.py`
- Test: `08-YieldAgent/tests/wiki/test_bootstrap_wiki_sync_metadata.py`

**Interfaces:**
- Extends the existing keyword-only `upsert_concept` arguments with `entities: list[dict] | None = None` and `relations: list[dict] | None = None`.
- Persists: current `entities`, current `relations`, and matching entries in `body_versions`

- [ ] **Step 1: Write failing persistence and recovery tests**

Test a Concept create and update with model-dumped candidates. Assert frontmatter contains the current lists and the latest body version contains the same lists. Add a sync recovery test where an existing Concept has the target `source_fingerprint`; assert `synthesize` is not called and materialization still runs once at the batch boundary.

```python
store.upsert_concept(
    filters=filters,
    synthesized_body="body",
    confidence=0.8,
    citations=[{"doc_id": "FH-1"}],
    entities=[{"canonical_name": "Queue time 초과", "entity_type": "process_condition"}],
    relations=[{
        "subject": "Queue time 초과", "predicate": "causes",
        "object": "자연 산화", "confidence": 0.82,
        "source_doc_ids": ["FH-1"],
    }],
    sync_metadata={"source_fingerprint": "sha256:one"},
    materialize=False,
)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Expected: `upsert_concept()` rejects `entities` and `relations`.

- [ ] **Step 3: Add additive storage fields**

Add keyword-only parameters with `None` defaults. Only replace current graph fields when `synthesized_body` is provided. Store copies in the new body version so a prior version remains auditable. Preserve old Concepts by defaulting missing fields to empty lists.

In both sync and bootstrap, convert Pydantic candidates through `model_dump(mode="json")` and pass them to `upsert_concept`. Do not modify manifest identity or fingerprint calculation.

- [ ] **Step 4: Verify store, sync, and bootstrap tests**

Run:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_store_external_vault.py tests/wiki/test_wiki_sync_service.py tests/wiki/test_bootstrap_wiki_sync_metadata.py
```

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/wiki_store.py 08-YieldAgent/wiki_sync.py 08-YieldAgent/bootstrap_wiki_warmup.py 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py 08-YieldAgent/tests/wiki/test_wiki_sync_service.py 08-YieldAgent/tests/wiki/test_bootstrap_wiki_sync_metadata.py
git commit -m "feat: persist Wiki graph synthesis"
```

---

### Task 3: Materialize Entity and Relation Markdown safely

**Files:**
- Modify: `08-YieldAgent/wiki_config.py`
- Modify: `08-YieldAgent/wiki_materializer.py`
- Modify: `08-YieldAgent/wiki_lint.py`
- Modify: `08-YieldAgent/materialize_obsidian_wiki.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_config.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_lint.py`
- Test: `08-YieldAgent/tests/wiki/test_materialize_obsidian_wiki_cli.py`

**Interfaces:**
- Adds: `WikiPaths.entities`, `WikiPaths.relations`
- Produces generated `type: entity` and `type: relation` notes
- Preserves absent generated relations as `status: stale`
- Extends `MaterializationReport` with non-fatal `warnings`

- [ ] **Step 1: Write failing Vault and materializer tests**

Extend the test Concept fixture with two Entities and one Relation. Assert:

```python
assert paths.entities.is_dir()
assert paths.relations.is_dir()
relation_post = frontmatter.load(next(paths.relations.glob("*.md")))
assert relation_post.metadata["predicate"] == "causes"
assert relation_post.metadata["status"] == "active"
assert relation_post.metadata["source_doc_ids"] == ["FH-000238"]
relation_body = relation_post.content
assert "[[sources/FH-000238|FH-000238]]" in relation_body
assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in relation_body
```

Add tests for:

- relation subject/object missing from the Concept entity list producing a warning while other valid graph notes are written;
- relation Source not in Concept citations producing a warning while the Concept and valid relations remain materialized;
- exact stable output across a second materialization;
- a removed relation becoming `stale`, not deleted;
- a stale relation being absent from active Entity links;
- generated path containment and symlink replacement rejection using the existing Vault validation helpers.

- [ ] **Step 2: Run tests and verify RED**

Expected: `WikiPaths` has no Entity/Relation paths and no notes are emitted.

- [ ] **Step 3: Add managed paths**

Add direct-child `entities` and `relations` paths everywhere `WikiPaths` is constructed, initialized, and validated. Update `WIKI_SCHEMA_TEMPLATE` to describe the new namespaces without overwriting an operator-owned existing `schema.md`.

- [ ] **Step 4: Extend the atomic materialization plan**

Read `entities`, `relations`, and `source_fingerprint` into `_Concept`. Generate stable IDs with SHA-256 over canonical JSON; use hash filenames so Korean names and filename sanitization cannot collide.

```python
def _stable_graph_id(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{kind}:sha256:{hashlib.sha256(encoded).hexdigest()}"
```

Build desired active Entity and Relation targets in the same `_MaterializationPlan` as Sources. A Relation is valid only when its normalized endpoints exist in the same Concept entity list and every `source_doc_id` exists in that Concept's citation-derived Source target map. For previously generated but no-longer-desired Relation/Entity notes, retain their body, change only generated frontmatter to `status: stale`, and keep them outside active backlinks.

Add `warnings` to `_MaterializationPlan` and `MaterializationReport`. Invalid individual Relations are omitted with warnings; warnings do not prevent valid targets from being applied and do not fail a sync job. Vault escape, generated path collision, conflicting Source metadata, and malformed managed blocks remain fatal `errors` and preserve the current all-or-nothing plan behavior. Update `materialize_obsidian_wiki.py` to print warnings separately without returning a failure exit code for warnings alone.

Update index counts and sections for active Entities and Relations. Do not add these directories to deletion pruning.

- [ ] **Step 5: Extend lint**

Scan generated active Relation notes and report `invalid_relation` when endpoint notes or Source notes are missing or the predicate is invalid. Report stale nodes separately without treating expected staleness as a broken active edge.

- [ ] **Step 6: Verify materializer idempotency and fatal-error atomicity**

Run the focused suites. Assert invalid individual Relations produce warnings and do not block valid targets. For every fatal plan error, assert the complete Vault byte snapshot is unchanged.

- [ ] **Step 7: Commit**

```bash
git add 08-YieldAgent/wiki_config.py 08-YieldAgent/wiki_materializer.py 08-YieldAgent/wiki_lint.py 08-YieldAgent/materialize_obsidian_wiki.py 08-YieldAgent/tests/wiki/test_wiki_config.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_lint.py 08-YieldAgent/tests/wiki/test_materialize_obsidian_wiki_cli.py
git commit -m "feat: materialize semantic Wiki graph"
```

---

### Task 4: Build the read-only one-hop Graph projection

**Files:**
- Modify: `08-YieldAgent/wiki_graph_models.py`
- Create: `08-YieldAgent/wiki_graph_projection.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_graph_projection.py`

**Interfaces:**
- Produces: `GraphContext`
- Produces: `build_graph_projection(paths: WikiPaths) -> WikiGraphProjection`
- Produces: `expand_concepts(concept_ids, *, max_relations=10, max_related=3, max_sources=8) -> GraphContext`

- [ ] **Step 1: Write failing projection tests**

Create a temporary Vault with two Concepts sharing an Entity and Source, active and stale Relations, and one invalid Source link. Assert one-hop expansion:

- returns only active Relations;
- includes the seed first;
- includes a shared-Entity related Concept once;
- de-duplicates exact Source `doc_id` values;
- never exceeds each bound;
- ignores relation body text and reads frontmatter only;
- rejects symlinked notes through the canonical resolver;
- returns an empty context, not an exception, when no seed exists.

- [ ] **Step 2: Run and verify RED**

Expected: module import fails.

- [ ] **Step 3: Implement immutable indexed records**

Add `GraphRelation` and `GraphContext` to `wiki_graph_models.py`. Use frozen dataclasses for internal Concept, Entity, Relation, and Source records. Build dictionaries keyed by exact IDs and adjacency maps keyed by `origin_concept_id`, Entity ID, and Source `doc_id`. Do not add NetworkX.

`GraphContext` must contain only structured fields required downstream:

```python
class GraphContext(BaseModel):
    primary_concept_id: str | None = None
    concept_ids: list[str] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
```

Cache the immutable projection by a fingerprint computed from canonical relative path, size, and `mtime_ns` of Concept/Entity/Relation/Source Markdown. A changed file produces a new projection; no watcher or background thread is added.

- [ ] **Step 4: Verify all projection tests**

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/wiki_graph_models.py 08-YieldAgent/wiki_graph_projection.py 08-YieldAgent/tests/wiki/test_wiki_graph_projection.py
git commit -m "feat: traverse the Wiki graph"
```

---

### Task 5: Merge Graph and OpenSearch evidence

**Files:**
- Modify: `08-YieldAgent/fail_history_tools.py`
- Modify: `08-YieldAgent/fail_history_agent.py`
- Modify: `08-YieldAgent/wiki_plugin_notes.py`
- Test: `08-YieldAgent/tests/wiki/test_fail_history_wiki_graph.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py`

**Interfaces:**
- Adds internal retrieval mode: `graph-assisted`
- Adds output field: `graph_context: dict`
- Preserves: `results: list[dict]` and existing public result keys

- [ ] **Step 1: Write failing retrieval tests**

Cover both seed paths:

1. exact triple selects the canonical Concept before OpenSearch;
2. no exact triple maps OpenSearch result metadata through `make_triple_key` to Concepts.

Assert Graph-only Sources are fetched through `_fetch_results_by_doc_ids`, merged by exact `doc_id`, and appended without replacing OpenSearch scores. Assert Graph failure returns the exact prior OpenSearch result shape and mode. Assert no graph function receives raw query text for relationship selection.

- [ ] **Step 2: Write failing answer grounding tests**

Capture the model input in `_synthesize_answer()` and assert it contains a separate block labelled as untrusted Graph evidence with subject, predicate, object, confidence, and Source IDs. Assert conflicting relations are both supplied. Assert a Relation with no resolved Source is absent.

- [ ] **Step 3: Implement seed, expand, and merge helpers**

Add focused private helpers:

```python
def _seed_concept_ids(
    product: str,
    fail_type: str,
    cause_oper: str,
    results: list[dict],
) -> list[str]:
    seeds: list[str] = []
    if product and fail_type and cause_oper:
        seeds.append(
            f"concept:{make_triple_key(product, fail_type, cause_oper).canonical}"
        )
    for result in results:
        triple = make_triple_key(
            str(result.get("product") or ""),
            str(result.get("fail_type") or ""),
            str(result.get("cause_oper") or ""),
        )
        if triple.product and triple.fail_type and triple.cause_oper:
            seeds.append(f"concept:{triple.canonical}")
    return list(dict.fromkeys(seeds))

def _merge_evidence(results: list[dict], graph_results: list[dict]) -> list[dict]:
    merged = {str(item.get("doc_id") or ""): item for item in results if item.get("doc_id")}
    for item in graph_results:
        merged.setdefault(str(item.get("doc_id") or ""), item)
    return list(merged.values())
```

Seed IDs must use the existing canonical triple contract, not text matching. Wrap only projection loading/expansion in the Graph fallback boundary; do not swallow OpenSearch or provider exceptions.

- [ ] **Step 4: Integrate answer synthesis and structured citations**

Pass `graph_context` as data in the human evidence message with an explicit untrusted-evidence label. Keep Source documents in `fail_history_results`, allowing the existing SSE Citation builder to resolve canonical Source paths. Do not parse `[FH-*]` text to create new Plugin citations.

Make `wiki_plugin_notes.read_source()` the single canonical Source resolver used by Graph-to-Plugin navigation, eliminating the remaining duplicate filename-only Source path logic where touched.

- [ ] **Step 5: Verify Fail History and Plugin regressions**

Run:

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_fail_history_wiki_graph.py tests/wiki/test_wiki_plugin_chat.py tests/wiki/test_wiki_plugin_search.py
```

- [ ] **Step 6: Commit**

```bash
git add 08-YieldAgent/fail_history_tools.py 08-YieldAgent/fail_history_agent.py 08-YieldAgent/wiki_plugin_notes.py 08-YieldAgent/tests/wiki/test_fail_history_wiki_graph.py 08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py 08-YieldAgent/tests/wiki/test_wiki_plugin_search.py
git commit -m "feat: ground RAG in the Wiki graph"
```

---

### Task 6: Harden trace and recovery boundaries

**Files:**
- Modify: `08-YieldAgent/node_planner.py` only if the new graph envelope reaches Planner diagnostics
- Modify: `08-YieldAgent/agent_server.py` only for additive safe graph metadata in SSE/session storage
- Test: `08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py`
- Test: `08-YieldAgent/tests/wiki/test_wiki_sync_service.py`

**Interfaces:**
- Default traces retain IDs, predicates, counts, hashes, and Source IDs, never raw bodies.
- Sync resume repairs projection without re-synthesis.

- [ ] **Step 1: Add raw-sentinel leakage tests**

Place unique sentinels in Concept, Entity, Relation, and Source bodies. Run the real planner/stream diagnostic callbacks with fake providers and inspect persisted trace JSON. Assert no sentinel appears; IDs, relation counts, and SHA-256 metadata remain observable.

- [ ] **Step 2: Add crash/resume integration test**

Force materialization to fail after Concept persistence, then resume with a synthesizer that raises if called. Assert the job succeeds after materialization repair and the synthesizer call count remains zero.

- [ ] **Step 3: Implement the minimum redaction or recovery change**

Reuse the existing Wiki-context trace redaction helpers. Do not create a second trace framework. Store graph relation summaries only if they contain no raw body or evidence excerpt.

- [ ] **Step 4: Run focused and full Wiki suites**

```bash
cd 08-YieldAgent
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --with pytest pytest -q -p no:cacheprovider tests/wiki
```

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/node_planner.py 08-YieldAgent/agent_server.py 08-YieldAgent/tests/wiki/test_wiki_plugin_chat.py 08-YieldAgent/tests/wiki/test_wiki_sync_service.py
git commit -m "fix: protect Wiki graph evidence boundaries"
```

Omit unchanged production files from the commit.

---

### Task 7: Real M5 end-to-end verification

**Files:**
- Create: `08-YieldAgent/docs/wiki-m5-e2e-results.md`
- Modify generated live Vault files only through production commands; do not commit the Vault.

**Interfaces:**
- Produces factual PASS/FAIL/BLOCKED evidence for real services.

- [ ] **Step 1: Verify clean dependencies and services**

Run `uv lock --check`, OpenSearch cluster health, MongoDB connectivity used by sync jobs, configured Vault validation, and Backend Plugin health. Record exact HTTP statuses and index/Vault counts without recording secrets.

- [ ] **Step 2: Re-synthesize one exact live Concept**

With an approved real LLM destination configured outside Git:

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
uv run --frozen python bootstrap_wiki_warmup.py \
  --apply \
  --product 4SS \
  --fail-type EASY \
  --cause-oper 'PRE METAL CLN'
```

Record the provider/model, exit status, generated Entity/Relation counts, and Concept fingerprint. Do not record tokens or raw confidential prompts.

- [ ] **Step 3: Inspect the real Markdown projection**

Parse generated frontmatter and verify every active Relation endpoint and Source. Run `materialize_obsidian_wiki.py --check` twice and confirm the second run reports zero changes.

- [ ] **Step 4: Verify real Agent retrieval**

Start the Backend with local Plugin authentication and send a non-sensitive relation question through the authenticated Plugin Chat route. Confirm the real response uses an active Relation, emits a real Source citation, and the persisted session contains the structured citation.

- [ ] **Step 5: Verify Obsidian Desktop**

Open the real Vault, confirm Entity and Relation nodes are visible, open one Relation and its Source, ask the same question, and click the Citation. Reload Obsidian and confirm Plugin state recovers.

- [ ] **Step 6: Verify incremental stale and fallback behavior**

Use a temporary E2E Vault cloned from the live test subset. Change its controlled source set, run sync, confirm an obsolete Relation becomes stale, then re-run unchanged and confirm zero synthesis calls. Simulate Graph projection failure and confirm OpenSearch-only retrieval still answers.

- [ ] **Step 7: Record honest results and commit**

If the LLM is unavailable or quota-exhausted, mark real LLM/Chat/Citation as BLOCKED and do not claim M5 complete.

```bash
git add 08-YieldAgent/docs/wiki-m5-e2e-results.md
git commit -m "docs: verify Wiki graph RAG end to end"
```

---

### Task 8: Full regression, independent review, and handoff

**Files:**
- Modify only files required by Critical/Important review findings.

- [ ] **Step 1: Run all required regression gates**

```bash
cd 08-YieldAgent
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --with pytest pytest -q -p no:cacheprovider tests/wiki tests/test_user_memory.py
OPENROUTER_API_KEY=test-key OPENROUTER_BASE_URL=http://test uv run --frozen --with pytest python - <<'PY'
import langchain
langchain.verbose = False
langchain.debug = False
langchain.llm_cache = None
import pytest
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_confirm_edit.py']))
PY
cd ../obsidian/plugin
npm test
npm run build
```

- [ ] **Step 2: Run repository safety checks**

```bash
cd ../../
uv lock --check
git diff --check
git status --short
```

Scan tracked files for real API tokens without printing secret values. Confirm no existing frontend source changed and the temporary Backend is stopped.

- [ ] **Step 3: Request an independent whole-range review**

Review from commit `2e3b4bd` through `HEAD`, focusing on Source-grounding, path safety, stale behavior, trace leakage, OpenSearch fallback, backward compatibility, and the no-hardcoded-semantic-rules constraint.

- [ ] **Step 4: Fix and re-review blocking findings**

Use a failing regression test for every Critical or Important defect. Repeat review until no blocking findings remain.

- [ ] **Step 5: Final handoff**

Report implemented behavior, exact test counts, real E2E outcome, external blockers, commit range, and the remaining non-blocking backlog. Keep the feature branch/worktree until the user chooses integration.
