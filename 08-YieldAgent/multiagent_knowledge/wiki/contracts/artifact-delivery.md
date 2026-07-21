---
agent_use: read-and-propose
description: Artifact delivery state-channel snapshot.
evidence_refs:
- code:query_state.YieldQueryState
last_reviewed: '2026-07-21'
llmwiki_agent_use: read-and-propose
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_status: current
owner: yield-platform
page_id: contracts/artifact-delivery
relations: {}
review_cycle: P90D
routing_summary: Artifact delivery state-channel snapshot.
sensitivity: internal
source_status: code-backed
status: current
title: Artifact Delivery
type: Contract
version: 2
---

# Artifact Delivery

## Artifact Channels

- **fail_history_agent**: fail_history_artifacts
- **lot_history_agent**: lot_history_artifacts
- **map_agent**: map_artifacts
- **mining_agent**: mining_artifacts
- **ppt_export**: ppt_artifacts
- **relation_tree_agent**: relation_tree_artifacts
- **wads_agent**: wads_artifacts
- **yield_agent**: yield_artifacts

## Payload Boundary

Artifacts are exposed through state channels; the actual artifact payloads are not embedded in the control Wiki but are referenced via these channels.

## Producers and Consumers

- **Producers**: each agent listed in the Artifact Channels section produces the corresponding artifacts.
- **Consumers**: any downstream agents or processes that subscribe to the respective channel may consume the artifacts.

## Source Evidence

- code:query_state.YieldQueryState
