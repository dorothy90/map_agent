---
type: Integration
title: React Frontend
description: The Vite + React 19 chat frontend (yield-insight-frontend) that consumes the agent_server SSE protocol via /session and /chat/stream.
tags: [react, frontend, vite, sse, chat]
openwiki:
  roles: [integration, delivery]
  source_paths: [08-YieldAgent/yield_frontend/src/App.tsx, 08-YieldAgent/yield_frontend/src/lib/stream.ts, 08-YieldAgent/yield_frontend/src/types.ts]
  symbols: [App, streamChat, createSession, RealStreamEvent, ChatItem, CanvasCard]
  test_paths: []
  invariants: ["Frontend proxies /session and /chat to agent_server :8001 via vite.config.ts proxy.", "Resume value for plan_review is free text (string); for missing_param is a slot dict."]
  validation_commands: ["cd 08-YieldAgent/yield_frontend && npm run build"]
---

# React Frontend

The `yield-insight-frontend` (`08-YieldAgent/yield_frontend/`) is a Vite + React 19 app that provides the chat UI for the multi-agent supervisor. It consumes the SSE protocol from `agent_server` `/chat/stream` endpoint.

## Architecture

The frontend uses `fetch` + `ReadableStream` (not `EventSource`, since `/chat/stream` is POST) to parse SSE frames. `src/lib/stream.ts` exposes `createSession()` and `streamChat()` generators that yield `RealStreamEvent` objects. The vite dev server proxies `/session` and `/chat` to `agent_server` on port 8001.

## Key files

| File | Role |
|---|---|
| `src/App.tsx` | Main chat UI: session management, SSE event handling, HITL interrupt rendering, artifact canvas |
| `src/lib/stream.ts` | SSE parsing (`sseLines` async generator), `createSession`, `streamChat` |
| `src/types.ts` | TypeScript types mirroring `agent_server`'s `models.py` SSE event schema |
| `src/components/AgentPlan.tsx` | Multi-agent execution timeline |
| `src/components/Artifacts.tsx` | Artifact panel (HTML/image/markdown/pptx cards) |
| `src/components/Hitl.tsx` | HITL interrupt card with form rendering for `missing_param` fields |
| `src/components/ui/` | Badge, button, card primitives |

## SSE event model

The frontend mirrors `models.py` event types: `stream_start`, `node_complete`, `message`, `token`, `thinking`, `status`, `artifact`, `suggestion`, `interrupt`, `error`, `stream_end`. The `interrupt` event carries an `InterruptPayload` with `fields` (for `missing_param`) or `options` (for plan review / post-WADS choice). See [HITL Contracts](../architecture/hitl-contracts.md).

## HITL rendering

The `HitlCard` component renders one input per `fields` entry for `missing_param` interrupts and replies with a `{slot: value}` dict as `resume_value`. For `plan_review` interrupts, it renders a free-text input and replies with a string.

## Presets

The app ships three preset queries: "최근 4주 4SS 수율 보여주고 열화 원인 알려줘", "원인 관계도 보여줘", "PPT 리포트로 정리해줘".

## Build

- `npm run dev` — Vite dev server (proxies to :8001)
- `npm run build` — `tsc -b && vite build`
- `npm run preview` — Vite preview

Dependencies: React 19, Tailwind CSS 4, lucide-react, react-markdown + remark-gfm.

## When to consult this page

- Adding a new SSE event type to the frontend.
- Changing HITL form rendering.
- Modifying the vite proxy configuration.

## Validation

```bash
cd 08-YieldAgent/yield_frontend && npm run build
```

No test runner is configured for this frontend; `npm run build` (tsc + vite) is the typecheck + build validation.
