# M4 Final Security Fixes Report

Base: `ab762e1`

## Changes

1. Split the public and private chat contracts. Public `ChatRequest` exposes only the existing client fields and rejects extra input, including `wiki_context`, with HTTP 422. The authenticated Plugin adapter resolves the current Vault note and constructs a typed `InternalChatRequest`; only the private `plugin_chat_stream` handler accepts it. The planner labels Vault content as untrusted evidence.
2. Prevented Wiki note bodies from entering default local or remote planner diagnostics. Plugin-context planner calls disable input-capturing callbacks, including the empty-plan retry. Local `planner.input` keeps only note identifiers, a metadata allowlist, and body SHA-256/length. Langfuse automatic planner input capture is disabled.
3. Hardened Source resolution. A sanitized filename is returned only when its Markdown frontmatter has `type: source` and a `doc_id` exactly equal to the requested OpenSearch document ID.

No existing frontend files were changed. No secret values were added.

## TDD Evidence

- RED: public `wiki_context` injection returned 200; no internal envelope existed.
- RED: the raw Wiki sentinel reached default trace callbacks and lacked the untrusted-evidence boundary.
- RED: OpenSearch `doc_id: A/B` incorrectly linked to a Vault note whose `doc_id` was `A_B`.
- GREEN focused: `pytest -q tests/wiki/test_wiki_plugin_chat.py tests/wiki/test_wiki_plugin_search.py` — 21 passed.
- GREEN Wiki suite: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/wiki` — 180 passed.
- User memory: 9 passed.
- Confirm-edit under the documented process-local LangChain compatibility shim: 9 passed.
- Obsidian Plugin: 36 tests passed; production TypeScript/esbuild build passed (`main.js` 28.8 kB).
- Python compile and `git diff --check`: passed.

## Known Environment Concern

The exact unshimmed confirm-edit command still stops during collection because the installed `langchain` package lacks legacy `verbose`, `debug`, and `llm_cache` attributes expected by the installed `langchain_core`. This is the previously documented dependency mismatch; source and dependency versions were not changed by this security fix.
