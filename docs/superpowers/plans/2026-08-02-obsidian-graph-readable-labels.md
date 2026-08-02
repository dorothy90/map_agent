# Obsidian Graph Readable Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hash-only generated Entity and Relation filenames with readable, collision-safe names and complete the real Obsidian Citation-open acceptance check.

**Architecture:** Keep full SHA-256 graph IDs as the canonical identity in frontmatter and Graph RAG. Add one deterministic filename projection for display, migrate only materializer-owned legacy paths by full ID, and leave the existing Plugin Citation code unchanged unless the real Desktop check exposes a defect.

**Tech Stack:** Python 3.12, python-frontmatter, pytest, Obsidian Markdown/Vault, TypeScript, Vitest, Obsidian Desktop

## Global Constraints

- Work only in `/Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform` on `feat/obsidian-wiki-platform`.
- Canonical `entity:sha256:<64 hex>` and `relation:sha256:<64 hex>` IDs do not change.
- Entity filenames use `<canonical name>--<hash8>.md`; Relation filenames use `<subject> <predicate> <object>--<hash8>.md`.
- Complete UTF-8 filenames stay below 180 bytes and retain the eight-character hash suffix.
- Never rename or delete user-owned Markdown.
- Do not change semantic extraction, embeddings, OpenSearch schema, React frontend, or add Neo4j.
- Do not add keyword, regex, phrase-list, or special-case natural-language routing rules.
- Follow RED → GREEN for every production behavior change.
- Before changing `/Users/daehwankim/SYLDAIX/YieldWiki`, create and verify a recoverable backup.
- External OpenRouter calls are limited to the already approved `4SS / EASY / PRE METAL CLN` E2E scenario.

---

### Task 1: Deterministic readable Graph paths and labels

