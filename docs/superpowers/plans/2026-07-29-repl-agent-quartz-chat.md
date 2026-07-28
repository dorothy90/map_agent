# REPL Agent Quartz Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign only the active REPL chat surface as the approved Quartz Light, Grok-like conversation with a continuously visible inline Python execution timeline.

**Architecture:** Keep `useReplStream`, reducer state, session ownership, SSE contracts, and Plotly rendering unchanged. Add a chat-local layout boundary in `Chat`, reshape `AnalysisCard` into semantic user/timeline/assistant turns, and scope all Quartz Light tokens under that chat boundary so separately implemented navigation and history remain untouched.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, React Markdown with GFM, Plotly, CSS.

## Global Constraints

- Change only the active chat viewport and its tests; do not add conversation history, sidebar, navigation, or global shell redesign.
- Keep all backend endpoints, SSE shapes, reducer semantics, runtime lifecycle, prompt text, preset questions, cancellation flow, and artifacts unchanged.
- Add no component library, icon package, font download, or theme framework.
- Use Quartz Light tokens: canvas `#f1f4f5`, surface `#ffffff`, text `#172126`, secondary text `#65757c`, action blue `#2766d8`, success teal `#168765`, anomaly orange `#d47b36`, divider `#d6dfe2`.
- Preserve semantic status/alert elements, keyboard submission, visible focus, readable contrast, and reduced-motion behavior.
- Follow test-driven development: every production behavior or structure change starts with a test that is observed failing for the expected reason.

---

### Task 1: Semantic Chat Boundary and Composer

**Files:**
- Modify: `08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/Chat.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/styles.css`

**Interfaces:**
- Consumes: `useReplStream(sessionId)` with existing `state`, `send`, `cancel`, `sending`, `cancelPending`, and `cancelError` values.
- Produces: one `<section className="chat-shell" aria-label="분석 대화">` containing the existing message stream and composer; all Quartz styles use this boundary.

- [x] **Step 1: Write the failing chat-boundary test**

Add this test to `Chat.test.tsx`:

```tsx
it("owns a labeled chat surface with its composer inside", () => {
  render(<Chat sessionId="session-1" />);

  const chat = screen.getByRole("region", { name: "분석 대화" });
  expect(chat).toHaveClass("chat-shell");
  expect(within(chat).getByRole("form", { name: "분석 질문" })).toBeInTheDocument();
});
```

Import `within` from `@testing-library/react`.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm test -- src/Chat.test.tsx -t "owns a labeled chat surface"
```

Expected: FAIL because no region named `분석 대화` exists.

- [x] **Step 3: Add the minimal semantic wrapper**

Change `Chat` to return this structure without changing submission or cancellation logic:

```tsx
return (
  <section className="chat-shell" aria-label="분석 대화">
    <div className="messages">{/* existing message rendering */}</div>
    {/* existing runtime and cancellation alerts remain in their current order */}
    <form className="input-row" aria-label="분석 질문" onSubmit={submit}>
      {/* existing input and button */}
    </form>
  </section>
);
```

- [x] **Step 4: Add only the layout styles needed by the wrapper**

Add chat-scoped tokens and layout at the start of the chat section in `styles.css`:

```css
.chat-shell {
  --chat-canvas: #f1f4f5;
  --chat-surface: #ffffff;
  --chat-text: #172126;
  --chat-muted: #65757c;
  --chat-action: #2766d8;
  --chat-success: #168765;
  --chat-anomaly: #d47b36;
  --chat-divider: #d6dfe2;
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  margin-top: 0.75rem;
  overflow: hidden;
  border: 1px solid var(--chat-divider);
  border-radius: 18px;
  background: var(--chat-canvas);
  color: var(--chat-text);
}
```

Move chat layout styling under `.chat-shell` where necessary; do not change `.app`, `header`, or session-form ownership in this task.

- [x] **Step 5: Run the focused and full Chat tests**

Run:

```bash
npm test -- src/Chat.test.tsx
```

Expected: 5 tests pass, including the new semantic-boundary test.

- [x] **Step 6: Commit Task 1**

```bash
git add 08-YieldAgent/repl_agent/frontend/src/Chat.tsx \
  08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx \
  08-YieldAgent/repl_agent/frontend/src/styles.css
