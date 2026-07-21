---
agent_use: read-and-propose
description: Canonical definition of the wads_agent, including its allowed slot keys
  for request routing.
evidence_refs:
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/wads-agent
relations: {}
review_cycle: P90D
routing_summary: Canonical definition of the wads_agent, including its allowed slot
  keys for request routing.
sensitivity: internal
source_status: code-backed
status: current
title: wads_agent
type: Agent
version: 1
---

# wads_agent

**Canonical Agent**: `wads_agent`

**Allowed Slot Keys**:
- `fail_type`
- `lotcd`
- `wads_category`
- `wads_end_tm`
- `wads_start_tm`

These slot keys define the parameters that the wads_agent can accept in canonical requests.
