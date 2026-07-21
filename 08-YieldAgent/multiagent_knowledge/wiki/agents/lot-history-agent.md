---
agent_use: read-and-propose
description: Structured snapshot for canonical agent lot_history_agent.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/lot-history-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Structured snapshot for canonical agent lot_history_agent.
sensitivity: internal
source_status: code-backed
status: current
title: Lot History Agent
type: Agent
version: 2
---

# Lot History Agent

## Responsibility
Queries lot movement history and derives common-process insights.

## Boundaries
- Does not choose WADS parameters or render wafer maps.

## Inputs
- **Required slots**: `lot_ids`
- **Optional slots**: none

## Outputs
- **Result kinds**: table, summary
- **Output contracts**: `[[contracts/result-envelope]]`, `[[contracts/artifact-delivery]]`
- **Artifact channels**: `lot_history_artifacts`

## Workflow Position
- **Predecessors**: none
- **Successors**: `replanner`

## Tools and External Systems
- **Tool modules**: `lot_history_tools`
- **External systems**: Oracle

## HITL Contracts
- `missing_param`
- `plan_review`

## Verified Failure Modes
- **Code**: `query_failure`
  - **Effect**: Returns an error ResultEnvelope for a permanent lot-history failure.
  - **Source**: `lot_history_agent.py:819`

## Source Evidence
- `lot_history_agent.py`
- `canonical_request.py:30`

## Related Knowledge
- `[[contracts/artifact-delivery]]`
- `[[contracts/hitl-contracts]]`
- `[[contracts/result-envelope]]`
- `[[workflows/orchestration-graph]]`
