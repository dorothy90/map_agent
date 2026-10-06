# Tool result detail implementation plan

Goal: Connect a timeline invocation to its saved arguments, results and errors without changing the current layout or agent behavior.

Approved scope: The user requested only necessary additions after the recommendation to connect tool execution details with results. Keep the existing split view. Select a timeline entry to show its details on the right; return with “전체 결과”. No additional dashboards, plans or usage indicators.

Architecture: Preserve run_id and invocation_id on ExecStep. Read the existing authenticated GET /runs/{run_id} endpoint only on selection. Match observations by invocation ID within that run, never by tool name. Render named tables using the existing ArtifactPanel and match stored artifact references by exact ID. Abort stale requests. Restore the latest run's timeline from history observations.

Files: yield_frontend/src/types.ts; src/lib/tool-detail.ts; src/components/ToolDetail.tsx; src/components/AgentPlan.tsx; src/App.tsx; tests/tool-detail.test.mjs.

1. Write and run failing tests for exact invocation matching, named tables/artifact ownership, and history step restoration.
2. Add minimal projection helpers and selection panel. Show arguments in a disclosure, summary and errors visibly, reuse existing result rendering. Handle loading, unavailable results and request errors explicitly.
3. Run existing frontend unit tests and production build. Inspect actual existing DB-backed history in the browser and run a fresh read-only query through the real LLM and tools. Verify selected execution shows its own results, switching and return work, and capture the final screen.

No backend changes or additional dependencies. No automatic commit/push requested for this change.

Verification completed: 9 frontend unit tests passed; production TypeScript/Vite build passed; git diff --check passed. CUA verified stored 4SA vs 4SS invocation selection, Python failure notification, and input disclosure. A separate real backend session queried 4SS for 2026-09-30 (daily, 1 period), completed with GMS CUM0 83.94%, FAB 94.65%, PRB 89.43%. Verified selected result, final completion, reload restoration, and return to all results. Screenshot: outputs/tool-result-detail-20261006.jpg. Production agent/backend unchanged.
