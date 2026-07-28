# REPL Agent Quartz Chat UI Design

## Goal

Replace the current card-heavy dark REPL with one focused, contemporary chat surface while preserving every existing runtime behavior. Session data selection belongs at the top of that surface rather than on a separate page. Conversation history, application navigation, and other surrounding product-shell work remain outside this change.

## Approved Direction

- Product direction: Grok-like focused conversation rather than a dashboard or studio layout.
- Integration boundary: `App` owns the session lifecycle while presenting session selection and chat inside one continuous workspace.
- Runtime visibility: an inline execution timeline that keeps Python work visible in reading order.
- Color direction: Quartz Light.
  - canvas: `#f1f4f5`
  - surface: `#ffffff`
  - primary text: `#172126`
  - secondary text: `#65757c`
  - action blue: `#2766d8`
  - success teal: `#168765`
  - anomaly orange: `#d47b36`
  - divider: `#d6dfe2`
- Visual signature: analysis runs read as a measured sequence. A thin status line joins compact execution nodes, and anomaly color is reserved for data signals and warnings rather than decoration.

## Scope

### In scope

- The active chat viewport, empty state, prompt suggestions, analysis run presentation, inline Python timeline, artifacts, Markdown answer, error states, composer, send state, and cancel action.
- A compact workspace header and an in-place data-selection sheet at the top of the chat.
- The pre-session state and active-session state within the same light workspace.
- Responsive behavior within the width supplied by the parent application.
- Chat-local visual tokens required to realize Quartz Light.
- Accessibility behavior for focus, live status, keyboard submission, reduced motion, and readable contrast.

### Out of scope

- Conversation history and persistence UI.
- Sidebar, global navigation, account controls, or application-shell layout.
- Backend endpoints, SSE event shapes, reducer semantics, runtime lifecycle, and tool execution.
- New analysis capabilities or prompt changes.
- A new component library, icon package, font download, or theme framework.

## Information Architecture

`App` continues to decide which session is active, but no longer swaps between a standalone form page and the chat. The order is:

1. Compact workspace header with product name and current data-context pill.
2. Data-selection sheet, expanded before a session and collapsed to a summary after loading.
3. Scrollable message stream for the active session.
4. Empty-state invitation and suggested questions when no run exists.
5. For each run: user question, execution timeline, artifacts, assistant answer, and terminal state.
6. Runtime or cancellation error immediately above the composer.
7. Sticky composer at the bottom of the supplied chat region.

The existing run order and event correlation remain unchanged. This is a presentation redesign, not a data-flow rewrite.

## Component Design

### Workspace and data sheet

- Apply Quartz Light to the full page and workspace, not only the nested `Chat` component.
- Keep one workspace mounted before and after session creation so starting a session feels like opening a conversation, not navigating to another page.
- Before session creation, show the data sheet expanded with LOTCD, date range, fail name, and optional column guidance.
- After session creation, collapse the sheet to a compact summary led by `LOTCD · fail_name`; keep row count and date range in the sheet details rather than a raw metadata sentence.
- The existing “새 세션” action closes the server session first, then expands the same data sheet in place.
- Do not show raw session identifiers or full column lists in the primary header.

### Conversation surface

- Remove the large enclosing dark analysis card.
- Keep the assistant side visually open on the page canvas with a readable content measure.
- Render each user question as a compact pale-blue bubble aligned to the right.
- Separate consecutive analysis runs with whitespace and a quiet divider, not another nested panel.
- Keep the stream as the only scrolling region owned by `Chat`.

### Empty state

- Use one direct prompt explaining what the user can ask.
- Present the four existing presets as restrained outlined suggestion chips.
- Preserve the exact preset questions and disabled behavior.

### Inline execution timeline

- Show each tool step in execution order on a thin vertical rule.
- Use teal for completed execution, blue for active execution, orange/red only for warning or failure.
- Keep the tool name, execution duration, and result status visible at a glance.
- Put code, stdout, stderr, and raw result content behind native disclosure controls. The timeline remains visible even when details are collapsed.
- Keep Plotly artifacts inline with their correlated step; uncorrelated artifacts remain after the timeline.
- Preserve truncation badges and all existing result information.

