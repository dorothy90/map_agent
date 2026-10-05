# Progress commentary implementation plan

Goal: Show useful progress before the final answer, with minimal changes to the existing harness and SSE client.

Approved approach: Reuse the persisted event stream; distinguish replaceable runtime status from retained user-facing model commentary. Keep non-streaming model invocation, existing tool loop, budgets, verification and provider configuration. No extra LLM call, semantic routing rules, new dependency or worklog/reasoning disclosure.

References: [Codex items](https://learn.chatgpt.com/docs/app-server), [Claude message flow](https://code.claude.com/docs/en/agent-sdk/streaming-output), [Hermes completed interim messages](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md#display-settings).

- [x] Add regression tests for public text extraction, commentary preceding tools, phase status preceding model calls, stable replay IDs, history restoration and final-answer isolation.
- [x] In executor.py publish model-phase status before invocation; in nodes.py publish nonempty public text accompanying nonterminal tool calls. Reuse call IDs for event identity; honor epoch fencing. Clarify the common instruction in instructions/analysis.md.
- [x] In router.py map commentary to its own SSE event, leave final message behavior unchanged, and keep raw worklogs out of status text. Preserve completed-run commentary through agent_server.py history and the optional models.py phase field; active-run history continues to use SSE replay.
- [x] In types.ts and App.tsx render retained commentary separately from the latest status. Reuse stream.ts stable-ID deduplication. Clear live status when streams stop, fail, pause or are replaced; restore commentary from history.
- [x] Run targeted regression tests, full harness tests and frontend build. Exercise real 4SS DB/model/tool execution through the web endpoint, inspect status/commentary/final ordering, reload/reconnect and stop behavior. Restart only the verified backend with no active user runs.

Validation uses the previously authorized recent-four-week 4SS scenario. Only its current run's data is sent to the existing OpenRouter model; no broader conversation replay.

Result: 122 harness tests, 4 frontend transport tests and build passed. First live request ended partial on existing scope validation; second completed/verified with 5 commentary messages. Both outcomes, reload/cancel/error checks and the remaining scope issue are recorded in outputs/progress-validation.md.
