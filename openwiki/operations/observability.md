---
type: Operations
title: Observability and User Memory
description: Local JSONL trace events (local_trace), Langfuse integration (lf_utils), and per-user long-term preference memory (user_memory) in MongoDB.
tags: [observability, tracing, langfuse, user-memory, mongodb]
openwiki:
  roles: [operations, architecture]
  source_paths: [08-YieldAgent/local_trace.py, 08-YieldAgent/lf_utils.py, 08-YieldAgent/user_memory.py]
  symbols: [emit_trace_event, emit_runtime_detail, make_trace_id, new_turn_id, TRACE_SCHEMA_VERSION, lf_callbacks, get_profile, update_profile_from_feedback, MemoryUpdateResult, set_lf_capture_disabled]
  test_paths: [08-YieldAgent/tests/test_user_memory.py]
  invariants: ["local_trace is self-contained — no dependency on LangSmith, Langfuse, or external SaaS.", "User memory stores only qualitative preferences, never slot values (product codes/lot IDs/dates/parameter values).", "All user_memory public functions swallow exceptions — memory failure never kills the pipeline."]
  validation_commands: ["pytest tests/test_user_memory.py -q"]
---

# Observability and User Memory

## Local tracing

`local_trace.py` provides self-contained JSONL observability for supervisor multi-agent runs. It does not depend on LangSmith, Langfuse, or any external SaaS. It writes structured, redacted trace events to JSONL in development and stdout in production.

- **Schema**: `local-trace/v1`. Events are typed via `TRACE_EVENT_TYPES` (e.g. `user_turn_started`, `planner_output`, `task_builder_output`, `supervisor_dispatch`, `agent_started`, `agent_result_enveloped`, `task_completed`, `task_failed`, `hitl_triggered`, `memory_profile_injected`).
- **Context**: `TRACE_ID` and `TURN_ID` are `contextvars.ContextVar` for per-request isolation.
- **Redaction**: `_SENSITIVE_EXACT_KEYS` and `_MAX_SAFE_STRING_CHARS` / `_MAX_DICT_KEYS` / `_MAX_LIST_ITEMS` bound trace output. `summarize_trace_value`, `summarize_tasks`, `summarize_result_envelope` provide bounded summaries.
- **Key functions**: `emit_trace_event`, `emit_runtime_detail`, `make_trace_id`, `new_turn_id`, `set_trace_context`, `reset_trace_context`, `fingerprint_trace_value`, `preview_text`.

## Langfuse integration

`lf_utils.py` provides Langfuse callback integration:

- `lf_callbacks()` — returns Langfuse callbacks for `with_structured_output` and `_model.invoke` calls.
- `set_lf_capture_disabled(bool)` / `reset_lf_capture_disabled(token)` — temporarily disables Langfuse capture for privacy-sensitive turns (e.g. wiki-context turns). Used by the [agent server](agent-server.md) chat stream and the [wiki queue](../wiki/wiki-system.md).

## User memory

`user_memory.py` — per-`user_id` long-term preference memory in MongoDB (`user_profiles` collection, db `yield_agent`).

**Principle**: The profile stores **only qualitative preferences** — never slot values (product codes, lot IDs, dates, parameter values). Value derivation is the planner's job every turn. This avoids the "god-state" anti-pattern where stale slot values mislead future turns.

**Feedback events**: HITL touchpoints (task_confirm rejection, postwads_choice selection/non-selection, plan_review cancel/modify) generate feedback events via `make_feedback_event`. At turn end, `update_profile_from_feedback` makes a single LLM call to update the profile.

**Injection**: The planner reads `get_profile(user_id)` as structured context for the next turn, emitted as a `memory_profile_injected` trace event.

**Safety**: All public functions swallow exceptions — memory failure never kills the main pipeline. A 4000-char code-level failsafe caps profile length.

Key symbols: `get_profile`, `update_profile_from_feedback`, `make_feedback_event`, `MemoryUpdateResult`, `_collection` (lazy MongoClient singleton).

## When to consult this page

- Adding new trace event types.
- Changing Langfuse privacy/capture behavior.
- Modifying user memory feedback or injection.

## Validation

```bash
pytest tests/test_user_memory.py -q
```
