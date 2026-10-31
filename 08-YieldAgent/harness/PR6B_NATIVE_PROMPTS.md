# PR-6b: native 질문·승인 연결

기본값은 `HARNESS_NATIVE_PROMPTS=false`. true일 때만 6a 저장소를 생성하고
질문 도구·콜백·입력 이벤트를 연결한다. HTTP/RPC 응답 라우트는 추가하지 않았다.
실제 운영 도구를 승인 대상으로 지정하지 않았다. engine/6a 라이브러리는 변경하지 않았다.

## 구현과 검증 순서

1. 통신 검증·설정·워커 콜백의 실패 테스트를 작성한 뒤 연결했다.
2. 호스트 실행권·대기·취소·이벤트·승인 경계를 연결하고 순수 테스트를 실행했다.
3. 실제 Mongo + 가짜 워커 통합 테스트를 작성했다. 이 파일은 실행하지 않았다.
4. 지정된 기존 테스트와 새 순수 테스트만 실행했다. 소켓 테스트는 제외했다.

## 원본 대응

H는 `/tmp/hermes-agent-ro`, commit `5894e750`.
PB는 `harness_console/PORTING_BASELINE.md`이며 읽기만 했다.

| 구현 | 근거 |
|---|---|
| server_request 별도 request/params 및 기존 reply/Waiters | 원본 대응 없음 → PB:94,663 및 PR-6b 설계 1 |
| native_prompts 기본 false / 환경 설정 | 원본 대응 없음 → PR-6b 설계 2; 기존 publish_commentary 패턴 |
| clarify 도구·callback(questions) | H/run_agent.py:253; H/tools/clarify_tool.py:95-115,195-196 |
| 질문 wire 키 4개와 answers/outcome 반환 | H/tui_gateway/server.py:1104-1115; contracts/server_requests.py:36-60 |
| 300초와 실행 잔여 시간 상한 | H/tui_gateway/server.py:1094-1101; 6a effective_timeout; PB:663 |
| authority request/settled 및 응답·snapshot | H/gateway/session_pending_controls.py:16-101; canonical_sessions.py:98-110 |
| legacy 요청 복원·답변 | 6a legacy_request/respond_legacy 재사용; H/tui_gateway/server_requests.py:75-102 |
| 취소 이벤트 | H/tui_gateway/contracts/events.py:546-563 |
| clarify.respond 및 approval.respond | H/tui_gateway/contracts/canonical.py:355-375; canonical_sessions.py:286-289 |
| 승인 선택·항상 허용 조건 | H/tools/approval.py:850-890,1121-1147; 6a rule_key/register 그대로 |
| sudo/secret 빈 value, 기타 -32601 | H/tui_gateway/contracts/server_requests.py:199-223; PB:375,640; 6a decline 그대로 |
| 호스트 승인 게이트·사용자별 저장/감사 | 원본 대응 없음 → PB:576 D05,731 Q5 및 서버 소유 권한 원칙 |
| yield.input_required·pending_input | 원본 대응 없음 → PB:517,663; 질문 본문은 pending 이벤트에만 |
| 취소 중 해소 이벤트 저장 | 원본 대응 없음 → PB:663; control.py:205-215가 먼저 cancelling으로 변경 |
| 응답 본문 이력 제외 | 원본 대응 없음 → PB:391,607 및 PR-6b 공개 정책 |

## 실행 순서

| 상황 | 요청 → 발행 → 응답 → 재개 |
|---|---|
| 질문 | 워커 server_request(request=clarify) → register_clarify → clarify.request → yield.input_required → 6a respond/respond_legacy → clarify.settled → 기존 reply의 answers/outcome → 워커 재개 |
| 다중 질문 | 6a가 만든 각 authority ID마다 request/input_required/settled; 한 batch wait 결과는 원래 qid별 answers |
| 승인 | 허용 artifact 가짜 도구 → register → approval.request → yield.input_required → once/session/always 또는 deny → approval.settled → 허용 때만 executor 실행 |
| 자동 승인 | register의 규칙 조회·감사 → request/input_required 없이 settled → 실행 |
| 취소 | run finally 또는 대기 실행권 상실 → cancel → clarify.settled 또는 approval.settled + approval.cancelled → 취소/거부; 실제 도구 실행 없음 |
| 시간 초과 | effective_timeout(300, remaining) → 6a wait → settled → clarify timed_out / approval deny |
| 재접속 | pending_snapshot → 같은 principal/generation의 살아 있는 prompt → 새 인스턴스 respond → 호스트가 저장 상태를 확인해 settled → 기존 워커 reply |
| 서버 복구 | cancel_run_prompts → 해당 run의 대기 취소와 세대 검사된 이벤트 발행; 기존 recovery 호출 연결은 하지 않음 |
| 제한 요청 | 6a decline_server_request + 감사 → value 빈 문자열 또는 -32601 즉시 reply |

## 세대와 실행 상태

`prompt_generation(run)` 한 곳에서 `run['epoch']`를 사용한다.
PB:517/663에는 epoch와의 명시적 대응 정의가 없고, 실제 실행권과 모든 native
이벤트가 epoch로 검사된다(store.py:232-244,298 이후). goal_revision은 질문 이벤트에
별도로 보존한다. epoch는 run 범위이므로 PR-7은 세션의 현재 실행을 확인한 뒤
snapshot을 호출해야 한다. 세션 전체의 단조 증가 generation으로 간주하면 안 된다.

