# PR-10 제한 배포·롤백

2026-10-25 소스 기준. 이 문서는 실행 절차와 특성화 테스트를 제공한다. 실제 배포·Mongo·모델·브라우저 검증은 수행하지 않았다. PB는 [PORTING_BASELINE.md](PORTING_BASELINE.md)를 뜻한다. 현재 번들은 `harness/engine/UPSTREAM.json:3`의 `5894e750c3dba980d43b25aa29e3958149ba2c61`이다.

## UI만 롤백

1. 배포 링크·북마크에서 `?ui=hermes`를 제거하고 문서를 전체 새로고침한다. 필요하면 후속 코드 변경으로 `src/main.tsx`의 opt-in 분기를 제거한다(이번 작업에서는 변경하지 않음).
2. 기본 진입은 항상 기존 App이다. 빈 query, `?ui=old`, `?ui=hermes2`도 기존 App으로 간다. `main-optin.test.tsx`는 main을 직접 import해 렌더와 동적 entry 로드 여부를 검증한다.
3. UI 선택만 끄며 신규 runtime의 활성 실행은 계속 원 runtime에서 처리한다. UI를 바꾼다고 실행 취소나 engine 전환을 하지 않는다. CSS까지 복원하려면 query 변경 후 전체 새로고침한다(PR9 §위험·미결정·서버 요청 사항, 2296행).

## API/엔진 롤백

1. 신규 admission을 중단한다. 어떤 배포 장치에서 차단할지는 **미확정**이다. `GATEWAY_M1_ENABLED`를 해제하거나 `false`로 설정할 다음 프로세스를 준비한다.
2. 기존 활성 실행과 입력/승인 대기를 원 runtime에서 종료하거나 명시적으로 취소한다. 관리 경로를 먼저 없애 대기를 방치하지 않는다. 기존 실행을 구 runtime으로 강제 이식하지 않는다.
3. 정리가 확인되면 플래그 OFF로 프로세스를 교체한다. 플래그는 import 시 읽으므로 실행 중 환경변수만 바꿔도 이미 등록된 라우터가 제거되지는 않는다. `/gateway-m1/**`는 404가 된다.
4. 구 reader와 v2 결과 어댑터가 있는 호환 빌드에서 구 경로로 새 실행을 받는다. `GATEWAY_M1_ENABLED`는 마운트 스위치이며 엔진 버전을 자동으로 되돌리지 않는다. `harness.app.app`에는 플래그와 무관하게 gateway 라우터가 없다.

이는 PB §7.1 681행의 “신규 admission 중단 → 활성 실행을 원 runtime에서 종료/취소 → 구 경로 새 실행” 순서 및 `harness/README.md`의 v2 호환 빌드 제한과 같다. 실제 agent_server ON 블록은 `/gateway-m1` prefix로 기본 거부 resolver를 연결하며, 운영 인증·GatewayServices 주입 자체가 완료됐다는 뜻은 아니다(`agent_server.py:210-217`). 리버스 프록시 공개 경로·사내 인증·배포 명령·drain 담당자는 **미확정**이다.

## Mongo 데이터와 인덱스

아래 P는 `principal_id`, S는 `session_id`, U는 unique이다. 방향 미표기는 모두 오름차순이다. “구 reader”는 이 작업 트리의 `harness.router`/`HarnessStore`를 말하며, 이전 임의 바이너리와의 호환 보장은 아니다. MongoSessionStore는 세션/메시지 별도 컬렉션을 쓰고, 실행은 기존 `harness_runs`를 공유한다. `session_id`와 `epoch`는 이미 구 run 계약에도 있는 필드다.

