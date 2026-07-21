---
type: Component
page_id: architecture/system-overview
title: Multi-Agent System Overview
description: Defines the top-level multi-agent control-plane components.
routing_summary: Open when locating an agent, workflow, contract, or curator boundary.
status: current
owner: yield-platform
source_status: code-backed
agent_use: read-and-propose
llmwiki_status: current
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_agent_use: read-and-propose
sensitivity: internal
last_reviewed: 2026-07-21
review_cycle: P90D
version: 1
relations: {}
evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]
---
# Multi-Agent System Overview

The runtime graph routes requests through supervisor-controlled workers. This bundle documents only control-plane structure and behavior.
