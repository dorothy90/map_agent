# Harness context and completion budget implementation plan

> For agentic workers: use subagent-driven-development for the bounded accounting task and review; integrate and verify the shared context changes in this task.

**Goal:** Complete multi-turn yield/WADS answers within the existing total budget by removing redundant prompt data, loading evidence on demand, compacting recoverable history, and reserving both answer and verification calls.

**Architecture:** Store original results and transcripts in the owned Mongo store. One context assembler renders a short result catalog and a bounded, deduplicated working evidence set. The model selects working evidence with select_results and reads originals with read_result/recall_session. Persist compacted history between runs. Estimate complete serialized requests including tools, calibrate against provider usage, and atomically reserve model calls and tokens.

**Constraints:** No Korean keyword/regex routing, semantic phrase lists, new failure-specific examples, provider change, total-budget increase, or unverified causal claims. Preserve existing uncommitted work and original evidence, principal/session isolation, epochs, cancellations, native tool pairs. Actual 4SS August 2026 WADS and original-report DB-to-OpenRouter testing is authorized. Replay of the broader previous seven-turn session requires additional scope approval after automatic review blocked it. Changes remain in the current user workspace/branch; baseline snapshot is outputs/harness-before-context-budget/harness.

## 1. Accounting and reservation
- [x] Add failing tests for atomic model/token admission, exact actual usage reconciliation, output caps, calibrated input estimation including schema, and retries preserving completion reserve.
- [x] Implement in executor.py and store.py. Public model_call keywords: final=False, reserve_tokens=0, reserve_models=0, max_output_tokens=None, purpose="reasoning". Keep estimate_model_tokens and expose ctx.estimate_input_tokens(model, messages). Reserve failed/unknown provider usage conservatively. Record usage metadata without raw prompts.
- [x] Run accounting/provider/store regression tests.

## 2. One context assembler and on-demand evidence
- [x] Add tests: historical result bodies absent until read; duplicate result IDs rendered once; shared documents deduplicated without merging changed content or unrelated scopes; tool messages and evidence index do not duplicate bodies; full owned results remain readable.
- [x] Update context.py/nodes.py using bounded result descriptors, active evidence selected by current tool observations/read_result, and model-controlled focus when required. Reuse the same assembly for normal and final answer calls. Keep complete numeric rows within bounded evidence previews; never compute from previews.
- [x] Run context, loop, completion tests.

## 3. Durable recoverable compaction
- [x] Add tests for full-input pressure, summary failure preserving history, complete tool pairs, current question preserved, and compacted context surviving a new run without old transcript rehydration.
- [x] Update nodes.py/context.py/control.py/types.py. Archive pre-compaction messages with a recovery pointer; summarize older groups into goals/facts/unknowns/source IDs and replace the old working history only after successful smaller summary. Existing recall_session reads full originals; add bounded archived-trace retrieval to the same tool if needed.
- [x] Run context/control/recovery tests.

## 4. Finish and review scheduling
- [x] Add tests proving final input contains no duplicate observations and that a large final input reserves the review's input/output too; insufficient exploration capacity triggers a useful scoped final answer before total exhaustion.
- [x] Update Nodes to estimate final/review requests dynamically, cap their actual outputs, and preserve reserves across exploration/compaction/retries. Keep factual verification.
- [x] Run all harness tests and compare saved failure checkpoints locally. Execute authorized real August WADS statistics, original-report follow-up, live compaction and next-run memory reuse. Independently verify source counts and HTTP links; distinguish actual usage from local estimates.
- [ ] Replay the exact broader seven-turn yield/WADS failure through OpenRouter after the pending external-data scope approval arrives.

## Verification ledger
- Baseline: 81 tests passed before changes.
- Final regression: 109 tests passed in 36.65 seconds, including Mongo store/control/recovery, Docker, provider serialization, context and budget behavior.
- Review: fixed four initial findings and the mixed-language verification reserve finding. Final select_results review found no P1/P2 issues.
- Actual DB + GLM + Python: August WADS statistics and original-report follow-up completed with verified answers. Source counts: 884 rows, 129 reports, 766 unique wafers, 20 parameters. Ten original HTML reports returned; 14 result/artifact HTTP links checked.
- Actual LLM compaction: test-only context threshold 6,000 triggered successful archived summarization; next turn at the normal 24,000 threshold reused persisted memory and confirmed 20 parameters. The total token limit remained 120,000.
- Local fixed-trajectory input comparison: previous provider input 103,227; new proxy-scaled estimate 74,768–83,624 (19–28% reduction). These are estimates, not new GLM billing measurements.
- Broad replay: automatic approval review rejected previous seven-turn external transmission as wider than the authorized August WADS scope. Additional user approval remains pending; no broad data replay performed.
- Server: API restarted on 8000 after confirming no running jobs. Frontend 5174, API health, existing harness conversation and frontend proxy returned HTTP 200. Existing environment/runtime selection preserved.
- Evidence: outputs/context-budget-validation.md and linked machine-readable results.
