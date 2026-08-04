# One-command Wiki Enrichment Design

## Goal

Provide one executable repository file that enriches the live Obsidian Wiki from the
approved content-only OpenSearch index without requiring command-line arguments.

## Interface

The executable is `08-YieldAgent/enrich_extra_index.sh`. The operator runs:

```bash
./enrich_extra_index.sh
```

The script resolves `enrich_wiki.py` relative to its own location and invokes it with:

- apply mode and explicit external-LLM authorization;
- Vault `/Users/daehwankim/SYLDAIX/YieldWiki`;
- exact source index `syld_gpt_2067627`.

## Behavior and safety

The script uses strict shell error handling and returns the existing enrichment CLI's
exit status. It does not duplicate retrieval, LLM judgment, or Vault mutation logic.
OpenSearch remains read-only. Only LLM-approved related evidence is attached to existing
Triple Concepts; the script does not create new Triple structure.

## Verification

An automated test executes the wrapper with a fake `uv` binary and verifies the exact
arguments. A live read-only `enrich_wiki.py --check` validates the configured OpenSearch
mapping and Vault before completion.
