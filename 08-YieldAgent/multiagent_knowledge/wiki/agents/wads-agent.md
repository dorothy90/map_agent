---
agent_use: read-and-propose
description: Canonical definition of the wads_agent, including its responsibility,
  boundaries, inputs, outputs, workflow position, tools, HITL contracts, failure modes,
  and source evidence.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/wads-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Canonical definition of the wads_agent, including its responsibility,
  boundaries, inputs, outputs, workflow position, tools, HITL contracts, failure modes,
  and source evidence.
sensitivity: internal
source_status: code-backed
status: current
title: WADS Agent
type: Agent
version: 2
---

# WADS Agent

## Responsibility
Retrieves and summarizes WADS degradation reports.

## Boundaries
- Does not render wafer maps or execute downstream failure analysis.

## Inputs
- **Slots**: `fail_type`, `lotcd`, `wads_category`, `wads_end_tm`, `wads_start_tm`

## Outputs
- **Result Kinds**: table, report, summary
- **Output Contracts**: `result-envelope`, `artifact-delivery`

## Workflow Position
- **Predecessors**: none
- **Successors**: replanner

## Tools and External Systems
- **Tool Modules**: `wads_tools`
- **External Systems**: Oracle, LLM

## HITL Contracts
- `plan_review`
- `task_confirm`
- `postwads_choice`

## Verified Failure Modes
- **Code**: `query_failure`
  - **Effect**: Returns an error ResultEnvelope for a non-transient WADS failure.
  - **Source**: `wads_agent.py:628`

## Source Evidence
- `wads_agent.py`
- `canonical_request.py:14`
- `wads_agent.py:628`

## Related Knowledge
- `contracts/artifact-delivery`
- `contracts/hitl-contracts`
- `contracts/result-envelope`
- `workflows/orchestration-graph`
