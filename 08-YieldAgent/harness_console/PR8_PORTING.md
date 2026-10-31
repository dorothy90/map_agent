> 상단은 PR-8a 이력이다. 현재 파일 상태·해시·제외 결정은 아래 PR-8c 기록이 우선하며 미변경 파일은 PR-8b 최종 표를 따른다.

# PR-8a 웹 전송 계층 이식

원본 H: /tmp/hermes-agent-ro, 커밋 **5894e750c3dba980d43b25aa29e3958149ba2c61** (읽기 전용).
PB: [PORTING_BASELINE.md](./PORTING_BASELINE.md). 구현 위치는 harness_console뿐이다.

## 설계 및 실행 계획
- [x] 복사 전 import closure 분석 → 45개 원본 파일 바이트 복사.
- [x] 원본 shared/request/channel/replay/canonical 구현 유지, 웹 경계만 주입.
- [x] 원본 node 테스트 무수정 실행, 웹 어댑터 가짜 소켓 검증.
- [x] TypeScript 5.9.3, 기존 node:test, Vite 빌드 검증.
- [x] 전체 이식 해시 및 patch/shim 이유 기록.

### 선택한 경계
WebHostAdapter → (공개 request gate + socket wire gate) → HermesGateway → JsonRpcGatewayClient → JsonRpcRequestChannel.
CanonicalDesktopProtocol은 바이트 그대로 유지한다. session resume/receipt/replay의 내용 변환을 새로 구현하지 않았다.
웹 어댑터만 연결 descriptor, location/fetch, 소켓 생성 및 재접속 일정의 소유자가 된다.
원본 store/gateway 전체를 가져오는 방식은 UI/store/native 의존과 자동 효과를 끌어들이므로 제외했다.
원본의 무인자 HermesGateway와 native_dial 동작은 무수정 원본 테스트 호환용으로 남아 있지만,
웹 소비자는 반드시 WebHostAdapter.gateway를 사용한다. 웹은 rpcMode를 항상 전달하고 socketFactory를 교체하므로 native ticket 경로를 실행하지 않는다.

## import closure (복사 전에 분석)
| 원본 | 이식 경로 | 직접 import |
|---|---|---|
| apps/shared/src/json-rpc-channel.ts | src/protocol/json-rpc-channel.ts | ./gateway-contract.generated.js, ./gateway-events.js |
| apps/shared/src/json-rpc-gateway.ts | src/protocol/json-rpc-gateway.ts | ./gateway-events.js, ./json-rpc-channel.js, ./gateway-events.js |
| apps/shared/src/gateway-events.ts | src/protocol/gateway-events.ts | ./gateway-contract.generated.js, ./gateway-contract.generated.js, ./gateway-contract.generated.js |
| apps/shared/src/gateway-contract.generated.ts | src/protocol/gateway-contract.generated.ts | : { params: SessionForeignIdParams; result: SessionForeignImportResult }
  /** One page of Claude Code / Codex sessions found on the serving backend. */
  , ,
   |
| apps/shared/src/reconnect-backoff.ts | src/protocol/reconnect-backoff.ts |  |
| apps/shared/src/websocket-url.ts | src/protocol/websocket-url.ts |  |
| apps/shared/src/backend-scope.ts | src/protocol/backend-scope.ts |  |
| apps/shared/src/json-rpc-channel.test.ts | src/protocol/json-rpc-channel.test.ts | vitest, ./json-rpc-channel.js |
| apps/shared/src/json-rpc-gateway-connect.test.ts | src/protocol/json-rpc-gateway-connect.test.ts | vitest, ./json-rpc-gateway |
| apps/shared/src/json-rpc-gateway-legacy-replay.test.ts | src/protocol/json-rpc-gateway-legacy-replay.test.ts | vitest, ./json-rpc-gateway |
| apps/shared/src/json-rpc-gateway-replay.test.ts | src/protocol/json-rpc-gateway-replay.test.ts | vitest, ./json-rpc-gateway |
| apps/shared/src/reconnect-backoff.test.ts | src/protocol/reconnect-backoff.test.ts | vitest, ./reconnect-backoff.js |
| apps/desktop/src/lib/gateway-rpc.ts | src/hermes/lib/gateway-rpc.ts | @hermes/shared |
| apps/desktop/src/lib/gateway-rpc.test.ts | src/hermes/lib/gateway-rpc.test.ts | @hermes/shared, vitest, ./gateway-rpc |
| apps/desktop/src/lib/gateway-ws-url.ts | src/hermes/lib/gateway-ws-url.ts | @hermes/shared, @/global |
| apps/desktop/src/lib/gateway-ws-url.test.ts | src/hermes/lib/gateway-ws-url.test.ts | @hermes/shared, vitest, @/global, ./gateway-ws-url |
| apps/desktop/src/lib/gateway-events.ts | src/hermes/lib/gateway-events.ts | @/app/shell/statusbar-controls |
| apps/desktop/src/lib/gateway-events.test.ts | src/hermes/lib/gateway-events.test.ts | vitest, ./gateway-events |
| apps/desktop/src/lib/gateway-liveness-policy.ts | src/hermes/lib/gateway-liveness-policy.ts |  |
| apps/desktop/src/lib/gateway-liveness-policy.test.ts | src/hermes/lib/gateway-liveness-policy.test.ts | vitest, ./gateway-liveness-policy |
| apps/desktop/src/lib/execution-authority.ts | src/hermes/lib/execution-authority.ts |  |
| apps/desktop/src/lib/execution-authority.test.ts | src/hermes/lib/execution-authority.test.ts | @hermes/shared, @hermes/shared, vitest, ./execution-authority, ./execution-authority.frames.json |
| apps/desktop/src/lib/execution-authority.frames.json | src/hermes/lib/execution-authority.frames.json |  |
| apps/desktop/src/lib/replay-gap-owner.ts | src/hermes/lib/replay-gap-owner.ts | @hermes/shared, @/store/session-request-router |
| apps/desktop/src/lib/replay-gap-owner.test.ts | src/hermes/lib/replay-gap-owner.test.ts | vitest, ./replay-gap-owner |
| apps/desktop/src/lib/with-timeout.ts | src/hermes/lib/with-timeout.ts |  |
| apps/desktop/src/lib/with-timeout.test.ts | src/hermes/lib/with-timeout.test.ts | vitest, ./with-timeout |
| apps/desktop/src/lib/session-ids.ts | src/hermes/lib/session-ids.ts |  |
| apps/desktop/src/lib/session-ids.test.ts | src/hermes/lib/session-ids.test.ts | vitest, ./session-ids |
| apps/desktop/src/lib/json-rpc-gateway-heartbeat.test.ts | src/hermes/lib/json-rpc-gateway-heartbeat.test.ts | @hermes/shared, vitest |
| apps/desktop/src/lib/json-rpc-gateway-recovery.test.ts | src/hermes/lib/json-rpc-gateway-recovery.test.ts | @hermes/shared, vitest |
| apps/desktop/src/lib/json-rpc-gateway-url-guard.test.ts | src/hermes/lib/json-rpc-gateway-url-guard.test.ts | @hermes/shared, vitest |
| apps/desktop/src/api/client.ts | src/hermes/api/client.ts | @hermes/shared, nanostores, @/global, ./canonical-protocol |
| apps/desktop/src/api/canonical-protocol.ts | src/hermes/api/canonical-protocol.ts |  |
| apps/desktop/src/hermes.ts | src/hermes/hermes.ts | ./api/client, ./api/client, ./api/config, ./api/cron, ./api/delegation, ./api/local-models, ./api/mcp, ./api/messaging, ./api/models, ./api/plugins, ./api/profiles, ./api/sessions, ./api/skills, ./api/system, ./api/toolsets, @/types/hermes |
| apps/desktop/src/api/canonical-ambiguous-owner-reconcile.test.ts | src/hermes/api/canonical-ambiguous-owner-reconcile.test.ts | @hermes/shared, vitest, ./client |
| apps/desktop/src/api/canonical-deadline.test.ts | src/hermes/api/canonical-deadline.test.ts | @hermes/shared, vitest, ./client |
| apps/desktop/src/api/canonical-live-pick.test.ts | src/hermes/api/canonical-live-pick.test.ts | @hermes/shared, vitest, ./client |
| apps/desktop/src/api/canonical-prompt-owner.test.ts | src/hermes/api/canonical-prompt-owner.test.ts | @hermes/shared, vitest, ./canonical-protocol, ./client |
| apps/desktop/src/api/canonical-protocol.test.ts | src/hermes/api/canonical-protocol.test.ts | vitest, ./canonical-protocol |
| apps/desktop/src/api/canonical-retained-control-retire.test.ts | src/hermes/api/canonical-retained-control-retire.test.ts | vitest, ./canonical-protocol |
| apps/desktop/src/api/canonical-retained-mutation-stale.test.ts | src/hermes/api/canonical-retained-mutation-stale.test.ts | vitest, ./canonical-protocol |
| apps/desktop/src/api/canonical-rewind-lost-reply.test.ts | src/hermes/api/canonical-rewind-lost-reply.test.ts | vitest, ./canonical-protocol |
| apps/desktop/src/api/canonical-session-updated-revision.test.ts | src/hermes/api/canonical-session-updated-revision.test.ts | vitest, ./canonical-protocol |
| apps/desktop/src/api/canonical-unknown-admission-fence.test.ts | src/hermes/api/canonical-unknown-admission-fence.test.ts | vitest, ./canonical-protocol |

외부 런타임 의존은 기존 nanostores뿐이다(api/client의 원본 $apiRequestScope map).
이 map은 원본 API의 ambient scope 계약을 보존하기 위한 것이며, session/event/receipt/pending store 이식은 하지 않았다.
React는 StatusbarMenuItem의 ReactNode **타입 전용**이다. 테스트는 기존 vitest, node:fs/crypto를 쓴다.
@hermes/shared는 src/protocol/index.ts, @/는 src/hermes/로 tsconfig paths + vitest alias에만 연결한다.
Vite 설정/main.tsx는 변경하지 않았으므로 실제 UI 번들 연결용 alias 및 opt-in은 PR-9에서 다룬다.

### 제외한 closure
| 원본 묶음 | 이유/후속 |
|---|---|
| shared index의 billing/charge/catalog/theme/skin/translucency/i18n/cron/color/font 관련 export | 전송 의존이 아님. 축소 barrel에서 제외 (D03,665). |
| gateway-contract.openrpc.json | 복사된 generated 타입과 현재 테스트가 import하지 않음. |
| desktop api의 config/cron/delegation/local-models/mcp/messaging/models/plugins/profiles/sessions/skills/system/toolsets 및 type barrel | 전송 client의 의존이 아닌 상위 API; 8b/PR-9에서 필요한 closure만 추가. |
| store/gateway, session, notifications, notify-baseline, local-gateway-ensure, secondary socket pool, i18n translateNow | 이식한 transport에서 직접 import하지 않는다. 따라서 가짜 성공 no-op shim을 만들 필요도 없다. 8b에서 capability로 effect/polling/subscription을 제한. |
| lib/connection-scoped 및 persistence storage closure | nanostores/localStorage 동기화 store 범위 → 8b. |
| lib/gateway-health-pill 및 runtime-readiness, i18n/en | 상태바 표시 파생 함수로 전송 필수 의존이 아님 → PR-9. |
| PromptOverlays, UI TSX, assistant-ui, streamdown, @nous-research/ui, fonts.css/woff2, Electron/native 모듈 | 전부 미복사·미import. PromptOverlays 통째 비활성 (PB:681,D03,D10). |

## 모드와 협상
- 원본 기본은 legacy: H/apps/desktop/src/api/client.ts:312-315의 native_dial 없는 URL.
- WebHostOptions.rpcMode 기본 legacy, authority는 명시적으로 선택. mount와 일치시킬 책임은 호출자에게 있다. 실패 시 URL/모드 fallback은 없다.
- 원본 channel의 gateway.ready → client.capabilities(server_requests:true), canonical attach → client.capabilities(desktop_local:true) 흐름은 그대로다.
- Yield manifest의 capability 두 메서드는 legacy에만 있다. legacy에서는 원본 client.capabilities 및 host의 gateway.capabilities 조회를 사용한다.
- authority에서 원본이 내부적으로 시도한 legacy-only capability 호출은 socket gate가 로컬 -32004 응답으로 끝낸다. capability를 얻으려고 다른 mount에 연결하지 않는다.
- gateway.capabilities는 per_session_exclusive_submit 같은 원본 필드다. 임의 RPC 허용 목록으로 해석하거나 성공을 꾸미지 않는다.
- gateway.ping은 현재 manifest에 없고 서버 gateway.ready도 heartbeat를 광고하지 않는다. 웹 호스트는 이 경우 원본 heartbeat timer 자체를 끈다. 원본 heartbeat/liveness 정책과 테스트는 그대로 이식했다. store가 맡는 busy-session 재probe/focus 정책 결합은 8b.
- legacy prompt.submit은 manifest에 implemented지만 PR7A/PR7B 실행 계약상 서버가 unsupported를 반환한다. 클라이언트는 이를 성공으로 바꾸거나 authority로 fallback하지 않는다. 제출·receipt 검증은 authority로 수행했다.

## 웹 호스트와 인증 경계
기본 URL은 location 기반 same-origin /gateway-m1/api/ws; mount 상수는 GATEWAY_BASE_PATH 한 곳이다.
getWebSocketConnection({location,profile,rpcMode,fetch})로 매 dial마다 새 URL/티켓/프로토콜을 주입한다.
전달 방식, cookie/token 저장소를 새로 만들지 않았다. 기본 profile은 원본 canonicalProfile의 default이며, 사용자 권한은 서버에서 확인한다.
getConnection은 원본 HermesConnection 형태를 유지하되 token 빈 문자열, native 창 상태 비활성 값만 제공한다.
getWebSocketConnection이 반환한 인증 URL은 getConnection에 캐시하거나 로그로 출력하지 않는다.
api는 명시적 호출에만 fetch를 실행하며 same-origin /api/ 경로만 mount에 연결한다. native upload/다중 connection routing은 거절한다.
installApiBridge는 기존 hermesApi 호출자의 opt-in 연결점이다. 반환한 해제 함수를 호출해 정리한다.
windowEvents는 online 이벤트를 주입받는 작은 경계이며 연결이 닫혔을 때만 재접속한다. close는 타이머/이벤트와 늦은 URL 응답을 정리한다.

## capability gate 및 자동 호출 조사
정적 표는 ../harness/gateway/manifest.json을 읽어 rpc 이름 → 분류/사유/feature/모드별 분류, events, server_requests로 투영했다.
생성 스크립트는 저장소에 추가하지 않았다. source_sha256 = **6d362634879b8a2fd9d218c317075ad2eeee3cd34f784df7607388979d5aef3d**.
테스트는 backend JSON만 읽어 전 항목과 SHA-256을 비교한다. 백엔드는 변경하지 않았다.
공개 request에서 먼저 거절하여 stub 요청이 canonical attach까지 유발하지 않는다. 소켓 send도 감싸므로 원본 내부 협상 및 변환된 wire 메서드도 검사한다.
implemented가 아닌 stub/unsupported/unknown/다른 모드의 RPC는 실제 소켓 송신 0회이고 원본 JsonRpcGatewayError로 code=-32004, message=unsupported, data.reason/feature/upstream=5894e750을 반환한다.
unknown의 로컬 reason은 unknown_method_or_mode다(서버 unknown=-32601과 의도적으로 다름: 이번 작업의 로컬 차단 계약).
서버 요청도 manifest의 implemented만 후속 핸들러에 넘긴다. native 요청은 -32601로 응답하여 대기를 끝낸다.

| 원본 자동 경로 | 자동 호출/효과 | 8a 처리 |
|---|---|---|
| shared/json-rpc-channel.ts:245-257,510-539 | client.capabilities, 서버 요청 지원 협상 | 원문 유지, mode gate 적용 |
| shared/json-rpc-gateway.ts:286-291, fetchReplay | session.events.since 재접속 재생 | 원문 유지, 가짜 소켓에서 seq/epoch 확인 |
| desktop/api/client.ts:280-305,475-489 | owner ambiguity/session attach에서 session.resume | 원문 유지; public gate 후만 허용 |
| desktop/app/gateway/hooks/use-gateway-boot.ts:897-918,1572-1604 | active profile, 기본 cwd, config, session list, local runtime job watcher, terminal probe | hook 미이식, 자동 fetch/polling 없음 |
| desktop/app/gateway/report-startup-latency.ts:22 | shared_metrics.startup_latency | hook 미이식; RPC gate도 unsupported 거절 |
| desktop/api/config.ts:91,135,161 / api/models.ts:23,59 / store/profile.ts:251 | /api/status, /api/config, /api/model/info/options, /api/profiles/active | 도메인 helpers/hydrate 미이식. REST bridge 자체는 명시 호출만 |
| api/client.ts:14-23 startup 설명 | config/profile/model/cron boot burst | 전체 boot/hydrate store를 미이식하여 미기동; cron.list 등은 gate에서 차단 |
| store/gateway.ts:1-34,471-491 | Electron descriptor/registry/secondary socket resolve | 단일 WebHostAdapter, native/secondary 초기화 없음 |
| shared/json-rpc-channel.ts:579-592 | gateway.ping heartbeat | 현재 manifest 미구현이므로 타이머 off |

테스트는 양 모드에서 manifest의 **모든 비구현 RPC**와 unknown/prototype 키를 연결 직후·재연결 후 호출해 송신 수가 늘지 않는지 확인한다.
초기 handshake/지원 협상 송신 자체가 0회라는 의미는 아니다. **비구현 자동 호출이 0회**라는 의미다.

## patch / shim 전체 목록
- **src/hermes/api/client.ts** (patched): H/apps/desktop/src/api/client.ts; 312-315 모드 명시 주입,520-550 생성자 socketFactory/옵션 주입,633/700 webApi로 교체; PB:414,D10,665.
- **src/hermes/hermes.ts** (patched): H/apps/desktop/src/hermes.ts; 20행 이후 도메인 API/type export 제거,1-19 유지; PB:665/8a 범위.
- **src/protocol/index.ts** (patched): H/apps/shared/src/index.ts; 전송 export 문장만 원문에서 추출; PB:665,D03.
- **src/protocol/LICENSE-HERMES-NOTICE.md** (patched): H/LICENSE; MIT 전문 그대로 + 출처/커밋 추가; PB:705,747.
- **src/hermes/global.d.ts** (shim): H/apps/desktop/src/global.d.ts; 1016-1056,1553-1580 타입 발췌 및 URL bridge 타입만 선언; PB:414,D10.
- **src/hermes/store/session-request-router.ts** (shim): H/apps/desktop/src/store/session-request-router.ts; 21-31 소유자 타입만 유지, 실행 store 제외; PB:665/8b.
- **src/hermes/app/shell/statusbar-controls.ts** (shim): H/apps/desktop/src/app/shell/statusbar-controls.tsx; 33-44 타입만 유지, TSX UI 미복사; PB:D03,D10.
- **src/hermes/platform-bridge.ts** (shim): H/apps/desktop/src/api/client.ts; 633-639,700-702 API 호출을 명시적 설치 web host로 전달; PB:414,D10.
- **src/platform/fake-websocket.ts** (patched): H/apps/shared/src/json-rpc-gateway-legacy-replay.test.ts; 8-44 FakeWebSocket 클래스 원문 추출 + export; 원본 테스트 파일 무수정; PB:665.

추가 신규 파일: capability-gate.ts(정적 계약 경계), web-host.ts(호스트 수명/REST/연결 주입), generated JSON(현재 manifest 투영), platform 테스트, test-setup.ts(Node에 없는 CloseEvent의 표준 이벤트 필드만 제공), vitest.config.ts(node 환경/alias/대상 한정).
원본의 자연어/명령 parser는 byte 그대로 유지했으며 사례별 keyword/regex 분기를 추가하지 않았다.
TypeScript 6→5.9 호환을 위한 원본 알고리즘 patch는 필요 없었다.

## 테스트 제외 목록
| 원본 api 테스트 | 이유 |
|---|---|
| canonical-attach-snapshot.test.ts | 실제 소켓 필요(8b) |
| canonical-bot-relay-deliver.test.ts | 실제 소켓 필요(8b) |
| canonical-clarify-lifecycle.test.ts | store/UI 또는 외부 계약 fixture 의존(8b/PR-9) |
| canonical-clarify-prompt.test.ts | 실제 소켓 필요(8b) |
| canonical-create-options.test.ts | 실제 소켓 필요(8b) |
| canonical-model-confirm.test.tsx | DOM 필요(PR-9 이후) |
| canonical-producer-contract.test.ts | 실제 소켓 필요(8b) |
| canonical-profile-route.test.ts | 실제 소켓 필요(8b) |
| canonical-protocol-wire.test.ts | 실제 소켓 필요(8b) |
| canonical-reasoning-config-set.test.ts | 실제 소켓 필요(8b) |
| canonical-reconnect-replay-owner.test.ts | 실제 소켓 필요(8b) |
| canonical-rewind-redial-profile.test.ts | 실제 소켓 필요(8b) |
| canonical-watch-window-resume.test.ts | store/UI 또는 외부 계약 fixture 의존(8b/PR-9) |

DOM 필요(PR-9 이후): canonical-model-confirm.test.tsx 외에 상위 boot UI의 use-gateway-boot.test.tsx, use-gateway-boot-update-hold.test.tsx, use-gateway-boot-profile-pick.test.tsx, clarify-hydration.test.tsx, subagent-hydration.test.tsx는 이식하지 않았다.
store/gateway 및 connection-scoped.test.ts는 8b에서 이식 완료; gateway-health-pill.test.ts는 i18n/UI 표시 의존으로 PR-9.
실제 소켓 테스트는 수정하여 가짜 성공으로 만들지 않고 제외했으며, 같은 흐름을 새 platform 테스트에서 원본 FakeWebSocket 클래스를 재사용해 검증했다.

## 8b 인계 결과 (최종 갱신)
- 완료: gateway/session/event/receipt/pending 원본 nanostores 이식, 명시적 WebHostAdapter 연결/해제.
- 완료: replay_gap → session.resume 응답 경계 → REST messages → 원본 메시지 변환/store 반영. Node 가짜 전송 검증 및 실서버 시나리오 작성.
- 완료: connection-scoped/gateway registry 원본 테스트 이식. 실제 소켓을 요구하는 원본 canonical 테스트는 미실행·미복사 유지; 아래 실서버 테스트로 별도 검증 예정.
- 작성 완료·실행 대기: 임시 Mongo DB/두 마운트/인증 격리/응답 유실 통합 테스트. 운영 Q1 인증과 배포 mount 결정은 여전히 미정이다.
- 유지: rpcMode 기본 legacy(8a 호환), authority 사용은 명시 선택. 서버가 모드를 광고하지 않아 불일치는 해당 모드 요청 거절로 확인하며 fallback하지 않는다.
- PR-9: React/DOM/UI/폰트/실제 opt-in 연결. main.tsx와 기존 /runs/SSE UI는 변경하지 않았다.

## 검증 결과
- 원본 테스트 26파일은 byte 그대로이며 전부 통과.
- 새 platform 테스트 포함 vitest: 28파일 / 182테스트 통과 (원본 164 + 추가 18).
- 별도 읽기 전용 검토 후 소켓 생성 예외, capability 초기화 거절, 잘못된 URL을 실패 테스트로 재현하고 웹 호스트에서 수정했다. ACK 유실 → 끊김 → redial → reattach → 동일 submission_id 재전송도 추가 확인했다.
- npx --no-install tsc --noEmit: 통과.
- npm test: 기존 node:test 10/10 통과.
- npm run build: tsc와 Vite 7.3.7 빌드 통과. 설치된 실제 Vite 버전을 사용했으며 의존성 변경 없음.
- 실제 소켓/네트워크/Mongo/모델/백엔드/브라우저 end-to-end는 지시대로 미실행. 이것은 8a 가짜 전송 테스트 결과이지 실서버 통합 완료가 아니다.
- npm install/ci/add, Docker, git commit/push/stash/checkout, .env 및 scripts 열람/수정 없음.

## 파일 수와 상태
상태 집계(문서/package/tsconfig 제외): {"verbatim":43,"patched":5,"shim":4,"new":7}.
| 디렉터리 | 파일 | 줄 수 |
|---|---:|---:|
| src/protocol | 14 | 10659 |
| src/hermes | 37 | 5060 |
| src/platform | 7 | 4497 |

기존 파일 수정은 package.json scripts.test:protocol 추가, tsconfig.json compilerOptions.paths 추가뿐이다.
새 vitest.config.ts는 기존 core.test.ts를 제외하고 세 새 디렉터리의 *.test.ts만 node 환경에서 실행한다.
PR8_PORTING.md 자체 해시는 자기 참조를 피하기 위해 표에 넣지 않았다.