대기 중 `status=running` 유지. `pending_input`은 authority ID를 키로 하고
kind/expires_at만 가진 맵이다. 다중 질문·동시 요청을 덮어쓰지 않는다.
해소 시 비우며, 쓰기/삭제 모두 epoch를 검사한다. 기존 waiting_user·question은 그대로다.

store.event는 epoch가 있는 clarify.settled/approval.settled/approval.cancelled에만
cancelling 상태를 추가 허용한다. 새 요청·다른 실행 이벤트는 여전히 running에서만 쓴다.
종료된 run 또는 다른 epoch에는 새 이벤트를 쓸 수 없다.

## 의도적인 차이와 안전 경계

- AIAgent에는 approval_callback 인수가 없다(H/run_agent.py:253). 임의 승인 콜백을 만들지 않았다.
  워커가 보내는 독립 approval 요청은 deny. 승인 권한은 호스트의 실제 도구 호출과 연결한다.
- NativeBridge.approval_tools 기본 빈 집합. 운영 생성 경로에서 값을 지정하지 않는다.
  기존 read/artifact 허용 집합을 먼저 검사하므로 persistent_write는 승인으로 열리지 않는다.
- 6a는 read 승인을 거부하고 PB:576도 효과 있는 호출만 승인한다. 읽기 전용 호출은
  approval_tools에 들어 있어도 승인 없이 기존대로 실행한다. artifact만 게이트 대상이다.
- 승인 거부는 호스트 도구 응답에 status=denied와 BLOCKED 오류를 반환한다.
  원본 엔진 내부 승인 결과 객체를 호스트 ToolObservation으로 위조하지 않는다.
- 원본 undelivered 대신 끊김/interrupt/취소 시 워커는 answers={}, outcome=cancelled를
  반환한다(이번 명시 설계). timeout의 잠긴 답변은 6a 결과대로 보존한다.
- 질문 도구의 호출/결과는 저장할 native_messages에서 제거한다. 살아 있는 워커는 답변을
  사용하지만 /runs·이벤트·감사에 별도 답변 본문을 추가하지 않는다. 검증된 최종 답변이
  사용자 입력을 의미상 인용하는 것까지 문자열 규칙으로 제거하지는 않는다.
- 별도 인스턴스 응답은 호스트가 저장 상태를 확인하고 6a settled 빌더로 발행한다.
  안정적인 event_key로 중복을 막는다. snapshot/cancel 헬퍼는 자기 인스턴스 이벤트를
  직접 발행한다. 6a 객체를 직접 쓰는 향후 호출자도 drain_events 발행 책임을 지켜야 한다.

## 기존 화면과 공개 경로

router.py:45-46의 /runs 허용 필드에는 pending_input/native_messages가 없어 공개 모양은 같다.
router.py:134-153의 원본 이벤트 스트림은 새 이벤트를 그대로 전달한다.
compatibility_chat(router.py:187-245)은 알려진 이벤트만 변환하므로 새 이벤트를 무시한다.
기존 input_required와 새 yield.input_required는 다른 이름이다. 기존 입력 제출로
새 pending을 해소할 수 없다. 새 화면/응답 라우트 없이 flag만 켜면 질문은 timeout될 수 있다.

## 테스트와 미구현

최종 허용 테스트: 152 passed, 1 deselected (소켓 local_server 제외).
새 순수 테스트 13개: IPC 타입/ID, flag, callback 가짜 스트림, 끊김/취소/예산,
검증 차단, 권한 집합, 비대상 read 실행, timeout, 이력 제외, cancelling 이벤트,
flag false 호스트 startup/이벤트/저장소 미생성.

`test_native_pr6_mongo.py`는 **내가 실행하지 않음**. 구문만 파싱했다.
실제 Mongo + UUID 임시 DB + finally drop으로 질문 왕복/시간 초과/취소/워커 종료,
재접속/사용자 격리/승인/규칙 취소/정보 노출/이벤트 순서/세대/고아 pending/검증을 검사한다.
`test_pending_controls_mongo.py`, 다른 Mongo/engine/socket 테스트, 모델 호출도 실행하지 않았다.

미구현/결정 필요:

- form, yield.input.respond, HTTP/RPC 응답 라우트, clarify.lock 라우트: PR-7 이후.
- recovery 호출 연결은 하지 않았다. 서버 재기동 때 호출자가 cancel_run_prompts를 호출해야 한다.
- 강제 종료 원자성은 6a 수준. durable pending 결정과 run 이벤트 발행 사이의 유실 가능성이 있다.
  특히 취소 이유는 6a pending에 저장되지 않아 강제 종료 뒤 approval.cancelled를 정확히 재구성할 수 없다.
  개선은 취소 이유와 발행할 이벤트를 같은 문서에 저장하는 outbox + 멱등 전달이지만 이번에는 변경하지 않았다.
- read 도구까지 반드시 승인하려면 6a/PB의 효과 있는 호출 정책 변경 결정이 필요하다.
- 세션 단위 generation 도입, 응답 보존기간, 항상 허용 규칙/감사 원자성은 별도 결정이다.
- 실제 Mongo와 모델을 사용한 종단 간 검증은 아직 안 했다. 운영 준비 완료를 의미하지 않는다.

원본 비교: clarify_gateway.py와 tools/approval.py SHA-256 일치.
run_agent.py는 기존 standalone self-update bootstrap 제거만 차이(diff 확인); 이번 수정 없음.
