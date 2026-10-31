# PR-7a 실행·receipt·세션 replay 라이브러리

라우트에 연결하지 않은 라이브러리다. 인증·모델·도구를 생성하지 않는다.
검증된 principal_id와 서버 소유 profile_id를 호출자가 주입한다.
원본 H는 `/tmp/hermes-agent-ro`, 기준 커밋은 `5894e750`이다.
PB는 읽기 전용 `harness_console/PORTING_BASELINE.md`다.

## 구현 및 검증 순서

- [x] 원본 wire와 실패 사례를 단위 테스트로 고정했다.
- [x] events.py: 단일 Mongo 문서의 조건부 갱신으로 seq/ring/cursor를 함께 저장했다.
- [x] execution.py: controller.start, request_id 복구, receipt/error 및 pending을 연결했다.
- [x] 실제 Mongo 통합 시나리오를 작성했다. 실행하지 않았다.
- [x] 독립 코드 검토에서 찾은 경쟁 조건을 재현하고 수정했다.
- [x] 허용된 회귀 테스트를 실행했다. 최종 수치는 아래 검증 기록 참조.

## 파일과 기존 변경

새 파일: gateway/events.py, gateway/execution.py,
tests/test_gateway_pr7a_unit.py, tests/test_gateway_pr7a_mongo.py, 이 문서.
기존 파일 수정은 store.py setup의 7줄뿐이다(주석 1줄, 인덱스 6개).
새 컬렉션은 gateway_event_logs, gateway_receipts, gateway_receipt_counters다.
기존 store.py의 pending 취소 이벤트 관련 4줄 변경은 작업 시작 전에 있었으며 건드리지 않았다.
config.py, router.py, control.py, executor.py, native_*.py, gateway의 기존 파일,
manifest, agent_server.py, engine, scripts, harness_console 코드는 수정하지 않았다.

## 원본과 기준 문서 대응

| 요구 | 직접 확인한 근거 | 구현 |
|---|---|---|
| Desktop submit와 ACK 유실 | H/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:1149-1177; PB:70,702 | 같은 submission_id/input_id를 request_id로 전달, 새 ID 생성 없음 |
| authority submit params/result | H/tui_gateway/contracts/canonical_sessions.py:205-245,319-330; H/gateway/session_controls.py:750-771 | text + submission_id/input_id, admission receipt |
| legacy submit 차이 | H/tui_gateway/methods_prompt.py:816,982 | 원본은 streaming, 영속 ID 없음. 모호한 자동 변환 거부 |
| receipt 상태·필드 | H/tui_gateway/contracts/canonical.py:29-74 | queued/started/unknown/terminal, outcome, ref, sequence, authority_epoch, execution_generation |
| 충돌·busy 오류 | H/hermes_state_runtime.py:149-153; H/gateway/session_controls.py:321-326; PB:635 | 4001 admission_conflict/session_busy, data.reason |
| queued 취소 | H/gateway/session_controls.py:803-817; canonical.py:64 | 시작한 실행은 prompt.cancel로 취소하지 않음 |
| unknown 해소 | H/hermes_state_runtime.py:651-681 | 동일 generation의 interrupted 종결, 입력 재등록 없음 |
| 실행 중단·handle | H/tui_gateway/contracts/canonical_sessions.py:78-84,324; H/gateway/session_authority.py:298-308 | 원본 필드 모양, 본인 run만 controller.cancel |
| 상태 문자열 | H/tui_gateway/contracts/sessions.py:478-487 | output 문자열. 내용은 호스트 snapshot으로 별도 구성 |
| ring 크기/바이트/세션 상한 | H/tui_gateway/event_replay.py:29-35,93-142 | 512개, 세션당 4 MiB, 전체 64 MiB, 64개 세션, FIFO |
| freeze·seq·truncated | H/tui_gateway/event_replay.py:57-173 | UTF-8 frozen params, 절단 watermark, seq는 run sequence와 별도 |
| authority replay 결과 | H/gateway/session_events.py:77-89 | epoch 불일치/미래 seq/잘린 구간이면 events=[]와 snapshot_required |
| legacy replay 결과 | H/tui_gateway/methods_session.py:2309-2324; contracts/sessions.py:724-739 | epoch 입력 무시, 남은 tail + open_requests, 잘림 표시 |
| event 외곽 | H/tui_gateway/server.py:715-718; PB:503 | jsonrpc=2.0, method=event, params |
| replay_gap | H/gateway/session_events.py:60-63; PB:282 | 순번을 붙이지 않는 overflow 알림 빌더 |
| principal 격리 | H/gateway/session_events.py:15-18; PB:506,698 | 모든 외부 읽기/쓰기 principal+session 제한 |
| 최종 본문 공개 | PB:639; 기존 gateway/publication.py:14; native_hermes.py:485-500,522 | settle 재사용, 마지막 verdict/digest/answer_text/terminal 확인 |
| pending 응답 | H/tui_gateway/contracts/canonical.py:355-375; canonical_sessions.py:286-289 | PendingControls.respond, 중복 already_resolved |
| legacy 질문/승인 | H/tui_gateway/methods_prompt.py:1267-1339; contracts/prompt_voice.py:257-335 | respond_legacy, approval.pending/respond, request.answer |
| 편집 가능한 질문 잠금 | H/gateway/session_clarify_lock.py:18-46 | 모든 질문이 잠기기 전까지 수정 가능, 마지막 답변은 PR-6a _finish |
| pending 세대 | 기존 PR6B_NATIVE_PROMPTS.md 세대 절; native_hermes.py:78,101 | 현재 run 확인 후 run epoch, pending_snapshot 재사용 |
| Mongo 단일 저장·수집·복구 | 원본 대응 없음 → PB:575,664,702,715 | 원자적 문서 갱신, pull, 영속 receipt 메타데이터, 재시작 복구 |

