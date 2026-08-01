# Obsidian Schema Materializer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Wiki Concept metadata와 citation을 Product → Product Fail → Operation → Concept → Source Markdown graph로 materialize하여 Obsidian 기본 Graph View에서 실제 연결선을 표시한다.

**Architecture:** 새 `wiki_materializer.py`는 `WikiPaths`가 가리키는 Vault만 스캔해 목표 Markdown 집합을 결정론적으로 계산하고, check/apply 모드로 원자적 파일 교체를 수행한다. `wiki_store.py`는 Concept/Super Concept 저장 후 materializer를 호출하며 bootstrap은 batch 동안 호출을 미루고 마지막에 한 번 실행한다. OpenSearch, embedding, 기존 frontend와 Wiki API graph 계약은 변경하지 않는다.

**Tech Stack:** Python 3.11, `python-frontmatter`, PyYAML, pytest, Obsidian Markdown wikilinks

## Global Constraints

- 작업 경로는 `/Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform`이고 브랜치는 `feat/obsidian-wiki-platform`이다.
- 실제 Vault는 `WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki`로만 주입하고 코드에 경로를 하드코딩하지 않는다.
- OpenSearch mapping, embedding, 기존 문서 재임베딩을 변경하지 않는다.
- `yield_frontend`, `wiki_frontend`, `repl_agent/frontend`, Streamlit UI를 변경하지 않는다.
- 기존 `/api/wiki/graph` 응답 shape를 변경하지 않는다.
- 링크와 filename은 metadata/citation에서 결정하며 LLM이나 자연어 keyword/regex 규칙을 사용하지 않는다.
- Concept/Super Concept의 managed marker 밖 본문과 marker 없는 사용자 파일을 보존한다.
- 실제 Vault 변경 전 복구 가능한 백업을 만들고 실제 Obsidian UI에서 Graph를 검증한다.

---

### Task 1: M2 Vault path contract

**Files:**
- Modify: `08-YieldAgent/wiki_config.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_config.py`

**Interfaces:**
- Produces: `WikiPaths.products`, `product_fails`, `operations`, `purpose`, `schema`, `overview`, `obsidian`, `graph_config`
- Produces: `initialize_wiki_vault(paths)`가 M2 directory와 create-only root 문서를 준비하는 계약

- [ ] **Step 1: Write the failing path/layout tests**

```python
def test_resolve_paths_includes_m2_materialized_graph(tmp_path):
    root = (tmp_path / "YieldWiki").resolve()
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(root)})
    assert paths.products == root / "products"
    assert paths.product_fails == root / "product_fails"
    assert paths.operations == root / "operations"
    assert paths.purpose == root / "purpose.md"
    assert paths.schema == root / "schema.md"
    assert paths.overview == root / "overview.md"
    assert paths.obsidian == root / ".obsidian"
    assert paths.graph_config == root / ".obsidian" / "graph.json"


def test_initialize_creates_m2_directories_without_overwriting_operator_docs(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    paths.root.mkdir(parents=True)
    paths.root.joinpath("purpose.md").write_text("operator purpose\n", encoding="utf-8")
    initialize_wiki_vault(paths)
    assert paths.purpose.read_text(encoding="utf-8") == "operator purpose\n"
    assert paths.schema.exists()
    assert paths.overview.exists()
    assert all(path.is_dir() for path in (paths.products, paths.product_fails, paths.operations, paths.obsidian))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_config.py -k 'm2_materialized or m2_directories'`

Expected: FAIL because the new `WikiPaths` attributes do not exist.

- [ ] **Step 3: Extend the path dataclass and create-only initialization**

Add the exact fields to `WikiPaths`, resolve them under `root`, include the four new directories in initialization/validation, and create `purpose.md`, `schema.md`, `overview.md` only when missing. Use the existing `_initialize_file()` atomic create helper. Do not create or overwrite `graph.json` in this task; Task 3 owns its content.

