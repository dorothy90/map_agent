# Opt-in integration suite (PR8b)

These files are artifacts for a user-run check. They have **not been executed** by
Codex. Running them binds a real loopback socket and connects to a real MongoDB.
The default suite injects a fake controller and never launches an engine, model or tool.
The separate real-model opt-in path below starts the actual harness controller.

From `harness_console`, supply `HARNESS_MONGO_URI` through your own environment,
then run `npm run test:integration`. Do not paste a credential-bearing URI into
logs or a shared command transcript. The suite requires Node's native `fetch` and
`WebSocket`, plus the already installed Python gateway/Motor/uvicorn dependencies.
No dependency installer runs. `PR8_PYTHON` optionally overrides the default
repository `.venv/bin/python` (`../../.venv/bin/python` from this directory).

For the default suite, `global-setup.ts` starts only `pr8_gateway_server.py`, waits for its one stdout JSON record
`{"port": ..., "db": "zz_pr8_<uuid32>"}`, and provides the origin to Vitest. It never
forwards Python stderr. Missing `HARNESS_MONGO_URI` fails before spawning Python.
The helper creates the Motor client in FastAPI's lifespan and drops its UUID
DB in `finally`, then closes the client. Teardown closes stdin; SIGTERM is the
fallback and also uses uvicorn's graceful lifespan shutdown. Abrupt SIGKILL or
machine failure cannot guarantee cleanup; the provided database name identifies
that run's fixture database.

Authority mounts at `/gateway-m1`; legacy mounts at `/gateway-legacy`. A fake
resolver accepts **only** explicit `?user=alice` or `?user=bob` on both HTTP and WS.
Missing/invalid identity fails closed. Every dial supplies a unique, single-use,
user-bound test ticket. The fake Origin policy rejects a supplied foreign Origin;
Node's native WebSocket omits Origin, so absent Origin is accepted in this
loopback fixture. These policies are test fixtures, not company authentication.
Production Q1 ticket/auth semantics remain unspecified.

Loopback-only controls are intentionally outside the gateway protocol:

- `POST /__test/control`: `ack_loss` discards the next successful submit response
  and closes that socket; `drop` closes active sockets; `replay_limit` changes only
  the test process ring cap; `epoch` rebuilds an owned replay log; `publish` writes
  synthetic run events; `finish` persists the same verified candidate evidence
  and terminal state as the existing PR7 Mongo fixture.
- `GET /__test/state`: RPC names, HTTP paths, launch IDs, guarded tool names and ACK-loss status.
  It exposes no request bodies, prompts, URI, tokens, environment, or raw DB rows.

The ten scenarios (11 tests, including both gap variants) cover matching/mismatched modes without fallback; real session,
admission and verified event/store flow; dropped ACK and durable receipt identity;
reconnect/replay deduplication; truncation/epoch snapshot recovery and history
order; client-side unsupported wire suppression and separate raw-server errors;
user isolation; unsupported legacy submit; implemented REST; missing identity.
The backend has no mode-advertisement handshake: legacy-on-authority fails its
capability call, while authority-on-legacy fails an authority operation. Tests do
not invent a readiness frame or treat socket-open as proof of mode compatibility.

Static compilation does not establish integration success. Only an actual user
run against their Mongo instance can validate these network/DB scenarios.

