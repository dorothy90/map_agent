---
type: Architecture
title: HITL Contracts
description: Structured interrupt and resume protocol for missing parameters (missing_param), plan approval (plan_review), and post-WADS follow-up selection (postwads_choice/task_confirm).
tags: [hitl, interrupt, resume, plan-review, missing-param]
openwiki:
  roles: [architecture, workflow]
  source_paths: [08-YieldAgent/node_supervisor.py, 08-YieldAgent/node_plan_review.py, 08-YieldAgent/agent_server.py, 08-YieldAgent/models.py]
  symbols: [InterruptEvent, ChatRequest, plan_review_node, _resume_is_interrupt_answer, _require_agent_params]
  test_paths: [08-YieldAgent/tests/test_e2e_regression.py, 08-YieldAgent/tests/test_confirm_edit.py]
  invariants: ["One interrupt() call per dispatch — all missing slots collected into fields and asked together.", "Resume value for missing_param is a {slot: value} dict; bare string is the degraded Streamlit fallback filling only the first slot.", "plan_review keeps its own approve/modify/cancel text resume path, separate from missing_param."]
  validation_commands: ["pytest tests/test_e2e_regression.py -v"]
---

# HITL Contracts

The supervisor graph uses LangGraph `interrupt()` to pause execution and ask the user for input. There are distinct interrupt types, each with its own payload and resume contract.

## Interrupt types

| Type | Source node | Trigger | Resume contract |
|---|---|---|---|
| `missing_param` | `supervisor_node` | Required slots empty for the next task | `{slot: value}` dict (React form); bare string fills only the first slot (Streamlit fallback) |
| `plan_review` | `plan_review_node` | Task plan has ≥2 tasks | Free text: approve / modify / cancel |
| `task_confirm` | `supervisor_node` | Auto-suggestion gate (e.g. "WADS 보여줄까요?") | Free text (yes/no) or new intent |
| `postwads_choice` | `supervisor_node` | Post-WADS follow-up selection | Free text or new intent |

## missing_param payload

The server sends an `InterruptEvent` (defined in `models.py`) to the frontend:

```jsonc
{
  "type": "missing_param",
  "fields": [
    {
      "slot": "lot_ids",
      "label": "맵을 조회할 Lot ID …",
      "type": "lot_ids",
      "required_any_group": "lot_or_groupkey",
      "validation_hint": "PT1H|PT1C"
    }
  ],
  "param": "lot_ids",
  "message": "다음 정보를 입력해주세요 — …",
  "route": "map_agent"
}
```

- `fields` is the **source of truth** — every missing slot is listed. `param` is a representative slot for observability/back-compat only; never infer "only this slot is missing" from `param` in a batch.
- **One `interrupt()` call per dispatch**: all missing slots are collected and asked together. Never split into per-slot calls (that regresses to N round-trips).

## Resume contract

The frontend replies via `ChatRequest.resume_value`:

- **Dict path (React form)**: `{slot: value}` dict — the canonical path. React renders one input per `fields` entry and replies with the dict.
- **String path (Streamlit fallback)**: a bare string fills only the first slot, leaving the rest missing so they are re-asked next round. No positional parsing of a string into multiple slots.

## plan_review contract

`plan_review_node` interrupts with a plan to approve. The user responds with free text classified by an LLM as `approve`, `cancel`, or `modify`:

- **approve**: current requests are committed, graph proceeds to `supervisor`.
- **cancel**: `requests` is emptied, graph ends.
- **modify**: updated full request list is committed, graph re-enters `plan_review` in a new super-step.

This node enforces **one interrupt + one LLM call per super-step** to avoid LangGraph replay bugs. A `modify` action returns `Command(goto="plan_review")` rather than looping within the same node execution.

## Resume intent classification

When a resume value arrives for `task_confirm` or `postwads_choice` interrupts, `_resume_is_interrupt_answer` (in `supervisor.py`) classifies whether the input is an **answer** to the pending interrupt or a **new intent**. It uses an LLM (`_RESUME_INTENT_SYSTEM`) that judges semantically — not keyword matching. If the input is a new intent, the gate is dropped (via `Command(resume="")` which rejects the suggestion) and the fresh query is processed as a new turn.

## Invalid lotcd

An invalid (non-product-code) `lotcd` is a **value** problem, not a missing slot. It is handled by a separate re-prompt and early return (`_validate_lotcd_or_early_return`), kept apart from the slot collection batch.

## Known limitation (string fallback)

The bare-string resume fills a slot with the raw natural-language answer. For slots like `lot_ids` this risks a 0-rows silent-wrong (the whole sentence lands in the slot, no rows match, query ends quietly). The dict path (React form) is unaffected.

## When to consult this page

- Adding a new interrupt type or changing the interrupt payload.
- Changing how resume values are parsed.
- Modifying the plan-review approval flow.

## Validation

```bash
# Requires server on :8001
pytest tests/test_e2e_regression.py -v
pytest tests/test_confirm_edit.py -q
```

The `missing_lotcd_blocks_dispatch` case verifies that a missing required param blocks dispatch (not silently runs).
