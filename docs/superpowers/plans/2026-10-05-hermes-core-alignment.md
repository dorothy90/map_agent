# Hermes core alignment and repeat verification

Goal: Preserve the existing backend integrations while aligning the tool/skill/context/investigation loop with the verified upstream architecture. This is not installation of the entire Hermes product.

Reference: NousResearch/hermes-agent e473f5a9c976a0b5bc292aa415dae28c638a47c3.

## Source comparison

| Mechanism | Upstream source | Local finding / action |
| --- | --- | --- |
| Core tools eager, specialist tools deferred | tools/tool_search.py | All domain tools were deferred. Declare core tools on ToolSpec; keep specialist load_tools. |
| Required relevant skills and optional auto load | agent/prompt_builder.py, agent/system_prompt.py | Auto load and snapshot already added; verify first model call and review receive identical content. |
| Time wrap-up at 80% of optional run budget | agent/conversation_loop.py | Local 2*90 second reservation left only 120/300 seconds. Reserve 20% for answer/review, bounded by call timeouts. |
| Original request + tool call/result loop | agent/conversation_loop.py | Original goal retained; preserve this without keyword routing. |
| Context pressure handled before hard termination | agent/context_compressor.py | Stored evidence projections and archive-backed compaction exist; verify budgets include tools and skills. |
| Recovery / evidence review | Local extension | Hermes is not the source of our separate semantic verifier. Test its rejection and continuation explicitly. |
| execute_code tool RPC | tools/code_execution_tool.py | Local Python is offline computation only; do not claim parity or silently expand sandbox authority. |
| Memory learning, CLI, gateways | Outside backend core adaptation | Not implemented as full Hermes features. |

## Implementation / verification

- [x] Reproduce eager-tool and premature-time-wrapup failures with tests.
- [x] Add ToolSpec.eager, expose declared core schemas from first call, omit duplicate catalog descriptions.
- [x] Use one shared finalization_seconds calculation across model and tool calls.
- [x] Run affected unit/integration suite; verify skill and reviewer contracts.
- [x] Run exact backend query three times with independent sessions and unchanged model/total budget.
- [x] Inspect actual calls, input arguments, completion verdict, generated artifacts and data scope; do not use backend status alone as pass criterion.
- [x] Record remaining failures candidly with source commit and traces.

Constraints: No user-text keyword/regex routing; no database mutations; preserve existing working changes; no automatic model change or increased total budget.

## Final verification record

- Related unit/MongoDB integration tests: **84 passed**, 7.33 seconds. These do not establish live task success.
- Independent code review found stale read-result priority could hide a new calculation. Reproduced failing regression and fixed priority to the newest observation only; reviewer rechecked and reported no new P1/P2.
- Added `period_ranges` to yield result scope and all changed metrics to analysis output after reproducing missing ranges / top-three clipping. This is a domain result contract fix, not a Hermes feature.
- Source comparison additionally checked `agent/tool_guardrails.py`. Local observational warning after three equivalent consecutive visible results is inspired by it; it is **not** upstream exact-call guard/cycle detection/stubbing/hard-stop parity. It does not inspect natural-language query keywords or block tools.
- Upstream 80% behavior is a soft one-time notice. Local 20% hard answer/review reservation is an adaptation for the existing backend verifier, not identical runtime behavior.

### Actual DB + LLM + tools, unchanged model and 120,000 total-token / 300-second budget

| Revision stage | Trial | Seconds | Tokens | Result |
| --- | --- | ---: | ---: | --- |
| Core + result contracts | 1 | 188.68 | 108346 | partial / ValidationError; reviewer emitted exactly its 2048-token cap |
| Core + result contracts | 2 | 136.43 | 89178 | partial / incomplete_at_budget |
| Core + result contracts | 3 | 105.72 | 95508 | partial / incomplete_at_budget |
| Latest-read priority + repetition notice (final code) | 1 | 100.03 | 99154 | partial / incomplete_at_budget |
| Latest-read priority + repetition notice (final code) | 2 | 74.07 | 87620 | partial / incomplete_at_budget |
| Latest-read priority + repetition notice (final code) | 3 | 73.50 | 101511 | partial / incomplete_at_budget |

Exact query: `4SS 최근4주 수율 조회하고 열화 파라미터 중에 제일 수율안좋은 wafer 10개 binmap 보여줘`. Sessions were independent; each three-trial batch ran concurrently. Model/provider routing variability and concurrent load therefore remain possible latency influences.

Final code **does not pass the requested end-to-end scenario**. Identified remaining behaviors: calendar-period ranges extending into future dates are not reliably rejected by the planner/reviewer; WADS selection versus period-change selection is inconsistent; repeated retrieval still consumes aggregate token budget; malformed native tool argument keys occurred and remained recoverable validation errors rather than being repaired with case-specific parsing. One earlier trial generated ten maps but narrowed scope to one selected parameter, so it was not counted as task success.

Full machine-readable record: `outputs/hermes-alignment-evaluation-20261005.json`; per-run `evaluation.json` contains actual tool arguments and completion review, alongside request/events/stream logs.

### Scope that is explicitly not claimed

This is still a custom backend with selected Hermes core mechanisms adapted. Full upstream execute_code tool RPC, identical-call cycle controller, memory/skill learning, provider adapter behavior, CLI and gateways have not been ported. Do not label this a complete Hermes implementation or a validated replacement for the original direct report. No database contents, deployment service, configured model, or total budget were changed in this audit.
