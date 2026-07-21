---
agent_use: read-and-propose
description: Agent that finds main-operation candidates for a lot and failure parameter,
  building relation trees.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/relation-tree-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Agent that finds main-operation candidates for a lot and failure
  parameter, building relation trees.
sensitivity: internal
source_status: code-backed
status: current
title: Relation Tree Agent
type: Agent
version: 2
---

# Relation Tree Agent

## Responsibility
Finds main-operation candidates for a lot and failure parameter.

## Boundaries
- Does not execute the downstream WT response analysis.

## Inputs
- Required slots: `fail_type`, `lotcd`
- Optional slots: `cause_oper`, `rt_groups`
- Artifact channels: `relation_tree_artifacts`

## Outputs
- Result kind: `report`
- Output contracts: `contracts/result-envelope`, `contracts/artifact-delivery`

## Workflow Position
- Predecessors: none
- Successors: `replanner`

## Tools and External Systems
- External systems: `Oracle`
- Tool modules: none

## HITL Contracts
- `missing_param`
- `plan_review`
- `task_confirm`
- `postwads_choice`

## Verified Failure Modes
- **lot_missing**: Skips relation analysis when no lot code is available. *(Source: `relation_tree_agent.py:170`)*

## Source Evidence
- `relation_tree_agent.py`
- `canonical_request.py:34`

## Related Knowledge
- `contracts/artifact-delivery`
- `contracts/hitl-contracts`
- `contracts/result-envelope`
- `workflows/orchestration-graph`