## 흐름

1. `submit` → 세션 소유 확인 → `RunController.start` → 기존 request_id/active 유일 인덱스 → receipt 메타데이터 저장 → 현재 run 상태를 합친 receipt 반환.
2. ACK(접수 응답) 유실 → 같은 ID로 submit → 같은 run 확인 → 기존 receipt 반환. 실행이 만들어졌지만 receipt가 없으면 run으로 복구한다. created에서 중단됐다면 같은 ID로 controller.start를 다시 거친다.
3. 실행 이벤트 저장 → `SessionEvents.ingest` → 공개 projection → 세션 seq 할당·ring·수집 위치를 한 문서에 저장 → `since` 재생.
4. seq gap → 마지막 받은 순번으로 since 재요청 → 동일한 남은 이벤트. run의 sequence는 payload에 별도 남는다.
5. ring 초과/과대 이벤트 → 절단 watermark 증가 → authority에서는 truncated/snapshot_required, legacy에서는 truncated와 남은 tail → 호스트 snapshot + 7b의 이력 재조회.
6. 객체/서버 재생성 → Mongo seq/epoch 유지 → 기존 receipt/pending/replay 복원. 로그 재구성은 `rebuild`로 epoch 변경·ring 비우기 → 옛 epoch 요청은 snapshot 복구.
7. 실행권 만료를 확인한 `recover_unknown` → 해당 run을 unknown으로 막음 → `resolve_unknown` → 동일 run을 interrupted로 종결. 동시 재시도와 중간 중단을 위해 종결 의도와 cancelling을 함께 저장한다.

## 설계 결정

**Pull 수집.** 장기 background 없이 요청 시 저장된 run 이벤트를 순서대로 읽는다.
RunController가 생성하는 순서(created_at, _id)로 run을 읽고 active run에서는 멈춘다.
각 run의 수집 sequence와 이미 본 event_id를 ring과 같은 문서에 저장한다.
다른 서버가 먼저 갱신하면 version 조건이 실패하므로 새 문서로 다시 계산한다.
따라서 seq만 할당되고 payload가 빠지는 중간 상태는 없다.
run_id/event_id가 같으면 run sequence가 다르더라도 다시 공개하지 않는다.

**Epoch.** 원본 프로세스 메모리는 재시작 때 사라지지만 여기서는 Mongo가 기준이다.
단순 객체 생성으로 epoch를 변경하면 다른 서버의 유효한 replay까지 무효화하므로 유지한다.
명시적 rebuild만 epoch를 바꾸고 seq/dedupe는 유지한다. 역행시키지 않는다.
forget_session 이후 늦은 이벤트는 seq와 절단 표시만 올리고 ring을 되살리지 않는다.

