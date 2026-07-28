# REPL Agent Quartz Chat UI Design

## Goal

Replace the current card-heavy dark REPL conversation with a focused, contemporary chat surface while preserving every existing runtime behavior. The design applies only to the chat content owned by this feature; conversation history, application navigation, and other surrounding shell work remain outside this change.

## Approved Direction

- Product direction: Grok-like focused conversation rather than a dashboard or studio layout.
- Integration boundary: the parent keeps ownership of session selection and data context; this change begins at the active chat surface.
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

The parent application continues to decide which session is active. Inside the active conversation, the order is:

1. Scrollable message stream.
2. Empty-state invitation and suggested questions when no run exists.
3. For each run: user question, execution timeline, artifacts, assistant answer, and terminal state.
4. Runtime or cancellation error immediately above the composer.
5. Sticky composer at the bottom of the supplied chat region.

The existing run order and event correlation remain unchanged. This is a presentation redesign, not a data-flow rewrite.

## Component Design

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

- The chat fills the width and height supplied by its parent; it does not impose a global page width or add navigation.
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

- `frontend/src/Chat.tsx`: chat-specific wrapper and presentation structure only where needed.
- `frontend/src/AnalysisCard.tsx`: transform the run card into the approved user-message, timeline, artifact, and answer flow while preserving data handling.
- `frontend/src/styles.css`: introduce scoped Quartz Light tokens and replace chat-related dark styles.
- Existing React tests for `Chat` and `AnalysisCard`: update or extend only for changed semantics and interactions.

`App.tsx`, session lifecycle logic, backend files, and history/navigation code are not expected to change unless implementation reveals a concrete integration requirement. Any such expansion requires review before editing.

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
- Python execution remains more visible than in a Grok-style collapsed activity pill, matching the approved inline-timeline choice.
- Quartz Light is applied consistently and reserves strong color for actions, execution state, and anomalies.
- No existing session, streaming, cancellation, artifact, or error behavior regresses.
- The diff remains limited to the chat presentation and its tests.
