---
type: Component
page_id: architecture/state-and-data-flow
title: State and Data Flow
description: Defines boundaries between runtime state, evidence, candidates, and curated knowledge.
routing_summary: Open before changing state contracts, evidence collection, or knowledge flow.
status: current
owner: yield-platform
source_status: code-backed
agent_use: read-and-propose
llmwiki_status: current
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_agent_use: read-and-propose
sensitivity: internal
last_reviewed: 2026-07-21
review_cycle: P90D
version: 1
relations:
  related_to: ["[[architecture/system-overview]]"]
evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]
---
# State and Data Flow

Structured code snapshots and redacted runtime evidence become candidates. A single curator validates and writes ordinary pages or creates protected-page proposals.
