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
version: 2
---

# Orchestration Graph

## Nodes and Edges

**Nodes**

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

**Edges**

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

## State and Result Flow

The workflow produces result and artifact contracts for each agent. Key fields used in follow‑up contracts include:

- agent, choice_kind, choice_message, choice_option_sets, choice_options, choice_target_agent, confirm, confirm_message, default_slots, goal, guard_agents, guard_key, prefilter_message, prefilter_options

**Output contracts by agent**

- fail_history_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- lot_history_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- map_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- mining_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- ppt_export → [[contracts/artifact-delivery]]
- relation_tree_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- wads_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]
- wt_resp_agent → [[contracts/result-envelope]]
- yield_agent → [[contracts/result-envelope]], [[contracts/artifact-delivery]]

**Artifact fields**

- fail_history_artifacts, lot_history_artifacts, map_artifacts, mining_artifacts, ppt_artifacts, relation_tree_artifacts, wads_artifacts, yield_artifacts

## Dynamic Handoffs

The central handoff node is **replanner**, which receives control flow from many upstream agents (fail_history_agent, lot_history_agent, map_agent, mining_agent, ppt_export, relation_tree_agent, wads_agent, wt_resp_agent, yield_agent) and redistributes execution based on runtime decisions. This pattern enables dynamic re‑planning and error recovery.

## Source Evidence

- code:supervisor.workflow
