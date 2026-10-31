# Harness Console

사내 이식은 [현재 상태 이식 안내](../harness/INTERNAL_MIGRATION_2026_10_31.md)를 먼저 읽는다. 기본 화면과 선택형 Hermes 화면, 게이트웨이 경로와 인증 연결 조건을 구분한다.

기존 Yield Agent harness API를 사용하는 독립적인 대화형 웹 프런트엔드입니다. 기존 `yield_frontend`와 백엔드 파일을 변경하지 않습니다. Vite, React 19, TypeScript, Tailwind CSS v4, react-markdown, remark-gfm을 사용합니다.

## 준비 사항 및 실행

- Node.js 22.12 이상(또는 20.19 이상), npm
- `http://127.0.0.1:8001`에서 실행 중인 FastAPI 백엔드
- 백엔드에 필요한 DB, 모델 및 harness 환경 설정은 기존 프로젝트 설정을 사용합니다.

저장소 루트에서 백엔드를 시작하는 경우:

```bash
bash 08-YieldAgent/start_servers.sh
```

또는 기존 백엔드 Python 환경을 활성화한 뒤:

```bash
cd 08-YieldAgent
uvicorn agent_server:app --host 127.0.0.1 --port 8001
```

백엔드가 이미 실행 중이면 다시 시작할 필요가 없습니다. 별도 터미널에서:

```bash
cd 08-YieldAgent/harness_console
npm install
npm run dev
```

브라우저에서 **http://127.0.0.1:5175** 를 엽니다. 포트가 사용 중이면 자동으로 다른 포트를 선택하지 않고 오류를 냅니다.

`/runs`, `/harness`, `/sessions`, `/session`, `/health`는 Vite가 `127.0.0.1:8001`로 프록시합니다. SSE 프록시 timeout을 해제하고 응답을 버퍼링하거나 변환하지 않습니다. 별도 인증 키나 `.env` 파일이 필요하지 않습니다. `vite preview`는 빌드 미리보기용이며 API 프록시가 필요한 실제 사용은 `npm run dev`로 실행하세요.

## 기능

- Enter 전송, Shift + Enter 줄바꿈, 한국어 IME 입력 지원
- 저장된 commentary 이벤트를 수신 즉시 Markdown/GFM으로 표시
- invocation_id로 시작·종료를 연결하고 parent_invocation_id로 중첩하는 도구 타임라인
- 도구 상태, 소요 시간, 요약, 저장된 입력 인자 및 `run_python` 코드 구문 강조
- 세션 목록, 새 대화, 같은 세션의 후속 질문, 전체 run 페이지 로딩 및 이벤트 재생
- 중지 버튼과 입력 대기 폼: 모든 fields를 표시하고 slot별 사전을 제출; fields가 없으면 문자열 답변
- 이벤트 MIME 또는 응답 Content-Type으로 HTML을 판별해 흰 배경의 sandbox iframe으로 표시; 기존 프런트엔드의 제목·행 수 기반 높이 계산 사용
- HTML의 `새 창`(Blob URL) 및 다운로드, text/plain 코드 블록, Markdown/GFM, 이미지 미리보기; PPTX·기타 파일은 다운로드
- `실행 정보 / 캔버스` 전환: 현재 세션의 모든 실행 아티팩트 표시, 채팅의 제목 또는 캔버스 버튼으로 해당 항목 선택·스크롤 (같은 항목을 다시 눌러도 해당 위치로 이동)
- 세션을 다시 열면 저장된 artifact 이벤트를 재생해 미리보기와 캔버스 복원; Blob URL은 화면 해제 시 정리
- 각 result_id의 named table을 `/harness/results`로 조회, 100행씩 추가 로딩, 열별 정렬
- `verify_answer` / `completion_review` observation의 검증 요약과 상세 scope
- 최종 답변, 실행 상태, stop_reason, 사용량 및 실시간 경과 시간
- 키보드 포커스, 좁은 화면 대응, 어두운 테마, reduced-motion 지원

## API 및 이벤트 매핑

`POST /runs`에 `{session_id, request_id, query}`를 보내며 새 UUID를 사용합니다. GET SSE의 `data`는 compatibility `/chat/stream` 이벤트가 아닌 `{event_id, sequence, type, payload}` 원본 계약입니다.

| 이벤트 | 화면 처리 |
| --- | --- |
| `run_started` | 실행 재생 시작, run 메타데이터와 함께 표시 |
| `progress` | 최신 진행 문구 |
| `tool_started` | invocation_id별 타임라인 항목 생성, parent_invocation_id로 중첩 |
| `tool_finished` | 상태·elapsed_seconds·summary 갱신, 저장된 observation으로 결과 보완 |
| `commentary` | payload.content를 수신 즉시 표시 |
| `artifact` | artifact_id와 session_id로 파일 조회, MIME별 미리보기 및 세션 캔버스에 추가 |
| `input_required` | message/fields/interrupt_id를 입력 폼으로 표시 |
| `input_received` | 추가 답변 기록 표시 |
| `model_usage` | input_tokens/output_tokens/total_tokens 합산; 실패 호출의 누락 값은 미제공 처리 |
| `run_finished` | answer, status, reason을 최종 답변·상태·stop_reason으로 표시 |
| 기타 이벤트 | 저장된 이벤트 목록에는 유지; 별도 의미를 추론하지 않음 |

