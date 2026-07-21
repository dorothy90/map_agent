---
type: Policy
page_id: governance/agent-write-policy
title: Agent Write Policy
description: Restricts knowledge writes to the curator and proposal path.
routing_summary: Open before any agent writes or proposes control knowledge.
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
  related_to: ["[[governance/ownership]]"]
evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]
---
# Agent Write Policy

Workers emit structured candidates and never write pages directly. The single curator writes validated ordinary pages and proposes protected changes.
