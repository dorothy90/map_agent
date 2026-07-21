---
agent_use: read-and-propose
description: Agent that builds WT response groups for a selected lot, parameter, and
  main operation.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/wt-resp-agent
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/result-envelope]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Agent that builds WT response groups for a selected lot, parameter,
  and main operation.
sensitivity: internal
source_status: code-backed
status: current
title: WT Response Agent
type: Agent
version: 2
---

# WT Response Agent

## Responsibility
Builds WT response groups for a selected lot, parameter, and main operation.

## Boundaries
- Does not select the main operation or run the subsequent mining analysis.

## Inputs
**Required slots:**
- cause_oper
- fail_type
- lotcd

## Outputs
- Result kind: report
- Output contract: [[contracts/result-envelope]]

## Workflow Position
- Predecessors: none
- Successors: replanner

## Tools and External Systems
- External system: Oracle

## HITL Contracts
- missing_param
- plan_review
- task_confirm

## Verified Failure Modes
- **required_input_missing**: Skips WT response analysis when a required input is absent. (Source: `wt_resp_agent.py:94`)

## Source Evidence
- Source module: `wt_resp_agent.py`
- Additional source: `canonical_request.py:45`

## Related Knowledge
- [[contracts/hitl-contracts]]
- [[contracts/result-envelope]]
- [[workflows/orchestration-graph]]
