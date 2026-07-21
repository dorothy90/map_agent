---
agent_use: read-and-propose
description: Agent that retrieves and synthesizes failure‑history documents, exposing
  slot inputs and result outputs for downstream workflows.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/fail-history-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Agent that retrieves and synthesizes failure‑history documents, exposing
  slot inputs and result outputs for downstream workflows.
sensitivity: internal
source_status: code-backed
status: current
title: Fail History Agent
type: Agent
version: 2
---

# Fail History Agent

## Responsibility
Retrieves and synthesizes failure-history documents.

## Boundaries
- Does not query wafer maps or calculate yield metrics.

## Inputs
- **Allowed Slots**: cause_oper, dh_query, fail_groups, fail_type, lotcd
- **Required Slots**: none
- **Required Any Slots**: none

## Outputs
- **Result Kinds**: document, summary
- **Output Contracts**: [[contracts/result-envelope]], [[contracts/artifact-delivery]]

## Workflow Position
- **Predecessors**: none
- **Successors**: replanner

## Tools and External Systems
- **Tool Modules**: fail_history_tools
- **External Systems**: OpenSearch

## HITL Contracts
- plan_review
- postwads_choice

## Verified Failure Modes
- **search_failure**: Returns an error ResultEnvelope for a permanent search failure. (Source: fail_history_agent.py:312)

## Source Evidence
- fail_history_agent.py
- canonical_request.py:25

## Related Knowledge
- contracts/artifact-delivery
- contracts/hitl-contracts
- contracts/result-envelope
- workflows/orchestration-graph