## PR-8a 당시 SHA-256 표 (현재 값은 아래 8b 최종 표 참조)
| 이식 경로 | 원본 경로 (H=5894e750) | 원본 SHA-256 | 이식본 SHA-256 | 상태 | 사유/PB |
|---|---|---|---|---|---|
| src/protocol/LICENSE-HERMES-NOTICE.md | LICENSE | 821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6 | 8fecc32f90572499f38b8b2d5a14a4c1e450282579e4609b78023884a915e1dd | patched | MIT 전문 그대로 + 출처/커밋 추가; PB:705,747 |
| src/protocol/backend-scope.ts | apps/shared/src/backend-scope.ts | fd6998b8f0d4b6969f3147a19d7a1c93c2603f26cddc930b86a9e2e58ff34a3f | fd6998b8f0d4b6969f3147a19d7a1c93c2603f26cddc930b86a9e2e58ff34a3f | verbatim | 원문 바이트 일치 |
| src/protocol/gateway-contract.generated.ts | apps/shared/src/gateway-contract.generated.ts | c3c8f86a909e3a763af19b3d9af168018e88373fa38ba60b475b45c243d3aa9a | c3c8f86a909e3a763af19b3d9af168018e88373fa38ba60b475b45c243d3aa9a | verbatim | 원문 바이트 일치 |
| src/protocol/gateway-events.ts | apps/shared/src/gateway-events.ts | 0c44bbb5b2c76a5b355654438d0cb7a77bfe85b592b7fb89d17b3f25d271ed00 | 0c44bbb5b2c76a5b355654438d0cb7a77bfe85b592b7fb89d17b3f25d271ed00 | verbatim | 원문 바이트 일치 |
| src/protocol/index.ts | apps/shared/src/index.ts | cbfeb921966ce1558edccaab042a44075ee38e22174b66f1778dec99fad6744b | f6b79cc38892f7ad8dc30bcdea8f8bf9fbde99e24285f74cf1e77bf5d4950db2 | patched | 전송 export 문장만 원문에서 추출; PB:665,D03 |
| src/protocol/json-rpc-channel.test.ts | apps/shared/src/json-rpc-channel.test.ts | 5e9edfedb3c1a85201f638617b473466b72197b56320c5ccce5bc5d36cec9e0b | 5e9edfedb3c1a85201f638617b473466b72197b56320c5ccce5bc5d36cec9e0b | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-channel.ts | apps/shared/src/json-rpc-channel.ts | d47042e66521292781b4c46c1a5b6ff8171b41b82ec69db9e27e98faa9bcc34b | d47042e66521292781b4c46c1a5b6ff8171b41b82ec69db9e27e98faa9bcc34b | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-connect.test.ts | apps/shared/src/json-rpc-gateway-connect.test.ts | 1146b64f5c55c927e575ffa5f2d5e63f77bd09dbeeefc2353b18d607f7830c81 | 1146b64f5c55c927e575ffa5f2d5e63f77bd09dbeeefc2353b18d607f7830c81 | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-legacy-replay.test.ts | apps/shared/src/json-rpc-gateway-legacy-replay.test.ts | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-replay.test.ts | apps/shared/src/json-rpc-gateway-replay.test.ts | 8b09276d99f64868e21f3b92b1b532df72aee93acb4c066fab1845f934823e17 | 8b09276d99f64868e21f3b92b1b532df72aee93acb4c066fab1845f934823e17 | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway.ts | apps/shared/src/json-rpc-gateway.ts | c943f1fbc2d456e8d6c831596c17a11ae55a1edb1a0855bea052c57cc0d57bef | c943f1fbc2d456e8d6c831596c17a11ae55a1edb1a0855bea052c57cc0d57bef | verbatim | 원문 바이트 일치 |
| src/protocol/reconnect-backoff.test.ts | apps/shared/src/reconnect-backoff.test.ts | 3156b1815b8be9eedfa3c6d3c4800add121b731f02bf0796d34154971981f4ea | 3156b1815b8be9eedfa3c6d3c4800add121b731f02bf0796d34154971981f4ea | verbatim | 원문 바이트 일치 |
| src/protocol/reconnect-backoff.ts | apps/shared/src/reconnect-backoff.ts | 066a9efab5237e47b0b892ef72ec129485622a45ff8e1b964e278999c8466328 | 066a9efab5237e47b0b892ef72ec129485622a45ff8e1b964e278999c8466328 | verbatim | 원문 바이트 일치 |
| src/protocol/websocket-url.ts | apps/shared/src/websocket-url.ts | a4282b7d610d3c022a8dbcb77597b85497885eec606f6f09a354d373fea3d10c | a4282b7d610d3c022a8dbcb77597b85497885eec606f6f09a354d373fea3d10c | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-ambiguous-owner-reconcile.test.ts | apps/desktop/src/api/canonical-ambiguous-owner-reconcile.test.ts | 0cef10124aec94811a721ad83a2343dcbbb4e769ee542630945ebb8c4f6227bf | 0cef10124aec94811a721ad83a2343dcbbb4e769ee542630945ebb8c4f6227bf | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-deadline.test.ts | apps/desktop/src/api/canonical-deadline.test.ts | 0f1f57ee0fb48c8572fa2e40a4d5d31d5ebc17dd93ba43a166ff16f20efd1433 | 0f1f57ee0fb48c8572fa2e40a4d5d31d5ebc17dd93ba43a166ff16f20efd1433 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-live-pick.test.ts | apps/desktop/src/api/canonical-live-pick.test.ts | 044ab710f2e3b4758846a701480e04b437db55a2068db0cbef9fced7132f8a9c | 044ab710f2e3b4758846a701480e04b437db55a2068db0cbef9fced7132f8a9c | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-prompt-owner.test.ts | apps/desktop/src/api/canonical-prompt-owner.test.ts | f2e70d59246545fc746761388296099f1c92fef27ed963eb327b2a1d38c2f431 | f2e70d59246545fc746761388296099f1c92fef27ed963eb327b2a1d38c2f431 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-protocol.test.ts | apps/desktop/src/api/canonical-protocol.test.ts | 3f51f1154a44595d1346673806698bc4734c1cf2a73ba257affe88f42e40e8e3 | 3f51f1154a44595d1346673806698bc4734c1cf2a73ba257affe88f42e40e8e3 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-protocol.ts | apps/desktop/src/api/canonical-protocol.ts | 76406e7784afe5a72aefd5d9c523686bce2a48b1b922bcb9b6e7620ce9d4822f | 76406e7784afe5a72aefd5d9c523686bce2a48b1b922bcb9b6e7620ce9d4822f | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-retained-control-retire.test.ts | apps/desktop/src/api/canonical-retained-control-retire.test.ts | cac332c1bf3d8c09889e861f0b4a7e9cb8f4896204350f65f63f3e4d06383e41 | cac332c1bf3d8c09889e861f0b4a7e9cb8f4896204350f65f63f3e4d06383e41 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-retained-mutation-stale.test.ts | apps/desktop/src/api/canonical-retained-mutation-stale.test.ts | 5776b4a4dd366b14e85b60f26af616feaf289ce0b29d04b1088fa7e848ec1554 | 5776b4a4dd366b14e85b60f26af616feaf289ce0b29d04b1088fa7e848ec1554 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-rewind-lost-reply.test.ts | apps/desktop/src/api/canonical-rewind-lost-reply.test.ts | f0586c33ac8f5001936b9d4c034660e3e33774204a13aaa1a3b30221864e0d96 | f0586c33ac8f5001936b9d4c034660e3e33774204a13aaa1a3b30221864e0d96 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-session-updated-revision.test.ts | apps/desktop/src/api/canonical-session-updated-revision.test.ts | 5c2ff164b062c5988caa78f1c049eb80fb03549efb9bda4b748779833b060efa | 5c2ff164b062c5988caa78f1c049eb80fb03549efb9bda4b748779833b060efa | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-unknown-admission-fence.test.ts | apps/desktop/src/api/canonical-unknown-admission-fence.test.ts | da83f1e17ae4618e30831370cfe2dee7be6988881d1cecd135670692649f747c | da83f1e17ae4618e30831370cfe2dee7be6988881d1cecd135670692649f747c | verbatim | 원문 바이트 일치 |
| src/hermes/api/client.ts | apps/desktop/src/api/client.ts | b56abc62e90460a2b69c71fef6798cadff3050c9745a54f2468de3b73534ecc3 | fc28c5430d3396b6213c995382eabe3e7bccdaab40a480d048b1252e10e06608 | patched | 312-315 모드 명시 주입,520-550 생성자 socketFactory/옵션 주입,633/700 webApi로 교체; PB:414,D10,665 |
| src/hermes/app/shell/statusbar-controls.ts | apps/desktop/src/app/shell/statusbar-controls.tsx | 67e0dbb288eb88458d790f76d88b50d0a384b57b8e39bf5c5bf2b7c08fbea9fa | 57ce698bd70fc645f73ff441e37157b8c8c353bb1081737f83e16602d8bc740d | shim | 33-44 타입만 유지, TSX UI 미복사; PB:D03,D10 |
| src/hermes/global.d.ts | apps/desktop/src/global.d.ts | b39b1fff32ca48b957896774121af10874f2a639ab0bd7b842f45c5ce9c6035f | d74cf3e369cc31aa6a2c5c55122c0958786151f17b27932dfccfabfa7ee69193 | shim | 1016-1056,1553-1580 타입 발췌 및 URL bridge 타입만 선언; PB:414,D10 |
| src/hermes/hermes.ts | apps/desktop/src/hermes.ts | 87b6ae0fa05ea9d28b7c1556d7e9f438dd777d3c52594d0a49329d5204d29fad | da23965d77deb3d4a97ab0b2d0967ece29d44b8231addaa35c8645cb25d76da8 | patched | 20행 이후 도메인 API/type export 제거,1-19 유지; PB:665/8a 범위 |
| src/hermes/lib/execution-authority.frames.json | apps/desktop/src/lib/execution-authority.frames.json | e1c6bf9320849999e5fd0deb16dc67e535c1a671203ee0d3bb3bae7b1dcdcd8f | e1c6bf9320849999e5fd0deb16dc67e535c1a671203ee0d3bb3bae7b1dcdcd8f | verbatim | 원문 바이트 일치 |
| src/hermes/lib/execution-authority.test.ts | apps/desktop/src/lib/execution-authority.test.ts | a53eee220e2608994d079295a09b5df7c19b2595654e1389c5f14e0e0238a478 | a53eee220e2608994d079295a09b5df7c19b2595654e1389c5f14e0e0238a478 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/execution-authority.ts | apps/desktop/src/lib/execution-authority.ts | 438d22c51061ee135ffdafb0bef97cfee20cffa20d1479ea8d2d92e4b94bebce | 438d22c51061ee135ffdafb0bef97cfee20cffa20d1479ea8d2d92e4b94bebce | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-events.test.ts | apps/desktop/src/lib/gateway-events.test.ts | 15ccfe35afe3350c3ebb131dce699f61f0c2a094bec2a5acb4d416b838c36e45 | 15ccfe35afe3350c3ebb131dce699f61f0c2a094bec2a5acb4d416b838c36e45 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-events.ts | apps/desktop/src/lib/gateway-events.ts | 49d791c9a8067d8a22ed3f60b1c8e5c29b2ad34dc79f64073668a2d4d9ed70a8 | 49d791c9a8067d8a22ed3f60b1c8e5c29b2ad34dc79f64073668a2d4d9ed70a8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-liveness-policy.test.ts | apps/desktop/src/lib/gateway-liveness-policy.test.ts | 857710b5e230bc39280202d34b3de553cd47371d2d9ef0f46b02ee38acc49afb | 857710b5e230bc39280202d34b3de553cd47371d2d9ef0f46b02ee38acc49afb | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-liveness-policy.ts | apps/desktop/src/lib/gateway-liveness-policy.ts | ebfa0c34c5a55f9ae46f5672d78e1d5c31ea28a0ae7c57d0ac76c6712405e0c8 | ebfa0c34c5a55f9ae46f5672d78e1d5c31ea28a0ae7c57d0ac76c6712405e0c8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-rpc.test.ts | apps/desktop/src/lib/gateway-rpc.test.ts | b808af95b5ae21d3693b5a64e274b6e310fd191935e94a96a481913ec6b748e7 | b808af95b5ae21d3693b5a64e274b6e310fd191935e94a96a481913ec6b748e7 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-rpc.ts | apps/desktop/src/lib/gateway-rpc.ts | eabe8d8ac494748461910fffed53225881c235a97a77e389f43f07bfe1bea3cf | eabe8d8ac494748461910fffed53225881c235a97a77e389f43f07bfe1bea3cf | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-ws-url.test.ts | apps/desktop/src/lib/gateway-ws-url.test.ts | 2c83316ecbabddad5906b8f5d48285241c4d07a92303ae468d100d9d7daeb7e9 | 2c83316ecbabddad5906b8f5d48285241c4d07a92303ae468d100d9d7daeb7e9 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-ws-url.ts | apps/desktop/src/lib/gateway-ws-url.ts | 6e18b8d8974d6cb4004309d3acc6c445930b57336bcb2e62aa7f4587267e69a0 | 6e18b8d8974d6cb4004309d3acc6c445930b57336bcb2e62aa7f4587267e69a0 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-heartbeat.test.ts | apps/desktop/src/lib/json-rpc-gateway-heartbeat.test.ts | cfcc4ec4ea5b17e14691fb3f584496f1e547a349259c46afd5bf9b0364dd1f4b | cfcc4ec4ea5b17e14691fb3f584496f1e547a349259c46afd5bf9b0364dd1f4b | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-recovery.test.ts | apps/desktop/src/lib/json-rpc-gateway-recovery.test.ts | 694ea7e4aaa04080e0617e16612b9a4bcd337710e1ae7af3a0f7b84f6ea98a72 | 694ea7e4aaa04080e0617e16612b9a4bcd337710e1ae7af3a0f7b84f6ea98a72 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-url-guard.test.ts | apps/desktop/src/lib/json-rpc-gateway-url-guard.test.ts | a2ab7bc9d7f28472a649982f3094847eb5e52fd6d91e12bc59db6de2bfaf7619 | a2ab7bc9d7f28472a649982f3094847eb5e52fd6d91e12bc59db6de2bfaf7619 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/replay-gap-owner.test.ts | apps/desktop/src/lib/replay-gap-owner.test.ts | 72fadf493abd37ca49f7a5da0f405ac2c8f3f529a07683a806a10a77abd017a3 | 72fadf493abd37ca49f7a5da0f405ac2c8f3f529a07683a806a10a77abd017a3 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/replay-gap-owner.ts | apps/desktop/src/lib/replay-gap-owner.ts | 499814bad73d3577cd2700cb2670e06c3a6441eff8b90d89ea88ca526159c035 | 499814bad73d3577cd2700cb2670e06c3a6441eff8b90d89ea88ca526159c035 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-ids.test.ts | apps/desktop/src/lib/session-ids.test.ts | 8f56b9a9f424f69ca4c7513f9a7602a51ca021f4d77d714ced078866fbca9d49 | 8f56b9a9f424f69ca4c7513f9a7602a51ca021f4d77d714ced078866fbca9d49 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-ids.ts | apps/desktop/src/lib/session-ids.ts | e1c6ee880bf6249531ac1daa8e93f280ed077359b5d4f6fce78af0c01ea5b2f5 | e1c6ee880bf6249531ac1daa8e93f280ed077359b5d4f6fce78af0c01ea5b2f5 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/with-timeout.test.ts | apps/desktop/src/lib/with-timeout.test.ts | db31608fd17838c3370567b7d3beac0c66f71d443ff1dfab714a6a4eb850e295 | db31608fd17838c3370567b7d3beac0c66f71d443ff1dfab714a6a4eb850e295 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/with-timeout.ts | apps/desktop/src/lib/with-timeout.ts | 80a35ef6f95067a9cfbf707b3cf481244a6534654d1ca36a578292e3346039ea | 80a35ef6f95067a9cfbf707b3cf481244a6534654d1ca36a578292e3346039ea | verbatim | 원문 바이트 일치 |
| src/hermes/platform-bridge.ts | apps/desktop/src/api/client.ts | b56abc62e90460a2b69c71fef6798cadff3050c9745a54f2468de3b73534ecc3 | b151b6607a0abb19df8180597feed28d9792f37f7f9f7ee2d650eaff1ed0e6fa | shim | 633-639,700-702 API 호출을 명시적 설치 web host로 전달; PB:414,D10 |
| src/hermes/store/session-request-router.ts | apps/desktop/src/store/session-request-router.ts | 60bbb2a1e63405d95a8639f6b8d78c9a4fa50f97e1b749493bcf9a3f2dafb8f6 | 27b8c19ef6b529151ece17f449dc9ae7c3bfb962341cd5ac9f229df197f63c80 | shim | 21-31 소유자 타입만 유지, 실행 store 제외; PB:665/8b |
| src/platform/capability-gate.test.ts | 신규 (아래 설계 근거) | — | 3ea02243258cb7dfa45c3f87ac54a4e8614d2e576398fd6e3e769a342307f24e | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| src/platform/capability-gate.ts | 신규 (아래 설계 근거) | — | 266fa07fbb5c67b90ea5c77dd39a50bc151a83f2564f65748e5156c25085d521 | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| src/platform/fake-websocket.ts | apps/shared/src/json-rpc-gateway-legacy-replay.test.ts | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | 4ac6b30760340f81ef04cdd437b2a997febd11af279c411874eda7dca388bd16 | patched | 8-44 FakeWebSocket 클래스 원문 추출 + export; 원본 테스트 파일 무수정; PB:665 |
| src/platform/test-setup.ts | 신규 (아래 설계 근거) | — | d27a3be1f7e8bc0f7beed7f59ce3636f46da90a6c8e0efe06f1ec1584827c119 | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| src/platform/web-host.test.ts | 신규 (아래 설계 근거) | — | bccacdac3d05060e3926c89530599c91a8cb35aa2df5b7fb8e8bd9866567e78f | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| src/platform/web-host.ts | 신규 (아래 설계 근거) | — | 65208f07e5736e19056d8f3b9b65e7f92de97846bd600531fe3c55edf00eaa00 | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| src/platform/yield-manifest.generated.json | 신규 (아래 설계 근거) | — | 36d471357d53018f542e3f33c23cd5102cbc819547ac87e187d9d2143a42894e | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |
| vitest.config.ts | 신규 (아래 설계 근거) | — | 22238c79f874c514637318676cc2f147a26e81953acf9d8851fb14b3c2d04015 | new | 웹 경계/테스트/정적 표; PB:414,635,665,D10 |


# PR-8b store 및 실서버 통합 테스트 이식 (구현·로컬 검증 완료; 실서버 실행 대기)

## 실행 계획과 복사 전 closure 조사
- [x] 원본 gateway/session/event/pending import closure를 바이트 복사하고 웹 경계 최소 수정.
- [x] 비 React 순수 함수와 node 원본 테스트 보존; DOM/네이티브/UI closure 제외 기록.
- [x] REST manifest gate 및 store 수명 연결, 가짜 소켓으로 비구현 자동 송신 차단 검증.
- [x] 엔진 없는 Mongo/FastAPI 통합 헬퍼와 시나리오 작성 (실행은 사용자만).
- [x] 허용된 vitest/TypeScript/기존 테스트/빌드 및 Python 컴파일, 해시·제외·인계 기록.

사용자가 확정한 설계를 그대로 실행한다. 설치/커밋/체크아웃/실제 네트워크 테스트는 하지 않는다.
아래는 복사 전 직접 import 분석이다. 세부 전이 closure와 해시는 완료 표에 기록한다.

| 원본 묶음 | 필수/제외 | 이유/처리 |
|---|---|---|
| store/gateway.ts → shared, api/canonical-protocol, hermes/client, gateway-liveness-policy, gateway-rpc, with-timeout | 필수 | 8a 전송 위에 원본 registry·routing 유지. PB:665,676,D10 |
| gateway → notifications, i18n, identity-trace, local-gateway-ensure-failure, notify-baseline | 필수 경계 | 앞 세 개는 표시/네이티브 로그 shim, 뒤 둘은 순수 원본; 네이티브 자동 동작 없음 |
| gateway → session, session-event-provenance | 필수 | 연결 상태 및 신뢰 가능한 event owner 전달 |
| session → storage, connection-scoped, session-removal, session-unread-remote, types | 필수 | 원본 세션·owner·복원 상태 보존, REST는 gate 경유 |
| session-states → pane tree/preview/profile/window/metrics | 상태는 필수, UI 효과 제외 | 원본 세션별 격리 유지, 다중 pane/preview/native 부작용은 비활성 경계 (PB:680) |
| clarify → parked-card-answers/server-requests/session-states | 필수 | 대기 카드·답변·owner routing 보존 |
| pending-submissions, inflight-turn-journal, chat-runtime, chat-messages | 필수 | ACK 유실·메시지·복원 순수 로직 |
| use-message-stream/gateway-event 및 use-session-actions의 독립 순수 모듈 | 필수 | replay gap 및 이벤트 state 반영, 훅은 PR-9 |
| React hook 및 TSX, PromptOverlays/sudo/secret/vault, native 창·Electron | 제외 | PB:674,680 및 사용자 범위; 설치/복사 안 함 |

## 8b 최종 구현과 검증

- 원본 기준 5894e750 유지. gateway 2,484줄을 기반으로 명시 어댑터 교체와 secondary 차단만 적용했다. 기존 session-request-router의 8a 타입 shim은 원본 전체로 교체했다.
- 누락된 session-info/use-session-actions/utils 및 sessions API closure는 원본에서 추가 복사했다. 기존 테스트 파일은 수정하지 않았다.
- 신규 store-host는 React 없이 원본 event handlers, message callback 본문, 원본 session state를 연결하는 opt-in 호스트다. production window 전역을 만들거나 바꾸지 않는다.
- 원본 use-message-stream/index.ts는 React hook 전체로 가져오지 않았다. runtime.ts는 해당 파일 callback 본문의 발췌 patch이며 새 메시지 알고리즘이 아니다. React 수명·배치 타이머·UI refetch는 PR-9에서 원본 훅으로 복구할 경계다.
- verification-before-completion에 따라 실제 실행: vitest **88파일 / 1,036테스트 통과**, 기존 8a 182개 포함. 원본 테스트 바이트 비교도 수행했다.
- `npx --no-install tsc --noEmit` 통과(build에서도 실행). `npx --no-install tsc --noEmit -p tests-integration/tsconfig.json` 통과. 후자는 파일 검사뿐이며 globalSetup/서버를 실행하지 않는다.
- `npm test`: **10/10**. `npm run build -- --outDir /tmp/pr8b-build`: **통과**. 사용자 금지 디렉터리 dist를 보존하기 위해 출력 경로만 임시 디렉터리로 지정했다.
- `PYTHONPYCACHEPREFIX=/tmp/pr8b-pycache /Users/daehwankim/yield-agent/.venv/bin/python -m py_compile tests-integration/pr8_gateway_server.py`: 통과.
- 실서버 통합, 실제 소켓/네트워크/Mongo/모델, backend 기동, Python engine/socket 테스트는 **전부 미실행**. 통합 성공을 선언하지 않는다.
- `.env`, 08-YieldAgent/scripts 및 사용자 지정 보호 파일 변경 없음. 설치/커밋/push/stash/checkout/Docker 실행 없음. UI .tsx 복사 및 main.tsx 연결 없음.

### PR-8b 후속 fixture 수정 (2026-10-25)

- 사용자 로컬 Mongo 통합 실행 결과: 11개 중 8개 실패·3개 통과. 실패 a/b/c/d/e(2개)/h/i는 공유 `create()`의 고정 제목으로 인한 `Title is already in use`였다. 수정 후 통합 재실행은 사용자에게 남겨 두며 Codex는 실행하지 않았다.
- 선택: `create()`마다 `randomUUID()` 접미사를 붙여 사용자별 제목 고유성 계약을 만족하면서 명시 제목 경로의 검증을 유지한다(52자, 서버 제한 100자 이내).
- `gateway.itest.ts`, `global-setup.ts`, 실제 서버 fixture인 `pr8_gateway_server.py` 전체 점검: g의 title 생략은 `SessionRPC.create`에서 허용되며 세션 ID는 서버 UUID다. submission_id와 매 dial ticket은 이미 UUID이고 c의 재전송만 의도적으로 같은 submission_id를 유지한다. raw RPC id는 연결별 범위이며 서버 receipt/run/event ID를 재생성하지 않는다.
- DB 이름의 UUID 8자리 절단은 실행 간 DB 공유·teardown 충돌 가능성을 높이므로 전체 32자리 hex로 변경하고 globalSetup ready-record 검증도 일치시켰다.
- 정적 계약 근거: `harness/gateway/session_store.py`의 사용자별 title 고유 인덱스와 `set_session_title`, `session_rpc.py`의 create/snapshot/history, `execution.py`의 submit/receipt 멱등성, `events.py`의 replay/rebuild/verified_answer, `rest.py`의 응답 projection 및 `harness/store.py`의 고유 인덱스/event/finish를 대조했다. 이 범위에서 추가 fixture 불일치나 `src/hermes`·`src/protocol` 수정 필요 근거는 발견하지 못했다. 실시간 연결·Mongo 동작은 미검증이다.
- 후속 검증: `npx tsc --noEmit`, `npx tsc --noEmit -p tests-integration/tsconfig.json` 통과; `npx vitest run` 88파일/1,036개 통과; `npm test` 10개 통과; `npm run build -- --outDir /tmp/pr8b-followup-build` 통과(기존 dist 보존). Python 서버 fixture 실행·통합 테스트·커밋·push 없음.
- 변경 파일은 위 fixture 3개와 `tests-integration/README.md`, 이 문서뿐이다. 아래 최종 SHA-256 표의 변경된 4개 파일 행을 갱신했다.