| 컬렉션 | 용도 / 코드에 선언된 인덱스 | 구 코드가 읽는지 | 롤백 후 남는 데이터·정리 |
|---|---|---|---|
| `gateway_sessions` | 세션 메타·압축 부모·tombstone. U(P,id), (P,parent_session_id,started_at -1), (P,started_at -1), U(P,title), 마지막은 title 문자열에만 partial | 구 router는 읽지 않음 | 메타·삭제 tombstone 보존. 삭제 시 늦은 append 부활 방지 계약 고려; 기간/정리 절차 미확정 |
| `gateway_messages` | transcript. U(P,id), U(P,S,seq) | 구 router는 읽지 않음 | transcript·압축 이력 보존; 세션 계보와 함께 정리해야 하며 정책 미확정 |
| `gateway_message_counters` | principal별 단조 메시지 ID. U(P) | 구 router는 읽지 않음 | counter 유지; 재사용 방지 때문에 단독 초기화하지 않음, 정책 미확정 |
| `gateway_event_logs` | 세션 replay ring/epoch/watermark. U(P,S), (opened_at) | 구 SSE는 run.events를 읽음; Store.setup은 OFF에서도 인덱스 생성 | replay 저장 상태 유지. opened_at은 TTL 인덱스가 아님; 보존 정책 미확정 |
| `gateway_receipts` | durable admission/ACK 복구. U(P,S,request_id), U(P,S,admission_id), U(P,S,sequence) | 구 router는 직접 읽지 않음; Store.setup은 인덱스 생성 | 중복 실행 방지 근거를 보존. run과 불일치하는 수동 삭제 금지 권고; 보존 정책 미확정 |
| `gateway_receipt_counters` | 세션 receipt sequence. U(P,S) | 구 router는 직접 읽지 않음 | receipt와 같이 보존; 정리 정책 미확정 |
| `pending_prompts` | 질문/승인 pending·decision. U(P,prompt_id), U(P,authority_ids), (P,S,status,execution_generation), (P,run_id,status) | 구 router 직접 조회 없음; native pending 경로는 읽음 | 원 runtime에서 pending 해소 후 보존. 운영 보존 기간 미확정 |
| `approval_rules` | 본인 always 규칙/취소. U(P,rule_id), (P,tool_name,rule_key,revoked_at), U(P,tool_name,rule_key) | 구 router 직접 조회 없음; native 승인 경로 사용 | 규칙과 즉시 취소 상태 유지. 삭제/보존 정책 미확정 |
| `approval_audit` | 승인 감사. U(P,audit_id), (P,ts) | 구 router 직접 조회 없음; 승인 경로 기록 | 규칙과 별도 컬렉션 유지. 사내 보존 정책 미확정 |
| `harness_runs` (기존·공유) | 실행·epoch·events·공개 결과. U(P,S,request_id), U(P,S) partial active=true | `/runs`·SSE·세션 reader가 읽음 | 새 필드 포함 그대로 유지. gateway_unknown_resolution 등 추가 상태를 필드 삭제로 복구하지 않음 |
| `harness_sessions` (기존·공유) | runtime pin·session lock. U(P,S) | uses_harness/session_lock이 읽음 | runtime·lock 정합성 유지. 전체 drop/초기화 금지 권고 |
| `harness_results` (기존) | observation·표·artifact 참조. U(result_id) | 결과 reader가 읽음 | run/결과/blob 계보 보존, 운영 정리 정책 미확정 |
| `harness_invocations` (기존) | 도구 호출·중복/효과 기록. U(run_id,invocation_id) | 실행 저장소가 읽음 | 실행 결과와 같이 보존, 정책 미확정 |
| `harness_blobs.files`, `harness_blobs.chunks` (기존 GridFS) | 표 원본·artifact. 코드에는 bucket_name만 선언; 드라이버 관리 인덱스의 실제 DB 상태 미확인 | 결과/artifact reader가 읽음 | metadata 참조와 함께 정리; 이 문서는 drop을 지시하지 않음 |
| `harness_checkpoints`, `harness_checkpoint_writes` (기존) | MongoDBSaver 체크포인트. 인덱스는 라이브러리 관리, 실제 DB 상태 미확인 | 원 runtime 복구가 읽음 | 활성 실행 drain 전 삭제 금지, 기간 미확정 |

근거: `harness/store.py:30-52,208-245`, `harness/gateway/session_store.py:263-286`, `pending_controls.py:30-53`, `execution.py:83-88,107-129,246`, `events.py:147`, `harness/app.py:21-23`. 자동 TTL/운영 purge 정책은 위 선언에서 확인되지 않는다. 임시 테스트 DB만 `harness_pr10_test_<uuid>`로 만들고 finally에서 drop한다. PR9 fixture의 `zz_pr9_<uuid>`는 정상 종료에서 정리되며 강제 종료 후 정리는 사용자 확인이 필요하다. 운영 DB를 테스트 이름으로 추정하거나 전체 drop하지 않는다.

