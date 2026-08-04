# Obsidian Product Tree Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an idempotent Markdown-only `LOTCD → FAIL → OPER` projection and materialize it into the live Obsidian Vault.

**Architecture:** Extend `WikiPaths` with one managed `product_tree` directory. The existing materializer derives three generated note types from canonical Concept metadata, copies the Concept body into the triple-scoped operation leaf, and owns stale projection cleanup. New Vaults default the native Graph filter to `path:product_tree`; the live rollout preserves every other Graph preference.

**Tech Stack:** Python 3.11, python-frontmatter, pytest, existing `PinnedWikiMutation`, Obsidian Markdown.

## Global Constraints

- Preserve canonical Concept, Source, Entity, Relation, Review, and Super Concept notes unchanged.
- Keep projection files one directory below the Vault root; do not broaden pinned mutation depth.
- Use `generated_by: yield-wiki-materializer` and exact generated ownership validation.
- Do not overwrite or delete foreign files in `product_tree`.
- Do not add a plugin or another scheduler command.
- Back up the live Vault before the first projection write.

---

### Task 1: Register the managed product-tree namespace

**Files:**
- Modify: `08-YieldAgent/wiki_config.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_config.py`

**Interfaces:**
- Produces: `WikiPaths.product_tree: Path`
- Produces: initialized and validated `<vault>/product_tree` directory

- [ ] **Step 1: Write the failing path and initialization tests**

Add assertions to the existing path and initialization tests:

```python
assert paths.product_tree == root / "product_tree"
assert paths.product_tree.is_dir()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --frozen pytest -q tests/wiki/test_wiki_config.py
```

Expected: failure because `WikiPaths` has no `product_tree` attribute.

- [ ] **Step 3: Add the path to every managed-directory boundary**

Add `product_tree: Path` to `WikiPaths`, resolve it as `root / "product_tree"`,
and include it in `initialize_wiki_vault()` plus `_managed_writer_directories()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add 08-YieldAgent/wiki_config.py 08-YieldAgent/tests/wiki/test_wiki_config.py
git commit -m "feat(wiki): add product tree namespace"
```

### Task 2: Generate and safely reconcile the three-tier Markdown projection

**Files:**
- Modify: `08-YieldAgent/wiki_materializer.py`
- Modify: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`

**Interfaces:**
- Consumes: `WikiPaths.product_tree`
- Produces: `product_tree/<product>.md`
- Produces: `product_tree/<product>__<fail>.md`
- Produces: `product_tree/<product>__<fail>__<oper>.md`

- [ ] **Step 1: Write failing topology and leaf-content tests**

Create a Concept for `4SS / EASY / PRE METAL CLN`, run materialization, and assert:

```python
product = paths.product_tree / "4SS.md"
fail = paths.product_tree / "4SS__EASY.md"
oper = paths.product_tree / "4SS__EASY__PRE_METAL_CLN.md"
assert "[[product_tree/4SS__EASY|EASY]]" in product.read_text()
assert "[[product_tree/4SS__EASY__PRE_METAL_CLN|PRE METAL CLN]]" in fail.read_text()
leaf = oper.read_text()
assert "Generated body" in leaf
assert "[[concepts/" not in leaf
assert "[[sources/" not in leaf
```

Also assert the three exact frontmatter types and the leaf `concept_id`.

- [ ] **Step 2: Write failing isolation, cleanup, and idempotency tests**

Cover two triples sharing an operation name, deletion of a stale generated
projection file, preservation of a foreign projection file, and a second run
with `created == modified == deleted == 0`.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
uv run --frozen pytest -q tests/wiki/test_wiki_materializer.py -k product_tree
```

Expected: projection files are missing.

- [ ] **Step 4: Add deterministic projection paths and targets**

Inside `_build_plan()`, derive flat paths with the existing `_stable_filename()`:

```python
product_path = paths.product_tree / f"{_stable_filename(product)}.md"
fail_path = paths.product_tree / (
    f"{_stable_filename(product)}__{_stable_filename(fail_type)}.md"
)
oper_path = paths.product_tree / (
    f"{_stable_filename(product)}__{_stable_filename(fail_type)}"
    f"__{_stable_filename(cause_oper)}.md"
)
```

Render parent-to-child Wiki links only. Render the operation leaf with its
canonical product/fail/operation metadata, `concept_id`, title, and exact
current Concept body. Do not render any internal Wiki link in the leaf.

- [ ] **Step 5: Extend exact owner validation and stale cleanup**

Teach `_namespace_deletion_owner()` to validate the three projection node
types against their metadata-derived paths. Scan `paths.product_tree.glob("*.md")`
for stale materializer-owned files. Reject collisions and preserve foreign files
using the existing target preflight and pinned mutation path.

- [ ] **Step 6: Set new-Vault Graph defaults**

Change only the default JSON created when `graph.json` does not exist:

```json
{"search":"path:product_tree","showOrphans":false}
```

Keep the existing test proving an operator-created `graph.json` is byte-for-byte
preserved.

- [ ] **Step 7: Run focused and regression tests**

```bash
uv run --frozen pytest -q tests/wiki/test_wiki_config.py tests/wiki/test_wiki_materializer.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py
git commit -m "feat(wiki): materialize Markdown product tree"
```

### Task 3: Materialize and verify the live Vault

**Files:**
- Modify: `08-YieldAgent/docs/wiki-m5-e2e-results.md`
- Generate externally: `/Users/daehwankim/SYLDAIX/YieldWiki/product_tree/*.md`
- Modify externally: `/Users/daehwankim/SYLDAIX/YieldWiki/.obsidian/graph.json`

**Interfaces:**
- Consumes: `materialize_obsidian_wiki(paths, apply=True)` through the existing CLI
- Produces: recoverable live Product Tree Markdown

- [ ] **Step 1: Back up the live Vault**

Create a timestamped sibling backup and verify `diff -qr` is empty before any
write. Never overwrite an existing backup.

- [ ] **Step 2: Run read-only materializer preview**

```bash
uv run --frozen python materialize_obsidian_wiki.py --check \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

Expected: only generated product-tree creates plus existing known warnings.

- [ ] **Step 3: Apply materialization**

```bash
uv run --frozen python materialize_obsidian_wiki.py --apply \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

Expected: `product_tree` Markdown files created with zero errors.

- [ ] **Step 4: Preserve Graph preferences while selecting Product Tree**

Back up `.obsidian/graph.json`, parse it as JSON, set only:

```json
{"search":"path:product_tree","showOrphans":false}
```

Write it atomically and confirm every other key/value is unchanged.

- [ ] **Step 5: Verify actual Markdown topology and idempotency**

Assert the live links for `4SS → EASY → PRE METAL CLN`, run materialization a
second time, and require `created=0 modified=0 deleted=0 errors=0`. Compare the
second-run Vault hashes to prove no change.

- [ ] **Step 6: Run final automated verification**

```bash
uv run --frozen pytest -q tests/wiki/test_wiki_config.py tests/wiki/test_wiki_materializer.py
```

Also run the full Wiki test suite and Obsidian plugin build, reporting the known
environment-only statsmodels failures separately if unchanged.

- [ ] **Step 7: Record and commit E2E evidence**

Document backup path, generated file counts, exact example links, Graph config
delta, idempotency result, and test totals.

```bash
git add 08-YieldAgent/docs/wiki-m5-e2e-results.md
git commit -m "docs: verify Obsidian Markdown product tree"
```