### PR-8b 통합 테스트 2차 수정 (2026-10-25)

- 사용자 재실행 결과는 11개 중 10개 통과, c만 `-32005 handler_not_connected`. 기존 로그는 method/feature가 없어 실패 RPC를 직접 확정하지 못한다. 아래는 정적 원인 판단이며 수정 후 통합 실행 결과가 아니다.
- `src/hermes/api/client.ts`의 `connect()`는 attached 집합을 비운다. 이어지는 `prompt.submit`은 `attachForRequest()`에서 `session.resume({session_id, defer_history:true, omit_messages:true})`를 먼저 보낸다. `canonical-protocol.ts`는 defer_history를 제거하지 않는다. 서버 `harness/gateway/session_rpc.py:148-151`은 이 true 옵션을 명시적으로 `NotConnected`로 거절하며, `connection.py:292-293`에서 -32005로 변환한다. submit 재전송 자체에 앞선 숨은 resume가 가장 유력한 실패 지점이다.
- c에서 close 상태를 확인한 뒤 재연결 → 지원되는 기본 `session.resume({session_id})` 완료 → 같은 params/submission_id의 submit → finish → receipt 순서로 수정했다. wire 기록으로 resume/submit 순서, 추가 implicit resume 부재, defer_history 부재를 검증한다. 기존 receipt identity/terminal outcome/launch 1회 검증은 유지한다.
- `connection.py:251-262`와 `execution.py:132-185`는 raw submit에 별도 attach를 요구하지 않으며 저장된 동일 submission의 receipt를 반환한다. PR-7 `test_gateway_pr7b_mongo.py::test_ws_submit_push_ack_loss_reconnect_gap_and_publication`도 새 raw 소켓에서 resume 없이 동일 submit을 재전송하고 terminal receipt를 확인한다. 따라서 PR-7 raw 테스트와 Hermes 클라이언트의 자동 prerequisite는 구분해야 한다.
- 원본 흐름: `store/pending-submissions.ts`는 admission별 journal과 snapshot reconciliation이며 재전송 루프가 아니다. 원본 `/tmp/hermes-agent-ro/apps/desktop/src/app/session/hooks/use-prompt-actions/{submit,prepared-submissions,utils}.ts`는 명시 재시도 때 보관된 params/ID를 재사용한다. busy/not-found 재시도와 ACK 유실을 구분하며, ambiguous ACK에 identityless fallback을 하지 않는다. `lib/gateway-rpc.ts`는 오류 분류 헬퍼다. `store/gateway.ts`의 연결 bring-up은 gateway.connect를 호출하고, 실제 submit 전 attach는 위 api/client에서 수행한다. c는 명시 재시도 계약을 직접 행사하며 전체 UI 자동 복구를 증명하지 않는다.
- `pr8_gateway_server.py::ObserveWire`는 submit의 성공 result만 유실시키고 1012로 소켓을 닫는다. admission 처리 전 실패를 만드는 fixture가 아니다. finish는 durable run을 완료한 뒤 events.ingest를 수행하고, `execution.receipt()`는 최신 run 상태로 terminal receipt를 만든다. authority manifest에서 submit/receipt/resume는 implemented다. 이 경로에서 ACK-loss 제어·finish/receipt 순서·manifest를 바꿀 근거는 없다.
- `gateway.itest.ts` 내부 request 헬퍼는 c의 create/submit/resume/receipt 오류에 method, code, data.reason, data.feature를 추가하고 원래 오류를 cause로 보존한다. params/ticket/전체 data는 진단 문자열에 넣지 않는다. 내부 prerequisite 오류라면 method는 요청한 외부 호출, feature는 서버가 보고한 실패 RPC를 나타낸다.
- 남은 한계: 기본 자동 attach의 defer_history 호환성 차이는 그대로다. 이번 변경은 지원되는 snapshot 호출을 쓰는 통합 시나리오 수정이며 백엔드 버그 수정 또는 Hermes 자동 재연결 전체 통과로 해석하지 않는다. 서버나 원본 수정이 필요하다고 단정할 근거는 없으며, 옵션 지원 범위는 별도 결정 대상이다.
- 검증: `npx tsc --noEmit -p tests-integration/tsconfig.json` 통과; `npx vitest run` 88파일/1,036개 통과; `npm test` 10개 통과; `npm run build -- --outDir /tmp/pr8b-second-followup-build` (tsc --noEmit 포함) 통과. 통합 테스트·Python fixture 실행 없음. 변경은 `tests-integration/gateway.itest.ts`, `tests-integration/README.md`, 이 문서뿐이며 커밋·push 없음. 아래 최종 표의 테스트 및 `tests-integration/README.md` 해시를 갱신했다. 보호 대상 `harness_console/README.md`에 잘못 삽입했던 섹션은 제거하여 원본으로 복원했다(MD5: `6f4f4ebd46d05cf939026e7a6bd16195`). 이 문서는 자기참조 해시를 포함하지 않는다.

### 8a 파일 변경과 이유
| 파일 | 8b 변경 이유 |
|---|---|
| src/hermes/hermes.ts, src/protocol/index.ts | 필요한 원본 sessions/shared export만 추가 |
| src/hermes/platform-bridge.ts, global.d.ts | store descriptor 및 선택적 native 경계 타입. 실제 WebHost는 native capability 미제공 |
| src/hermes/store/session-request-router.ts | 8a 타입 shim을 원본 store로 대체 |
| src/platform/web-host.ts | 명시 basePath, desktop bridge 설치/해제, api의 REST gate |
| src/platform/capability-gate.ts, yield-manifest.generated.json | manifest routes 투영, HTTP 메서드/경로 기준 로컬 차단 |
| vitest.config.ts | 원본 Node 테스트의 storage/event fixture 설정 추가; integration include는 별도 |
| package.json | test:integration script만 추가; 의존성 변경 없음 |

### 자동 호출 조사 추가분
| 경로 | 원본 효과 | 8b 처리/검증 |
|---|---|---|
| store/gateway.ts:471-491,1045,1128 | descriptor/secondary resolve·재연결 | explicit bridge; 웹 secondary capability false. fixture에서만 원본 multi-profile 테스트 허용 |
| store/session.ts:353,376 | native 기본 폴더/경로 정리 | optional bridge, 실제 WebHost 미제공 → early return |
| store/session-states, session-unread | 구독 및 세션별 복원·읽음 watermark | 원본 유지. window는 원본의 별도 native browser 창이 아니므로 false. 네트워크는 gate 뒤 |
| store/profile/projects/preview/windows/onboarding/pet | hydrate, polling, IPC, 창/모델/프로젝트 갱신 | 같은 경로 shim으로 효과 비활성 |
| store/session-control.ts, composer-status.ts, goals.ts | 명시 refresh가 요청하는 세션 제어/프로세스 조회 | import로 시작하지 않음. 호출 시 원본 gateway의 public+wire gate가 미지원 요청 차단 |
| store/prompts.ts | approval 및 native credential pending | approval 원문 유지; native 5종 setter는 거절. PromptOverlays 미이식 |
| platform/store-host.ts | 이벤트·state 구독, gap 복원 | 명시 install만 활성화. gap의 원본 owner 검사 → resume 응답 → pending replay boundary → REST messages |
| platform/store-host.test.ts | 설치·연결·60초 타이머·재연결 | 두 연결 모두 비구현 RPC 송신 0, REST fetch 0; gap 시 응답 전 이력 fetch 0 확인 |
| platform/rest-capability.test.ts | manifest 모든 non-implemented REST | method/path/query 구조 검사, fetch 0; 자연어 routing 규칙 아님 |

### 통합 테스트 시나리오 (작성·정적 검사 완료, 실행 대기)
| 항목 | 실제 검증 대상 |
|---|---|
| a | authority/legacy 명시 일치 연결, 불일치 요청 거절 및 mount/mode fallback 없음 |
| b | session.create → prompt.submit → verified 완료 이벤트 → 원본 store 메시지 |
| c | 서버 처리 후 ACK만 유실·연결 종료 → 같은 submission_id 재전송 → launch 1회·receipt 식별자 보존 |
| d | 연결 종료 중 완료한 이벤트 → 재접속 replay → seq 단조·중복 없음·store 복구 |
| e (2개) | truncation/epoch 변경 → 원본 gap 처리 → resume → REST messages 순서와 store 최종 이력 |
| f | 모든 비구현 RPC의 client/server 송신 기록 증가 0, REST fetch 0. 원시 소켓 -32004/-32601 및 미연결 -32005 구분 |
| g | 다른 user의 replay/receipt/resume/messages 접근 차단 |
| h | legacy submit은 -32004, launch 없음 |
| i | 실제 GET sessions/messages/health/status/profiles active 계약 |
| j | 쿼리 없음/빈 user/허용하지 않은 user: HTTP 401 및 WS upgrade 거절 |

서버 helper는 PR-7의 OfflineController lease 획득/launch 계수와 verified candidate/finish 방식을 사용한다.
127.0.0.1 임시 포트, UUID 기반 `zz_pr8_<uuid32>` DB, stdout은 port/db JSON 한 줄이다.
URI는 `HARNESS_MONGO_URI`에서만 읽고 출력하지 않는다. globalSetup은 stderr를 전달하지 않는다.
EOF 또는 SIGTERM의 graceful shutdown에서 lifespan finally로 DB를 drop한다. 강제 SIGKILL/머신 중단은 finally 보장 범위 밖이다.
`/__test/control`, `/__test/state`는 루프백에서만 허용한다. trace는 메서드 이름/경로만 기록하며 본문·자격정보를 기록하지 않는다.

실행(사용자가 샌드박스 밖에서):
```sh
cd /Users/daehwankim/yield-agent/08-YieldAgent/harness_console
# HARNESS_MONGO_URI는 기존 안전한 환경 주입 방식으로 설정한다. 값은 출력하지 않는다.
export PR8_PYTHON=/Users/daehwankim/yield-agent/.venv/bin/python
npm run test:integration
```
PR8_PYTHON 생략 시 repo `.venv/bin/python` (`../../.venv/bin/python`)이다. Node 내장 WebSocket/fetch 필요.
기본 vitest는 src의 *.test.ts만 수집하며 tests-integration/**/*.itest.ts 및 globalSetup을 실행하지 않는다.

### 모호함·남은 결정
- Q1 운영 인증은 미정이다. 테스트 resolver의 alice/bob 쿼리와 ticket/origin 정책은 운영 구현이 아니다.
- 서버에 mode 광고 handshake가 없다. legacy→authority는 capability 요청 실패, authority→legacy는 authority 작업 실패로 확인한다. socket open만으로 모드 일치를 주장하지 않는다.
- 8a 기본 legacy를 유지했다. 배포 시 authority mount와 rpcMode를 명시적으로 함께 선택해야 한다.
- manifest implemented와 실제 handler 연결은 다르다. 예: config.get은 알려진 implemented지만 서버 -32005, /api/config는 503. 비구현 분류만 로컬 gate가 차단하며 가짜 성공은 만들지 않는다.
- replay 보존량/기간/epoch 운영 정책은 백엔드 소유다. 테스트 control은 해당 프로세스의 한도만 바꾸며 영구 정책을 정하지 않는다.
- 여러 profile/native 창/모델 설정/secret/vault/온보딩/preview/폰트/UI·main opt-in은 PR-9. 단일 웹 primary에서 원본 세션별 격리와 owner/replay 계약을 유지한다.
- 실서버 결과가 도착하기 전 네트워크·Mongo 동작 완료 여부는 미확인이다.

## 최종 파일 집계 (8a 포함 현재 이식 트리)

상태: new 21, patched 20, shim 37, verbatim 188. 설정(package/tsconfig) 2개는 Hermes 원본 외 patched로 분류한다.

| 디렉터리 | 파일 | 줄 수 |
|---|---:|---:|
| src/hermes | 222 | 55264 |
| src/protocol | 20 | 11218 |
| src/platform | 11 | 6814 |
| tests-integration | 8 | 961 |
| 설정 | 5 | 85 |

### 8b에서 추가한 파일 집계 (8a 표와 비교)

| 디렉터리 | 추가 파일 | 줄 수 |
|---|---:|---:|
| src/hermes | 185 | 49908 |
| src/protocol | 6 | 554 |
| src/platform | 4 | 247 |
| tests-integration | 8 | 961 |
| 설정 | 2 | 35 |

총 추가 205파일. PR8_PORTING.md 갱신은 별도이며, 위 설정 2개는 vitest.integration.config.ts와 vitest.real-model.config.ts다. 기존 8a 파일 변경은 위 9개와 package.json script 추가다. tsconfig paths는 8a 설정을 그대로 사용한다. 원본 테스트 84파일 모두 원본 SHA-256과 일치하며, 신규 platform 4파일을 합쳐 vitest 88파일이다.

## 8b 최종 import closure (직접 의존, 순수/경계 구분)

원본 closure와 현재 파일을 함께 확인했다. 아래 @/는 기존 8a alias로 유지한다. 표의 shim 의존은 원본 UI/native 구현 대신 같은 경로의 비활성 경계다. 상단 복사 전 조사표와 아래 제외 표를 함께 읽는다.

| 이식 모듈 | 처리 | 직접 import |
|---|---|---|
| src/hermes/api/canonical-protocol.ts | 필수 원본 | 없음 |
| src/hermes/api/client.ts | 필수 경계/patched | @hermes/shared, nanostores, @/global, ../platform-bridge, ./canonical-protocol |
| src/hermes/api/sessions.ts | 필수 경계/patched | @/lib/gateway-rpc, @/lib/legacy-session-owner-backfill, @/lib/session-owner-stamp, @/store/transcript-tail, @/types/hermes, @hermes/shared, ./client, @/store/connection-registry-state |
| src/hermes/app/chat/composer/types.ts | 필수 경계/shim | 없음 |
| src/hermes/app/chat/sidebar/projects/workspace-groups.ts | 필수 경계/shim | @/types/hermes |
| src/hermes/app/chat/thread-loading.ts | 필수 원본 | @/lib/chat-messages |
| src/hermes/app/contrib/wiring-routing.ts | 필수 원본 | @/store/session-request-router |
| src/hermes/app/open-session.ts | 필수 원본 | @/contrib/types, @/store/session, @/store/session-request-router, @/store/session-states, @/store/windows, ./routes |
| src/hermes/app/routes.ts | 필수 경계/shim | nanostores |
| src/hermes/app/session/hooks/use-message-stream/collapse-duplicate-final.ts | 필수 원본 | @/lib/chat-messages |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts | 필수 경계/patched | @hermes/shared, @/app/shell/hooks/use-context-breakdown, @/lib/replay-gap-owner, @/store/clarify, @/store/live-sync, @/store/prompts, @/store/runtime-gone, @/store/session, @/store/session-request-router, @/store/session-states, @/themes/backend-sync, ./types |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/message-stream.ts | 필수 원본 | @hermes/shared, @/components/chat/vibe-hearts, @/i18n, @/lib/chat-messages, @/lib/chat-runtime, @/lib/completion-sound, @/lib/error-surface, @/lib/haptics, @/store/billing-block, @/store/clarify, @/store/compaction, @/store/free-tier-sign-in, @/store/notifications, @/store/pet, @/store/prompts, @/store/provider-wait, @/store/session, @/store/session-control, @/store/session-focus, @/store/session-states, @/store/session-unread, @/store/subagents, @/store/todos, ./types |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/session-info.ts | 필수 원본 | @hermes/shared, @/lib/chat-messages, @/lib/chat-runtime, @/lib/model-options, @/store/approval-mode, @/store/clarify, @/store/compaction, @/store/onboarding, @/store/pending-submissions, @/store/projects, @/store/prompts, @/store/session, @/store/updates, ../utils, ./types |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/types.ts | 필수 경계/patched | @hermes/shared, @/lib/chat-messages, @/lib/error-surface, ../../../../types |
| src/hermes/app/session/hooks/use-message-stream/interrupted-reply.ts | 필수 원본 | @/lib/chat-messages, @/lib/generated-images |
| src/hermes/app/session/hooks/use-message-stream/receipt-user-rows.ts | 필수 원본 | @hermes/shared, @/app/session/hooks/use-session-actions/pending-turn-identity, @/lib/chat-messages |
| src/hermes/app/session/hooks/use-message-stream/response-parts.ts | 필수 원본 | @/lib/chat-messages |
| src/hermes/app/session/hooks/use-message-stream/runtime.ts | 필수 경계/patched | @hermes/shared, @/i18n, @/store/native-notifications, @/store/notifications, @/store/session-sync, @/store/transcript-sync, @/lib/chat-messages, @/lib/error-surface, @/lib/generated-images, @/lib/todos, @/lib/turn-done-notification-body, @/store/gateway, @/store/subagents, @/store/todos, ../../../types, ./collapse-duplicate-final, ./interrupted-reply, ./receipt-user-rows, ./response-parts, ./utils, ./gateway-event/types |
| src/hermes/app/session/hooks/use-message-stream/utils.ts | 필수 원본 | @/lib/chat-messages, @/lib/chat-runtime, @/lib/todos, ../../../types |
| src/hermes/app/session/hooks/use-session-actions/pending-turn-identity.ts | 필수 원본 | @/lib/chat-messages, @/lib/spoken-reply |
| src/hermes/app/session/hooks/use-session-actions/resume-verdicts.ts | 필수 원본 | 없음 |
| src/hermes/app/session/hooks/use-session-actions/runtime-owner.ts | 필수 원본 | @/store/approval-mode, @/store/gateway, @/store/profile, @/store/session-request-router, @/types/hermes |
| src/hermes/app/session/hooks/use-session-actions/utils.ts | 필수 원본 | @hermes/shared, @/app/contrib/wiring-routing, @/components/assistant-ui/reference-kinds, @/hermes, @/lib/chat-messages, @/lib/chat-runtime, @/lib/embedded-images, @/lib/error-surface, @/lib/session-source, @/lib/spoken-reply, @/store/onboarding, @/store/pending-submissions, @/store/profile, @/store/projects, @/store/session, @/store/session-removal, @/store/session-request-router, @/store/session-states, @/store/updates, @/types/hermes, ../../../types, ./pending-turn-identity, ./runtime-owner, ./resume-verdicts |
| src/hermes/app/shell/hooks/use-context-breakdown.ts | 필수 경계/shim | 없음 |
| src/hermes/app/shell/statusbar-controls.ts | 필수 경계/shim | react |
| src/hermes/app/types.ts | 필수 원본 | react, @/lib/chat-messages, @/store/interface-mode, @/types/hermes |
| src/hermes/components/assistant-ui/directive-text.ts | 필수 경계/shim | 없음 |
| src/hermes/components/assistant-ui/reference-kinds.ts | 필수 원본 | 없음 |
| src/hermes/components/chat/vibe-hearts.ts | 필수 경계/shim | 없음 |
| src/hermes/components/pane-shell/tree/model.ts | 필수 원본 | 없음 |
| src/hermes/components/pane-shell/tree/store.ts | 필수 경계/shim | nanostores, ./model |
| src/hermes/components/pane-shell/workspace-scope.ts | 필수 경계/shim | nanostores, @/contrib/types |
| src/hermes/contrib/types.ts | 필수 경계/shim | 없음 |
| src/hermes/global.d.ts | 필수 경계/shim | ./platform-bridge |
| src/hermes/hermes.ts | 필수 경계/patched | ./api/client, ./api/sessions |
| src/hermes/i18n/index.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/chat-messages/attachment-turn.ts | 필수 원본 | @/lib/chat-messages, @/lib/chat-messages/parts |
| src/hermes/lib/chat-messages/coverage.ts | 필수 원본 | ./parts, ./types |
| src/hermes/lib/chat-messages/hydration.ts | 필수 원본 | @hermes/shared, @/components/assistant-ui/directive-text, @/components/assistant-ui/reference-kinds, @/lib/embedded-images, @/lib/error-surface, @/lib/generated-images, @/lib/todos, @/types/hermes, ./parts, ./tool-parts, ./types |
| src/hermes/lib/chat-messages/index.ts | 필수 원본 | ./attachment-turn, ./hydration, ./parts, ./reconciliation, ./tool-parts, ./types |
| src/hermes/lib/chat-messages/parts.ts | 필수 원본 | @/lib/media, ./types |
| src/hermes/lib/chat-messages/reconciliation.ts | 필수 원본 | ./attachment-turn, ./parts, ./types |
| src/hermes/lib/chat-messages/tool-parts.ts | 필수 원본 | @hermes/shared, @/lib/connector-tools, @/lib/text, @/lib/todos, @/lib/tool-result-metadata, @/types/hermes, ./types |
| src/hermes/lib/chat-messages/types.ts | 필수 경계/patched | @/lib/message-types, @hermes/shared, @/lib/error-surface, @/lib/tool-result-metadata, @/types/hermes |
| src/hermes/lib/chat-runtime.ts | 필수 경계/patched | @/lib/message-types, @hermes/shared, @/app/chat/composer/types, @/app/types, @/components/assistant-ui/directive-text, @/lib/chat-messages, @/lib/personalities, @/store/composer, @/types/hermes |
| src/hermes/lib/completion-sound.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/connection-scoped.ts | 필수 원본 | nanostores, ./persisted, ./storage |
| src/hermes/lib/connector-tools.ts | 필수 경계/patched | @/lib/message-types, @hermes/shared |
| src/hermes/lib/draft-title.ts | 필수 원본 | @hermes/shared |
| src/hermes/lib/embedded-images.ts | 필수 원본 | 없음 |
| src/hermes/lib/error-surface.ts | 필수 원본 | 없음 |
| src/hermes/lib/execution-authority.ts | 필수 원본 | 없음 |
| src/hermes/lib/gateway-events.ts | 필수 원본 | @/app/shell/statusbar-controls |
| src/hermes/lib/gateway-liveness-policy.ts | 필수 원본 | 없음 |
| src/hermes/lib/gateway-rpc.ts | 필수 원본 | @hermes/shared |
| src/hermes/lib/gateway-ws-url.ts | 필수 원본 | @hermes/shared, @/global |
| src/hermes/lib/generated-images.ts | 필수 원본 | @/lib/media |
| src/hermes/lib/haptics.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/identity-trace.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/inflight-turn-journal.ts | 필수 원본 | @/lib/chat-messages, @/lib/chat-messages/coverage, @/lib/spoken-reply |
| src/hermes/lib/keyed-timeouts.ts | 필수 원본 | 없음 |
| src/hermes/lib/legacy-session-owner-backfill.ts | 필수 원본 | @/api/client, @/lib/gateway-rpc, @/lib/session-owner-stamp, @/store/connection-registry-state |
| src/hermes/lib/local-gateway-ensure-failure.ts | 필수 원본 | 없음 |
| src/hermes/lib/lru-cache.ts | 필수 원본 | 없음 |
| src/hermes/lib/media.ts | 필수 경계/shim | @/lib/text |
| src/hermes/lib/message-types.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/model-options.ts | 필수 경계/shim | 없음 |
| src/hermes/lib/persisted.ts | 필수 원본 | nanostores, ./storage |
| src/hermes/lib/personalities.ts | 필수 원본 | 없음 |
| src/hermes/lib/replay-gap-owner.ts | 필수 원본 | @hermes/shared, @/store/session-request-router |
| src/hermes/lib/session-ids.ts | 필수 원본 | 없음 |
| src/hermes/lib/session-owner-stamp.ts | 필수 원본 | @/types/hermes |
| src/hermes/lib/session-source.ts | 필수 원본 | @/lib/text |
| src/hermes/lib/session-timer-since.ts | 필수 원본 | 없음 |
| src/hermes/lib/spoken-reply.ts | 필수 원본 | 없음 |
| src/hermes/lib/stable-array.ts | 필수 원본 | 없음 |
| src/hermes/lib/storage.ts | 필수 원본 | 없음 |
| src/hermes/lib/text.ts | 필수 원본 | 없음 |
| src/hermes/lib/todos.ts | 필수 원본 | 없음 |
| src/hermes/lib/tool-result-metadata.ts | 필수 원본 | 없음 |
| src/hermes/lib/turn-done-notification-body.ts | 필수 원본 | 없음 |
| src/hermes/lib/with-timeout.ts | 필수 원본 | 없음 |
| src/hermes/platform-bridge.ts | 필수 경계/shim | ./global, @hermes/shared |
| src/hermes/store/approval-mode.ts | 필수 원본 | nanostores |
| src/hermes/store/billing-block.ts | 필수 경계/shim | 없음 |
| src/hermes/store/clarify.ts | 필수 원본 | nanostores, @/i18n, ./gateway, ./notifications, ./parked-card-answers, ./server-requests, ./session, ./session-states |
| src/hermes/store/compaction.ts | 필수 원본 | nanostores |
| src/hermes/store/composer-queue.ts | 필수 경계/patched | @hermes/shared, nanostores, ./composer, ./session, ./session-states |
| src/hermes/store/composer-status-drawer.ts | 필수 원본 | @/lib/persisted |
| src/hermes/store/composer-status.ts | 필수 원본 | nanostores, @/i18n, @/lib/stable-array, @/lib/todos, ./gateway, ./goals, ./native-notifications, ./notifications, ./runtime-gone, ./session, ./session-gone-latch, ./session-states, ./subagents, ./todos |
| src/hermes/store/composer.ts | 필수 원본 | nanostores, @/lib/draft-title, @/lib/haptics, @/lib/storage, ./desktop-metrics |
| src/hermes/store/connection-registry-state.ts | 필수 경계/patched | nanostores, @/global |
| src/hermes/store/desktop-metrics.ts | 필수 경계/shim | 없음 |
| src/hermes/store/free-tier-sign-in.ts | 필수 경계/shim | 없음 |
| src/hermes/store/gateway-reconnect.ts | 필수 원본 | @/i18n |
| src/hermes/store/gateway.ts | 필수 경계/patched | @/platform-bridge, @hermes/shared, nanostores, @/api/canonical-protocol, @/global, @/hermes, @/i18n, @/lib/gateway-liveness-policy, @/lib/gateway-rpc, @/lib/identity-trace, @/lib/local-gateway-ensure-failure, @/lib/with-timeout, @/store/notifications, @/store/notify-baseline, @/store/session, @/store/session-event-provenance, @/store/session-states, @/lib/execution-authority |
| src/hermes/store/goals.ts | 필수 원본 | nanostores, @/lib/keyed-timeouts, ./gateway, ./runtime-gone, ./session-gone-latch, ./session-states |
| src/hermes/store/interface-mode.ts | 필수 경계/shim | 없음 |
| src/hermes/store/live-sync.ts | 필수 원본 | nanostores |
| src/hermes/store/native-notifications.ts | 필수 경계/shim | 없음 |
| src/hermes/store/notifications.ts | 필수 경계/patched | nanostores, @/i18n, @/lib/gateway-rpc, @/lib/with-timeout, @/store/desktop-metrics, @/store/recovery-requests, @/store/updates |
| src/hermes/store/notify-baseline.ts | 필수 원본 | 없음 |
| src/hermes/store/onboarding-session.ts | 필수 경계/shim | nanostores |
| src/hermes/store/onboarding.ts | 필수 경계/shim | 없음 |
| src/hermes/store/parked-card-answers.ts | 필수 원본 | nanostores, ./onboarding-session |
| src/hermes/store/pending-submissions.ts | 필수 원본 | ./composer-queue, ./session |
| src/hermes/store/pet.ts | 필수 경계/shim | 없음 |
| src/hermes/store/preview-ownership.ts | 필수 경계/shim | 없음 |
| src/hermes/store/preview-status.ts | 필수 경계/shim | 없음 |
| src/hermes/store/preview.ts | 필수 경계/shim | 없음 |
| src/hermes/store/profile.ts | 필수 경계/shim | nanostores |
| src/hermes/store/project-tree.ts | 필수 원본 | nanostores, @/app/chat/sidebar/projects/workspace-groups |
| src/hermes/store/projects.ts | 필수 경계/shim | ./project-tree |
| src/hermes/store/prompts.ts | 필수 경계/patched | nanostores, ./clarify, ./runtime-gone, ./server-requests, ./session, ./session-gone-latch, ./session-states |
| src/hermes/store/provider-wait.ts | 필수 원본 | nanostores |
| src/hermes/store/recovery-requests.ts | 필수 원본 | nanostores |
| src/hermes/store/runtime-gone.ts | 필수 원본 | ./session, ./session-gone-latch, ./session-states |
| src/hermes/store/server-requests.ts | 필수 원본 | @hermes/shared |
| src/hermes/store/session-control.ts | 필수 원본 | @hermes/shared, nanostores, ./gateway, ./goals, ./runtime-gone, ./session-gone-latch, ./session-states |
| src/hermes/store/session-event-provenance.ts | 필수 원본 | @hermes/shared |
| src/hermes/store/session-focus.ts | 필수 원본 | nanostores, @/components/pane-shell/tree/model, @/components/pane-shell/tree/store, @/components/pane-shell/workspace-scope, ./session |
| src/hermes/store/session-gone-latch.ts | 필수 원본 | @hermes/shared |
| src/hermes/store/session-no-reply.ts | 필수 원본 | @/lib/chat-messages, @/lib/error-surface |
| src/hermes/store/session-owner-holds.ts | 필수 원본 | @hermes/shared, nanostores, ./profile, ./session-request-router |
| src/hermes/store/session-owner-resolution.ts | 필수 원본 | ./connection-registry-state, ./profile, ./session-request-router |
| src/hermes/store/session-removal.ts | 필수 원본 | nanostores |
| src/hermes/store/session-request-router.ts | 필수 원본 | @/hermes, @/store/gateway, ./session-gone-latch |
| src/hermes/store/session-states.ts | 필수 원본 | @hermes/shared, nanostores, @/api/client, @/app/routes, @/app/types, @/components/pane-shell/tree/model, @/components/pane-shell/tree/store, @/components/pane-shell/workspace-scope, @/contrib/types, @/lib/chat-messages, @/lib/session-timer-since, @/lib/stable-array, @/lib/storage, @/types/hermes, ./composer-status-drawer, ./connection-registry-state, ./desktop-metrics, ./gateway, ./preview, ./preview-ownership, ./preview-status, ./profile, ./project-tree, ./provider-wait, ./session, ./session-event-provenance, ./session-focus, ./session-no-reply, ./session-owner-resolution, ./session-request-router, ./session-unread, ./transcript-tail-cache, ./windows, ./session-owner-holds |
| src/hermes/store/session-sync.ts | 필수 경계/shim | 없음 |
| src/hermes/store/session-unread-remote.ts | 필수 원본 | nanostores, @/hermes, ./session |
| src/hermes/store/session-unread.ts | 필수 원본 | @/api/canonical-protocol, @/lib/persisted, @/lib/session-source, @/lib/stable-array, @/lib/storage, @/store/profile, @/types/hermes, ./project-tree, ./session, ./windows |
| src/hermes/store/session.ts | 필수 경계/patched | @/platform-bridge, @hermes/shared, nanostores, @/api/client, @/app/chat/thread-loading, @/app/types, @/global, @/lib/chat-messages, @/lib/connection-scoped, @/lib/session-source, @/lib/session-timer-since, @/lib/storage, @/types/hermes, ./session-removal, ./session-request-router, ./session-unread-remote, ./composer |
| src/hermes/store/stream-delta-flush.ts | 필수 원본 | 없음 |
| src/hermes/store/subagents.ts | 필수 원본 | nanostores, @/lib/text |
| src/hermes/store/todos.ts | 필수 원본 | nanostores, @/lib/keyed-timeouts, @/lib/stable-array, @/lib/todos, ./session, ./session-states |
| src/hermes/store/transcript-sync.ts | 필수 경계/shim | 없음 |
| src/hermes/store/transcript-tail-cache.ts | 필수 원본 | @/lib/chat-messages |
| src/hermes/store/transcript-tail.ts | 필수 원본 | nanostores, @/types/hermes |
| src/hermes/store/updates.ts | 필수 경계/shim | 없음 |
| src/hermes/store/windows.ts | 필수 경계/shim | 없음 |
| src/hermes/test/deferred.ts | 필수 원본 | 없음 |
| src/hermes/test/session-info.ts | 필수 원본 | @/types/hermes |
| src/hermes/themes/backend-sync.ts | 필수 경계/shim | 없음 |
| src/hermes/types/hermes.ts | 필수 원본 | @hermes/shared, @/lib/tool-result-metadata |

