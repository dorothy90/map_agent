# Harness simplification implementation plan

> Execute inline with executing-plans. Design approved by the user: “적용해줘”.

**Goal:** Preserve the existing tools and operational controls while reliably delivering answers and follow-up results without per-claim proof reconstruction.

**Architecture:** A native model/tool loop with optional, replaceable work notes. Finish carries an answer and result references; the engine resolves owned stored results, renders their values/artifacts, and provides bounded semantic feedback. User messages remain authoritative; model plans do not overwrite them.

**Constraints:** No natural-language keyword/regex routing, new intent cases, or few-shot patches. Keep the selected GLM model and budgets unchanged for the before/after comparison. Preserve ownership, cancellation, complete stored data and provenance. The user explicitly authorized sending the actual 4SS August 2026 WADS DB results to OpenRouter for the statistics and original-report follow-up tests.

## Tasks

- [x] Capture the current implementation and synthetic two-turn WADS baseline in `outputs/harness-before-simplification/` and `outputs/harness-simplification-before.json`.
- [x] Add failing graph/contract tests: optional native text, replaceable plan without mutation of user goal, finish with result IDs, missing/foreign/error sources rejected, source scope conflict, bounded review fallback that preserves stored tables, compact errors and follow-up source focus.
- [x] Simplify `types.py`, `completion.py`, `nodes.py`, and `instructions/analysis.md`: remove the claims/coverage proof schema and mandatory planning; retain a short result-reference contract and bounded semantic review. Resolve references through the owned result store, not model-provided values. Render stored tables and original artifact links even if narrative completion fails.
- [x] Update `context.py`, `control.py`, and Python model views: preserve original messages and final selected source IDs, compact older observations, keep full errors/data in storage, return concise recoverable errors to the model.
- [x] Update graph tests and documentation to the new contract. Run the full harness suite including DB, cancellation, isolation and Python execution tests.
- [x] Run the same live GLM + synthetic WADS two-turn scenario, then product/date/expression variants and a calculation scenario. Inspect actual answers and source data as well as completion flags.
- [x] Review the diff, fix concrete findings, perform authorized live DB/tool checks and browser rendering checks, and restart the backend only when idle. Record any uncompleted live LLM/DB acceptance separately.

## Acceptance

No invented source or numerical value may be substituted for stored data. A corrected plan replaces the model's previous interpretation. Native conversation does not require a finish call or plan. Verified tables and source links remain available under narrative review failure. The WADS scenario returns the report statistics and then the original reports with the same product/date; no yield query or new PPT is substituted. Compare calls, tokens, elapsed time and final answer quality, not only unit test status.

## Validation and remaining limits

See [validation report](../../../outputs/harness-simplification-validation.md). The complete harness suite passed 81 tests. Live-model synthetic tests, product/date variants, authorized DB integration and the browser two-turn scenario completed. All 39 category/parameter counts matched the independent DB aggregation; 129 original HTML artifacts were readable.

The DB contains test-report content. All 129 saved HTML body dates disagree with their August `END_TM` metadata; the adapter returns the stored HTML unchanged. This source-data issue was recorded, not silently repaired. Narrative review can still miss an incorrect interpretation or presentation detail. Full release evaluation, including mining and 30-turn memory, is outside this scoped change and remains unpassed.
