# PR-7b-1 WebSocket 연결

## 설계와 실행 계획

사용자가 승인한 범위만 현재 작업 트리에서 구현한다. 커밋/설치/모델 호출 없음.
원본 H는 /tmp/hermes-agent-ro (5894e750), PB는 PORTING_BASELINE.md다.

- [x] 새 services.py: 서버 소유 principal/profile에 PR-3/7a 어댑터를 묶는다.
- [x] 새 session_rpc.py: 원본 모드별 결과, Mongo 세션과 공개 이력 연결.
- [x] 새 connection.py: 연결별 구독/응답 매칭/단일 pump/직렬 송신/종료 정리.
- [x] routes.py: services=None 인자와 accept 뒤 연결 분기만 추가.
- [x] 새 단위 테스트에서 실제 in-process WebSocket으로 경계·경쟁·복구 검증.
- [x] 새 Mongo 통합 테스트 작성 (실행 금지).
- [x] 지정된 허용 목록 회귀 실행 후 근거·제약·남은 항목 기록.

원본 rpc_dispatch.py:46-100처럼 느린 처리를 수신과 분리한다.
연결 수명 제어는 직렬, 실행 요청은 독립 태스크, 송신은 하나의 잠금으로 보호한다.
Mongo pull pump는 원본 대응 없음 → PB:503-505,575,664. 이벤트는 7a projection만 사용한다.
services 미주입 경로와 HTTP는 그대로 유지한다.

## 연결된 메서드

L=legacy(기존 전송 계약), A=authority(실행 권위 계약). 모드는 서버가 선택한다.
아래 원본 경로의 H는 /tmp/hermes-agent-ro, 기준 커밋은 5894e750이다.
원본의 함수 자체는 에이전트·파일·전역 상태를 초기화하므로 import하지 않는다.
원본 계약의 안전한 필드만 PR-3/7a로 연결한다. 분류/manifest는 변경하지 않았다.

