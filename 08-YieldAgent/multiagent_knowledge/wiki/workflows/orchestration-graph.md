---
agent_use: read-and-propose
description: Explicit LangGraph topology snapshot
evidence_refs:
- code:supervisor.workflow
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: workflows/orchestration-graph
relations: {}
review_cycle: P90D
routing_summary: Explicit LangGraph topology snapshot
sensitivity: internal
source_status: code-backed
status: current
title: Orchestration Graph
type: Workflow
version: 1
---

# Orchestration Graph

## Nodes

The workflow contains the following nodes:

- fail_history_agent
- lot_history_agent
- map_agent
- mining_agent
- plan_review
- planner
- ppt_export
- relation_tree_agent
- replanner
- supervisor
- task_normalizer_validator
- wads_agent
- wt_resp_agent
- yield_agent

## Edges

Directed edges between nodes define the execution order:

- __start__ → planner
- fail_history_agent → replanner
- lot_history_agent → replanner
- map_agent → replanner
- mining_agent → replanner
- planner → task_normalizer_validator
- ppt_export → replanner
- relation_tree_agent → replanner
- task_normalizer_validator → plan_review
- wads_agent → replanner
- wt_resp_agent → replanner
- yield_agent → replanner

## Follow‑up Fields

Fields used in follow‑up contracts:

- agent
- choice_kind
- choice_message
- choice_option_sets
- choice_options
- choice_target_agent
- confirm
- confirm_message
- default_slots
- goal
- guard_agents
- guard_key
- prefilter_message
- prefilter_options
