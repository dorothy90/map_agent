# Content-only Wiki Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one command that safely links semantically relevant documents from `syld_gpt_2067627` to existing Obsidian Wiki Concepts without changing Concept prose or authoritative citations.

**Architecture:** A focused enrichment service reads immutable Concept snapshots, retrieves content-only candidates with the producer-compatible embedding model, and accepts links only through validated structured LLM decisions. Accepted evidence is persisted as enrichment-owned Source Markdown plus Concept `related_evidence` metadata; the existing materializer projects the links, while a Vault-local manifest makes reruns incremental.

**Tech Stack:** Python 3.12, OpenSearch `knn_vector`, Pydantic v2, LangChain structured output, python-frontmatter, existing `PinnedWikiMutation`, pytest, Obsidian Markdown.

## Global Constraints

- Source index defaults to the exact name `syld_gpt_2067627`; reject aliases, wildcards, and comma-separated expressions.
- Read `page_content`, `embedding`, `source_file`, `page_num`, and `download_url`; never mutate the source index.
- Use `qwen/qwen3-embedding-8b` with exactly 4096 dimensions.
- Do not infer missing `product`, `fail_type`, or `cause_oper` metadata.
- Do not modify Concept body text, `citations`, `entities`, `relations`, or manual edits.
- Store accepted links under `related_evidence`, never under `citations`.
- Do not log or persist raw OpenSearch `_id`, prompts, embeddings, Concept bodies, or page content in the manifest.
- Persist page content only in enrichment-owned Source Markdown.
- Require `--allow-external-llm` before any external embedding or chat request.
- `--check` is read-only and performs no external model call.
- Live external E2E is limited to `4SS / EASY / PRE METAL CLN` unless the user separately authorizes more company data.
- Do not add keyword, regex, phrase-list, or product-specific semantic matching rules.

---

## File structure

### Create

- `08-YieldAgent/wiki_evidence_enrichment.py`: immutable models, manifest, retrieval, structured judgment, and orchestration.
- `08-YieldAgent/enrich_wiki.py`: argument parsing, dependency wiring, JSON result, and exit status.
- `08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py`: core, retrieval, manifest, and orchestration tests.
- `08-YieldAgent/tests/wiki/test_enrich_wiki_cli.py`: CLI contract and external opt-in tests.

### Modify

- `08-YieldAgent/wiki_store.py`: pinned Concept metadata and enrichment-owned Source note writes.
- `08-YieldAgent/wiki_materializer.py`: validate and project `related_evidence` links.
- `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`: persistence safety and ownership tests.
- `08-YieldAgent/tests/wiki/test_wiki_materializer.py`: Concept-to-related-Source projection tests.
- `08-YieldAgent/docs/wiki-deployment-procedure.md`: operator commands and cron guidance.
- `08-YieldAgent/docs/wiki-m5-e2e-results.md`: append actual M7 verification evidence without rewriting prior results.

---

### Task 1: Immutable snapshots, identities, and incremental manifest

**Files:**
- Create: `08-YieldAgent/wiki_evidence_enrichment.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py`

**Interfaces:**
- Produces: `EvidenceSelector`, `ConceptEvidenceSnapshot`, `EvidenceCandidate`, `EvidenceDecision`, `EvidencePairState`, `EnrichmentRunResult`.
- Produces: `stable_evidence_id(source_index: str, raw_id: str) -> str`.
- Produces: `read_concept_snapshots(paths: WikiPaths, selector: EvidenceSelector | None = None) -> tuple[ConceptEvidenceSnapshot, ...]`.
- Produces: `EvidenceManifestStore(path: Path).load() -> dict[str, Any]` and `.save(manifest: Mapping[str, Any]) -> None`.

- [ ] **Step 1: Write failing identity, snapshot, and manifest tests**

Add tests that create a minimal external Vault Concept and assert:

