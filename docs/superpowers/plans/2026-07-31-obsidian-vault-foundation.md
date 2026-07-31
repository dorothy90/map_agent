# Obsidian Vault Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing Fail History Wiki to one configurable external Obsidian Vault without changing existing frontend code or API response contracts.

**Architecture:** Add one shared Wiki path module, keep compatibility aliases in `wiki_store.py`, and make every Wiki reader/CLI resolve the same `WIKI_VAULT_PATH`. Add a non-destructive migration command and fail-fast production validation, then prove the path with isolated tests and one real OpenSearch → LLM → Vault run.

**Tech Stack:** Python 3.13, FastAPI, python-frontmatter, pytest, OpenSearch, existing LangChain LLM configuration, Markdown filesystem Vault

## Global Constraints

- Work only in `/Users/daehwankim/yield-agent/.worktrees/obsidian-wiki-platform` on branch `feat/obsidian-wiki-platform`.
- The workstation Vault path is `/Users/daehwankim/SYLDAIX/YieldWiki`, supplied through `WIKI_VAULT_PATH`; do not hardcode it in Python.
- Keep `fail-history` OpenSearch as the evidence source and keep `product + cause_oper + fail_type` as the Concept key.
- Keep `bootstrap_wiki_warmup.py` and the question-driven `fail_history_tools.py` flow.
- Do not modify `08-YieldAgent/yield_frontend/`, `08-YieldAgent/wiki_frontend/`, `08-YieldAgent/repl_agent/frontend/`, `08-YieldAgent/pages/`, or UI code in `08-YieldAgent/app.py`.
- Preserve existing Wiki API paths and response shapes.
- Do not add local embedding, LanceDB, `kb.json`, Tauri, or content-only index enrichment.
- Do not add keyword, regex, phrase-list, or special-case natural-language metadata inference.
- Preserve the current single-writer and atomic `.tmp` → `os.replace` Markdown write behavior.
- Do not delete the repository Vault during migration.
- The pre-existing full-suite collection failures from `motor`/`pymongo` and `statsmodels`/pandas are recorded but are not part of this plan.

## Plan Boundary

This plan implements design milestone M1 only: shared external Vault configuration, migration, API compatibility, and end-to-end verification. Obsidian properties/MOCs, incremental fingerprints, persistent jobs, the Obsidian Plugin, graph expansion, and new ingestion adapters each require a separate implementation plan after M1 passes.

## File Map

- Create `08-YieldAgent/wiki_config.py`: resolve, initialize, and validate the canonical Vault layout.
- Create `08-YieldAgent/tests/wiki/conftest.py`: mark Wiki tests as server-independent and isolate import state.
- Create `08-YieldAgent/tests/wiki/test_wiki_config.py`: shared path and fail-fast tests.
- Create `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`: writer path integration tests.
- Create `08-YieldAgent/tests/wiki/test_wiki_router_external_vault.py`: reader/API path integration tests.
- Create `08-YieldAgent/migrate_wiki_vault.py`: non-destructive source-to-target Vault copier and verifier.
- Create `08-YieldAgent/tests/wiki/test_migrate_wiki_vault.py`: dry-run, copy, checksum, and conflict tests.
- Modify `08-YieldAgent/wiki_store.py`: consume shared paths while preserving existing private path aliases.
- Modify `08-YieldAgent/wiki_router.py`: read the same shared Vault as the writer.
- Modify `08-YieldAgent/bootstrap_wiki_warmup.py`: use the shared Vault root.
- Modify `08-YieldAgent/wiki_lint.py`: use the shared Vault root as CLI default.
- Modify `08-YieldAgent/migrate_v2_to_v3.py`: use the shared Vault root as CLI default.
- Modify `08-YieldAgent/agent_server.py`: validate and initialize the Vault before starting Wiki workers.
- Modify `08-YieldAgent/docs/wiki-deployment-procedure.md`: document the external Vault setup, migration, and rollback.

---

### Task 1: Canonical Wiki Vault Configuration

**Files:**
- Create: `08-YieldAgent/wiki_config.py`
- Create: `08-YieldAgent/tests/wiki/conftest.py`
- Create: `08-YieldAgent/tests/wiki/test_wiki_config.py`

