---
type: Runbook
page_id: runbooks/operating-curator
title: Operating the Curator
description: Defines safe curator operation and rollback.
routing_summary: Open when starting, stopping, validating, or recovering the curator.
status: reviewed
owner: yield-platform
source_status: reviewed
agent_use: read-and-propose
llmwiki_status: reviewed
llmwiki_owner: yield-platform
llmwiki_source_status: reviewed
llmwiki_agent_use: read-and-propose
sensitivity: internal
last_reviewed: 2026-07-21
review_cycle: P90D
version: 2
relations:
  related_to: ["[[governance/agent-write-policy]]", "[[governance/review-policy]]"]
evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]
---
# Operating the Curator

Run the validator before publishing. Disable collection and writes to roll back without changing request analysis.

## Rollout

```bash
# Stage 1: lint only
python control_knowledge_cli.py --root multiagent_knowledge lint

# Stage 2: shadow candidate collection
CONTROL_KNOWLEDGE_ENABLED=true
CONTROL_KNOWLEDGE_WRITER=false

# Stage 3: single writer with real curator
CONTROL_KNOWLEDGE_ENABLED=true
CONTROL_KNOWLEDGE_WRITER=true

# Immediate rollback: stop all collection/writes without changing analysis flow
CONTROL_KNOWLEDGE_ENABLED=false
CONTROL_KNOWLEDGE_WRITER=false
```

Only one server process may set `CONTROL_KNOWLEDGE_WRITER=true`.

## Inspection and approval

Inspect immutable evidence in `raw/candidates`, processing results in `raw/curation-ledger.jsonl`, published changes in `wiki/log.md`, and protected proposals in `wiki/review_queue`.

Approve a reviewed proposal by ID:

```bash
python control_knowledge_cli.py --root multiagent_knowledge approve <proposal_id>
```

## Registry drift recovery

Inspect drift observations and rerun the registry and compiled-page checks:

```bash
find multiagent_knowledge/wiki/observations -maxdepth 1 -name 'registry-drift-*.md' -print
uv run python -m pytest tests/test_control_knowledge_registry.py \
  tests/test_control_knowledge_validator.py -v
uv run python control_knowledge_cli.py --root multiagent_knowledge lint
```

An affected Agent page remains unchanged until the registry/code mismatch is corrected and a new valid snapshot is processed.

## Retry invalid decisions

`invalid_decision` and `failed` ledger entries are audit records, not terminal processing states. After correcting the curator model, prompt, or validation issue, retry pending candidates:

```bash
jq 'select(.action == "invalid_decision" or .action == "failed")' \
  multiagent_knowledge/raw/curation-ledger.jsonl
uv run python control_knowledge_cli.py --root multiagent_knowledge curate-once
```

A later successful entry with the same fingerprint becomes the current terminal result; the earlier failure remains in the append-only ledger.