```python
def test_stable_evidence_id_hides_raw_path():
    raw_id = "/private/uploads/company-deck.pptx_p1_0"
    first = stable_evidence_id("syld_gpt_2067627", raw_id)
    assert first.startswith("EVD-")
    assert len(first) == 24
    assert first == stable_evidence_id("syld_gpt_2067627", raw_id)
    assert "company" not in first


def test_read_concept_snapshots_filters_exact_triple(paths):
    write_concept(
        paths.concepts / "4SS_PRE_METAL_CLN_EASY.md",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        body="Generated body",
    )
    selected = read_concept_snapshots(
        paths,
        EvidenceSelector("4SS", "EASY", "PRE METAL CLN"),
    )
    assert [(item.product, item.fail_type, item.cause_oper) for item in selected] == [
        ("4SS", "EASY", "PRE METAL CLN")
    ]
    assert selected[0].file_sha256
    assert selected[0].semantic_sha256


def test_manifest_round_trip_excludes_sensitive_payloads(paths):
    store = EvidenceManifestStore(paths.state_dir / "evidence-manifest.json")
    manifest = {
        "version": 1,
        "pairs": {
            "concept:4SS|PRE METAL CLN|EASY\u0000EVD-abc": {
                "concept_sha256": "a" * 64,
                "content_sha256": "b" * 64,
                "accepted": False,
                "confidence": 0.1,
                "relation": "supporting_context",
                "retrieval_model": "qwen/qwen3-embedding-8b",
                "judgment_model": "test-model",
            }
        },
    }
    store.save(manifest)
    raw = store.path.read_text(encoding="utf-8")
    assert "page_content" not in raw
    assert "/private/" not in raw
    assert store.load() == manifest
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
cd 08-YieldAgent
uv run --frozen pytest -q tests/wiki/test_wiki_evidence_enrichment.py
```

Expected: collection fails because `wiki_evidence_enrichment` does not exist.

- [ ] **Step 3: Implement the minimal models and manifest boundary**

Implement frozen dataclasses for snapshots/candidates/results, a Pydantic decision schema, exact selector matching, SHA-256 helpers, and a versioned JSON manifest. Snapshot hashes must cover the complete Concept file bytes so any concurrent/manual change invalidates the plan. `EvidenceManifestStore.save()` must create the state directory and use `PinnedWikiMutation.replace_text()` with a fresh snapshot rather than `Path.write_text()`.

The public model shapes must be:

```python
@dataclass(frozen=True)
class EvidenceSelector:
    product: str
    fail_type: str
    cause_oper: str


@dataclass(frozen=True)
class ConceptEvidenceSnapshot:
    path: Path
    concept_id: str
    product: str
    fail_type: str
    cause_oper: str
    body: str
    file_sha256: str
    semantic_sha256: str


@dataclass(frozen=True)
class EvidenceCandidate:
    raw_id: str = field(repr=False)
    doc_id: str
    page_content: str = field(repr=False)
    content_sha256: str
    source_file: str
    page_num: int | None
    download_url: str
    score: float


@dataclass(frozen=True)
class EvidencePairState:
    concept_sha256: str
    content_sha256: str
    retrieval_model: str
    judgment_model: str
    accepted: bool
    confidence: float
    relation: str


class EvidenceDecision(BaseModel):
    relevant: bool
    confidence: float = Field(ge=0.0, le=1.0)
    relation: Literal[
        "supporting_context",
        "possible_cause",
        "possible_action",
        "contradiction",
    ]
    reason: str = Field(max_length=500)
```

`file_sha256` covers the complete Concept bytes and is used only for stale-write rejection. `semantic_sha256` covers the exact retrieval input: canonical triple plus Concept body after removing the materializer-owned Knowledge Links block. Store only `semantic_sha256` as `concept_sha256` in the manifest so the enrichment's own materialization does not force a second judgment. Reject malformed manifest versions and any manifest value containing unapproved pair-state keys. Sanitize `source_file` with `Path(value).name`; do not serialize `raw_id`, `body`, or `page_content`.

- [ ] **Step 4: Run focused tests**

