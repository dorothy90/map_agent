# Yield Agent — 검증 콘솔 (React)

멀티에이전트 실행과 **HITL**(plan_review · missing_param)을 실제 백엔드(`agent_server`, :8001)로
검증하기 위한 경량 React 콘솔. Streamlit(`../app.py`)의 레이아웃·인터랙션 제약을 대체한다.

**스택**: React 19 + Vite 6 + TypeScript · Tailwind v4 + shadcn/ui(다크 테마) ·
react-markdown(메시지/markdown 아티팩트) · lucide-react(아이콘).

```bash
# 1) 백엔드: 08-YieldAgent 에서
uvicorn agent_server:app --port 8001

# 2) 프론트
npm install
npm run dev      # http://localhost:5174  (vite proxy → :8001)
npm run build    # tsc -b + vite build (strict 타입체크)
```

## 레이아웃 · 동작

- **1:3 2-pane** — 좌(1): 대화 + 멀티에이전트 **실행 타임라인**(node_complete 누적),
  우(3): **Artifacts 캔버스**(도구 출력이 카드로 누적).
- **HTML-first 아티팩트** — 도구가 사전 렌더한 HTML(`yield_table`·`yield_scatter`·`yield_cummap`·
  wads 등)을 **sandbox iframe** 으로 충실히 렌더. `markdown`→react-markdown, `image`→img,
  `pptx`→다운로드 카드(`/download/pptx/...`).
- **HITL 카드**
  - `plan_review`: 계획 마크다운 + [그대로 실행] / 자유텍스트 수정 / [취소] → `resume_value`(str).
  - `missing_param`: `fields[]` 입력 폼(+`options` 빠른선택) → `{slot: value}` **dict** 로 resume.
- 단일 세션: 앱 시작 시 `POST /session` 1개 생성. 도메인 용어는 **fail** 통일.

## 백엔드 계약 (`../models.py`, `../agent_server.py`)

`POST /chat/stream` `{query, session_id, resume_value?}` → SSE:
`stream_start` / `node_complete` / `message` / `token` / `thinking` / `status` /
`artifact{artifact_type: html|image|markdown|pptx, data}` / `suggestion` /
`interrupt{interrupt_type, param, message, options, fields}` / `error` / `stream_end`.
프록시 경유라 CORS 무관.

## 파일

| 파일 | 역할 |
| --- | --- |
| `src/types.ts` | 실제 SSE 이벤트(`RealStreamEvent`) + artifact/interrupt 타입 |
| `src/lib/stream.ts` | `sseLines` SSE 파서 + `createSession` + `streamChat` |
| `src/App.tsx` | 스트림 → UI 상태 reduce, 1:3 셸, HITL resume(str/dict) |
| `src/components/Artifacts.tsx` | artifact 렌더러(iframe / markdown / image / pptx) |
| `src/components/Hitl.tsx` | plan_review · missing_param HITL 카드 |
| `src/components/AgentPlan.tsx` | 멀티에이전트 실행 타임라인 |
| `src/components/ui/*` | shadcn/ui 프리미티브 |
| `src/index.css` | Tailwind v4 + shadcn 토큰(다크 테마) |
| `src/lib/utils.ts` | `cn()` 클래스 머지 헬퍼 |