## 제외 테스트 전체 목록 (현재 production closure에 대응하는 미복사 테스트 및 React 훅 테스트)

테스트를 부분 삭제하거나 기대값을 바꾸지 않았다. DOM/React와 비활성 native/UI 계약 테스트를 구분한다. 데이터-only 핵심은 원본 pending/session/router 테스트와 신규 platform 테스트로 검증한다.

| 원본 테스트 | 제외 이유 / 후속 |
|---|---|
| apps/desktop/src/app/chat/sidebar/projects/workspace-groups.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/app/gateway/hooks/canonical-owner-routing.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/gateway/hooks/use-connections-registry.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/gateway/hooks/use-gateway-boot-profile-pick.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/gateway/hooks/use-gateway-boot-update-hold.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/gateway/hooks/use-gateway-boot.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/open-session.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/app/routes.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/app/session/hooks/use-message-stream/agent-init-error.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/approval-mode-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/background-complete-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/background-review-interrupt.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/btw-complete-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/clarify-hydration.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/compaction-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/completion-boundaries.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/completion-projections.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/composer-model-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/delta-flush.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/duplicate-background-reply.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/duplicate-completion.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/duplicate-delivery-120104.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/execution-authority.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/folded-segment-refresh.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/lifecycle.test.ts | DOM/React 렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/heartbeat-complete-reorder.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interim-history.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interim-redelivery-duplicate.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interim-redelivery.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interim-sealing.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interrupted-reply.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/interrupted-status-sync.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/late-tool-events.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/moa-progress-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/moa-reference-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/peer-transcript-notify.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/pet-tool-failure-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/provider-wait-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/replay-gap.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/response-occurrences.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/reused-final-after-tool.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/session-info-side-effects.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/session-reclaimed.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/stale-pending-settle.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/steer-arrival-order.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/stream-flush.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/terminal-error-frame.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/timeline-events.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/todo-cleanup.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/tool-drafting-event.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/transformed-final.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-message-stream/usage.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/browser-manage.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/index.deep-cut.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/index.sleep-wake.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/owner-forward.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submission-windows.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/queued-first-input.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/slash-desktop-commands.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/slash-session-commands.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/steering-recovery.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/steering-undelivered-refusal.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/submission-destination.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/submit-unknown-receipt.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-prompt-actions/submit-voice-stopped.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/ambient-local-owner.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/branch-approval-owner.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/pinned-session-removal.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/remove-archived-session.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/unarchive-session.test.tsx | DOM/React 훅·렌더링 의존 → PR-9 |
| apps/desktop/src/app/session/hooks/use-session-actions/utils.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/app/shell/hooks/use-context-breakdown.test.ts | DOM/React 렌더링 의존 → PR-9 |
| apps/desktop/src/components/assistant-ui/directive-text.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/components/chat/vibe-hearts.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/components/pane-shell/workspace-scope.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/hermes.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/lib/completion-sound.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/lib/error-surface.test.ts | 미이식 i18n/en·error-surface-copy 표시 계약 → PR-9 |
| apps/desktop/src/lib/model-options.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/billing-block.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/composer-status.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/desktop-metrics.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/free-tier-sign-in.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/interface-mode.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/native-notifications.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/notifications.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/onboarding.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/parked-card-answers.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/pet.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/preview-status.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/preview.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/profile.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/projects.test.ts | DOM/React 렌더링 의존 → PR-9 |
| apps/desktop/src/store/prompts.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/session-owner-resolution.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/session-states.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/updates.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/store/windows.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |
| apps/desktop/src/themes/backend-sync.test.ts | 비활성 UI/native/온보딩/다중 pane 계약을 포함하는 전체 suite → PR-9 (DOM 필요로 오인하지 않음) |

## 8b 전체 patch / shim 목록 (8a 유지분 포함)

아래 line 범위는 **5894e750 원본**의 삭제·교체·발췌 범위다. 파일 전체 축소 shim은 전체 범위로 표시한다. 모든 테스트 파일은 원문 바이트 그대로다.

| 이식 경로 | 상태 | 원본 file:line | 사유/PB |
|---|---|---|---|
| src/hermes/api/client.ts | patched | apps/desktop/src/api/client.ts:2, 11, 336, 518, 531, 541, 634, 701 | 8a 유지: 명시 rpcMode/socketFactory/API bridge; PB:414,D10,665 |
| src/hermes/api/sessions.ts | patched | apps/desktop/src/api/sessions.ts:13 | 원본 monorepo 상대 shared 경로를 기존 @hermes/shared alias로 변경; PB:665,676 |
| src/hermes/app/chat/composer/types.ts | shim | apps/desktop/src/app/chat/composer/types.ts:1-79 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/chat/sidebar/projects/workspace-groups.ts | shim | apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts:1-13, 82-1015 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/routes.ts | shim | apps/desktop/src/app/routes.ts:1-232, 236-318 | routeSessionId 비활성; sessionRoute 및 prefix 원문 발췌, workspace page false atom; UI router 연결 PR-9; PB:680,Q7 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts | patched | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts:2 | skin 타입만 unknown으로 축소; 원본 @/ import는 유지하고 같은 경로 shim 연결; PB:680 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/types.ts | patched | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/types.ts:2-3 | 미설치 react-query QueryClient와 React ref를 사용 필드의 구조 타입으로 치환; PB:680 |
| src/hermes/app/session/hooks/use-message-stream/runtime.ts | patched | apps/desktop/src/app/session/hooks/use-message-stream/index.ts:2-3, 6, 34-37, 40, 50-51, 57-74, 76-81, 144-157, 159-162, 247-261, 264-308, 332-334, 336-339, 349, 351-448, 464-500, 503-519, 525-527, 529-530, 560-562, 564-565, 625-627, 629-630, 732-734, 736-737, 1147-1149, 1151-1152, 1228-1230, 1232-1276 | 원본 index.ts의 callback 본문 발췌. React useCallback/useRef/useEffect와 구독 wrapper 제거, 명시 factory/refs로 연결; delta는 즉시 flush, UI refresh/native 효과 비활성. 원본 메시지 병합 알고리즘 유지; PB:674,680 |
| src/hermes/app/shell/hooks/use-context-breakdown.ts | shim | apps/desktop/src/app/shell/hooks/use-context-breakdown.ts:1-210 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/shell/statusbar-controls.ts | shim | apps/desktop/src/app/shell/statusbar-controls.tsx:1-32, 46-361 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/components/assistant-ui/directive-text.ts | shim | apps/desktop/src/components/assistant-ui/directive-text.tsx:1-160, 180-634 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/components/chat/vibe-hearts.ts | shim | apps/desktop/src/components/chat/vibe-hearts.tsx:1-121 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/components/pane-shell/tree/store.ts | shim | apps/desktop/src/components/pane-shell/tree/store.ts:1-2282 | pane tree atoms의 빈 초기값/비활성 표시 효과; 세션 state 원본 보존, 다중 pane UI는 PR-9; PB:680 |
| src/hermes/components/pane-shell/workspace-scope.ts | shim | apps/desktop/src/components/pane-shell/workspace-scope.ts:1-88, 92-190 | workspaceScopeKey 원문 발췌 및 sessions 기본 scope; pane memory 비활성; PB:680 |
| src/hermes/contrib/types.ts | shim | apps/desktop/src/contrib/types.ts:1-17, 19-51 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/global.d.ts | shim | apps/desktop/src/global.d.ts:1-1015, 1058-1060, 1063-1067, 1069-1179, 1227-1777 | 원본 connection/API/registry 타입만 발췌, explicit web bridge 구조 타입; PB:D10 |
| src/hermes/hermes.ts | patched | apps/desktop/src/hermes.ts:20-134 | 원본 barrel의 client 및 sessions getSession/setSessionUnreadRemote export만 유지; PB:665,674 |
| src/hermes/i18n/index.ts | shim | apps/desktop/src/i18n/index.ts:1-44 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/chat-messages/types.ts | patched | apps/desktop/src/lib/chat-messages/types.ts:1 | assistant-ui 메시지 타입을 설치 없는 data-only 타입 경계로 대체; PB:674,680 |
| src/hermes/lib/chat-runtime.ts | patched | apps/desktop/src/lib/chat-runtime.ts:1 | assistant-ui 타입만 data-only 타입 경계로 대체, 로직 원문 유지; PB:674,680 |
| src/hermes/lib/completion-sound.ts | shim | apps/desktop/src/lib/completion-sound.ts:1-525 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/connector-tools.ts | patched | apps/desktop/src/lib/connector-tools.ts:1-2 | assistant-ui isRecord/ToolCall 타입을 data-only 경계로 교체; PB:680 |
| src/hermes/lib/haptics.ts | shim | apps/desktop/src/lib/haptics.ts:1-129 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/identity-trace.ts | shim | apps/desktop/src/lib/identity-trace.ts:1-31 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/media.ts | shim | apps/desktop/src/lib/media.ts:1-7, 9-11, 112-442, 449 | 순수 경로/표시 함수 및 dimensions 타입만 원문 발췌; native filesystem/media 호출 제외; PB:680 |
| src/hermes/lib/message-types.ts | shim | apps/desktop/src/lib/chat-messages/types.ts:1-245 | 외부 assistant-ui 미설치. 메시지 data 구조와 isRecord만 제공, UI/runtime 없음; PB:680 |
| src/hermes/lib/model-options.ts | shim | apps/desktop/src/lib/model-options.ts:1-119, 130-224 | modelOptionsQueryKey 원문만 발췌. 모델 조회/설정/자동 효과 제외; PB:680 |
| src/hermes/platform-bridge.ts | shim | apps/desktop/src/store/gateway.ts:1-104, 107-183, 185-238, 240-2484 | API 호출/connection descriptor를 명시 설치·해제; native settings 타입은 선택사항, 실제 웹 호스트 미제공; PB:D10 |
| src/hermes/store/billing-block.ts | shim | apps/desktop/src/store/billing-block.ts:1-76 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/composer-queue.ts | patched | apps/desktop/src/store/composer-queue.ts:345 | Web Locks async callback의 TS 5.9 Promise 중첩 타입을 async 함수 반환으로 평탄화(런타임 의미 동일); PB:674 |
| src/hermes/store/connection-registry-state.ts | patched | apps/desktop/src/store/connection-registry-state.ts:17 | native registry 자동 발견 false. 웹 단일 primary, 원본 topology 로직 유지; PB:D10,680 |
| src/hermes/store/desktop-metrics.ts | shim | apps/desktop/src/store/desktop-metrics.ts:1-30, 32, 34-154, 169-999 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/free-tier-sign-in.ts | shim | apps/desktop/src/store/free-tier-sign-in.ts:1-524 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/gateway.ts | patched | apps/desktop/src/store/gateway.ts:1, 471, 693, 767, 783, 1046, 1128, 1320, 1539, 1804, 1858 | window.hermesDesktop를 명시 bridge로 교체; secondary 생성은 supportsSecondaryGateways 없으면 거절; closure의 non-null 좁히기 TS 5.9 호환; PB:676,D10 |
| src/hermes/store/interface-mode.ts | shim | apps/desktop/src/store/interface-mode.ts:1-261 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/native-notifications.ts | shim | apps/desktop/src/store/native-notifications.ts:1-444 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/notifications.ts | patched | apps/desktop/src/store/notifications.ts:58, 310, 320, 329-338, 367, 380 | 원본 notifications 복사 유지. window 타이머를 globalThis 및 해당 반환 타입으로 변경, Electron logLine 본문 제거; PB:D10,680 |
| src/hermes/store/onboarding-session.ts | shim | apps/desktop/src/store/onboarding-session.ts:1-36 | 온보딩 세션 없음 및 boolean false atom. credential 온보딩 제외; PB:680 |
| src/hermes/store/onboarding.ts | shim | apps/desktop/src/store/onboarding.ts:1-1480 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/pet.ts | shim | apps/desktop/src/store/pet.ts:1-266 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview-ownership.ts | shim | apps/desktop/src/store/preview-ownership.ts:1-200 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview-status.ts | shim | apps/desktop/src/store/preview-status.ts:1-413 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview.ts | shim | apps/desktop/src/store/preview.ts:1-1316 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/profile.ts | shim | apps/desktop/src/store/profile.ts:1-37, 43-1193 | normalizeProfileKey 원문 발췌 및 default profile atom; native hydration/전환 효과 제외; PB:680 |
| src/hermes/store/projects.ts | shim | apps/desktop/src/store/projects.ts:1-1722 | projectTree 재수출, followActiveSessionCwd 효과 중단; 프로젝트 목록/자동 refresh 제외; PB:680 |
| src/hermes/store/prompts.ts | patched | apps/desktop/src/store/prompts.ts:76, 206-208, 218, 228 | approval 원본 유지. sudo/secret/vault 입구는 typed disabled store로 거절, pending 상태 저장 불가; PB:680 PromptOverlays 비활성 |
| src/hermes/store/session-sync.ts | shim | apps/desktop/src/store/session-sync.ts:1-25 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/session.ts | patched | apps/desktop/src/store/session.ts:1, 353, 376 | native settings/sanitizeWorkspaceCwd를 optional bridge 뒤로 이동. WebHost는 제공하지 않아 기존 early-return 유지; PB:674,D10 |
| src/hermes/store/transcript-sync.ts | shim | apps/desktop/src/store/transcript-sync.ts:1-49 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/updates.ts | shim | apps/desktop/src/store/updates.ts:1-1399 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/windows.ts | shim | apps/desktop/src/store/windows.ts:1-292 | 별도 native browser/secondary 창이 아닌 주 세션 호스트(false/false). native 창 열기 불가; PB:D10,680 |
| src/hermes/themes/backend-sync.ts | shim | apps/desktop/src/themes/backend-sync.ts:1-170 | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/platform/fake-websocket.ts | patched | apps/shared/src/json-rpc-gateway-legacy-replay.test.ts:1-9, 43-165 | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |
| src/protocol/LICENSE-HERMES-NOTICE.md | patched | LICENSE:22 | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |
| src/protocol/index.ts | patched | apps/shared/src/index.ts:1, 3-65, 67-75, 104-125, 132-196, 209 | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |

## 8b 전체 SHA-256 표 (최종 파일 기준)

문서 자체는 자기 참조 때문에 제외한다. 신규 파일에는 원본 hash가 없다. verbatim은 실제 바이트 hash 일치로 분류했다.