**Interfaces:**
- Produces: `WikiPaths`, `WikiConfigurationError`, `resolve_wiki_paths()`, `initialize_wiki_vault()`, `validate_wiki_vault()`.
- Consumes: `WIKI_VAULT_PATH` and `WIKI_REQUIRE_EXTERNAL_VAULT` environment values.

- [ ] **Step 1: Create the Wiki test package fixture**

```python
# 08-YieldAgent/tests/wiki/conftest.py
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def pytest_configure(config):
    config.addinivalue_line("markers", "no_server: test does not require agent_server")


@pytest.fixture(autouse=True)
def clear_wiki_modules():
    yield
    for name in ("wiki_config", "wiki_store", "wiki_router"):
        sys.modules.pop(name, None)
```

- [ ] **Step 2: Write failing configuration tests**

```python
# 08-YieldAgent/tests/wiki/test_wiki_config.py
from pathlib import Path

import pytest

from wiki_config import (
    WikiConfigurationError,
    initialize_wiki_vault,
    resolve_wiki_paths,
    validate_wiki_vault,
)


pytestmark = pytest.mark.no_server


def test_resolve_paths_from_explicit_environment(tmp_path):
    root = tmp_path / "YieldWiki"
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(root)})
    assert paths.root == root.resolve()
    assert paths.concepts == root.resolve() / "concepts"
    assert paths.sources == root.resolve() / "sources"
    assert paths.reviews == root.resolve() / "reviews"
    assert paths.configured is True


def test_require_external_rejects_missing_path(tmp_path):
    with pytest.raises(WikiConfigurationError, match="WIKI_VAULT_PATH"):
        resolve_wiki_paths(
            {"WIKI_REQUIRE_EXTERNAL_VAULT": "true"},
            default_root=tmp_path / "repo-wiki",
        )


def test_initialize_creates_complete_m1_layout(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    expected = (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    )
    assert all(path.is_dir() for path in expected)
    assert paths.index.read_text(encoding="utf-8") == "# Wiki Index\n\n"
    assert paths.log.read_text(encoding="utf-8") == "# Wiki Operation Log\n\n"


def test_validate_reports_unwritable_vault(tmp_path, monkeypatch):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)

    def fail_write_text(self, *args, **kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(Path, "write_text", fail_write_text)
    with pytest.raises(WikiConfigurationError, match="not writable"):
        validate_wiki_vault(paths)
```

- [ ] **Step 3: Run the tests and confirm the missing module failure**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_config.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'wiki_config'`.

- [ ] **Step 4: Implement the canonical path module**

```python
# 08-YieldAgent/wiki_config.py
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class WikiConfigurationError(RuntimeError):
    """Raised when the configured Wiki Vault cannot be used safely."""


@dataclass(frozen=True)
class WikiPaths:
    root: Path
    configured: bool
    episodes: Path
    concepts: Path
    aliases: Path
    super_concepts: Path
    sources: Path
    reviews: Path
    attachments: Path
    lint_logs: Path
    state_dir: Path
    log: Path
    index: Path
    manifest: Path


