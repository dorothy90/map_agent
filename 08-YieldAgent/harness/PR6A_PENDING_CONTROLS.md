# PR-6a: 대기 저장소와 두 wire 계약

기존 파일 수정 없이 추가한 순수 라이브러리다. HTTP/RPC 라우트, 호스트,
워커, 프로세스 간 통신(IPC), 모델, 실제 도구 호출과 연결하지 않았다.
Mongo 통합 테스트는 작성했지만 이 작업에서는 실행하지 않았다.

## 호출 계약

- `PendingControls(harness_store, principal_id)` → `await setup()`.
- `register_clarify(session_id, generation, entry, timeout_s=300, run_id=None)`는
  `clarify_id/question/choices/multi_select` 객체 또는 같은 키의 dict를 받는다.
  다중 질문은 `{clarify_id, questions:[{qid,question,choices?,multi_select?}]}`.
- `register(session_id, generation, data, timeout_s=300, run_id=None,
  tool_allowed=False, effect=None)`는 approval `request_id/command/description/
  tool_name/allow_session?/allow_permanent?/smart_denied?`를 받는다.
  호출자가 기존 도구 권한을 확인한 뒤 `tool_allowed=True`와 효과 종류를
  제공해야 한다. 기본값은 거부한다. read 효과, 비활성 호스트 도구, 파일
  편집은 거부한다. 이것은 도구 권한을 만들거나 실제 실행하는 함수가 아니다.
- 두 register는 저장된 pending ID를 반환한다. 항상/세션 허용이면 같은 ID에
  완료 결과를 저장하되 `approval.request`는 만들지 않는다.
- `snapshot(session_id, generation)` → CanonicalPendingPrompt tuple.
  `respond(session_id, generation, prompt_id, response, kind='approval')` →
  `{status:'resolved'|'already_resolved', prompt_id}`.
- `legacy_request(...)`는 같은 pending의 JSON-RPC 서버 요청을 반환한다.
  `respond_legacy(..., result, kind)`는 원본 결과 본문을 검증하고 같은 기록을 해소한다.
- `wait(prompt_id, timeout=None)` → clarify `{answers,outcome}` 또는 approval `{choice,all?}`.
  Mongo에 저장된 만료와 호출자의 대기 상한 중 먼저 오는 시각을 적용한다.
- `cancel(prompt_id, reason=...)` 또는 `cancel(run_id=..., reason=...)`는 취소한
  프롬프트의 이벤트 dict를 반환한다. `drain_events()`는 이 인스턴스에서 만든
  이벤트 전체를 비운다. 둘을 모두 발행하면 중복되므로 6b는 한 방식만 사용한다.
- `list_rules/revoke_rule/list_audit`는 본인 데이터만 조회/수정한다.
- 순수 `decline_server_request(request_name, params)`와 같은 이름의 저장소 메서드가
  있다. 후자는 거부 감사도 남긴다. params 값은 읽거나 기록하지 않는다.

## 원본 대응

H = `/tmp/hermes-agent-ro`, 커밋 `5894e750`.
PB = 읽기 전용 `harness_console/PORTING_BASELINE.md`.