| 이식 경로 | 원본 경로 (H=5894e750) | 원본 SHA-256 | 이식본 SHA-256 | 상태 | 사유/PB |
|---|---|---|---|---|---|
| package.json | 신규/console 설정 | — | 0dbf4e1ae7cc47b95a245cd4e096239629de7de0276f0b3a00507f72d67bbadb | patched | Hermes 외 기존 console 설정. package는 scripts만, tsconfig는 paths만 변경; 의존성 불변 |
| src/hermes/api/canonical-ambiguous-owner-reconcile.test.ts | apps/desktop/src/api/canonical-ambiguous-owner-reconcile.test.ts | 0cef10124aec94811a721ad83a2343dcbbb4e769ee542630945ebb8c4f6227bf | 0cef10124aec94811a721ad83a2343dcbbb4e769ee542630945ebb8c4f6227bf | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-deadline.test.ts | apps/desktop/src/api/canonical-deadline.test.ts | 0f1f57ee0fb48c8572fa2e40a4d5d31d5ebc17dd93ba43a166ff16f20efd1433 | 0f1f57ee0fb48c8572fa2e40a4d5d31d5ebc17dd93ba43a166ff16f20efd1433 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-live-pick.test.ts | apps/desktop/src/api/canonical-live-pick.test.ts | 044ab710f2e3b4758846a701480e04b437db55a2068db0cbef9fced7132f8a9c | 044ab710f2e3b4758846a701480e04b437db55a2068db0cbef9fced7132f8a9c | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-prompt-owner.test.ts | apps/desktop/src/api/canonical-prompt-owner.test.ts | f2e70d59246545fc746761388296099f1c92fef27ed963eb327b2a1d38c2f431 | f2e70d59246545fc746761388296099f1c92fef27ed963eb327b2a1d38c2f431 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-protocol.test.ts | apps/desktop/src/api/canonical-protocol.test.ts | 3f51f1154a44595d1346673806698bc4734c1cf2a73ba257affe88f42e40e8e3 | 3f51f1154a44595d1346673806698bc4734c1cf2a73ba257affe88f42e40e8e3 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-protocol.ts | apps/desktop/src/api/canonical-protocol.ts | 76406e7784afe5a72aefd5d9c523686bce2a48b1b922bcb9b6e7620ce9d4822f | 76406e7784afe5a72aefd5d9c523686bce2a48b1b922bcb9b6e7620ce9d4822f | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-retained-control-retire.test.ts | apps/desktop/src/api/canonical-retained-control-retire.test.ts | cac332c1bf3d8c09889e861f0b4a7e9cb8f4896204350f65f63f3e4d06383e41 | cac332c1bf3d8c09889e861f0b4a7e9cb8f4896204350f65f63f3e4d06383e41 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-retained-mutation-stale.test.ts | apps/desktop/src/api/canonical-retained-mutation-stale.test.ts | 5776b4a4dd366b14e85b60f26af616feaf289ce0b29d04b1088fa7e848ec1554 | 5776b4a4dd366b14e85b60f26af616feaf289ce0b29d04b1088fa7e848ec1554 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-rewind-lost-reply.test.ts | apps/desktop/src/api/canonical-rewind-lost-reply.test.ts | f0586c33ac8f5001936b9d4c034660e3e33774204a13aaa1a3b30221864e0d96 | f0586c33ac8f5001936b9d4c034660e3e33774204a13aaa1a3b30221864e0d96 | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-session-updated-revision.test.ts | apps/desktop/src/api/canonical-session-updated-revision.test.ts | 5c2ff164b062c5988caa78f1c049eb80fb03549efb9bda4b748779833b060efa | 5c2ff164b062c5988caa78f1c049eb80fb03549efb9bda4b748779833b060efa | verbatim | 원문 바이트 일치 |
| src/hermes/api/canonical-unknown-admission-fence.test.ts | apps/desktop/src/api/canonical-unknown-admission-fence.test.ts | da83f1e17ae4618e30831370cfe2dee7be6988881d1cecd135670692649f747c | da83f1e17ae4618e30831370cfe2dee7be6988881d1cecd135670692649f747c | verbatim | 원문 바이트 일치 |
| src/hermes/api/client.ts | apps/desktop/src/api/client.ts | b56abc62e90460a2b69c71fef6798cadff3050c9745a54f2468de3b73534ecc3 | fc28c5430d3396b6213c995382eabe3e7bccdaab40a480d048b1252e10e06608 | patched | 8a 유지: 명시 rpcMode/socketFactory/API bridge; PB:414,D10,665 |
| src/hermes/api/sessions.test.ts | apps/desktop/src/api/sessions.test.ts | 517e936fbfa839ab2c3708a888f074ef94b5b0e2a9942ade4b9106f011ae01f0 | 517e936fbfa839ab2c3708a888f074ef94b5b0e2a9942ade4b9106f011ae01f0 | verbatim | 원문 바이트 일치 |
| src/hermes/api/sessions.ts | apps/desktop/src/api/sessions.ts | 316ba4a9b5a4e1f898328b56796a8eb180dc5360f9ba56ba82dcdcc03a11a64c | e42badee79542394b7e1ce259152a598ff3d24495d446b01535d16e1e03afef3 | patched | 원본 monorepo 상대 shared 경로를 기존 @hermes/shared alias로 변경; PB:665,676 |
| src/hermes/app/chat/composer/types.ts | apps/desktop/src/app/chat/composer/types.ts | 3a757c590a818046a3baa35fe3d1d2330329e7e9b43f80aa34445bfbffc5f4d7 | f0b386baaf6e6769a873054d3748af13a8d2184f92ecf7397f33e1b39f8bd422 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/chat/sidebar/projects/workspace-groups.ts | apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts | 3f6f17fe5c0b33d1c188a12a05d3db9cb477175498d7306f0e1f12b398de7628 | 77c450219daf49f0ea264aba39b9efaea363d797478248074fc8a2b7578580ca | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/chat/thread-loading.test.ts | apps/desktop/src/app/chat/thread-loading.test.ts | 493737014bf07364a1792befd008fde3acff934ec0a66606589cf796e748af63 | 493737014bf07364a1792befd008fde3acff934ec0a66606589cf796e748af63 | verbatim | 원문 바이트 일치 |
| src/hermes/app/chat/thread-loading.ts | apps/desktop/src/app/chat/thread-loading.ts | 91a373d8f31f926da02489fe6681dcf17b8a624830de7934c6d28a15e9a330ac | 91a373d8f31f926da02489fe6681dcf17b8a624830de7934c6d28a15e9a330ac | verbatim | 원문 바이트 일치 |
| src/hermes/app/contrib/wiring-routing.test.ts | apps/desktop/src/app/contrib/wiring-routing.test.ts | 1b06786297e7bc5675563d26b19e524a5eeb361d75bdb2d60e94a0eacae7d159 | 1b06786297e7bc5675563d26b19e524a5eeb361d75bdb2d60e94a0eacae7d159 | verbatim | 원문 바이트 일치 |
| src/hermes/app/contrib/wiring-routing.ts | apps/desktop/src/app/contrib/wiring-routing.ts | b3a0a2287b426bb0c70f8836738d86d0eefb4507ceefbf1b896ebdd124487294 | b3a0a2287b426bb0c70f8836738d86d0eefb4507ceefbf1b896ebdd124487294 | verbatim | 원문 바이트 일치 |
| src/hermes/app/open-session.ts | apps/desktop/src/app/open-session.ts | 4b8c9f613819973b49bbeb6af638ccec6b2e44f9414b58d5b412d84003c07e96 | 4b8c9f613819973b49bbeb6af638ccec6b2e44f9414b58d5b412d84003c07e96 | verbatim | 원문 바이트 일치 |
| src/hermes/app/routes.ts | apps/desktop/src/app/routes.ts | 037f10823e2567ec9c5bb84ca395607a14b7b9dd515e09d7df9ac421b57afc28 | dc593fa830b9b7799e5079529140c484b13d9f6ed3a9e646181dfb4268bc4d87 | shim | routeSessionId 비활성; sessionRoute 및 prefix 원문 발췌, workspace page false atom; UI router 연결 PR-9; PB:680,Q7 |
| src/hermes/app/session/hooks/use-message-stream/collapse-duplicate-final.ts | apps/desktop/src/app/session/hooks/use-message-stream/collapse-duplicate-final.ts | 49f6b487e7a1c333f6cb852fb0ab5e7993d4ca5d843f90d33ba2a999e46b4cfd | 49f6b487e7a1c333f6cb852fb0ab5e7993d4ca5d843f90d33ba2a999e46b4cfd | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts | 372ffc3a7332b4a62f36f2fdaca0e060b95bbd8524a3efc1a75297aff171d811 | c24678de9f5997f75c71018e2bb207af5a751a113f14477426700de0f608c16c | patched | skin 타입만 unknown으로 축소; 원본 @/ import는 유지하고 같은 경로 shim 연결; PB:680 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/message-stream.test.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/message-stream.test.ts | 94bb8e03d99aa6b6a92dca70df84718fc1fa1184b9fa54eef2483722693f1400 | 94bb8e03d99aa6b6a92dca70df84718fc1fa1184b9fa54eef2483722693f1400 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/message-stream.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/message-stream.ts | b5756edfeea9e207dd2787246a02f452e25b004f60f1e91f28f27fbf1ae2fafe | b5756edfeea9e207dd2787246a02f452e25b004f60f1e91f28f27fbf1ae2fafe | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/session-info.test.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/session-info.test.ts | 1fc46f295916181be808580c9d45874c55d8031ddf2b84cccadb57e1e6dea00e | 1fc46f295916181be808580c9d45874c55d8031ddf2b84cccadb57e1e6dea00e | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/session-info.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/session-info.ts | 593cb513a67b0f491a567557b08ae70fa204b1f57f276e0a02be69e69499bd2c | 593cb513a67b0f491a567557b08ae70fa204b1f57f276e0a02be69e69499bd2c | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/types.ts | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/types.ts | d0818a8b7dd498bb97faa68fbb5e211a4fd7d2f598bec65b4eb1a3edfe254827 | 0b5fad0a457da1afca9f96afd7ac2f1362e838c61773de4db4f8dd8a5f9eacb1 | patched | 미설치 react-query QueryClient와 React ref를 사용 필드의 구조 타입으로 치환; PB:680 |
| src/hermes/app/session/hooks/use-message-stream/interrupted-reply.ts | apps/desktop/src/app/session/hooks/use-message-stream/interrupted-reply.ts | 1f97f38bf0160c8331e2dc6665831f273df677ca66ccc0c4cc3dc903ded17902 | 1f97f38bf0160c8331e2dc6665831f273df677ca66ccc0c4cc3dc903ded17902 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/receipt-user-rows.ts | apps/desktop/src/app/session/hooks/use-message-stream/receipt-user-rows.ts | eb9d5bddb1b94bb2a49bfb4b690cce5a7387d8cd0f3ff69430e22a7b01c5f8ce | eb9d5bddb1b94bb2a49bfb4b690cce5a7387d8cd0f3ff69430e22a7b01c5f8ce | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/response-parts.ts | apps/desktop/src/app/session/hooks/use-message-stream/response-parts.ts | 9bf06843facfb6e0cec9ddb061c593b54d3f3ed309497a691d7e9649061754d8 | 9bf06843facfb6e0cec9ddb061c593b54d3f3ed309497a691d7e9649061754d8 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/runtime.ts | apps/desktop/src/app/session/hooks/use-message-stream/index.ts | a727aad73710b90657e61e7c4aaa0ff73ffcfac25ccbeffbd7a8dd3b97a31888 | 68284a28340a2c692c06e7894db598eebcfb889c294ab693a1d0529c369927a3 | patched | 원본 index.ts의 callback 본문 발췌. React useCallback/useRef/useEffect와 구독 wrapper 제거, 명시 factory/refs로 연결; delta는 즉시 flush, UI refresh/native 효과 비활성. 원본 메시지 병합 알고리즘 유지; PB:674,680 |
| src/hermes/app/session/hooks/use-message-stream/utils.test.ts | apps/desktop/src/app/session/hooks/use-message-stream/utils.test.ts | 4e8c294049ce53cd9c528139bfb0c0c4c60a53b4646893c3382181936cd04ee5 | 4e8c294049ce53cd9c528139bfb0c0c4c60a53b4646893c3382181936cd04ee5 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-message-stream/utils.ts | apps/desktop/src/app/session/hooks/use-message-stream/utils.ts | 2f271b55c9c450db4c657301f7718a058934510f704489ed09c1f2d3db318f58 | 2f271b55c9c450db4c657301f7718a058934510f704489ed09c1f2d3db318f58 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-session-actions/pending-turn-identity.ts | apps/desktop/src/app/session/hooks/use-session-actions/pending-turn-identity.ts | f66d02cdbb670e8d35265928644d250cda4e556a1d738d9f478df1b4ea3a1d0f | f66d02cdbb670e8d35265928644d250cda4e556a1d738d9f478df1b4ea3a1d0f | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-session-actions/resume-verdicts.ts | apps/desktop/src/app/session/hooks/use-session-actions/resume-verdicts.ts | e25eef8fc9e660cca5b68a5c89928cf19228636a9afa2cf681a66bc9b4c3955c | e25eef8fc9e660cca5b68a5c89928cf19228636a9afa2cf681a66bc9b4c3955c | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-session-actions/runtime-owner.test.ts | apps/desktop/src/app/session/hooks/use-session-actions/runtime-owner.test.ts | 6a008ce6c094b35688d56814b51078994df481cb0c0221a4b69bc0541186de0e | 6a008ce6c094b35688d56814b51078994df481cb0c0221a4b69bc0541186de0e | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-session-actions/runtime-owner.ts | apps/desktop/src/app/session/hooks/use-session-actions/runtime-owner.ts | ec616f360e8ef170c3ab7268d5321be73206f1d6f816d7be55fc66c7929f4db0 | ec616f360e8ef170c3ab7268d5321be73206f1d6f816d7be55fc66c7929f4db0 | verbatim | 원문 바이트 일치 |
| src/hermes/app/session/hooks/use-session-actions/utils.ts | apps/desktop/src/app/session/hooks/use-session-actions/utils.ts | 8c5a9ce3ee551d96642d893c0a1911c70e7a96226895f237e76c39c20160a961 | 8c5a9ce3ee551d96642d893c0a1911c70e7a96226895f237e76c39c20160a961 | verbatim | 원문 바이트 일치 |
| src/hermes/app/shell/hooks/use-context-breakdown.ts | apps/desktop/src/app/shell/hooks/use-context-breakdown.ts | f93e4f16f3a07d76481de6af43aedb67a90b30bff65d4a92982ee3f4d433879b | 4e61cab012bc2e7e61c835fb3ed795db161fd8a50b735fc89ba22b5556566fa0 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/shell/statusbar-controls.ts | apps/desktop/src/app/shell/statusbar-controls.tsx | 67e0dbb288eb88458d790f76d88b50d0a384b57b8e39bf5c5bf2b7c08fbea9fa | 57ce698bd70fc645f73ff441e37157b8c8c353bb1081737f83e16602d8bc740d | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/app/types.ts | apps/desktop/src/app/types.ts | 3e4ac99789a053b894e7c754dd01557d0a635413d0393a13cb6b25bccba5a1a1 | 3e4ac99789a053b894e7c754dd01557d0a635413d0393a13cb6b25bccba5a1a1 | verbatim | 원문 바이트 일치 |
| src/hermes/components/assistant-ui/directive-text.ts | apps/desktop/src/components/assistant-ui/directive-text.tsx | a45fb87126e1508cd6ddea44d871577066c41a127a00c2959c4fc8071d11627c | 1a4d0d026b713da0d834a9066d991815a2c2c33fff9e1de5806bd18bb9e6f9bf | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/components/assistant-ui/reference-kinds.ts | apps/desktop/src/components/assistant-ui/reference-kinds.ts | fa12e4a68897e92410cdbf958c9004a2ceab9cccfd24561554d184d3296c4a22 | fa12e4a68897e92410cdbf958c9004a2ceab9cccfd24561554d184d3296c4a22 | verbatim | 원문 바이트 일치 |
| src/hermes/components/chat/vibe-hearts.ts | apps/desktop/src/components/chat/vibe-hearts.tsx | 0d585e3c39306db257527390056e4dbbbca9ae8938cb8a285095569f32a7b5c7 | fde0fa0bec9bd67de52b8952aaf27049879e09e79029ba35fc6f58767c943fbd | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/components/pane-shell/tree/model.ts | apps/desktop/src/components/pane-shell/tree/model.ts | 0a7bbfff9abc89dba6784a6452f3cd3f05f2c45aa9c42fe0cea752fc2b57e668 | 0a7bbfff9abc89dba6784a6452f3cd3f05f2c45aa9c42fe0cea752fc2b57e668 | verbatim | 원문 바이트 일치 |
| src/hermes/components/pane-shell/tree/store.ts | apps/desktop/src/components/pane-shell/tree/store.ts | acb704334d40d2c5c77f59712a3967632d02fceebab7f0f4fe280caa97b16c9b | 644e797565ed78fb5921df468a2f8a4b5e60d73d58d5c3a8a73c04bf348fb69f | shim | pane tree atoms의 빈 초기값/비활성 표시 효과; 세션 state 원본 보존, 다중 pane UI는 PR-9; PB:680 |
| src/hermes/components/pane-shell/workspace-scope.ts | apps/desktop/src/components/pane-shell/workspace-scope.ts | 8518f1463e2395d87a0a95fa26067754f2ee1fd1f5312b8e2b19c62694b22807 | 3a94eb1d1b20ba9575d55eec2ac8f707953786c6b63a2c7e52a8af47402ad30d | shim | workspaceScopeKey 원문 발췌 및 sessions 기본 scope; pane memory 비활성; PB:680 |
| src/hermes/contrib/types.ts | apps/desktop/src/contrib/types.ts | 0bc9809f982a18dd38e7362988c5bc1f956236c1ea8cfc23fbb1cf44e94a2145 | 8b55881023e0c58b30812b0a663aafa573290c6795ce5d1d4b5c20733a570d21 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/global.d.ts | apps/desktop/src/global.d.ts | b39b1fff32ca48b957896774121af10874f2a639ab0bd7b842f45c5ce9c6035f | 6e1187e2b2b0cc6dbd8341111d3b478166ca882ead323e31352e832c2a2e761e | shim | 원본 connection/API/registry 타입만 발췌, explicit web bridge 구조 타입; PB:D10 |
| src/hermes/hermes.ts | apps/desktop/src/hermes.ts | 87b6ae0fa05ea9d28b7c1556d7e9f438dd777d3c52594d0a49329d5204d29fad | 0200d11daeeb8ca05d7b88259c0b69eecdd30612fae115fc1450e14ac9ddc3ff | patched | 원본 barrel의 client 및 sessions getSession/setSessionUnreadRemote export만 유지; PB:665,674 |
| src/hermes/i18n/index.ts | apps/desktop/src/i18n/index.ts | 0b4f38d631ec0289a7e9540b4cd5d8f39ad04d6d0d9f4e1047d52c9a9da4cb8c | 5690f761ba211b55c770985061cdfff31fe23d9d11aeed44b8a2bd5f969180a1 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/chat-messages/attachment-turn.ts | apps/desktop/src/lib/chat-messages/attachment-turn.ts | e67b347cb5bd6bfe08d57e8e9a2d0d2f8e613c0d9c0b39091eaa5013d8246b45 | e67b347cb5bd6bfe08d57e8e9a2d0d2f8e613c0d9c0b39091eaa5013d8246b45 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/coverage.ts | apps/desktop/src/lib/chat-messages/coverage.ts | b18d531055f23e44460d483cb367de645edd12d89e16644144ffd8d626486ed0 | b18d531055f23e44460d483cb367de645edd12d89e16644144ffd8d626486ed0 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/hydration.ts | apps/desktop/src/lib/chat-messages/hydration.ts | e3269e596920033a979c8e9e19281014ec5436c8206026b23a6d2cb0a789ff1c | e3269e596920033a979c8e9e19281014ec5436c8206026b23a6d2cb0a789ff1c | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/index.ts | apps/desktop/src/lib/chat-messages/index.ts | e0731584f430dc06ac76f6d0ac71e295ef67de2ef6b69a0b58a69ff5d9c065ca | e0731584f430dc06ac76f6d0ac71e295ef67de2ef6b69a0b58a69ff5d9c065ca | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/parts.test.ts | apps/desktop/src/lib/chat-messages/parts.test.ts | c885b53a6daced90a392e53b02bfc0d0f3e800a6ec059fd2a4c68998f234e468 | c885b53a6daced90a392e53b02bfc0d0f3e800a6ec059fd2a4c68998f234e468 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/parts.ts | apps/desktop/src/lib/chat-messages/parts.ts | 16d8aea1e6643d7553e443b24f6142a09b9967a90efbb374af07c9aec55250f3 | 16d8aea1e6643d7553e443b24f6142a09b9967a90efbb374af07c9aec55250f3 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/reconciliation.test.ts | apps/desktop/src/lib/chat-messages/reconciliation.test.ts | e71844e33c76bfb6ca6167a2358e87095c4467c2ba16f5bb356886210feef656 | e71844e33c76bfb6ca6167a2358e87095c4467c2ba16f5bb356886210feef656 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/reconciliation.ts | apps/desktop/src/lib/chat-messages/reconciliation.ts | fa80bd2b0483f1b1f967b6d71e4398034de27b714c1c61a084da6e2a77638985 | fa80bd2b0483f1b1f967b6d71e4398034de27b714c1c61a084da6e2a77638985 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/tool-parts.ts | apps/desktop/src/lib/chat-messages/tool-parts.ts | 56e1330de2135bb0e723d3a4f5237761c88442d44c9fbf046066985e8b33d83a | 56e1330de2135bb0e723d3a4f5237761c88442d44c9fbf046066985e8b33d83a | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-messages/types.ts | apps/desktop/src/lib/chat-messages/types.ts | 2e484105b842c9e247e15b1afa453f6888713717425a9fcd60d5402cfca6ddac | 3fbbe202737bb19e957691f55119ade96967d3865b7e07623a4827e5b03ff6b7 | patched | assistant-ui 메시지 타입을 설치 없는 data-only 타입 경계로 대체; PB:674,680 |
| src/hermes/lib/chat-runtime.test.ts | apps/desktop/src/lib/chat-runtime.test.ts | c2ac603511428f2e8a7f7c03d410415b3bedb3346dcf20552496ac10e4027e89 | c2ac603511428f2e8a7f7c03d410415b3bedb3346dcf20552496ac10e4027e89 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/chat-runtime.ts | apps/desktop/src/lib/chat-runtime.ts | cdaf6e0dfa3d3532235f3c926a85b2bdcc888f3f088a06266db87d8637e51c79 | 51c1f89fcc0e2855182801b1767864cf00ac129b7cd2d056d1ede4c45e80a71b | patched | assistant-ui 타입만 data-only 타입 경계로 대체, 로직 원문 유지; PB:674,680 |
| src/hermes/lib/completion-sound.ts | apps/desktop/src/lib/completion-sound.ts | fdf82282a7a681e60822550b041b6691fc42aeec1dfbc8410435dd19dec896f9 | 8af42f8bfeb45c46068aa4d7b887aabfd6aedfc6905f92c741beb0c3623a1395 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/connection-scoped.test.ts | apps/desktop/src/lib/connection-scoped.test.ts | 9cbfc3275d2f69078e539bf2e2b42818b13cc243d299c5d285c43389bcb989cd | 9cbfc3275d2f69078e539bf2e2b42818b13cc243d299c5d285c43389bcb989cd | verbatim | 원문 바이트 일치 |
| src/hermes/lib/connection-scoped.ts | apps/desktop/src/lib/connection-scoped.ts | d3ed707102f33e97767c28836a2194a4dbdafd2ca3b6c8157ef76e3e7fa9e84c | d3ed707102f33e97767c28836a2194a4dbdafd2ca3b6c8157ef76e3e7fa9e84c | verbatim | 원문 바이트 일치 |
| src/hermes/lib/connector-tools.ts | apps/desktop/src/lib/connector-tools.ts | 0ddfd86d9aaa3c817274dbe053ccb8ca6d130a9f1299ea912de15c13e791bf15 | 3b6ceadbaea30fd8fb95f577c3c481be02c77d8732d6de2f6549b254a1514568 | patched | assistant-ui isRecord/ToolCall 타입을 data-only 경계로 교체; PB:680 |
| src/hermes/lib/draft-title.ts | apps/desktop/src/lib/draft-title.ts | 1f1428d8e281fee49d6a03cd1f75d949d865ae3c851f4dd0680c7bd60af449c8 | 1f1428d8e281fee49d6a03cd1f75d949d865ae3c851f4dd0680c7bd60af449c8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/embedded-images.test.ts | apps/desktop/src/lib/embedded-images.test.ts | 7cef2c9ac062fc77df198eb758ba74871a3e96f21ae2e144728f8be6fde8585d | 7cef2c9ac062fc77df198eb758ba74871a3e96f21ae2e144728f8be6fde8585d | verbatim | 원문 바이트 일치 |
| src/hermes/lib/embedded-images.ts | apps/desktop/src/lib/embedded-images.ts | 66b2d804e821c807170b74a0f6a7d9a400c7e6ca10a818b39e4304e240eb511d | 66b2d804e821c807170b74a0f6a7d9a400c7e6ca10a818b39e4304e240eb511d | verbatim | 원문 바이트 일치 |
| src/hermes/lib/error-surface.ts | apps/desktop/src/lib/error-surface.ts | 76d69636288ac8dc50ae0cafa3ee3699c3c40ca832253bae4d39534b65b41da6 | 76d69636288ac8dc50ae0cafa3ee3699c3c40ca832253bae4d39534b65b41da6 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/execution-authority.frames.json | apps/desktop/src/lib/execution-authority.frames.json | e1c6bf9320849999e5fd0deb16dc67e535c1a671203ee0d3bb3bae7b1dcdcd8f | e1c6bf9320849999e5fd0deb16dc67e535c1a671203ee0d3bb3bae7b1dcdcd8f | verbatim | 원문 바이트 일치 |
| src/hermes/lib/execution-authority.test.ts | apps/desktop/src/lib/execution-authority.test.ts | a53eee220e2608994d079295a09b5df7c19b2595654e1389c5f14e0e0238a478 | a53eee220e2608994d079295a09b5df7c19b2595654e1389c5f14e0e0238a478 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/execution-authority.ts | apps/desktop/src/lib/execution-authority.ts | 438d22c51061ee135ffdafb0bef97cfee20cffa20d1479ea8d2d92e4b94bebce | 438d22c51061ee135ffdafb0bef97cfee20cffa20d1479ea8d2d92e4b94bebce | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-events.test.ts | apps/desktop/src/lib/gateway-events.test.ts | 15ccfe35afe3350c3ebb131dce699f61f0c2a094bec2a5acb4d416b838c36e45 | 15ccfe35afe3350c3ebb131dce699f61f0c2a094bec2a5acb4d416b838c36e45 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-events.ts | apps/desktop/src/lib/gateway-events.ts | 49d791c9a8067d8a22ed3f60b1c8e5c29b2ad34dc79f64073668a2d4d9ed70a8 | 49d791c9a8067d8a22ed3f60b1c8e5c29b2ad34dc79f64073668a2d4d9ed70a8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-liveness-policy.test.ts | apps/desktop/src/lib/gateway-liveness-policy.test.ts | 857710b5e230bc39280202d34b3de553cd47371d2d9ef0f46b02ee38acc49afb | 857710b5e230bc39280202d34b3de553cd47371d2d9ef0f46b02ee38acc49afb | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-liveness-policy.ts | apps/desktop/src/lib/gateway-liveness-policy.ts | ebfa0c34c5a55f9ae46f5672d78e1d5c31ea28a0ae7c57d0ac76c6712405e0c8 | ebfa0c34c5a55f9ae46f5672d78e1d5c31ea28a0ae7c57d0ac76c6712405e0c8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-rpc.test.ts | apps/desktop/src/lib/gateway-rpc.test.ts | b808af95b5ae21d3693b5a64e274b6e310fd191935e94a96a481913ec6b748e7 | b808af95b5ae21d3693b5a64e274b6e310fd191935e94a96a481913ec6b748e7 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-rpc.ts | apps/desktop/src/lib/gateway-rpc.ts | eabe8d8ac494748461910fffed53225881c235a97a77e389f43f07bfe1bea3cf | eabe8d8ac494748461910fffed53225881c235a97a77e389f43f07bfe1bea3cf | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-ws-url.test.ts | apps/desktop/src/lib/gateway-ws-url.test.ts | 2c83316ecbabddad5906b8f5d48285241c4d07a92303ae468d100d9d7daeb7e9 | 2c83316ecbabddad5906b8f5d48285241c4d07a92303ae468d100d9d7daeb7e9 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/gateway-ws-url.ts | apps/desktop/src/lib/gateway-ws-url.ts | 6e18b8d8974d6cb4004309d3acc6c445930b57336bcb2e62aa7f4587267e69a0 | 6e18b8d8974d6cb4004309d3acc6c445930b57336bcb2e62aa7f4587267e69a0 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/generated-images.test.ts | apps/desktop/src/lib/generated-images.test.ts | 1437a2fa2e42dfb0bd0c7a6da9daee6371fdedf7753d38de350adb90cad41f49 | 1437a2fa2e42dfb0bd0c7a6da9daee6371fdedf7753d38de350adb90cad41f49 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/generated-images.ts | apps/desktop/src/lib/generated-images.ts | 3a1d01a5f21fba15a1e1167f42556d6c20398032721f1b392bb0ff360a90d57c | 3a1d01a5f21fba15a1e1167f42556d6c20398032721f1b392bb0ff360a90d57c | verbatim | 원문 바이트 일치 |
| src/hermes/lib/haptics.ts | apps/desktop/src/lib/haptics.ts | 10eb1ec3942c88586f7d0e1c9f1054c3f07848c4aec180bc6ec2efc12c40523d | a8890147c72eef5da2a8d112b33c87021b9351ebcff53bad0c914a19b387ddf4 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/identity-trace.ts | apps/desktop/src/lib/identity-trace.ts | e45b890836db71285a238a079b967c0af0c353182a9cb8a08ddcdfe65c0c81f5 | 1bea2968aa15d62122536a5e84faaf73aaa99948371cbe13748165a5cd358d7f | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/lib/inflight-turn-journal.test.ts | apps/desktop/src/lib/inflight-turn-journal.test.ts | fd78e5a3430b7c4065c59d3de2f4de39e2db45a859e441dee6375c20aa6f3a19 | fd78e5a3430b7c4065c59d3de2f4de39e2db45a859e441dee6375c20aa6f3a19 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/inflight-turn-journal.ts | apps/desktop/src/lib/inflight-turn-journal.ts | ee59dc29fc05a03fe506fa5579a98ef22fec4837f40336deff9b60cc41d16fc9 | ee59dc29fc05a03fe506fa5579a98ef22fec4837f40336deff9b60cc41d16fc9 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-heartbeat.test.ts | apps/desktop/src/lib/json-rpc-gateway-heartbeat.test.ts | cfcc4ec4ea5b17e14691fb3f584496f1e547a349259c46afd5bf9b0364dd1f4b | cfcc4ec4ea5b17e14691fb3f584496f1e547a349259c46afd5bf9b0364dd1f4b | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-recovery.test.ts | apps/desktop/src/lib/json-rpc-gateway-recovery.test.ts | 694ea7e4aaa04080e0617e16612b9a4bcd337710e1ae7af3a0f7b84f6ea98a72 | 694ea7e4aaa04080e0617e16612b9a4bcd337710e1ae7af3a0f7b84f6ea98a72 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/json-rpc-gateway-url-guard.test.ts | apps/desktop/src/lib/json-rpc-gateway-url-guard.test.ts | a2ab7bc9d7f28472a649982f3094847eb5e52fd6d91e12bc59db6de2bfaf7619 | a2ab7bc9d7f28472a649982f3094847eb5e52fd6d91e12bc59db6de2bfaf7619 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/keyed-timeouts.ts | apps/desktop/src/lib/keyed-timeouts.ts | dfd948c283f136cbb52fcd27b53b578e9d6c87984d99e8cbb8b0d64384399819 | dfd948c283f136cbb52fcd27b53b578e9d6c87984d99e8cbb8b0d64384399819 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/legacy-session-owner-backfill.test.ts | apps/desktop/src/lib/legacy-session-owner-backfill.test.ts | 8cbbcf891beeac5eb1f65b1cd508ff09a4babad311a27417c73dcf21a40b39b2 | 8cbbcf891beeac5eb1f65b1cd508ff09a4babad311a27417c73dcf21a40b39b2 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/legacy-session-owner-backfill.ts | apps/desktop/src/lib/legacy-session-owner-backfill.ts | 7eeb2b98fce3e3410a528361fcd9f47dd0ad382d4e49537e25492ccb6c00c69f | 7eeb2b98fce3e3410a528361fcd9f47dd0ad382d4e49537e25492ccb6c00c69f | verbatim | 원문 바이트 일치 |
| src/hermes/lib/local-gateway-ensure-failure.ts | apps/desktop/src/lib/local-gateway-ensure-failure.ts | 45aa78ef2504cace4d8f8c73b859431d670e4501f1ad305aec9f5b08964e5f66 | 45aa78ef2504cace4d8f8c73b859431d670e4501f1ad305aec9f5b08964e5f66 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/lru-cache.test.ts | apps/desktop/src/lib/lru-cache.test.ts | 871e721ffca85d77763889d18b0fcb9fd1d8ac9565bd374e26c1dd4f649829e7 | 871e721ffca85d77763889d18b0fcb9fd1d8ac9565bd374e26c1dd4f649829e7 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/lru-cache.ts | apps/desktop/src/lib/lru-cache.ts | fcc14eed8f3d96ca0f347510678cde2b6040e2cabfead1f770b486c51c426b51 | fcc14eed8f3d96ca0f347510678cde2b6040e2cabfead1f770b486c51c426b51 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/media.ts | apps/desktop/src/lib/media.ts | d39d5a3bd2f275edd10abf12a48d844e90a09407032c2e2a9c3f26b3a1488413 | 9ddefa221ada2466a8e1e284043e2a97133cfb608ee560367103812ee849e4c5 | shim | 순수 경로/표시 함수 및 dimensions 타입만 원문 발췌; native filesystem/media 호출 제외; PB:680 |
| src/hermes/lib/message-types.ts | apps/desktop/src/lib/chat-messages/types.ts | 2e484105b842c9e247e15b1afa453f6888713717425a9fcd60d5402cfca6ddac | 529833c21d85616dd893fcff8417e23151f68bc3df5b1ab15f020a6370441259 | shim | 외부 assistant-ui 미설치. 메시지 data 구조와 isRecord만 제공, UI/runtime 없음; PB:680 |
| src/hermes/lib/model-options.ts | apps/desktop/src/lib/model-options.ts | 473794e08e15b96b48e12409f7869dbf08e09cdafb6cb3b56fd765cb93199f89 | 9f3bfa1674ed86722a27cde33d99a22ee5b84d280908517c5dcb8a9a5a06675f | shim | modelOptionsQueryKey 원문만 발췌. 모델 조회/설정/자동 효과 제외; PB:680 |
| src/hermes/lib/persisted.test.ts | apps/desktop/src/lib/persisted.test.ts | 5825256b012a7af19a66539a71d1e27ec837d279805061b3fb6ad94fc1acd76e | 5825256b012a7af19a66539a71d1e27ec837d279805061b3fb6ad94fc1acd76e | verbatim | 원문 바이트 일치 |
| src/hermes/lib/persisted.ts | apps/desktop/src/lib/persisted.ts | 996b2ac29468713ec279b549b20cabd1697ed8284332a09a5109698fb3712975 | 996b2ac29468713ec279b549b20cabd1697ed8284332a09a5109698fb3712975 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/personalities.ts | apps/desktop/src/lib/personalities.ts | 0db72ba835fd3aeee989eee924265bffb9df3467cff4a607a63d8ad1804fae60 | 0db72ba835fd3aeee989eee924265bffb9df3467cff4a607a63d8ad1804fae60 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/replay-gap-owner.test.ts | apps/desktop/src/lib/replay-gap-owner.test.ts | 72fadf493abd37ca49f7a5da0f405ac2c8f3f529a07683a806a10a77abd017a3 | 72fadf493abd37ca49f7a5da0f405ac2c8f3f529a07683a806a10a77abd017a3 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/replay-gap-owner.ts | apps/desktop/src/lib/replay-gap-owner.ts | 499814bad73d3577cd2700cb2670e06c3a6441eff8b90d89ea88ca526159c035 | 499814bad73d3577cd2700cb2670e06c3a6441eff8b90d89ea88ca526159c035 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-ids.test.ts | apps/desktop/src/lib/session-ids.test.ts | 8f56b9a9f424f69ca4c7513f9a7602a51ca021f4d77d714ced078866fbca9d49 | 8f56b9a9f424f69ca4c7513f9a7602a51ca021f4d77d714ced078866fbca9d49 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-ids.ts | apps/desktop/src/lib/session-ids.ts | e1c6ee880bf6249531ac1daa8e93f280ed077359b5d4f6fce78af0c01ea5b2f5 | e1c6ee880bf6249531ac1daa8e93f280ed077359b5d4f6fce78af0c01ea5b2f5 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-owner-stamp.ts | apps/desktop/src/lib/session-owner-stamp.ts | 46f3be04a14277e4b015a9bcd7825b3df5243755bd2a9bb7c43f708fb11c4bb3 | 46f3be04a14277e4b015a9bcd7825b3df5243755bd2a9bb7c43f708fb11c4bb3 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-source.test.ts | apps/desktop/src/lib/session-source.test.ts | 3bdd1db07ad1d1629c8a18b5172f0d1a386182c618ef6b7a01e23cb384ea08de | 3bdd1db07ad1d1629c8a18b5172f0d1a386182c618ef6b7a01e23cb384ea08de | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-source.ts | apps/desktop/src/lib/session-source.ts | 74558cdc0ae026022d02ea5ea7a1dd937fb5c823466430f4fe87e7c2e39dd932 | 74558cdc0ae026022d02ea5ea7a1dd937fb5c823466430f4fe87e7c2e39dd932 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-timer-since.test.ts | apps/desktop/src/lib/session-timer-since.test.ts | 55a74ac52664923e10dc87743cc52fe9fe8a97eed7a7511d16d9002dab41c9af | 55a74ac52664923e10dc87743cc52fe9fe8a97eed7a7511d16d9002dab41c9af | verbatim | 원문 바이트 일치 |
| src/hermes/lib/session-timer-since.ts | apps/desktop/src/lib/session-timer-since.ts | 7bbc0ebbe5963be8a056acf05a8b207c4de708a1e60e405b272c90462943615e | 7bbc0ebbe5963be8a056acf05a8b207c4de708a1e60e405b272c90462943615e | verbatim | 원문 바이트 일치 |
| src/hermes/lib/spoken-reply.test.ts | apps/desktop/src/lib/spoken-reply.test.ts | 87609f5b7a0a4b348d652683e5049067ffaba2ab6901cf037913012313334e66 | 87609f5b7a0a4b348d652683e5049067ffaba2ab6901cf037913012313334e66 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/spoken-reply.ts | apps/desktop/src/lib/spoken-reply.ts | b460353aaf8289fe63a2decdc89b078d9e44ecf41575a6c173a61da03245ef30 | b460353aaf8289fe63a2decdc89b078d9e44ecf41575a6c173a61da03245ef30 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/stable-array.ts | apps/desktop/src/lib/stable-array.ts | 05dec9ff8f7f8d5339c17a618a81f0929cddfcfdf5a015a43eecf00d4f159ca0 | 05dec9ff8f7f8d5339c17a618a81f0929cddfcfdf5a015a43eecf00d4f159ca0 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/storage.test.ts | apps/desktop/src/lib/storage.test.ts | ed3bdf053eab3d58ea5f4915ff068aa698ab89fbfa39f77fa9a3c207e54d3b14 | ed3bdf053eab3d58ea5f4915ff068aa698ab89fbfa39f77fa9a3c207e54d3b14 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/storage.ts | apps/desktop/src/lib/storage.ts | 49ae07bdd059fde7d450c55a8aeef31d26d2d2604fa4ef9cb42b99000c79e7cc | 49ae07bdd059fde7d450c55a8aeef31d26d2d2604fa4ef9cb42b99000c79e7cc | verbatim | 원문 바이트 일치 |
| src/hermes/lib/text.ts | apps/desktop/src/lib/text.ts | d5d7ed877f1e1c862978bfe9d6e22423d1c892927bf5932c1ade6bdfc10a7680 | d5d7ed877f1e1c862978bfe9d6e22423d1c892927bf5932c1ade6bdfc10a7680 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/todos.test.ts | apps/desktop/src/lib/todos.test.ts | 42d29cc4d16eb4775732a2007b97c78c9301ae1a07ff12171fb57e10c7d18d08 | 42d29cc4d16eb4775732a2007b97c78c9301ae1a07ff12171fb57e10c7d18d08 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/todos.ts | apps/desktop/src/lib/todos.ts | b95cf48d420f5ac78572248a0197d445b8cfeb2c01045eba9d154f37ef83590b | b95cf48d420f5ac78572248a0197d445b8cfeb2c01045eba9d154f37ef83590b | verbatim | 원문 바이트 일치 |
| src/hermes/lib/tool-result-metadata.ts | apps/desktop/src/lib/tool-result-metadata.ts | 352f53ad4e0a46cad16c5758dbaf88b29c38795fa2b5e7aec349feda0fd845f8 | 352f53ad4e0a46cad16c5758dbaf88b29c38795fa2b5e7aec349feda0fd845f8 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/turn-done-notification-body.test.ts | apps/desktop/src/lib/turn-done-notification-body.test.ts | a8c865d9ae40e48dbd619931e06cf2308f087012766ac565794ba3760a8eac67 | a8c865d9ae40e48dbd619931e06cf2308f087012766ac565794ba3760a8eac67 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/turn-done-notification-body.ts | apps/desktop/src/lib/turn-done-notification-body.ts | 5a15659ed7abbb526559f52d54d7a4dcceb64294051c1d662bf59c839b768bbe | 5a15659ed7abbb526559f52d54d7a4dcceb64294051c1d662bf59c839b768bbe | verbatim | 원문 바이트 일치 |
| src/hermes/lib/with-timeout.test.ts | apps/desktop/src/lib/with-timeout.test.ts | db31608fd17838c3370567b7d3beac0c66f71d443ff1dfab714a6a4eb850e295 | db31608fd17838c3370567b7d3beac0c66f71d443ff1dfab714a6a4eb850e295 | verbatim | 원문 바이트 일치 |
| src/hermes/lib/with-timeout.ts | apps/desktop/src/lib/with-timeout.ts | 80a35ef6f95067a9cfbf707b3cf481244a6534654d1ca36a578292e3346039ea | 80a35ef6f95067a9cfbf707b3cf481244a6534654d1ca36a578292e3346039ea | verbatim | 원문 바이트 일치 |
| src/hermes/platform-bridge.ts | apps/desktop/src/store/gateway.ts | 78315591ca1320f76038a9cc75125f2627d9a3f8cfa8582ddd83820c11cd3603 | 64a4d450d6e5061df6d8b1e8ce422edb678a7bd7325ac5f2e4b48c940ed95938 | shim | API 호출/connection descriptor를 명시 설치·해제; native settings 타입은 선택사항, 실제 웹 호스트 미제공; PB:D10 |
| src/hermes/store/approval-mode.test.ts | apps/desktop/src/store/approval-mode.test.ts | 5f7113fc61bab542cdde683666a698232cfd853832a4d96fa472af1dc266b3e9 | 5f7113fc61bab542cdde683666a698232cfd853832a4d96fa472af1dc266b3e9 | verbatim | 원문 바이트 일치 |
| src/hermes/store/approval-mode.ts | apps/desktop/src/store/approval-mode.ts | d7a0d2691d6fecf77db597d5ec1ea73f5e3c6fb20e562b0b748d0c4cddad4b81 | d7a0d2691d6fecf77db597d5ec1ea73f5e3c6fb20e562b0b748d0c4cddad4b81 | verbatim | 원문 바이트 일치 |
| src/hermes/store/billing-block.ts | apps/desktop/src/store/billing-block.ts | 59aaa5062921d3241d8a80ec9ffa3531a5a11f66a6d679b04c99a783b2a24408 | c0117affc9504c6f6fb49a75bea728e5dcaa38d885dc83e208895810559ba09f | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/clarify.test.ts | apps/desktop/src/store/clarify.test.ts | 39e64e40ec4ae41318b87cfc3b1898358aa0a9ec91a4ef0130fe76762f6f3def | 39e64e40ec4ae41318b87cfc3b1898358aa0a9ec91a4ef0130fe76762f6f3def | verbatim | 원문 바이트 일치 |
| src/hermes/store/clarify.ts | apps/desktop/src/store/clarify.ts | e4520fd99d642b47e419338a92def847bf1a823ac79d2c9cc4bcc3825481203c | e4520fd99d642b47e419338a92def847bf1a823ac79d2c9cc4bcc3825481203c | verbatim | 원문 바이트 일치 |
| src/hermes/store/compaction.test.ts | apps/desktop/src/store/compaction.test.ts | 395abecdbdbc2c9a61a62b73b73500a64ef94749ef4fb33baeb71192f92dfacd | 395abecdbdbc2c9a61a62b73b73500a64ef94749ef4fb33baeb71192f92dfacd | verbatim | 원문 바이트 일치 |
| src/hermes/store/compaction.ts | apps/desktop/src/store/compaction.ts | c40843ebcff04f39ab7c0fbe6139506285e29d70405e974e8806adc5a7b3056a | c40843ebcff04f39ab7c0fbe6139506285e29d70405e974e8806adc5a7b3056a | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer-queue.test.ts | apps/desktop/src/store/composer-queue.test.ts | 48260069661dec03f75fcf095282f3d0ef6cdc8120dd8f16835f491759abbe45 | 48260069661dec03f75fcf095282f3d0ef6cdc8120dd8f16835f491759abbe45 | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer-queue.ts | apps/desktop/src/store/composer-queue.ts | 8f5856d9ec91cd72f9aef05bdf58f1e0cb4e72b09d0849b8374476c771f07cb9 | 59625605c1cafe813a621cd96cb3ab0ed304aaff998f87d865b23efc19b52a99 | patched | Web Locks async callback의 TS 5.9 Promise 중첩 타입을 async 함수 반환으로 평탄화(런타임 의미 동일); PB:674 |
| src/hermes/store/composer-status-drawer.test.ts | apps/desktop/src/store/composer-status-drawer.test.ts | 3846911509d763fa4c6c45c4636c7f31ae7e3134a55494e49a95cc6f02e8c7b5 | 3846911509d763fa4c6c45c4636c7f31ae7e3134a55494e49a95cc6f02e8c7b5 | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer-status-drawer.ts | apps/desktop/src/store/composer-status-drawer.ts | 99e6714cf926909cad53025b28c15bc21752287e0c20847a12600d393b2005f5 | 99e6714cf926909cad53025b28c15bc21752287e0c20847a12600d393b2005f5 | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer-status.ts | apps/desktop/src/store/composer-status.ts | 1f299e75d57d501c94929b30655224d5c76c146527311420cc71d2d562bda4b0 | 1f299e75d57d501c94929b30655224d5c76c146527311420cc71d2d562bda4b0 | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer.test.ts | apps/desktop/src/store/composer.test.ts | d2cd9d7de87dd5b63a1f1d638901f2bcbf143ac49f7f9a90c2b5912836822f5f | d2cd9d7de87dd5b63a1f1d638901f2bcbf143ac49f7f9a90c2b5912836822f5f | verbatim | 원문 바이트 일치 |
| src/hermes/store/composer.ts | apps/desktop/src/store/composer.ts | 2345f865b4f9a9680607432b887f870afa1a6f4f52956a35941692b2cfe3081b | 2345f865b4f9a9680607432b887f870afa1a6f4f52956a35941692b2cfe3081b | verbatim | 원문 바이트 일치 |
| src/hermes/store/connection-registry-state.ts | apps/desktop/src/store/connection-registry-state.ts | e4b30cde542ed0008d3e11ccfc2b8a96a0a2596dbcc66fc2fa5f5ffaf80ee3af | b7870be362dcca985bed573d8d9f5032f31ad771739846fea97c2d0534ec5e6f | patched | native registry 자동 발견 false. 웹 단일 primary, 원본 topology 로직 유지; PB:D10,680 |
| src/hermes/store/desktop-metrics.ts | apps/desktop/src/store/desktop-metrics.ts | a2140ccda06c03b83dc5b6d35aa6ff268ac92eea83a41b929a00761e96d8a7db | be110f7b2d81ac9f6f6e1c883845ba41c921dc9891f37c944e5d9b7f82f94093 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/free-tier-sign-in.ts | apps/desktop/src/store/free-tier-sign-in.ts | 79b53ac501adfaf58964117fa270c87bea24dbaa82081dc6794585fb08a7dfb8 | fc824289ca5fe46eae6ad38bf182b724fb62a8a8558fc2e1f18e70c8daea2be0 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/gateway-activation-prune-lease.test.ts | apps/desktop/src/store/gateway-activation-prune-lease.test.ts | ea87a3be59406f9877faca7fc641ec9fc41602f48ef5dee4a99f7d841e5e1e77 | ea87a3be59406f9877faca7fc641ec9fc41602f48ef5dee4a99f7d841e5e1e77 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-agent-scope.test.ts | apps/desktop/src/store/gateway-agent-scope.test.ts | aa4801f8bd2d127ed27b76d145fd031777ebd5d110227e3669200e3f42724cea | aa4801f8bd2d127ed27b76d145fd031777ebd5d110227e3669200e3f42724cea | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-background-reconnect.test.ts | apps/desktop/src/store/gateway-background-reconnect.test.ts | 3e69a21a7f3068d75ae5417ef13bb27ea44d70f978870d8b669d8b02dc72838d | 3e69a21a7f3068d75ae5417ef13bb27ea44d70f978870d8b669d8b02dc72838d | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-connection-lifecycle.test.ts | apps/desktop/src/store/gateway-connection-lifecycle.test.ts | 3dd004685aea7e587c6240b72f7fd3729978b8ed556b4086fad3a274a33621b2 | 3dd004685aea7e587c6240b72f7fd3729978b8ed556b4086fad3a274a33621b2 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-connection-scope.test.ts | apps/desktop/src/store/gateway-connection-scope.test.ts | bcaf070c5093fbaee01d63714340cf1246790369364bec793439bde7fa05aeec | bcaf070c5093fbaee01d63714340cf1246790369364bec793439bde7fa05aeec | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-foreground-retention.test.ts | apps/desktop/src/store/gateway-foreground-retention.test.ts | 72afd554ee030ee4cbfc58aed9f2d147e6b0004e5f02529aeb058be45eb72e1d | 72afd554ee030ee4cbfc58aed9f2d147e6b0004e5f02529aeb058be45eb72e1d | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-profile-only-owner.integration.test.ts | apps/desktop/src/store/gateway-profile-only-owner.integration.test.ts | 36b31af31d403c547970feeceb02ad0c0fcce7ea73d363057bb94fabec52b4fb | 36b31af31d403c547970feeceb02ad0c0fcce7ea73d363057bb94fabec52b4fb | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-profile-request.test.ts | apps/desktop/src/store/gateway-profile-request.test.ts | 98927f3cdd585378e87a25e85a44f8c35940a2a0ffbcc7986ad9f98ff64a01eb | 98927f3cdd585378e87a25e85a44f8c35940a2a0ffbcc7986ad9f98ff64a01eb | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-reconnect.test.ts | apps/desktop/src/store/gateway-reconnect.test.ts | 2e5d58f9f5cc95eba9f0e7a51966f8bcc976aa8bc2d2720fdf72f0086911b9e2 | 2e5d58f9f5cc95eba9f0e7a51966f8bcc976aa8bc2d2720fdf72f0086911b9e2 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-reconnect.ts | apps/desktop/src/store/gateway-reconnect.ts | 5150b06b388e5ae799270d0ba83af1beb83fdaffd97864dea16d3c22b139d670 | 5150b06b388e5ae799270d0ba83af1beb83fdaffd97864dea16d3c22b139d670 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-relay-retention.test.ts | apps/desktop/src/store/gateway-relay-retention.test.ts | 60395bcf7990a5ce7cac5222bc6582d7ac0a48e5bb2ced515f499f21160c9665 | 60395bcf7990a5ce7cac5222bc6582d7ac0a48e5bb2ced515f499f21160c9665 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-secondary-open-check.test.ts | apps/desktop/src/store/gateway-secondary-open-check.test.ts | 3f6288c703204cdb05f33dfbd7e1fe76c13e16d98916bf78ae5e71ba70a62888 | 3f6288c703204cdb05f33dfbd7e1fe76c13e16d98916bf78ae5e71ba70a62888 | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway-shared-remote.test.ts | apps/desktop/src/store/gateway-shared-remote.test.ts | 166a05c5b93483868bf75993f01d4a94dd6f7accee1ffbbca17d60e4e9043eac | 166a05c5b93483868bf75993f01d4a94dd6f7accee1ffbbca17d60e4e9043eac | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway.test.ts | apps/desktop/src/store/gateway.test.ts | bf0cc993fe32039f1a3eefc50488ff419e7fd0d0e2980a93de0cdb462614fcce | bf0cc993fe32039f1a3eefc50488ff419e7fd0d0e2980a93de0cdb462614fcce | verbatim | 원문 바이트 일치 |
| src/hermes/store/gateway.ts | apps/desktop/src/store/gateway.ts | 78315591ca1320f76038a9cc75125f2627d9a3f8cfa8582ddd83820c11cd3603 | 5bea4860fe7a62739ce24f7146b802f0578f98875746be434f046cf6733a70b1 | patched | window.hermesDesktop를 명시 bridge로 교체; secondary 생성은 supportsSecondaryGateways 없으면 거절; closure의 non-null 좁히기 TS 5.9 호환; PB:676,D10 |
| src/hermes/store/goals.test.ts | apps/desktop/src/store/goals.test.ts | 0c103b02a37428eb2452d7f562b70f5d5a869a740c6d39e17be8c35bd3c120d8 | 0c103b02a37428eb2452d7f562b70f5d5a869a740c6d39e17be8c35bd3c120d8 | verbatim | 원문 바이트 일치 |
| src/hermes/store/goals.ts | apps/desktop/src/store/goals.ts | fe3c0693fd9939bf122a600be7c9b5a2a86a1e8481bcd2c2489ac0d0aa31bb8f | fe3c0693fd9939bf122a600be7c9b5a2a86a1e8481bcd2c2489ac0d0aa31bb8f | verbatim | 원문 바이트 일치 |
| src/hermes/store/interface-mode.ts | apps/desktop/src/store/interface-mode.ts | 6c5d0acfeabbf5f21675754a37d6ceb0ee758f17441f1a08f4cb46a7dd66886f | 4a4cb155765407aff846ea1e4ca5e08f34682211c9b25efb4c67c28b5e177f64 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/live-sync.ts | apps/desktop/src/store/live-sync.ts | d887f162e541d0063a181d33679fa4dd6d5da825ed2c7a23a3bd905684fdf518 | d887f162e541d0063a181d33679fa4dd6d5da825ed2c7a23a3bd905684fdf518 | verbatim | 원문 바이트 일치 |
| src/hermes/store/native-notifications.ts | apps/desktop/src/store/native-notifications.ts | 433211e476a3dc98d0d2f62485bf195256a6ff0e033725084dcf8e1dc3a7380f | 218ae581e47cd6072b470b63946918085ac9009093e8eb881030c908cd6fb122 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/notifications.ts | apps/desktop/src/store/notifications.ts | 5d6eb5a189a60b822df5b6f9a0df09595ef14ca12deb9ad3c80a8a96a31fa9fd | d5bcf05a2e439f2a425e98f55a8a6a2a3dccf447193b6cba1054e56a6dd3934f | patched | 원본 notifications 복사 유지. window 타이머를 globalThis 및 해당 반환 타입으로 변경, Electron logLine 본문 제거; PB:D10,680 |
| src/hermes/store/notify-baseline.ts | apps/desktop/src/store/notify-baseline.ts | 62fb42e5752fd2d8add6700ab1249887bf8135cce7174e7e90eea10de6d847c7 | 62fb42e5752fd2d8add6700ab1249887bf8135cce7174e7e90eea10de6d847c7 | verbatim | 원문 바이트 일치 |
| src/hermes/store/onboarding-session.ts | apps/desktop/src/store/onboarding-session.ts | 70e249a1b447e65fbf621bfb0d7df4a4b850eb96b88fd20ae47a1e4d6357ba57 | 08e2ce247afc326778ec70d4725770a574166b180fa2089133ce7a4234ff62d8 | shim | 온보딩 세션 없음 및 boolean false atom. credential 온보딩 제외; PB:680 |
| src/hermes/store/onboarding.ts | apps/desktop/src/store/onboarding.ts | 640d460554e8b1c3231e8d14a22d35912857faf9a86db1bbb2b37d937e8fa637 | d44ca9c0bf1c99304b537ccaea877f76cb0217da90d41cbc8a851873cbdaa871 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/parked-card-answers.ts | apps/desktop/src/store/parked-card-answers.ts | 2b10feb66c8529003038e84f9b69e7fa891adfb9acdaa1dd0a3de6bd9581008a | 2b10feb66c8529003038e84f9b69e7fa891adfb9acdaa1dd0a3de6bd9581008a | verbatim | 원문 바이트 일치 |
| src/hermes/store/pending-submissions.test.ts | apps/desktop/src/store/pending-submissions.test.ts | 6a0b213ce276b48c0dd793a7c1f003c42b3f88e1e0ef2d876adc32ab1ca1afe5 | 6a0b213ce276b48c0dd793a7c1f003c42b3f88e1e0ef2d876adc32ab1ca1afe5 | verbatim | 원문 바이트 일치 |
| src/hermes/store/pending-submissions.ts | apps/desktop/src/store/pending-submissions.ts | eda44405fe14786a34ae68adf61e6ccee4620c604af1ab1e8749164f798edda1 | eda44405fe14786a34ae68adf61e6ccee4620c604af1ab1e8749164f798edda1 | verbatim | 원문 바이트 일치 |
| src/hermes/store/pet.ts | apps/desktop/src/store/pet.ts | 055213af2cf10a83ded4d247ed0a333002ff7d57d75615c179133edb16c9a0cb | 42cc5da3768eb77b9d52c8309371c203c2de8d9d92afca0db6569eab1d7a85d8 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview-ownership.ts | apps/desktop/src/store/preview-ownership.ts | fa97126d0f88029b22e86270daa619c97145879bbec71c03bf2eb164e85e83e9 | 6c7247f8f35685a0e71b1fb544da6f11f6466330c8c4228881d4bb3c35b45840 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview-status.ts | apps/desktop/src/store/preview-status.ts | 0b7d83caac260e625ca8f98548888e807a6b19fe86f5e26d04dae7bfeb36caae | 01d3181fce0d541e765f02ed90a62727cf430b8b26e9ed2436662f2bb80f9e1f | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/preview.ts | apps/desktop/src/store/preview.ts | 91e7d945bb8bb07d3fc17b4a4ff6d85981133c8062c41eb880064e1b502cd8ea | dc90b45e22949417a15f12908d55b5a8b20c1d9e876a4aa29025d49d5d253f9e | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/profile.ts | apps/desktop/src/store/profile.ts | 55d064cdff86ca18dab3dae7d26da01d54ecfa33ad5ca99fb233fa2f60fa897d | a3e435566f7434774adc2586019f8f57846c885587a70750fd30debbe7d76122 | shim | normalizeProfileKey 원문 발췌 및 default profile atom; native hydration/전환 효과 제외; PB:680 |
| src/hermes/store/project-tree.ts | apps/desktop/src/store/project-tree.ts | 21e16d6405349cfebc7d74c4f673bb4c3d619ea2231a1cf2bc109e8272ab4d69 | 21e16d6405349cfebc7d74c4f673bb4c3d619ea2231a1cf2bc109e8272ab4d69 | verbatim | 원문 바이트 일치 |
| src/hermes/store/projects.ts | apps/desktop/src/store/projects.ts | 024dcd5aeaea8d97f7829050c657ce35ff15fb9d3bb2b206036f5460547e2af0 | 79a0665e5c83d7ef1e91540b9f93eb8dda13a7fb9393875691379331de643b69 | shim | projectTree 재수출, followActiveSessionCwd 효과 중단; 프로젝트 목록/자동 refresh 제외; PB:680 |
| src/hermes/store/prompts.ts | apps/desktop/src/store/prompts.ts | aa21821e29321f643fec48d8d530f9423a362996779ae28226cdb6bc81fc6eb0 | d4838c880a4477644570a9b8c35c1814a6dc0fae8b7c4aafc5e840a965c2611e | patched | approval 원본 유지. sudo/secret/vault 입구는 typed disabled store로 거절, pending 상태 저장 불가; PB:680 PromptOverlays 비활성 |
| src/hermes/store/provider-wait.test.ts | apps/desktop/src/store/provider-wait.test.ts | 0d8b06d93ecadfd08693b0bec04ce66ff92ba4602fc857d432689cd718a7194e | 0d8b06d93ecadfd08693b0bec04ce66ff92ba4602fc857d432689cd718a7194e | verbatim | 원문 바이트 일치 |
| src/hermes/store/provider-wait.ts | apps/desktop/src/store/provider-wait.ts | aae74517f440f5a9ee0be3d5c31183f778132b7d7ae8d4b5f71a587a3af9e217 | aae74517f440f5a9ee0be3d5c31183f778132b7d7ae8d4b5f71a587a3af9e217 | verbatim | 원문 바이트 일치 |
| src/hermes/store/recovery-requests.ts | apps/desktop/src/store/recovery-requests.ts | 6cc6c513dde0e6f83a0b7a0ec513df812f964218c0480a9beea1177c1d8dcf6c | 6cc6c513dde0e6f83a0b7a0ec513df812f964218c0480a9beea1177c1d8dcf6c | verbatim | 원문 바이트 일치 |
| src/hermes/store/runtime-gone.test.ts | apps/desktop/src/store/runtime-gone.test.ts | cf19705566a025915239bc10084c7a2d499a5863e31a3d7db2385c2bc2f6da92 | cf19705566a025915239bc10084c7a2d499a5863e31a3d7db2385c2bc2f6da92 | verbatim | 원문 바이트 일치 |
| src/hermes/store/runtime-gone.ts | apps/desktop/src/store/runtime-gone.ts | 2fb67186ee81b7c3268b723b9159eed75384288807f3cd37e63ef2b906496f6b | 2fb67186ee81b7c3268b723b9159eed75384288807f3cd37e63ef2b906496f6b | verbatim | 원문 바이트 일치 |
| src/hermes/store/server-requests.ts | apps/desktop/src/store/server-requests.ts | fb9e3fc124a3ba7ebf4a82c23902cc2712c9a7dd96968d6c5f8daff600810666 | fb9e3fc124a3ba7ebf4a82c23902cc2712c9a7dd96968d6c5f8daff600810666 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-control.test.ts | apps/desktop/src/store/session-control.test.ts | 57a95a355eec3ca2143939432a5257921b08d9a3e6202723562f78e4517960cb | 57a95a355eec3ca2143939432a5257921b08d9a3e6202723562f78e4517960cb | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-control.ts | apps/desktop/src/store/session-control.ts | 5112b32b2a3e21fb5cdb5de957123df2dfb560745442fc8fc08dda38c12e567e | 5112b32b2a3e21fb5cdb5de957123df2dfb560745442fc8fc08dda38c12e567e | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-event-provenance.ts | apps/desktop/src/store/session-event-provenance.ts | 4e944ff30501ad289bca41d440dc04672e058c4820454ebc0d0dbf8ad152c19a | 4e944ff30501ad289bca41d440dc04672e058c4820454ebc0d0dbf8ad152c19a | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-focus.ts | apps/desktop/src/store/session-focus.ts | 8d36d5af01f560ff5650a9a455f3c2f2f91341123296fb9111270fd167935b29 | 8d36d5af01f560ff5650a9a455f3c2f2f91341123296fb9111270fd167935b29 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-gone-latch.ts | apps/desktop/src/store/session-gone-latch.ts | 831c1276238ba9388beccc8caeeef85e1cc9fd1d8a75828c200f887ce294d682 | 831c1276238ba9388beccc8caeeef85e1cc9fd1d8a75828c200f887ce294d682 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-no-reply.ts | apps/desktop/src/store/session-no-reply.ts | f0fc6fca29007813a70915a2fa0fa76eebe4365810cbcb8a2c930ffd59a3058f | f0fc6fca29007813a70915a2fa0fa76eebe4365810cbcb8a2c930ffd59a3058f | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-owner-holds.ts | apps/desktop/src/store/session-owner-holds.ts | c91e0289915a2ed75f011e875282b538f0c6f0f5a6b152051f492b42deedcb16 | c91e0289915a2ed75f011e875282b538f0c6f0f5a6b152051f492b42deedcb16 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-owner-resolution.ts | apps/desktop/src/store/session-owner-resolution.ts | ea53882bedaf9f88024c29e139d3d431ea91dc5a54d6fe24923c312fa17aca97 | ea53882bedaf9f88024c29e139d3d431ea91dc5a54d6fe24923c312fa17aca97 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-removal.test.ts | apps/desktop/src/store/session-removal.test.ts | ccdf6f9e049ff1534d8a7fd25bfa80174d8b45cb581a2ba503bc804dbe73502e | ccdf6f9e049ff1534d8a7fd25bfa80174d8b45cb581a2ba503bc804dbe73502e | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-removal.ts | apps/desktop/src/store/session-removal.ts | 12263934e4bd10bfdc2b4eed7ba69ef3dd5cedf2c5f9f025f1fa7a275d629523 | 12263934e4bd10bfdc2b4eed7ba69ef3dd5cedf2c5f9f025f1fa7a275d629523 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-request-router.test.ts | apps/desktop/src/store/session-request-router.test.ts | 0cd167d7e416477115d50c4bd77143101714702783b365d365b5ea113c8e599d | 0cd167d7e416477115d50c4bd77143101714702783b365d365b5ea113c8e599d | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-request-router.ts | apps/desktop/src/store/session-request-router.ts | 60bbb2a1e63405d95a8639f6b8d78c9a4fa50f97e1b749493bcf9a3f2dafb8f6 | 60bbb2a1e63405d95a8639f6b8d78c9a4fa50f97e1b749493bcf9a3f2dafb8f6 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-states.ts | apps/desktop/src/store/session-states.ts | 9e89136510ac99f854143e025ca4f0ca98995ba3cdd86a15da4997a23f18460a | 9e89136510ac99f854143e025ca4f0ca98995ba3cdd86a15da4997a23f18460a | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-sync.ts | apps/desktop/src/store/session-sync.ts | 5deda2136be9dbabc238618da6b2b5e886bb81cd37b049be8ca78750c461816f | 0c5a1d1827c77466c0a562f54f4117a44d056dc1168ff326855b920e8a8d7a1d | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/session-unread-remote.test.ts | apps/desktop/src/store/session-unread-remote.test.ts | 5e61e07434ec825f56d71888bd2ae2f1425bea3596c9f85ca5710a0f06e94a59 | 5e61e07434ec825f56d71888bd2ae2f1425bea3596c9f85ca5710a0f06e94a59 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-unread-remote.ts | apps/desktop/src/store/session-unread-remote.ts | 6bde1275a47ec93d6fe68885536bbfa774bdc3f18883d3a1100dc04fc034d0f6 | 6bde1275a47ec93d6fe68885536bbfa774bdc3f18883d3a1100dc04fc034d0f6 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-unread.test.ts | apps/desktop/src/store/session-unread.test.ts | 9f23dc1b32aa32e49939c7f65944f4c59841c18d330c4ce76ece693ce93ad9ac | 9f23dc1b32aa32e49939c7f65944f4c59841c18d330c4ce76ece693ce93ad9ac | verbatim | 원문 바이트 일치 |
| src/hermes/store/session-unread.ts | apps/desktop/src/store/session-unread.ts | d0e74b0841b84d4e15f215e09eb892160133cf4be727f4d63abf3e36bf3cf7c3 | d0e74b0841b84d4e15f215e09eb892160133cf4be727f4d63abf3e36bf3cf7c3 | verbatim | 원문 바이트 일치 |
| src/hermes/store/session.test.ts | apps/desktop/src/store/session.test.ts | 5a17f32f15ae2208d97fdaded349dd5dee362bdc76ac30c3d35696818be447ae | 5a17f32f15ae2208d97fdaded349dd5dee362bdc76ac30c3d35696818be447ae | verbatim | 원문 바이트 일치 |
| src/hermes/store/session.ts | apps/desktop/src/store/session.ts | 25aafdfe866c4a7d9927616ff51ce0cef83cef57cfa03d8226c4f40fdd835952 | 1f44fbc31331231910f0f2a18d43047acd3aad9d3ea683ba04ff5cb69c32cb2e | patched | native settings/sanitizeWorkspaceCwd를 optional bridge 뒤로 이동. WebHost는 제공하지 않아 기존 early-return 유지; PB:674,D10 |
| src/hermes/store/stream-delta-flush.ts | apps/desktop/src/store/stream-delta-flush.ts | e8df773e1f08ad4f60509a9ca305d2bf2248ecc90641dab308b215fb3428cfdc | e8df773e1f08ad4f60509a9ca305d2bf2248ecc90641dab308b215fb3428cfdc | verbatim | 원문 바이트 일치 |
| src/hermes/store/subagents.test.ts | apps/desktop/src/store/subagents.test.ts | f8ba1d14971554ed735cf0347b28952bc2c5dd7560e7733fcf392c9b36c99512 | f8ba1d14971554ed735cf0347b28952bc2c5dd7560e7733fcf392c9b36c99512 | verbatim | 원문 바이트 일치 |
| src/hermes/store/subagents.ts | apps/desktop/src/store/subagents.ts | d9539966e3b037c7946fa9988341b982576cb8ceb58183160159212c77d791fe | d9539966e3b037c7946fa9988341b982576cb8ceb58183160159212c77d791fe | verbatim | 원문 바이트 일치 |
| src/hermes/store/todos.test.ts | apps/desktop/src/store/todos.test.ts | 417e3f745ba76e079399b37f33e8503b420c8472d95c2af7a527522174775f36 | 417e3f745ba76e079399b37f33e8503b420c8472d95c2af7a527522174775f36 | verbatim | 원문 바이트 일치 |
| src/hermes/store/todos.ts | apps/desktop/src/store/todos.ts | dfc29432a3f3f36a2972d2e090e06c97e48ca08b12603c85c27d4db1b6ff367d | dfc29432a3f3f36a2972d2e090e06c97e48ca08b12603c85c27d4db1b6ff367d | verbatim | 원문 바이트 일치 |
| src/hermes/store/transcript-sync.ts | apps/desktop/src/store/transcript-sync.ts | c36ad608cd6cc23fa8dc8eca69fd391882b0b3dbeb2eb9007211d6b4d9db81fc | ee0b0dde944c18c971b3df8149e19fa6d2d44bb1899108436baac92c8f5cfb50 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/transcript-tail-cache.test.ts | apps/desktop/src/store/transcript-tail-cache.test.ts | 9654018eff76f5aabdaf99e11bc48e178be94b632e116033518ea986613fda57 | 9654018eff76f5aabdaf99e11bc48e178be94b632e116033518ea986613fda57 | verbatim | 원문 바이트 일치 |
| src/hermes/store/transcript-tail-cache.ts | apps/desktop/src/store/transcript-tail-cache.ts | eee17d853bb65da8e29d7315e593224255dabc8206ca6c3649f158b9551f8a6f | eee17d853bb65da8e29d7315e593224255dabc8206ca6c3649f158b9551f8a6f | verbatim | 원문 바이트 일치 |
| src/hermes/store/transcript-tail.test.ts | apps/desktop/src/store/transcript-tail.test.ts | a9ad6fce7fe24b530b58d7ae44b9e083e9a7b7de55f105ab1d35a0e1c6714727 | a9ad6fce7fe24b530b58d7ae44b9e083e9a7b7de55f105ab1d35a0e1c6714727 | verbatim | 원문 바이트 일치 |
| src/hermes/store/transcript-tail.ts | apps/desktop/src/store/transcript-tail.ts | d9c418456cd98f761548e0eded1cf85a97c5c80e340546265c3a47f9b4d72b02 | d9c418456cd98f761548e0eded1cf85a97c5c80e340546265c3a47f9b4d72b02 | verbatim | 원문 바이트 일치 |
| src/hermes/store/updates.ts | apps/desktop/src/store/updates.ts | 3e0f0250446381d4fbb9f76a79759d78b634fac46eb484ca53f32c129c74542f | a5e28c353b9b48279bede2dce1be4428351b7650ff6a2e8fe33a74292c942f87 | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/store/windows.ts | apps/desktop/src/store/windows.ts | 10e64b84b133acd5dd5b2055b5bbdf3c01b7931560ee6effebc3e22486c46277 | 4ef3df6402b81bb47f418d3a108b3f50dd04ed9d2b7129053196881af61d9c60 | shim | 별도 native browser/secondary 창이 아닌 주 세션 호스트(false/false). native 창 열기 불가; PB:D10,680 |
| src/hermes/test/deferred.ts | apps/desktop/src/test/deferred.ts | db69333e10751fe309aeea0aed3b255d81c43a714291be21214ca35613a4ffab | db69333e10751fe309aeea0aed3b255d81c43a714291be21214ca35613a4ffab | verbatim | 원문 바이트 일치 |
| src/hermes/test/session-info.ts | apps/desktop/src/test/session-info.ts | 1836817156b9b9e1af5269e3bd6316456e6897240db23c511661afd1ec1586cf | 1836817156b9b9e1af5269e3bd6316456e6897240db23c511661afd1ec1586cf | verbatim | 원문 바이트 일치 |
| src/hermes/themes/backend-sync.ts | apps/desktop/src/themes/backend-sync.ts | 627e83c22ad7f61230d1d746b1fa1ad009d234262e18c0b7876808710ffdf302 | 6e0b88aa95eb1e3c54d0b373ebbe33b64ce4ed9b7bc99745c224516c77fd2c8b | shim | 표시/타입/미구현 native 효과 경계. 원본 export의 사용 계약만 유지; PB:674,680,D10 |
| src/hermes/types/hermes.ts | apps/desktop/src/types/hermes.ts | 37c10b12fe1f6a3af841edd20d117cae3dfb86ebfe506312dab85d13fbcfd795 | 37c10b12fe1f6a3af841edd20d117cae3dfb86ebfe506312dab85d13fbcfd795 | verbatim | 원문 바이트 일치 |
| src/platform/capability-gate.test.ts | 신규/console 설정 | — | 3ea02243258cb7dfa45c3f87ac54a4e8614d2e576398fd6e3e769a342307f24e | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/capability-gate.ts | 신규/console 설정 | — | d663df441dd83af46fced3aed317d8a361c74172c7f5c2ef6ac947fc12ceed03 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/fake-websocket.ts | apps/shared/src/json-rpc-gateway-legacy-replay.test.ts | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | 4ac6b30760340f81ef04cdd437b2a997febd11af279c411874eda7dca388bd16 | patched | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |
| src/platform/hermes-node-setup.ts | 신규/console 설정 | — | e7f047703d7101fa17564a6004801eaae4b15a1047da7c2e959634b78f54865a | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/rest-capability.test.ts | 신규/console 설정 | — | 20fd87317e21d296df1fce57e49c34ddb8832a12c5a6f6b76ff51b76102ef780 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/store-host.test.ts | 신규/console 설정 | — | c419cea387d6d6be2e14697a10579497136e4f13cca1ddbd38f767c3e8736a45 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/store-host.ts | 신규/console 설정 | — | 205775930544f394510b6f72924cbb47f77e03edea3206b450401b158670d9cd | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/test-setup.ts | 신규/console 설정 | — | d27a3be1f7e8bc0f7beed7f59ce3636f46da90a6c8e0efe06f1ec1584827c119 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/web-host.test.ts | 신규/console 설정 | — | bccacdac3d05060e3926c89530599c91a8cb35aa2df5b7fb8e8bd9866567e78f | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/web-host.ts | 신규/console 설정 | — | 143389cd0b2ab9e0001a302ccf9e97ba52391eeca25905fc4be47c71d53ade00 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/platform/yield-manifest.generated.json | 신규/console 설정 | — | 160312a0034bbda5ae127c47ac18d9146efb15732b15082d803bf965d3a55f4d | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| src/protocol/LICENSE-HERMES-NOTICE.md | LICENSE | 821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6 | 8fecc32f90572499f38b8b2d5a14a4c1e450282579e4609b78023884a915e1dd | patched | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |
| src/protocol/backend-scope.ts | apps/shared/src/backend-scope.ts | fd6998b8f0d4b6969f3147a19d7a1c93c2603f26cddc930b86a9e2e58ff34a3f | fd6998b8f0d4b6969f3147a19d7a1c93c2603f26cddc930b86a9e2e58ff34a3f | verbatim | 원문 바이트 일치 |
| src/protocol/gateway-contract.generated.ts | apps/shared/src/gateway-contract.generated.ts | c3c8f86a909e3a763af19b3d9af168018e88373fa38ba60b475b45c243d3aa9a | c3c8f86a909e3a763af19b3d9af168018e88373fa38ba60b475b45c243d3aa9a | verbatim | 원문 바이트 일치 |
| src/protocol/gateway-events.ts | apps/shared/src/gateway-events.ts | 0c44bbb5b2c76a5b355654438d0cb7a77bfe85b592b7fb89d17b3f25d271ed00 | 0c44bbb5b2c76a5b355654438d0cb7a77bfe85b592b7fb89d17b3f25d271ed00 | verbatim | 원문 바이트 일치 |
| src/protocol/index.ts | apps/shared/src/index.ts | cbfeb921966ce1558edccaab042a44075ee38e22174b66f1778dec99fad6744b | c8afff2ef0db58044bcd625c36f59e9a6a758029d3b19f407ced3e18d3c46fb7 | patched | 원본 export/순수 코드 발췌 및 웹 경계; PB:665,674,676,D10 |
| src/protocol/json-rpc-channel.test.ts | apps/shared/src/json-rpc-channel.test.ts | 5e9edfedb3c1a85201f638617b473466b72197b56320c5ccce5bc5d36cec9e0b | 5e9edfedb3c1a85201f638617b473466b72197b56320c5ccce5bc5d36cec9e0b | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-channel.ts | apps/shared/src/json-rpc-channel.ts | d47042e66521292781b4c46c1a5b6ff8171b41b82ec69db9e27e98faa9bcc34b | d47042e66521292781b4c46c1a5b6ff8171b41b82ec69db9e27e98faa9bcc34b | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-connect.test.ts | apps/shared/src/json-rpc-gateway-connect.test.ts | 1146b64f5c55c927e575ffa5f2d5e63f77bd09dbeeefc2353b18d607f7830c81 | 1146b64f5c55c927e575ffa5f2d5e63f77bd09dbeeefc2353b18d607f7830c81 | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-legacy-replay.test.ts | apps/shared/src/json-rpc-gateway-legacy-replay.test.ts | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | 8e2c082fc09bbefbb062e4ba4d23e123a3abe7a03340dd5a9254ed541f51ac5a | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway-replay.test.ts | apps/shared/src/json-rpc-gateway-replay.test.ts | 8b09276d99f64868e21f3b92b1b532df72aee93acb4c066fab1845f934823e17 | 8b09276d99f64868e21f3b92b1b532df72aee93acb4c066fab1845f934823e17 | verbatim | 원문 바이트 일치 |
| src/protocol/json-rpc-gateway.ts | apps/shared/src/json-rpc-gateway.ts | c943f1fbc2d456e8d6c831596c17a11ae55a1edb1a0855bea052c57cc0d57bef | c943f1fbc2d456e8d6c831596c17a11ae55a1edb1a0855bea052c57cc0d57bef | verbatim | 원문 바이트 일치 |
| src/protocol/reconnect-backoff.test.ts | apps/shared/src/reconnect-backoff.test.ts | 3156b1815b8be9eedfa3c6d3c4800add121b731f02bf0796d34154971981f4ea | 3156b1815b8be9eedfa3c6d3c4800add121b731f02bf0796d34154971981f4ea | verbatim | 원문 바이트 일치 |
| src/protocol/reconnect-backoff.ts | apps/shared/src/reconnect-backoff.ts | 066a9efab5237e47b0b892ef72ec129485622a45ff8e1b964e278999c8466328 | 066a9efab5237e47b0b892ef72ec129485622a45ff8e1b964e278999c8466328 | verbatim | 원문 바이트 일치 |
| src/protocol/session-http-mutations.ts | apps/shared/src/session-http-mutations.ts | aa11b143d249678c102b0d00b29062ec6fc84f28ec5acbfd3a2ab038b4e81a25 | aa11b143d249678c102b0d00b29062ec6fc84f28ec5acbfd3a2ab038b4e81a25 | verbatim | 원문 바이트 일치 |
| src/protocol/skill-scaffold.test.ts | apps/shared/src/skill-scaffold.test.ts | 283e8bd86615d04d4a8a7e1427000b8bc85fce5358ec1caa7bb40869bac8d8b0 | 283e8bd86615d04d4a8a7e1427000b8bc85fce5358ec1caa7bb40869bac8d8b0 | verbatim | 원문 바이트 일치 |
| src/protocol/skill-scaffold.ts | apps/shared/src/skill-scaffold.ts | fb2ed52d1ed50cab8439e7f275e04ace702e888dcd51a39e98eed5bce92064de | fb2ed52d1ed50cab8439e7f275e04ace702e888dcd51a39e98eed5bce92064de | verbatim | 원문 바이트 일치 |
| src/protocol/slash.test.ts | apps/shared/src/slash.test.ts | 57f02f7a084c0f39533006b39b6f5d8ee5bc23e4bbdbbad8b0f67e41c4ca249c | 57f02f7a084c0f39533006b39b6f5d8ee5bc23e4bbdbbad8b0f67e41c4ca249c | verbatim | 원문 바이트 일치 |
| src/protocol/slash.ts | apps/shared/src/slash.ts | 44118cc7d445af3331999ed354fb85fa160705baab8804787e974bb7be84e869 | 44118cc7d445af3331999ed354fb85fa160705baab8804787e974bb7be84e869 | verbatim | 원문 바이트 일치 |
| src/protocol/usage-tick.ts | apps/shared/src/usage-tick.ts | 3650c7addb4b2e91ee78fb5ebf4ba34f35e9a30c4515151940eda84d56bb9c6a | 3650c7addb4b2e91ee78fb5ebf4ba34f35e9a30c4515151940eda84d56bb9c6a | verbatim | 원문 바이트 일치 |
| src/protocol/websocket-url.ts | apps/shared/src/websocket-url.ts | a4282b7d610d3c022a8dbcb77597b85497885eec606f6f09a354d373fea3d10c | a4282b7d610d3c022a8dbcb77597b85497885eec606f6f09a354d373fea3d10c | verbatim | 원문 바이트 일치 |
| tests-integration/README.md | 신규/console 설정 | — | 736d6ef1b32831d83c1de9febdd91642d4e43029f9c8fe23fae1a1bb811d641c | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| tests-integration/gateway.itest.ts | 신규/console 설정 | — | 73d443bc3c0a291fc2e35f3788fcb28d20508da2c65cf6184e170d2fe6a02eac | new | ACK 유실 후 기본 resume → 동일 ID 재전송 순서 검증·RPC 오류 진단; PB:665,676,D10 |
| tests-integration/global-setup.ts | 신규/console 설정 | — | e661b271a202e797571c93d56d857c8634856c878c8938ebce7ed04d97cf9535 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| tests-integration/pr8_gateway_server.py | 신규/console 설정 | — | 23d09d87ccbad18048712a9f4f613c33a76e01d048e4a325d4b165653b51afcf | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| tests-integration/tsconfig.json | 신규/console 설정 | — | 2229a59399157782cb917cdc04c30c43a0b6681334beea60604efd7f8e2e803a | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| tsconfig.json | 신규/console 설정 | — | c8c9d4abbc22b3584a3b33b4f63e2180b4ad1c924a636ed8b365ed92cbc9782a | patched | Hermes 외 기존 console 설정. package는 scripts만, tsconfig는 paths만 변경; 의존성 불변 |
| vitest.config.ts | 신규/console 설정 | — | 28680eb583a5c01e544d6de8727fbf246a7721b8f38aa6044a98ce99f2a2efe2 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| vitest.integration.config.ts | 신규/console 설정 | — | 13fafb194a8d8334a38173db87540f437844cb59f6c8383310c1e9836cf18553 | new | 명시 웹 연결·manifest gate·Node fixture·통합 검증 설정; PB:665,676,D10 |
| tests-integration/real-model.itest.ts | 신규/console 설정 | — | bed581f64c16f8bdacdb21c8f6a991941ceb282e49e36ed73aa274672562848f | new | 실제 모델 opt-in fixture/config/scenario; PR-7 lifespan·guard 재사용 |
| tests-integration/real-model-setup.ts | 신규/console 설정 | — | 2214fd867137098f5bc68d95e83b8ad67719b0b9a47da323322766ca185fb0b1 | new | 실제 모델 opt-in fixture/config/scenario; PR-7 lifespan·guard 재사용 |
| tests-integration/pr8_real_model_server.py | 신규/console 설정 | — | cbb95a1df5087edf6fb9bf8a44d587a341ffdcf52c3a665f3460d254ed5d79d0 | new | 실제 모델 opt-in fixture/config/scenario; PR-7 lifespan·guard 재사용 |
| vitest.real-model.config.ts | 신규/console 설정 | — | 6843c1b6f36bc8d740fa7087cb171f2fe555434892c2ad73bd9ab74e01ddd6c4 | new | 실제 모델 opt-in fixture/config/scenario; PR-7 lifespan·guard 재사용 |