Run the Task 1 test module and expect all Task 1 tests to pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add 08-YieldAgent/wiki_evidence_enrichment.py 08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py
git commit -m "feat(wiki): add evidence enrichment state"
```

---

### Task 2: Read-only vector retrieval and grounded structured judgment

**Files:**
- Modify: `08-YieldAgent/wiki_evidence_enrichment.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py`

**Interfaces:**
- Consumes: Task 1 snapshot and candidate models.
- Produces: `OpenSearchEvidenceRetriever(client: Any, source_index: str, embed: Callable[[str], list[float]], top_k: int = 5)`.
- Produces: `OpenSearchEvidenceRetriever.validate() -> int` and `.search(concept: ConceptEvidenceSnapshot) -> tuple[EvidenceCandidate, ...]`.
- Produces: `StructuredEvidenceJudge(llm: Any, model_name: str, minimum_confidence: float = 0.8).decide(concept, candidate) -> EvidenceDecision`.

- [ ] **Step 1: Write failing retrieval and judgment tests**

Use fakes that capture requests without source content in their repr/log. Assert:

```python
def test_retriever_validates_exact_index_and_vector_dimension():
    client = FakeOpenSearch(dimension=4096)
    retriever = OpenSearchEvidenceRetriever(client, "syld_gpt_2067627", lambda _: [0.0] * 4096)
    assert retriever.validate() == 4096
    with pytest.raises(ValueError, match="exact source index"):
        OpenSearchEvidenceRetriever(client, "syld_*", lambda _: [0.0] * 4096)


def test_retriever_uses_knn_and_redacts_raw_id():
    client = FakeOpenSearch.with_hit(
        raw_id="/private/company.pptx_p1_0",
        source={"page_content": "unrelated text", "source_file": "/private/company.pptx", "page_num": 1},
    )
    retriever = OpenSearchEvidenceRetriever(client, "syld_gpt_2067627", lambda _: [0.0] * 4096)
    candidate = retriever.search(concept_snapshot())[0]
    assert client.last_search_body["query"]["knn"]["embedding"]["vector"] == [0.0] * 4096
    assert candidate.source_file == "company.pptx"
    assert "/private/" not in repr(candidate)


def test_judge_rejects_unrelated_candidate_even_with_high_vector_score():
    judge = StructuredEvidenceJudge(
        FakeStructuredLLM(EvidenceDecision(
            relevant=False,
            confidence=0.99,
            relation="supporting_context",
            reason="The candidate is about an unrelated programming project.",
        )),
        "test-model",
    )
    decision = judge.decide(concept_snapshot(), candidate(score=0.99))
    assert decision.relevant is False
```

Also assert that a query vector of any length other than 4096 fails before search, and that `_source` is restricted to the five approved fields.

- [ ] **Step 2: Run the new tests and confirm the expected failures**

Run the Task 2 test names with `pytest -q`; expect missing retriever/judge symbols.

- [ ] **Step 3: Implement retrieval and judgment**

`validate()` must call `indices.get_mapping(index=source_index)` and require `embedding.type == "knn_vector"` and `dimension == 4096`. `search()` must use this request shape:

```python
body = {
    "size": self.top_k,
    "_source": ["page_content", "source_file", "page_num", "download_url"],
    "query": {
        "knn": {
            "embedding": {
                "vector": vector,
                "k": self.top_k,
            }
        }
    },
}
```

Build the embedding query from the exact structured triple plus a bounded Concept body. The structured judge must call `llm.with_structured_output(EvidenceDecision, method="function_calling")` and supply only the bounded Concept context and bounded candidate content. The system message must explicitly require abstention and prohibit inventing missing triple metadata. A decision is attachable only when `relevant` is true and `confidence >= minimum_confidence`; do not alter the model's semantic result with keyword rules.

- [ ] **Step 4: Run Task 1 and Task 2 tests**

Expect all tests in `test_wiki_evidence_enrichment.py` to pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add 08-YieldAgent/wiki_evidence_enrichment.py 08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py
git commit -m "feat(wiki): retrieve and judge related evidence"
```

---

### Task 3: Pinned Vault persistence for related evidence and Source notes

**Files:**
- Modify: `08-YieldAgent/wiki_store.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`

