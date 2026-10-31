# Hermes 동일 이식 기준 문서

> 작성 기준: **정적 소스 분석** · 작성일: **2026-10-24 Asia/Seoul** · **코드 변경 없음**.
> **D01~D13 모두 결정됨(2026-10-24 대환 님)** — 버전 5894e750, gateway 전체 범위. 개인 스킬은 사용자별 Mongo·본인만 사용·사전 검토 없음·사용 감사 기록(D12). 개인 MCP는 HTTP(Streamable HTTP)만 자유 등록, stdio·SSE 제외, 자격증명은 사용자별 Mongo 암호화 저장(키 분리), 감사 로그는 Mongo 별도 컬렉션·결과 본문 길이만 기록(D13). 남은 미확인·후속은 6.1 참조. 사내 플랫폼 보안은 **대환 님 전제(미검증)**.
> 이번 동기화 작업은 이 문서만 보강했다. PR-1은 대환 님 승인 하에 구현·검증 완료되었다는 사용자 제공 상태를 반영하며, 이번 작업에서 재실행한 것은 아니다. 서버·DB·LLM·브라우저 실행, 설치 및 배포는 수행하지 않았다. 아래 검증 항목은 향후 이식의 완료 조건이며, 실행 통과를 의미하지 않는다.
> 마일스톤 1 구현 계획(안)과 구현 전 합의 결과는 7절

## 목차

