---
agent_use: read-and-propose
description: Agent that queries lot yield data and produces result envelopes and visual
  artifacts.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/yield-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Agent that queries lot yield data and produces result envelopes and
  visual artifacts.
sensitivity: internal
source_status: code-backed
status: current
title: Yield Agent
type: Agent
version: 2
---

# Yield Agent

## Responsibility
Queries lot yield data and produces yield summaries and visual artifacts.

## Boundaries
- Does not retrieve WADS, map, or failure-history records.

## Inputs
**Required slots**: lotcd
**Optional slots**: periods, ref_date, time_range, unit
**Required any slots**: none

## Outputs
- Contracts: [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- Result kinds: table, summary

## Workflow Position
- Predecessors: none
- Successors: replanner

## Tools and External Systems
- Tools: yield_db, yield_viz
- External systems: Oracle, LLM

## HITL Contracts
- missing_param
- plan_review

## Verified Failure Modes
- **lot_data_empty**: Returns an empty ResultEnvelope when requested lot data is unavailable. (Source: yield_query_agent.py:212)

## Source Evidence
- yield_query_agent.py
- canonical_request.py:6
- yield_query_agent.py:212

## Related Knowledge
- [[contracts/artifact-delivery]]
- [[contracts/hitl-contracts]]
- [[contracts/result-envelope]]
- [[workflows/orchestration-graph]]
