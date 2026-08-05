---
type: Architecture
title: State and Contracts
description: Graph state schema (YieldQueryState), canonical request normalization, agent slot rules, and the result envelope contract that agents attach to their AIMessages.
tags: [state, contracts, schema, canonical-request, result-envelope]
openwiki:
  roles: [architecture, domain]
  source_paths: [08-YieldAgent/query_state.py, 08-YieldAgent/canonical_request.py, 08-YieldAgent/result_contracts.py]
  symbols: [YieldQueryState, CanonicalRequestItem, CanonicalPlanResponse, AGENT_SLOT_RULES, build_result_envelope, attach_result_envelope, build_recent_result_index_entry]
  test_paths: [08-YieldAgent/tests/test_e2e_regression.py]
  invariants: ["_clean_slots drops any slot not in the agent allow-list before dispatch.", "Result envelope rows are capped at 50 and sanitized to remove payload-like strings.", "recent_results is capped at 10 entries; artifact bodies stay in *_artifacts state keys."]
  validation_commands: ["pytest tests/test_e2e_regression.py -v"]
---

# State and Contracts

The orchestration graph communicates through a shared `YieldQueryState` TypedDict, a canonical request schema that the planner emits, slot rules that govern what each agent accepts, and a result envelope contract that agents attach to their `AIMessage.additional_kwargs`.

## YieldQueryState

Defined in `08-YieldAgent/query_state.py`. Key fields:

| Field | Type | Purpose |
|---|---|---|
| `messages` | `list` (`add_messages` reducer) | Conversation history |
| `step_count` | `int` | Supervisor loop counter (reset per turn) |
| `task_plan` | `list[dict]` | Full task plan from planner |
| `pending_tasks` | `list[dict]` | Remaining tasks to dispatch |
| `current_task` | `dict` | Currently executing task |
| `past_steps` | `list` | Completed task outcomes (reset per turn) |
| `canonical_request` | `dict` | First canonical request (convenience) |
| `canonical_requests` | `list[dict]` | All canonical requests from planner |
| `recent_results` | `list[dict]` | Bounded recent-result index (max 10) |
| `yield_artifacts` | `list` (`operator.add`) | Yield HTML artifacts |
| `wads_artifacts` | `list` (`operator.add`) | WADS HTML artifacts |
| `map_artifacts` | `list` (`operator.add`) | Map HTML artifacts |
| `fail_history_artifacts` | `list` (`operator.add`) | Fail history HTML artifacts |
| `lot_history_artifacts` | `list` (`operator.add`) | Lot history HTML artifacts |
| `relation_tree_artifacts` | `list` (`operator.add`) | Relation tree HTML artifacts |
| `mining_artifacts` | `list` (`operator.add`) | Mining HTML artifacts |
| `ppt_artifacts` | `list` (`operator.add`) | PPTX artifacts |
| `anomaly_params` | `list` | Anomaly detection results |
| `response` | `str` | Set by replanner to end the turn (`should_end` → END) |
| `turn_id` | `str` | Per-turn trace ID |

Artifact lists use `operator.add` as their reducer within a turn but are **reset per turn** via `Overwrite([])` in `agent_server._chat_stream`. `past_steps` is also reset per turn.

## Canonical request schema

The planner emits a `CanonicalPlanResponse` containing a list of `CanonicalRequestItem` objects. Each item has:

- `intent: str` — normalized intent name.
- `agent: Literal["", "yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent", "relation_tree_agent", "mining_agent", "postwads_selector"]` — target agent.
- `slots: dict` — structured parameters matching the agent's slot schema.
- `goal: str` — Korean-language goal shown to the user.
- `answer: str` — direct answer when no tool execution is needed.
- `ambiguous_slots: list[dict]` — slots requiring user clarification (each with `slot`, `candidates`, `reason`).

### AGENT_SLOT_RULES

Defined in `canonical_request.py`. Each agent has an `allowed` set and a `required` tuple. `_clean_slots` drops any slot not in the allow-list before dispatch.

| Agent | Allowed slots | Required |
|---|---|---|
| `yield_agent` | lotcd, ref_date, unit, periods, time_range | lotcd |
| `wads_agent` | lotcd, wads_start_tm, wads_end_tm, fail_type, wads_category | — |
| `map_agent` | lot_ids, wf_ids, groupkey, map_type, map_oper, wf_mod, wf_rem, map_label, map_groups | map_oper + (lot_ids ∨ groupkey) |
| `fail_history_agent` | lotcd, fail_type, cause_oper, dh_query, fail_groups | — |
| `lot_history_agent` | lot_ids | lot_ids |
| `relation_tree_agent` | lotcd, fail_type, cause_oper, rt_groups | lotcd, fail_type |
| `mining_agent` | lotcd, fail_type, cause_oper, wads_category, group_good, group_bad, tech, user_id, rank_limit | — |
| `wt_resp_agent` | lotcd, fail_type, cause_oper | all three |
| `ppt_export` | (none) | — |
| `postwads_selector` | requested_fail_type | — |

`build_tasks_from_canonical_requests` converts canonical requests into `task_plan` entries (`{task_id, agent, params, goal}`).

### TimeRange

The planner emits `time_range` as a `TimeRange` Pydantic model (`unit: weekly|monthly|daily`, `start`, `end`) using natural labels (`"2026-W17"` for ISO weeks, `"2026-02"` for months, `"2026-05-06"` for days). The supervisor's `resolve_time_range` converts these to `ref_date` (YYYYMMDD), `periods`, and `unit` before dispatch. This prevents the LLM from doing date arithmetic that leads to silent-wrong results.

## Result envelope contract

Defined in `08-YieldAgent/result_contracts.py` (schema `result-envelope/v1`). Agents attach a `ResultEnvelope` to their returned `AIMessage.additional_kwargs["result"]` via `attach_result_envelope`. The envelope is a **payload-free index**:

- `rows` — sanitized, max 50 rows. Payload-like strings (HTML, base64, `data:` URIs) are stripped; strings truncated to 500 chars; NaN/Infinity dropped.
- `artifact_refs` — references to artifact bodies (no payloads).
- `entities`, `summary`, `followups`, `extensions` — structured metadata.

The envelope is checkpoint-safe and API-serializable. Full artifact bodies stay in the `*_artifacts` state keys. The envelope is consumed by the supervisor for routing, `recent_results` for context, and chained fan-out.

### Recent result index

`build_recent_result_index_entry` creates payload-free entries for `state["recent_results"]` (max 10 entries, max 50 rows each). The planner and replanner consume these via `_recent_results_prompt_context` to resolve references. `prune_recent_results` enforces the cap.

## When to consult this page

- Adding or changing a state field.
- Adding a new agent slot or changing required slots.
- Changing the result envelope schema or sanitization rules.

## Change recipe: adding a new agent slot

1. Add the slot name to the agent's `allowed` set in `AGENT_SLOT_RULES` (`canonical_request.py`).
2. If required, add it to the `required` tuple or `required_any` groups.
3. Ensure the agent node reads it from `state["current_task"]["params"]`.
4. Update the planner prompt if the slot should be LLM-derivable.
5. Add an E2E regression case verifying the slot lands correctly.

## Validation

```bash
pytest tests/test_e2e_regression.py -v
```