1. [목표와 버전 기준](#1-목표와-버전-기준)
2. [현재 계약과 화면 차이](#2-현재-계약과-화면-차이)
3. [층별 이식 범위](#3-층별-이식-범위)
   - [3.1 gateway 전체 계약 대응표 — RPC](#31-gateway-전체-계약-대응표--rpc)
     - [3.1.1 legacy/TUI 등록 메서드 254개](#311-legacytui-등록-메서드-254개)
     - [3.1.2 authority 계약 48개와 추가 경계](#312-authority-계약-48개와-추가-경계)
   - [3.2 서버→클라이언트 이벤트와 요청](#32-서버클라이언트-이벤트와-요청)
     - [3.2.1 생성 계약의 event type 78개](#321-생성-계약의-event-type-78개)
     - [3.2.2 authority에서 추가로 발행하는 event type 8개](#322-authority에서-추가로-발행하는-event-type-8개)
     - [3.2.3 요청성 메시지 16개](#323-요청성-메시지-16개)
   - [3.3 REST/HTTP 라우터와 WebSocket 전체 목록](#33-resthttp-라우터와-websocket-전체-목록)
     - [3.3.1 WebSocket 엔드포인트 8개](#331-websocket-엔드포인트-8개)
     - [3.3.2 직접 정의와 외부 mount의 집계 경계](#332-직접-정의와-외부-mount의-집계-경계)
   - [3.4 Yield 미해당 기능의 명시적 비활성 처리 — 제안](#34-yield-미해당-기능의-명시적-비활성-처리--제안)
   - [3.5 사내 다중 사용자 서비스 관점의 기능 분류 — 제안](#35-사내-다중-사용자-서비스-관점의-기능-분류--제안)
   - [3.6 전체 gateway 이식의 어려운 부분과 위험 — 제안](#36-전체-gateway-이식의-어려운-부분과-위험--제안)
   - [3.7 개인 스킬·메모리·SOUL 조사와 저장 변경 제안](#37-개인-스킬메모리soul-조사와-저장-변경-제안)
   - [3.8 개인 MCP 차단 경계와 연결 제안](#38-개인-mcp-차단-경계와-연결-제안)
4. [유지할 의도적 예외와 확장 계약](#4-유지할-의도적-예외와-확장-계약)
   - [4.1 확장 이벤트 공통 규칙 — 미구현 제안](#41-확장-이벤트-공통-규칙--미구현-제안)
   - [4.2 확장별 payload — 미구현 제안](#42-확장별-payload--미구현-제안)
   - [4.3 답변·초안·확정 정책 — B안 확정](#43-답변초안확정-정책--b안-확정)
5. [P0~P3 실행 계획과 호환 유지](#5-p0p3-실행-계획과-호환-유지)
   - [5.1 서비스 마일스톤 재조정 — 제안](#51-서비스-마일스톤-재조정--제안)
6. [확정 결정과 열린 결정](#6-확정-결정과-열린-결정) — D01~D13 확정·미확인 후속 분리
   - [6.1 남은 미확인·후속 항목](#61-남은-미확인후속-항목)
7. [마일스톤 1 구현 계획(안)과 구현 전 합의 결과](#7-마일스톤-1-구현-계획안과-구현-전-합의-결과)
   - [7.1 마일스톤 1 구현 계획(안) 요약](#71-마일스톤-1-구현-계획안-요약)
   - [7.2 구현 전 합의 결과](#72-구현-전-합의-결과)
   - [7.3 착수 조건](#73-착수-조건)
   - [7.4 2026-10-24 분류 리뷰 결과(대환 님 승인 5건)](#74-2026-10-24-분류-리뷰-결과대환-님-승인-5건)

근거 약칭은 아래 루트의 `파일:라인`을 뜻한다. H의 줄 번호는 확인한 clone HEAD `5894e750`, Y/E/F는 작성 시점의 작업 트리 기준이다. 기존 uncommitted 변경도 현재 상태에 포함되며, 해당 변경을 이 문서에서 수정하거나 승인하지 않는다.

| 약칭 | 경로 / 기준 |
|---|---|
| H | `/tmp/hermes-agent-ro` — 읽기 전용 clone, HEAD `5894e750` 직접 확인. 이후 움직이는 main을 자동 추종한다는 뜻이 아님 |
| Y | `/Users/daehwankim/yield-agent/08-YieldAgent` |
| E | `Y/harness/engine` — 현재 번들 pin `5894e750c3dba980d43b25aa29e3958149ba2c61` (`E/UPSTREAM.json:3`, `Y/harness/native_hermes.py:26`); 이전 pin `e473f5a9`는 회귀 비교점 |
| F | `Y/harness_console/src` |

## 1. 목표와 버전 기준

**동일 이식**은 선택한 Hermes 커밋의 실제 소스·계약·Desktop 컴포넌트를 가져오고, 원본 동작을 연결하는 것이다. 비슷한 에이전트 루프나 채팅 화면을 새로 구현하는 것은 동일 이식으로 인정하지 않는다. 이식 파일의 원본 경로·커밋·해시·로컬 차이를 추적하며, 변경은 명시한 Yield 확장과 호스트 어댑터로 제한한다.

Mongo, 격리 subprocess, 제한된 도구 등 예외가 남으므로 **전체 시스템이 Hermes와 완전히 동일하다는 주장은 하지 않는다**. 완료 판정은 “선택 버전의 소스 이식 + 합의한 계약 호환 + 아래 예외” 단위로 한다. LLM 응답 문장의 일치가 아니라 실행·상태·이벤트·재연결 의미의 일치를 검증한다.

| 기준 | 역할 | 확인 범위 / 영향 | 결정 상태 |
|---|---|---|---|
| 최신 main에서 확인한 `5894e750` 고정 | 엔진·gateway·생성 계약·Desktop UI **전 층의 이식 기준** | 도구별 timeout 및 terminal 승인 batch 구현 확인(`H/agent/tool_dispatch_helpers.py:107`, `H/agent/tool_executor.py:1865`). 엔진·gateway·UI를 함께 갱신하고 현행 패치를 재검토; 전체 구현 비용은 **미확인** | **결정됨 — 2026-10-24, 대환 님** |
| 이전 pin `e473f5a9` | **회귀 비교점**으로 보존; 현재 번들은 `5894e750` | 현재 pin은 `E/UPSTREAM.json:3`에 기록. 이전 pin의 Desktop/API가 최신 계약과 같은지는 **미확인** | **결정됨 — 2026-10-24, 대환 님**; D01에 따라 현재 번들은 `5894e750` |

**결정됨 — 2026-10-24, 대환 님:** 버전은 `5894e750`으로 전 층 통일하고 `/api/ws` 호환 대상은 **gateway 전체**로 한다. 3.1~3.3장의 모든 메서드·이벤트·요청·부가 HTTP/WS 표면에 대응 분류를 부여한다. 실제 기능 활성화와 미지원 예외는 분류 제안 및 6장의 남은 결정에 따른다. 최신 UI와 구 pin 엔진의 혼합은 최종 baseline이 아니며, 단계적 이식 중에도 별도 runtime/contract 식별로 구별한다. `PATCHES.diff` 제거만으로 최신 버전이 되지는 않는다. 위 최신 기능의 구 pin 부재 여부는 P0의 커밋 간 diff로 확정한다.

## 2. 현재 계약과 화면 차이

| 항목 | 직접 확인한 현재 사실 | 이식 기준에 미치는 영향 / 근거 |
|---|---|---|
| Hermes 채팅 API | `/api/ws` JSON-RPC, `event` 알림의 `params.type/session_id/payload` | `H/hermes_cli/web_routers/chat_ws.py:702`, `H/tui_gateway/server.py:717` |
| 세션 REST | 목록 `/api/sessions`, 메시지 `/api/sessions/{session_id}/messages` | 목록은 `H/hermes_cli/web_routers/sessions.py:182`, 메시지는 `:739`. 메시지 페이지·압축 조상 이력 의미도 보존해야 함 (`:752`) |
| Desktop 제출 | `prompt.submit` 호출과 admission 실패 분기 | 단순 `/runs` 별칭으로 치환할 수 없음 (`H/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:1159`) |
| 원본 이벤트 | `message.delta/interim/complete`, `tool.start/progress/complete` | 생성 계약 `H/apps/shared/src/gateway-contract.generated.ts:7000`, `:7100`. payload까지 이식 대상 |
| clarify·승인 | legacy는 서버 요청 `clarify`/`approval`에 대한 JSON-RPC 응답; authority는 `clarify.request`/`approval.request` 이벤트와 `clarify.respond`/`approval.respond` RPC | 기존 생성 계약 근거 `H/apps/shared/src/gateway-contract.generated.ts:6906`, `:6777`, 주석 `H/tui_gateway/contracts/canonical_sessions.py:229` 유지. 실제 authority 발행 확인: `H/gateway/session_pending_controls.py:38`, :51. 두 wire를 구별하며 전체 목록은 3.2장 |
| 재연결 | 세션별 seq와 bounded replay, `session.events.since`; truncated이면 상태 재조회 | `H/tui_gateway/event_replay.py:1`, `H/apps/shared/src/gateway-contract.generated.ts:6372`. run별 sequence와 같은 카운터가 아님 |
| 현행 Yield API | `POST /runs`, `/runs/{run_id}`, 하위 `/events` SSE·`/cancel`·`/input`, `/harness/sessions/{session_id}`, 결과·artifact 조회 | `Y/harness/router.py:86`, `:95`, `:100`, `:119`, `:125`, `:148`, `:157`, `:167` |
| 현행 저장 이벤트 | run 레코드에 events 저장, `{run_id,type,payload,sequence,event_id}` | `Y/harness/store.py:210`, `:299`. 세션 replay로 옮길 때 run 경계를 넘어 순서와 중복 방지 필요 |
| 답변과 결과 혼합 | `answer_text`에 서술, `answer`에는 서술+결과 마크다운. 조회 조건 JSON·표·링크 포함 | `Y/harness/native_hermes.py:528`, `Y/harness/completion.py:64`, `:71`, `:84`. `answer_text`는 저장하지만 public_run에 없음 (`Y/harness/control.py:199`, `Y/harness/router.py:45`) |
| 답변 스트리밍 | worker는 step·interim만 연결하고 `stream_delta_callback`은 연결하지 않음 | `Y/harness/native_worker.py:99`~`:106`, `:150`. UI만 바꿔도 native 답변 delta가 생기지 않음. D06 B안 확정: 연결하더라도 검증 전에는 클라이언트에 공개하지 않고 도구 진행·검증 상태만 표시 |
| 화면 중복·배치 | answer 마크다운과 구조화 표를 모두 렌더링; Timeline은 답변 위, 기본 open | `F/RunView.tsx:45`, `:57`, `:59`, `F/Timeline.tsx:19` |
| 넓은 표·검증 상태 | 모든 `page.columns`를 렌더링. 30열 고정값은 아님. verify fail/unknown이 partial로 저장되고 partial은 “일부 완료” 표시 | `F/Artifacts.tsx:64`, `Y/harness/native_hermes.py:492`, `F/core.ts:4`. 검증 판정과 자료 완전성을 분리해야 함 |
| 이식할 UI | `H/web` Chat은 xterm PTY, DOM 메시지 UI 원본은 Desktop. assistant-ui·Streamdown·nanostores 사용 | `H/web/src/pages/ChatPage.tsx:7`, `:12`, `:23`; `H/apps/desktop/package.json:81`, `:97`, `:136`, `:151`. PTY 화면을 DOM 메시지 컴포넌트로 그대로 가져올 수 없음 |
| Desktop 결합 | 제출 로직이 여러 store에 의존하고 gateway는 `window.hermesDesktop` 사용 | `H/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:24`, `H/apps/desktop/src/store/gateway.ts:471`. 컴포넌트뿐 아니라 store·bridge 경계도 이식 범위 |
| 별도 API server | 기본 8642, `/v1/runs` 계열은 별도 표면 | `H/gateway/platforms/api_server.py:4`, `:65`, `:156`. 확인한 Desktop 제출은 `prompt.submit`이므로 `/v1/runs`를 Desktop 계약의 대체 기준으로 삼지 않음 |

## 3. 층별 이식 범위

상태는 **동일**(명시한 비교 단위의 원본 구현 사용), **부분**(원본 있으나 연결·설정 제한), **다름**(별도 구현), **없음**(native 연결 없음)으로 표시한다. S/M/L은 상대 작업량의 **계획 추정**이며 일정 약속이 아니다. S=한 경계, M=여러 모듈, L=상태·복구·다중 계층 변경. 목표 버전은 **5894e750으로 결정됨**이며 전 층에 적용한다. API 호환 대상은 채팅 subset이 아닌 **gateway 전체**다. 정책은 D03~D13의 **결정됨 — 2026-10-24, 대환 님**을 적용한다. 아래 구현 방법·작업량은 **제안**이며 미확인·후속은 6.1에 정리한다.

| 층 | 현재 상태 | 목표 상태 | 이식 방법 | 작업량 |
|---|---|---|---|---|
| 에이전트 루프 | **동일(pin 코어 기준)**: 원본 AIAgent 호출, 호스트 실행은 다름 (`Y/harness/native_worker.py:86`, `:150`) | 5894e750의 실제 루프 + 명시한 호스트 예외 | 소스 동기화·해시 대조; subprocess IPC로 원본 실행을 감쌈. Hermes in-process 구성과 구별 (`H/gateway/session_execution.py:161`, `:191`) | L |
| 프롬프트·컨텍스트·압축 | **부분**: native.md·요청시각·임시 CWD, SOUL off; host context_tokens 미전달 (`Y/harness/native_hermes.py:364`, `:394`, `:401`; `Y/harness/native_worker.py:105`; `Y/harness/config.py:20`) | 원본 조립·압축 + 시스템 규칙 아래 사용자별 SOUL(D08) | 원본 로더 연결 제안. SOUL은 gate·도구 제한을 덮어쓸 수 없고 CWD는 서버 고정(사용자 지정 불가); context 크기 매핑은 미확인 | L |
| 모델 재시도·fallback | **부분**: 원본 내부 로직 사용, 호스트가 fallback·credential pool을 명시 전달하지 않음 (`Y/harness/native_worker.py:99`; `E/agent/agent_init.py:435`, `:846`) | 같은 endpoint 내 재시도·대체 모델 fallback만 + 공유 예산(D07) | 선택 버전의 설정 경로 연결; 다중 provider 제외·사용자 임의 모델/provider 설정 비활성 정책과 예산 호환 검증. 원본 fallback 연결 예: `H/gateway/session_execution.py:180` | M |
| 도구 레지스트리 | **부분**: 원본 register, Yield handler 구현 (`Y/harness/tools/registry.py:76`; `Y/harness/native_worker.py:84`) | 원본 등록 계약 + Yield 도구 | schema·handler·효과·결과 provenance를 어댑터로 제공; plugin 발견 제한 유지 | M |
| clarify·승인 | **없음(native 연결)**: IPC는 결과 응답 중심, AIAgent에 요청 콜백 미연결. 폼은 존재 (`Y/harness/native_worker.py:30`, `:99`; `F/RunView.tsx:49`) | Hermes clarify/승인 계약·화면 동일 이식(D05) | 허용 도구의 효과 있는 호출에만 승인 적용; 항상 허용은 사용자별 저장·감사 기록, timeout·재접속 복원 지원. sudo·secret 거부 및 terminal/셸/파일/browser 비활성은 승인으로 우회 불가. IPC 연결은 제안 | L |
| 병렬 도구 | **부분**: 원본 planner 유지, Yield 도구는 parallel-safe 목록에 없음 (`E/agent/tool_dispatch_helpers.py:33`, `:190`) | 병렬 도구 허용·동시 개수 제한(D09) | 원본과 동일하게 배치당 최대 8(`H/agent/tool_executor.py:134`, :241, :248), 설정 조정 가능 합의(구현 방식은 제안). 원본 직렬화 규칙 유지; 현재 Yield 쪽 적용 방식은 미확인(7.2) | M |
| delegate | **부분**: stateless 채널, 원본 child 생성 사용 (`Y/harness/native_worker.py:87`, `:126`) | delegate 1차 비활성·계약 수신 유지(D09) | 마일스톤 2에서 예산·감사 설계 후 재판단. 현재 delegation 도구셋 실제 동작은 미확인; 재활성 시 수명·예산 연결은 제안 | L |
| 세션·SessionDB | **다름**: Mongo/GridFS와 native_messages 재주입 (`Y/harness/store.py:30`; `Y/harness/native_hermes.py:64`, `:531`) | 원본 세션 의미 + Mongo가 유일한 저장 기준(D04) | SessionDB 원본 인터페이스를 Mongo 위에 맞춤(병행 사본·이중 권위 아님); 이력·압축·seq·복구 어댑터 제안 (`H/gateway/session_execution.py:177`) | L |
| 메모리·스킬 | **부분**: principal별 profile, 실행별 skill 복사 (`Y/harness/native_hermes.py:314`, `:351`, `:368`, `:577`) | 사용자별 메모리·개인 스킬 Mongo 분리 저장(D08·D12) | 스킬은 본인만 사용·사전 검토 없음·사용 감사 기록. 원본 로더·snapshot/sync·격리 검증은 제안; 팀 공유는 나중 | M |
| 취소·steer | **다름**: 프로세스 그룹 종료, steer=취소 후 새 run (`Y/harness/native_hermes.py:536`; `Y/harness/control.py:261`) | 원본 제어 계약 + 강제 종료 fallback | cooperative interrupt를 IPC로 전달; 같은 실행 steer는 기본 비활성 제안; run 분리 의미는 호환 어댑터에서 명시 | L |
| 예산 | **다름**: HTTP 호출수·토큰 예약·deadline (`Y/harness/native_budget.py:44`, `:88`; `Y/harness/native_worker.py:73`) | 원본 예산 의미 + 더 엄격한 Yield 공유 상한 | 같은 endpoint 재시도·검증의 admission 계상 제안(D07); delegate는 1차 비활성, M2 재판단(D09) | M |
| 스트리밍 콜백 | **부분**: step/interim만 연결 (`Y/harness/native_worker.py:106`) | B안: 검증 전 답변 숨김·도구 진행/검증 상태 표시·확정 답변만 공개(D06) | 원본 delta/interim/complete의 내부 매핑·보류와 callback→IPC→gateway 연결은 제안; native 답변 delta를 연결하더라도 검증 전에는 클라이언트에 공개하지 않음 | M |
| API | **다름**: run REST/SSE (`Y/harness/router.py:86`, `:148`) | **gateway 전체** `/api/ws` JSON-RPC + 전체 부가 REST/WS 대응 행렬 | 5894e750 원본 gateway·authority·생성 계약·라우터 이식; 3.1~3.4의 지원/비활성 응답까지 연결, 실행/저장 경계는 호스트 어댑터; 구 API 병행 | L |
| 프론트 | **다름**: React state+react-markdown (`F/useRun.ts:4`; `F/Markdown.tsx:1`) | 채팅 중심 원본 컴포넌트의 웹 UI 우선·opt-in, Desktop 앱은 나중(D10) | D03 허용 의존성 사용, @nous-research/ui fonts.css·번들 woff2 제외/대체; 법무 확인은 별도 선행 확인 항목. Electron 의존을 어댑터 뒤로 분리하는 이식 방법은 제안 | L |

### 3.1 gateway 전체 계약 대응표 — RPC

**범위 결정됨(2026-10-24, 대환 님): `5894e750`의 gateway 전체를 계약 검토·대응 대상으로 삼는다.** 채팅 subset으로 축소하지 않는다. 전체 기능을 무조건 실행 허용한다는 뜻은 아니며, 미해당 기능도 이름·스키마·거부 응답·UI 처리를 추적한다. 아래 ③ 비활성 항목은 D11 절차 아래의 기준이며, 기준을 벗어나는 신규 예외는 이유·범위·되돌리는 방법을 기록하고 대환 님 승인 후에만 구현한다. 지원 제외를 성공 응답으로 위장하거나 “Hermes 전체 기능 동일”이라고 표시하지 않는다.

이 절의 구현 분류·작업량은 **제안**이며 명시한 정책은 확정 결정이다: **① Yield 구현 필요**, **② Hermes 코드 재사용(어댑터/저장 경계만 연결)**, **③ Yield에 해당 없음 → 스텁/비활성(capability 응답에 `unsupported`)**. S/M/L은 3장 정의의 상대 작업량이다. ③/S는 기본 거부 처리만의 추정이며 보안·UI·호환 검증은 P2c/P3에 별도 계상한다. ②도 전체 기능이 현재 연결됐다는 뜻은 아니다. 세부 액션별 허용 여부·구현 비용은 **미확인**이며 P0/P2c에서 검증한다. 이 계획 분류와 별개로 M1 이름별 분류는 7.1·7.4의 **PR-1 manifest 기준 확정치(2026-10-24)**를 우선한다.

**집계 방법:** `python3 -B - <<'PY'`의 표준 라이브러리 `ast.parse(Path.read_text())`로 `H/tui_gateway/**/*.py`의 `@method`, wrapper와 등록 팩토리 호출을 추출하고 상수 테이블·반복 등록을 펼쳐 이름의 집합을 셌다(소스 import/실행 없음). `grep -RnE 'register_method|method\\(|_rpc\\(' H/tui_gateway --include='*.py'`로 누락 경계를 확인하고 `H/apps/shared/src/gateway-contract.generated.ts`의 `RpcMethods`(:6005), `CanonicalRpcMethods`(:6774)와 집합 차이를 비교했다.

- **legacy/TUI dispatcher 등록 254개**, 생성 `RpcMethods` **254개**, 누락·초과·중복 **0개**. `server.py`의 직접 상수 `@method`는 0개이며, 등록 seam은 `H/tui_gateway/server.py:906`, split 설치는 `:2170` 및 `H/tui_gateway/method_ctx.py:65`.
- 검색 시 반드시 포함한 간접 등록: `_rpc/_scoped_rpc/_mcp_rpc`(`H/tui_gateway/methods_tools.py:84`, `:109`), `_SIMPLE_RPCS` 6개(:263, :277), learning 반복 3개(:1356), billing view/route(`H/tui_gateway/methods_session.py:1844`, :1881), project mutator 4개(`H/tui_gateway/methods_projects.py:61`, :71), group passthrough(`H/tui_gateway/methods_groups.py:464`), correction 2개(`H/tui_gateway/methods_session_interrupt.py:171`). groups·interrupt 모듈도 상위 모듈에서 설치한다(`H/tui_gateway/methods_bot_relay.py:144`, `H/tui_gateway/methods_session.py:2338`).
- **authority 계약 48개**는 254개와 **19개 이름 중복**, **29개 추가**이므로 전체 이름의 합집합은 **283개**. 단일 dispatcher에 302개가 등록된다는 뜻은 아니다. authority 유무가 분기 조건(`H/tui_gateway/ws.py:357`), authority dispatch 등록은 `H/gateway/session_controls.py:170` 및 그 함수가 연결하는 handler 모듈이다. 두 모드의 같은 이름도 params/result가 다를 수 있다.
- **독립 `memory.*` RPC는 등록 목록에 없다.** 메모리는 `command.dispatch`의 memory 분기(`H/tui_gateway/methods_tools.py:972`), REST `/api/memory` 및 provider 라우트, 도구 경계로 다룬다. `cron.manage`, `skills.manage`, `plugins.manage` 등 다중 액션은 메서드 이름 1개로 센다. 임의의 하위 메서드를 만들지 않았다.

#### 3.1.1 legacy/TUI 등록 메서드 254개

| 카테고리 / 수 | 전체 이름과 근거 | 대응·작업량·이유(구현 제안·정책은 D03~D13 확정) |
|---|---|---|
| agents / 1 | `agents.list` (`H/tui_gateway/methods_tools.py:273`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| approval / 3 | `approval.pending` (`H/tui_gateway/methods_prompt.py:1305`)<br>`approval.received` (`H/tui_gateway/methods_prompt.py:1314`)<br>`approval.respond` (`H/tui_gateway/methods_prompt.py:1325`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| billing / 6 | `billing.auto_reload` (`H/tui_gateway/methods_session.py:1912`)<br>`billing.charge` (`H/tui_gateway/methods_session.py:1895`)<br>`billing.charge_status` (`H/tui_gateway/methods_session.py:1899`)<br>`billing.keep_going_on_credits` (`H/tui_gateway/methods_session.py:1914`)<br>`billing.state` (`H/tui_gateway/methods_session.py:1829`)<br>`billing.step_up` (`H/tui_gateway/methods_session.py:1918`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| bot_relay / 4 | `bot_relay.deliver` (`H/tui_gateway/methods_bot_relay.py:67`)<br>`bot_relay.outbox.drain` (`H/tui_gateway/methods_bot_relay.py:56`)<br>`bot_relay.reply` (`H/tui_gateway/methods_bot_relay.py:124`)<br>`bot_relay.roster.sync` (`H/tui_gateway/methods_bot_relay.py:31`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| browser / 1 | `browser.manage` (`H/tui_gateway/methods_tools.py:1202`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| clarify / 1 | `clarify.lock` (`H/tui_gateway/methods_prompt.py:1267`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| cli / 1 | `cli.exec` (`H/tui_gateway/methods_tools.py:445`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| client / 1 | `client.capabilities` (`H/tui_gateway/methods_voice.py:521`) | **① / M · 제안**: 지원 행렬 협상과 인증된 호스트 capability 연결 |
| clipboard / 1 | `clipboard.paste` (`H/tui_gateway/methods_prompt.py:969`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| command / 2 | `command.dispatch` (`H/tui_gateway/methods_tools.py:1007`)<br>`command.resolve` (`H/tui_gateway/methods_tools.py:465`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| commands / 1 | `commands.catalog` (`H/tui_gateway/methods_tools.py:431`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| complete / 1 | `complete.path` (`H/tui_gateway/methods_complete.py:229`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| complete / 1 | `complete.slash` (`H/tui_gateway/methods_complete.py:284`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| config / 3 | `config.get` (`H/tui_gateway/methods_config.py:287`)<br>`config.set` (`H/tui_gateway/methods_config_set.py:507`)<br>`config.show` (`H/tui_gateway/methods_tools.py:1216`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결 |
| connection / 1 | `connection.respond` (`H/tui_gateway/methods_connectors.py:356`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| connectors / 11 | `connectors.accounts` (`H/tui_gateway/methods_connectors_account.py:93`)<br>`connectors.accounts.remove` (`H/tui_gateway/methods_connectors_account.py:168`)<br>`connectors.accounts.rename` (`H/tui_gateway/methods_connectors_account.py:130`)<br>`connectors.catalog` (`H/tui_gateway/methods_connectors_account.py:76`)<br>`connectors.connect` (`H/tui_gateway/methods_connectors.py:286`)<br>`connectors.list` (`H/tui_gateway/methods_connectors.py:280`)<br>`connectors.operation.status` (`H/tui_gateway/methods_connectors.py:331`)<br>`connectors.operation.wake` (`H/tui_gateway/methods_connectors.py:343`)<br>`connectors.policy.get` (`H/tui_gateway/methods_connectors_account.py:202`)<br>`connectors.policy.set` (`H/tui_gateway/methods_connectors_account.py:232`)<br>`connectors.tools` (`H/tui_gateway/methods_connectors_account.py:51`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| cron / 1 | `cron.manage` (`H/tui_gateway/methods_tools.py:1305`) | **① / L · 제안**: 정기 리포트 설계 후 구현; 소유권·실행 시 권한·예산·실패 처리·알림 전달 선행 |
| delegation / 2 | `delegation.pause` (`H/tui_gateway/methods_session.py:2204`)<br>`delegation.status` (`H/tui_gateway/methods_session.py:2196`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결; **D09 확정: 1차 비활성, 계약 수신 유지, M2에서 예산·감사 설계 후 재판단; 현재 delegation 도구셋 실제 동작 미확인** |
| diagnostics / 1 | `diagnostics.share_nous` (`H/tui_gateway/methods_config.py:504`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| display / 9 | `display.install` (`H/tui_gateway/methods_display.py:185`)<br>`display.lease.acquire` (`H/tui_gateway/methods_display.py:234`)<br>`display.lease.release` (`H/tui_gateway/methods_display.py:247`)<br>`display.observe` (`H/tui_gateway/methods_display.py:149`)<br>`display.start` (`H/tui_gateway/methods_display.py:81`)<br>`display.status` (`H/tui_gateway/methods_display.py:50`)<br>`display.stop` (`H/tui_gateway/methods_display.py:93`)<br>`display.switchSandboxImage` (`H/tui_gateway/methods_display.py:171`)<br>`display.thumbnail` (`H/tui_gateway/methods_display.py:60`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| file / 1 | `file.attach` (`H/tui_gateway/methods_prompt.py:1110`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| free_tier / 5 | `free_tier.ack_notice` (`H/tui_gateway/methods_free_tier.py:93`)<br>`free_tier.challenge_result` (`H/tui_gateway/methods_free_tier.py:119`)<br>`free_tier.claim_nudge` (`H/tui_gateway/methods_free_tier.py:105`)<br>`free_tier.provision` (`H/tui_gateway/methods_free_tier.py:66`)<br>`free_tier.status` (`H/tui_gateway/methods_free_tier.py:20`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| gateway / 1 | `gateway.capabilities` (`H/tui_gateway/methods_voice.py:513`) | **① / M · 제안**: 지원 행렬 협상과 인증된 호스트 capability 연결 |
| groups / 18 | `groups.approve` (`H/tui_gateway/methods_groups.py:437`)<br>`groups.capabilities` (`H/tui_gateway/methods_groups.py:216`)<br>`groups.create` (`H/tui_gateway/methods_groups.py:357`)<br>`groups.demote` (`H/tui_gateway/methods_groups.py:518`)<br>`groups.disband` (`H/tui_gateway/methods_groups.py:396`)<br>`groups.list` (`H/tui_gateway/methods_groups.py:344`)<br>`groups.log` (`H/tui_gateway/methods_groups.py:487`)<br>`groups.peer.invite` (`H/tui_gateway/methods_groups.py:248`)<br>`groups.peer.register` (`H/tui_gateway/methods_groups.py:295`)<br>`groups.peer.revoke` (`H/tui_gateway/methods_groups.py:280`)<br>`groups.promote` (`H/tui_gateway/methods_groups.py:506`)<br>`groups.rename` (`H/tui_gateway/methods_groups.py:483`)<br>`groups.replica_state` (`H/tui_gateway/methods_groups.py:500`)<br>`groups.replicate` (`H/tui_gateway/methods_groups.py:494`)<br>`groups.retry` (`H/tui_gateway/methods_groups.py:448`)<br>`groups.send` (`H/tui_gateway/methods_groups.py:381`)<br>`groups.state` (`H/tui_gateway/methods_groups.py:367`)<br>`groups.stop` (`H/tui_gateway/methods_groups.py:429`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| handoff / 3 | `handoff.fail` (`H/tui_gateway/methods_session.py:1425`)<br>`handoff.request` (`H/tui_gateway/methods_session.py:1378`)<br>`handoff.state` (`H/tui_gateway/methods_session.py:1417`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| i18n / 2 | `i18n.catalog` (`H/tui_gateway/methods_i18n.py:32`)<br>`i18n.languages` (`H/tui_gateway/methods_i18n.py:22`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| image / 4 | `image.attach` (`H/tui_gateway/methods_prompt.py:993`)<br>`image.attach_bytes` (`H/tui_gateway/methods_prompt.py:1021`)<br>`image.detach` (`H/tui_gateway/methods_prompt.py:1133`)<br>`image.generate` (`H/tui_gateway/methods_images.py:54`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| input / 1 | `input.detect_drop` (`H/tui_gateway/methods_prompt.py:1148`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| insights / 1 | `insights.get` (`H/tui_gateway/methods_tools.py:1110`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| learning / 4 | `learning.delete` (`H/tui_gateway/methods_tools.py:1356`)<br>`learning.detail` (`H/tui_gateway/methods_tools.py:1356`)<br>`learning.edit` (`H/tui_gateway/methods_tools.py:1356`)<br>`learning.frames` (`H/tui_gateway/methods_tools.py:1332`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| llm / 1 | `llm.oneshot` (`H/tui_gateway/methods_session.py:1344`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결 |
| machine / 1 | `machine.facts` (`H/tui_gateway/methods_machine.py:15`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| mcp / 11 | `mcp.catalog` (`H/tui_gateway/methods_tools.py:1444`)<br>`mcp.servers.add` (`H/tui_gateway/methods_tools.py:1492`)<br>`mcp.servers.list` (`H/tui_gateway/methods_tools.py:1464`)<br>`mcp.servers.oauth.callback` (`H/tui_gateway/methods_tools.py:1648`)<br>`mcp.servers.oauth.cancel` (`H/tui_gateway/methods_tools.py:1640`)<br>`mcp.servers.oauth.poll` (`H/tui_gateway/methods_tools.py:1633`)<br>`mcp.servers.oauth.start` (`H/tui_gateway/methods_tools.py:1608`)<br>`mcp.servers.remove` (`H/tui_gateway/methods_tools.py:1597`)<br>`mcp.servers.set_api_key` (`H/tui_gateway/methods_tools.py:1535`)<br>`mcp.servers.status` (`H/tui_gateway/methods_tools.py:1474`)<br>`mcp.servers.test` (`H/tui_gateway/methods_tools.py:1567`) | **① / L · 제안**: D13 개인 MCP 켬; 원본 등록·조회·OAuth 계약 재사용+principal별 설정/비밀 저장 연결. set_api_key는 비밀 저장 어댑터로 제한, 범용 비밀 관리 UI는 ③; stdio는 1차 범위 제외(등록·실행 불가), HTTP(Streamable HTTP) 자유 등록·자격증명 분리·감사 로그 적용(3.8); **D13 확정:** stdio·SSE 제외, 자격증명은 사용자별 Mongo 암호화 저장(키 분리), 감사는 Mongo 별도 컬렉션·결과 본문 길이만 기록. **PR-1 추가 조치:** HTTP `/api/mcp/*` 12개 모두 `denied_actions=[stdio, SSE]`, 분류 개수 불변 (`Y/harness/gateway/policy.py:166`) |
| message / 1 | `message.react` (`H/tui_gateway/methods_session.py:1312`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| model / 3 | `model.disconnect` (`H/tui_gateway/methods_complete.py:382`)<br>`model.options` (`H/tui_gateway/methods_complete.py:319`)<br>`model.save_key` (`H/tui_gateway/methods_complete.py:334`) | **② / M · 제안**: options는 관리자 허용 모델만; disconnect는 소유권 검사. save_key 및 범용 API키 UI는 ③ |
| onboarding / 4 | `onboarding.set_run` (`H/tui_gateway/methods_onboarding.py:16`)<br>`onboarding.skip` (`H/tui_gateway/methods_onboarding.py:244`)<br>`onboarding.start` (`H/tui_gateway/methods_onboarding.py:169`)<br>`onboarding.state` (`H/tui_gateway/methods_onboarding.py:11`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| paste / 1 | `paste.collapse` (`H/tui_gateway/methods_complete.py:35`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| pdf / 1 | `pdf.attach` (`H/tui_gateway/methods_prompt.py:1052`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| pet / 15 | `pet.cancel` (`H/tui_gateway/methods_session.py:1681`)<br>`pet.cells` (`H/tui_gateway/methods_session.py:1541`)<br>`pet.disable` (`H/tui_gateway/methods_session.py:1665`)<br>`pet.export` (`H/tui_gateway/methods_session.py:1634`)<br>`pet.gallery` (`H/tui_gateway/methods_session.py:1566`)<br>`pet.generate` (`H/tui_gateway/methods_session.py:1712`)<br>`pet.generate.status` (`H/tui_gateway/methods_session.py:1689`)<br>`pet.hatch` (`H/tui_gateway/methods_session.py:1769`)<br>`pet.info` (`H/tui_gateway/methods_session.py:1496`)<br>`pet.info.meta` (`H/tui_gateway/methods_session.py:1513`)<br>`pet.remove` (`H/tui_gateway/methods_session.py:1611`)<br>`pet.rename` (`H/tui_gateway/methods_session.py:1642`)<br>`pet.scale` (`H/tui_gateway/methods_session.py:1673`)<br>`pet.select` (`H/tui_gateway/methods_session.py:1597`)<br>`pet.thumb` (`H/tui_gateway/methods_session.py:1656`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| ping / 1 | `ping` (`H/tui_gateway/methods_voice.py:542`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| plugins / 3 | `plugins.catalog.lookup` (`H/tui_gateway/methods_plugin_catalog.py:21`)<br>`plugins.list` (`H/tui_gateway/methods_tools.py:268`)<br>`plugins.manage` (`H/tui_gateway/methods_tools.py:1862`) | **② / M · 제안**: 개인 스킬/MCP 메타데이터 목록·catalog 계약 재사용, 허용 데이터만 projection. manage의 임의 코드 설치·갱신·실행 액션은 ③; MCP 등록과 구별(D12·D13) |
| preview / 1 | `preview.restart` (`H/tui_gateway/methods_prompt.py:1220`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| process / 3 | `process.kill` (`H/tui_gateway/methods_tools.py:285`)<br>`process.list` (`H/tui_gateway/methods_tools.py:281`)<br>`process.stop` (`H/tui_gateway/methods_tools.py:265`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| profiles / 6 | `profiles.configure` (`H/tui_gateway/methods_profiles.py:386`)<br>`profiles.create` (`H/tui_gateway/methods_profiles.py:306`)<br>`profiles.describe` (`H/tui_gateway/methods_profiles.py:349`)<br>`profiles.get_asset` (`H/tui_gateway/methods_profiles.py:450`)<br>`profiles.list` (`H/tui_gateway/methods_profiles.py:282`)<br>`profiles.set_asset` (`H/tui_gateway/methods_profiles.py:412`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결 |
| project / 1 | `project.facts` (`H/tui_gateway/methods_session.py:586`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| projects / 15 | `projects.add_folder` (`H/tui_gateway/methods_projects.py:74`)<br>`projects.archive` (`H/tui_gateway/methods_projects.py:104`)<br>`projects.create` (`H/tui_gateway/methods_projects.py:91`)<br>`projects.delete` (`H/tui_gateway/methods_projects.py:111`)<br>`projects.discover_repos` (`H/tui_gateway/methods_config.py:85`)<br>`projects.for_cwd` (`H/tui_gateway/methods_projects.py:123`)<br>`projects.get` (`H/tui_gateway/methods_projects.py:86`)<br>`projects.list` (`H/tui_gateway/methods_projects.py:81`)<br>`projects.project_sessions` (`H/tui_gateway/methods_config.py:152`)<br>`projects.record_repos` (`H/tui_gateway/methods_config.py:103`)<br>`projects.remove_folder` (`H/tui_gateway/methods_projects.py:77`)<br>`projects.set_active` (`H/tui_gateway/methods_projects.py:117`)<br>`projects.set_primary` (`H/tui_gateway/methods_projects.py:78`)<br>`projects.tree` (`H/tui_gateway/methods_config.py:137`)<br>`projects.update` (`H/tui_gateway/methods_projects.py:71`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| prompt / 3 | `prompt.background` (`H/tui_gateway/methods_prompt.py:1174`)<br>`prompt.btw` (`H/tui_gateway/methods_prompt.py:1198`)<br>`prompt.submit` (`H/tui_gateway/methods_prompt.py:816`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결; prompt.background의 장기 실행은 ③, submit과 수명 제한된 btw는 별도 허용 검토 |
| reload / 2 | `reload.env` (`H/tui_gateway/methods_tools.py:267`)<br>`reload.mcp` (`H/tui_gateway/methods_tools.py:351`) | **① / M · 제안**: reload.mcp는 본인 설정·허용 도구만 재발견; reload.env는 ③, 전역 환경 갱신 금지 |
| request / 1 | `request.answer` (`H/tui_gateway/methods_prompt.py:1289`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결 |
| rollback / 3 | `rollback.diff` (`H/tui_gateway/methods_tools.py:1168`)<br>`rollback.list` (`H/tui_gateway/methods_tools.py:1123`)<br>`rollback.restore` (`H/tui_gateway/methods_tools.py:1135`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| session / 27 | `session.activate` (`H/tui_gateway/methods_session.py:1157`)<br>`session.active_list` (`H/tui_gateway/methods_session.py:1143`)<br>`session.archive` (`H/tui_gateway/methods_session.py:1251`)<br>`session.branch` (`H/tui_gateway/methods_session.py:2184`)<br>`session.branch_stored` (`H/tui_gateway/methods_session.py:489`)<br>`session.branch_whole` (`H/tui_gateway/methods_session.py:2189`)<br>`session.close` (`H/tui_gateway/methods_session.py:2177`)<br>`session.compress` (`H/tui_gateway/methods_session.py:2118`)<br>`session.context_breakdown` (`H/tui_gateway/methods_session.py:1459`)<br>`session.control` (`H/tui_gateway/methods_session_control.py:272`)<br>`session.control.read` (`H/tui_gateway/methods_session_control.py:255`)<br>`session.create` (`H/tui_gateway/methods_session.py:484`)<br>`session.delete` (`H/tui_gateway/methods_session.py:1172`)<br>`session.events.since` (`H/tui_gateway/methods_session.py:2309`)<br>`session.events.stats` (`H/tui_gateway/methods_session.py:2326`)<br>`session.history` (`H/tui_gateway/methods_session.py:1978`)<br>`session.interrupt` (`H/tui_gateway/methods_session_interrupt.py:70`)<br>`session.list` (`H/tui_gateway/methods_session.py:541`)<br>`session.most_recent` (`H/tui_gateway/methods_session.py:568`)<br>`session.redirect` (`H/tui_gateway/methods_session_interrupt.py:174`)<br>`session.resume` (`H/tui_gateway/methods_session.py:1049`)<br>`session.set_hidden` (`H/tui_gateway/methods_session.py:1281`)<br>`session.status` (`H/tui_gateway/methods_session.py:1953`)<br>`session.steer` (`H/tui_gateway/methods_session_interrupt.py:171`)<br>`session.title` (`H/tui_gateway/methods_session.py:1219`)<br>`session.undo` (`H/tui_gateway/methods_session.py:2000`)<br>`session.usage` (`H/tui_gateway/methods_session.py:1448`) | **② / L · 제안**: 원본 세션 의미 재사용, Mongo가 유일한 세션·대화 저장 기준이며 SessionDB 인터페이스를 Mongo 위에 연결(D04); session.steer/redirect의 같은 실행 변경은 ③, 취소 후 새 run 의미만 명시적 어댑터로 제공 제안 |
| session / 6 | `session.cwd.set` (`H/tui_gateway/methods_session.py:1086`)<br>`session.foreign.import` (`H/tui_gateway/methods_session_foreign.py:40`)<br>`session.foreign.list` (`H/tui_gateway/methods_session_foreign.py:9`)<br>`session.foreign.preview` (`H/tui_gateway/methods_session_foreign.py:35`)<br>`session.save` (`H/tui_gateway/methods_session.py:2145`)<br>`session.workspace.move` (`H/tui_gateway/methods_session.py:1101`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인; D08 확정: CWD 서버 고정·사용자 지정 불가. `session.save`는 로컬 파일 저장 거부로 **unsupported 확정**; 서버 다운로드 `session.export`는 stub(7.4; `Y/harness/gateway/rpc_rules.py:78`, :117) |
| setup / 2 | `setup.runtime_check` (`H/tui_gateway/methods_config.py:424`)<br>`setup.status` (`H/tui_gateway/methods_config.py:382`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| shared_metrics / 10 | `shared_metrics.desktop_daily` (`H/tui_gateway/methods_shared_metrics.py:176`)<br>`shared_metrics.desktop_dislike` (`H/tui_gateway/methods_shared_metrics.py:164`)<br>`shared_metrics.desktop_feature_use` (`H/tui_gateway/methods_shared_metrics.py:132`)<br>`shared_metrics.desktop_friction` (`H/tui_gateway/methods_shared_metrics.py:143`)<br>`shared_metrics.desktop_onboarding` (`H/tui_gateway/methods_shared_metrics.py:154`)<br>`shared_metrics.set` (`H/tui_gateway/methods_shared_metrics.py:55`)<br>`shared_metrics.slash_command` (`H/tui_gateway/methods_shared_metrics.py:86`)<br>`shared_metrics.startup_latency` (`H/tui_gateway/methods_shared_metrics.py:98`)<br>`shared_metrics.status` (`H/tui_gateway/methods_shared_metrics.py:44`)<br>`shared_metrics.update_run` (`H/tui_gateway/methods_shared_metrics.py:115`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| shell / 1 | `shell.exec` (`H/tui_gateway/methods_tools.py:1872`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| skills / 2 | `skills.manage` (`H/tui_gateway/methods_tools.py:1418`)<br>`skills.reload` (`H/tui_gateway/methods_tools.py:1424`) | **② / M · 제안**: D12 개인 스킬은 사용자별 Mongo·본인만 사용·사전 검토 없음·사용 감사 기록으로 확정; 관리/reload·버전 어댑터는 제안(3.7), 팀 공유는 나중 |
| slash / 1 | `slash.exec` (`H/tui_gateway/methods_tools.py:1027`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| spawn_tree / 3 | `spawn_tree.list` (`H/tui_gateway/methods_session.py:2265`)<br>`spawn_tree.load` (`H/tui_gateway/methods_session.py:2285`)<br>`spawn_tree.save` (`H/tui_gateway/methods_session.py:2228`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결; **D09 확정: 1차 비활성, 계약 수신 유지, M2에서 예산·감사 설계 후 재판단; 현재 delegation 도구셋 실제 동작 미확인** |
| subagent / 4 | `subagent.interrupt` (`H/tui_gateway/methods_subagents.py:81`)<br>`subagent.list` (`H/tui_gateway/methods_subagents.py:53`)<br>`subagent.steer` (`H/tui_gateway/methods_session.py:2210`)<br>`subagent.tail` (`H/tui_gateway/methods_subagents.py:105`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결; subagent.steer의 같은 실행 변경·장기 background는 ③; **D09 확정: 1차 비활성, 계약 수신 유지, M2에서 예산·감사 설계 후 재판단; 현재 delegation 도구셋 실제 동작 미확인** |
| subscription / 5 | `subscription.change` (`H/tui_gateway/methods_session.py:1881`)<br>`subscription.preview` (`H/tui_gateway/methods_session.py:1851`)<br>`subscription.resume` (`H/tui_gateway/methods_session.py:1886`)<br>`subscription.state` (`H/tui_gateway/methods_session.py:1846`)<br>`subscription.upgrade` (`H/tui_gateway/methods_session.py:1890`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| system / 1 | `system.battery` (`H/tui_gateway/methods_tools.py:249`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| terminal / 1 | `terminal.resize` (`H/tui_gateway/methods_session.py:2303`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| tools / 3 | `tools.configure` (`H/tui_gateway/methods_tools.py:1255`)<br>`tools.list` (`H/tui_gateway/methods_tools.py:271`)<br>`tools.show` (`H/tui_gateway/methods_tools.py:1236`) | **① / M · 제안**: Yield 도구 registry와 허용 정책 반영 |
| toolsets / 1 | `toolsets.list` (`H/tui_gateway/methods_tools.py:272`) | **① / M · 제안**: Yield 도구 registry와 허용 정책 반영 |
| usage / 1 | `usage.bars` (`H/tui_gateway/methods_session.py:1844`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| vault / 7 | `vault.add` (`H/tui_gateway/methods_vault.py:135`)<br>`vault.list` (`H/tui_gateway/methods_vault.py:42`)<br>`vault.lock` (`H/tui_gateway/methods_vault.py:124`)<br>`vault.remove` (`H/tui_gateway/methods_vault.py:168`)<br>`vault.source.set` (`H/tui_gateway/methods_vault.py:78`)<br>`vault.sources` (`H/tui_gateway/methods_vault.py:60`)<br>`vault.unlock` (`H/tui_gateway/methods_vault.py:102`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| verification / 1 | `verification.status` (`H/tui_gateway/methods_session.py:597`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| voice / 3 | `voice.record` (`H/tui_gateway/methods_voice.py:808`)<br>`voice.toggle` (`H/tui_gateway/methods_voice.py:772`)<br>`voice.tts` (`H/tui_gateway/methods_voice.py:884`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| wake / 6 | `wake.feed` (`H/tui_gateway/methods_voice.py:685`)<br>`wake.pause` (`H/tui_gateway/methods_voice.py:625`)<br>`wake.resume` (`H/tui_gateway/methods_voice.py:638`)<br>`wake.start` (`H/tui_gateway/methods_voice.py:549`)<br>`wake.status` (`H/tui_gateway/methods_voice.py:646`)<br>`wake.stop` (`H/tui_gateway/methods_voice.py:610`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |

#### 3.1.2 authority 계약 48개와 추가 경계

아래 목록은 `canonical_method` 선언(반복 선언 포함) **48개**를 생성 타입과 교차 검증했으며 이름 차이는 **0개**다. 이 중 추가 29개는 `a2a.forward`, `bot_relay.settle`, `clarify.respond`, `command.observe`, `cron.cancel`, `cron.recover`, `cron.status`, `cron.submit`, `groups.attachment.download`, `groups.attachment.upload`, `groups.discard`, `kanban.run`, `prompt.cancel`, `prompt.receipt`, `prompt.replace_queued`, `prompt.resolve_unknown`, `prompt.steer_queued`, `rollback.session_diff`, `runtime.describe`, `session.automation`, `session.detach`, `session.export`, `session.info`, `session.mutate`, `session.prune`, `session.stats`, `worker.adopt`, `worker.persist`, `worker.register`이다. 나머지 19개는 위 legacy 목록과 같은 이름의 별도 계약이다. 실제 인증 주체·profile·authority 설정에서 각 메서드의 호출 가능 여부는 **미확인**이며 등록/계약 존재와 실행 허용을 구별한다.

| 카테고리 / 수 | 전체 이름과 근거 | 대응·작업량·이유(구현 제안·정책은 D03~D13 확정) |
|---|---|---|
| a2a / 1 | `a2a.forward` (`H/tui_gateway/contracts/canonical.py:457`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| approval / 1 | `approval.respond` (`H/tui_gateway/contracts/canonical_sessions.py:319`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| bot_relay / 2 | `bot_relay.deliver` (`H/tui_gateway/contracts/canonical_projections.py:101`)<br>`bot_relay.settle` (`H/tui_gateway/contracts/canonical_projections.py:101`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| clarify / 2 | `clarify.lock` (`H/tui_gateway/contracts/canonical.py:372`)<br>`clarify.respond` (`H/tui_gateway/contracts/canonical.py:367`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| command / 1 | `command.observe` (`H/tui_gateway/contracts/canonical.py:142`) | **② / M · 제안**: 원본 처리기 재사용, 허용 명령만 노출 |
| config / 2 | `config.get` (`H/tui_gateway/contracts/canonical_projections.py:101`)<br>`config.set` (`H/tui_gateway/contracts/canonical_projections.py:101`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결 |
| cron / 4 | `cron.cancel` (`H/tui_gateway/contracts/canonical.py:422`)<br>`cron.recover` (`H/tui_gateway/contracts/canonical.py:405`)<br>`cron.status` (`H/tui_gateway/contracts/canonical.py:414`)<br>`cron.submit` (`H/tui_gateway/contracts/canonical.py:391`) | **① / L · 제안**: 정기 리포트 설계 후 구현; 소유권·실행 시 권한·예산·실패 처리·알림 전달 선행 |
| groups / 6 | `groups.approve` (`H/tui_gateway/contracts/canonical_projections.py:101`)<br>`groups.attachment.download` (`H/tui_gateway/contracts/canonical.py:550`)<br>`groups.attachment.upload` (`H/tui_gateway/contracts/canonical.py:537`)<br>`groups.create` (`H/tui_gateway/contracts/canonical_projections.py:101`)<br>`groups.discard` (`H/tui_gateway/contracts/canonical.py:565`)<br>`groups.retry` (`H/tui_gateway/contracts/canonical_projections.py:101`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| image / 1 | `image.attach_bytes` (`H/tui_gateway/contracts/canonical_projections.py:101`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| kanban / 1 | `kanban.run` (`H/tui_gateway/contracts/canonical.py:439`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| profiles / 1 | `profiles.list` (`H/tui_gateway/contracts/canonical_projections.py:101`) | **② / M · 제안**: 원본 계약/로더 재사용, 사용자 격리·설정/저장 경계 연결 |
| prompt / 6 | `prompt.cancel` (`H/tui_gateway/contracts/canonical.py:64`)<br>`prompt.receipt` (`H/tui_gateway/contracts/canonical.py:55`)<br>`prompt.replace_queued` (`H/tui_gateway/contracts/canonical_sessions.py:341`)<br>`prompt.resolve_unknown` (`H/tui_gateway/contracts/canonical.py:74`)<br>`prompt.steer_queued` (`H/tui_gateway/contracts/canonical_sessions.py:332`)<br>`prompt.submit` (`H/tui_gateway/contracts/canonical_sessions.py:319`) | **① / L · 제안**: 실행 admission·IPC·대기 입력·공유 예산 연결 |
| rollback / 1 | `rollback.session_diff` (`H/tui_gateway/contracts/canonical.py:265`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| runtime / 1 | `runtime.describe` (`H/tui_gateway/contracts/canonical.py:351`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| session / 1 | `session.automation` (`H/tui_gateway/contracts/canonical.py:309`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| session / 13 | `session.create` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.detach` (`H/tui_gateway/contracts/canonical.py:326`)<br>`session.events.since` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.export` (`H/tui_gateway/contracts/canonical.py:288`)<br>`session.info` (`H/tui_gateway/contracts/canonical.py:204`)<br>`session.interrupt` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.list` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.mutate` (`H/tui_gateway/contracts/canonical.py:107`)<br>`session.prune` (`H/tui_gateway/contracts/canonical.py:126`)<br>`session.redirect` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.resume` (`H/tui_gateway/contracts/canonical_sessions.py:319`)<br>`session.stats` (`H/tui_gateway/contracts/canonical.py:242`)<br>`session.steer` (`H/tui_gateway/contracts/canonical_sessions.py:319`) | **② / L · 제안**: 원본 세션 의미 재사용, Mongo가 유일한 세션·대화 저장 기준이며 SessionDB 인터페이스를 Mongo 위에 연결(D04); session.steer/redirect의 같은 실행 변경은 ③, 취소 후 새 run 의미만 명시적 어댑터로 제공 제안 |
| shell / 1 | `shell.exec` (`H/tui_gateway/contracts/tools_commands.py:179`) | **③ / S · 제안**: Yield 미해당 기능 비활성 제안; 실제 제품 필요성은 미확인 |
| worker / 3 | `worker.adopt` (`H/tui_gateway/contracts/canonical.py:494`)<br>`worker.persist` (`H/tui_gateway/contracts/canonical.py:509`)<br>`worker.register` (`H/tui_gateway/contracts/canonical.py:492`) | **③ / unsupported · PR-1 확정**: 외부 worker 제어 표면은 사용자 불필요. 서버 내부 subprocess IPC와 구별 (`Y/harness/gateway/rpc_rules.py:123`) |

### 3.2 서버→클라이언트 이벤트와 요청

**집계 방법:** `python3 -B` AST로 `H/tui_gateway/contracts/*.py`의 `event(...)`와 `server_request(...)` 및 subagent 반복 등록을 세고 생성 `BackendGatewayEventMap`(:6962)/`ServerRequestMap`(:6907)과 대조했다. 발행 지점은 `H/tests/tui_gateway/contracts/test_generated.py:36`의 소스 스캔 규칙에 따라 `_emit/_broadcast_global_event/_voice_emit/_pet_emit/_emit_tool_lifecycle`, literal frame, side-agent, delegate, desktop_ui, bootstrap, local-viewer 발행을 확인했다. 직접·위임 발행 70종 + `H/tui_gateway/change_watcher.py:378`의 watch key 8종 = **78종**이며 생성 목록과 차이는 **0종**이다.

#### 3.2.1 생성 계약의 event type 78개

아래 근거는 payload 계약 선언 줄이다. 모든 타입은 외곽 `method:"event"`의 `params.type`이며 요청 ID로 응답하는 RPC 요청과 구별한다. 비활성 기능의 이벤트도 수신 타입을 삭제하지 않는다.

| 카테고리 / 수 | 전체 이름과 근거 | 대응·작업량·이유(구현 제안·정책은 D03~D13 확정) |
|---|---|---|
| agent / 1 | `agent.print` (`H/tui_gateway/contracts/events.py:381`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| agent / 1 | `agent.terminal.output` (`H/tui_gateway/contracts/events.py:749`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| approval / 1 | `approval.cancelled` (`H/tui_gateway/contracts/events.py:562`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| background / 1 | `background.complete` (`H/tui_gateway/contracts/events.py:595`) | **③ / S · 제안**: 장기 background 비활성; 계약 수신 타입은 유지 |
| billing / 1 | `billing.step_up.verification` (`H/tui_gateway/contracts/events.py:580`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| bot_relay / 1 | `bot_relay.outbox.pending` (`H/tui_gateway/contracts/events.py:855`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| browser / 1 | `browser.progress` (`H/tui_gateway/contracts/events.py:760`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| btw / 1 | `btw.complete` (`H/tui_gateway/contracts/events.py:596`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| connection / 2 | `connection.request` (`H/tui_gateway/contracts/connectors_operation.py:195`)<br>`connection.update` (`H/tui_gateway/contracts/connectors_operation.py:222`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| cron / 1 | `cron.changed` (`H/tui_gateway/contracts/events.py:848`) | **① / L · 제안**: 정기 리포트 설계 후 구현; 소유권·실행 시 권한·예산·실패 처리·알림 전달 선행 |
| display / 4 | `display.install.done` (`H/tui_gateway/contracts/display.py:204`)<br>`display.install.log` (`H/tui_gateway/contracts/display.py:195`)<br>`display.lease` (`H/tui_gateway/contracts/display.py:187`)<br>`display.status` (`H/tui_gateway/contracts/display.py:178`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| error / 1 | `error` (`H/tui_gateway/contracts/events.py:120`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| free_tier / 1 | `free_tier.challenge` (`H/tui_gateway/contracts/events.py:109`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| gateway / 1 | `gateway.ready` (`H/tui_gateway/contracts/events.py:63`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| message / 5 | `message.complete` (`H/tui_gateway/contracts/events.py:293`)<br>`message.delta` (`H/tui_gateway/contracts/events.py:169`)<br>`message.interim` (`H/tui_gateway/contracts/events.py:182`)<br>`message.reaction` (`H/tui_gateway/contracts/events.py:735`)<br>`message.start` (`H/tui_gateway/contracts/events.py:147`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결; D06 B안 확정: 검증 전 답변은 비공개. 원본 delta/interim/complete 매핑·보류는 구현 제안 |
| moa / 4 | `moa.aggregating` (`H/tui_gateway/contracts/events.py:693`)<br>`moa.phase` (`H/tui_gateway/contracts/events.py:695`)<br>`moa.progress` (`H/tui_gateway/contracts/events.py:694`)<br>`moa.reference` (`H/tui_gateway/contracts/events.py:692`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| notice / 1 | `notice` (`H/tui_gateway/contracts/events.py:129`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| notification / 2 | `notification.clear` (`H/tui_gateway/contracts/events.py:493`)<br>`notification.show` (`H/tui_gateway/contracts/events.py:492`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| onboarding / 1 | `onboarding.ended` (`H/tui_gateway/contracts/events.py:103`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| pairing / 1 | `pairing.changed` (`H/tui_gateway/contracts/events.py:852`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| pane / 1 | `pane.reveal` (`H/tui_gateway/contracts/events.py:724`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| pet / 3 | `pet.changed` (`H/tui_gateway/contracts/events.py:836`)<br>`pet.generate.progress` (`H/tui_gateway/contracts/events.py:837`)<br>`pet.hatch.progress` (`H/tui_gateway/contracts/events.py:838`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| platforms / 1 | `platforms.changed` (`H/tui_gateway/contracts/events.py:850`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| preview / 4 | `preview.close` (`H/tui_gateway/contracts/events.py:715`)<br>`preview.open` (`H/tui_gateway/contracts/events.py:714`)<br>`preview.restart.complete` (`H/tui_gateway/contracts/events.py:597`)<br>`preview.restart.progress` (`H/tui_gateway/contracts/events.py:608`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| projects / 1 | `projects.changed` (`H/tui_gateway/contracts/events.py:851`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| reaction / 1 | `reaction` (`H/tui_gateway/contracts/events.py:361`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| reasoning / 2 | `reasoning.available` (`H/tui_gateway/contracts/events.py:171`)<br>`reasoning.delta` (`H/tui_gateway/contracts/events.py:170`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| request / 1 | `request.cancel` (`H/tui_gateway/contracts/server_requests.py:378`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| review / 1 | `review.summary` (`H/tui_gateway/contracts/events.py:370`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| session / 6 | `session.control.update` (`H/tui_gateway/contracts/events.py:570`)<br>`session.info` (`H/tui_gateway/contracts/events.py:513`)<br>`session.reclaimed` (`H/tui_gateway/contracts/events.py:543`)<br>`session.resume_progress` (`H/tui_gateway/contracts/events.py:532`)<br>`session.title` (`H/tui_gateway/contracts/events.py:352`)<br>`session.usage` (`H/tui_gateway/contracts/events.py:342`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| sessions / 1 | `sessions.changed` (`H/tui_gateway/contracts/events.py:849`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| setup / 1 | `setup.ready` (`H/tui_gateway/contracts/events.py:105`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| skills / 1 | `skills.changed` (`H/tui_gateway/contracts/events.py:853`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| skin / 1 | `skin.changed` (`H/tui_gateway/contracts/events.py:65`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| status / 1 | `status.update` (`H/tui_gateway/contracts/events.py:303`) | **② / M · 제안**: 원본 알림/표시·watch 의미 유지, 호스트 이벤트 소스 연결 |
| subagent / 6 | `subagent.complete` (`H/tui_gateway/contracts/events.py:652`)<br>`subagent.progress` (`H/tui_gateway/contracts/events.py:652`)<br>`subagent.spawn_requested` (`H/tui_gateway/contracts/events.py:652`)<br>`subagent.start` (`H/tui_gateway/contracts/events.py:652`)<br>`subagent.thinking` (`H/tui_gateway/contracts/events.py:652`)<br>`subagent.tool` (`H/tui_gateway/contracts/events.py:652`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결; **D09 확정: 1차 비활성, 계약 수신 유지, M2에서 예산·감사 설계 후 재판단; 현재 delegation 도구셋 실제 동작 미확인** |
| terminal / 1 | `terminal.close` (`H/tui_gateway/contracts/events.py:750`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| thinking / 1 | `thinking.delta` (`H/tui_gateway/contracts/events.py:172`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| tip / 1 | `tip.show` (`H/tui_gateway/contracts/events.py:505`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| todo / 1 | `todo.updated` (`H/tui_gateway/contracts/events.py:467`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| tool / 5 | `tool.complete` (`H/tui_gateway/contracts/events.py:420`)<br>`tool.generating` (`H/tui_gateway/contracts/events.py:429`)<br>`tool.output_risk` (`H/tui_gateway/contracts/events.py:442`)<br>`tool.progress` (`H/tui_gateway/contracts/events.py:457`)<br>`tool.start` (`H/tui_gateway/contracts/events.py:401`) | **① / L · 제안**: 실행·저장 이벤트를 원본 payload/seq로 연결 |
| voice / 4 | `voice.interrupted` (`H/tui_gateway/contracts/events.py:766`)<br>`voice.partial` (`H/tui_gateway/contracts/events.py:799`)<br>`voice.status` (`H/tui_gateway/contracts/events.py:798`)<br>`voice.transcript` (`H/tui_gateway/contracts/events.py:800`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |
| wake / 1 | `wake.detected` (`H/tui_gateway/contracts/events.py:801`) | **③ / S · 제안**: 대응 기능 비활성 시 발행 중단·수신기는 안전하게 처리 |

#### 3.2.2 authority에서 추가로 발행하는 event type 8개

**전체 gateway 범위에서는 확인된 합집합 86종(78 + 8)**을 기준으로 한다. 아래 8종은 `BackendGatewayEventMap`에 **없다**. 생성 계약만 복사하면 빠지는 실제 소스 차이이며, 모두 **① / L · 제안**: authority 이벤트와 replay 수신 스키마까지 포함해 이식한다. 이를 원본 78종에 이미 선언됐다고 간주하지 않는다.

| type | 근거 / 의미 |
|---|---|
| `approval.request` | `H/gateway/session_pending_controls.py:38` — generation에 묶인 승인 알림 |
| `clarify.request` | `H/gateway/session_pending_controls.py:51` — generation에 묶인 질문 알림 |
| `approval.settled`, `clarify.settled` | `H/gateway/session_pending_controls.py:63`, :85 — kind(approval/clarify) + '.settled'; worker 경로도 `H/gateway/session_managed_worker.py:365`에서 발행 |
| `session.updated` | `H/gateway/session_mutations.py:320`, `H/gateway/session_deferred_pick.py:124` — mutation/선택 반영 |
| `session.tools_changed` | `H/gateway/session_local_mcp.py:330` — session 도구 설정 반영; 개인 MCP 변경을 해당 principal/session에만 전달하는 제안 |
| `session.replay_gap` | `H/gateway/session_events.py:62` — bounded fanout overflow 시 실제 서버가 보내는 복구 알림 |
| `agent.step` | `H/gateway/run_turn_progress.py:887` — iteration 진행 |

추가 집계는 `grep -RnE 'event_type=|_publish_execution\\(|publish_execution\\(' H/gateway --include='*.py'` 및 `SessionEvents`의 literal frame을 확인하고 동적 `kind + '.settled'`의 두 kind를 펼친 것이다. `tool.started/tool.completed`는 내부 progress queue(`H/gateway/run_turn_progress.py:832`, :844), `subagent.text`는 `message.delta`로 변환(`H/gateway/local_viewer_events.py:132`)하므로 wire 타입 합계에서 제외했다. 외부 플러그인의 런타임 임의 발행은 **미확인**이며 고정 소스 합계에 포함하지 않는다.

#### 3.2.3 요청성 메시지 16개

서버 요청은 `{jsonrpc:"2.0", id, method, params}`에 대한 클라이언트 result/error 응답이다. **16개** 모두 생성 `ServerRequestMap`과 일치(누락·초과 0). `H/tui_gateway/rpc_dispatch.py:59`는 이런 응답 프레임을 일반 메서드 호출보다 먼저 처리한다. authority의 `clarify.request/approval.request` 이벤트 + respond RPC와 혼동하지 않는다.

| 카테고리 / 수 | 전체 이름과 근거 | 대응·작업량·이유(구현 제안·정책은 D03~D13 확정) |
|---|---|---|
| approval / 1 | `approval` (`H/tui_gateway/contracts/server_requests.py:195`) | **① / L · 제안**: IPC 대기·응답·취소와 권한 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| clarify / 1 | `clarify` (`H/tui_gateway/contracts/server_requests.py:59`) | **① / L · 제안**: IPC 대기·응답·취소와 권한 연결; D05 확정: Hermes 계약·화면 동일 이식, 허용 도구의 효과 있는 호출만 승인, timeout·재접속 복원 지원 |
| display / 1 | `display.install.sudo` (`H/tui_gateway/contracts/display.py:167`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성; D05 확정: sudo·secret 요청 거부 |
| preview / 2 | `preview.act` (`H/tui_gateway/contracts/server_requests.py:285`)<br>`preview.read` (`H/tui_gateway/contracts/server_requests.py:263`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |
| secret / 1 | `secret` (`H/tui_gateway/contracts/server_requests.py:222`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성; D05 확정: sudo·secret 요청 거부 |
| setup_beat / 1 | `setup_beat` (`H/tui_gateway/contracts/server_requests.py:159`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |
| sudo / 1 | `sudo` (`H/tui_gateway/contracts/server_requests.py:212`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성; D05 확정: sudo·secret 요청 거부 |
| terminal / 1 | `terminal.read` (`H/tui_gateway/contracts/server_requests.py:261`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |
| theme / 2 | `theme.apply` (`H/tui_gateway/contracts/server_requests.py:355`)<br>`theme.pick` (`H/tui_gateway/contracts/server_requests.py:357`) | **② / M · 제안**: 원본 UI 요청 처리기와 웹 표시 경계 연결 |
| tour / 1 | `tour` (`H/tui_gateway/contracts/server_requests.py:311`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |
| vault / 3 | `vault.code` (`H/tui_gateway/contracts/server_requests.py:249`)<br>`vault.save_login` (`H/tui_gateway/contracts/server_requests.py:240`)<br>`vault.unlock_prompt` (`H/tui_gateway/contracts/server_requests.py:231`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |
| window / 1 | `window.read` (`H/tui_gateway/contracts/server_requests.py:265`) | **③ / S · 제안**: 로컬 권한·온보딩·비밀 입력 브리지 비활성 |

### 3.3 REST/HTTP 라우터와 WebSocket 전체 목록

**집계 방법:** `python3 -B` + `ast.parse`로 `H/hermes_cli/web_routers/*.py` 및 `H/hermes_cli/web_server.py`의 모든 함수 decorator를 읽어 `get/post/put/patch/delete/head/options/api_route/route/websocket`을 추출했다. `router`뿐 아니라 `list_router/search_router/manage_router/config_router/status_router/logs_router/hub_router/sessions_router`를 포함했다. `grep -nE 'include_router|mount|add_.*route' H/hermes_cli/web_server.py`로 mount 경계도 확인했다.

**web_routers 25개 라우트 보유 파일, HTTP 282개 + WebSocket 8개 = 등록 290개. web_server.py 직접 정의 HTTP/WS는 0개.** HTTP는 REST API 외 파일 제공 GET도 포함하며, 같은 경로의 다른 verb(예: GET/HEAD)는 각각 1개다. middleware는 endpoint가 아니다. `__init__.py/_common.py`는 0개다. 라우터 prefix 추가는 이 25개 파일에서 없으며 장착 순서는 `H/hermes_cli/web_server.py:788`~:819다.

아래는 **파일별 정확한 개수와 대표 경로**다. 한 파일의 서로 다른 기능은 '기본 분류 + 명시적 예외'로 분류했다. 구현 분류·작업량은 **제안**이며 D03~D13 정책은 확정이다. 그 밖의 ②/③ 대응은 실제 제품 필요성 **미확인** 상태의 제안이고, ③ 기준을 벗어나는 신규 예외는 D11 절차를 따른다.

| 파일(H/hermes_cli/web_routers/) | HTTP / WS | 대표 경로와 근거 | 대응·작업량(제안) |
|---|---:|---|---|
| `actions.py` | 8 / 0 | `POST /api/gateway/restart` (`H/hermes_cli/web_routers/actions.py:160`) | **제안** — ③/S: restart·migration·drain·자동 업데이트·상태/receipt 모두 비활성; 호스트 운영 API 개방 안 함 |
| `analytics.py` | 4 / 0 | `GET /api/config/raw` (`H/hermes_cli/web_routers/analytics.py:34`) | **제안** — ②/M: analytics 사용량 원본 집계+저장 연결. config/raw GET·PUT은 ③/S(원문 설정 노출·쓰기 비활성) |
| `audio.py` | 8 / 2 | `POST /api/audio/transcribe` (`H/hermes_cli/web_routers/audio.py:86`)<br>`WEBSOCKET /api/audio/transcribe-stream` (`H/hermes_cli/web_routers/audio.py:516`) | **제안** — ③/S: 음성·lease·스트리밍 전체 비활성 |
| `chat_workspaces.py` | 1 / 0 | `GET /api/chat/workspaces` (`H/hermes_cli/web_routers/chat_workspaces.py:72`) | **제안** — ③/S: 로컬 workspace 탐색 비활성 |
| `chat_ws.py` | 0 / 5 | `WEBSOCKET /api/console` (`H/hermes_cli/web_routers/chat_ws.py:299`) | **제안** — ①/L: /api/ws 전체 계약. /api/console·pty·pub·events는 ③/S(PTY/sidecar 기능 비활성) |
| `config_env.py` | 15 / 0 | `GET /api/config` (`H/hermes_cli/web_routers/config_env.py:80`) | **제안** — ②/M: /api/config 및 defaults/schema, /api/egress/status. 그 외 /api/env와 provider 검증/관리 경로는 ③/S(관리자 비밀·endpoint 설정 비활성); D07에 따라 같은 endpoint fallback만, 다중 provider 제외·사용자 임의 모델/provider 설정 비활성 |
| `cron.py` | 13 / 0 | `GET /api/cron/jobs` (`H/hermes_cli/web_routers/cron.py:701`) | **제안** — ①/L: jobs·blueprints·fire는 설계 후 구현; principal 권한·예산·실패·알림 설계 선행 |
| `dashboard_ui.py` | 18 / 0 | `GET /api/dashboard/themes` (`H/hermes_cli/web_routers/dashboard_ui.py:52`) | **제안** — ②/M: themes/theme/font 및 허용 개인 스킬/MCP 메타데이터 표시. plugin 코드 설치·갱신·임의 정적 파일 제공은 ③/S |
| `delegation.py` | 4 / 0 | `GET /api/delegation/routing` (`H/hermes_cli/web_routers/delegation.py:92`) | **제안** — ②/M: 계약 수신 유지, delegate는 1차 비활성; M2에서 예산·감사 설계 후 재판단(D09), 현재 도구셋 실제 동작 미확인 |
| `display.py` | 0 / 1 | `WEBSOCKET /api/display/ws` (`H/hermes_cli/web_routers/display.py:75`) | **제안** — ③/S: 원격 화면/설치 기능 비활성 |
| `files.py` | 19 / 0 | `GET /api/media` (`H/hermes_cli/web_routers/files.py:336`) | **제안** — ③/S: 범용 로컬 파일·media proxy·업로드 전체 비활성. Yield artifact는 기존 소유권 검사 endpoint로 제공(4장); 별도 이미지 첨부 지원 여부 미확인 |
| `git.py` | 21 / 0 | `GET /api/git/status` (`H/hermes_cli/web_routers/git.py:50`) | **제안** — ③/S: 저장소·worktree·review·commit/push/PR 전체 비활성 |
| `local_models.py` | 18 / 0 | `GET /api/local-models/status` (`H/hermes_cli/web_routers/local_models.py:534`) | **제안** — ③/S: 로컬 모델 설치·다운로드·서버 프로세스 전체 비활성 |
| `mcp.py` | 12 / 0 | `GET /api/mcp/servers` (`H/hermes_cli/web_routers/mcp.py:94`) | **제안** — ①/L: 개인 서버 등록·목록·시험·OAuth를 principal에 연결(D13); catalog의 설정 등록은 허용 범위 내, 패키지 자동 설치는 기본 ③; HTTP(Streamable HTTP) 자유 등록, stdio는 1차 범위 제외(등록·실행 불가); **D13 확정:** stdio·SSE 제외, 자격증명은 사용자별 Mongo 암호화 저장(키 분리), 감사는 Mongo 별도 컬렉션·결과 본문 길이만 기록. **PR-1 추가 조치:** HTTP `/api/mcp/*` 12개 모두 `denied_actions=[stdio, SSE]`, 분류 개수 불변 (`Y/harness/gateway/policy.py:166`) |
| `memory_providers.py` | 3 / 0 | `GET /api/memory/providers/{name}/config` (`H/hermes_cli/web_routers/memory_providers.py:508`) | **제안** — ②/M: 원본 provider 설정 의미+격리 저장/자격증명 어댑터; 외부 provider 허용 여부 미확인 |
| `messaging.py` | 11 / 0 | `POST /api/messaging/whatsapp/onboarding/start` (`H/hermes_cli/web_routers/messaging.py:629`) | **제안** — ③/S: WhatsApp·Telegram·플랫폼 연동 전체 비활성 |
| `models.py` | 7 / 0 | `GET /api/model/info` (`H/hermes_cli/web_routers/models.py:90`) | **제안** — ②/M: 같은 endpoint 재시도·대체 모델만 허용, 다중 provider 제외·사용자 임의 모델/provider 설정 비활성(D07); 공유 예산 연결은 제안, MoA 제품 필요성 미확인 |
| `oauth.py` | 7 / 0 | `GET /api/providers/oauth` (`H/hermes_cli/web_routers/oauth.py:671`) | **제안** — ③/S: D07 확정: 사용자 임의 모델/provider 설정 비활성·다중 provider 제외; Yield 로그인과 별개 |
| `ops.py` | 28 / 0 | `GET /api/pairing` (`H/hermes_cli/web_routers/ops.py:84`) | **제안** — ②/M: /api/memory GET·provider PUT의 원본 메모리 처리+격리 저장. **PR-1 확정:** `POST /api/memory/reset`은 사용자별 격리 설계 전 전역 초기화 위험으로 unsupported (`Y/harness/gateway/policy.py:132`). 나머지 pairing/webhook/gateway/credentials/doctor/security-audit/backup/import/hooks/checkpoints는 ③/S |
| `profiles.py` | 20 / 0 | `GET /api/profiles/sessions` (`H/hermes_cli/web_routers/profiles.py:453`) | **제안** — ②/L: 원본 profile/SOUL/모델·세션 projection+Mongo·격리 연결. setup-command/open-terminal, projects/tree, sessions/pull-requests, export/import, desktop-overlay는 ③/S |
| `sessions.py` | 18 / 0 | `GET /api/sessions` (`H/hermes_cli/web_routers/sessions.py:182`) | **제안** — ②/L: 목록·검색·단건 삭제·mutation snapshot·이력·timeline·서버 export의 원본 의미+Mongo 소유권/압축 조상 연결. **PR-1 확정:** 운영자 POST import/owner-backfill/prune/bulk-delete는 unsupported, 단건 DELETE 및 서버 export는 stub(7.4; `Y/harness/gateway/policy.py:126`, :163) |
| `shared_metrics.py` | 2 / 0 | `GET /api/shared-metrics/consent` (`H/hermes_cli/web_routers/shared_metrics.py:34`) | **제안** — ③/S: 외부 telemetry 동의·전송 비활성 |
| `skills.py` | 13 / 0 | `POST /api/skills/hub/install` (`H/hermes_cli/web_routers/skills.py:97`) | **제안** — ②/M: 개인 조회·toggle·content·작성에 사용자별 Mongo·본인 소유권·사용 감사 연결(D12 확정, 사전 검토 없음). 버전 저장은 제안; hub 검색/메타데이터는 ②, 자동 설치·갱신은 ③; 팀 공유는 나중 |
| `status.py` | 20 / 0 | `GET /api/ssh/ownership` (`H/hermes_cli/web_routers/status.py:109`) | **제안** — ①/M: health/host identity/idle/status의 Yield 상태 projection. ssh/ownership·retirement·system/stats·curator·learning·portal·ops/*·logs는 ③/S(로컬 운영/진단 비활성) |
| `tools.py` | 12 / 0 | `GET /api/tools/toolsets` (`H/hermes_cli/web_routers/tools.py:223`) | **제안** — ①/M: /api/tools/toolsets 조회·설정/모델/provider는 Yield 허용 registry로 제한. env·post-setup·terminal/*·computer-use/*는 ③/S |
| `../web_server.py` 직접 정의 | 0 / 0 | router mount `H/hermes_cli/web_server.py:788` | **①/M · 제안**: 앱 인증·mount·수명 경계 연결 |

#### 3.3.1 WebSocket 엔드포인트 8개

| 경로 | 근거 | 대응·작업량(제안) |
|---|---|---|
| `/api/audio/transcribe-stream` | `H/hermes_cli/web_routers/audio.py:516` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/audio/speak-stream` | `H/hermes_cli/web_routers/audio.py:593` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/console` | `H/hermes_cli/web_routers/chat_ws.py:299` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/pty` | `H/hermes_cli/web_routers/chat_ws.py:538` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/ws` | `H/hermes_cli/web_routers/chat_ws.py:702` | **①/L · 제안**: legacy+authority 전체 계약 연결 |
| `/api/pub` | `H/hermes_cli/web_routers/chat_ws.py:755` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/events` | `H/hermes_cli/web_routers/chat_ws.py:767` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |
| `/api/display/ws` | `H/hermes_cli/web_routers/display.py:75` | **③/S · 제안**: 해당 음성/PTY/화면/sidecar capability 비활성 |

`/api/events`는 이 소스에서 **WebSocket**이며 Yield의 기존 run SSE와 다른 표면이다. `/api/pub`→`/api/events`는 PTY sidecar fanout(`H/hermes_cli/web_routers/chat_ws.py:737`)이므로 이를 세션 replay API와 동일시하지 않는다.

#### 3.3.2 직접 정의와 외부 mount의 집계 경계

`web_server.py`가 밖에서 장착하는 라우트는 위 **290개에 중복 합산하지 않았다**. 전체 서비스 배포 시 추가 검토가 필요한 경계를 아래에 남긴다.

| 외부 mount | 확인 범위 / 근거 | 대응·작업량(제안) |
|---|---|---|
| memory OAuth | 2 HTTP: `POST /api/memory/providers/{provider}/oauth/start`, `GET .../oauth/status`; `H/hermes_cli/memory_oauth.py:10`, :51, :65; mount `H/hermes_cli/web_server.py:216` | **③/S · 제안**: loopback OAuth 비활성; provider 필요성 미확인 |
| dashboard 인증 | 11 HTTP decorator: login/providers/login redirect/native authorize/callback/password-login/logout/me/ws-ticket/native token/native refresh; `H/hermes_cli/dashboard_auth/routes.py:195`, :203, :217, :289, :338, :447, :496, :524, :556, :574, :594; mount `H/hermes_cli/web_server.py:827` | **①/L · 제안**: Yield 인증 주체/권한으로 연결, native token은 비활성 |
| plugin API·SPA | `_mount_plugin_api_routes()`, `mount_spa(app)` (`H/hermes_cli/web_server.py:824`, :828) | plugin **③/S · 제안**, 웹 정적 제공 **①/M · 제안**. 환경·설치 plugin·빌드에 따른 실제 mount 개수는 **미확인** |

따라서 확인한 정적 HTTP/WS decorator는 위 290개 + 외부 인증/메모리 13개 = **303개**지만, 이를 실행 중 전체 서비스 라우트 총수라고 주장하지 않는다. 자동 OpenAPI/docs, SPA mount, 동적 plugin route는 290개 집계의 대상이 아니다. 이번 작업에서는 서버를 실행하거나 인증·비밀 설정을 읽지 않았다.

### 3.4 Yield 미해당 기능의 명시적 비활성 처리 — 제안

1. **지원 행렬:** 283개 RPC 이름과 두 계약 모드, 86개 이벤트 type, 16개 서버 요청, 290개 HTTP/WS 등록을 빠짐없이 `supported/unsupported`와 사유로 관리한다. 터미널·셸·파일·브라우저 및 sudo·secret 비활성은 D05 확정 정책이다. PTY, 임의 plugin 코드 자동 설치·업데이트, 음성, 로컬 모델, 원격 화면, OS vault 등은 ③ 기준으로 두며 `unsupported` 응답 구현은 제안이다. 개인 스킬·개인 MCP는 **켬으로 결정됨(D12·D13)**; cron/스케줄링은 **설계 후 구현**(권한·예산·실패 처리·알림 설계 선행)으로 분류한다. 미구현 기간의 unsupported는 영구 비활성 결정과 구별한다. groups/bot relay/결제/pet/telemetry 등도 표의 ③ 제안을 따른다. D03~D13 정책은 확정이고 나머지 기능별 구현 대응은 **제안**이다. ③은 D11 절차 아래의 기준이며 신규 예외는 미승인 후보로 기록하고 이유·범위·되돌리는 방법을 문서화한 뒤 대환 님 승인 후에만 구현한다. 보안 관련 도구 활성화·권한 확대는 반드시 대환 님 승인이 필요하다.
2. **capability 응답:** 원본 `gateway.capabilities`는 `per_session_exclusive_submit`만 선언한다(`H/tui_gateway/contracts/liveness.py:21`, `H/tui_gateway/methods_voice.py:513`). 이미 범용 unsupported 행렬이 있다고 가정하지 않는다. **①/M · 신규 확장 제안**: 별도 `yield.capabilities` RPC의 `{schema_version:1, upstream:"5894e750", rpc_mode, methods:{[name]:{status:"supported"|"unsupported",reason}}, features:{...}}`를 계약·생성 타입·수신기와 함께 추가한다. 이 RPC는 원본 283개 집계에서 제외한다. 원본 `gateway.capabilities` 응답에 임의 키를 삽입하지 않는다. 원본 client capability의 `server_requests` 협상과 `declines_not_shown`도 유지한다(`H/tui_gateway/methods_voice.py:521`).
3. **오류:** 등록되지 않은 이름은 원본 `-32601`(`H/tui_gateway/rpc_dispatch.py:22`). 알고 있지만 Yield에서 비활성인 RPC에는 **신규 오류 제안** `-32004 / unsupported`, `data:{reason:"unsupported", feature, upstream:"5894e750"}`를 사용하되 기존 코드와 충돌 여부는 P0에서 확인(**미확인**). 원본에 이미 typed unsupported 응답이 있는 메서드는 그 계약을 우선 유지한다. HTTP는 **501 + 구조화 unsupported 오류 제안**, 인증/소유권 실패는 기존 401/403 정책을 우선한다. WS의 비지원 endpoint는 인증 검사 뒤 upgrade 거부(403 + 사유) 또는 연결 후 1008 종료를 하나로 고정하는 **제안**이며 클라이언트 호환 검증 전 확정하지 않는다.
4. **서버 요청과 UI:** 미지원 server request에 응답하지 않고 방치하지 않는다. 원본 무처리 메서드 응답 `-32601`(`H/tui_gateway/contracts/liveness.py:30`) 또는 해당 요청의 decline 결과를 사용한다. `4404`는 '이 세션을 표시하는 창이 없음'이라는 별도 의미(`H/tui_gateway/server_requests.py:119`)라 일반 unsupported로 재사용하지 않는다. 승인·clarify 허용이 terminal/sudo/secret 허용을 뜻하지 않는다. unsupported capability의 탭·설정·명령 palette·단축키·자동 polling/구독은 숨기고 deep link에는 미지원 사유를 표시한다. UI를 숨겨도 서버의 같은 거부 검사를 유지한다.
5. **도구·파일 경계:** 모델에 전달하는 tool schema와 UI 목록은 Yield 도구 허용목록으로 제한한다(개인 MCP 서버의 관리자 allowlist와 구별; 후자는 대안/참고로 채택하지 않음). `command.dispatch/slash.exec/cli.exec`를 통한 우회와 config/profile에서 재활성화하는 경로도 검사한다. 범용 로컬 경로를 artifact URL로 그대로 바꾸지 않고 principal/session 소유권을 검사하는 ID 기반 결과 API를 사용한다. 비활성 기능의 watcher·child process·OAuth listener·자동 설치를 시작하지 않는다. 원본 코드 재사용은 실행 권한 부여와 별개다. 개인 MCP 등록≠plugin 코드 자동 설치다. stdio는 도구 호출 전에도 로컬 프로세스를 시작하므로 D13에 따라 1차 범위 제외(등록·실행 불가)이며 관리자 승인 실행물·격리 환경 예외도 제외한다. 서버/도구 식별자·provenance·toolset 허용목록을 등록·발견·reload·실행마다 검사하여 이름 충돌이나 MCP 래퍼로 로컬 실행 권한이 열리지 않게 한다(D05·D11·D13).

### 3.5 사내 다중 사용자 서비스 관점의 기능 분류 — 제안

사내 서비스에서 중요한 것은 **권한·감사·비용 통제·정기 리포트**다. D03~D13 정책은 **결정됨 — 2026-10-24, 대환 님**이며 아래 우선순위 분류·구현 방법은 **제안**이다. RPC·이벤트·라우터 근거는 3.1~3.3의 해당 카테고리 행을 잇는다. 기존 3.5 위험 절은 3.6으로 이동했다.

| 분류(제안) | 기능 | gateway 연결 / 근거 | 서비스 경계(확정 정책·구현 제안 구분) |
|---|---|---|---|
| 필수 | 로그인·사용자별 권한 | 3.3.2 dashboard 인증(`H/hermes_cli/dashboard_auth/routes.py:524`), 3.3 chat_ws | principal을 서버에서 확정; host/operator 권한 자동 승계 금지 |
| 필수 | 채팅·스트리밍·도구 진행 표시 | 3.1 prompt, 3.2 message/tool(`H/tui_gateway/contracts/events.py:169`, :401), 3.3 /api/ws | D06 B안 확정: 검증 전 답변 숨김, 도구 진행·검증 상태만 표시, 확정 답변만 공개. 본인 대화 재연결·중복 방지·중지는 구현 제안 |
| 필수 | 세션 목록·이어받기·중지 | 3.1 session, 3.2 sessions/session, 3.3 sessions(`H/hermes_cli/web_routers/sessions.py:182`) | 목록·이력·replay·취소 모두 본인 소유권 검사 |
| 필수 | clarify | 3.1 clarify/request, 3.2 clarify/clarify.request(`H/gateway/session_pending_controls.py:51`) | D05 확정: clarify/승인 계약·화면은 Hermes 동일 이식, timeout·재접속 복원 지원. 승인은 허용 도구의 효과 있는 호출에만 적용; sudo·secret 거부, 비활성 도구 우회 불가 |
| 필수 | 웨이퍼맵·정렬 표·CSV·코드 결과 표시 | 3.2 message/tool + 4.2 yield.artifact/table 제안; 현행 `F/Artifacts.tsx:64`, `Y/harness/router.py:157`, :167 | 결과 표시/내보내기만; 코드 표시가 실행 권한을 뜻하지 않음. 원본 전용 RPC 대응은 미확인 |
| 필수 | 검증 결과 표시 | 3.2 review/message + 4.2 yield.verify 제안; `Y/harness/native_hermes.py:492` | 판정·자료 완전성·종료 상태 분리 |
| 필수 | 사용량·비용 한도 | 3.1 session.usage, 3.2 session.usage(`H/tui_gateway/contracts/events.py:342`), 3.3 analytics | 실행 전 예약·재시도/스케줄 통합 계상 제안; child는 D09 M2 재판단 후 재활성 시 계상; 결제 UI와 분리. MCP 별도 호출·금액 한도는 1차 범위 제외, 추후 필요 시 추가(D13) |
| 필수 | 감사 로그 | 3.1 approval/session/tools, 3.2 tool/session, 3.3 인증/상태 경계; 현행 `Y/harness/store.py:299` | D05 항상 허용은 사용자별 분리 저장·감사 기록, D12 사용한 스킬 기록, D13 MCP 감사는 Mongo 별도 컬렉션·결과 본문 길이만 기록으로 확정. 나머지 실행 권한·설정 버전 기록은 제안. 현행 events가 완전한 보안 감사인지는 미확인; 범용 host logs API는 개방 안 함 |
| 필수 | 개인 프롬프트·메모리 | 3.1 profiles/config/command, 3.3 profiles/ops/memory_providers(`H/hermes_cli/web_routers/profiles.py:453`) | D08 확정: 사용자별 SOUL은 시스템 규칙 아래만 적용, gate·도구 제한 덮어쓰기 불가; 메모리는 사용자별 Mongo 분리 저장, CWD 서버 고정(3.7) |
| 필수 | 개인 스킬(팀 공유는 나중) | 3.1 skills/plugins, 3.2 skills.changed, 3.3 skills/dashboard_ui(`H/tui_gateway/methods_tools.py:1418`) | D12 확정: 사용자별 Mongo 저장·본인만 사용·사전 검토 없음·사용 스킬 감사 기록. 공유 ACL·공용 승격은 팀 공유 도입 시 후속 |
| 필수 | 개인 MCP | 3.1 mcp/reload/plugins, 3.2 session.tools_changed, 3.3 mcp(`H/hermes_cli/web_routers/mcp.py:94`) | D13 확정: HTTP(Streamable HTTP)만 자유 등록, stdio·SSE 제외. 자격증명은 사용자별 Mongo 암호화 저장·키 별도 보관, 감사 로그는 Mongo 별도 컬렉션·결과 본문 길이만 기록. 로컬 실행 도구 비활성 유지 |
| 필수 | 정기 리포트(스케줄링) | 3.1 cron.manage, 3.2 cron.changed, 3.3 cron(`H/hermes_cli/web_routers/cron.py:701`) | 설계 후 구현; 실행 때 권한 재검사·예산·중복 방지·실패/재시도 정책 선행 |
| 필수 | 알림 전달 | 3.2 notification.show/clear(`H/tui_gateway/contracts/events.py:492`), 3.1 cron 및 3.3 cron 연결 | 수신자 권한·민감 결과 링크·전달 실패 처리; 실제 사내 전달 채널/라우터는 미확인. messaging/bot relay 전체 개방과 별개 |
| 있으면 좋음 | 공유 링크(권한 확인) | 3.3 sessions, 3.1 session; 목록 근거 `H/hermes_cli/web_routers/sessions.py:182` | 링크 전용 원본 계약은 미확인; 수신자 ACL·만료·철회 설계 필요 |
| 있으면 좋음 | 대화 검색·이름 변경 | 3.3 sessions, 3.1 session.title, 3.2 session.title(`H/tui_gateway/methods_session.py:1219`) | 본인 검색; 공유된 결과는 별도 권한 검사 |
| 있으면 좋음 | 피드백 | 3.1 message.react, 3.2 message.reaction/reaction(`H/tui_gateway/methods_session.py:1312`) | 사내 저장; shared_metrics 외부 전송은 비활성 |
| 있으면 좋음 | 관리자 화면 | 3.1 config/tools, 3.3 analytics/status(`H/hermes_cli/web_routers/status.py:109`) | 별도 관리자 권한, 정책·감사·한도 projection만; host 운영 endpoint 전체 노출 금지 |
| 있으면 좋음 | 모델 선택(관리자 범위) | 3.1 model/config, 3.3 models(`H/hermes_cli/web_routers/models.py:90`) | D07 확정: 사용자 임의 모델/provider 설정 비활성, 같은 endpoint 내 대체 모델만 허용. 관리자 허용 옵션 표시 방식은 제안 |
| 불필요/비활성 | 터미널·셸·파일·브라우저 도구, PTY | 3.1 cli/browser/terminal, 3.2 terminal/browser, 3.3 files/chat_ws(`H/hermes_cli/web_routers/chat_ws.py:538`) | ③ 유지; 결과 artifact 다운로드와 범용 파일 접근 구별 |
| 불필요/비활성 | 임의 plugin 자동 설치·업데이트 | 3.1 plugins.manage, 3.3 actions/dashboard_ui/plugin mount(`H/tui_gateway/methods_tools.py:1862`) | MCP 서버 등록≠plugin 코드 자동 설치; MCP stdio 경유 npx/uvx 실행도 1차 범위 제외(등록·실행 불가) |
| 불필요/비활성 | API키·비밀 관리 화면 | 3.1 model.save_key, 3.2 secret/vault, 3.3 config_env/oauth(`H/tui_gateway/methods_complete.py:334`) | 범용 비밀 열람/편집 UI 비활성. 개인 MCP에 필요한 인증 연결은 전용 비밀 저장 어댑터로 분리(D13) |
| 불필요/비활성 | 음성·로컬 모델·원격 화면·pet·결제 | 3.1 voice/display/pet/billing, 3.2 해당 이벤트, 3.3 audio/local_models/display(`H/hermes_cli/web_routers/local_models.py:534`) | ③ 유지 |
| 불필요/비활성 | 그룹·bot relay·텔레메트리 | 3.1 groups/bot_relay/shared_metrics, 3.2 bot_relay, 3.3 shared_metrics/messaging(`H/hermes_cli/web_routers/shared_metrics.py:34`) | ③ 유지; 사내 알림 전달·감사 로그와 구별 |
| 불필요/비활성 | 다중 provider fallback(사용자 설정) | 3.1 config/model, 3.3 config_env/models(`H/hermes_cli/web_routers/config_env.py:80`) | D07 확정: 다중 provider 제외, 같은 endpoint 안의 재시도·대체 모델 fallback만 허용; 사용자 임의 모델/provider 설정 비활성 |
| 불필요/비활성 | 같은 실행 steer·장기 background 서브에이전트 | 3.1 session.steer/subagent/prompt.background, 3.2 background/subagent(`H/tui_gateway/methods_session_interrupt.py:171`) | 같은 실행 steer·장기 background는 ③ 기준. D09 확정: 동기 delegate를 포함한 하위 에이전트는 1차 비활성, 계약 수신 유지; M2 예산·감사 설계 후 재판단. 병렬 도구는 원본과 동일하게 배치당 최대 8로 허용(`H/agent/tool_executor.py:134`, :241, :248), 설정 조정 가능(구현 방식은 제안), 현재 Yield 쪽 적용 방식은 미확인. 호스트 소유의 제한된 검증 실행은 일반 delegate와 분리한다(7.2). 정기 리포트는 독립 예약 실행 제안 |

### 3.6 전체 gateway 이식의 어려운 부분과 위험 — 제안

| 경계 | 직접 확인한 위험·근거 | 대응 분류·작업량 / 검증 제안 |
|---|---|---|
| Electron bridge | Desktop gateway가 `window.hermesDesktop`에 결합(`H/apps/desktop/src/store/gateway.ts:471`); preview/terminal/window/tour 요청 16종 계약 중 일부 | **①/L · 제안**: D10 확정: 웹 UI 우선·채팅 중심 opt-in, Desktop 앱은 나중; Electron 의존은 어댑터 뒤로 분리. 웹 bridge API별 대응·native OS 기능 ③은 구현 제안. store 재수화·연결 끊김·다중 창 응답 검증; 웹 대체 가능성 일부 미확인 |
| 로컬 실행 권한 | shell/CLI/browser·MCP 등록·plugin 관리 RPC 존재(`H/tui_gateway/methods_tools.py:445`, :1202, :1492, :1862). 개인 MCP stdio는 `StdioServerParameters`→`stdio_client`로 자식 실행(`H/tools/mcp_tool_transport.py:382`, :414) | **로컬 도구 ③/M, 개인 MCP ①/L · 제안**: stdio는 1차 범위 제외(등록·실행 불가)로 차단하며 관리자 예외도 제외. 개인 MCP를 켜도 terminal/셸/파일/browser는 열리지 않음. HTTP 자유 등록에는 사용자별 자격증명 분리·호출 감사 로그 필수; 기타 안전장치는 추후 필요 시 추가(3.8). 이름 정규화 충돌·toolset alias·reload·명령 우회 검사(3.8); 사내 플랫폼 보안은 전제(미검증) |
| 인증·권한 범위 | /api/ws는 인증 후 operator=True, identity profile_scope='host' 전달(`H/hermes_cli/web_routers/chat_ws.py:715`~:732). HTTP middleware만으로 WS가 보호되지는 않음(`H/hermes_cli/web_server.py:429`) | **①/L · 제안**: Yield principal→session/profile 권한 연결, host-wide 권한 자동 승계 금지; ticket/origin·다른 사용자 ID·replay 조회·재연결 권한 재검증 |
| 상태 공유·저장 권위 | 원본 registry 전역 rebind(`H/tui_gateway/method_ctx.py:78`), authority generation fencing(`H/gateway/session_authority.py:964`), private replay key(`H/gateway/session_events.py:16`) | **①/L · 제안**: D04 확정에 따라 Mongo가 유일한 세션·대화 저장 기준이며 SessionDB 인터페이스를 그 위에 연결(병행 사본 아님); profile 격리; seq/epoch/실행 generation 보존; 같은 session ID의 사용자 간 혼선·동시 submit·중복 응답 검증 |
| 파일·터미널 노출 | /api/files·fs와 PTY/console, media proxy까지 표면 존재(`H/hermes_cli/web_routers/files.py:380`, :515, :843; `chat_ws.py:299`, :538) | **③/M · 제안**: 경로·symlink·URL proxy·terminal 노출 비활성. Yield artifact 확장은 별도 소유권 검사; 단순 URL 치환 금지 |
| 프로세스 수명·취소 | gateway pool dispatch와 retirement acquire/release(`H/tui_gateway/rpc_dispatch.py:73`), authority side-agent daemon/ledger(`H/gateway/session_tui_side.py:123`), Yield 격리 subprocess | **①/L · 제안**: 대기 입력·tool 종료와 예산을 부모 수명에 연결; child·side-agent는 D09 1차 비활성, M2 재판단 후 필요 시 수명 연결; 재시작/강제 종료/늦은 callback/복구 후 재실행 검증. delegate 실제 동작은 미확인; 신규 예외는 D11 절차 적용 |
| 계약 모드·생성 누락 | legacy 254 + authority 전용 29; 생성 event map 밖 8종(`H/tui_gateway/ws.py:357`, `H/gateway/session_pending_controls.py:38`) | **①/L · 제안**: handshake에서 모드를 확정하고 서로 다른 params/result·승인 흐름을 섞지 않음. 원본과 확장 계약 fixture를 각각 유지; P2a~P2c 완료 전 전체 호환 통과 선언 금지 |


### 3.7 개인 스킬·메모리·SOUL 조사와 저장 변경 제안

실제 프로필·비밀 파일은 열지 않았다. 아래는 **소스 경로 확인**이며 등록 성공·배포 볼륨·OS 격리의 실효성은 **미확인**이다. `ROOT=Path(__file__).resolve().parents[2]`는 Y의 부모인 저장소 루트다(`Y/harness/native_hermes.py:25`).

| 조사 항목 | 확인한 현재 동작 / file:line | 막힌 지점·필요 변경(제안) / 작업량 |
|---|---|---|
| principal별 home/config | `prepare_profile`은 `<repo>/.runtime/hermes/profiles/<sha256(principal_id)>`를 생성하고 공용 `Y/harness/skills/*`를 없는 디렉터리만 복사. config.yaml의 auto_load 기본 yield-analysis, environment_probe=false, orchestrator_enabled=false(`Y/harness/native_hermes.py:314`~:333). worker에 HERMES_HOME 전달(:354~:355) | 경로상 사용자 분리는 존재. 같은 principal의 여러 세션은 같은 profile; 해시는 OS 접근 통제가 아님. 볼륨 영속성·권한·다중 replica 일관성은 미확인. 기존 배포 스킬과 개인 revision 충돌 처리는 제안/미확인; 사용자 간 팀 공유는 D12에 따라 나중 — M |
| 실행 CWD·읽기 snapshot | `/tmp` 계열 실제 위치는 OS tempfile 정책에 따름; `TemporaryDirectory(prefix="yield-hermes-")` 생성 후 skills 복사·snapshot/versions를 Mongo run에 기록, subprocess cwd 지정(`Y/harness/native_hermes.py:364`~:384). worker는 `hermes_constants.get_skills_dir`와 `skills_tool.SKILLS_DIR`만 snapshot으로 교체(`Y/harness/native_worker.py:77`~:80) | run의 skill_snapshot에는 Markdown 본문·hash·version도 저장됨(`Y/harness/skills.py:34`~:40). 다만 실행 시작 snapshot이며 개인 자산 CRUD/종료 변경 sync와 다름; 비Markdown 첨부는 이 수집 범위 밖. D08 확정: CWD는 서버 고정·사용자 지정 불가. 읽기·쓰기·auto_load를 동일 실행 버전으로 맞추는 경계는 제안 — M |
| 실행 중 스킬 쓰기: 단순 유실로 단정 금지 | `skills` toolset 활성(`Y/harness/native_worker.py:100`, `E/toolsets.py:104`~:107), builtin 발견(`E/model_tools.py:149`), `skill_manage`는 skills에 등록(`E/tools/skill_manager_tool.py:874`). 관리 도구는 별도 `_skills_dir()`로 **HERMES_HOME/skills**를 선택(:68~:81), 생성도 그 경로(:200~:208) | **skill_manage 비활성은 확인 사실이 아님.** 이 경로의 쓰기는 프로필에 직접 남을 수 있어 임시 삭제로 전부 유실된다는 설명은 틀림. 반면 snapshot에서 읽고 profile에 쓰는 불일치·동시 실행 충돌 가능성; 실제 호출 성공은 미확인 — M |
| 임시 변경 유실·외부 경로 | `external_dirs/create_dir`가 있으면 실행 거부(`Y/harness/native_hermes.py:351`~:352). 예외 및 종료 시 workspace.cleanup(:386, :577), 종료 경로(:536~:577)에 snapshot→profile sync 없음 | **임시 CWD/snapshot에 생긴 변경**은 삭제 대상. 개인 등록 API·Mongo skill 본문 CRUD는 조사한 native/store/control 경로에 연결 없음(`Y/harness/store.py:208`~:212, `Y/harness/control.py:100`~:113). 읽기 snapshot과 영속 변경의 단일 계약 필요 — M |
| 개인 메모리 | native MemoryStore는 설정에 따라 disk load(`E/agent/agent_init.py:1350`~:1364), `HERMES_HOME/memories/{MEMORY,USER}.md` 사용(`E/tools/memory_tool.py:38`, `E/tools/memory_tool_store.py:212`) | profile 파일 기반, 임시 CWD와 별개. Mongo `user_profiles`는 별도 경로(`Y/harness/memory.py:46`~:51, :76~:82); Hermes 분기는 load_profile 전에 return(`Y/harness/control.py:100`~:113, :136). native↔Mongo 자동 연결 없음. D08 확정: 메모리는 사용자별 Mongo 분리 저장; 크기·동시 쓰기 통합 방법은 제안/미확인 — M |
| SOUL·개인 프롬프트 | 원본 SOUL 로더는 HERMES_HOME/SOUL.md(`E/agent/prompt_builder.py:1606`~:1621); worker는 load_soul_identity=false, skip_context_files=false(`Y/harness/native_worker.py:105`). 호스트 native.md를 instructions로 전달(`Y/harness/native_hermes.py:401`) | SOUL은 현재 off. D08 확정: 사용자별 허용하되 시스템 규칙 아래 층이며 gate·도구 제한을 덮어쓰지 못함. 실행 CWD 서버 고정, context 파일과 영구 persona 구별; 읽기 크기·세부 우선순위·스캔 방법은 제안/미확인 — M |

| 개인 스킬 저장 설계(D12 정책 확정·구현 제안) | 변경 경계 / 근거 | 작업량 |
|---|---|---|
| 사용자별 Mongo 저장·본인만 사용·사전 검토 없음(D12 확정) | **구현 제안:** `(principal_id, skill_id, revision)`·본문/첨부 hash·크기 저장; 큰 첨부는 GridFS 등 선택. 검수 상태는 요구하지 않으며 공유 ACL·공용 승격은 팀 공유 도입 시 후속. 기존 store의 결과/GridFS 패턴은 `Y/harness/store.py:30`, :73; **개인 skill 전용 연결은 신규**. Mongo 저장 정책은 확정, replica/백업 구현 정책은 미확인 | M |
| 시작 materialize → 실행 snapshot → 종료 sync | **구현 제안:** 인증 principal의 개인 revision과 기존 배포 스킬을 실행 전 materialize. 읽기/skill_manage 쓰기를 같은 staging으로 연결하고 사전 검토 없이 CAS revision commit; 종료 실패/취소의 미완료 변경 복구·중복 방지는 미확인/후속. 현재 복사·삭제 위치 `Y/harness/native_hermes.py:368`, :577 | L |
| 안전한 등록·유지 | **기술적 안전 제한 제안(별도 승인된 예외 아님):** 사용자별 용량/파일 수/파일 크기 제한, 경로 탈출·symlink·실행물 차단. 사전 검토는 요구하지 않음(D12). 스킬로 terminal/browser 권한을 추가할 수 없으며 보안 예외는 D11 승인 필수. 기존 생성 스캔은 예외 시 로그만 남김(`E/tools/skill_manager_tool.py:55`~:64); 이 스캔을 사전 검토·게시 승인 절차로 요구하지 않음 | M |
| 격리·복구·감사 검증 | D12 확정: 사용한 스킬을 감사 로그에 기록. 저장 위치는 D13과 같은 Mongo 별도 컬렉션 방식 **제안**(미확정). 다른 principal 접근·같은 principal 동시 수정·삭제/rollback·replica 재시작·강제 종료·quota 초과 검증 제안; run에는 사용한 불변 snapshot 참조 저장. session lock은 principal+session 단위(`Y/harness/store.py:47`~:48)이므로 개인 자산 revision 충돌 제어 추가 | M; 전체 L |

### 3.8 개인 MCP 차단 경계와 연결 제안

H는 **최신 main 기준으로 채택한 고정 소스 `5894e750`**를 조사했다. 현재 원격 main과 동일한지는 **미확인**이며 fetch/설치/서버 실행은 하지 않았다. **결정됨 — 2026-10-24, 대환 님**(D13): HTTP(Streamable HTTP)만 자유 등록, stdio·SSE 제외, 자격증명은 사용자별 Mongo 암호화 저장(키는 서버 환경변수·비밀 저장소에 분리), 감사 로그는 Mongo 별도 컬렉션(결과 본문 길이만 기록). 아래 연결 설계·위험도·작업량은 모두 **제안**이며 구현 완료 여부는 **미확인**이다.

| 현행 Y/E 경계 | 확인 사실 / file:line | 연결 변경(제안) / 작업량 |
|---|---|---|
| 외부 plugin 자동 발견 제거 | `E/PATCHES.diff:89`~:97은 model_tools의 discover_plugins 호출만 제거. MCP 발견 자체는 model_tools import에서 원래 분리되어 있음(`E/model_tools.py:151`~:159) | 패치 제거를 MCP 활성화로 취급하지 않음. plugin 자동 발견 금지 유지, 허용 MCP config를 명시 등록 — M |
| worker 발견·활성 toolset | worker는 Yield 등록→AIAgent, enabled_toolsets는 yield_backend/yield_evidence/skills/memory/delegation뿐(`Y/harness/native_worker.py:81`~:100). MCP 발견 호출은 해당 초기화에 없음. turn refresh도 MCP 모듈 로드+등록 도구가 있을 때만(`E/agent/turn_context.py:544`~:560) | principal 설정 검증→허용 서버만 discover/register→허용 MCP toolset 추가→agent 구성; refresh/reload/child에도 같은 정책 유지 — M |
| 설정·자격증명 공급 | profile config는 파일에서 읽고 일반 부모 환경은 PATH/HOME/LANG/LC_ALL/SSL_CERT_*만 전달(`Y/harness/native_hermes.py:349`~:355). MCP config/secret 전용 startup 필드 없음(:394~:404) | Mongo의 본인 설정·암호화 자격증명을 분리 보관한 키로 복호화하여 필요한 서버별 header만 주입하는 구현 제안(D13). 전체 부모 env 복사 금지; config에 mcp_servers가 이미 있는지는 미확인 — L |
| 설치 제한 | lazy install off(`Y/harness/native_hermes.py:354`), extras 미설치면 오류(`E/PATCHES.diff:152`), self-update 제거(:169) | 승인된 MCP SDK/의존성을 배포 산출물로 공급. 실제 설치 유무 미확인. stdio의 npx/uvx는 별도 설치 경로이므로 이 패치만으로 금지 보장 안 됨; D13에 따라 stdio 등록·실행 제외 — M |
| HTTP endpoint·네트워크 | `Y/harness/native_budget.py:44`~:54는 JSON에 model 및 messages/input/prompt가 있는 **모델 요청만** base_url 제한; 그 밖은 통과. HOME도 그대로 전달(`Y/harness/native_hermes.py:354`), subprocess 실행은 :382 | 일반 MCP HTTP/SSE egress·SSRF·OS sandbox가 구현됐다는 근거가 아님. 사내 플랫폼 차단은 **대환 님 전제(미검증)**; 배포 통제는 미확인. **이 전제가 틀리면 SSRF 위험이 가장 먼저 드러남**. 목적지·redirect·크기·timeout·호출/금액 한도 등 추가 안전장치는 1차 범위 제외, 추후 필요 시 추가 — L |
| Yield 도구 효과 제한의 범위 | NativeBridge는 Yield registry의 read/artifact만 허용(`Y/harness/native_hermes.py:277`~:298); MCP는 원본 registry/transport에서 별도 실행 | MCP에는 principal별 자격증명 분리·호출 감사 로그 필수. 도구 효과·예산 admission 등 추가 안전장치는 1차 범위 제외, 추후 필요 시 추가. 기존 read/artifact 필터가 MCP까지 보호한다고 가정하지 않음 — L |

| H의 프로필별 MCP 방식 | 확인한 구현 / file:line | Yield 적용(제안) |
|---|---|---|
| config.yaml / 로딩 | named profile은 기본 Hermes root의 `profiles/<id>`를 HERMES_HOME으로 해석(`H/hermes_cli/profiles.py:283`, :434~:445); Yield의 principal hash 매핑과 구별. `get_hermes_home()/config.yaml`(`H/hermes_cli/config.py:433`), `mcp_servers`를 `tools/mcp_tool_config._load_mcp_config`에서 읽음(:690~:718); safe worker/safe mode면 빈 설정(:692~:699) | principal이 지정한 임의 home 경로를 받지 않고 서버가 profile 매핑. D13 등록 검증을 통과한 본인 config snapshot만 전달 |
| 숨은 plugin 재발견 경로 | `_load_mcp_config`는 `_portable_mcp_servers` 호출, 내부에서 discover_plugins와 plugin MCP 병합(`H/tools/mcp_tool_config.py:662`~:685, :717) | native config 명시 등록 경로 사용 또는 이 병합도 제한. E의 model_tools 패치 하나만으로 H의 자동 발견 제한은 보장되지 않음 |
| 자격증명 env/header | `${VAR}`는 profile secret scope 조회(`H/tools/mcp_tool_config.py:554`~:570); stdio safe env+명시 env(`:199`~:227)는 원본 근거이며 Yield 1차 범위 제외. 원본 bearer 저장은 profile의 비밀 환경 파일, config에는 참조 header(`H/hermes_cli/mcp_config.py:330`~:341; 경로 로직 `H/hermes_cli/config.py:474`) | 실제 비밀 파일은 열람하지 않음. D13 확정: 자격증명은 사용자별 Mongo 암호화 저장, 암호화 키는 서버 환경변수·비밀 저장소에 분리. 서버별 주입·사용자에게 원문 키/환경 파일 반환 금지는 구현 제안; 알고리즘·키 회전·KMS 여부는 미확인/후속 |
| OAuth 상태 | `HERMES_HOME/mcp-tokens/<server>.json`, `.client.json`, `.meta.json` 등(`H/tools/mcp_oauth.py:229`, :413~:430); 쓰기 파일 0600·부모 0700(:397~:404) | principal+server+issuer에 묶인 저장·refresh 잠금·회수·삭제; 프로필 파일 권한이 암호화 보관을 의미하지 않음. callback state의 principal/session 검증과 다중 replica token sync 필요 — L |
| 발견·이름·toolset | `discover_mcp_tools(allowed_mcp_names)`→config 필터→register(`H/tools/mcp_tool_discovery.py:604`~:643); `session.list_tools` 페이지 조회(`H/tools/mcp_tool_transport.py:692`). 이름은 `mcp__<정규화 서버>__<정규화 도구>`·64자 hash 축약(`H/tools/mcp_tool_schema.py:147`~:183), alias는 서버명→mcp-서버명(`H/tools/mcp_tool_registration.py:163`) | 발견 결과를 본인 등록의 `(principal, server_id, original_tool, revision)`에 연결. built-in/Yield/다른 서버·alias 충돌 거부; prefix 자체를 권한으로 사용하지 않음 |
| 프로필별 연결 수명 | multiplexer는 `(owner_scope, name)` key, 단일 profile은 bare name; route/credential 같으면 타 profile 연결 adopt 가능(`H/tools/mcp_tool_scope.py:1`~:14, :26~:31) | principal 전용 worker 유지 또는 registry/home/secret scope를 함께 바인딩. 사용자 간 connection/token/cache 공유 금지는 D13 필수 조건; revoke/reload/worker 종료 시 정리 — L |
| 보안 필터의 실제 한계 | 의심 config 필터(`H/tools/mcp_tool_config.py:643`), tools.include/exclude(`H/tools/mcp_tool_registration.py:247`), 이름 충돌 거부(:315). description 스캔은 **경고만**(`H/tools/mcp_tool_schema.py:15`~:40); stdio OSV preflight timeout은 **fail-open**(`H/tools/mcp_tool.py:34`~:54) | stdio는 1차 범위 제외(등록·실행 불가). 관리자 allowlist 권고는 **대안/참고(채택하지 않음)**; D13은 HTTP 자유 등록. description 경고·정책 검수 등은 추후 필요 시 추가; 필수 감사 로그를 대신하지 않음 |

| MCP 기동 방식 | 상대 위험도(제안) / 확인 근거 | 경계·완료 조건(제안) / 작업량 |
|---|---|---|
| stdio 자식 프로세스 | **높음**: command/args/env/cwd로 로컬 프로세스 실행(`H/tools/mcp_tool_transport.py:382`, :414). npx 캐시 미스는 원 명령 유지(`H/tools/mcp_tool.py:56`~:58); terminal tool off와 무관 | **제외(등록·실행 불가)**. 관리자 승인·고정 실행물·OS 격리 예외도 **1차 범위 제외, 추후 별도 결정 필요 시** 검토. D05 승인 폼으로 우회 불가; 등록·실행 거부 검증 — L |
| Streamable HTTP 허용 / SSE 제외 | **stdio보다 로컬 실행 위험 낮음**, 원격 도구 부작용·SSRF·데이터 유출·토큰/redirect 위험은 남음. HTTP 및 SSE transport(`H/tools/mcp_tool_transport.py:532`, :562), strict redirect header 정책에서 SSE 거부(:535) | **HTTP(Streamable HTTP)만 사용자 자유 등록 허용**; 사용자별 자격증명 분리·호출 감사 로그 필수. **SSE 제외 확정(D13)**. 자격증명은 사용자별 Mongo 암호화 저장(키 분리), 감사 로그는 Mongo 별도 컬렉션·결과 본문 길이만 기록. 기타 안전장치는 추후 필요 시 추가 — M~L |
| 전체 연결 | 서버 등록만으로 완료 아님 | 설정 CRUD·인증·발견·호출·감사·취소·reconnect·token refresh/revoke·타 principal 차단·로컬 권한 우회 방지 E2E. **전체 L**, 설정 표시/단일 상태 projection만 S; 실제 일정 및 배포 검증은 **미확인** |

**stdio·SSE 차단 구현 제안:** 등록 검증에서 `command/args`를 거부하고 transport 허용목록을 `http`만으로 제한하며, 발견·reload·실행에도 같은 검증을 적용한다(SSE 제외는 D13 확정 정책).

| D13 구분 | 항목 | 조건·사유 |
|---|---|---|
| 필수 조건 | 사용자(principal)별 자격증명 분리 | 실행 시 본인 설정·비밀만 주입; 사용자 간 연결·토큰·캐시 공유 금지. 자격증명은 Mongo 암호화 저장, 키는 서버 환경변수·비밀 저장소에 분리(D13 확정); 암호화 상세는 미확인/후속 |
| 필수 조건 | MCP 호출 감사 로그 | 누가·언제·어느 서버/도구를 호출했는지 기록. Mongo 별도 컬렉션에 저장, 결과 본문은 길이만 기록(D13 확정); 상세 스키마·보존기간·인덱스는 미확인/후속 |
| 1차 범위 제외 · 추후 필요 시 추가 | 사설 IP/로컬호스트 주소 거부 | SSRF 위험; 사내 플랫폼 네트워크 보안은 대환 님 전제(미검증) |
| 1차 범위 제외 · 추후 필요 시 추가 | 프롬프트 주입 경고(도구 description 등) | 원격 도구 설명을 통한 지시 주입 위험 |
| 1차 범위 제외 · 추후 필요 시 추가 | 호출·금액 한도 | 과다 호출·비용 위험 |
| 1차 범위 제외 · 추후 필요 시 추가 | 데이터 전송 확인 | 외부 서버로의 데이터 유출 위험 |
| 1차 범위 제외 · 추후 필요 시 추가 | 기타 목적지·redirect·도구 효과·응답 크기·timeout 등 안전장치 | 원격 도구 부작용·자원 소모 위험; 추가 설계는 제안 |

## 4. 유지할 의도적 예외와 확장 계약

아래 여섯 예외와 ③ 비활성 항목은 **D11 절차 아래의 기준**이다. **결정됨 — 2026-10-24, 대환 님:** 기준을 벗어나는 신규 예외는 **미승인 후보**로 두고 이유·범위·되돌리는 방법을 문서에 기록한 뒤 대환 님 승인 후에만 구현한다. 보안 관련 도구 활성화·권한 확대는 반드시 대환 님 승인이 필요하다. 아래 구현 방법은 **제안**이며 개별 신규 예외 승인 대기 목록은 현재 없음. 원본 코드와 다른 지점은 예외 목록·patch manifest에 남기며, 표면만 같게 보이도록 의미 차이를 숨기지 않는다.

| 예외 | 유지 목적 / 현재 근거 | Hermes 계약 위에 얹는 방법(제안) |
|---|---|---|
| Yield 도구·Oracle/MES 연계 | 도메인 도구 등록·백엔드 호출 유지 (`Y/harness/tools/registry.py:76`, `Y/harness/native_worker.py:51`). 실제 Oracle/MES 연결 상태는 **미확인** | 원본 tool schema/호출 흐름을 사용하고 결과는 저장 ID·provenance로 참조. 표·artifact는 아래 확장 알림으로 별도 전달 |
| Yield 검증 게이트 | 근거 ID·판정 검사와 실패 시 미확정 답변 (`Y/harness/native_verification.py:51`, `Y/harness/native_stop_gate.py:24`, `:67`) | 원본 stop 경계에 부착, `yield.verify`로 판정 공개. Hermes에도 stop gate 존재 (`H/agent/turn_stop_gates.py:1`, `:36`); “검증 기능 자체가 Hermes에 없음”으로 표현하지 않음 |
| HTTP 공유 예산 | 모든 관련 모델 호출 admission·토큰 예약 (`Y/harness/native_budget.py:44`, `:88`) | 원본 usage와 호스트 제한을 구별. 재시도·검증도 공유 계상; child는 D09의 M2 재판단 후 재활성 시에만 계상 설계; 상한 종료 사유 전달. D07 확정: fallback은 같은 endpoint의 재시도·대체 모델만, 다중 provider 제외. 현재 타 endpoint 차단과 같은 endpoint fallback의 호환 검증 제안; 일반 MCP HTTP 차단은 아님 (`:51`) |
| 격리 subprocess | 임시 CWD·별도 interpreter·프로세스 종료 (`Y/harness/native_hermes.py:382`, `:384`, `:536`) | gateway 원본을 부모 프로세스에 두고 원본 agent 호출을 IPC 어댑터로 연결. 승인·취소·delta를 IPC로 연결하는 제안; D06에 따라 검증 전 답변 delta는 클라이언트에 공개하지 않음 |
| Mongo 저장소 | run/result와 GridFS 저장 (`Y/harness/store.py:30`) | D04 확정: 세션·대화 저장의 유일한 기준은 Mongo, SessionDB 원본 인터페이스를 Mongo 위에 맞추며 병행 사본이 아님. 저장 어댑터 구현 제안. 세션 seq와 run sequence의 매핑 영속화; 이중 DB에 동시 권위를 부여하지 않음 |
| 범용 호스트 도구 제한 | backend read/artifact와 skills/memory/delegation 활성 선언(현재 delegation 실제 동작 미확인) (`Y/harness/native_hermes.py:277`; `Y/harness/native_worker.py:100`) | 원본 capability/toolset 설정으로 제한하고 미지원 동작을 명시. 개인 스킬/MCP는 D12·D13에 따라 연결하되 HTTP 자유 등록·자격증명 분리·호출 감사 로그 적용, stdio는 1차 범위 제외(등록·실행 불가). terminal/셸/파일/browser 비활성 및 plugin 자동 발견·runtime 설치·자기 갱신 제한 유지 (`E/PATCHES.diff:89`, `:152`, `:169`) |

### 4.1 확장 이벤트 공통 규칙 — 미구현 제안

기존 Hermes 이벤트 payload를 임의로 바꾸지 않고, capability 협상 후 `yield.*` 타입을 추가한다. 생성 계약·dispatcher 검증·Desktop 수신기를 함께 확장해야 하며, 원본이 미등록 타입을 자동 수용한다고 가정하지 않는다 (`H/tui_gateway/server.py:718`). 단, 원본 `check_payload`는 미선언 이벤트를 즉시 거부하지 않는다(`H/tui_gateway/contracts/registry.py:157`); 생성 타입에 없는 authority 이벤트 8종도 3.2.2처럼 따로 검증한다. 기존 클라이언트는 협상하지 않은 확장을 받지 않는다.

| 필드 | 제안 타입 / 의미 |
|---|---|
| 외곽 프레임 | `{jsonrpc:"2.0", method:"event", params:{type, session_id, seq, payload}}` — 원본 event 구조에 맞춤. seq 부착 위치·replay_epoch 등 부가 계약은 선택 버전 준수 |
| 공통 payload | `{schema_version:1, event_id:string, run_id:string, message_id:string, revision:integer}` — 세션은 외곽, 실행·메시지·수정은 payload에서 식별 |
| 순서·복구 | 세션 seq로 재생, event_id로 중복 제거. run sequence와 동일시하지 않음. replay 누락 시 REST snapshot 재조회 |
| 소유권 | principal은 인증된 서버 문맥에서만 결정. 결과·artifact 조회 시 session/principal 소유권 재검사; payload의 ID는 권한이 아님 (`Y/harness/router.py:40`, `:157`, `:167`) |

### 4.2 확장별 payload — 미구현 제안

아래 필드는 공통 payload에 추가한다. enum은 전송 계약의 값이며 자연어 판단용 키워드 규칙이 아니다.

| 타입 | 추가 필드 제안 | 표시·저장 원칙 |
|---|---|---|
| `yield.artifact` | `artifact_id:string`, `result_id:string`, `title:string`, `mime:string`, `url:string`, `source_result_ids:string[]` | 소유권을 검사하는 기존 artifact endpoint 참조. 본문에 파일 전체를 반복 삽입하지 않음 |
| `yield.table` | `result_id:string`, `table_id:string`, `title:string`, `columns:{key:string,label:string,type:string,unit?:string}[]`, `total_rows:integer`, `complete:boolean`, `missing_reason?:string`, `preview_rows:object[]`, `data_url:string`, `source_result_ids:string[]` | 프리뷰는 전체 분석 근거가 아님. 원본 rows는 결과 저장소에서 페이지 조회. `(result_id,table_id)`로 중복 제거 |
| `yield.verify` | `review_id:string`, `candidate_message_id:string`, `candidate_revision:integer`, `verdict:"pass" 또는 "fail" 또는 "unknown"`, `issues:string[]`, `result_ids:string[]`, `attempt:integer`, `final:boolean` | 검증 판정과 run 종료 상태·자료 완전성 분리. pass=자료 전체 완전함으로 해석하지 않음 |
| `yield.input_required` | `interrupt_id:string`, `request_id:string`, `generation:integer`, `goal_revision:integer`, `kind:"clarify" 또는 "approval" 또는 "form"`, `message:string`, `fields:Field[]`, `expires_at?:string`, `response_method:string` | D05 확정: Hermes 계약·화면 동일 이식, timeout·재접속 복원; 허용 도구의 효과 있는 호출만 승인하며 항상 허용은 사용자별 저장·감사 기록. sudo·secret 거부 및 비활성 도구 우회 불가. 동일 pending ID 연결은 구현 제안; 승인과 일반 입력은 서로 대체 불가 |

`Field` 제안: `{name:string, label:string, type:"text" 또는 "number" 또는 "select" 또는 "multiselect", required:boolean, options?:{value:string,label:string}[], description?:string}`. 제출 제안: `{request_id, interrupt_id, generation, goal_revision, values:{[name]:value}}`. 같은 request_id 재전송은 중복 실행하지 않고, 만료·이미 응답·세대 불일치는 충돌로 반환한다.

native clarify/approval은 **원본 요청·응답 방식**으로 처리하고 폼 확장은 해당 pending request의 표시 정보만 추가한다. `kind=form`의 별도 `yield.input.respond` RPC는 **신규 제안**이며 P0에서 스키마를 확정한다. 이 공통 제출 객체를 `clarify.respond`에 그대로 넣지 말고 선택 버전의 생성 params로 변환한다. 구 `/runs/{id}/input`에서는 기존 `request_id/goal_revision/interrupt_id/value`와 검사를 유지한다 (`Y/harness/control.py:236`, `:245`).

### 4.3 답변·초안·확정 정책 — B안 확정

**결정됨 — 2026-10-24, 대환 님**(D06): **B안.** 검증 전 답변(delta/초안)은 숨기고 도구 진행·검증 상태만 표시하며 **확정된 답변만 공개**한다. native 답변 delta를 연결하더라도 검증 전에는 클라이언트에 공개하지 않는다. A(미확정 표시 후 공개)/C 확장은 나중에 가능하며 시점은 미확인/후속이다.

- **구현 제안:** 신 UI의 확정 서술은 `answer_text`, 표·artifact는 구조화 결과로 표시하고 구 `answer`는 legacy 호환용으로 보존한다. 결과 표시도 위 공개 정책을 따른다.
- **구현 제안:** 원본 `message.delta/interim/complete`의 의미·식별자를 내부에서 보존하고 검증 전 답변은 보류한다. gateway 공개 이벤트로의 매핑·보류 방식과 수정 초안의 내부 보존·교체는 미확인/후속이며 원본 필드를 임의 변경하지 않는다.
- **구현 제안:** gate 이후 최종 메시지를 확정하고 `yield.verify`를 candidate revision에 연결한다. fail/unknown에서는 검증 상태를 표시하고 답변은 공개하지 않는다. 이력·replay에도 동일 공개 정책을 적용한다.

## 5. P0~P3 실행 계획과 호환 유지

**이번 작업은 이 문서 보강으로 끝난다.** 아래 소스 변경·테스트·스크린샷은 향후 구현 시 수행할 계획이다. 현재 DB/LLM E2E 결과 및 화면 비교 결과는 **미확인**이다.

| 단계 | 변경 대상과 순서(향후) | 완료 기준 | 검증 방법 / 증거 | 위험·호환 유지 |
|---|---|---|---|---|
| P0 기준 고정 | 확정된 5894e750 전 층 기준 → 원본 파일·의존성·patch 목록 → 283개 RPC 이름(legacy 254/authority 48), 86종 event, 16개 server request, 290개 HTTP/WS의 계약 행렬 → 외부 인증/mount·예외·확장 스키마 확정 | 5894e750 이식 경로·해시 및 e473f5a9 회귀 기준 고정; 전체 지원/unsupported·payload·오류·권한·replay 행렬 작성; D03 허용 의존성·고지 유지·@nous-research/ui fonts.css/번들 woff2 제외 또는 대체 반영; 사내 법무 확인은 별도 선행 확인 항목 | e473f5a9↔5894e750 diff, 계약 fixture/schema 검증, Desktop 호출 경로 대조. 원본·현행 동일 시나리오/viewport의 기준 스크린샷 확보 | 버전 혼합·숨은 bridge 의존성. 기존 실행 경로 변경 없음; 구 API 응답 fixture 확보 |
| P1 엔진·호스트 연결 | 5894e750 원본 엔진/설정 동기화 → worker IPC callback·clarify/승인·취소 연결 → Mongo 유일 저장 기준 위에 SessionDB 인터페이스 연결(D04) → 공유 예산·검증 gate 통합 | 대화 이력·압축·취소·대기 입력의 상태 전이와 delegate 1차 비활성(D09)이 정해진 계약과 일치; D06 B안(검증 전 답변 비공개·확정 답변만 공개), worker 종료와 중복 호출 방지; 예외 목록 밖 원본 변경 없음 | 원본 관련 테스트+호스트 통합 테스트. 모델 실패/재시도, 예산 소진, delegate 실행 거부(재활성은 M2 예산·감사 설계 후 재판단), 입력 만료, 프로세스 중단·복구 재현. 실제 Oracle/MES 읽기·LLM·도구 실행을 포함한 허용 시나리오 E2E; 비밀 제거 실행 기록 | IPC 교착·토큰 이중 계상·세션 소실. 신 경로는 opt-in, 기존 세션은 원 runtime 유지; 현행 UI 완료/partial/취소 화면 스크린샷으로 회귀 확인 |
| P2a gateway·authority 계약 연결 | 원본 gateway·authority·생성 계약 이식 → legacy/authority 모드별 실행/저장 어댑터 → 인증·capability·모든 메서드 이름 등록 또는 명시적 거부 | 283개 이름과 19개 중복 이름의 두 params/result 의미 검증; prompt receipt·중복 방지·pending 요청/응답·권한 일치 | WS 계약 테스트: duplicate submit, ACK 유실, 다른 principal/profile·stale input·미지원 메서드 거부. 생성 계약 밖 8종 event도 fixture화 | **M2a 마일스톤(제안)**: 전체 RPC 계약 표면 확보. 인증/host-wide 권한·이중 실행 위험; 구 /runs·SSE·/chat 유지 |
| P2b 저장·replay·부가 라우터 | 세션 seq/epoch/replay → 25개 web_routers 파일의 HTTP 282/WS 8 대응 → 외부 인증·memory OAuth·SPA/plugin mount 경계 → 원본 session 라우터·Mongo 단일 저장 기준 연결(별도 SessionDB 사본 없음) | 86종 이벤트의 모드별 수신·재생 의미, session snapshot·압축 조상·검색·삭제 일관성. 모든 HTTP/WS 등록은 지원 또는 명시적 unsupported; 파일·PTY 우회 없음 | 연결 단절, seq 누락/truncated/overflow, 서버 복구, 잘못된 소유권, 파일/운영 endpoint 우회 검사. 동일 실행을 신/구 클라이언트로 관찰하여 상태·결과 ID 대조; 재연결 화면 스크린샷 | **M2b 마일스톤(제안)**: 전체 gateway 저장·복구/라우트 경계 확보. run sequence→session seq 충돌 방지; 단일 실행 권위에서 두 표면 제공 |
| P2c 전체 기능 정책·Yield 확장 | config/model/profiles/skills/MCP/tools/memory/delegation 재사용 경계 → ③ 기능의 스텁·capability·오류/UI 숨김 → yield.* 확장 → legacy 변환 검증 | 채팅 외 모든 카테고리도 대응/거부 fixture 확보; 16종 서버 요청 무한 대기 없음; 비활성 기능을 명령·도구·설정으로 재활성화할 수 없음; 구 API 응답 호환 | 표의 모든 카테고리별 positive/negative 계약 테스트; 승인·취소·만료·다중 창·generation fencing, 비밀/로컬 경로 비노출, 확장 미협상 클라이언트 검사; 실제 허용 데이터·LLM·도구 E2E | **M2c 마일스톤(제안)**: 전체 계약 호환 후보. ③ 기준 준수·D11 신규 예외 승인 절차 및 미확인 검증이 필요; P2a 또는 채팅 성공만으로 전체 호환 완료 선언 금지 |
| P3 웹 UI 우선 이식·전환 | 5894e750 실제 Desktop의 채팅 중심 컴포넌트·store·스타일을 웹으로 이식(D10) → D03 폰트 제외/대체 및 별도 법무 확인 → 전체 gateway capability에 맞는 웹 bridge·설정/세션/도구 화면 연결 또는 숨김 → 표/artifact/verify/form 확장 → 제한 배포 | 원본 메시지·도구·입력 계약을 이식하되 D06 B안의 검증 전 답변 비공개·확정 답변만 공개 적용; 미지원 탭·명령·자동 구독/호출 차단; 웹 UI만 우선·opt-in 전환, Desktop 앱은 나중(D10); Electron 의존은 어댑터 뒤로 분리; 표 중복 없음; 검증과 완전성 구별; legacy 회귀 및 rollback 검증 통과 | 원본/이식본 동일 viewport·fixture 스크린샷 비교, 키보드·스크롤·긴 답변·넓은 표·artifact·폼·취소·새로고침 테스트. 실제 데이터/LLM E2E로 조회→도구→검증→확정→이력 재조회까지 확인 | Electron bridge·CSS·재수화 차이. Electron 전용 node-pty·dbus-native·get-windows 등의 라이선스·번들 영향은 Desktop 앱 단계 재확인(미확인). `yield_frontend` 유지, console부터 opt-in 전환. 실제 시나리오 통과 전 기본 UI 교체 금지 |

**기존 기술 작업 묶음(새 순서는 5.1 제안):** M0=P0 계약/해시·예외 후보 고정 → M1=P1 허용 실행 E2E → M2a=전체 RPC 표면 → M2b=저장·복구·HTTP/WS → M2c=기능 정책·확장·legacy 회귀 → M3=P3 UI 전환 후보. gateway 전체 범위로 작업량이 커져 P2를 세 단계로 분할했다. 실제 일정·인력 추정은 **미확인**이며 P0 의존 그래프와 6.1 미확인 항목 검증 후 산정한다. D10에 따라 웹 채팅 UI부터 이식하고 Desktop 앱은 나중에 진행한다.

### 5.1 서비스 마일스톤 재조정 — 제안

**순서 제안:** 1 채팅(+unsupported 응답 틀) → 2 권한·감사·사용량 한도 → 3 개인 설정·개인 스킬·개인 MCP → 4 스케줄링·알림. 실제 일정·인력은 **미확인**이다. 기존 P/M 번호는 기술 작업 묶음으로 남기고 아래 서비스 단계에 나누어 적용한다. 1단계도 기본 인증·본인 대화 접근 통제 없이 배포하지 않으며, 2단계는 다중 사용자 운영 통제를 완성하는 순서다.

| 새 서비스 단계(모두 제안) | 범위·선행 조건(제안) | 기존 M0~M3 / P0~P3 매핑(제안) | 완료 검증(향후 제안) |
|---|---|---|---|
| 1 채팅 + unsupported 틀 | 웹 채팅 opt-in, 배포 스킬만 읽기·사용 감사(개인 저장·편집은 3단계, 7.2), submit·확정 답변·tool 진행·검증 상태(D06: 검증 전 delta 비공개)·clarify·목록·이어받기·중지·결과/검증 표시, 미지원 응답 틀 | M0/P0 전체 계약 기준; M1/P1 채팅 실행; M2a/P2a 채팅 RPC와 나머지 거부 틀; M2b/P2b 세션/replay; M2c/P2c 확장/unsupported; M3/P3 채팅 UI 부분 | 본인 대화 실행→결과→검증→이력·재연결, 미지원 요청 무한 대기 없음. 이 단계만으로 gateway 전체 이식 완료 선언 금지 |
| 2 권한·감사·사용량 한도 | principal/session/profile 권한, 관리자 정책, 비용 admission·감사·취소/복구 | M0/P0 권한/비용 계약 보완; M1/P1 공유 예산; M2a/P2a 인증/admission; M2b/P2b 소유권/replay; M2c/P2c 우회 차단; M3/P3 사용량·감사 표시 | 타 사용자 ID·replay 접근 거부, 중복 실행/예산 초과 차단, 비밀 제거 감사 기록·권한 회수 검증 |
| 3 개인 설정·개인 스킬·개인 MCP | D08 사용자별 SOUL(시스템 규칙 아래)·메모리 Mongo·CWD 서버 고정, D12 사용자별 Mongo·본인만 사용·사전 검토 없음·사용 스킬 감사 기록 적용. 팀 공유는 나중. D13 HTTP(Streamable HTTP)만 자유 등록·stdio/SSE 제외·사용자별 Mongo 암호화 자격증명(키 분리)·Mongo 별도 컬렉션 감사(결과 본문 길이만 기록) 적용. MCP 추가 안전장치는 1차 제외, 필요 시 추가 | M0/P0 자산 계약; M1/P1 materialize/sync·MCP 발견/호출; M2a/P2a profiles/skills/MCP RPC; M2b/P2b 관련 라우터/저장; M2c/P2c toolset/설치 제한; M3/P3 개인 설정 UI | 재시작 후 개인 자산 유지, 동시 revision 충돌, OAuth 갱신/회수, HTTP 자유 등록·호출, stdio·SSE 등록·실행 거부(관리자·승인 폼 우회 포함), 본인 설정·비밀만 주입 및 사용자 간 연결·토큰·캐시 공유 금지, 누가·언제·어느 서버/도구 호출했는지 감사 로그 검증; alias·설치 우회 방지 |
| 4 스케줄링·알림 | **권한·예산·실패 처리·알림 전달 설계 선행**. 예약 소유자·실행 시 권한 재검사·수신자 ACL·재시도 한도·중복 방지 | M0/P0 예약/전달 계약 보완; M1/P1 독립 실행 admission; M2a/P2a cron.manage; M2b/P2b cron 저장/이벤트; M2c/P2c 정책/전달; M3/P3 예약/실패/알림 UI | 예약→허용 데이터/LLM/도구→리포트→권한 있는 수신자 전달 E2E. 권한 철회·예산 소진·서버 복구·중복 fire·전달 실패/재시도 검증 |

이 매핑은 **일대일 단계 치환이 아닌 제안**이다. 기존 M2=M2a/P2a + M2b/P2b + M2c/P2c로 유지하며, M3/P3 UI는 각 서비스 단계에서 점진 제공한다. 전체 계약 회귀·최종 전환 판정은 모든 단계와 D11 예외 확인 후 수행한다.

| 호환 경계 | 유지 방안(제안) | 회귀 판정 |
|---|---|---|
| `yield_frontend` | 기존 `/chat` 및 이력 소비 경로 유지. `Y/agent_server.py:557`의 compatibility_chat 분기, `:493`의 이력 경로를 회귀 기준으로 사용. frontend 세부 소비 계약은 P0에서 추가 확인 필요 | status/commentary/artifact/input/final의 기존 변환 유지 (`Y/harness/router.py:190`, `:215`, `:231`); 과거 이력·새 입력 모두 표시 |
| 현행 run API | URI·응답 키·SSE sequence 의미·취소/입력 검사 유지. 신 UI 전용 필드는 별도 계약에 제공 | `public_run` 및 기존 SSE fixture 비교 (`Y/harness/router.py:45`, `:148`). 기존 answer 합성은 legacy 변환에서 유지 |
| 저장 이력·활성 세션 | runtime/contract/선택 upstream 버전을 기록; 완료 이력은 어댑터로 읽고 진행 중 실행은 원 runtime에 고정 | 기존 runtime 선택 및 stale runtime 방어 유지 (`Y/harness/router.py:176`; `Y/harness/control.py:243`). 활성 세션 강제 마이그레이션 금지 |
| 배포·되돌리기 | 신 경로 opt-in과 기존 경로를 종료일 없이 병행(7.2); 전환 일정은 e2e·회귀 결과 후 합의. 저장 마이그레이션은 구 reader가 읽을 수 있는 추가 방식부터 적용 | UI만 되돌리는 경우와 엔진/API까지 되돌리는 경우 각각 재조회·재연결·신규 실행 검증 |

완료 판정에는 테스트 결과, 실제 실행 기록, 원본/이식 화면 비교, 남은 예외 목록을 함께 남긴다. lint·syntax·단위 테스트만으로 “동일 이식 완료”를 선언하지 않는다. 자연어 실패는 context·직전 메시지·structured result·request contract를 먼저 점검하며, 사례별 키워드 분기나 few-shot 누적으로 우회하지 않는다.

## 6. 확정 결정과 열린 결정

| 번호 / 상태 | 결정 항목 | 선택지 / 확정 내용 | 영향 / 결정 기록 |
|---|---|---|---|
| D01 / **결정됨** | 버전 기준 | **5894e750 전 층 통일**, e473f5a9는 회귀 비교점 | **2026-10-24 · 대환 님**. 엔진·gateway·생성 계약·UI에 동일 적용 |
| D02 / **결정됨** | `/api/ws` 호환 범위 | **gateway 전체**. 채팅 subset 아님 | **2026-10-24 · 대환 님**. 모든 계약에 구현/재사용/명시적 비활성 대응; 기능별 예외 승인은 D11에 남김 |
| D03 / **결정됨** | Desktop UI 라이선스 | assistant-ui(@assistant-ui/react 0.14.24, @assistant-ui/core 0.2.23, @assistant-ui/react-streamdown 0.3.5; MIT), streamdown 2.5.0(Apache-2.0), nanostores 1.4.2(MIT) 사용 가능. **@nous-research/ui 번들 woff2 폰트는 제외하거나 대체 폰트 사용**(fonts.css·폰트 파일을 가져오지 않음). 사내 법무 확인은 별도 진행 | **2026-10-24 · 결정자 대환 님**. Hermes MIT 근거 `H/LICENSE:1`, `:12`; 의존성 근거 `H/apps/desktop/package.json:81`, `:99` 및 아래 D03 조회 기록. @nous-research/ui 컴포넌트 코드 사용 범위·사용 버전·대체 폰트·peer/전이 의존 라이선스는 미확인/후속. 법률 자문 아님 |
| D04 / **결정됨** | SessionDB 역할 | 세션·대화 저장의 **유일한 기준은 Mongo**. Hermes SessionDB는 원본 인터페이스를 Mongo 위에 맞추며 병행 사본·이중 권위가 아님 | **2026-10-24 · 결정자 대환 님**. 이력·압축 조상·검색·replay·삭제 일관성의 기준. 현재 저장 근거 `Y/harness/store.py:30`, 원본 연결 `H/gateway/session_execution.py:177`; 어댑터 구현은 제안 |
| D05 / **결정됨** | native clarify/승인 허용 범위 | 계약·화면은 Hermes 동일 이식; sudo·secret 거부. 승인은 허용 도구의 효과 있는 호출에만 적용. 항상 허용은 사용자별 분리 저장·감사 로그 기록. timeout·재접속 복원 지원. 터미널·셸·파일·브라우저 비활성은 승인으로 우회 불가 | **2026-10-24 · 결정자 대환 님**. 원본 요청 근거 `H/tui_gateway/contracts/server_requests.py:59`, :195, :212, :222; authority `H/gateway/session_pending_controls.py:38`, :51. IPC 구현은 제안, 저장·감사 상세는 미확인/후속 |
| D06 / **결정됨** | 스트리밍 초안/확정 | **B안**: 검증 전 답변(delta/초안)은 숨기고 도구 진행·검증 상태만 표시, **확정된 답변만 공개**. native 답변 delta를 연결하더라도 검증 전 클라이언트 공개 금지. A(미확정 표시 후 공개)/C 확장은 나중에 가능 | **2026-10-24 · 결정자 대환 님**. 원본 `message.delta/interim/complete` 근거 `H/apps/shared/src/gateway-contract.generated.ts:7000`, `:7100`; 매핑·보류는 구현 제안, A/C 확장 시점·초안 내부 보존은 미확인/후속(4.3) |
| D07 / **결정됨** | fallback/provider | fallback은 **같은 endpoint 안에서만** 재시도·대체 모델 허용. 다중 provider 제외, 사용자 임의 모델·provider 설정 비활성 | **2026-10-24 · 결정자 대환 님**. 현재 모델 endpoint 제한 `Y/harness/native_budget.py:44`, `:51`; 공유 예산 호환 검증은 제안. 일반 MCP HTTP 정책과 구별 |
| D08 / **결정됨** | SOUL·CWD·메모리 | SOUL은 사용자별 허용하되 시스템 규칙 아래 층으로만 적용, 검증 gate·도구 제한 덮어쓰기 불가. CWD는 서버 고정(사용자 지정 불가). 메모리는 사용자별 Mongo 분리 저장 | **2026-10-24 · 결정자 대환 님**. 현재 SOUL off `Y/harness/native_worker.py:105`, 메모리 경계 `Y/harness/memory.py:46`~:51, :76~:82. 읽기 크기·세부 우선순위·동시 쓰기 구현은 제안/미확인; 스킬은 D12 |
| D09 / **결정됨** | 병렬 도구·delegate | 병렬 도구 호출 허용(동시 개수 제한). delegate(하위 에이전트)는 **1차 비활성**, 계약 수신 유지, 마일스톤 2에서 예산·감사 설계 후 재판단 | **2026-10-24 · 결정자 대환 님**. 현재 toolset 선언 `Y/harness/native_worker.py:100`, 병렬 경계 `E/agent/tool_dispatch_helpers.py:33`, `:190`. 동시 개수는 원본과 동일하게 배치당 최대 8(`H/agent/tool_executor.py:134`, :241, :248), 설정 조정 가능(구현 방식은 제안). 현재 Yield 쪽 적용 방식·delegation 도구셋 실제 동작은 **미확인/후속** |
| D10 / **결정됨** | UI 범위·전환 시점 | 우선 **웹 UI만**, 채팅 중심 원본 컴포넌트부터 opt-in 전환. Desktop 앱은 나중. Electron 의존(window.hermesDesktop 등)은 어댑터 뒤로 분리 | **2026-10-24 · 결정자 대환 님**. 결합 근거 `H/apps/desktop/src/store/gateway.ts:471`. 어댑터 구현·일정은 제안/미확인; node-pty·dbus-native·get-windows 등 Electron 네이티브 모듈의 라이선스·번들 영향은 Desktop 단계 재확인(미확인) |
| D11 / **결정됨(절차)** | 예외 승인 | 기준을 벗어나는 예외마다 **이유·범위·되돌리는 방법을 문서에 기록한 뒤 대환 님 직접 승인 후에만 구현**. 보안 관련 예외(도구 활성화·권한 확대)는 반드시 대환 님 승인 | **2026-10-24 · 결정자 대환 님**. 4장의 여섯 예외와 ③ 비활성 항목은 이 절차 아래의 기준. 개별 신규 예외는 승인 전 **미승인 후보**. 승인 대기 목록 현재 없음. 구현 방법·작업량은 제안이며 D05 승인 폼으로 제한 우회 불가 |
| D12 / **결정됨** | 개인 스킬 저장 정책 | 개인 스킬은 **사용자별 Mongo 저장·본인만 사용·사전 검토 없음·사용한 스킬 감사 로그 기록**. 팀 공유는 나중 | **2026-10-24 · 결정자 대환 님**. 현재 snapshot 근거 `Y/harness/skills.py:34`~:40, 저장 경계 `Y/harness/store.py:30`, :73. materialize/sync·CAS·기술적 안전 제한 및 감사 Mongo 별도 컬렉션 방식은 제안. 공유 ACL·공용 승격은 팀 공유 도입 시 후속(3.7) |
| D13 / **결정됨** | 개인 MCP 등록 범위·필수 조건 | **HTTP(Streamable HTTP)만 자유 등록, stdio·SSE 제외**(등록·실행 불가, stdio 관리자 예외도 1차 제외). 자격증명은 **Mongo에 암호화해 사용자별 저장**, 키는 서버 환경변수·비밀 저장소에 분리. 감사 로그는 **Mongo 별도 컬렉션**, 누가·언제·어느 서버/도구 호출인지 기록하고 **결과 본문은 길이만 기록**. 본인 설정·비밀만 주입, 사용자 간 연결·토큰·캐시 공유 금지. 주소 제한·프롬프트 주입 경고·호출 한도 등 기타 안전장치는 1차 제외, 필요 시 추가 | **2026-10-24 · 결정자 대환 님**. 원본 transport 근거 `H/tools/mcp_tool_transport.py:382`, :414, :532, :562; 현재 경계 `Y/harness/native_budget.py:44`~:54. 사내 플랫폼이 배포 환경 네트워크 등 보안을 막아 준다는 것은 **대환 님 전제(미검증)**이며 틀리면 **SSRF 위험이 가장 먼저 드러남**. 암호화 상세·감사 보존기간/인덱스는 미확인/후속, 구현 방법은 제안 |

6절 결정표의 D03 사용 범위·대체 폰트 및 D05 저장·감사 후속 중 방향은 7.2에서 구체화했다. 구체 컴포넌트·폰트·스키마 등 남은 상세는 6.1·7.3을 따른다. 결정표의 정책 의미와 상태는 유지한다.

D03~D13의 정책은 모두 **결정됨 — 2026-10-24, 대환 님**이다. 아래 라이선스 기록은 사용자가 제공한 2026-10-24 읽기 전용 조회 결과를 반영한 것이며, 이번 문서 수정에서 네트워크 재조회나 코드 변경은 수행하지 않았다.

**D03 라이선스 근거·조회 기록:**

- assistant-ui 패키지 근거: `H/apps/desktop/package.json:81`(@assistant-ui/core 0.2.23), :82(@assistant-ui/react 0.14.24), :83(@assistant-ui/react-streamdown 0.3.5); lock `H/package-lock.json:565`, :597, :636. npm registry 및 GitHub LICENSE 조회(2026-10-24): assistant-ui/assistant-ui는 MIT, Copyright 2026 AgentbaseAI Inc.
- streamdown 2.5.0은 `H/apps/desktop/package.json:151`, `H/package-lock.json:16814`; vercel/streamdown LICENSE는 Apache 2.0, Copyright 2023 Vercel. nanostores 1.4.2는 `H/apps/desktop/package.json:136`, `H/package-lock.json:14493`; nanostores/nanostores LICENSE는 MIT(같은 날 npm registry·GitHub 조회 기록).
- @nous-research/ui 0.18.2는 npm 공개 레지스트리 패키지다. `H/apps/desktop/package.json:99`의 일반 버전 문자열, `H/package-lock.json:3239`~:3243의 registry.npmjs.org resolve·MIT 표기가 근거다. 제공된 npm 조회 기록상 메타데이터는 MIT이나 repository·homepage 소스 저장소 링크가 없고, tarball 352개 파일에 LICENSE/NOTICE가 없다. package.json files는 dist, src, README.md, CHANGELOG.md이며 Collapse·Mondwest·Neuebit·RulesCompressed·RulesExpanded woff2 폰트가 포함되어 있으나 **폰트 라이선스는 미확인**이다.
- 같은 조회 기록에서 당시 최신 1.5.2 README는 폰트 파일을 포함하지 않고 앱별로 라이선스 있는 폰트를 따로 로드한다고 안내하며, 1.5.2 tarball의 woff 파일은 0개다. 이 기록이 0.18.2 번들 폰트의 이용 허락을 뜻하지 않는다. **결정됨 — 2026-10-24, 대환 님:** fonts.css·번들 폰트 파일을 가져오지 않고 제외 또는 대체한다. 7.2 합의로 필요한 컴포넌트만 사용하고 시스템 또는 사내 승인 폰트로 대체한다. 구체 컴포넌트 목록·사용 시점 버전(0.18.2 vs 최신)·개별 폰트는 미확인/후속. 사내 법무 확인은 별도 진행하며 이 문서는 법률 자문이 아니다.
- 별도 라이선스 항목(제공된 조회 기록): @novnc/novnc 1.7.0 MPL-2.0(파일 단위 카피레프트, `H/package-lock.json:3283`), @vscode/codicons 0.0.45 CC-BY-4.0(출처표기, :7063), dompurify 3.4.17 (MPL-2.0 OR Apache-2.0)(:9395). @streamdown/code·@streamdown/math·remend·class-variance-authority는 Apache-2.0으로 고지 유지. 이 네 패키지의 개별 file:line은 제공되지 않아 **미확인**이며 새 줄 번호를 추정하지 않는다. @nous-research/ui peer 의존(gsap, leva, three, @react-three/fiber, @observablehq/plot)과 전이 의존성 전체의 라이선스는 **미확인**이다.

### 6.1 남은 미확인·후속 항목

정책 결정과 구현 완료는 별개다. 아래는 확정 정책을 다시 선택하는 목록이 아니라 미확인 사실·구현 상세·후속 시점이며, 이번 작업은 문서만 수정했다.

- **D03:** 사내 법무 확인은 별도 진행(이식 전 별도 선행 확인 항목). @nous-research/ui는 필요한 컴포넌트만 사용하고 fonts.css·woff2를 제외하여 시스템 또는 사내 승인 폰트로 대체하기로 합의(7.2). 구체 컴포넌트 목록·개별 폰트·사용 버전·peer 및 전이 의존 라이선스는 미확인. 별도 라이선스의 고지·출처표기 적용 방법도 후속 확인.
- **D10:** Electron 네이티브 모듈(node-pty·dbus-native·get-windows 등) 라이선스·번들 영향은 Desktop 앱 단계 재확인(미확인); 웹 어댑터 구현·Desktop 일정은 제안/미확인. 기존 UI와 /runs·SSE는 종료일 없이 opt-in 병행 유지하며 전환 일정은 e2e·회귀 결과 후 합의(7.2).
- **D09:** 병렬 도구는 원본과 동일하게 배치당 최대 8로 합의(`H/agent/tool_executor.py:134`의 `_MAX_TOOL_WORKERS = 8`, :241, :248 사용; 5894e750 직접 확인). 설정 조정 가능(구현 방식은 제안), 현재 Yield 쪽 적용 방식은 미확인. 검증은 호스트 소유의 제한된 실행으로 분리하며 구체 경계는 PR-4·PR-5에서 확정(7.2). delegate는 마일스톤 2에서 예산·감사 설계 후 재판단하며 현재 delegation 도구셋 실제 동작은 미확인.
- **D06:** 스트리밍 A/C 확장 시점, 내부 delta/interim/complete 매핑·보류·초안 보존/교체 방법은 미확인/후속. B안 공개 정책은 확정.
- **D08·D12:** SOUL 읽기 크기·시스템 규칙 아래의 세부 우선순위, 메모리/스킬 동시 쓰기·snapshot/sync·복구 상세는 제안/미확인. 팀 공유 스킬은 나중이며 공유 ACL·공용 승격 절차는 그 도입 시점의 후속 항목. 개인 스킬 사전 검토는 요구하지 않음. 1단계는 채팅에 필요한 배포 스킬만 읽기 허용·사용 감사 기록, 개인 저장·편집은 마일스톤 3으로 합의; 실제 사용 스킬 목록은 미확인(7.2).
- **D05·D12·D13:** 항상 허용은 Mongo 승인 규칙 컬렉션과 감사 컬렉션을 분리하고 사용자+도구+조건 단위·본인만 적용·취소 즉시 효력 상실로 합의(7.2). 보존기간은 사내 정책(미확인), 상세 스키마·인덱스는 후속 제안. 사용 스킬·MCP 감사의 상세 스키마·보존기간·인덱스는 미확인/후속. MCP 감사 Mongo 별도 컬렉션과 결과 본문 길이만 기록은 확정; 스킬 감사 저장 위치를 같은 방식으로 두는 것은 제안.
- **D13:** MCP 자격증명 암호화 알고리즘·키 회전·KMS 사용 여부는 미확인/후속. Mongo 사용자별 암호화 저장과 암호화 키의 서버 환경변수·비밀 저장소 분리는 확정.
- **D13:** 사내 플랫폼의 배포 환경 보안은 **대환 님 전제(미검증)**이며 틀리면 SSRF 위험이 가장 먼저 드러남. 주소 제한·프롬프트 주입 경고·호출 한도 등 1차 제외 안전장치의 추후 추가 시점은 미확인/후속.
- **3.1~3.3:** ②/③ 대응 중 실제 제품 필요성 미확인 항목, 외부 mount·인증별 호출 가능성·구현 비용은 후속 검증. 확정된 비활성 정책 변경은 D11 절차를 따른다.
- **PR-1 분류 후속 검토:** host identity의 pid 노출 허용·Yield 토큰 연동, 상태/설정 HTTP 응답 필드 필터, `llm.oneshot`, `slash.exec`·`command.dispatch`, `usage.bars`·`insights.get`, steer/queue 분류 불일치는 **미확인**이다. 이번 분류 변경에 포함하지 않으며 현재 상태·file:line 근거는 7.4 후속 검토 표 참조.
- **D11 신규 예외 승인 대기 목록: 현재 없음.** 새 후보는 미승인 후보로 표시하고 이유·범위·되돌리는 방법을 문서에 기록한 뒤 대환 님 승인 후에만 구현한다. 보안 관련 도구 활성화·권한 확대는 반드시 대환 님 승인.

## 7. 마일스톤 1 구현 계획(안)과 구현 전 합의 결과

### 7.1 마일스톤 1 구현 계획(안) 요약

마일스톤 1은 **웹 채팅과 전체 계약의 명시적 거부 틀**을 제공하는 단계이며 gateway 전체 이식 완료를 뜻하지 않는다. 5894e750 전 층 기준·Mongo 단일 저장 권위·D06 B안·기존 UI/API 병행을 유지한다. 아래 PR 분할·후속 파일 배치·규모는 **계획 제안**이다. PR-1은 승인 하에 구현·검증 완료(사용자 제공 상태)이며, 이름별 분류는 **PR-1 manifest 기준 확정치(2026-10-24)**다. PR-2 이후 구현과 통합 테스트는 별도 지시가 필요한 계획이며 이번 작업에서는 실행하지 않았다. 로그의 초기 질문·권고보다 7.2의 후속 합의를 우선하며, 실제 설정 수치는 코드 기본값과 구별하여 운영값 **미확인**으로 남긴다.

원문은 /tmp/m1_plan_codex.log 6059~6555줄(Mac, 비영속 파일일 수 있음).

전체 방법명 표는 복제하지 않는다. **PR-1 계약 manifest로 분류가 확정되었다** (`Y/harness/gateway/manifest.json:65`). 이 절의 계획 근거는 위 로그이며, 아래 원본 file:line은 로그에서 재인용한 근거다. 당시 계획 작성에서 직접 연 소스는 7.2의 principal·검증 helper·병렬 상한 부분이다. 이번 동기화는 manifest·rpc_rules.py·policy.py를 읽어 대조했다. 로그의 B:줄은 수정 전 603줄 문서 기준이므로 새 문서 줄 번호로 재해석하지 않는다. 이하 G는 `H/apps/shared/src/gateway-contract.generated.ts`다.


**현재 집계 — Python으로 manifest 직접 집계; 구현/stub/unsupported 순서:** RPC 38/58/187, 이벤트 35/9/42, 서버 요청 2/2/12, HTTP 20/71/191, WS 1/0/7, 외부 인증 3/0/10. 전체 합계·변경 전후·집계 경계는 7.4 참조.

**범위 분류 — PR-1 manifest 기준 확정치(2026-10-24), 기능 연결은 후속**

| 분류 | 의미 | 254 legacy·48 authority 분류 요지와 근거 |
|---|---|---|
| 1단계 구현 (`implemented`) | M1 구현 대상 분류. 실제 처리·저장·권한·복구 연결 완료를 뜻하지 않음 (`Y/harness/gateway/policy.py:3`) | 연결·capability·세션 생성/목록/재개/중지/이력·submit/receipt/ACK 유실 복구·replay·clarify/approval. 비밀 없는 config, 고정 모델 정보, 본인 profile, 허용 도구 목록의 제한된 projection. legacy에는 i18n·paste.collapse·제목·usage, authority에는 info/detach/stats 포함. `G:6005`~:6514, :6774~:6852 |
| stub | 후속 단계 대상으로 이름·스키마·거부 처리만 제공; 외부에는 unsupported와 단계 유예 사유 | 명령·설정 변경·피드백·세션 관리 확장·수동 압축 UI·별도 oneshot/btw·queue 편집. `session.delete`·`session.export`는 stub 유지. 개인 설정/스킬/MCP는 마일스톤 3, cron은 4. 내부 자동 압축은 보존. 원본 verification.status는 유예하고 Yield 판정은 yield.verify로 분리. `G:6005`~:6514, :6774~:6852 |
| unsupported | 확정 비활성 정책/③ 기준에 따른 실행 거부 | 터미널/셸/CLI/브라우저/범용 파일·첨부·프로젝트·로컬 runtime·비밀/provider 관리·음성/화면·pet/결제·groups/relay/telemetry·일반 delegate/spawn tree·같은 실행 steer/redirect·장기 background·외부 worker 제어(`worker.adopt/persist/register`)·로컬 파일 저장(`session.save`). plugin 설치/갱신과 MCP stdio/SSE 등은 유예 기능 안에서도 거부. `G:6005`~:6514, :6774~:6852 |

legacy 254개와 authority 48개는 이름 19개가 중복되고 합집합은 283개다(3.1). 실행 권위는 하나로 두되 모드별 params/result를 별도 어댑터·fixture로 검증한다. `prompt.submit`의 지원을 모든 busy-mode 지원으로 광고하지 않으며 비활성 액션은 별도 거부한다. stub도 빈 목록·가짜 성공으로 위장하지 않는다. 원본 typed unsupported 결과가 있으면 우선한다(`H/tui_gateway/ws.py:357`~:385, `H/tui_gateway/contracts/liveness.py:21`~:26).

| 이벤트·요청·라우트 | 1단계 처리 — 제안 | 근거 |
|---|---|---|
| 이벤트 78+8종 | 타입은 유지. message.start는 상태만, delta/interim은 내부 보류, complete는 pass를 받은 최종 revision만 한 번 공개. tool/status/session·pending/settled·replay_gap 연결. reasoning/thinking/agent.print 원문은 공개하지 않고 상태로 대체; 비활성 producer/watcher는 미기동 | 3.2; `H/tui_gateway/contracts/events.py:147`~:183, :280~:303, :395~:457; `H/gateway/session_pending_controls.py:18`~:101 |
| 서버 요청 16개 | approval·clarify만 구현. theme.apply/theme.pick 2개 유예. 나머지 12개(display.install.sudo, preview.act/read, secret, setup_beat, sudo, terminal.read, tour, vault.code/save_login/unlock_prompt, window.read)는 거부. 원본 decline 또는 -32601로 응답해 무한 대기 방지; 4404를 일반 unsupported로 쓰지 않음 | `G:6942`~:6958; `H/tui_gateway/contracts/liveness.py:29`~:48 |
| WS 8개 | /api/ws만 활성. 음성 2개·console·pty·pub·events·display/ws 7개는 거부. /api/events는 PTY sidecar WS이며 기존 run SSE나 replay의 대체재가 아님 | 3.3.1; `H/hermes_cli/web_routers/chat_ws.py:702`, :737 |
| 최소 활성 REST | 세션 목록·메시지, 사내 인증 연결, 필요한 health/identity/status 및 허용 config/model/tools/profile 조회, 기존 결과·artifact API. 세션 검색/단건 삭제/서버 export·개인 설정·스킬/MCP·cron 등은 stub. 운영자 POST 세션 import/owner-backfill/prune/bulk-delete 및 memory/reset은 unsupported(7.4), 호스트 운영/파일/설치/비밀 표면은 거부. 외부 native token·memory OAuth·plugin mount 제외, SPA만 opt-in 연결 | 3.3; `H/hermes_cli/web_routers/sessions.py:182`, :739; `Y/harness/router.py:157`~:173 |

**unsupported 프레임 — 제안:** 알려진 비활성 RPC는 `error:{code:-32004,message:"unsupported",data:{reason:"unsupported",feature,upstream:"5894e750"}}`, unknown 이름은 `-32601`로 구별한다. HTTP는 501과 `error:{code:"unsupported",reason:"unsupported",feature,upstream:"5894e750"}`를 사용하고 인증/소유권 오류를 우선한다. `yield.capabilities`는 schema_version·upstream·rpc_mode·methods별 status/reason·features를 갖는 별도 RPC이며 원본 gateway.capabilities에 섞지 않는다. 비지원 WS는 **인증 후 upgrade 거부 403을 우선 제안**하되 ASGI/client 호환성은 미확인이다. legacy fallback에서도 거부를 우회할 수 없게 한다. `-32004`의 전체·동적 확장 충돌 확인은 PR-1 완료 조건이다(3.4; `H/tui_gateway/rpc_dispatch.py:17`~:41; `H/tui_gateway/ws.py:368`~:385).

**B안 데이터 흐름 — 제안**

`AIAgent callback → worker IPC → 내부 후보 저장/검증 → 공개 projection → Mongo 공개 이벤트 → gateway/REST/legacy 변환`

검증 전 후보는 **서버에서 차단**한다. callback의 내부 ID를 보존하고 candidate_message_id+candidate_revision과 실제 최종 답변이 일치하는 pass만 공개한다. fail/unknown/cancel은 검증·종료 상태만 표시하고 후보 본문·표·artifact는 공개하지 않는다. 우회 공개 검사 대상은 delta뿐 아니라 interim→commentary→store.event, tool.complete.result/args/summary, 검증 observation의 validated_arguments.candidate, 결과·표·artifact 조회, REST 이력·replay, 신규 runtime의 legacy /runs·SSE·/chat/stream·이력 변환이다. 구 응답 키와 구 runtime 동작은 유지한다(`Y/harness/native_worker.py:106`; `Y/harness/native_hermes.py:494`~:507; `Y/harness/native_stop_gate.py:53`~:64; `Y/harness/router.py:45`~:46, :134~:154; `Y/agent_server.py:493`~:525, :555~:558). 검증기 조사에서 확인한 현행 표 선공개 동작은 목표 B안의 완료 근거가 아니다(`/tmp/verif_explain_codex.log:5096`~:5314).

**PR 분할 — PR-1 완료 상태 반영, PR-2 이후는 제안·검증 계획**

원본은 출처·커밋·해시와 최소 patch를 추적하고 Yield 어댑터와 분리한다. 아래는 원래 PR 분할 계획이다. PR-1은 승인 하에 구현·검증 완료(사용자 제공 상태); PR-2 이후 파일은 제안 경로이며 이번 작업에서 생성하지 않는다. SessionDB는 Mongo 위에서 원본 이력·압축 조상 의미를 보존한다. 동기 호출과 Motor의 차이는 저장 호출 경계에서 해결하고 부모를 Mongo 접근 권위로 두는 제안이다(`H/hermes_state.py:463`~:469; `H/hermes_cli/web_routers/sessions.py:747`~:765; `Y/harness/store.py:30`~:37).

| PR | 작업·제안 파일 | 의존 | 완료 기준·검증 계획 / 근거 |
|---|---|---|---|
| PR-1 계약 기준·거부 틀 — 완료(사용자 제공 상태) | 원본 계약 manifest, gateway/capabilities.py·unsupported.py, 계약 fixtures | 없음 | 254 legacy·48 authority·283 이름 대조; 86 event·16 request·HTTP/WS 행렬과 세 분류 확정. -32004 충돌·unknown/unsupported·typed 거부 검증. `H/tui_gateway/server.py:906`; 3.1~3.4 |
| PR-2 인증·웹 호스트 경계 | gateway/auth.py·routes.py, agent_server.py mount | PR-1 | REST/WS 서버 확정 principal, 익명·타 사용자 profile/session 거부, host/operator 자동 승계 없음. 비활성 route의 탐색·설치·프로세스 기동 차단. `Y/harness/router.py:40`~:42; `H/hermes_cli/web_routers/chat_ws.py:704`~:732 |
| PR-3 Mongo 세션 어댑터 | gateway/session_db.py·session_store.py, store.py 컬렉션/인덱스 | PR-2 | 생성·목록·resume·pagination·압축 조상, 동일 ID의 principal 격리, SQLite 이중 권위 없음, 동시 submit 단일 실행. `H/hermes_cli/web_routers/sessions.py:747`~:765; `Y/harness/store.py:40`~:54 |
| PR-4 엔진 pin·IPC·예산 | harness/engine 5894e750 동기화, native_worker.py·native_hermes.py·native_ipc.py·native_budget.py | PR-1, PR-3 | 원본 해시/patch 기록, callback/제어 상관 ID 분리, 취소 waiter 해제·cooperative interrupt·강제 종료 fallback. 같은 endpoint 재시도·검증 예약. 일반 delegate 차단과 호스트 검증 분리, 배치당 8·직렬화·설정·허용목록/예산 상호작용 확인(7.2). `Y/harness/native_worker.py:30`~:50, :73~:137 |
| PR-5 검증과 공개 제어 | gateway/publication.py, native_stop_gate.py·native_verification.py, 신규 runtime router/이력 projection | PR-3, PR-4 | WS·SSE·REST·replay·artifact·검증 observation에 검증 전 본문 없음. pass/revision 일치 때 한 번 공개, fail/unknown/cancel은 상태만. 호스트 검증 실행 경계 구체화. `Y/harness/native_stop_gate.py:53`~:69; 4.3 |
| PR-6 clarify·approval | gateway/pending_controls.py, IPC 확장, pending/decision/audit 저장 | PR-2, PR-3, PR-4 | 두 wire 재현, timeout·재접속·다중 창·중복 응답·generation 불일치 처리. always 본인 규칙/감사 컬렉션 분리·취소 즉시 무효. sudo/secret·제한 도구 우회 거부. `H/gateway/session_pending_controls.py:66`~:101; 7.2 |
| PR-7 submit·receipt·replay·REST 연결 | gateway/execution.py·events.py, 원본 session/prompt handler 어댑터 | PR-3~PR-6 | ACK 유실 재시도 단일 실행, run sequence/session seq 분리, epoch/gap/truncated snapshot 복구, 세션 전환·서버 재시작·pending 복원. `H/tui_gateway/event_replay.py:1`~:26; `H/gateway/session_events.py:29`~:63 |
| PR-8 웹 transport·store 이식 | console src/hermes/·src/platform/web-host.ts·src/protocol/ | PR-1, PR-2; 통합 PR-7 | Electron 없이 연결·협상·세션 state·receipt·replay, unsupported 자동 호출 없음. `H/apps/desktop/src/store/gateway.ts:1`~:34, :471~:491 |
| PR-9 채팅 UI·Yield 결과 확장 | 원본 ChatView/Thread/composer/clarify/approval, src/yield/ 렌더러, main.tsx opt-in | PR-7, PR-8 | 원본 상호작용·B안·표 중복 제거·결과/검증/입력 표시, 번들 폰트 제외와 대체 방침 적용(7.2). `H/apps/desktop/src/app/chat/index.tsx:1`~:92; 5장 P3 |
| PR-10 통합 회귀·제한 배포 | 계약·통합·e2e fixtures, rollout/rollback 설정·문서 | 전체 | 실제 허용 데이터→LLM→도구→검증→확정→이력 재조회. 기존 UI 회귀 및 UI만/API까지 두 rollback 검증. 5.1 호환 경계; 연결 상태·결과 미확인 |

**프론트 이식 — 제안**

| 가져올 단위 | 역할·근거(원본 import closure 포함) |
|---|---|
| ChatView·thread·composer | AssistantRuntimeProvider·메시지/목록/timeline/scroll·ChatBar 제출. `H/apps/desktop/src/app/chat/index.tsx:1`~:92; `H/apps/desktop/src/components/assistant-ui/thread/index.tsx:3`~:18 |
| session-view·session stores·hooks | 세션별 state 격리, submit/resume/message-stream·receipt·ACK 유실·pending 복원. `H/apps/desktop/src/app/chat/session-view.tsx:25`~:43, :73~:101; `H/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:1145`~:1177 |
| clarify·approval | 질문 store와 inline/floating 승인·once/session/always/deny. `H/apps/desktop/src/components/assistant-ui/clarify/index.tsx:7`~:39; `H/apps/desktop/src/components/assistant-ui/tool/approval.tsx:8`~:45 |
| markdown·표·코드 | assistant-ui/streamdown 렌더러와 YieldVerify/YieldTable/YieldArtifact 연결. `H/apps/desktop/src/components/assistant-ui/markdown-text.tsx:3`~:49 |
| store/gateway·shared 계약 | transport·재시도·서버 요청·세션 이벤트 routing. `H/apps/desktop/src/store/gateway.ts:1`~:34; `G:6005`, :6774 |

웹 opt-in → WebHostAdapter → HermesGateway/shared/capability → event/receipt/pending stores → 세션별 SessionView/hooks → AssistantRuntimeProvider/ChatView → Thread·ChatBar·Yield 렌더러 순으로 연결한다. `window.hermesDesktop`는 WebHostAdapter로 치환하고 동일 서비스의 인증된 연결·허용 profile routing만 제공한다(`H/apps/desktop/src/store/gateway.ts:471`~:491). sudo·secret·vault store를 직접 가져오는 **PromptOverlays는 통째 비활성**으로 둔다(`H/apps/desktop/src/components/prompt-overlays.tsx:25`~:50). native window·terminal·preview·첨부·모델 설정·프로젝트·온보딩·pet·음성·다중 pane은 capability로 렌더링과 effect/polling/구독을 함께 끈다. 로컬 media/file 링크는 소유권을 확인하는 결과 ID 기반 Yield artifact로 제한한다. @nous-research/ui는 필요한 컴포넌트만 쓰고 fonts.css·woff2를 제외하여 시스템 또는 사내 승인 폰트로 대체한다(7.2). 원본 Thread를 기존 RunView로 대체하지 않는다.

**opt-in·rollback — 제안:** 기본 진입은 기존 App, 새 UI는 명시적 opt-in이다. 서버가 세션 runtime/contract/upstream을 결정하고 활성 세션을 강제 이식하지 않는다. 기존 yield_frontend·/runs·SSE·/chat/stream·과거 이력은 종료일 없이 병행 유지한다(7.2 합의). UI rollback은 새 UI 선택만 끄고 신규 runtime의 활성 실행은 계속 처리한다. 엔진/API rollback은 신규 admission을 중단하고 활성 실행을 원 runtime에서 종료/취소한 뒤 구 경로로 새 실행을 받는다. 저장 필드는 additive하게 도입하고 구 reader 재조회·재연결을 검증한다. 신규 runtime의 B안은 구 UI로 접근해도 적용한다(5.1; `Y/harness_console/src/main.tsx:3`~:5).

**보안·제한 — 확정 정책과 7.2 합의를 따르는 구현 제안**

| 결정 | 1단계에서 할 일 | 하지 않는 일 | 근거 |
|---|---|---|---|
| D05 | clarify/approval·pending 복원·timeout·stale/중복 방지, Mongo의 본인 always 규칙/감사 분리·즉시 취소 | sudo·secret, 승인으로 terminal/shell/file/browser 개방 | 6절 D05·7.2; `H/gateway/session_pending_controls.py:66`~:101 |
| D07 | 고정 endpoint·같은 endpoint fallback/재시도/검증의 공유 예산·임의 모델 설정 차단 | 다중 provider·사용자 API key 관리 | 6절 D07; `Y/harness/native_budget.py:44`~:90 |
| D08 | 서버 CWD·타 사용자 profile/memory 주입 차단·개인 자산 단계 유예 명시 | 개인 SOUL/메모리 CRUD UI·동시 편집/sync 완성 | 5.1·6절 D08; `Y/harness/native_worker.py:105` |
| D09 | 배치당 최대 8·원본 직렬화 규칙·설정 조정, 효과/공유 상태 확인, 일반 delegate 차단, 호스트 소유 제한 검증 분리 | 사용자/모델 주도 subagent·spawn tree, M2 재판단 전 일반 delegate 재활성화 | 7.2; `H/agent/tool_executor.py:134`, :241, :248; `Y/harness/native_worker.py:108`~:137 |
| D12 | 채팅에 필요한 배포 스킬만 읽기 허용·사용 감사, 실제 목록 확인 | 개인 스킬 저장/편집·CRUD/materialize/sync·팀 공유; 사전 검토 제도 도입 | 5.1·7.2; `Y/harness/native_worker.py:77`~:100 |
| D13 | 개인 MCP API 유예 거부·자동 탐색/reload/plugin 우회 검사·stdio/SSE 차단 | 개인 HTTP MCP 등록/OAuth·암호화 키 관리 구현은 M3. 주소 제한·주입 경고·별도 호출 한도 등을 M1 필수로 추가하지 않음 | 3.8·5.1·6절 D13; `Y/harness/native_budget.py:44`~:54 |
| 기본 인증 | HTTP/WS 검증된 principal·본인 session/profile/result/artifact/replay/pending 접근 통제 | settings fallback을 배포 인증으로 사용; M2 관리자/조직 운영 화면 완성 | `Y/harness/router.py:40`~:42; 7.2 |

사내 플랫폼 보안은 **대환 님 전제(미검증)**다. 모델 endpoint 검사는 일반 HTTP/MCP egress·OS sandbox 보장의 근거가 아니다. 스킬/memory/delegation 활성 선언과 profile 디스크 경로를 그대로 두어 단계 유예가 우회되지 않게 하는 구현을 제안한다(`Y/harness/native_worker.py:77`~:100; `Y/harness/native_hermes.py:347`~:376).

| 위험 | 영향 | 대응 제안·근거 |
|---|---|---|
| SessionDB/authority 저장 호출 범위 미확인 | 단순 façade로 압축 조상·resume 의미 누락 | PR-3에서 활성 handler 저장 closure·동기 호출 목록 확인. `H/hermes_state.py:463`~:469; `H/hermes_cli/web_routers/sessions.py:747`~:765 |
| B안 우회 공개 | commentary·candidate·결과·legacy 이력으로 초안 유출 | 모든 읽기 표면에 공통 공개 projection. `Y/harness/native_hermes.py:494`~:507; `Y/agent_server.py:512`~:525 |
| 글로벌 상태/profile 혼선 | 타 사용자 이벤트 혼입 | principal/profile key·generation fencing·동시 사용자 검증. `H/gateway/session_events.py:15`~:18 |
| ACK 유실 | 도구 이중 실행·이중 과금 | 영속 receipt·admission 원자성, 모호한 ACK에서 새 ID 자동 재제출 금지. `H/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:1149`~:1177 |
| IPC pending 교착 | 취소/종료 뒤 승인 대기 잔존 | 단일 수신 루프·제어/응답 구분·종료 waiter 정리. `Y/harness/native_worker.py:30`~:50 |
| verifier와 D09 경계 | 검증 제거 또는 delegation 우회 | 분리 방향은 합의, 구체 실행 경계는 PR-4/5에서 확인. `Y/harness/native_worker.py:108`~:137 |
| UI 의존·라이선스 | 이식 범위 증가·금지 폰트 포함 | import graph·번들 검사·법무 확인. `H/apps/desktop/src/app/chat/index.tsx:1`~:92; 6절 D03 |
| 원본 초기화 부작용 | 숨긴 기능의 탐색·설치·watcher 기동 | 허용 lifecycle만 기동·초기화 경계 검증. `H/hermes_cli/web_routers/chat_ws.py:706`~:711; `H/hermes_cli/web_server.py:824`~:828 |
| 인증 규격·Yield 병렬 적용 미확인 | 배포 인증·안전한 실행 조건 미완성 | 상한은 8로 합의; PR-2 규격 및 PR-4 허용목록/예산 상호작용 확인. `Y/harness/router.py:40`~:42; 7.2 |

**규모 — 계획 예산용 제안, 추정 오차 큼:** Yield 백엔드 신규 8~12·기존 7~10 모듈, Yield 프론트 6~10 모듈, 테스트 12~20 파일, 수기 작성/수정 코드 **5,000~10,000줄**(원본 복사·generated·테스트 별도). 원본 백엔드 import closure는 수백 파일, 프론트는 수십~100여 파일 가능성이 있다는 원문 추정이며 실제 수·일정·인력은 미확인이다. PR-1 manifest/import closure로 재산정한다(`/tmp/m1_plan_codex.log:6059`~:6555).

| 검증 층 | 계획 시나리오·완료 기준 — 모두 계획·미실행 |
|---|---|
| 단위·계약 | 254 legacy·48 authority·19 중복 이름의 모드별 params/result; unknown/stub/unsupported/typed 거부·HTTP/WS·16 요청. 인증 우선·무한 대기/가짜 성공 없음 |
| 저장·IPC·예산 통합 | 두 principal의 같은 session ID·동시 submit·압축 조상/pagination, 승인 대기 취소/EOF/timeout/늦은·중복 응답, 같은 endpoint 동시 호출/재시도/fallback/verifier 예약. 격리·단일 실행·waiter 정리·상한 후 호출 차단 |
| 공개 제어·복구 | 후보 식별 문자열로 pass/fail/unknown/cancel 검사: pass 전 WS/SSE/REST/replay/결과/이력 비노출, 통과 revision만 공개. ACK 유실·seq gap·ring 초과·epoch 변경·재시작·다중 창에서 snapshot/pending 복원 |
| UI | IME Enter·긴 답변·넓은 표·스크롤·세션 전환·승인/질문·deep link·원본 화면 비교. 표 중복/타 대화 state 혼입/unsupported polling 없음 |
| e2e·회귀·rollback | 실제 허용 Oracle/MES 조회→실제 LLM→도구→검증→확정→새로고침/재개, 결과 ID·검증 revision·이력 일치. 기존 console/yield_frontend·API 키·SSE sequence·구 이력 및 UI만/API까지 rollback 검증. 실제 연결 상태·결과 미확인 |

검증 계획 근거는 `/tmp/m1_plan_codex.log:6059`~:6555 및 5장·5.1이다. lint/build/단위 테스트만으로 M1 완료를 선언하지 않는다. 이번 문서 작업에서는 위 테스트와 서버·DB·LLM 실행을 수행하지 않는다.

### 7.2 구현 전 합의 결과

**결정자: 대환 님 · 2026-10-24.** 아래 7건은 이번 요청으로 제공된 **구현 상세 합의**이며 D01~D13의 변경이 아니다. 일반 delegate 비활성과 호스트 검증을 분리하고 개인 기능의 단계 유예를 명시하므로 기존 D 결정과 모순되지 않는다.

| 번호 | 질문 | 합의 내용 | 근거·남은 미확인 |
|---|---|---|---|
| 1 | principal 인증 경로 | 사내 플랫폼이 검증한 세션·토큰 연계. 현재 settings fallback은 배포 인증으로 쓰지 않음 | `Y/harness/router.py:40`~:42 직접 확인: request.state.principal_id가 없으면 settings.principal 사용. 전달 규격·위조 방지·WS origin/ticket 책임은 **미확정(미확인)**. 사내 플랫폼 보안은 **대환 님 전제(미검증)** 유지 |
| 2 | D09 비활성과 verifier 구분 | 검증기를 **호스트(서버) 소유의 제한된 실행**으로 분리. 검증 하위 에이전트에는 저장 결과 읽기 도구 read_result만, toolset은 yield_evidence만 허용. 일반 delegate 비활성과 충돌하지 않게 함 | `Y/harness/native_worker.py:108`~:137 직접 확인: delegate helper·yield_evidence·남은 예산 사용. `/tmp/verif_explain_codex.log:5096`~:5314 조사상 판정은 pass/fail/unknown, partial은 실행 상태. 40회·120,000토큰·300초는 `Y/harness/config.py:18`~:21의 **코드 기본값(조사 재인용), 운영값 미확인**. 깊이 설정에 따른 delegation 추가 가능성도 조사에 있어 제한이 이미 완성됐다고 단정하지 않음. 호스트 실행 경계의 구체 형태는 **미확인**, PR-4·PR-5에서 확정 |
| 3 | 병렬 도구 동시 개수 | Hermes 원본과 동일하게 **배치당 최대 8 worker thread**. 공유 상태가 안전하지 않은 도구의 직렬화 규칙도 원본 것을 가져옴. 값은 설정으로 조정 가능(구현 방식은 **제안**) | `H/agent/tool_executor.py:134`의 `_MAX_TOOL_WORKERS = 8`, :241·:248 사용을 5894e750 기준 **직접 확인**. run 전체 동시성 수치로 바꾸어 해석하지 않음. Yield 도구 허용목록·예산과의 상호작용 및 현재 적용 방식은 **미확인**, PR-4에서 확인 |
| 4 | 1단계 스킬 범위 | 채팅에 필요한 **배포 스킬만 읽기 허용**, 사용 기록 감사. 개인 스킬 저장·편집은 마일스톤 3 | 5.1·D12 및 이번 합의. 현행 snapshot 근거 `Y/harness/skills.py:34`~:40, worker 선언 `Y/harness/native_worker.py:77`~:100(문서/계획 재인용). 현재 실제 사용하는 스킬 목록은 **미확인** |
| 5 | 항상 허용 저장·감사 | Mongo **승인 규칙 컬렉션과 감사 컬렉션 분리**. 사용자+도구+조건 단위, 본인에게만 적용, 취소 즉시 효력 상실. 터미널·파일·브라우저 등 비활성 기능은 승인으로 열리지 않음 | D05·이번 합의; 저장 경계 `Y/harness/store.py:30`(기존 문서 근거). 보존기간은 사내 정책(**미확인**), 스키마·인덱스 상세는 후속 **제안** |
| 6 | UI 코드·폰트 범위 | @nous-research/ui는 필요한 컴포넌트만 사용. fonts.css·woff2 제외, **시스템 폰트 또는 사내 승인 폰트**로 대체. 법무 확인 별도 | D03 조회 기록·이번 합의; `H/apps/desktop/package.json:99`, `H/package-lock.json:3239`~:3243(기존 근거). 사용 버전 **0.18.2 vs 최신**, 구체 컴포넌트/폰트, peer·전이 의존 라이선스는 **미확인** |
| 7 | legacy 종료 시점 | yield_frontend 및 **/runs·SSE를 종료일 없이 유지**, 새 UI opt-in 병행. 전환 일정은 e2e·회귀 결과 후 합의 | D10·5.1·이번 합의; `Y/agent_server.py:493`, :557; `Y/harness/router.py:148`(기존 근거). 전환 일정과 실제 검증 결과는 **미확인** |

### 7.3 착수 조건

아래는 7.2 합의를 PR 의존성에 연결한 기록이다. PR-1 완료 상태를 반영하며 PR-2 이후는 **착수 판단 제안**이다. 방향 합의는 구현·배포 조건 검증 완료를 뜻하지 않는다.

| 질문/작업 | 영향 PR | 조건과 판단 — 제안 |
|---|---|---|
| PR-1 완료 | PR-1 | 대환 님 승인 하에 계약 manifest·거부 틀 구현·검증 완료(사용자 제공 상태). 현재 분류는 7.4에 정리. 인증된 서비스 공개·배포 또는 runtime 연결 완료를 뜻하지 않음 (`Y/harness/gateway/manifest.json:6`) |
| 질문 1 인증 | PR-2 | 방향 합의됨. 전달 규격·위조 방지·WS origin/ticket 책임이 미확정이므로 **PR-2 설계 단계에서 규격을 확정하고, 확정 전 인증 연결 구현은 시작하지 않는 방안 제안**. 플랫폼과 앱의 책임을 문서화하는 작업 자체가 auth/host 설계 범위이기 때문이며 착수 가능을 단정하지 않음. settings fallback 대체·재접속 재검증까지 확인 필요 |
| 질문 2 verifier | PR-4·PR-5 | 방향의 충돌 **해소**. 호스트 소유 실행 경계·저장 결과 읽기 제한·공유 예산·일반 delegation 차단의 구체 형태는 PR-4/5 설계에서 확정 |
| 질문 3 병렬 | PR-4 | 한도 값 질문 **해소**(배치당 8). 원본 직렬화 규칙 이식·설정 방식·Yield 허용목록/공유 예산 상호작용은 PR-4 설계·검증 조건 |
| 질문 4 스킬 | PR-4·PR-6·PR-10 | 범위 질문 **해소**. PR-4에서 배포 스킬 읽기·개인 쓰기 우회 차단과 사용 감사 연결, PR-6의 감사 저장 경계와 연계하는 분할 제안. PR-10에서 사용 기록 검증; 실제 스킬 목록은 PR-4 전에 확인 필요 |
| 질문 5 항상 허용 | PR-3·PR-6·PR-10 | 저장 방향 **해소**. PR-3 저장 경계와 PR-6 규칙/감사 스키마·인덱스·즉시 취소 반영 설계. 보존기간 사내 정책 확인 및 PR-10 회귀 검증 필요 |
| 질문 6 폰트 | PR-9(의존성 검토는 PR-8 연계) | 제외·대체 방향 **해소**. 법무·사용 버전·peer/전이 라이선스는 **미확인**이며 이식 전 선행 확인 항목 유지. 방향 합의만으로 패키지 사용 검토 완료를 주장하지 않음 |
| 질문 7 opt-in 유지 | PR-9·PR-10 | 유지 기간 질문 **해소**(종료일 없음). PR-9 opt-in 진입, PR-10 기존 UI/API·두 rollback 검증 후 전환 일정 별도 합의 |

| 남은 미확인 항목 | 영향 PR | 확인 방법 제안 |
|---|---|---|
| PR-1 분류 경계·응답 필드 필터 | PR-2 이후 별도 검토 | host identity pid/토큰, 상태·설정 응답 필터, oneshot·명령·사용량·steer/queue의 현재 분류를 유지하고 7.4 후속 검토 표의 근거와 정책을 대조. 결론은 **미확인** |
| 인증 전달 규격·위조 방지·origin/ticket·재접속 책임 | PR-2 | 플랫폼 담당 규격과 앱 신뢰 경계를 대조하고 검증 실패·만료·위조·타 principal 계약 fixture 정의 |
| 호스트 검증 경계·하위 깊이/도구 제한·운영 예산 | PR-4·PR-5 | helper 호출/도구 등록 흐름 대조, read_result/yield_evidence만 허용하는 경계와 공유 예약 설계. 운영값은 승인된 설정 확인 단계에서 기본값과 대조(이번에는 설정·비밀 열람 없음) |
| Yield 병렬 허용목록·직렬화·예산 상호작용·설정 방식 | PR-4 | 원본 planner/직렬화 경계와 도구 효과·공유 상태를 대조하고 배치당 상한·예약 경합 검증 계획 수립 |
| 실제 배포 스킬 목록·감사 스키마/저장 위치 | PR-4·PR-6 | 배포 목록·로더·읽기/쓰기 경로 확인, 사용 기록 항목 정의. 스킬 감사 위치는 항상 허용의 컬렉션 분리 합의와 별도로 확정 |
| 승인 규칙/감사 스키마·인덱스·사내 보존기간 | PR-3·PR-6 | 사용자+도구+조건 key·즉시 취소 조회 일관성 설계, 사내 보존 정책 확인 |
| 법무·사용 버전·컴포넌트/개별 폰트·peer/전이 라이선스 | PR-8·PR-9 | 의존/import 목록·고지와 법무 검토 결과 대조, 번들 fonts.css/woff2 제외 확인 계획 |
| -32004 전체/동적 충돌·WS 403 호환·미분류 표면 | PR-1·PR-2 | 계약 manifest 및 오류 코드 전체 검색, ASGI/client 거부 fixture 정의; 동적 mount 범위 별도 확인 |
| SessionDB 호출 closure·UI import closure·초안 보존 상세 | PR-1·PR-3~PR-5·PR-8~PR-9 | 활성 경로의 import/저장 호출 그래프, candidate revision 보존·공개 projection 설계로 구체화 |
| 실제 데이터 연결·e2e/회귀 결과·전환 일정 | PR-10 | 구현 후 허용된 실행 단계에서 실제 시나리오 기록·두 rollback 결과를 확인하고 일정 별도 합의 |
| 개인 MCP 암호화/키 회전·감사 보존/인덱스 | 마일스톤 3; M1 PR-1·PR-4는 거부 경계만 | 6.1 후속으로 유지, M3 설계에서 정책·구현 대조. M1에서 개인 MCP를 열어 확인하지 않음 |

**PR-1은 대환 님 승인 하에 구현·검증 완료(사용자 제공 상태). PR-2 이후 구현은 별도 지시가 필요하다. 이번 작업은 문서 동기화만 수행했으며 실행 검증을 재수행하지 않았다.**


### 7.4 2026-10-24 분류 리뷰 결과(대환 님 승인 5건)

**PR-1 manifest 기준 확정치(2026-10-24).** 승인 및 PR-1 구현·검증 완료 상태는 사용자 제공 기록을 반영한다. 이번 작업은 기준 파일을 읽고 Python으로 집계한 문서 동기화다. `implemented`는 M1 구현 대상이며 runtime 연결 완료가 아니다 (`Y/harness/gateway/policy.py:3`; `Y/harness/gateway/manifest.json:5`~:6).

| 승인 건 | 확정 분류·이유 | 근거 |
|---|---|---|
| 외부 worker 제어 | authority `worker.adopt`·`worker.persist`·`worker.register`: stub → unsupported. 사용자에게 불필요한 외부 worker 제어 표면이며 내부 subprocess IPC와 구별 | `Y/harness/gateway/rpc_rules.py:123`; `Y/harness/gateway/manifest.json:7086`, :7110, :7134 |
| 세션 운영자 HTTP | `POST /api/sessions/import`, `POST /api/sessions/owner-backfill`, `POST /api/sessions/prune`, `POST /api/sessions/bulk-delete`: stub → unsupported. 다른 사용자 세션에 영향 가능. 단건 `DELETE /api/sessions/{session_id}`와 `session.delete`는 stub 유지 | `Y/harness/gateway/policy.py:126`~:131; `Y/harness/gateway/rpc_rules.py:71` |
| 메모리 초기화 | `POST /api/memory/reset`: stub → unsupported. 사용자별 메모리 격리 설계 전 전역 초기화 위험 | `Y/harness/gateway/policy.py:132`~:134; `Y/harness/gateway/manifest.json:11125` |
| 저장·내보내기 구별 | `session.save`는 unsupported(로컬 파일 저장 거부), `session.export`·`GET /api/sessions/{session_id}/export`는 stub(서버 다운로드 유예) 유지 | `Y/harness/gateway/rpc_rules.py:78`, :117; `Y/harness/gateway/policy.py:163`~:165 |
| host identity | `GET /api/host/identity`는 implemented 유지. 응답 필드는 `ok/protocolVersion/pid/role/servesSpa`, `_require_token` 요구. pid 노출 허용 여부·Yield 토큰 연동은 **미확인** | `Y/harness/gateway/manifest.json:9605`; `H/hermes_cli/web_routers/status.py:147`~:149(사용자 제공 근거, 이번 작업에서 원본 재열람 안 함) |

**추가 조치:** HTTP `/api/mcp/*` 12개 모두 `denied_actions`에 `stdio`·`SSE`를 명시한 것을 확인했다. 이름별 분류 개수는 변하지 않는다 (`Y/harness/gateway/policy.py:166`~:167; `Y/harness/gateway/manifest.json:8797`의 routes 목록).

기존 문서에는 이전 RPC·HTTP 분류 숫자표가 없었고, 7.1은 계획 로그를 인용한 제안이었다. 아래 변경 전은 현재 manifest에서 위 worker·HTTP 분류 변경만 되돌려 Python으로 **역산한 비교값**이며, 과거 manifest나 계획 로그를 재열람해 검증한 값이 아니다. 이전 숫자는 이 비교표에만 남기고 현재 확정치로 사용하지 않는다. HTTP는 routes에서 WEBSOCKET을 제외하며, 외부 인증은 external_routes로 별도 집계한다.

| 계약 | 구현 전→후 | stub 전→후 | unsupported 전→후 | 합계 | 현재 집계 근거 |
|---|---|---|---|---|---|
| RPC | 38 → 38 | 61 → 58 | 184 → 187 | 283 | `Y/harness/gateway/manifest.json:65` |
| 이벤트 | 35 → 35 | 9 → 9 | 42 → 42 | 86 | `Y/harness/gateway/manifest.json:7158` |
| 서버 요청 | 2 → 2 | 2 → 2 | 12 → 12 | 16 | `Y/harness/gateway/manifest.json:8587` |
| HTTP | 20 → 20 | 76 → 71 | 186 → 191 | 282 | `Y/harness/gateway/manifest.json:8797` |
| WS | 1 → 1 | 0 → 0 | 7 → 7 | 8 | `Y/harness/gateway/manifest.json:8797` |
| 외부 인증 | 3 → 3 | 0 → 0 | 10 → 10 | 13 | `Y/harness/gateway/manifest.json:12065` |

**후속 검토 — 이번에는 분류를 바꾸지 않음.** 아래 항목의 처리 방향·필터 상세는 **제안/미확인**이며 별도 지시 후 검토한다.

| 경계 사례 | 현재 분류·남은 확인 | 근거 |
|---|---|---|
| 상태·설정 응답 | GET `/api/status`·`/api/config`·`/api/config/schema`·`/api/egress/status`·`/api/tools/toolsets/{name}/config`는 implemented 유지. 사용자별 허용 응답 필드 필터의 구체 범위·적용은 미확인 | `Y/harness/gateway/policy.py:112`~:117, :168~:169 |
| host identity 인증·pid | implemented 유지. pid 공개 허용 여부와 `_require_token`의 Yield 토큰 연동은 미확인 | `Y/harness/gateway/manifest.json:9605`; `H/hermes_cli/web_routers/status.py:147`~:149(제공 근거) |
| 별도 LLM 호출 | `llm.oneshot` stub 유지. 본 채팅 외 호출의 예산·검증·공개 경계는 후속 검토 | `Y/harness/gateway/rpc_rules.py:39` |
| 명령 실행 | `slash.exec`·`command.dispatch` stub 유지. 제한 도구 우회 방지와 허용 명령 범위는 후속 검토 | `Y/harness/gateway/rpc_rules.py:19`, :90 |
| 사용량·인사이트 | `usage.bars`·`insights.get` unsupported 유지. 제품 필요성과 사용자별 정보 경계는 후속 검토 | `Y/harness/gateway/rpc_rules.py:37`, :97 |
| steer 계열 불일치 | `session.steer`·`session.redirect`는 unsupported, `prompt.steer_queued`·`prompt.replace_queued`는 stub 유지. 같은 실행 변경과 대기열 편집의 정책 일관성은 미확인 | `Y/harness/gateway/rpc_rules.py:76`, :81, :114, :120~:122; `Y/harness/gateway/policy.py:52`~:53 |