**Receipt 원자성.** 실행과 receipt는 별도 문서다. 트랜잭션을 가정하지 않는다.
request_id 유일 인덱스가 실행 권위이고 admission_id는 run_id와 같다.
receipt에는 식별자·접수 순번·query 해시만 저장한다. 현재 상태는 영속 run에서 읽는다.
상태 사본을 만들지 않아 started 사본이 영원히 남는 문제를 피한다.
receipt 순번 할당 중 충돌/중단은 빈 번호를 남길 수 있으나 중복 번호는 없다.
늦게 복구된 receipt의 순번은 receipt 생성 순서이며 원본 admission 순서와 다를 수 있다.

**공개 경계.** 현재 publication.py에는 요청에서 언급한 이벤트 projection이 없다.
기존 settle을 재사용하는 제한적 projection을 새 파일에 넣었다.
message.delta/interim/직접 complete/commentary는 저장하지 않고,
run_finished + completed + 마지막 pass + 후보 해시 일치만 message.complete로 바꾼다.
원본 reasoning/thinking/agent.print 본문은 제거한다. 도구 입력·결과 원문·summary도 복제하지 않는다.
질문/승인 request는 PR-6a의 마스킹된 필드만, settled는 식별자만 재생한다.
답변 본문은 pending 저장소에만 있고 run 이벤트·receipt·감사 본문에 추가하지 않는다.

## 의도적인 차이와 결정 필요

- Mongo 영속화, pull, run_id 추가, admission_id=run_id, receipt 순번 빈 번호는 원본에 없는 호스트 결정(PB:503,575,664,702).
- 512개/4 MiB 세션 제한은 한 문서 갱신으로 원자적이다. 64개 세션/64 MiB 전체 제한은 여러 Mongo 문서의 조건부 정리다. 동시 쓰기 사이에는 잠깐 초과할 수 있다. 읽기/수집 때 다시 정리한다. 전체 제한까지 엄격하게 원자적으로 만들려면 저장 구조 또는 트랜잭션 정책 결정이 필요하다.
- run 수집 위치와 중복 방지 ID는 ring이 잘려도 보존한다. 장기 보존 기간·정리 정책은 정하지 않았다(PB:757). 매우 긴 세션의 Mongo 문서 크기 한계를 별도로 결정해야 한다.
- 원본 authority는 세션 단위 generation이지만 PR-6b는 run epoch다. 다음 run에서 epoch가 반복될 수 있다. 둘 이상 run이 있는 세션의 authority interrupt는 host가 검증된 snapshot의 expected_run_id를 넘기지 않으면 stale_generation으로 거부한다. 일반 RPC params로 이 값을 받아 우회하지 않는다. 원본 그대로의 세션 단위 generation 도입은 결정 필요다.
- prompt.cancel은 원본대로 queued만 취소한다. 이미 started인 run 취소는 session.interrupt다. 취소 전에 해당 run/epoch를 cancelling으로 원자적으로 바꿔 실행권 재획득과 경쟁하지 않게 한다.
- legacy prompt.submit은 본래 submission_id가 없고 streaming을 반환한다. 정확한 자동 재시도와 영속 receipt를 보장하는 대응이 모호하여 이 경로는 unsupported다. authority submit을 legacy 성공처럼 위장하지 않는다.
- session.status는 원본 output 필드에 호스트 상태 문자열을 반환한다. 원본 Hermes TUI Status 렌더러의 모델/provider/프로젝트·파일 경로는 노출하지 않는다.
- handle의 instance_id는 저장된 replay epoch, authority_epoch는 실행 epoch다. 원본 owner 프로세스/authority epoch와 같은 수명으로 해석하면 안 된다. 7b에서 원본 authority 수명까지 연결하려면 별도 결정이 필요하다.
- 원본 선택 기능(surface/voice/attachments/model_override 등)은 적용하지 않고 활성 값이 있으면 unsupported로 거부한다. queue/steer/redirect/form/장기 background/권한 확대는 없다.
- PR-6a pending 결정과 run 이벤트 사이의 강제 종료 원자성은 기존 한계다. durable outbox 없이 모든 settled/cancelled 이벤트의 무손실 복원을 주장하지 않는다. snapshot이 미해소 pending의 기준이다.
- 시간에 따른 삭제, 회사 인증/profile 매핑, 전송 fanout 구독 관리, 자동 REST 재조회, 전체 session.resume 응답과 RPC 등록은 미구현이다. 7a의 snapshot은 resume 구성용 조각이다.
- 테스트는 가짜 실행·단위 검증이다. 실제 Mongo·모델·도구로 운영 동작을 검증한 것은 아니다.