**Interfaces:**
- Produces: `replace_related_evidence(filters: dict[str, str], source_index: str, items: list[dict[str, Any]], expected_content_sha256: str) -> bool`.
- Produces: `upsert_related_evidence_source(item: Mapping[str, Any], page_content: str) -> bool`.
- Produces: `refresh_related_evidence_backlinks(doc_id: str) -> bool`.
- Source owner is exactly `yield-wiki-evidence-enricher`.

- [ ] **Step 1: Write failing persistence tests**

Tests must cover:

```python
def test_replace_related_evidence_preserves_concept_body_and_citations(store):
    path = store.create_concept(body="manual-safe body", citations=[{"doc_id": "FH-1"}])
    before = path.read_bytes()
    expected = hashlib.sha256(before).hexdigest()
    changed = wiki_store.replace_related_evidence(
        {"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN"},
        "syld_gpt_2067627",
        [{"doc_id": "EVD-abc", "source_index": "syld_gpt_2067627", "source_file": "a.pptx", "page_num": 1, "content_sha256": "b" * 64, "relevance": 0.9, "relation": "supporting_context"}],
        expected,
    )
    post = frontmatter.load(path)
    assert changed is True
    assert post.content == "manual-safe body"
    assert post["citations"] == [{"doc_id": "FH-1"}]
    assert post["related_evidence"][0]["doc_id"] == "EVD-abc"


def test_replace_related_evidence_rejects_changed_snapshot(store):
    path = store.create_concept(body="before")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text() + "operator edit", encoding="utf-8")
    with pytest.raises(ConceptEditConflict):
        wiki_store.replace_related_evidence(filters(), "syld_gpt_2067627", [], expected)


def test_source_writer_rejects_manual_or_other_generated_owner(store):
    path = store.paths.sources / "EVD-abc.md"
    path.write_text("# manual", encoding="utf-8")
    with pytest.raises(WikiConfigurationError, match="ownership collision"):
        wiki_store.upsert_related_evidence_source(evidence_item(), "source body")
```

Also test idempotency, basename-only source metadata, no raw `_id`, and backlink refresh across two Concepts.

- [ ] **Step 2: Run focused store tests and confirm failure**

Run:

```bash
uv run --frozen pytest -q tests/wiki/test_wiki_store_external_vault.py
```

Expected: new public functions are missing.

- [ ] **Step 3: Implement minimal safe persistence**

Use `_read_with_snapshot()` and `_write()` for every Concept mutation. Compare `hashlib.sha256(snapshot.content).hexdigest()` to `expected_content_sha256` before changing metadata. The service passes the Task 1 `file_sha256`, not the manifest `semantic_sha256`. Preserve evidence from other source indexes and replace only entries whose `source_index` equals the requested exact index. Sort by `doc_id`; return `False` without writing when unchanged.

Source Markdown must use this ownership contract:

```yaml
id: source:EVD-...
type: source
generated_by: yield-wiki-evidence-enricher
doc_id: EVD-...
source_index: syld_gpt_2067627
source_file: basename.pptx
page_num: 1
content_sha256: <sha256>
```

The body contains `# <source label>`, `## Source Content`, the exact OpenSearch `page_content`, and a managed `## Related Concepts` backlink block. Reject any existing path whose `(generated_by, type, id)` does not exactly match. Backlink refresh scans Concept frontmatter for matching `related_evidence` IDs and rewrites only its managed block through pinned mutation.

- [ ] **Step 4: Run store and safe-mutation regression tests**

Run:

```bash
uv run --frozen pytest -q \
  tests/wiki/test_wiki_store_external_vault.py \
  tests/wiki/test_wiki_materializer.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add 08-YieldAgent/wiki_store.py 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py
git commit -m "feat(wiki): persist related evidence safely"
```

---

### Task 4: Obsidian materializer projection

**Files:**
- Modify: `08-YieldAgent/wiki_materializer.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`

**Interfaces:**
- Consumes: Concept `related_evidence` and enrichment-owned `sources/EVD-*.md` from Task 3.
- Produces: deterministic `Related Evidence` links in the existing managed Concept block.
- Preserves: existing citation `Sources` behavior and ownership/deletion rules.

- [ ] **Step 1: Write failing materializer tests**