| 이름 | 모드 | 원본 근거 | 연결 |
|---|---|---|---|
| ping | L | H/tui_gateway/methods_voice.py:542 | pong:true |
| client.capabilities | L | H/tui_gateway/methods_voice.py:521; contracts/liveness.py:29 | 연결별 server_requests 협상; approval/clarify만 |
| gateway.capabilities | L | H/tui_gateway/methods_voice.py:513 | per_session_exclusive_submit:false (legacy 제출 비활성) |
| session.create | L/A | H/tui_gateway/methods_session.py:484; contracts/sessions.py:118; contracts/canonical_sessions.py:23,133 | MongoSessionStore.create_session, Execution.snapshot/handle |
| session.list | L/A | H/tui_gateway/methods_session.py:541; contracts/canonical_sessions.py:163 | list_sessions_rich, 공개 history; A는 scope=stored만 |
| session.most_recent | L | H/tui_gateway/methods_session.py:568 | list_sessions_rich; 내부 source 및 unknown 제외 |
| session.resume | L/A | H/tui_gateway/methods_session.py:1049; contracts/canonical_sessions.py:64,133 | resolve_resume_session_id, snapshot/handle, pending 복원 |
| session.activate | L | H/tui_gateway/methods_session.py:1157 | snapshot, 연결 구독 |
| session.history | L | H/tui_gateway/methods_session.py:1978 | get_compression_lineage/get_messages + verified_answer |
| session.title | L | H/tui_gateway/methods_session.py:1219 | get_session_title/set_session_title; 4021/4022 |
| session.info | A | H/tui_gateway/contracts/canonical.py:175,204 | get_session의 안전한 필드만 |
| session.usage | L | H/tui_gateway/methods_session.py:1448; contracts/common.py:28 | 저장된 native_usage의 input/output/total/calls 합계; 불완전하면 미연결 |
| session.close | L | H/tui_gateway/methods_session.py:2177 | Execution.interrupt + 해당 연결 구독/대기 요청 정리; 저장 이력 보존 |
| session.detach | A | H/tui_gateway/contracts/canonical.py:314 | 연결별 subscription_id 일치 검사; 오래된 ID는 detached:false |
| session.status | L | H/tui_gateway/contracts/sessions.py:478 | Execution.rpc → status |
| session.interrupt | L/A | H/tui_gateway/contracts/sessions.py:605; contracts/canonical_sessions.py:324 | Execution.rpc → interrupt; 서버 snapshot/submit의 run ID로 세대 결합 |
| prompt.submit | A | H/tui_gateway/contracts/canonical_sessions.py:205,319 | Execution.rpc → submit (동일 submission_id 실행 중복 방지) |
| prompt.submit | L | H/tui_gateway/methods_prompt.py:816,982; PR7A 문서 | Execution.rpc의 unsupported(-32004) 유지 |
| prompt.receipt | A | H/tui_gateway/contracts/canonical.py:55 | Execution.rpc → receipt |
| prompt.cancel | A | H/tui_gateway/contracts/canonical.py:64 | Execution.rpc → cancel; queued만 |
| prompt.resolve_unknown | A | H/tui_gateway/contracts/canonical.py:74 | Execution.rpc → resolve_unknown |
| clarify.respond | A | H/tui_gateway/contracts/canonical.py:355,367 | Execution.rpc → respond |
| clarify.lock | L/A | H/tui_gateway/methods_prompt.py:1267; contracts/canonical.py:372 | principal로 pending 조회, Execution.rpc → clarify_lock |
| approval.respond | L/A | H/tui_gateway/methods_prompt.py:1327; contracts/canonical_sessions.py:286 | Execution.rpc 모드별 어댑터 |
| approval.pending | L | H/tui_gateway/methods_prompt.py:1306 | Execution.rpc → approval_pending |
| request.answer | L | H/tui_gateway/methods_prompt.py:1289 | principal로 pending 조회, Execution.rpc → request_answer |
| session.events.since | L/A | H/tui_gateway/methods_session.py:2309; H/gateway/session_events.py:77 | Execution.rpc → since_legacy/since; legacy open_requests 별칭 |

clarify.respond는 manifest상 A에만 있다. ping/capabilities/approval.pending/
request.answer는 L에만 있다. 반대 모드에 추가하거나 fallback하지 않는다(-32601).
알려진 비활성 메서드는 기존 rpc_rejection으로 -32004, 모르는 이름은 -32601이다.
기타 implemented는 -32005 handler_not_connected이며 가짜 성공을 반환하지 않는다.

## 프레임과 수명

1. 기존 인증 → origin → 일회용 ticket → accept 순서 유지. 기본 인증은 계속 거부한다.
2. services가 주입되면 연결별 GatewayConnection을 만들고, 첫 저장소 요청에서
   services.open(검증된 Principal)을 호출한다. Mongo와 PendingControls 인덱스를 준비한다.
3. create/resume/activate/authority submit 성공으로 구독한다. snapshot 응답을 보낸 뒤
   해당 구독을 활성화한다. 같은 연결의 세션 전환도 이전 구독을 자동 삭제하지 않는다.
4. 연결당 하나의 pump가 SessionEvents.since를 부른다. since 내부에서 ingest를 하므로
   별도의 이벤트 수집기를 복제하지 않는다. event_frame으로 seq 순서대로 송신한다.
5. 각 연결은 독립 epoch/seq를 가진다. 누락·절단·epoch 변화 시 replay_gap을 보내고
   해당 구독을 일시 중지한다. 클라이언트가 resume snapshot을 조회하면 새 구독 ID와
   새 기준점으로 다시 전송한다. HTTP snapshot 연결은 7b-2 범위다.
6. legacy는 client.capabilities에서 server_requests:true를 보낸 연결에만
   clarify/approval 요청을 보낸다. 원본 srq-<12 hex> 형식의 연결별 ID를 발급하고
   실제 pending ID는 내부에만 매핑한다. snapshot/replay의 open_requests도 같은 별칭이다.