### Assistant answer

- Place the Markdown answer directly after the execution timeline with no heavy outer card.
- Retain GFM tables, code, lists, headings, and streaming cursor behavior.
- Give tables and code blocks white or lightly tinted bordered surfaces suited to Quartz Light.
- Keep long content horizontally safe: code and tables scroll within their own bounds rather than widening the chat.

### Composer

- Use a single elevated white composer with a quiet border and subtle shadow.
- Keep the current single-line input and Enter-to-submit behavior; do not introduce multiline shortcuts in this change.
- Present send as a compact blue action. While running, expose the existing cancel action clearly in the active run and retain the disabled composer behavior.
- Preserve the current placeholder meaning, submission guard, cancellation flow, and error presentation.

### Errors and terminal states

- Runtime loss and cancellation errors remain `role="alert"` messages.
- Use pale red/orange surfaces with dark readable text instead of saturated dark-red blocks.
- Failed and cancelled runs remain visually distinct and must not be confused with an in-progress run.

## Responsive Behavior

- The unified workspace fills the available page height with a restrained desktop maximum width and no dark outer shell.
- At narrow widths, reduce horizontal padding, allow status controls to wrap, and keep user bubbles below approximately 86% width.
- Timeline details, Plotly output, Markdown tables, and code blocks must not overflow the chat viewport.
- Touch targets for send, cancel, disclosures, and suggestion chips remain at least 40px in the narrow layout where practical.

## Accessibility

- Preserve semantic article, heading, ordered-list, status, alert, form, and button elements.
- Keep `aria-live` status updates and the streaming cursor label.
- Use visible blue focus rings on all interactive controls.
- Do not encode run state with color alone; retain text labels such as 실행 중, 완료, 실패, and 중지됨.
- Honor `prefers-reduced-motion`; only the streaming cursor may animate in the default mode.

## Files Expected to Change

- `frontend/src/App.tsx`: render the persistent workspace header, in-place data sheet, and active chat.
- `frontend/src/SessionForm.tsx`: provide compact data-sheet copy and form structure without changing the request contract.
- `frontend/src/Chat.tsx`: chat-specific wrapper and presentation structure only where needed.
- `frontend/src/AnalysisCard.tsx`: transform the run card into the approved user-message, timeline, artifact, and answer flow while preserving data handling.
- `frontend/src/styles.css`: introduce scoped Quartz Light tokens and replace chat-related dark styles.
- Existing React tests for `Chat` and `AnalysisCard`: update or extend only for changed semantics and interactions.

Session lifecycle behavior, backend files, and history/navigation code are not changed. Only the `App.tsx` presentation around the existing lifecycle is revised.

## Verification

1. Component tests verify empty state, preset submission, user message, status labels, disclosure content, artifact placement, cancellation, errors, and streaming state.
2. The full frontend test suite and production build pass.
3. The backend suite remains unchanged and passing if the repository's standard full verification is run.
4. Browser verification uses a real session and real streamed analysis to confirm:
   - Quartz Light colors and readable contrast;
   - user question, live timeline, Python result, Plotly artifact, and assistant answer appear in order;
   - cancel still works during execution;
   - the composer stays usable and visible;
   - narrow viewport content does not overflow.
5. Visual review confirms the surrounding application shell and separately implemented conversation-history UI were not redesigned by this change.

## Success Criteria

- The active REPL feels like a modern focused chatbot instead of a stack of dark debug cards.
- Data selection is visibly part of the chat workspace and never appears as a separate page.
- The full viewport, header, selector, messages, and composer use Quartz Light consistently.
- Python execution remains more visible than in a Grok-style collapsed activity pill, matching the approved inline-timeline choice.
- Quartz Light is applied consistently and reserves strong color for actions, execution state, and anomalies.
- No existing session, streaming, cancellation, artifact, or error behavior regresses.
- The diff remains limited to the chat presentation and its tests.
