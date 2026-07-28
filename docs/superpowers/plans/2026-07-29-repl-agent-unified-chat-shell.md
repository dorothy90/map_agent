# REPL Agent Unified Chat Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the approved Quartz Light mock by keeping data selection and analysis chat in one persistent workspace.

**Architecture:** `App` remains the owner of session lifecycle state and renders a persistent workspace around both session states. `SessionForm` remains responsible for the existing POST contract, while CSS turns it into the expanded state of a top data sheet and turns active session metadata into a compact summary.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, Vite, CSS

## Global Constraints

- Preserve all backend endpoints, request bodies, SSE behavior, runtime lifecycle, and cancellation behavior.
- Do not add conversation history, navigation, dependencies, fonts, or component libraries.
- Apply Quartz Light across the complete page with canvas `#f1f4f5`, surface `#ffffff`, text `#172126`, muted `#65757c`, action `#2766d8`, success `#168765`, anomaly `#d47b36`, and divider `#d6dfe2`.
- Do not expose the raw session identifier or full column list in the primary header.

---

### Task 1: Lock the unified workspace behavior

**Files:**
- Modify: `08-YieldAgent/repl_agent/frontend/src/App.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/App.tsx`

**Interfaces:**
- Consumes: `SessionForm.onStarted(info: SessionInfo): void`, `Chat({ sessionId }: { sessionId: string })`
- Produces: persistent `aria-label="Yield 분석 워크스페이스"` and active data summary accessible by its LOTCD and fail name

- [ ] **Step 1: Write failing tests** asserting the workspace and data sheet coexist before a session, the workspace remains after starting, and raw session/column metadata is absent from the primary header.
- [ ] **Step 2: Run `npm test -- --run src/App.test.tsx`** and confirm the new assertions fail for missing workspace/data-sheet semantics.
- [ ] **Step 3: Implement the smallest `App.tsx` markup change** that keeps one workspace mounted, renders the form in its top region before loading, and renders compact active-session context plus `Chat` after loading.
- [ ] **Step 4: Run `npm test -- --run src/App.test.tsx`** and confirm all App tests pass.

### Task 2: Match the Quartz mock at page and component level

**Files:**
- Modify: `08-YieldAgent/repl_agent/frontend/src/SessionForm.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/styles.css`

**Interfaces:**
- Consumes: existing form field state and `/repl/session` POST body
- Produces: compact top data sheet on desktop and stacked accessible controls on narrow screens

- [ ] **Step 1: Add a failing App test** for the user-facing `데이터 선택` heading and active-session `데이터 변경` action.
- [ ] **Step 2: Run `npm test -- --run src/App.test.tsx`** and confirm the copy/controls are missing.
- [ ] **Step 3: Update `SessionForm` copy and `App` active context** without altering the fetch request, then replace dark global/session styles with the approved Quartz tokens, compact header, data-sheet grid, and responsive rules.
- [ ] **Step 4: Run `npm test -- --run` and `npm run build`** and confirm all tests and the production build pass.

### Task 3: Verify the real user journey and integrate

**Files:**
- Modify only files required by defects found in this task's verification.

**Interfaces:**
- Consumes: live FastAPI `/repl/session`, `/repl/chat`, `/repl/session/{id}` routes
- Produces: visual evidence that the selector and chat occupy one screen and that a real streamed analysis renders

- [ ] **Step 1: Start FastAPI and Vite from this worktree** using the repository's configured environment and ports that do not collide with existing processes.
- [ ] **Step 2: In a real browser, create a session with the default dataset and submit `df.head()를 보여주고 각 컬럼의 의미를 설명해줘`.** Verify the sheet collapses in place, the streamed execution timeline and answer appear, and the composer remains visible.
- [ ] **Step 3: Capture desktop and narrow screenshots** and compare page background, header density, data context, message width, alerts, and composer against the approved mock.
- [ ] **Step 4: Run the full frontend suite/build and backend suite fresh.** Do not claim completion without zero failing tests and a successful build.
- [ ] **Step 5: Commit the scoped diff, merge it into `main`, verify the merged commit, and push `main` to origin.**