- [ ] **Step 4: Run the full Wiki config tests and verify GREEN**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_config.py`

Expected: all tests pass, including updated exact managed-directory assertions.

- [ ] **Step 5: Commit the path contract**

```bash
git add 08-YieldAgent/wiki_config.py 08-YieldAgent/tests/wiki/test_wiki_config.py
git commit -m "feat: add Obsidian graph vault paths"
```

### Task 2: Deterministic graph model and Markdown rendering

**Files:**
- Create: `08-YieldAgent/wiki_materializer.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`

**Interfaces:**
- Consumes: `WikiPaths` from Task 1 and Concept/Super Concept Markdown in the Vault
- Produces: `MaterializationReport(created, modified, deleted, unchanged, errors)`
- Produces: `materialize_wiki(paths: WikiPaths, *, apply: bool = False) -> MaterializationReport`

- [ ] **Step 1: Write failing topology and body-preservation tests**

Create a temporary Vault containing one Concept with canonical metadata and two citation dictionaries. Assert that `materialize_wiki(paths, apply=True)` creates these exact links:

```python
assert "[[product_fails/4SS_EASY|EASY]]" in paths.products.joinpath("4SS.md").read_text()
assert "[[products/4SS|4SS]]" in paths.product_fails.joinpath("4SS_EASY.md").read_text()
assert "[[operations/PRE_METAL_CLN|PRE METAL CLN]]" in paths.product_fails.joinpath("4SS_EASY.md").read_text()
assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in paths.operations.joinpath("PRE_METAL_CLN.md").read_text()
assert "[[sources/FH-000238|FH-000238]]" in paths.concepts.joinpath("4SS_PRE_METAL_CLN_EASY.md").read_text()
assert "LLM BODY SENTINEL" in paths.concepts.joinpath("4SS_PRE_METAL_CLN_EASY.md").read_text()
```

Also assert a Source page contains its `source_file`, `download_url`, and Concept backlink; `index.md` counts one product, one product fail, one operation, one concept, and two sources.

- [ ] **Step 2: Run the materializer test and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py -k 'topology'`

Expected: collection fails with `ModuleNotFoundError: No module named 'wiki_materializer'`.

- [ ] **Step 3: Implement the minimal graph model and renderers**

Implement these public types and function exactly:

```python
@dataclass(frozen=True)
class MaterializationReport:
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed_count(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted)


def materialize_wiki(paths: WikiPaths, *, apply: bool = False) -> MaterializationReport:
    plan = _build_plan(paths)
    if plan.errors:
        return MaterializationReport(errors=plan.errors)
    return _execute_plan(paths, plan, apply=apply)
```

Define `_MaterializationPlan(targets: dict[Path, str], deletions: tuple[Path, ...], errors: tuple[str, ...])`, `_build_plan(paths) -> _MaterializationPlan`, and `_execute_plan(paths, plan, *, apply: bool) -> MaterializationReport` in the same module. `_build_plan` performs no writes; `_execute_plan` compares rendered UTF-8 bytes to current files and either reports or atomically applies the exact plan.

The implementation must:

- read Concept metadata only from `product`, `fail_type`, `cause_oper`, `citations`, and `id`;
- build shared Operation nodes when an operation appears under multiple Product Fail nodes;
- sort every node/link list by canonical identifier;
- create Source nodes only for citations with non-empty `doc_id`;
- preserve Concept content outside `<!-- yield-wiki:knowledge-links:start -->` and `<!-- yield-wiki:knowledge-links:end -->`;
- render generated pages with `generated_by: yield-wiki-materializer`;
- render `index.md` and `overview.md` from the computed graph, not cached counters;
- return planned relative paths without writing when `apply=False`.

Keep all helpers private in this module; do not import `wiki_store` to avoid a circular dependency.

- [ ] **Step 4: Run focused topology tests and verify GREEN**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py -k 'topology or body'`

Expected: all selected tests pass.

- [ ] **Step 5: Add failing Super Concept tests**

Add one Super Concept whose `source_concept_ids` contains one real Concept and one missing Concept. Assert:

```python
assert frontmatter.load(super_path).metadata["status"] == "stale"
assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in super_path.read_text()
assert "concept:4SS|STI CMP|EASY(W)" in super_path.read_text()
assert "[[super_concepts/fail_type_EASY|fail_type=EASY]]" in concept_path.read_text()
```

- [ ] **Step 6: Run the Super Concept test and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py -k 'super_concept'`

Expected: FAIL because Super Concept managed blocks and stale status are not implemented.

- [ ] **Step 7: Implement Super Concept managed links**

Parse `source_concept_ids`, link only existing Concepts, list missing canonical IDs as plain text, and set `status: stale` when any reference is missing. If every reference exists, preserve the operational `reference_only` status. Update only metadata plus the managed block and retain the existing body text.

- [ ] **Step 8: Run all materializer tests and commit**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py`

Expected: all tests pass.

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py
git commit -m "feat: materialize Obsidian Wiki graph"
```

### Task 3: Safety, idempotency, graph defaults, and backfill CLI

**Files:**
- Modify: `08-YieldAgent/wiki_materializer.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`
- Create: `08-YieldAgent/materialize_obsidian_wiki.py`
- Create: `08-YieldAgent/tests/wiki/test_materialize_obsidian_wiki_cli.py`