## 되돌리기 체크리스트와 사용자 검증

- [ ] 제한 배포 대상·관찰 기간·중단 기준·담당자 확정(현재 미확정).
- [ ] 동일 principal의 신규/기존 이력, 공개 결과·SSE cursor를 기록하고 접근 권한을 확인한다.
- [ ] UI 링크 제거 후 기존 App, 진행 중 실행 지속, 결과 재조회 확인.
- [ ] 신규 admission 중단, 원 runtime drain/취소, OFF 재기동 순서 확인.
- [ ] OFF에서 아래 확인, 기존 reader 응답 키·after replay·소유권을 검증한다.
- [ ] yield_frontend 기존 테스트와 브라우저 회귀는 사용자 수행; 종료일 없이 유지.
- [ ] 감사/규칙/receipt/원본 결과를 보존하고 삭제 정책은 별도 승인한다.

다음은 **사용자 실행 예시**다. 여기서는 curl·서버·브라우저·Mongo·Python pytest를 실행하지 않았다. BASE는 실제 배포 주소로 바꾸고 인증은 확정된 규격을 사용한다. 기본 포트 충돌은 [PR9B_RUNBOOK.md](PR9B_RUNBOOK.md) 참조.

```sh
BASE=http://127.0.0.1:8001
curl -i "$BASE/health"                         # 200
curl -i "$BASE/gateway-m1/api/sessions"        # OFF: 404
curl -i "$BASE/gateway-m1/api/ws"              # OFF: HTTP probe 404
curl -i "$BASE/api/ws"                        # 직접 agent_server 기준 404
curl -i "$BASE/api/sessions"                  # 직접 agent_server 기준 404
curl -i "$BASE/runs/$RUN_ID"                  # 본인 run 200, 기존 응답 키
curl -N "$BASE/runs/$RUN_ID/events?after=1"    # sequence > 1; Last-Event-ID 대체 아님
curl -i "$BASE/harness/sessions/$SESSION_ID"
../.venv/bin/python -m pytest harness/tests/test_pr10_rollback.py -q
# Mongo 케이스: 안전하게 HARNESS_MONGO_URI를 설정한 사용자 환경에서만 실행.
cd yield_frontend
npm test
```

`harness/tests/test_pr10_rollback.py`는 실제 마운트 AST 블록, 독립 import subprocess, Mongo 공유 run 재조회·SSE·OFF 앱 재조립을 다룬다. gateway session row 없는 구 run은 gateway 목록에 자동 등장하지 않는다. row가 있으면 구 run query가 이력에 반영되고, 별도 user transcript와 섞이면 503 `handler_not_connected`로 닫힌다. 이는 특성화이며 성공으로 바꾸지 않는다. 모델·실제 프로세스 재시작·배포 프록시 동작까지 입증하지 않는다.

## PB 합의 대응

| 합의 | 이 문서/남은 게이트 |
|---|---|
| §7.1 PR-10 (667행), 실제 허용 데이터→모델→도구→검증→확정→재조회 | 테스트 작성과 오프라인 검증만 수행. 실제 E2E와 제한 배포 승인은 사용자 검증 후 별도 |
| §7.1 opt-in·rollback (681행) | UI만/API까지 분리, 활성 실행 원 runtime 유지, additive 저장/구 reader 재조회 |
| §7.2 Q1 인증 | 서버 확정 principal; 사내 인증·origin/ticket·프록시 규격 미확정 |
| §7.2 Q5 항상 허용 | 규칙/감사 별도 Mongo, 보존 기간 미확정 |
| §7.2 Q6 폰트 | 시스템/승인 폰트, 법무 판단 별도; PR10_LIMITS 참조 |
| §7.2 Q7 legacy 유지 | 기존 UI·/runs·SSE 종료일 없음. 전환 일정은 회귀 결과 후 합의 |
