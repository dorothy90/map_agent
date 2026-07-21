---
agent_use: read-and-propose
description: Canonical definition of the map_agent with responsibilities, boundaries,
  slot contracts, workflow position, tools, HITL contracts, failure modes, and evidence.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/map-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Canonical definition of the map_agent with responsibilities, boundaries,
  slot contracts, workflow position, tools, HITL contracts, failure modes, and evidence.
sensitivity: internal
source_status: code-backed
status: current
title: Map Agent
type: Agent
version: 2
---

# Map Agent

## Responsibility
Queries wafer map data and renders map or cumulative‑map artifacts.

## Boundaries
- Does not perform WADS degradation detection or lot‑history retrieval.

## Inputs
**Required slots**
- map_oper

**Optional slots**
- groupkey
- lot_ids
- map_groups
- map_label
- map_type
- wf_ids
- wf_mod
- wf_rem

**Required any of**
- lot_ids
- groupkey

**Allowed slots**
- groupkey
- lot_ids
- map_groups
- map_label
- map_oper
- map_type
- wf_ids
- wf_mod
- wf_rem

## Outputs
**Result kinds**
- image
- summary

**Output contracts**
- [[contracts/result-envelope]]
- [[contracts/artifact-delivery]]

## Workflow Position
- Predecessors: none
- Successors: replanner

## Tools and External Systems
- Oracle

## HITL Contracts
- missing_param
- plan_review
- postwads_choice

## Verified Failure Modes
- **render_empty**: Returns an empty summary when no map artifact is produced. *(Source: map_agent.py:1035)*

## Source Evidence
- map_agent.py
- canonical_request.py:20

## Related Knowledge
- [[contracts/artifact-delivery]]
- [[contracts/hitl-contracts]]
- [[contracts/result-envelope]]
- [[workflows/orchestration-graph]]
