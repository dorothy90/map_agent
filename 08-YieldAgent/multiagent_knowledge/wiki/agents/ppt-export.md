---
agent_use: read-and-propose
description: Agent that builds a PowerPoint file from accumulated analysis artifacts.
evidence_refs:
- code:control_knowledge_registry.AGENT_CONTROL_PROFILES
- code:canonical_request.AGENT_SLOT_RULES
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: agents/ppt-export
relations:
  participates_in:
  - '[[workflows/orchestration-graph]]'
  uses_contract:
  - '[[contracts/artifact-delivery]]'
  uses_hitl_contract:
  - '[[contracts/hitl-contracts]]'
review_cycle: P90D
routing_summary: Agent that builds a PowerPoint file from accumulated analysis artifacts.
sensitivity: internal
source_status: code-backed
status: current
title: PPT Export Agent
type: Agent
version: 2
---

# PPT Export Agent

## Responsibility
Builds a PowerPoint file from accumulated analysis artifacts.

## Boundaries
- Does not attach a ResultEnvelope or perform analytical queries.

## Inputs
None (the agent does not accept any slots).

## Outputs
- [[contracts/artifact-delivery]]

## Workflow Position
- Predecessors: none
- Successors: replanner

## Tools and External Systems
- External Systems: Local filesystem
- Tool Modules: none

## HITL Contracts
- plan_review

## Verified Failure Modes
- **generation_failure**: Returns no artifact when PowerPoint generation fails. (Source: ppt_export_agent.py:93)

## Source Evidence
- ppt_export_agent.py
- canonical_request.py:51
- ppt_export_agent.py:93

## Related Knowledge
- [[contracts/artifact-delivery]]
- [[contracts/hitl-contracts]]
- [[workflows/orchestration-graph]]