def _enabled(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes"}


def resolve_wiki_paths(
    env: Mapping[str, str] | None = None,
    *,
    default_root: Path | None = None,
) -> WikiPaths:
    values = os.environ if env is None else env
    configured_value = (values.get("WIKI_VAULT_PATH") or "").strip()
    configured = bool(configured_value)
    if _enabled(values.get("WIKI_REQUIRE_EXTERNAL_VAULT")) and not configured:
        raise WikiConfigurationError(
            "WIKI_VAULT_PATH is required when WIKI_REQUIRE_EXTERNAL_VAULT=true"
        )
    fallback = default_root or (Path(__file__).resolve().parent / "wiki")
    root = Path(configured_value).expanduser() if configured else fallback
    root = root.resolve()
    state_dir = root / ".yield-wiki"
    return WikiPaths(
        root=root,
        configured=configured,
        episodes=root / "episodes",
        concepts=root / "concepts",
        aliases=root / "aliases",
        super_concepts=root / "super_concepts",
        sources=root / "sources",
        reviews=root / "reviews",
        attachments=root / "attachments",
        lint_logs=root / "lint_logs",
        state_dir=state_dir,
        log=root / "log.md",
        index=root / "index.md",
        manifest=state_dir / "manifest.json",
    )


def initialize_wiki_vault(paths: WikiPaths) -> None:
    for directory in (
        paths.episodes,
        paths.concepts,
        paths.aliases,
        paths.super_concepts,
        paths.sources,
        paths.reviews,
        paths.attachments,
        paths.lint_logs,
        paths.state_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if not paths.log.exists():
        paths.log.write_text("# Wiki Operation Log\n\n", encoding="utf-8")
    if not paths.index.exists():
        paths.index.write_text("# Wiki Index\n\n", encoding="utf-8")


def validate_wiki_vault(paths: WikiPaths) -> None:
    probe = paths.state_dir / f".write-probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise WikiConfigurationError(f"Wiki Vault is not writable: {paths.root}") from exc
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_config.py
```

Expected: `4 passed`.

- [ ] **Step 6: Commit the configuration boundary**

```bash
git add 08-YieldAgent/wiki_config.py 08-YieldAgent/tests/wiki/conftest.py 08-YieldAgent/tests/wiki/test_wiki_config.py
git commit -m "feat(wiki): centralize vault configuration"
```

### Task 2: Route All Wiki Writers and CLIs Through the Shared Vault

**Files:**
- Create: `08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py`
- Modify: `08-YieldAgent/wiki_store.py:7-26,84-90`
- Modify: `08-YieldAgent/bootstrap_wiki_warmup.py:39-45`
- Modify: `08-YieldAgent/wiki_lint.py:170-180`
- Modify: `08-YieldAgent/migrate_v2_to_v3.py:87-95`

**Interfaces:**
- Consumes: `resolve_wiki_paths()` and `initialize_wiki_vault()` from Task 1.
- Produces: all existing Wiki write paths and CLI defaults resolve to the same `WikiPaths.root`.
- Preserves: `wiki_store._VAULT`, `_EPISODES`, `_CONCEPTS`, `_ALIASES`, `_SUPER_CONCEPTS`, `_LOG`, and `_INDEX` for current callers.

- [ ] **Step 1: Write a failing subprocess integration test**

```python
# 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_server


def test_store_writes_only_to_explicit_vault(tmp_path):
    vault = tmp_path / "YieldWiki"
    script = """
import json
import wiki_store

eid, status = wiki_store.upsert_episode({
    "query": "4SS EASY 이력",
    "filters": {"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN"},
    "doc_ids": ["FH-1"],
    "body": "## 근거\\n\\n검증 본문",
    "summary": "검증",
})
print(json.dumps({"root": str(wiki_store._VAULT), "eid": eid, "status": status}))
"""
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["root"] == str(vault.resolve())
    assert result["status"] == "created"
    assert (vault / "episodes" / f"{result['eid']}.md").exists()
    assert (vault / "sources").is_dir()
    assert (vault / "reviews").is_dir()
```

- [ ] **Step 2: Run the test and verify it exposes missing shared-path adoption**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_store_external_vault.py
```

Expected: failure because the current `wiki_store._ensure_dirs()` does not create `sources/` or `reviews/` through the shared layout.

- [ ] **Step 3: Replace `wiki_store.py` path construction while preserving aliases**

```python
from wiki_config import initialize_wiki_vault, resolve_wiki_paths

_PATHS = resolve_wiki_paths()
_VAULT = _PATHS.root
_EPISODES = _PATHS.episodes
_CONCEPTS = _PATHS.concepts
_ALIASES = _PATHS.aliases
_SUPER_CONCEPTS = _PATHS.super_concepts
_LOG = _PATHS.log
_INDEX = _PATHS.index


def _ensure_dirs() -> None:
    initialize_wiki_vault(_PATHS)
```

Remove the old `Path(os.getenv(...))` block but leave unrelated environment settings untouched.

- [ ] **Step 4: Make CLI defaults use the canonical root and extend the test**

Use these exact imports/defaults:

```python
# bootstrap_wiki_warmup.py
from wiki_config import resolve_wiki_paths
_VAULT_PATH = resolve_wiki_paths().root

# wiki_lint.py and migrate_v2_to_v3.py
from wiki_config import resolve_wiki_paths
default_vault = str(resolve_wiki_paths().root)
```

Extend the subprocess script with:

```python
import bootstrap_wiki_warmup
import wiki_config

assert bootstrap_wiki_warmup._VAULT_PATH == wiki_store._VAULT
assert wiki_config.resolve_wiki_paths().root == wiki_store._VAULT
```

- [ ] **Step 5: Run writer and configuration tests**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_config.py tests/wiki/test_wiki_store_external_vault.py
```

Expected: `5 passed`.

- [ ] **Step 6: Verify CLI defaults without making writes**

Run:

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH="$(mktemp -d)/YieldWiki" python bootstrap_wiki_warmup.py --dry-run --top 1 --min-docs 2
```

Expected: the first output line starts with the temporary `vault:` path and the command ends with `--dry-run — 실 실행 X`.

- [ ] **Step 7: Commit shared writer adoption**

```bash
git add 08-YieldAgent/wiki_store.py 08-YieldAgent/bootstrap_wiki_warmup.py 08-YieldAgent/wiki_lint.py 08-YieldAgent/migrate_v2_to_v3.py 08-YieldAgent/tests/wiki/test_wiki_store_external_vault.py
git commit -m "refactor(wiki): use canonical vault paths"
```

### Task 3: Make Wiki API Read the External Vault

**Files:**
- Create: `08-YieldAgent/tests/wiki/test_wiki_router_external_vault.py`
- Modify: `08-YieldAgent/wiki_router.py:8-25`

**Interfaces:**
- Consumes: `resolve_wiki_paths().root` from Task 1.
- Produces: `_scan_nodes()` and all four existing endpoints read from the configured root.
- Preserves: `GET /api/wiki/graph`, `GET /api/wiki/trip-docs`, `GET /api/wiki/doc/{doc_id}`, and `GET /api/wiki/node/{node_id:path}` response contracts.

- [ ] **Step 1: Write a failing API path test**

```python
# 08-YieldAgent/tests/wiki/test_wiki_router_external_vault.py
import importlib

import frontmatter
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = pytest.mark.no_server


def test_node_endpoint_reads_explicit_external_vault(tmp_path, monkeypatch):
    vault = tmp_path / "YieldWiki"
    concepts = vault / "concepts"
    concepts.mkdir(parents=True)
    post = frontmatter.Post(
        content="## 외부 Vault 본문",
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        updated="2026-07-31T00:00:00",
    )
    (concepts / "4SS_PRE_METAL_CLN_EASY.md").write_text(
        frontmatter.dumps(post), encoding="utf-8"
    )
    monkeypatch.setenv("WIKI_VAULT_PATH", str(vault))

    import wiki_router
    wiki_router = importlib.reload(wiki_router)
    app = FastAPI()
    app.include_router(wiki_router.router, prefix="/api/wiki")
    response = TestClient(app).get(
        "/api/wiki/node/concept:4SS%7CPRE%20METAL%20CLN%7CEASY"
    )
    assert response.status_code == 200
    assert response.json()["body_markdown"] == "## 외부 Vault 본문"
    assert wiki_router._VAULT == vault.resolve()
```

- [ ] **Step 2: Run the test and confirm it fails against the hardcoded repository Vault**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_router_external_vault.py
```

Expected: `404` or an assertion showing `_VAULT` is `08-YieldAgent/wiki`.

- [ ] **Step 3: Replace the router path constant**

```python
from wiki_config import resolve_wiki_paths

_VAULT = resolve_wiki_paths().root
```

Remove the now-unused `Path` import. Do not change endpoint decorators, query parameters, graph shapes, labels, or response keys.

- [ ] **Step 4: Run all Wiki path tests**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki
```

Expected: all tests pass.

- [ ] **Step 5: Commit reader alignment**

```bash
git add 08-YieldAgent/wiki_router.py 08-YieldAgent/tests/wiki/test_wiki_router_external_vault.py
git commit -m "fix(wiki): read configured external vault"
```

### Task 4: Fail Fast Before Wiki Workers Start

**Files:**
- Modify: `08-YieldAgent/tests/wiki/test_wiki_config.py`
- Modify: `08-YieldAgent/agent_server.py:144-157`

**Interfaces:**
- Consumes: `resolve_wiki_paths()`, `initialize_wiki_vault()`, and `validate_wiki_vault()`.
- Produces: a prepared writable Vault before `wiki_queue.start()`.

- [ ] **Step 1: Add a validation-cleanup regression test**

```python
def test_validate_removes_write_probe(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    validate_wiki_vault(paths)
    assert list(paths.state_dir.glob(".write-probe-*")) == []


def test_agent_server_prepares_vault_before_queue_start():
    source = (Path(__file__).resolve().parents[2] / "agent_server.py").read_text(
        encoding="utf-8"
    )
    lifespan = source.split("async def lifespan", 1)[1]
    assert lifespan.index("initialize_wiki_vault") < lifespan.index(
        "await wiki_queue.start()"
    )
    assert lifespan.index("validate_wiki_vault") < lifespan.index(
        "await wiki_queue.start()"
    )
```

- [ ] **Step 2: Run the test before touching server startup**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_wiki_config.py::test_validate_removes_write_probe tests/wiki/test_wiki_config.py::test_agent_server_prepares_vault_before_queue_start
```

Expected: the probe cleanup test passes and the server-order test fails because `agent_server.py` does not prepare the Vault yet.

- [ ] **Step 3: Prepare the Vault at the first line of FastAPI lifespan**

Add this import:

```python
from wiki_config import initialize_wiki_vault, resolve_wiki_paths, validate_wiki_vault
```

Add these lines before creating `AsyncIOMotorClient`:

```python
wiki_paths = resolve_wiki_paths()
initialize_wiki_vault(wiki_paths)
validate_wiki_vault(wiki_paths)
app.state.wiki_vault_path = wiki_paths.root
logger.info("Wiki Vault ready: %s", wiki_paths.root)
```

Because `resolve_wiki_paths()` enforces `WIKI_REQUIRE_EXTERNAL_VAULT=true`, a production process without `WIKI_VAULT_PATH` exits before MongoDB, queue, or graph initialization.

- [ ] **Step 4: Verify syntax and focused tests**

Run:

```bash
cd 08-YieldAgent
python -m py_compile wiki_config.py agent_server.py
python -m pytest -q tests/wiki
```

Expected: compilation succeeds and all Wiki tests pass. Do not use the unrelated full-suite collection result as the M1 gate.

- [ ] **Step 5: Commit startup validation**

```bash
git add 08-YieldAgent/agent_server.py 08-YieldAgent/tests/wiki/test_wiki_config.py
git commit -m "feat(wiki): validate vault before startup"
```

### Task 5: Non-Destructive Vault Migration

**Files:**
- Create: `08-YieldAgent/migrate_wiki_vault.py`
- Create: `08-YieldAgent/tests/wiki/test_migrate_wiki_vault.py`

**Interfaces:**
- Consumes: source and target filesystem paths supplied by an operator.
- Produces: `MigrationReport`, `sha256_file()`, `plan_migration()`, and `migrate_vault()`.
- CLI: `python migrate_wiki_vault.py --source <path> --target <path> --dry-run|--apply`.
- Guarantee: never deletes the source and never overwrites a different target file.

- [ ] **Step 1: Write migration behavior tests**

```python
# 08-YieldAgent/tests/wiki/test_migrate_wiki_vault.py
from pathlib import Path

import pytest

from migrate_wiki_vault import migrate_vault, sha256_file


pytestmark = pytest.mark.no_server


def _source_vault(root: Path) -> Path:
    source = root / "source"
    (source / "concepts").mkdir(parents=True)
    (source / "concepts" / "one.md").write_text("---\nid: concept:one\n---\nbody\n", encoding="utf-8")
    (source / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    return source


def test_dry_run_does_not_create_target(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"
    report = migrate_vault(source, target, apply=False)
    assert report.planned == 2
    assert not target.exists()


def test_apply_copies_and_verifies_checksums(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"
    report = migrate_vault(source, target, apply=True)
    assert report.copied == 2
    assert sha256_file(source / "concepts" / "one.md") == sha256_file(
        target / "concepts" / "one.md"
    )


def test_apply_refuses_different_existing_target(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"
    (target / "concepts").mkdir(parents=True)
    (target / "concepts" / "one.md").write_text("different", encoding="utf-8")
    with pytest.raises(FileExistsError, match="different target file"):
        migrate_vault(source, target, apply=True)


def test_temporary_files_are_not_migrated(tmp_path):
    source = _source_vault(tmp_path)
    (source / "concepts" / "partial.md.tmp").write_text("partial", encoding="utf-8")
    target = tmp_path / "target"
    migrate_vault(source, target, apply=True)
    assert not (target / "concepts" / "partial.md.tmp").exists()
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_migrate_wiki_vault.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'migrate_wiki_vault'`.

- [ ] **Step 3: Implement the migration core**

```python
# Core of 08-YieldAgent/migrate_wiki_vault.py
from __future__ import annotations

import argparse
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationReport:
    planned: int
    copied: int
    identical: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_migration(source: Path) -> list[Path]:
    return sorted(
        path for path in source.rglob("*")
        if path.is_file() and not path.name.endswith(".tmp")
    )


def migrate_vault(source: Path, target: Path, *, apply: bool) -> MigrationReport:
    source = source.resolve()
    target = target.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source Vault not found: {source}")
    files = plan_migration(source)
    if not apply:
        return MigrationReport(planned=len(files), copied=0, identical=0)
    copied = 0
    identical = 0
    for source_file in files:
        relative = source_file.relative_to(source)
        target_file = target / relative
        if target_file.exists():
            if sha256_file(source_file) != sha256_file(target_file):
                raise FileExistsError(f"different target file: {target_file}")
            identical += 1
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        if sha256_file(source_file) != sha256_file(target_file):
            raise OSError(f"checksum mismatch: {target_file}")
        copied += 1
    return MigrationReport(planned=len(files), copied=copied, identical=identical)
```

Add an `argparse` `main()` that requires exactly one of `--dry-run` and `--apply`, prints the source, target, and report counts, and returns nonzero when a conflict or checksum error is raised.

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="copy and verify a Wiki Vault")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate_vault(args.source, args.target, apply=args.apply)
    except (FileNotFoundError, FileExistsError, OSError) as exc:
        print(f"migration failed: {exc}")
        return 1
    print(f"source={args.source.resolve()}")
    print(f"target={args.target.resolve()}")
    print(
        f"planned={report.planned} copied={report.copied} "
        f"identical={report.identical}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run migration tests**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki/test_migrate_wiki_vault.py
```

Expected: `4 passed`.

- [ ] **Step 5: Exercise CLI dry-run against the repository Vault**

Run:

```bash
cd 08-YieldAgent
python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --dry-run
```

Expected: report shows planned files and `/Users/daehwankim/SYLDAIX/YieldWiki` is not created by dry-run.

- [ ] **Step 6: Commit migration tooling**

```bash
git add 08-YieldAgent/migrate_wiki_vault.py 08-YieldAgent/tests/wiki/test_migrate_wiki_vault.py
git commit -m "feat(wiki): add safe vault migration"
```

### Task 6: Deployment Runbook and Real End-to-End Verification

**Files:**
- Modify: `08-YieldAgent/docs/wiki-deployment-procedure.md`
- Test: `08-YieldAgent/tests/wiki/`

**Interfaces:**
- Consumes: migration CLI and common Vault configuration from Tasks 1–5.
- Produces: repeatable migration, startup, rollback, and E2E commands for operators.

- [ ] **Step 1: Add the exact local/enterprise Vault configuration to the runbook**

Add this configuration block:

```bash
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki
WIKI_REQUIRE_EXTERNAL_VAULT=true
```

Document that a production server uses its own mounted absolute path, not the workstation path, while keeping the same environment variable name.

- [ ] **Step 2: Add non-destructive migration commands**

```bash
cd 08-YieldAgent
python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --dry-run

python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --apply
```

State that rollback means stopping the server and restoring the prior `WIKI_VAULT_PATH`; no source files are deleted by the migration.

- [ ] **Step 3: Run all isolated M1 tests**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki
```

Expected: all M1 Wiki tests pass with zero failures.

- [ ] **Step 4: Run lint and syntax verification**

Run:

```bash
cd 08-YieldAgent
python -m py_compile wiki_config.py wiki_store.py wiki_router.py bootstrap_wiki_warmup.py wiki_lint.py migrate_v2_to_v3.py migrate_wiki_vault.py agent_server.py
git diff --check
```

Expected: both commands exit `0`.

- [ ] **Step 5: Apply the real migration**

Run:

```bash
cd 08-YieldAgent
python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --apply
```

Expected: copied/identical counts sum to the planned count, with no conflicts or checksum failures.

- [ ] **Step 6: Run a real OpenSearch → LLM → external Vault synthesis**

Run:

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
python bootstrap_wiki_warmup.py \
  --apply \
  --top 1 \
  --min-docs 2 \
  --max-docs 5
```

Expected:

- OpenSearch `fail-history` aggregation returns at least one triple.
- The configured real LLM returns one structured Concept synthesis.
- A Markdown Concept is created or updated under `/Users/daehwankim/SYLDAIX/YieldWiki/concepts/`.
- The command finishes with lint output and no `save_fail`.

- [ ] **Step 7: Verify the same external note through the Wiki API router**

The current global environment cannot import the full `agent_server` because of the recorded `motor`/`pymongo` incompatibility. Start a minimal FastAPI process that mounts the production `wiki_router` unchanged:

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
python -c 'from fastapi import FastAPI; import uvicorn; from wiki_router import router; app=FastAPI(); app.include_router(router, prefix="/api/wiki"); uvicorn.run(app, host="127.0.0.1", port=8001)'
```

In a second terminal, request the graph:

```bash
curl --fail 'http://127.0.0.1:8001/api/wiki/graph?view=product_tree&limit=20'
```

Expected: HTTP `200` and graph JSON containing at least one `has_wiki: true` Concept from the external Vault. Full `agent_server` startup remains a separately recorded baseline dependency limitation; no dependency versions are changed in M1.

- [ ] **Step 8: Confirm existing frontend source files were untouched**

Run:

```bash
git diff main...HEAD --name-only | rg '^(08-YieldAgent/(yield_frontend|wiki_frontend|repl_agent/frontend|pages)/|08-YieldAgent/app.py$)'
```

Expected: no output.

- [ ] **Step 9: Commit the runbook and final M1 verification notes**

```bash
git add 08-YieldAgent/docs/wiki-deployment-procedure.md
git commit -m "docs(wiki): document external vault operations"
```

### Task 7: M1 Completion Audit

**Files:**
- Review only: all files changed by Tasks 1–6

**Interfaces:**
- Produces: evidence that M1 is complete without expanding into M2–M7.

- [ ] **Step 1: Review the branch diff for surgical scope**

Run:

```bash
git diff --stat main...HEAD
git diff --check main...HEAD
git status --short
```

Expected: only M1 configuration, migration, tests, server hookup, and runbook files are changed; worktree status is clean.

- [ ] **Step 2: Re-run the complete M1 test gate**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki
```

Expected: all tests pass.

- [ ] **Step 3: Record the known unrelated baseline failures without modifying dependencies**

Run:

```bash
cd 08-YieldAgent
python -m pytest -q repl_agent/tests tests
```

Expected baseline limitation: collection still reports the previously observed `motor`/`pymongo` and `statsmodels`/pandas incompatibilities. Confirm the error signatures are unchanged; do not edit dependency versions in this branch.

- [ ] **Step 4: Record live evidence in the handoff**

The handoff must state:

```text
- external Vault path used
- migration planned/copied/identical counts
- real OpenSearch triple used
- real LLM synthesis status and confidence
- generated Concept path
- Wiki API HTTP status
- isolated test count
- unchanged baseline dependency failures
```

- [ ] **Step 5: Verify commits are focused**

Run:

```bash
git log --oneline main..HEAD
```

Expected: separate commits for configuration, writer paths, router path, startup validation, migration, and operations documentation.