Create an enrichment-owned source note and a Concept with one `related_evidence` item. Assert:

```python
def test_materializer_links_related_evidence_without_claiming_source_ownership(paths):
    source = write_enrichment_source(paths.sources / "EVD-abc.md", doc_id="EVD-abc")
    concept = write_concept(paths, related_evidence=[{
        "doc_id": "EVD-abc",
        "source_index": "syld_gpt_2067627",
        "source_file": "deck.pptx",
        "page_num": 3,
        "content_sha256": "a" * 64,
        "relevance": 0.91,
        "relation": "supporting_context",
    }])
    report = materialize_wiki(paths, apply=True)
    assert report.errors == ()
    body = concept.read_text(encoding="utf-8")
    assert "Related Evidence:" in body
    assert "[[sources/EVD-abc|deck.pptx · p.3]]" in body
    assert frontmatter.load(source)["generated_by"] == "yield-wiki-evidence-enricher"
```

Add tests that reject a missing Source note, a manual note at the expected path, a wrong evidence owner, duplicate/conflicting metadata, and unsafe/noncanonical `doc_id` values. Confirm citation `Sources` and related evidence render as separate sections.

- [ ] **Step 2: Run the new materializer tests and confirm failure**

Run the selected tests and expect no `Related Evidence` projection yet.

- [ ] **Step 3: Extend `_Concept` and `_build_plan()` minimally**

Add `related_evidence: tuple[dict[str, Any], ...]` to `_Concept`. Validate each item against the approved schema while reading Concepts. Resolve its path with the existing stable filename rule, require an existing regular file, parse frontmatter, and require:

```python
(
    metadata.get("generated_by"),
    metadata.get("type"),
    metadata.get("id"),
) == (
    "yield-wiki-evidence-enricher",
    "source",
    f"source:{doc_id}",
)
```

Add a `Related Evidence` list to the Concept managed block after citation `Sources`. Do not add enrichment-owned notes to materializer targets or deletion owners. Add their links to the generated index `Sources` section only if doing so does not make the materializer claim ownership; otherwise leave discovery through Concept links and Obsidian backlinks.

- [ ] **Step 4: Run materializer regression tests**

Run:

```bash
uv run --frozen pytest -q \
  tests/wiki/test_wiki_materializer.py \
  tests/wiki/test_materialize_obsidian_wiki_cli.py \
  tests/wiki/test_wiki_graph_projection.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py
git commit -m "feat(wiki): project related evidence links"
```

---

### Task 5: Enrichment orchestration and one-line CLI

**Files:**
- Modify: `08-YieldAgent/wiki_evidence_enrichment.py`
- Create: `08-YieldAgent/enrich_wiki.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py`
- Create: `08-YieldAgent/tests/wiki/test_enrich_wiki_cli.py`

**Interfaces:**
- Produces: `WikiEvidenceEnrichmentService.check(selector: EvidenceSelector | None) -> EnrichmentRunResult`.
- Produces: `WikiEvidenceEnrichmentService.apply(limit: int, selector: EvidenceSelector | None) -> EnrichmentRunResult`.
- CLI: `python enrich_wiki.py --check|--apply [--allow-external-llm] --vault PATH [--source-index INDEX] [--limit N] [exact selector]`.

- [ ] **Step 1: Write failing service tests**

Use injected fake retriever, judge, manifest store, Vault callbacks, and materializer. Cover:

```python
def test_apply_rejects_irrelevant_candidate_without_vault_attachment(deps):
    deps.judge.decision = EvidenceDecision(
        relevant=False,
        confidence=0.99,
        relation="supporting_context",
        reason="unrelated",
    )
    result = deps.service.apply(limit=10, selector=None)
    assert result.rejected == 1
    assert result.attached == 0
    assert deps.replace_calls == []
    assert deps.materialize_calls == 0


def test_apply_attaches_accepted_candidate_and_materializes_once(deps):
    deps.judge.decision = EvidenceDecision(
        relevant=True,
        confidence=0.91,
        relation="supporting_context",
        reason="grounded",
    )
    result = deps.service.apply(limit=10, selector=None)
    assert result.attached == 1
    assert len(deps.source_write_calls) == 1
    assert len(deps.replace_calls) == 1
    assert deps.materialize_calls == 1


def test_second_apply_skips_unchanged_judgment_pair(deps):
    deps.service.apply(limit=10, selector=None)
    deps.reset_call_counts_but_keep_manifest()
    result = deps.service.apply(limit=10, selector=None)
    assert result.skipped == 1
    assert deps.judge.calls == []
    assert deps.replace_calls == []
```