git commit -m "feat(repl-ui): add quartz chat surface"
```

---

### Task 2: User Turn and Inline Execution Timeline

**Files:**
- Modify: `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx`
- Create: `08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/styles.css`

**Interfaces:**
- Consumes: unchanged `AnalysisRun`, `ToolStep`, `PlotlyMessage`, `onCancel`, and `cancelPending` props.
- Produces: one `.analysis-run` article containing `.user-turn`, an ordered list labeled `Python 실행 과정`, disclosure-based execution details, correlated artifacts, and `.assistant-turn`.

- [x] **Step 1: Write failing semantic-flow tests**

Add these assertions to the main completed-run test in `AnalysisCard.test.tsx`:

```tsx
const run = screen.getByRole("article", { name: "fail_value 평균을 계산해줘" });
expect(within(run).getByText("fail_value 평균을 계산해줘")).toHaveClass("user-turn-text");
expect(within(run).getByRole("list", { name: "Python 실행 과정" })).toBeInTheDocument();
expect(within(run).getByText(/판정: 평균 차이/).closest(".assistant-turn")).toBeInTheDocument();
expect(within(run).getByText("코드").closest("details")).not.toHaveAttribute("open");
expect(within(run).getByText(/stdout/).closest("details")).not.toHaveAttribute("open");
```

The article obtains its name through the existing heading referenced by `aria-labelledby`.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm test -- src/AnalysisCard.test.tsx -t "renders code, structured output"
```

Expected: FAIL because `.user-turn-text`, the labeled execution list, and `.assistant-turn` do not exist, and the disclosures are currently open.

- [x] **Step 3: Reshape the run without changing its data logic**

Keep `correlatedArtifacts`, `uncorrelatedArtifacts`, result parsing, truncation handling, and Markdown rendering intact. Change presentation as follows:

```tsx
<article className={`analysis-run status-${run.status}`} aria-labelledby={`query-${run.runId}`}>
  <header className="user-turn">
    <h2 className="user-turn-text" id={`query-${run.runId}`}>{run.userMessage}</h2>
  </header>
  <div className="run-meta">
    <span className="analysis-label">Analysis</span>
    <div className="analysis-controls">{/* existing status and conditional cancel */}</div>
  </div>
  {run.steps.length > 0 ? (
    <ol className="analysis-steps" aria-label="Python 실행 과정">
      {/* existing ToolExecution mapping */}
    </ol>
  ) : null}
  {/* existing uncorrelated artifacts and error */}
  {run.assistantText || run.status === "running" ? (
    <div className="assistant-turn analysis-answer md">
      {/* existing label, Markdown, and cursor */}
    </div>
  ) : null}
</article>
```

Remove `open` from code and result `<details>`. Apply `step-completed` when `step.result` exists and `step-running` otherwise so CSS can show state without inferring from natural-language content:

```tsx
<li className={`analysis-step ${result ? "step-completed" : "step-running"}`}>
```

- [x] **Step 4: Implement Quartz Light run and timeline styles**

Replace the dark `.analysis-card` styles with chat-scoped `.analysis-run`, `.user-turn`, `.run-meta`, `.analysis-steps`, `.analysis-step`, `.step-index`, disclosure, output, artifact, answer, and state styles. Use:

```css
.chat-shell .user-turn {
  display: flex;
  justify-content: flex-end;
}
.chat-shell .user-turn-text {
  max-width: 82%;
  margin: 0;
  padding: 0.7rem 0.9rem;
  border-radius: 16px 16px 4px 16px;
  background: #dde8f7;
  color: var(--chat-text);
  font-size: 0.92rem;
  font-weight: 500;
}
.chat-shell .analysis-step {
  border-left: 1px solid var(--chat-divider);
}
.chat-shell .step-completed .step-index {
  border-color: var(--chat-success);
  background: var(--chat-success);
  color: #ffffff;
}
.chat-shell .step-running .step-index {
  border-color: var(--chat-action);
  background: var(--chat-surface);
  color: var(--chat-action);
}
```

Use white surfaces for details and Plotly artifacts, pale warning/error surfaces, dark code text on a light blue-gray code background, and local overflow for code, tables, and plots. Do not introduce gradients or decorative animation.

- [x] **Step 5: Run the AnalysisCard suite**

Run:

```bash
npm test -- src/AnalysisCard.test.tsx
```

Expected: all 4 AnalysisCard tests pass with disclosures collapsed in markup while their content remains in the DOM; both Plotly tests pass with Quartz defaults and container-resize handling.

- [x] **Step 6: Commit Task 2**

Before committing, add a `PlotlyMessage` test that asserts the default `paper_bgcolor`, `plot_bgcolor`, and font color are `#ffffff`, `#ffffff`, and `#172126`. Observe it fail against the dark defaults, then update only those three defaults so incoming artifact layout values can still override them.

```bash
git add 08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx \
  08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx \
  08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.tsx \
  08-YieldAgent/repl_agent/frontend/src/PlotlyMessage.test.tsx \
  08-YieldAgent/repl_agent/frontend/src/styles.css
git commit -m "feat(repl-ui): reshape analysis timeline"
```

---

### Task 3: Empty State, Composer, Responsive Polish, and Verification