## 7b 연결 계약

먼저 `await store.setup()` 및 `await PendingControls(store, principal_id).setup()`을 수행한다.
검증된 Principal.user_id를 principal_id로 전달하고 profile_id는 서버 매핑으로 주입한다.
`adapter = Execution(controller, principal_id, profile_id=server_profile_id)`.

공통: `await adapter.rpc(method, session_id, params, rid=None, mode='authority', expected_run_id=None)`.
반환은 JSON-RPC 2.0의 result 또는 error 프레임이다. 알려진 도메인 오류는 RpcError,
예상하지 않은 Mongo/프로그램 오류는 숨기지 않고 호출자에게 전달한다.

| RPC/호스트 용도 | 비동기 handler 시그니처 |
|---|---|
| prompt.submit | submit(session_id, params) |
| prompt.receipt | receipt(session_id, params) |
| prompt.cancel | cancel(session_id, params) |
| prompt.resolve_unknown | resolve_unknown(session_id, params) |
| session.interrupt | interrupt(session_id, params, mode='authority', expected_run_id=None) |
| session.status | status(session_id, params) |
| clarify.respond / authority approval.respond | respond(session_id, params, kind='clarify' 또는 'approval') |
| legacy approval.respond | approval_respond_legacy(session_id, params) |
| clarify.lock | clarify_lock(session_id, params) |
| approval.pending | approval_pending(session_id, params) |
| request.answer / legacy JSON-RPC 응답 | request_answer(session_id, {id, result}) |
| session.events.since | rpc에서 mode 분기; events.since(session_id, epoch, after_seq=0) / since_legacy(session_id, last_seen=0) |
| resume 구성 | snapshot(session_id), handle(session_id) |
| 만료 실행 복구 | recover_unknown(session_id), 기존 controller.recover보다 먼저 호출 |
| 요청/폴링 수집 | events.ingest(session_id) |
| 서버 소유 로그 재구성/종료 | events.rebuild(session_id), events.forget_session(session_id) |
| fanout 초과 알림 | 동기 replay_gap(session_id, epoch, latest_seq) |
| 이벤트 외곽 프레임 | 동기 event_frame(params) |

모든 경로의 인증은 7b 호출자 책임이다. 클라이언트 params의 principal/profile/run ID로
서버의 소유권 문맥을 바꾸지 않는다. 이 파일들은 라우트/manifest를 변경하지 않는다.

## 검증 기록

초기 실패 테스트와 경쟁 조건 재현 실패를 확인한 뒤 수정했다.
허용된 전체 파일 14개(기존 13개 + 새 단위 파일)를 명시하고 `-k 'not local_server'`로 실행했다.
최종 결과는 작업 마지막 보고와 아래 기록을 기준으로 한다.

실제 localhost Mongo용 test_gateway_pr7a_mongo.py는 **내가 실행하지 않음**.
UUID 임시 DB와 finally drop을 사용한다. 중복/경합 제출, ACK 유실, receipt 쓰기 전 중단,
다중 principal, 재시작, 수집 순서/중복/gap/ring/epoch, pending 왕복과 본문 비유출을 담았다.
test_pending_controls_mongo.py, test_native_pr6_mongo.py, test_gateway_session_db.py,
test_gateway_session_contract.py, 나머지 engine/socket/Mongo 테스트는 실행하지 않았다.
설치·Docker·모델 네트워크 호출·git commit/push/stash/checkout은 하지 않았다.

최종 실행 결과: **846 passed, 1 deselected, 38.21초**. 새 단위 테스트는 17개다.
Mongo 테스트는 ast.parse로 구문만 확인했으며 import/실행하지 않았다.