| 요구 | 원본 또는 결정 | 구현 흐름 |
|---|---|---|
| authority approval | H/gateway/session_pending_controls.py:16-38 | once,deny,session,always → approval.request |
| authority clarify | 같은 파일:40-51 | question,choices,multi_select → clarify.request |
| 응답 검증/중복 | 같은 파일:66-101 | invalid_params / resolved / already_resolved; 16,384자 상한 |
| 재접속 투영 | 같은 파일:53-64; H/tui_gateway/contracts/canonical_sessions.py:98-110 | snapshot → 살아 있는 CanonicalPendingPrompt |
| settled | H/gateway/session_pending_controls.py:62-64,84-85 | kind.settled {prompt_id,execution_generation} |
| 취소 | H/tui_gateway/contracts/events.py:546-563 | approval.cancelled + kind.settled |
| legacy approval | H/tui_gateway/server.py:781-793,835-860; contracts/server_requests.py:163-196 | JSON-RPC approval 요청 / ApprovalResult |
| legacy clarify | H/tui_gateway/server.py:1094-1115; contracts/server_requests.py:36-60 | JSON-RPC clarify 요청 / answers,outcome |
| 재접속 답변 | H/tui_gateway/server_requests.py:75-102 | questions와 이미 받은 answers 재전송 |
| batch 종료 | H/tools/clarify_batch.py:17-35; clarify_tool.py:87-130 | 잠긴 답변 보존, submitted/timed_out/cancelled |
| 단일 해소 | H/tools/clarify_gateway.py:49-112; tools/approval.py:149-176 | Mongo의 pending 조건부 갱신으로 치환 |
| rule_key | H/tools/approval.py:1121-1147 | 도구명 + ':' + 설명 SHA-256 앞 12자리; 빈 설명 원본 fallback |
| 승인 timeout | H/tools/approval.py:882-885,1121-1147; gateway/platforms/base_exec_approval.py:65-68 | 기본 300초, 무응답 deny |
| 마스킹 | H/gateway/run.py:577-587; agent/redact.py:893 | 엔진 redact_sensitive_text(force=True) 그대로 호출 |
| 16개 요청 거부 | H/tui_gateway/contracts/server_requests.py:59-357; display.py:167; liveness.py:30 | ValueResult 또는 -32601, 4404 미사용 |
| Mongo 컬렉션/인덱스/감사 | 원본 대응 없음 → PB:391,607,663,731 | pending_prompts / approval_rules / approval_audit, principal 필터 |
| 두 wire 동일 pending 연결 | 원본 대응 없음 → PB:517,663 및 이번 PR-6a 결정 | 다중 질문별 authority ID를 한 Mongo 기록에 연결 |
| 대기 시간 예산 | 원본 대응 없음 → 이번 PR-6a 결정 | max(0,min(default,remaining-1)) |

## 의도적인 호스트 차이

- 원본의 threading.Event 대신 asyncio.Event와 50ms Mongo 폴링을 함께 쓴다.
  같은 인스턴스는 Event로 깨우고 다른 인스턴스/프로세스의 응답은 폴링으로
  감지한다. 폴링 지연에는 실제 Mongo 응답 시간이 추가된다.
- 원본 큐/잠금 대신 principal + pending 상태 + version 조건의 update_one을
  사용한다. 한 질문의 중복 답변은 한 번만 반영한다. 다중 질문은 각각
  `pending_id:q1`, `pending_id:q2`로 연결하며 같은 Mongo 문서에 모은다.
- generation은 호출자가 제공하는 0 이상의 정수다. PB에서 run epoch와의
  정확한 대응 정의는 찾지 못했다. 오래된 뷰어가 새 generation을 취소하지
  않도록 과거 generation만 취소한다. 실제 epoch 연결은 6b의 책임이다.
- rule_id는 조건별 안정적인 UUID다. 세션 허용은 같은 HarnessStore를 공유하는
  인스턴스들의 메모리에만 존재한다. 호스트 재시작 후 세션 허용은 사라진다.
- standalone Mongo도 지원하도록 다중 컬렉션 트랜잭션을 전제하지 않는다.
  최종 결정은 먼저 원자적으로 저장하고 규칙/감사를 멱등적으로 마무리한다.
  wait/snapshot이 미완료 마무리를 복구한다. 오래된 승인 마무리는 이후의
  규칙 취소를 되돌리지 않는다. 등록 감사·부분 답변 감사·취소 감사까지의
  프로세스 강제 종료 원자성은 보장하지 않는다. 별도 outbox 결정이 필요하다.