**Files:**
- Modify: `08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/Chat.tsx`
- Modify: `08-YieldAgent/repl_agent/frontend/src/styles.css`
- Modify: `08-YieldAgent/repl_agent/frontend/e2e/repl-live.spec.ts`

**Interfaces:**
- Consumes: existing four preset tuples and existing `locked` behavior.
- Produces: a Quartz Light empty state, suggestion chips, sticky white composer, narrow-layout safeguards, and unchanged live user flow.

- [x] **Step 1: Write the failing empty-state and composer test**

Add to `Chat.test.tsx`:

```tsx
it("presents suggestions and a named send action inside the empty chat", () => {
  render(<Chat sessionId="session-1" />);

  const suggestions = screen.getByRole("group", { name: "추천 질문" });
  expect(within(suggestions).getAllByRole("button")).toHaveLength(4);
  expect(screen.getByRole("button", { name: "질문 보내기" })).toHaveTextContent("보내기");
});
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm test -- src/Chat.test.tsx -t "presents suggestions"
```

Expected: FAIL because the recommendation group and named send action do not exist.

- [x] **Step 3: Add accessible labels without changing behavior**

Change only the preset container and submit button:

```tsx
<div className="preset-row" role="group" aria-label="추천 질문">
  {/* existing preset buttons */}
</div>
<button type="submit" aria-label="질문 보내기" disabled={locked || !input.trim()}>
  {sending ? "분석 중…" : "보내기"}
</button>
```

- [x] **Step 4: Finish chat-scoped Quartz Light CSS**

Apply the approved palette to `.messages`, `.empty-hint`, `.preset-row`, `.input-row`, cursor, errors, Markdown, and responsive rules only beneath `.chat-shell`. Required behavior:

```css
.chat-shell .messages {
  min-height: 0;
  padding: clamp(1rem, 2.8vw, 2rem);
  scrollbar-gutter: stable;
}
.chat-shell form.input-row {
  margin: 0 clamp(0.75rem, 2vw, 1.5rem) clamp(0.75rem, 2vw, 1.25rem);
  padding: 0.45rem;
  border: 1px solid var(--chat-divider);
  border-radius: 14px;
  background: var(--chat-surface);
  box-shadow: 0 8px 24px rgb(23 33 38 / 8%);
}
.chat-shell form.input-row input {
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--chat-text);
}
@media (max-width: 640px) {
  .chat-shell { margin-top: 0.5rem; border-radius: 14px; }
  .chat-shell .messages { padding: 1rem 0.75rem; }
  .chat-shell .user-turn-text { max-width: 86%; }
  .chat-shell .analysis-controls { flex-wrap: wrap; }
  .chat-shell .analysis-steps { padding-left: 1.8rem; padding-right: 0; }
}
```

Ensure `.md table`, code blocks, `.artifact`, and `.result-output` have `max-width: 100%` and their own overflow. Keep focus rings and reduced-motion behavior.

- [x] **Step 5: Run focused tests, full frontend tests, and build**

Run:

```bash
npm test -- src/Chat.test.tsx src/AnalysisCard.test.tsx
npm test
npm run build
```

Expected: 82 frontend tests pass (two new Chat tests and two new Plotly tests) and the TypeScript/Vite build exits 0.

- [x] **Step 6: Run real browser verification**

Update the live selectors from `.analysis-card` to `.analysis-run` and from the old send accessible name to `질문 보내기`. Because code and result disclosures are intentionally collapsed, open their summaries before asserting code, status, and stdout.

Start or reuse the real FastAPI and Vite servers, then run:

```bash
PLAYWRIGHT_BASE_URL=http://127.0.0.1:5174 npm run e2e:live
```

Expected: live session creation, real streamed Python execution, Plotly artifact, final assistant response, worker reuse, and session close pass. Inspect the rendered desktop and narrow viewport to confirm Quartz colors, inline ordering, composer visibility, and no horizontal overflow; cancellation remains covered by the existing component and hook suites.

- [x] **Step 7: Review scope and commit Task 3**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~2
```

Confirm no backend, `App.tsx`, `SessionForm.tsx`, history, or navigation source changed. Then commit only the intended chat files and plan:

```bash
git add docs/superpowers/plans/2026-07-29-repl-agent-quartz-chat.md \
  08-YieldAgent/repl_agent/frontend/src/Chat.tsx \
  08-YieldAgent/repl_agent/frontend/src/Chat.test.tsx \
  08-YieldAgent/repl_agent/frontend/src/AnalysisCard.tsx \
  08-YieldAgent/repl_agent/frontend/src/AnalysisCard.test.tsx \
  08-YieldAgent/repl_agent/frontend/src/styles.css
git commit -m "style(repl-ui): polish quartz chat"
```
