---
agent_use: read-and-propose
description: Structured HITL identifier snapshot.
evidence_refs:
- code:models.ChatRequest.resume_value
- code:models.HITL_CONTRACT_IDS
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: contracts/hitl-contracts
relations: {}
review_cycle: P90D
routing_summary: Structured HITL identifier snapshot.
sensitivity: internal
source_status: code-backed
status: current
title: HITL Contracts
type: Contract
version: 2
---

# HITL Contracts

## Interrupt Types
- missing_param
- plan_review
- postwads_choice
- task_confirm

## Resume Contract
Resume Value Schema:
```json
{
  "anyOf": [
    {"type": "string"},
    {"additionalProperties": true, "type": "object"},
    {"type": "null"}
  ],
  "default": null,
  "title": "Resume Value"
}
```

## Applicable Agents
- missing_param: lot_history_agent, map_agent, relation_tree_agent, wt_resp_agent, yield_agent
- plan_review: fail_history_agent, lot_history_agent, map_agent, mining_agent, ppt_export, relation_tree_agent, wads_agent, wt_resp_agent, yield_agent
- postwads_choice: fail_history_agent, map_agent, mining_agent, relation_tree_agent, wads_agent
- task_confirm: relation_tree_agent, wads_agent, wt_resp_agent

## Source Evidence
- code:models.ChatRequest.resume_value