## PR-8b 실제 모델 opt-in 최종 보완 (2026-10-25)

- 사용자 보고: 기존 Mongo 통합 11개 모두 통과. gateway.itest.ts는 그대로 유지했다.
- 신규 4파일: real-model.itest.ts, real-model-setup.ts, pr8_real_model_server.py,
  vitest.real-model.config.ts. 위 SHA-256 표에 모두 new로 기록했다.
- 변경: package.json의 별도 test:integration:real script, 기본 integration config의
  real 파일 제외, 통합 tsconfig include, 공유 global setup/서버의 opt-in 확장,
  tests-integration/README.md와 이 문서. harness/·이식 src/·기존 console README 불변.
- PR-7 smoke의 실제 lifespan/ctrl/registry guard 구성을 재사용하고 기존 PR8
  resolver·ticket/origin·ObserveWire·main을 사용한다. 기본 setup은 환경과 무관하게
  OfflineController를 선택하며, 실제 helper는 PR8_REAL_MODEL=1을 요구한다.
- 질문 하나, ACK 폐기 뒤 동일 submission 재전송 하나만 수행한다. 획득된 _run
  실행을 계수해 두 번째 실행을 차단한다. API HTTP 호출 수 1회를 보장하는 것은 아니다.
- store 완료/비어 있지 않은 응답/seq/새 연결 replay/terminal receipt/실행 1회/
  blocked 0개를 단언한다. read_result 예외는 PR-7 guard와 동일하다.