7. method 없는 id+result/error 프레임을 일반 RPC보다 먼저 처리한다. 그 연결의 열린 ID만
   매칭하고, 타 연결·타 principal·종료된 ID는 무시한다. 오류 응답은 pending을 취소한다.
   request.answer는 원본대로 놓친 요청의 durable ID를 본인 principal 범위에서 찾을 수 있다.
8. detach/close는 구독과 별칭을 정리한다. 연결 종료는 pump와 요청 태스크를 취소·await하고,
   AnyIO 취소 범위로부터 보호한 정리 작업으로 마지막 legacy 관찰자의 pending을 취소한다.
   authority pending은 영속 저장소에 남아 다음 resume에서 복원된다.

## 설계 결정과 원본 차이

- **주입 구조:** GatewayServices(store, controller, profile_id_for=..., poll_interval=0.1,
  max_requests=32). store/controller는 반드시 같은 저장소를 쓴다. 객체와 Motor는 앱의
  event loop/lifespan에서 생성한다. 서비스는 호스트가 소유하고 연결 종료 시 닫지 않는다.
  원본 대응 없음 → PB:575,664. serve_ws 인터페이스는 routes.py의 의존성 제한을 지킨다.
- **권한:** Principal.user_id만 권위다. params의 principal/principal_id/profile/profile_id는
  권한 문맥에 사용하지 않는다. operator/host 권한, desktop_local의 로컬 권한은 승계하지
  않는다. 원본 H/hermes_cli/web_routers/chat_ws.py:702-731과 다른 PB:416 결정이다.
- **profile 기본값:** 원본 이름 default(H/hermes_cli/profiles.py:249-250). 회사 매핑을 추정하지
  않으며 profile_id_for로 교체한다. legacy source 기본은 환경 탐색 없이 tui
  (H/tui_gateway/agent_factory.py:27-38), authority는 원본 cli다.
- **요청 분리:** H/tui_gateway/rpc_dispatch.py:46-100, ws.py:490-509의 비동기 처리 의도를 따른다.
  수신과 요청 태스크는 분리하며 동시 요청은 연결당 32개다. ping은 포화되어도 처리한다.
  create/resume/activate/close/detach는 연결별 잠금으로 순서대로 처리한다. 그 외 응답은
  완료 순서이며 클라이언트는 id로 대응해야 한다. 모든 실제 전송은 같은 send_lock을 쓴다.
  원본의 worker pool/세부 read lane 대신 asyncio 태스크를 쓰는 호스트 결정(PB:664)이다.
- **구독:** 원본 대응 없음 → PB:503-505,575,664. Mongo pull만 사용하고 연결 밖 장기 작업은
  만들지 않는다. 일반적인 admission lock 경합(session_busy)은 다음 poll에서 재시도한다.
  다른 저장소/pump 오류는 연결 1011 종료로 복구를 요구한다. RPC 예상 밖 오류는 -32603
  Internal error이며 예외 문자열/스택/비밀을 반환하지 않는다.
- **공개:** 이벤트는 7a SessionEvents의 publication projection만 통과한다. 원문 candidate,
  delta/interim, tool 본문, reasoning은 우회하지 않는다. 이력은 host run query와
  verified_answer만 공개한다. 기존 PR-3 assistant/tool 원문은 검증 증거 없이 공개하지 않는다.
- **저장:** 세션은 바로 Mongo에 생성한다. runtime/stored ID는 호스트에서 같다. request_id
  또는 idempotency_key가 있으면 principal과 키에서 안정된 ID를 만들고, 없으면 UUID를 만든다.
  원본의 로컬 runtime ID/SQLite 지연 저장과 다름 → PB:575,664.
