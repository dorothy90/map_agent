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
version: 1
relations:
  related_to: ["[[governance/agent-write-policy]]", "[[governance/review-policy]]"]
evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]
---
# Operating the Curator

Run the validator before publishing. Disable collection and writes to roll back without changing request analysis.
