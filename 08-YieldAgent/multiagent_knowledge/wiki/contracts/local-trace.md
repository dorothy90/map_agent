---
agent_use: read-and-propose
description: Local trace contract version snapshot.
evidence_refs:
- code:local_trace.TRACE_EVENT_FIELDS
- code:local_trace.TRACE_SCHEMA_VERSION
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: contracts/local-trace
relations: {}
review_cycle: P90D
routing_summary: Local trace contract version snapshot.
sensitivity: internal
source_status: code-backed
status: current
title: Local Trace Contract
type: Contract
version: 2
---

# Local Trace Contract

**Schema Version:** local-trace/v1

## Event Boundary

- agent_result_enveloped
- agent_started
- hitl_triggered
- memory_profile_injected
- normalization_applied
- planner_output
- reference_resolved
- result_consistency_warning
- result_pruned
- supervisor_dispatch
- task_builder_output
- task_completed
- task_failed
- user_turn_started
- validation_issue
- workflow_node

## Redaction Boundary

- answer
- api_key
- apikey
- authorization
- base64
- bytes
- content
- cookie
- data
- goal
- html
- image
- message
- passwd
- password
- payload
- prompt
- query
- raw
- rows
- secret
- sql
- summary
- table_result
- token
- value

## Fields

- event_id
- event_type
- payload
- result_id
- schema_version
- severity
- source
- task_id
- timestamp
- trace_id
- turn_id

## Source Evidence

- code:local_trace.TRACE_EVENT_FIELDS