PR8b follow-up: the user's external Mongo run reported 8 failures / 3 passes,
all failures caused by the shared `create()` title violating the per-user unique
title contract. Each helper call now appends a fresh UUID (52 characters total,
below the server's 100-character limit). Scenario g omits the optional title;
the server generates its session ID. Submit IDs and per-dial tickets already use
UUIDs; scenario c deliberately reuses one submit ID to verify idempotency. Raw
JSON-RPC IDs are connection-local. Server-generated receipt/event IDs are retained.
Fixture database names now retain all 32 UUID hex digits rather than eight to
reduce cross-run collisions, including teardown dropping a shared database.
The setup ready-record validator uses the same format.

The follow-up has not been integration-tested by Codex; an external rerun is
required. Session creation, submission/receipt scope, replay/epoch, verified
finish, and REST expectations were checked statically against `harness/gateway`
and `harness/store.py`; no ported client or backend changes were made.

## PR-8b 통합 테스트 2차 수정 (2026-10-25)

로컬 Mongo 실행에서 보고된 11개 중 10개 통과·시나리오 c 실패에 대한 정적 분석 및 수정이다. ACK 유실 후 재연결하면 Hermes가 자동 attach에 사용하는 `session.resume(defer_history: true)`를 Yield 서버가 `-32005 handler_not_connected`로 거절한다. c는 지원되는 `session.resume({session_id})`를 명시적으로 완료한 다음 같은 submission_id로 재전송하며, 실제 전송 순서와 terminal receipt의 식별자·launch 1회를 검증한다. 테스트의 RPC 헬퍼는 실패한 호출의 메서드명과 `error.code/data.reason/data.feature`를 추가하고 원래 오류를 cause로 보존한다.

이는 기본 자동 attach 옵션의 호환성 문제를 해결한 것이 아니다. 서버·이식 원본은 변경하지 않았으며, 기존 실패 로그에 RPC 이름이 없어 당시 실패 지점은 정적 추론이다. 상세 근거와 SHA-256은 [PR8_PORTING.md](../PR8_PORTING.md)에 기록했다.

이번 수정의 타입 검사(통합 테스트 소스 포함), Vitest 단위 88파일/1,036개, `npm test` 10개 및 빌드는 통과했다. 빌드는 `/tmp/pr8b-second-followup-build`에 출력해 기존 dist를 보존했다. 통합 테스트와 서버 fixture는 실행하지 않았으며, 실제 Mongo 재검증은 사용자가 샌드박스 밖에서 실행한다. 실행 방법은 [통합 테스트 안내](./README.md)를 따른다.

## PR-8b 실제 모델 opt-in (2026-10-25)

사용자는 기본 통합 11개가 로컬 Mongo에서 모두 통과했다고 보고했다. 기존
`gateway.itest.ts`는 변경하지 않았고, `npm run test:integration`은 환경에
`PR8_REAL_MODEL`이 있어도 OfflineController만 사용한다. 기본 config는
`real-model.itest.ts`를 명시적으로 제외한다.

별도 실행: 환경에서 `PR8_REAL_MODEL`을 `1`로 설정하고 `HARNESS_MONGO_URI`를
제공한 뒤 `npm run test:integration:real`을 실행한다. 선택적으로 `PR8_PYTHON`을
지정한다. URI 값이나 키를 명령 기록에 붙이지 않는다. 모델 키는 기존 harness
Python 설정이 읽는다. 테스트 코드는 키를 조회하거나 파일로 복사하지 않는다.
Mongo URI는 localhost/loopback 단일 호스트만 허용하며, DB는 매번
`zz_pr8_<uuid32>`를 생성한다. 기존 DB 이름 설정은 사용하지 않는다.

`vitest.real-model.config.ts` → `real-model-setup.ts` → 공유 `setupFixture(realModel=true)`
→ `pr8_real_model_server.py` 경로만 실제 모델을 사용한다. setup과 Python 진입점
모두 opt-in을 검사한다. 자동 test retry는 0이다.

PR-7 smoke의 `harness_app.router.lifespan_context`에서 실제 ctrl을 얻고,
`registry_factory`를 감싸 `read_result` 외 도구 handler를 차단한다. resolver,
Origin/ticket 정책, gateway 마운트, ACK 유실 제어 및 서버 main은 기존 fixture를
재사용한다. `_run` 진입을 계수하고 두 번째 실행은 모델 호출 전에 거절한다.
여기서 **1회는 질문 하나의 controller 실행**이다. 내부 모델 API 요청/검증 호출이
정확히 한 HTTP 요청이라는 뜻은 아니다.

시나리오는 authority WebHostAdapter 연결 → session.create → 기존 bindStores와
동일한 설치 순서 → 질문 1회/실제 ACK 폐기 → 재연결 및 기본 session.resume →
같은 submission_id로 1회 재전송이다. 원래 run_id 일치, 240초 이내
message.complete와 설치된 assistant store의 비어 있지 않은 동일 응답,
seq 엄격 증가, 새 연결 events.since의 완료 재생 및 기존 push 포함,
include_result terminal receipt 일치, 실행 1회, blocked 목록 0개를 검증한다.
모델 응답의 정확한 문자열은 고정하지 않는다. synthetic finish는 호출하지 않는다.

민감 출력은 대체문으로 마스킹한다: Python stdout은 진단 stderr로 돌리고
부모가 stderr를 폐기한다. 준비/worker PID 제어 레코드만 별도 stdout으로 전달하며
출력하지 않는다. native_worker stderr도 DEVNULL로 보내 원문 로그 기록을 막는다.
Vitest console 출력은 억제하고 오류에는 고정 단계명만 남긴다. 응답/프롬프트/URI를
assertion diff나 cause로 전달하지 않는다.

teardown은 stdin EOF → 실제 ctrl.close()와 native_worker 종료 → lifespan finally의
DB drop을 우선 사용한다. 지연 시 30초 SIGTERM, 45초 서버 및 별도 worker 그룹
SIGKILL로 정리한다. worker PID는 프로세스 생성 시 추적한다. 강제 SIGKILL·머신
장애·Mongo 불통에서는 finally의 DB 삭제를 보장할 수 없다.

Codex 검증: 통합 소스 포함 tsc, Vitest 단위 88파일/1,036개, npm test 10개,
빌드(`/tmp/pr8-real-model-build`), 두 Python helper의 py_compile 모두 통과.
통합 및 실모델 실행은 하지 않았다. 실제 provider 응답, 재연결 시점의 store/replay,
worker 종료와 Mongo drop은 사용자의 샌드박스 밖 실행으로 확인해야 한다.

## PR-9c 실제 모델 화면 확인

`08-YieldAgent` 디렉터리에서, localhost 전용 `HARNESS_MONGO_URI`와 기존 모델
설정이 준비된 환경에서 수동 실행한다:

```bash
PR9_REAL_MODEL=1 PR9_PORT=0 PR9_VITE_ORIGIN=http://127.0.0.1:5176 ../.venv/bin/python harness_console/tests-integration/pr9_real_dev_server.py
```

stdout의 준비 JSON 한 줄에서 `port`를 확인해 Vite의 `HARNESS_GATEWAY_TARGET`을
`http://127.0.0.1:<port>`로 지정한다. UI는 `?user=alice` 또는
`?user=bob`으로 연다. fake resolver는 개발 전용이다.
`GET http://127.0.0.1:<port>/__dev/state`는 user 없이 loopback에서만
`launches`, `blocked`, `allowed`, `workers`를 제공한다. `allowed`는 명시적으로 허용한
도구 이름 목록이며 기본값은 `[]`이다. worker PID는 stderr에 `worker <pid>`로 기록한다.

각 사용자에게 네 세션을 시드한다. `alice-approval`/`bob-approval`의 **승인 대기**는
`write_report --name weekly` 승인 UI 확인용이며 1시간 후 만료된다.
선택지는 gateway 계약이 생성한다. 시드는 실행 중인 도구가 아니므로 승인해도
보고서를 생성하거나 모델 실행을 재개하지 않는다. 일반 대화에서 새 요청을 보내
실제 스트리밍·도구 진행·중지를 확인한다.

기본적으로 외부 DB/Oracle 접속 도구는 차단하며 `read_result`만 허용한다.
복합 요청 확인 시 `PR9_ALLOW_TOOLS=query_lot_history,query_wads`처럼 쉼표로 구분한
도구 이름을 지정할 수 있다. 기본값은 빈 문자열이며, 앞뒤 공백과 빈 항목은
제거하고 중복 이름은 첫 등장 순서로 한 번만 유지한다. registry의
`read_only is True`이고 `effect == 'read'`인 도구와 입력 rows만 HTML로 만드는
`render_yield_table`만 원래 핸들러를 유지한다. 표 아티팩트 검증에는
`PR9_ALLOW_TOOLS=render_yield_table`을 사용한다.
알 수 없는 이름이나 조건에 맞지 않는 도구(`run_python`, `export_report` 포함)는
`make_real_app()`에서 요청 처리 전에 `ValueError`로 실패한다. 나머지 도구는
기존처럼 차단하고 `blocked`에 기록한다.
명시적으로 허용한 도구가 있으면 stderr에 다음 한 줄을 출력한다(검증된 이름만 표시):
`PR9_ALLOW_TOOLS enabled (read-only tools may query configured external DBs): <names>`.
허용된 읽기 도구는 설정된 외부 DB를 실제 조회할 수 있다.

서버·Mongo·도구 호출 없이 허용 목록만 검사하려면 프로젝트 루트에서
`../.venv/bin/python -m pytest harness_console/tests-integration/test_pr9_allow_tools.py -q -p no:cacheprovider`를 실행한다.

localhost Mongo의
임시 `zz_pr9r_<uuid>` DB와 설정된 실제 모델 provider를 사용한다.
`PR9_MAX_RUNS`는 실제 실행 예산이며 기본 4, 허용 1~10이다. 초과 시
`Real-model execution budget exceeded`로 실패한다.
종료는 `kill -INT <서버 PID>`로 한다(SIGTERM도 정상 종료 경로 사용).
lifespan 종료 시 worker 그룹 SIGKILL/wait, 임시 DB drop, client close를 수행한다.
강제 SIGKILL·시스템 장애·Mongo 불통에서는 정리를 보장할 수 없다.
이번 검증은 두 Python 파일의 `py_compile`만 수행하며 서버·Mongo·모델·브라우저
실행 및 실제 UI/종료 정리 동작은 미검증이다.
