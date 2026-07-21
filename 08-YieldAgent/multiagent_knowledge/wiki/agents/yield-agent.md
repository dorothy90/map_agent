---
agent_use: read-and-propose
description: Canonical definition of the yield_agent with its allowed slot keys.
evidence_refs:
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/yield-agent
relations: {}
review_cycle: P90D
routing_summary: Canonical definition of the yield_agent with its allowed slot keys.
sensitivity: internal
source_status: code-backed
status: current
title: Yield Agent
type: Agent
version: 1
---

# Yield Agent

**Canonical Agent**: `yield_agent`

**Allowed Slot Keys**:
- lotcd
- periods
- ref_date
- time_range
- unit

This agent is defined in the system snapshot and is the authoritative source for yield calculations.