- 민감 진단은 폐기/고정 단계명으로 대체한다. 키·환경 값 파일 기록 없음.
  실제 ctrl.close 종료 후 DB finally drop, worker PID 그룹 추적 및 TERM/KILL fallback.
  강제 종료나 Mongo 장애 시 DB 삭제는 보장할 수 없다.
- 실행 검증: 통합 소스 포함 tsc 통과, Vitest 88파일/1,036개 통과, npm test 10개 통과,
  npm run build -- --outDir /tmp/pr8-real-model-build 통과, Python helper 2개 py_compile 통과.
  pycache는 /tmp/pr8-real-model-pycache에 출력했다. 통합/실모델은 실행하지 않았다.
- 실제 provider 동작과 store/replay 시점, native_worker 및 DB teardown 검증은
  사용자 실행에 남는다. 실행법과 환경변수 이름은 tests-integration/README.md 참조.
- 이 문서 자체는 재귀 해시를 피하려 SHA 표·파일 집계에서 제외한다. 커밋·push 없음.

## PR-8c — 자동 attach의 deferred history 지원 (2026-10-25)

이 절이 PR-8b fix2의 defer_history 미지원/수동 resume 우회 설명을 대체한다.
Mongo·소켓·실서버·실제 모델 통합 검증은 작성자 미실행이며 사용자 실행 대기다.