- **snapshot:** 여러 Mongo 문서를 묶는 트랜잭션은 없다. replay 경계를 이력보다 먼저 읽어
  동시 이벤트 유실을 피한다. 경계 동안 바뀐 메시지가 snapshot과 replay 양쪽에 나타날 수
  있으므로 클라이언트의 재조회/중복 제거가 필요하다(PB:503-505).
- **종료:** close는 본인 실행을 interrupt하고 호출 연결의 구독을 정리한다. 다른 창은
  종료 이벤트를 받을 수 있다. 원본 process agent 제거/파일 정리/플러그인 finalize는 없다.
  detach.end_reason에 따른 마지막 전역 관찰자의 영속 종료는 아직 미연결이다.
- **legacy 별칭:** 원본은 요청 ID를 연결 사이에 공유하지만, 여기서는 사용자 요구의
  타 연결 응답 차단을 위해 연결별 별칭을 쓴다. 동일 GatewayServices의 여러 창 중 마지막
  관찰자가 떠날 때만 pending을 취소한다. 원본 대응 없음 → PB:698,703.
- **레거시 미협상:** capabilities가 없는 연결에는 서버 요청을 보내지 않는다. 이미 등록된
  영속 pending은 PR-6의 timeout/취소로 정리되며, 원본처럼 등록 자체를 즉시 None으로 바꾸는
  새 producer는 만들지 않았다. timeout=None인 외부 등록의 정책은 호스트가 정해야 한다.

## 모호/미구현/결정 필요

- session.active_list: 원본 필수 model 및 process agent inventory를 안전하게 만들 수 없어 미연결.
- session.stats: 필수 model/provider, compressor/context/latency 등 실제 통계가 없어 미연결.
- approval.received: 원본은 화면 표시 시 timeout을 시작하지만 PR-6은 등록 시 시작한다.
  동일 효과 없이 acknowledged:true를 만들지 않고 미연결로 둔다.
- authority session.list의 기본 scope=live는 원본 live agent inventory가 없어 미연결.
  scope=stored는 연결했다. session.usage는 실행 중/누락된 native_usage/이식된 이력만 있는
  경우 미연결이며, 기록된 실제 수치가 모두 있는 경우만 집계한다.
- session.create는 source/title/모드별 idempotency 식별자만 연결한다. 모델/파일/도구/권한/
  seed messages 등 다른 생성 옵션은 연결하지 않는다. resume는 명시적 session_id와
  omit_messages를 연결하며 latest/title 검색, deferred/lazy/eager build, API key 복원 등은 없다.
- PR-3 user 메시지와 host run이 동일 세션에 함께 있으면 공통 메시지 ID가 없어 안전한 순서/
  중복 판별이 불가하다. 이 경우 history/snapshot은 미연결로 남긴다. 일반 7a run 이력은 지원한다.
- 여러 GatewayServices/서버 프로세스에 걸친 legacy 관찰자 소유권은 아직 영속화하지 않았다.
  한 서비스 인스턴스 안의 다중 창은 지원한다. 여러 프로세스를 동시에 운영하려면 durable
  observer lease 또는 마지막 연결 취소 정책을 결정해야 한다.
- 세션 단위 generation과 run 단위 epoch 차이는 7a의 제한 그대로다. 이 연결이 본 snapshot/
  submit의 run_id만 interrupt에 사용한다. 검증된 결합이 없으면 7a의 stale_generation 거부를 유지한다.
- 회사 인증/profile 매핑, 운영 마운트, 실제 모델/도구, pending outbox 원자성은 미검증/별도 결정이다.

## 7b-2 인계

GatewayServices.open(principal)의 GatewayScope(sessions, execution)를 REST에서도 재사용할 수 있다.
서비스 생명주기와 profile_id_for는 호스트가 주입한다. agent_server.py는 수정하지 않았으므로
현재 운영 마운트는 여전히 기본 deny resolver/서비스 미주입 상태다.