Also test changed source hash, changed Concept snapshot, low-confidence relevant decision, partial failure, sanitized error output, and a stale Concept snapshot rejected before persistence.

- [ ] **Step 2: Write failing CLI tests**

Mirror the established `sync_wiki.py` pattern. Assert parser rejection for missing mode, both modes, nonpositive limit, partial selector, wildcard index, apply without `--allow-external-llm`, and missing Vault configuration. Assert `--check` builds only read-only dependencies and does not require the external flag.

```python
@pytest.mark.parametrize("argv", [
    [],
    ["--check", "--apply"],
    ["--apply"],
    ["--apply", "--allow-external-llm", "--limit", "0"],
    ["--check", "--product", "4SS"],
    ["--check", "--source-index", "syld_*"],
])
def test_parser_rejects_unsafe_arguments(argv):
    with pytest.raises(SystemExit):
        enrich_wiki._parse_args(argv)
```

- [ ] **Step 3: Run service and CLI tests to verify failure**

Run both enrichment test modules. Expected: missing service/CLI symbols.

- [ ] **Step 4: Implement minimal orchestration**

For each bounded Concept job:

1. retrieve candidates;
2. compute pair key `concept_id + NUL + doc_id`;
3. reuse a manifest decision only when Concept semantic hash, source hash, retrieval model, and judgment model all match;
4. judge otherwise;
5. write sanitized pair state;
6. derive the complete accepted set for that Concept/source index from current manifest state;
7. write accepted Source notes;
8. replace only that source index's `related_evidence` set with the planned Concept snapshot hash;
9. refresh backlinks for affected Source IDs;
10. save manifest atomically;
11. materialize once after the batch if any Concept changed.

Do not persist the LLM `reason`. Keep it only in memory long enough to validate the decision. `EnrichmentRunResult` must expose bounded counters and sanitized errors only:

```python
@dataclass(frozen=True)
class EnrichmentRunResult:
    status: str
    concepts: int = 0
    candidates: int = 0
    evaluated: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    attached: int = 0
    materialized: bool = False
    errors: tuple[str, ...] = ()
```

- [ ] **Step 5: Implement CLI dependency wiring**

`enrich_wiki.py` must call `load_dotenv(override=False)`, apply `--vault` to `WIKI_VAULT_PATH` before importing `wiki_store`, validate the external Vault, construct the existing OpenSearch client, reuse `_get_embedding`, construct `get_llm()` with `WIKI_EVIDENCE_MODEL` falling back to `WIKI_SUMMARIZE_MODEL` and then `RETRIEVE_CHAIN_MODEL`, and print `json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)`.

`--check` must only validate the Vault and source-index mapping/count. It must not construct the embedder or LLM.

- [ ] **Step 6: Run focused and full Wiki automated tests**

Run:

```bash
uv run --frozen pytest -q \
  tests/wiki/test_wiki_evidence_enrichment.py \
  tests/wiki/test_enrich_wiki_cli.py \
  tests/wiki/test_wiki_store_external_vault.py \
  tests/wiki/test_wiki_materializer.py

uv run --frozen pytest -q tests/wiki
```

Expected: all pass with only already-documented warnings.

- [ ] **Step 7: Commit Task 5**

```bash
git add \
  08-YieldAgent/wiki_evidence_enrichment.py \
  08-YieldAgent/enrich_wiki.py \
  08-YieldAgent/tests/wiki/test_wiki_evidence_enrichment.py \
  08-YieldAgent/tests/wiki/test_enrich_wiki_cli.py
git commit -m "feat(wiki): add one-command evidence enrichment"
```

---

### Task 6: Operator documentation and real authorized E2E

