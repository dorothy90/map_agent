# One-command Wiki Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one executable file that enriches the live YieldWiki from `syld_gpt_2067627` without operator arguments.

**Architecture:** A strict Bash wrapper resolves its own directory and delegates all behavior to the existing `enrich_wiki.py` CLI. A subprocess test replaces `uv` through `PATH` and records the exact delegated arguments, so no OpenSearch, LLM, or Vault mutation occurs in the automated test.

**Tech Stack:** Bash, Python `subprocess`, pytest, existing `uv` CLI.

## Global Constraints

- Create only one production executable: `08-YieldAgent/enrich_extra_index.sh`.
- Use Vault `/Users/daehwankim/SYLDAIX/YieldWiki`.
- Use exact source index `syld_gpt_2067627`.
- Delegate retrieval, external LLM judgment, and safe Vault mutation to `enrich_wiki.py`.
- Do not create new Triple structure or write to OpenSearch.

---

### Task 1: Executable enrichment wrapper

**Files:**
- Create: `08-YieldAgent/enrich_extra_index.sh`
- Create: `08-YieldAgent/tests/wiki/test_enrich_extra_index_script.py`

**Interfaces:**
- Consumes: `enrich_wiki.py --apply --allow-external-llm --vault PATH --source-index NAME`.
- Produces: executable `./enrich_extra_index.sh` with the delegated CLI exit status.

- [ ] **Step 1: Write the failing subprocess test**

Create a temporary `uv` executable that writes its arguments to `$CAPTURE_PATH`. Execute
the repository wrapper with that directory first in `PATH`, then assert an exit code of
zero and this exact argument sequence:

```python
[
    "run", "--frozen", "python", str(script.parent / "enrich_wiki.py"),
    "--apply", "--allow-external-llm", "--vault",
    "/Users/daehwankim/SYLDAIX/YieldWiki",
    "--source-index", "syld_gpt_2067627",
]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run --frozen pytest -q tests/wiki/test_enrich_extra_index_script.py
```

Expected: failure because `enrich_extra_index.sh` does not exist.

- [ ] **Step 3: Add the minimal strict wrapper**

Create an executable Bash file with `set -euo pipefail`, resolve its directory from
`BASH_SOURCE[0]`, and `exec uv run --frozen python "$script_dir/enrich_wiki.py"` with
the fixed apply, Vault, and index arguments.

- [ ] **Step 4: Run focused tests and syntax checks**

Run:

```bash
uv run --frozen pytest -q tests/wiki/test_enrich_extra_index_script.py tests/wiki/test_enrich_wiki_cli.py
bash -n enrich_extra_index.sh
git diff --check
```

Expected: all tests pass, Bash syntax succeeds, and no whitespace errors are reported.

- [ ] **Step 5: Verify the real configured dependencies read-only**

Run:

```bash
uv run --frozen python enrich_wiki.py --check \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki \
  --source-index syld_gpt_2067627
```

Expected: JSON with `"status": "checked"` and no external LLM or Vault writes.

- [ ] **Step 6: Commit**

```bash
git add 08-YieldAgent/enrich_extra_index.sh \
  08-YieldAgent/tests/wiki/test_enrich_extra_index_script.py
git commit -m "feat(wiki): add one-command enrichment"
```