**Interfaces:**
- Consumes: `materialize_wiki()` from Task 2
- Produces: CLI contract `materialize_obsidian_wiki.py (--check | --apply)` with exit `0` on valid plan/apply and `1` on validation errors

- [ ] **Step 1: Write failing safety and idempotency tests**

Add tests that assert:

```python
first = materialize_wiki(paths, apply=True)
snapshot = {str(p.relative_to(paths.root)): p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
second = materialize_wiki(paths, apply=True)
assert first.changed_count > 0
assert second.changed_count == 0
assert snapshot == {str(p.relative_to(paths.root)): p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
```

Also create stale generated and user-owned files under `products/`; assert only the file containing `generated_by: yield-wiki-materializer` is deleted. Add malformed Concept and conflicting duplicate Citation fixtures; assert `apply=True` returns errors and changes no file. Add an existing operator-authored `purpose.md`, `schema.md`, and `.obsidian/graph.json`; assert all three remain byte-identical.

- [ ] **Step 2: Run safety tests and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py -k 'idempotent or user_owned or validation or graph_config'`

Expected: one or more assertions fail because full preflight validation, managed pruning, and graph defaults are incomplete.

- [ ] **Step 3: Implement preflight and safe apply**

Before any write:

- validate every Concept triple and citation;
- reject empty/colliding stable filenames;
- detect conflicting non-empty metadata for the same `doc_id`;
- compute the complete target write/delete set;
- return `errors` and perform zero writes when any validation fails.

For valid apply, write same-directory unique temp files and promote with `os.replace`. Skip files whose bytes already equal the rendered content. Delete only obsolete `.md` files whose parsed metadata contains `generated_by: yield-wiki-materializer`.

Create `.obsidian/graph.json` only when missing with the existing Obsidian schema and search filter `-file:index -file:log -path:lint_logs`; never modify an existing config.

- [ ] **Step 4: Run all materializer tests and verify GREEN**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py`

Expected: all tests pass and the second apply reports zero changes.

- [ ] **Step 5: Write the failing CLI tests**

Use subprocess with `WIKI_VAULT_PATH` pointing at a temporary Vault. `--check` must report planned changes without changing the snapshot; `--apply` must create Product through Source pages; passing both flags or neither must exit non-zero.

- [ ] **Step 6: Run CLI tests and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_materialize_obsidian_wiki_cli.py`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 7: Implement the CLI**

Use `argparse` with a required mutually exclusive group. Resolve and initialize `WikiPaths`, call `materialize_wiki(paths, apply=args.apply)`, print sorted created/modified/deleted/error paths, and return `1` when `report.errors` is non-empty, otherwise `0`.

- [ ] **Step 8: Run CLI and materializer suites, then commit**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer.py tests/wiki/test_materialize_obsidian_wiki_cli.py`

Expected: all tests pass.

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/materialize_obsidian_wiki.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py 08-YieldAgent/tests/wiki/test_materialize_obsidian_wiki_cli.py
git commit -m "feat: add safe Wiki graph backfill"
```

### Task 4: Incremental write hooks and bootstrap batching

**Files:**
- Modify: `08-YieldAgent/wiki_store.py`
- Modify: `08-YieldAgent/bootstrap_wiki_warmup.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_materializer_hooks.py`

**Interfaces:**
- Consumes: `materialize_wiki(_PATHS, apply=True)` from Task 2
- Produces: `upsert_concept(filters: dict, source_episode_id: str | None = None, links: list[str] | None = None, *, synthesized_body: str | None = None, confidence: float | None = None, citations: list[dict] | None = None, evidence: dict | None = None, materialize: bool = True) -> tuple[str, str]`
- Produces: `upsert_super_concept(axis: str, axis_value: str, source_concept_ids: list[str], synthesized_body: str, confidence: float, *, materialize: bool = True) -> tuple[str, Path]`
- Produces: `wiki_store.materialize_obsidian_wiki() -> MaterializationReport`

- [ ] **Step 1: Write failing store hook tests**

Reload `wiki_store` against a temporary external Vault, monkeypatch `wiki_materializer.materialize_wiki`, and assert a successful Concept upsert invokes it exactly once by default and zero times with `materialize=False`. Repeat for Super Concept upsert. Assert the wrapper always passes `wiki_store._PATHS` and `apply=True`.

- [ ] **Step 2: Run hook tests and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer_hooks.py`

Expected: FAIL because the keyword argument and wrapper do not exist.

- [ ] **Step 3: Implement lazy materialization hooks**

Add this wrapper without importing `wiki_materializer` at module import time:

```python
def materialize_obsidian_wiki():
    from wiki_materializer import materialize_wiki
    return materialize_wiki(_PATHS, apply=True)
```

Add keyword-only `materialize: bool = True` to both upsert functions. Invoke the wrapper after their primary atomic write and log operation completes. Existing callers remain source-compatible.

- [ ] **Step 4: Run hook tests and verify GREEN**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer_hooks.py tests/wiki/test_wiki_store_external_vault.py`

Expected: all tests pass.

- [ ] **Step 5: Write the failing bootstrap batch test**

Patch `process_triple` dependencies so two triples synthesize without OpenSearch or LLM. Assert `wiki_store.upsert_concept` receives `materialize=False` for both and `wiki_store.materialize_obsidian_wiki()` is called exactly once after the loop.

- [ ] **Step 6: Run the bootstrap test and verify RED**

Run: `cd 08-YieldAgent && pytest -q tests/wiki/test_wiki_materializer_hooks.py -k 'bootstrap'`

Expected: FAIL because bootstrap currently materializes neither explicitly nor in a batch.

- [ ] **Step 7: Implement batch deferral**

Pass `materialize=False` in `process_triple()`. In apply mode, call `wiki_store.materialize_obsidian_wiki()` once after all seeds are processed and before counts/lint. If the report has errors, print each error and return failure rather than claiming a completed backfill.

- [ ] **Step 8: Run Wiki suites and commit**

Run: `cd 08-YieldAgent && pytest -q tests/wiki`

Expected: all Wiki tests pass.

```bash
git add 08-YieldAgent/wiki_store.py 08-YieldAgent/bootstrap_wiki_warmup.py 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py 08-YieldAgent/tests/wiki/test_wiki_materializer_hooks.py
git commit -m "feat: refresh Obsidian graph after Wiki writes"
```

### Task 5: Real Vault backfill and Obsidian end-to-end verification

**Files:**
- Modify only if verification finds an M2 defect: files introduced or modified in Tasks 1–4
- Do not add the external Vault or its backup to Git

**Interfaces:**
- Consumes: completed CLI and actual `WIKI_VAULT_PATH`
- Produces: verified connected Obsidian Graph without reembedding

- [ ] **Step 1: Run pre-backfill regression verification**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/wiki
git diff --check
git diff --name-only 86274a2..HEAD -- yield_frontend wiki_frontend repl_agent/frontend pages app.py
```

Expected: Wiki suite passes, diff check is clean, and frontend diff command prints nothing.

- [ ] **Step 2: Create a recoverable external Vault backup**

Create a sibling timestamped copy such as `/Users/daehwankim/SYLDAIX/YieldWiki.backup-20260801-HHMMSS` after confirming the source and destination are exact non-symlink directories. Print the chosen backup path and verify its file count and hashes match the source before applying changes.

- [ ] **Step 3: Preview actual backfill**

Run:

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki python materialize_obsidian_wiki.py --check
```

Expected: exit 0; planned Product, Product Fail, Operation, Concept managed-block, Source, index, and overview changes are listed; no validation errors.

- [ ] **Step 4: Apply actual backfill twice**

Run:

```bash
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki python materialize_obsidian_wiki.py --apply
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki python materialize_obsidian_wiki.py --apply
```

Expected: first apply creates/updates the graph; second apply reports zero created, modified, and deleted files.

- [ ] **Step 5: Verify every actual wikilink resolves**

Parse every `[[target|alias]]` or `[[target]]` in the actual Vault, normalize extensionless targets relative to the Vault root, and assert every target has a corresponding `.md` file. Expected broken link count: `0`.

- [ ] **Step 6: Verify the real Wiki API contract**

Start or import the FastAPI Wiki router against the actual external Vault and request the existing Concept node plus `/api/wiki/graph?view=product_tree`. Assert HTTP 200 and the pre-existing response keys remain unchanged.

- [ ] **Step 7: Verify Obsidian UI directly**

Use the `computer-use:computer-use` skill to open `/Users/daehwankim/SYLDAIX/YieldWiki` in Obsidian. Confirm visible edges along `4SS → 4SS EASY → PRE METAL CLN → 4SS_PRE_METAL_CLN_EASY → FH-000238`, click nodes to open their Markdown, and confirm operational files are filtered. Capture the observed result in the final report.

- [ ] **Step 8: Run final verification and commit any verification-only fix**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/wiki
git diff --check
git status --short
```

Expected: Wiki suite passes and only intentional files are changed. If Task 5 exposed a defect, first add a failing regression test, observe RED, implement the minimal fix, observe GREEN, then commit with `fix: correct Obsidian graph materialization`.