**Files:**
- Modify: `08-YieldAgent/docs/wiki-deployment-procedure.md`
- Modify: `08-YieldAgent/docs/wiki-m5-e2e-results.md`

**Interfaces:**
- Consumes: completed CLI from Task 5.
- Produces: exact operator commands, privacy warning, cron guidance, backup path, live counts, and idempotency evidence.

- [ ] **Step 1: Add the operator commands**

Document the read-only preview:

```bash
cd /Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform/08-YieldAgent
uv run --frozen python enrich_wiki.py --check \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

Document the one-line apply command:

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

State plainly that `--allow-external-llm` sends bounded Concept context and candidate page content to the configured provider. Include the exact-selector command for controlled runs and a cron example that uses an absolute working directory and captures only the bounded JSON result.

- [ ] **Step 2: Run read-only live checks**

Before external calls or Vault writes:

1. record `syld_gpt_2067627` count and mapping hash;
2. back up `/Users/daehwankim/SYLDAIX/YieldWiki` to a new timestamped sibling directory;
3. record a deterministic live Vault manifest hash;
4. run `enrich_wiki.py --check` and confirm no Vault hash change;
5. confirm no external model call occurred.

- [ ] **Step 3: Run the exact authorized live apply scenario**

Use the repository `.env` through the established dotenv wrapper and set a currently working free model only for this run. Execute exactly:

```bash
uv run --with python-dotenv dotenv \
  -f /Users/daehwankim/yield-agent/.env run -- \
  env PYTHON_DOTENV_DISABLED=1 \
  WIKI_EVIDENCE_MODEL=nvidia/nemotron-3-super-120b-a12b:free \
  uv run --frozen python enrich_wiki.py --apply --allow-external-llm \
    --vault /Users/daehwankim/SYLDAIX/YieldWiki \
    --product 4SS --fail-type EASY --cause-oper 'PRE METAL CLN'
```

Do not broaden this external run. Record accepted/rejected/skipped counts and errors. Verify the source index count and mapping hash remain identical.

- [ ] **Step 4: Verify idempotency and Obsidian projection**

Run the same exact-selector apply a second time. Confirm unchanged pairs do not call the judgment model, no Concept or Source Markdown bytes change, and the materializer reports no unintended mutation.

If evidence was accepted, open the live Vault in Obsidian and verify the Concept-to-Source graph edge plus the Source note body. If none was accepted, record that the real index contained no grounded match and do not lower the threshold.

- [ ] **Step 5: Run final gates**

Run:

```bash
uv run --frozen pytest -q tests/wiki tests/test_user_memory.py
uv run --frozen python wiki_lint.py --vault /Users/daehwankim/SYLDAIX/YieldWiki
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
  uv run --frozen python materialize_obsidian_wiki.py --check
cd ../obsidian/plugin
npm test
npm run build
```

Also run `git diff --check`, scan tracked changes for secrets and raw producer paths, and confirm only task files changed.

- [ ] **Step 6: Record evidence and commit documentation**

Append actual commands, timestamps, result counts, hashes, backup path, and Obsidian outcome to `wiki-m5-e2e-results.md`. Do not write tokens, prompts, source page text, raw `_id`, or absolute producer upload paths.

```bash
git add 08-YieldAgent/docs/wiki-deployment-procedure.md 08-YieldAgent/docs/wiki-m5-e2e-results.md
git commit -m "docs(wiki): verify content evidence enrichment"
```

---

## Final review gate

- [ ] Review every changed line against the approved design and AGENTS.md hardcoding ban.
- [ ] Confirm source-index access is read-only by code inspection and real pre/post count/mapping hashes.
- [ ] Confirm `citations`, Concept prose, entities, relations, and manual edits are byte-preserved except the generated managed link block and `related_evidence` frontmatter.
- [ ] Confirm external calls cannot occur without `--allow-external-llm`.
- [ ] Confirm no company data outside the exact authorized triple was sent during E2E.
- [ ] Confirm the live Vault is recoverable from the recorded backup.
- [ ] Do not merge to `main`; leave the completed work on `feat/obsidian-wiki-platform` for user review.