**Files:**
- Modify: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`
- Modify: `08-YieldAgent/wiki_materializer.py`

**Interfaces:**
- Consumes: `_stable_graph_id(kind: str, payload: dict[str, Any]) -> str` and existing Entity/Relation records.
- Produces: `_readable_graph_path(directory: Path, node_id: str, label: str) -> Path` and `_relation_label(subject: str, predicate: str, object_name: str) -> str`.

- [ ] **Step 1: Replace hash-only expectations with readable-path expectations**

In `test_materializes_product_to_source_topology_and_preserves_concept_body`, map each generated note to its metadata and assert behavior rather than a hardcoded digest:

```python
entity_paths = {
    frontmatter.load(path).metadata["canonical_name"]: path
    for path in paths.entities.glob("*.md")
}
assert re.fullmatch(
    r"Queue time 초과--[0-9a-f]{8}\.md",
    entity_paths["Queue time 초과"].name,
)
assert re.fullmatch(
    r"자연 산화--[0-9a-f]{8}\.md",
    entity_paths["자연 산화"].name,
)
relation_path = next(paths.relations.glob("*.md"))
assert re.fullmatch(
    r"Queue time 초과 causes 자연 산화--[0-9a-f]{8}\.md",
    relation_path.name,
)
```

Also assert the Concept and index use the readable Relation label rather than the canonical hash ID:

```python
assert f"[[relations/{relation_path.stem}|Queue time 초과 causes 자연 산화]]" in concept_text
assert f"[[relations/{relation_path.stem}|Queue time 초과 causes 자연 산화]]" in index
```

- [ ] **Step 2: Add filename safety and collision-suffix tests**

Add these two focused tests:

```python
def test_graph_filenames_preserve_unicode_and_bound_unsafe_long_labels(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(
        paths,
        entities=[
            {"canonical_name": 'Plasma/Damage:*?"<>|', "entity_type": "condition"},
            {"canonical_name": "가" * 200, "entity_type": "condition"},
        ],
        relations=[],
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    names = sorted(path.name for path in paths.entities.glob("*.md"))
    assert any(name.startswith("Plasma_Damage_") for name in names)
    assert all(not set('/\\:*?"<>|').intersection(name) for name in names)
    assert all(len(name.encode("utf-8")) < 180 for name in names)
    assert all(re.search(r"--[0-9a-f]{8}\.md$", name) for name in names)


def test_same_sanitized_graph_prefix_keeps_distinct_hash_paths(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(
        paths,
        entities=[
            {"canonical_name": "A/B", "entity_type": "condition"},
            {"canonical_name": "A:B", "entity_type": "condition"},
        ],
        relations=[],
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    names = sorted(path.name for path in paths.entities.glob("*.md"))
    assert len(names) == 2
    assert all(name.startswith("A_B--") for name in names)
    assert names[0] != names[1]
```

- [ ] **Step 3: Run the focused test and verify RED**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py \
  -k 'topology or graph_filenames or same_sanitized'
```

Expected: failures show the current 64-character hash filenames and missing readable Relation link label.

- [ ] **Step 4: Implement the minimal readable filename projection**

In `wiki_materializer.py`, replace `_graph_path` with these focused helpers:

```python
_GRAPH_FILENAME_MAX_BYTES = 179
_UNSAFE_GRAPH_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _relation_label(subject: str, predicate: str, object_name: str) -> str:
    return f"{subject} {predicate} {object_name}"


def _truncate_utf8(value: str, byte_limit: int) -> str:
    while len(value.encode("utf-8")) > byte_limit:
        value = value[:-1]
    return value


def _readable_graph_path(
    directory: Path,
    node_id: str,
    label: str,
) -> Path:
    digest = node_id.rsplit(":", 1)[-1]
    suffix = f"--{digest[:8]}.md"
    readable = _UNSAFE_GRAPH_FILENAME.sub("_", label)
    readable = re.sub(r"_+", "_", readable).strip(" .")
    fallback = node_id.split(":", 1)[0]
    byte_limit = _GRAPH_FILENAME_MAX_BYTES - len(suffix.encode("utf-8"))
    readable = _truncate_utf8(readable or fallback, byte_limit).rstrip(" .")
    return directory / f"{readable or fallback}{suffix}"
```

Build `entity_paths` with `canonical_name`, build `relation_paths` with `_relation_label(...)`, store `display_label` on each Relation record, and use that label for Relation wikilinks in Entity, Concept, and index notes. Do not change full IDs in metadata.

- [ ] **Step 5: Run the focused test and verify GREEN**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py \
  -k 'topology or graph_filenames or same_sanitized'
```

Expected: all selected tests pass.

- [ ] **Step 6: Run the complete materializer test file**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py
```

Expected: all tests pass with no new warnings.

- [ ] **Step 7: Commit Task 1**

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py
git commit -m "feat: add readable Obsidian graph paths"
```

---

### Task 2: Safe legacy-path migration and crash recovery

**Files:**
- Modify: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`
- Modify: `08-YieldAgent/wiki_materializer.py`

**Interfaces:**
- Consumes: readable active target maps from Task 1 and generated-note ownership metadata.
- Produces: `_scan_generated_graph_paths(paths: WikiPaths) -> tuple[dict[str, list[Path]], list[str]]` plus legacy migration deletions included in `_MaterializationPlan.deletions`.

- [ ] **Step 1: Add RED tests for legacy migration and interrupted-run recovery**

Create a test helper that writes a materializer-owned graph note at an explicit path:

```python
def _write_generated_graph_note(path, *, node_id, node_type, status="active"):
    path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content=f"# {node_id}\n",
                id=node_id,
                type=node_type,
                generated_by="yield-wiki-materializer",
                status=status,
            )
        ),
        encoding="utf-8",
    )
```

Add tests covering both the normal and interrupted migration:

```python
def test_active_legacy_hash_path_is_replaced_not_marked_stale(tmp_path):
    from wiki_materializer import _stable_graph_id, materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    node_id = _stable_graph_id("entity", {"canonical_name": "Queue time 초과"})
    legacy = paths.entities / f"{node_id.rsplit(':', 1)[-1]}.md"
    _write_generated_graph_note(legacy, node_id=node_id, node_type="entity")

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    assert not legacy.exists()
    assert legacy.relative_to(paths.root).as_posix() in report.deleted
    readable = [
        path for path in paths.entities.glob("Queue time 초과--*.md")
        if frontmatter.load(path).metadata["id"] == node_id
    ]
    assert len(readable) == 1
    assert frontmatter.load(readable[0]).metadata["status"] == "active"


def test_interrupted_path_migration_deletes_only_legacy_duplicate(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    first = materialize_wiki(paths, apply=True)
    assert first.errors == ()
    readable = next(paths.entities.glob("Queue time 초과--*.md"))
    node_id = frontmatter.load(readable).metadata["id"]
    legacy = paths.entities / f"{node_id.rsplit(':', 1)[-1]}.md"
    legacy.write_bytes(readable.read_bytes())

    resumed = materialize_wiki(paths, apply=True)

    assert resumed.errors == ()
    assert readable.exists()
    assert not legacy.exists()
    assert resumed.deleted == (legacy.relative_to(paths.root).as_posix(),)
```

- [ ] **Step 2: Add RED tests for unsafe duplicate and user-owned target handling**

```python
def test_duplicate_noncanonical_graph_paths_are_fatal_and_write_nothing(tmp_path):
    from wiki_materializer import _stable_graph_id, materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    node_id = _stable_graph_id("entity", {"canonical_name": "Queue time 초과"})
    for name in ("duplicate-a.md", "duplicate-b.md"):
        _write_generated_graph_note(
            paths.entities / name,
            node_id=node_id,
            node_type="entity",
        )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "duplicate generated graph id" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before
```

Extend the collision test to place a user-owned note at the exact readable target and assert a fatal no-write result.

- [ ] **Step 3: Run migration tests and verify RED**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py \
  -k 'legacy_hash or interrupted_path or duplicate_noncanonical or generated_path_collision'
```

Expected: the new migration tests fail because legacy paths are currently marked stale and duplicate IDs are not classified.

- [ ] **Step 4: Implement generated-note scanning and migration classification**

Add one scan before active-path collision validation:

```python
def _scan_generated_graph_paths(
    paths: WikiPaths,
) -> tuple[dict[str, list[Path]], list[str]]:
    by_id: dict[str, list[Path]] = {}
    errors: list[str] = []
    for directory, node_type in (
        (paths.entities, "entity"),
        (paths.relations, "relation"),
    ):
        for path in sorted(directory.glob("*.md")):
            try:
                metadata = frontmatter.load(path).metadata
            except Exception:
                continue
            if metadata.get("generated_by") != _GENERATED_BY:
                continue
            if metadata.get("type") != node_type:
                continue
            node_id = str(metadata.get("id") or "")
            if not node_id:
                errors.append(f"{_relative(paths, path)}: generated graph note missing id")
                continue
            by_id.setdefault(node_id, []).append(path)
    return by_id, errors
```

For each active full ID, classify existing paths as canonical target, exact legacy `<full digest>.md`, or noncanonical. Permit zero or one legacy path, including the recoverable canonical-plus-legacy pair. Add the legacy path to `migration_deletions`, exclude it from stale-note rendering, and prepend it to the final deletion set. Any two noncanonical claims or a non-legacy claim must add a `duplicate generated graph id` error before `_execute_plan` can write.

Retain the existing target ownership check: an existing readable path is valid only when both `generated_by` and full `id` match.

- [ ] **Step 5: Run migration tests and verify GREEN**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py \
  -k 'legacy_hash or interrupted_path or duplicate_noncanonical or generated_path_collision'
```

Expected: all selected tests pass.

- [ ] **Step 6: Verify stale semantics and idempotency did not regress**

```bash
cd 08-YieldAgent
uv run --frozen --with pytest pytest -q tests/wiki/test_wiki_materializer.py \
  -k 'stale or idempotent or legacy_hash or interrupted_path'
```

Expected: all selected tests pass; truly removed IDs stay as stale notes while migrated active IDs do not leave duplicate nodes.

- [ ] **Step 7: Commit Task 2**

```bash
git add 08-YieldAgent/wiki_materializer.py 08-YieldAgent/tests/wiki/test_wiki_materializer.py
git commit -m "fix: migrate legacy Obsidian graph paths"
```

---

### Task 3: Automated regression and dry-run migration audit

**Files:**
- Modify only if a test exposes a defect: `08-YieldAgent/wiki_materializer.py`
- Modify only if a test contract is wrong: `08-YieldAgent/tests/wiki/test_wiki_materializer.py`

**Interfaces:**
- Consumes: Task 1 readable paths and Task 2 migration classification.
- Produces: green Wiki, Graph projection, Plugin, and build gates before touching the live Vault.

- [ ] **Step 1: Run all Wiki and memory regressions**

```bash
cd 08-YieldAgent
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --with pytest pytest -q \
  -p no:cacheprovider tests/wiki tests/test_user_memory.py
```

Expected: zero failures.

- [ ] **Step 2: Run confirm-edit regressions**

```bash
cd 08-YieldAgent
OPENROUTER_API_KEY=test-key OPENROUTER_BASE_URL=http://test \
uv run --frozen --with pytest python - <<'PY'
import langchain
langchain.verbose = False
langchain.debug = False
langchain.llm_cache = None
import pytest
raise SystemExit(pytest.main([
    '-q', '-p', 'no:cacheprovider', 'tests/test_confirm_edit.py'
]))
PY
```

Expected: zero failures.

- [ ] **Step 3: Run Plugin tests and production build**

```bash
cd obsidian/plugin
npm test
npm run build
```

Expected: Vitest reports zero failures and the TypeScript/esbuild production build exits 0.

- [ ] **Step 4: Preview the real Vault migration without writes**

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
PYTHON_DOTENV_DISABLED=1 \
uv run --frozen python materialize_obsidian_wiki.py --check
```

Expected: `errors=0`; created readable paths and deleted legacy hash paths are reported. Existing seven invalid Relation endpoint warnings may remain, but no new warning class is accepted without investigation.

- [ ] **Step 5: Run repository safety checks**

```bash
cd /Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform
uv lock --check
git diff --check
git status --short
```

Expected: lock and whitespace checks pass. Only task files plus the existing `.superpowers/brainstorm` runtime artifacts may appear.

---

### Task 4: Live Vault migration and Obsidian Citation acceptance

**Files:**
- Modify: `08-YieldAgent/docs/wiki-m5-e2e-results.md`
- Modify only if real verification reproduces a Citation defect: `obsidian/plugin/src/view.ts`
- Modify only with a RED test if a Citation defect is found: `obsidian/plugin/tests/view.test.ts`

**Interfaces:**
- Consumes: live `/Users/daehwankim/SYLDAIX/YieldWiki`, current authenticated Backend, existing Plugin `source_path` handling, and the approved free OpenRouter models.
- Produces: a recoverable Vault migration, readable Desktop graph, opened canonical Source note, and recorded E2E evidence.

- [ ] **Step 1: Create and hash-verify a live Vault backup**

Use the explicit backup path and stop if it already exists:

```bash
test ! -e /Users/daehwankim/SYLDAIX/YieldWiki.backup-20260802-readable-labels
cp -a /Users/daehwankim/SYLDAIX/YieldWiki \
  /Users/daehwankim/SYLDAIX/YieldWiki.backup-20260802-readable-labels
```

Compute sorted relative-path plus SHA-256 manifests for the live Vault and backup and require identical output before applying changes. Do not delete the backup after verification.

```bash
(cd /Users/daehwankim/SYLDAIX/YieldWiki && \
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256) \
  > /tmp/yield-wiki-readable-live.sha256
(cd /Users/daehwankim/SYLDAIX/YieldWiki.backup-20260802-readable-labels && \
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256) \
  > /tmp/yield-wiki-readable-backup.sha256
cmp /tmp/yield-wiki-readable-live.sha256 \
  /tmp/yield-wiki-readable-backup.sha256
```

Expected: `cmp` exits 0 with no output.

- [ ] **Step 2: Apply the real Vault migration and prove idempotency**

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
PYTHON_DOTENV_DISABLED=1 \
uv run --frozen python materialize_obsidian_wiki.py --apply

WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
PYTHON_DOTENV_DISABLED=1 \
uv run --frozen python materialize_obsidian_wiki.py --check

WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
PYTHON_DOTENV_DISABLED=1 \
uv run --frozen python materialize_obsidian_wiki.py --check
```

Expected: apply reports readable Entity/Relation creations and legacy deletions with `errors=0`. Both subsequent checks report `created=0 modified=0 deleted=0 errors=0` with the same known Relation warnings.

- [ ] **Step 3: Start the authenticated Backend with free models**

Create mode-600 token and PID files without printing the token:

```bash
umask 077
openssl rand -hex 24 > /tmp/yield-wiki-readable-plugin-token
```

Load the existing root `.env` through the dotenv CLI while preserving explicit model overrides with `PYTHON_DOTENV_DISABLED=1`:

```bash
cd /Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform/08-YieldAgent
nohup uv run --with python-dotenv dotenv \
  -f /Users/daehwankim/yield-agent/.env run -- \
  env PYTHON_DOTENV_DISABLED=1 \
  RETRIEVE_CHAIN_MODEL=google/gemma-4-26b-a4b-it:free \
  WIKI_SUMMARIZE_MODEL=nvidia/nemotron-3-super-120b-a12b:free \
  WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
  WIKI_REQUIRE_EXTERNAL_VAULT=true \
  OBSIDIAN_PLUGIN_API_TOKEN="$(< /tmp/yield-wiki-readable-plugin-token)" \
  uv run --frozen uvicorn agent_server:app \
    --host 127.0.0.1 --port 18001 --log-level info \
  > /tmp/yield-wiki-readable-backend.log 2>&1 &
echo $! > /tmp/yield-wiki-readable-backend.pid
```

Wait for startup by inspecting `/tmp/yield-wiki-readable-backend.log`, then verify authentication without printing the token:

```bash
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:18001/health)" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H 'Authorization: Bearer wrong-token' \
  http://127.0.0.1:18001/api/wiki/plugin/health)" = 401
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $(< /tmp/yield-wiki-readable-plugin-token)" \
  http://127.0.0.1:18001/api/wiki/plugin/health)" = 200
```

Expected: all three `test` commands exit 0.

The effective model and Vault settings are:

```text
RETRIEVE_CHAIN_MODEL=google/gemma-4-26b-a4b-it:free
WIKI_SUMMARIZE_MODEL=nvidia/nemotron-3-super-120b-a12b:free
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki
WIKI_REQUIRE_EXTERNAL_VAULT=true
```

- [ ] **Step 4: Verify readable Graph nodes in Obsidian Desktop**

Open the existing `YieldWiki` Vault and Graph view. Confirm the actual active Entity and Relation nodes display natural labels followed by an eight-character hash rather than a 64-character hash. Open one Entity and one Relation note and verify their full frontmatter IDs, Source links, and Concept links remain intact.

- [ ] **Step 5: Verify Citation button opens the canonical Source note**

Configure the Plugin with `http://127.0.0.1:18001` and the temporary token. Ask exactly:

```text
Fail History에서 product 4SS, fail_type EASY, cause_oper PRE METAL CLN을 검색하고 Wiki에 연결된 원인과 조치를 출처와 함께 알려줘
```

Require a normal `stream_end` and at least one rendered Citation. Click one Citation button and confirm Obsidian opens the corresponding canonical `sources/<doc_id>.md` note whose frontmatter `doc_id` equals the clicked citation. This is a Desktop interaction requirement; API-only evidence is insufficient.

If the click fails, first add a failing Vitest reproduction in `obsidian/plugin/tests/view.test.ts`, verify RED, make the smallest change in `src/view.ts`, verify GREEN, rebuild, reinstall the artifact, and repeat the Desktop check. Do not alter Citation code if the existing behavior passes.

- [ ] **Step 6: Stop the temporary Backend and record evidence**

Stop the exact Backend PID and remove only the temporary credential/PID files:

```bash
kill "$(< /tmp/yield-wiki-readable-backend.pid)"
wait "$(< /tmp/yield-wiki-readable-backend.pid)" 2>/dev/null || true
test -z "$(lsof -nP -iTCP:18001 -sTCP:LISTEN -t)"
rm /tmp/yield-wiki-readable-plugin-token \
  /tmp/yield-wiki-readable-backend.pid
```

Expected: port `18001` has no listener. Keep the Backend log and hash manifests as local E2E evidence until the final report is written.

Append to `08-YieldAgent/docs/wiki-m5-e2e-results.md`:

- backup path and matching pre-apply tree hashes;
- created/deleted counts from the migration;
- two idempotent check summaries;
- representative readable Entity and Relation filenames;
- authenticated health results;
- Chat retrieval mode and Citation count;
- clicked Citation label and canonical Source path;
- confirmation that the temporary Backend stopped;
- any unchanged known warnings.

- [ ] **Step 7: Re-run final verification after all live findings**

Repeat Task 3 Steps 1–3 plus:

```bash
cd /Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform
uv lock --check
git diff --check
git status --short
```

Expected: all automated suites and production build pass, the lock is valid, no whitespace errors exist, and no secret or generated live Vault content is staged.

- [ ] **Step 8: Commit the E2E record and any test-driven fix**

If no Plugin defect was found:

```bash
git add 08-YieldAgent/docs/wiki-m5-e2e-results.md
git commit -m "docs: verify readable Obsidian graph labels"
```

If a Plugin defect was fixed, include only the RED/GREEN test and minimal implementation files in the same commit. Never add `.env`, Plugin tokens, Vault notes, backups, or `.superpowers/brainstorm` runtime artifacts.