남은 정적 implemented RPC: config.get, model.options, profiles.list, tools.list,
tools.show, toolsets.list, i18n.catalog, i18n.languages, paste.collapse.
이 외 위의 세션 관련 부분 미연결 항목도 그대로 남는다. manifest 분류는 바꾸지 않았다.
HTTP는 전부 이전 동작 그대로다. 최소 REST는 세션 목록/메시지, health/identity/status,
안전한 config/model/tools/profile projection이다(PB:642). 정확한 활성 대상은 manifest의
routes/external_routes를 7b-2에서 사용해야 하며 기존 /runs와 SSE는 별도 그대로 유지한다.

## 검증 기록

새 테스트를 먼저 실패시킨 뒤 구현/수정했다. 검토에서 발견한 구독 해제와 pending 조회의
경쟁, snapshot 실패 후 구독이 켜지지 않는 문제, 접수 잠금 경합을 회귀 테스트로 고정했다.
기존 파일 중 실제 변경은 gateway/routes.py의 선택 인자와 서비스 분기 **4줄 추가뿐**이다.
기존 파일들이 이미 Git 미추적 상태여서 git diff만으로 작업 전후 비교가 되지 않으며,
기존 라우트 테스트 및 직접 확인한 원문을 기준으로 추가 범위를 확인했다.

Mongo 통합 테스트 test_gateway_pr7b_mongo.py는 **내가 실행하지 않음**.
앱 lifespan에서 Motor 생성, UUID DB/finally drop, 실제 RunController의 launch만 가짜로
대체한다. 제출/ACK 유실/동시 제출/두 principal/두 창/replay gap/검증 공개/두 pending wire/
재접속 snapshot/연결 종료 정리 시나리오를 작성했다. 구문 검사만 한다.
모든 Mongo·engine·실제 socket 테스트, 실제 모델·도구 호출, Docker·설치,
git commit/push/stash/checkout은 실행하지 않았다.

7b-2에서 검토할 manifest의 implemented HTTP 목록(이번에는 모두 미연결 그대로):

- GET /api/config, /api/config/defaults, /api/config/schema
- GET /api/egress/status, /api/health, /api/health/idle, /api/host/identity, /api/status
- GET /api/model/info, /api/model/options, /api/model/recommended-default
- GET /api/profiles, /api/profiles/active, /api/profiles/sessions, /api/profiles/sessions/sidebar
- GET /api/sessions, /api/sessions/{session_id}/messages
- GET /api/tools/toolsets, /api/tools/toolsets/{name}/config, /api/tools/toolsets/{name}/models
- GET /api/auth/me, POST /api/auth/ws-ticket, POST /auth/logout

인증 관련 세 항목도 사내 규격이 결정되기 전에는 추정 구현하지 않는다.


최종 검증: 허용된 15개 파일 전체 **874 passed, 1 deselected (39.55초)**.
제외된 1개는 test_native_budget.py의 local_server 테스트다.
그 뒤 미연결 정적 RPC가 저장소를 열지 않도록 분기를 보강하고 새 단위 파일을 다시 실행:
**25 passed (1.43초)**. Mongo 파일은 import/수집/실행 없이 ast.parse 구문 검사만 했다.

실행 명령:

```sh
cd /Users/daehwankim/yield-agent/08-YieldAgent && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -p no:cacheprovider harness/tests/test_publication.py harness/tests/test_native_pr5.py harness/tests/test_native_pr4c.py harness/tests/test_native_pr6.py harness/tests/test_native_ipc.py harness/tests/test_native_stop_gate.py harness/tests/test_native_verification.py harness/tests/test_native_budget.py harness/tests/test_pending_controls_unit.py harness/tests/test_gateway_auth.py harness/tests/test_gateway_contract_frames.py harness/tests/test_gateway_contract_manifest.py harness/tests/test_gateway_routes.py harness/tests/test_gateway_pr7a_unit.py harness/tests/test_gateway_pr7b_unit.py -k 'not local_server' -q
```
