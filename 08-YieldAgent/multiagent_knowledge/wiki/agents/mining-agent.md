---
agent_use: read-and-propose
description: Canonical definition of the mining_agent, including its responsibilities,
  boundaries, inputs, outputs, workflow position, tools, HITL contracts, failure modes,
  and related knowledge.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/mining-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Canonical definition of the mining_agent, including its responsibilities,
  boundaries, inputs, outputs, workflow position, tools, HITL contracts, failure modes,
  and related knowledge.
sensitivity: internal
source_status: code-backed
status: current
title: Mining Agent
type: Agent
version: 2
---

# Mining Agent

## Responsibility
Runs GINI mining analysis over prepared good and bad lot groups.

## Boundaries
- Does not create the upstream WADS or relation-tree groups.

## Inputs
- Optional slots: cause_oper, fail_type, group_bad, group_good, lotcd, rank_limit, tech, user_id, wads_category
- Artifact channels: mining_artifacts

## Outputs
- Contracts: [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- Result kinds: table, summary

## Workflow Position
- Predecessors: none
- Successors: replanner

## Tools and External Systems
- External systems: Mining API, LLM
- Source module: mining_agent

## HITL Contracts
- plan_review
- postwads_choice

## Verified Failure Modes
- **analysis_failure**: Returns an error ResultEnvelope for a permanent mining failure. (Source: `mining_agent.py:524`)

## Source Evidence
- `mining_agent.py`
- `canonical_request.py:40`
- `mining_agent.py:524`

## Related Knowledge
- [[contracts/artifact-delivery]]
- [[contracts/hitl-contracts]]
- [[contracts/result-envelope]]
- [[workflows/orchestration-graph]]