서버가 실행 중일 때 `/runs/{id}`를 3초 간격으로 조회하여 usage와 observations를 보완합니다. 시작 이벤트에 도구 인자가 없으므로 인자는 저장된 `observations[].validated_arguments`가 도착한 뒤 표시됩니다.

SSE 종료 시 run을 확인합니다. 실행 중이면 마지막 sequence 이후로 재연결하며, `waiting_user` 또는 종료 상태이면 재연결을 멈춥니다. 통신 실패는 최대 5회 재시도하고 수동 재연결 버튼을 제공합니다. event_id/sequence 중복은 제거합니다. 세션을 바꾸면 해당 화면의 스트림을 해제하지만 서버 실행은 취소하지 않습니다.

입력 제출 형식:

```json
{
  "kind": "input",
  "request_id": "새 UUID",
  "goal_revision": 1,
  "interrupt_id": "서버가 제공한 ID",
  "value": {"slot": "사용자 입력"}
}
```

제출 후 같은 run에 마지막 sequence부터 재연결합니다. 입력 응답이 유실되어도 재연결하며, 같은 입력 또는 시작 요청의 재시도에서는 request_id를 유지해 중복 실행을 방지합니다. 과거 입력 요청은 기록으로 남고 현재 waiting_user 질문만 폼으로 렌더링합니다.

## 표시 범위

- 서버가 commentary를 토큰 단위로 보내지 않으면 화면도 이벤트 단위로 갱신됩니다. 최종 answer는 run_finished 또는 run 조회 응답이 제공할 때 표시됩니다.
- native Hermes가 전체 토큰/호출 수만 제공하는 경우 입력·출력 토큰은 `—`로 표시합니다. 수치를 임의 추정하지 않습니다. 서버 전체 토큰에는 예약량이 포함될 수 있습니다.
- 활성 경과 시간은 서버의 active_seconds를 기반으로 화면에서 증가합니다. 완료 후 서버 기록으로 동기화됩니다.
- 표 정렬은 **현재 불러온 행**에 적용됩니다. 전체 데이터 정렬이 필요하면 모든 행을 불러오세요. 일부 결과의 complete/missing_reason도 표시합니다.
- legacy 세션은 `/session/{id}/history`로 대화 텍스트를 표시하며 harness 도구 기록은 없습니다.
- 서버가 공개 API에 노출하지 않는 내부 completion_review는 가져올 수 없습니다. 해당 도구 observation이 있으면 표시합니다.

## 검증 및 스크린샷

```bash
npm test
npm run build
```

아티팩트 브라우저 통합 테스트는 서버를 시작하지 않고 빌드 파일과 API fixture를 Playwright에서 제공합니다:

```bash
npm run test:artifacts
# 설치된 Chrome을 사용할 경우 (macOS)
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:artifacts
```

Chromium 또는 지정한 브라우저를 실행할 수 있는 환경이 필요합니다. 이벤트 재생, MIME별 표시, sandbox 내 스크립트, 새 창·다운로드, 재시도, 캔버스 재선택, 세션 재열기 및 좁은 화면을 검증합니다. 실제 백엔드/LLM 연결을 검증하는 테스트는 아닙니다.

실제 백엔드 요청 및 모델 실행을 포함하는 스크린샷은 **직접 실행**합니다. 개발 서버와 백엔드를 먼저 켜고 Chromium을 설치하세요.

```bash
npx playwright install chromium
npm run screenshot
```

`e2e/screenshot.mjs`는 Chromium에서 질문 `최근 일주일 동안 수율 낮은 거 알려줘`를 제출합니다. 실행 종료 또는 도구 타임라인과 최종 답변이 표시될 때까지 최대 15분 기다린 후 `screenshots/run.png`에 전체 페이지 스크린샷을 저장합니다. 입력 대기는 종료가 아니며 자동 답변하지 않습니다. 제한 시간을 넘기면 실패하고 브라우저를 닫습니다. 실패/부분 완료/취소도 실행 종료이므로 실제 상태가 스크린샷에 남습니다.

테스트 선택자: `chat-input`, `send`, `tool-timeline`, `final-answer`, `run-status`, `html-artifact` (HTML iframe). `run-status`에는 원본 상태를 담은 `data-status`도 있습니다.

실제 저장 세션 재열기 검증 (8001 백엔드와 5175 Vite가 이미 실행 중이어야 합니다):

```bash
SESSION_ID='저장된-세션-id' PW_CHANNEL=chrome npm run verify:reopen
```

`PW_CHANNEL`을 생략하면 Playwright 기본 Chromium을 사용합니다. `/sessions`에서 ID를 확인하고 사이드바의 `data-session-id` 항목을 클릭하여 가장 오래된 질문과 내용이 있는 HTML iframe을 확인합니다. `screenshots/reopen_viewport.png`에 뷰포트를, `screenshots/reopen_html.png`에 첫 HTML iframe을 잘라 저장하고 PASS/FAIL을 출력합니다. 실패 시 종료 코드는 1입니다. 서버를 시작하거나 새 질문을 제출하지 않습니다.

설정 참고: [Vite 서버 프록시](https://vite.dev/config/server-options), [Tailwind v4 Vite 설치](https://tailwindcss.com/docs/installation/using-vite).