- gateway/run.py 전체 import는 애플리케이션 의존성을 불러오므로 피했다.
  엔진의 redact.py/file_safety.py/hermes_constants.py만 전용 모듈 공간에서
  읽어 실행한다. sys.path/sys.modules를 바꾸지 않고 마스킹 식을 복제하지 않는다.
  force=True라서 설정과 .env를 읽는 경로는 호출하지 않는다. 오류 시 원문을
  반환하는 fallback도 없다. 세 파일 및 clarify_gateway.py,
  session_pending_controls.py, tools/approval.py의 원본/engine SHA-256 일치를 확인했다.
- 원본 legacy builder는 command만 마스킹하지만 이 PR은 사용자 지시에 따라
  description도 마스킹한다. 임의 queue 추가 키는 노출하지 않는다.
- timeout=0은 예산 소진으로 즉시 종료하며 무제한은 None으로 명시한다.
  테스트와 호스트 예산을 위해 timeout_s에 소수 초도 받는다.
- 이벤트 dict는 `{type,session_id,payload}`라는 발행 입력이다. 원본 wire의
  sequence/replay/JSON-RPC notification envelope를 대신하지 않는다.

## 거부 분류표

| 처리 | 요청 이름 |
|---|---|
| 별도 처리 대상으로 None 반환 | approval, clarify |
| `{value:''}` | sudo, secret, vault.unlock_prompt, vault.save_login, vault.code, display.install.sudo, terminal.read, window.read, preview.read, preview.act, tour, theme.apply |
| `error.code=-32601` | setup_beat, theme.pick, 알려지지 않은 이름 |

기존 manifest/unsupported.py의 -32601 정책은 수정하지 않았다. PR-6b 연결 시
이 라이브러리의 원본 ValueResult 거부와 기존 분류를 조정해야 한다.

## 검증과 미구현

순수 단위 테스트와 허용된 기존 테스트를 실행했다. 허용 파일 내부의 소켓
테스트를 사전에 제외하지 못하여 test_native_budget.py의
`test_sdk_retries_are_admitted_individually_on_local_server`가 포트 바인딩 단계에서
PermissionError로 실패했다. 모델 요청 단계에는 도달하지 않았다. 이후에는
그 테스트를 `-k 'not test_sdk_retries_are_admitted_individually_on_local_server'`로
제외했다. 테스트 결과의 최신 숫자는 작업 최종 보고에 기록한다.

`test_pending_controls_mongo.py`는 실제 localhost Mongo + UUID 임시 DB + finally
삭제 패턴이다. 가짜 컬렉션은 없다. 파일 구문만 확인했고 **내가 실행하지 않음**.
따라서 저장소의 실제 Mongo 통합 동작이 검증되었다고 주장하지 않는다.

미구현/결정 필요:

- form, yield.input.respond, clarify.lock 라우트, HTTP/RPC 노출.
- native_hermes/native_worker/native_ipc/config 연결, 이벤트 발행·복원 전달,
  yield.input_required 어댑터, 실제 모델 스모크: PR-6b/PR-7.
- legacy ApprovalResult의 all 필드는 검증/보존하지만 다른 pending을 일괄
  승인하는 실행은 하지 않는다. 이 PR의 응답은 같은 pending 하나에만 적용된다.
- 감사와 pending/응답 본문 보존기간 및 삭제 정책: PB:607,731 미확인.
  임의 TTL이나 자동 삭제는 넣지 않았다. 감사에는 본문 대신 길이/해시 접두만
  있지만 pending에는 wait/reconnect용 답변과 마스킹한 질문이 저장된다.
- 강제 종료 시 등록/부분 답변/규칙 취소 감사의 완전한 원자적 보존은 별도 결정 필요.

실제 도구 검토: NativeBridge.allowed(native_hermes.py:78)는 read/artifact만
허용한다. tools/registry.py:38-44에는 persistent_write 타입이 있지만 현재
등록에서 해당 효과는 발견하지 못했다. run_python(python_tools.py:95)과
export_report(artifact_tools.py:81)는 read_only=False, effect=artifact다.
executor.py:257,286은 read_only를 호출 기록과 재시도 판단에 사용하며 승인
연결은 없다. 이 PR에서는 두 도구를 새 승인 대상으로 지정하지 않았다.