### 설계 및 작업 순서

1. 원본 resume/계약/클라이언트를 대조하고 소켓 없는 재현 테스트를 먼저 작성한다.
2. `session_rpc.py`의 source/defer_history만 허용하고 기존 snapshot·소유권·replay 순서를 보존한다.
3. 자동 attach 우회를 제거하고 REST hydration·Mongo finalized 테스트를 추가한다.
4. 단위/TypeScript/Vitest/기존 테스트/build 및 manifest 재생성 비교와 파일 해시를 검증한다.

### 원본 근거와 구현 매핑

기준은 읽기 전용 `/tmp/hermes-agent-ro`, commit `5894e750`이다.

| 원본 file:line | PR-8c 대응 |
|---|---|
| `apps/desktop/src/app/session/hooks/use-session-actions/resume-owner.ts:117-132` | desktop cold params(source, cols, defer_history, omit_messages), 통합 k에서 양 모드 검증 |
| `tui_gateway/agent_factory.py:34-38`, `methods_session.py:48-55,620-628` | source는 desktop에 한정된 enum이 아닌 문자열; Yield는 surface hint를 수용하되 저장된 source/실행 설정은 변경하지 않음 |
| `tui_gateway/methods_session.py:885-890,899-914` | deferred 응답의 messages=[], message_count, hydrating; 기존 snapshot 필드 유지 |
| `tui_gateway/methods_session.py:958-978,1074-1077` | deferred cold 흐름; Yield에는 background agent/history build가 없으므로 hydrating=false |
| `tui_gateway/contracts/sessions.py:185-195` | legacy source/defer_history/omit_messages/cols/inline_images 계약 |
| `tui_gateway/contracts/canonical_sessions.py:62-74,133-156` | authority source/defer_history/omit_messages/cols 및 epoch/subscription/pending/prompts snapshot 유지 |
| `tests/tui_gateway/test_resume_readonly_mount.py:59-73` | False/True resume 모두 ended_at/end_reason 및 전체 저장 row 불변 |
| `tests/tui_gateway/test_protocol.py:827-870`, `methods_session.py:842-866` | 원본 tip_only guard 조사; 아래 대형 세션 한계 참조 |
| `tests/tui_gateway/test_tui_gateway_server.py:3681-3830`, `test_deferred_model_history.py:76-154` | 재시도 시 같은 저장 세션·하나의 구독, 실패 후 재시도 검증. 원본 background worker 취소/교체는 Yield에 없으므로 이식하지 않음 |
| `tests/tui_gateway/test_resume_profile_scope.py` | wire profile 무시, 연결 principal의 저장소와 서버 profile 사용; 타 사용자와 없는 ID는 같은 not_found |

### 지원 및 거절 값

| 파라미터 | 처리 / 이유 |
|---|---|
| session_id | 기존 principal 소유 세션만; 존재 확인이 옵션 처리보다 먼저 |
| source | 문자열 또는 null/없음 수용. desktop/telegram/cli 등 문자열을 허용. row source·runtime·profile 변경 없음 |
| defer_history=true | messages=[], 원래 projection의 message_count, hydrating=false. running 및 authority의 last_sequence/replay_epoch 등 유지 |
| defer_history=false/없음 | 기존 snapshot 응답 유지(hydrating/status를 새로 추가하지 않음) |
| omit_messages | 기존 boolean 계약. deferred일 때 값과 관계없이 빈 messages; legacy deferred 응답은 원본 _resume_response처럼 messages_omitted 대신 hydrating 사용 |
| cols, inline_images | 기존 수용 유지. Yield public projection은 text만 반환하므로 터미널 폭/이미지 렌더링 효과를 만들지 않음 |
| profile/profile_id/principal/principal_id | 기존 connection 경계에서 제거; 연결 principal/server profile이 권한 기준 |
| eager_build | 실제 agent eager build 없음 → -32005 |
| lazy | live mirror/관찰자 소유권 PR-7 미결정 → -32005 |
| workspace | 사용자 작업공간 변경 및 latest 검색 범위 미구현 → -32005 |
| editor | editor 통합/수명 관리 미구현 → -32005 |
| api_key | 클라이언트 launch key 주입/재공급 미지원 → -32005 |
| close_on_disconnect | host-wide 마지막 viewer의 종료 정책 미구현 → -32005 |
| display_projection | 별도 TUI display_messages projection 미구현 → -32005 |
| title, latest | resume의 제목/최신 세션 선택 미구현; 기존 명시 session_id 유지 → -32005 |

미지원 옵션은 종전처럼 `None/False/''` 이외의 활성 값에서 `handler_not_connected`,
`data.feature=session.resume`로 거절한다. defer_history 때문에 이 옵션들을 우회하지 않는다.
`defer_history`는 기존 Yield flag 검증 방식대로 boolean만, source는 원본 typed contract대로
문자열/null만 허용하며 잘못된 타입은 legacy 4000/authority 4001이다.

### hydrating·상태·순서 및 한계

hydrating은 클라이언트의 REST 조회 진행 여부가 아니다. 원본은 history restore/background
agent 준비 중 true/resuming이며, Yield에는 이 작업이 없으므로 false다. deferred status는
pending prompts가 있으면 waiting, 그 외 active_run 유무로 working/idle을 계산한다
(`tui_gateway/session_registry.py:648-655,822`). finalized session row를 reopen하거나
ended_at/end_reason을 지우지 않는다. 실제 실행 중 여부와 저장 row의 종료 여부는 별개다.

authority는 canonical epoch/sequence/subscription/pending/prompts를 그대로 보존하고,
legacy는 기존 info/open_requests를 유지한다. hydrating/status는 deferred 응답의 부가
정보이며 canonical 필수 snapshot을 대체하지 않는다. 소유권 확인 → replay 경계 읽기 →
history projection → 구독 등록 → 응답 → event pump 순서를 변경하지 않았다.

대형 세션: `MongoSessionStore`에는 assert_resume_safe/4130 정책이 없고 동기 SessionDB의
해당 메서드는 미지원 목록이다. 임의 상한/설정 또는 가짜 tip_only guard를 추가하지 않았다.
원본은 lazy 또는 omit_messages 또는 (defer_history && !eager_build)에서 tip_only다.
Yield는 정확한 공개 message_count 및 기존 publication gate를 위해 현재도 history 전체
projection을 읽는다. 따라서 빈 WS transcript 지원은 구현했지만 원본의 bounded ACK,
백그라운드 복원, tip-only 메모리 최적화/4130 동등성은 주장하지 않는다. REST도 기존
projection/paging 정책 그대로이며 이 PR은 성능 정책을 새로 정하지 않는다.

### 클라이언트 및 통합 테스트

- `src/hermes/api/client.ts:478-486`의 자동 attach가 보내는
  `{session_id, defer_history:true, omit_messages:true}`를 c와 real-model에서 실제 `sent`로
  캡처한다. 명시적인 기본 resume 우회와 defer_history 부재 단언을 제거했다.
- 통합 k는 legacy/authority 각각 desktop primary params로 resume한 뒤 빈 messages,
  hydrating=false, message_count를 확인하고 REST messages와 비교하며 서버 trace 순서를 확인한다.
- `src/platform/store-host.ts:82-89`의 replay gap 경로는 resume 후
  hydrateFromStoredSession을 호출한다. 기존 e 시나리오의 replay → resume → REST 검증을 유지했다.
- `src/hermes/app/session/hooks/use-session-actions`에는 순수 유틸 일부만 이식되어 있으며
  원본 React resume-owner 전체는 없다. k는 원본 primary params로 프로토콜 흐름을 검증하는
  테스트이며 완전한 React 화면 전환 E2E를 주장하지 않는다. 이식 원본 파일은 변경하지 않았다.
- gateway request wrapper는 이미 method/code/data.reason/data.feature를 포함했다.
  원본 error 전체를 cause로 붙이던 부분은 제거했다. real-model wrapper도 같은 진단 필드를
  보존하며 최상위 catch가 이 RPC 진단을 지우지 않게 했다(본문/티켓/키 출력 없음).
- PR-7 테스트에서 defer_history를 NotConnected로 단언하는 기존 케이스는 발견되지 않았다.
  기존 테스트 변경 없이 PR-8c 테스트를 별도 추가했다.

### manifest 및 재생성

`rpc_rules.py`에는 원본 Params와 handler subset의 관계만 주석으로 명시했다.
양 모드 implemented 분류 및 Params 타입 이름은 이미 정확하므로 JSON 구조/정책을 바꾸지 않았다.
생성기는 `harness/gateway/tools/extract_contract.py`다. 아래 재생성 결과는 기존 manifest와
바이트 단위로 일치했다. 따라서 generated JSON도 쓰지 않았으며 `src/platform` 불변이다.

```sh
../.venv/bin/python -m harness.gateway.tools.extract_contract --hermes-root /tmp/hermes-agent-ro --output /tmp/pr8c-manifest.json
cmp harness/gateway/manifest.json /tmp/pr8c-manifest.json
```

source_sha256는 **6d362634879b8a2fd9d218c317075ad2eeee3cd34f784df7607388979d5aef3d**로
유지되며 새 단위 테스트가 console projection의 source_sha256와 서버 파일 실제 SHA를 비교한다.
수동 manifest 편집은 없다.

### 검증 및 사용자 실행 인계

- 수정 전 신규 회귀: 14 failed / 24 passed. 수정 직후 38 passed.
- 추가 타입/재시도/manifest 검증을 포함한 PR-8c 순수 단위: 48 passed.
- 기존 PR-7a/7b/7b2 단위 + manifest 검사 + 최종 PR-8c: 145 passed.
- 통합 소스 포함 `tsc --noEmit -p tests-integration/tsconfig.json`: 성공.
- `vitest run`: 88 files / 1,036 tests passed.
- `npm test`: 10 passed. `npm run build`: 성공.
- 변경 Python의 py_compile: 성공. Mongo/실제 소켓/통합/real-model 테스트 미실행.

사용자가 프로젝트 루트(`08-YieldAgent`)에서 실행:

```sh
../.venv/bin/python -m pytest harness/tests/test_gateway_pr8c_mongo.py harness/tests/test_gateway_pr7a_mongo.py harness/tests/test_gateway_pr7b_mongo.py harness/tests/test_gateway_pr7b2_mongo.py -p no:cacheprovider -q
cd harness_console
npm run test:integration
PR8_REAL_MODEL=1 npm run test:integration:real
```

real-model의 기존 opt-in 환경/허용 목록 준비 절차는 tests-integration/README.md를 따른다.
새 Mongo fixture도 UUID DB 및 finally drop이며 외부 DB/Oracle/engine/model 호출은 하지 않는다.

### PR-8c 현재 SHA-256 (이전 표의 해당 행보다 우선)

| 파일 (08-YieldAgent 기준) | SHA-256 |
|---|---|
| harness/gateway/session_rpc.py | 1cad700332a37e28b3498c355026b168bc2f72c707fc92e3799aaba9c1ed4e8e |
| harness/gateway/rpc_rules.py | af4ec7d316ba2cbb9371c8fa91f2c54d3dd8faf8f46887c5e08f093c89b584fc |
| harness/tests/test_gateway_pr8c_unit.py | fb50d64ec52227f3e55a61694f618504622d41136be8a42ef8b1e5ee4ebc7f09 |
| harness/tests/test_gateway_pr8c_mongo.py | 8bebfdb0a7d7a2a1a7561270cbff1830d215727ebce589f65a21c18568ff4ea7 |
| harness_console/tests-integration/gateway.itest.ts | 90293e9167563a40c38d6525249b77fc5071c80ef70cfc8af715fb98c5a60805 |
| harness_console/tests-integration/real-model.itest.ts | f26d13b9318e15bd0dfb47c3f309af2931aad0502194e9ec0d6a5f6659d380f5 |
| harness/gateway/manifest.json | 6d362634879b8a2fd9d218c317075ad2eeee3cd34f784df7607388979d5aef3d |
| harness_console/src/platform/yield-manifest.generated.json | 160312a0034bbda5ae127c47ac18d9146efb15732b15082d803bf965d3a55f4d |

PR8_PORTING.md 자체 해시는 자기참조를 피하기 위해 표에 넣지 않는다.

최종 보호 파일 검증: 작업 시작 시 저장한 MD5와 비교해 engine 파일 2,658개,
`harness_console/src` 전체(hermes/protocol/platform 및 기존 App 포함),
`harness_console/README.md`가 모두 불변임을 확인했다. 커밋/push 없음.
