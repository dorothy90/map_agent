# PR-7b-2 REST와 정적 원격 호출 연결

기준 원본 H = `/tmp/hermes-agent-ro`, 커밋 `5894e750`.
PB = `08-YieldAgent/harness_console/PORTING_BASELINE.md`.
원본의 파일·전역 에이전트 초기화 함수를 import하지 않고, 아래의 응답 계약과
정적 번역 데이터만 재사용한다. manifest의 implemented는 연결 완료 표시가 아니다.
현재 연결은 HTTP GET 6개, 정적 RPC 2개다. 나머지는 아래 이유로 명시적 미연결이다.

## 설계와 진행

- [x] A: 영속 receipt의 현재 상태 계산을 확인하고 Mongo 테스트 기대값만 수정.
- [x] 신규 단위 테스트를 먼저 실패시킨 뒤 REST와 정적 RPC 연결.
- [x] `GatewayServices.open(principal)`과 `SessionRPC.history`의 공개 결정을 재사용.
- [x] 원본과 현재 엔진의 번역 차이를 확인하고, 고정 원본의 내장 번역만 정적 자료로 이식.
- [x] 실제 Mongo 통합 테스트 작성. import/수집/실행하지 않고 구문만 검사.
- [x] 허용된 16개 단위 테스트 파일 회귀와 최종 읽기 전용 검토.

기존 파일 변경은 routes.py 3줄 추가, services.py의 HTTP 진입점 추가,
connection.py의 정적 RPC 분기 추가, session_rpc.py의 기존 목록 공개 부분 함수 추출과
REST의 include_compacted 선택 인자 추가(WS 기본값 유지),
test_gateway_pr7b_mongo.py의 기대값 수정이다. engine, manifest, 정책, 인증,
agent_server.py, 기존 /runs 및 서버 전송 이벤트(SSE)는 수정하지 않는다.
`services=None`은 모든 기존 HTTP/WS 동작 그대로다. 운영 마운트도 기본 거부·서비스
미주입 그대로라서 이 작업만으로 운영 서버의 경로가 활성화되지는 않는다.

## A: 재전송 테스트 수정

`Execution.receipt_value`(execution.py:65-79)는 영속 실행의 현재 상태를 계산한다.
`_receipt_for_run`(:107-131)은 식별자를 보존하고 상태의 두 번째 사본을 만들지 않는다.
`submit`(:133-142)은 같은 submission_id의 기존 실행을 찾아 receipt를 다시 반환한다.
따라서 완료 후 재전송 응답을 최초 started 응답 전체와 비교한 테스트가 잘못됐다.

수정된 테스트는 run_id, admission_id, ref, sequence, authority_epoch,
execution_generation 동일성과 terminal/completed를 따로 검사한다.
Mongo에서 같은 principal/session/submission의 실행이 한 개인지와 launch가 한 번인지도
검사한다. 실행 구현은 변경하지 않았다. 해당 Mongo 테스트는 실행하지 않았다.

## 연결한 REST

아래 H 경로의 공통 접두사는 `hermes_cli/web_routers/`다.
모든 경로는 인증 후에만 동작하며, profile 쿼리로 권한을 바꿀 수 없다.

| GET 경로 | 원본 근거 | 응답과 재사용 함수 |
|---|---|---|
| `/api/sessions` | H/sessions.py:182-246 | sessions,total,limit,offset,storage. `MongoSessionStore.list_sessions_rich` → `SessionRPC.public_list_row` → `history` |
| `/api/sessions/{session_id}/messages` | H/sessions.py:739-785 | session_id,profile,messages,pagination. `owned` → `resolve_resume_session_id` → `history` |
| `/api/profiles/sessions` | H/profiles.py:453-518 | 목록 형식에 profile_totals,errors 추가. 동일한 공개 목록 함수, 본인만 조회 |
| `/api/profiles/active` | H/profiles.py:885-903 | active,current 모두 `services.profile_id_for(principal)`에서 얻는 서버 소유 이름 |
| `/api/health` | H/status.py:119-130 | ok:true,auth_required:true. 이 HTTP 처리기의 생존만 뜻하며 저장소나 모델의 정상 여부가 아님 |
| `/api/status` | H/status.py:383-406,482-547 | auth_required:true만. 인증 제공자·인증 흐름·설치 ID·시스템 상태를 추정하지 않음 |

REST 어댑터는 `gateway/rest.py:dispatch/serve_http`다.
`routes.py`에는 인증 후 서비스를 호출하는 분기만 추가했다.
stub/unsupported는 기존 `http_rejection`으로 처리한다.

목록의 공개 필드는 id,started_at,source,title,message_count,profile,
is_default_profile,archived,pinned와 저장된 경우 last_active다. message_count는 원문 저장 행 수가 아니라
`history`의 공개 메시지 수다. 원본의 limit/offset, created/recent, source/sources/
exclude_sources, archived, min_messages를 연결한다. 고정 세션은 원본처럼 페이지 밖에서도
남긴다(H/profiles.py:257-265,506-514; H/sessions.py:225-229).
full=true와 cwd_prefix는 안전한 계약이 없어 503 미연결로 거부한다.
목록은 공개 개수를 정확하게 계산하기 위해 본인 세션을 읽은 후 필터·페이지 처리를 한다.
세션/실행 수가 커질 때의 조회 비용 최적화는 후속 과제다.

메시지는 원본 REST의 content/id 이름으로 변환하며, WS의 text/row_id와 같은 공개
내용이다. host run에는 원본 메시지 행 ID가 없으므로 id를 꾸며 넣지 않는다.
기본 최근 500개, 명시 limit은 최대 500개, oldest/latest와 offset을 지원한다.
include_compacted는 원본대로 기본 false이며 공통 history 함수에 전달한다.
WS 기본값은 기존 true를 유지한다. 혼합 이력 검사는 항상 전체 행에서 먼저 수행하므로
이 옵션으로 미연결 결정을 우회할 수 없다. inline_images는 불리언 형식을 검사하되
PR-7b-1의 텍스트 공개 함수만 사용한다. 이미지·도구·요약 원문을 추가로 꺼내는 경로가 아니다.
타인 세션과 없는 세션은 모두 404 `{"detail":"Session not found"}`다(H/sessions.py:43,769).

## 연결한 정적 원격 호출(RPC)

| 이름 | 모드 | 원본 근거 | 응답과 재사용 |
|---|---|---|---|
| `i18n.languages` | legacy | H/tui_gateway/methods_i18n.py:22-29; H/agent/i18n_languages.py:23-75; G:1896-1905 | languages 배열의 id,endonym,rtl,source. 17개 내장 언어, en 먼저·나머지 정렬 |
| `i18n.catalog` | legacy | H/tui_gateway/methods_i18n.py:32-44; H/agent/i18n.py:109-122,164-175; H/agent/i18n_layers.py:43-62; G:1906-1917 | lang,surface,messages. 원본 별칭·지역 태그 정규화, core/tui/desktop |

G = H/apps/shared/src/gateway-contract.generated.ts.
`connection.py`는 기존 manifest/모드 판정을 통과한 요청에만 `static_rpc.static_rpc`을
호출한다. authority에 없는 i18n 호출은 -32601이다. 정적 호출은 저장소를 열지 않는다.
surface 오류는 원본 4002 및 `surface must be one of core, tui, desktop` 그대로다.

`static_i18n.json`은 H/agent/i18n.py:33-70의 언어 별칭,
H/agent/i18n_languages.py:23-41의 언어 정보,
H/locales/{af,ar,de,es,fr,ga,hu,it,ja,ko,pt,ru,tr,uk,zh,zh-hant}.tui.yaml:1-1406을
원본 `flatten` 규칙으로 JSON화한 자료다. 새 번역을 작성하지 않았다.
현재 engine의 파일과 일치하지 않아 엔진 파일을 조용히 재사용하지 않았다.
원본 core는 내장 문자열을 전송하지 않으며, 원본에 desktop YAML 및 en.tui.yaml은 없다.
따라서 이 표면의 messages:{}는 원본의 실제 결과다. 미연결 기능을 빈 결과로 위장한
것과 다르다. 비영어 TUI 16개 언어는 실제 원본 번역을 반환한다.
사용자/plugin overlay는 manifest의 기존 유예 결정대로 읽지 않는다.

## 연결하지 않은 정적 RPC

필수 값을 안전하게 만들 수 없는 요청은 두 모드에서 기존 -32005
handler_not_connected를 유지한다. 해당 모드에 이름이 없으면 기존 -32601이다.
이들 호출은 성공 응답이나 빈 목록을 만들어 내지 않으며 저장소·엔진도 열지 않는다.

| 이름 | 원본 근거 | 이유 |
|---|---|---|
| `config.get` | H/tui_gateway/methods_config.py:168-299; G:488-511,5204 | 원본 getter는 실제 profile 설정/agent/경로를 요구. policy/capabilities에는 공개할 실제 설정 값이 없다. 빈 설정·원본 기본값을 운영 사실로 대신하지 않음 |
| `model.options` | H/tui_gateway/methods_complete.py:319-332; G:813-845 | 실제 허용 모델·provider 목록을 주는 공개 공급자가 없음. providers:[]는 실제 목록 조회 성공을 가장하므로 사용하지 않음 |
| `profiles.list` | H/tui_gateway/methods_profiles.py:282-304; G:2013-2039,5234-5254 | ProfileRow/CanonicalProfileRow의 필수 path를 안전하게 채울 수 없음. 빈 path·가짜 URI로 대체하지 않음 |
| `tools.list`, `toolsets.list` | H/tui_gateway/methods_tools.py:232-246,270-272; G:4007-4017 | policy는 비활성 분류만 알며 실제 도구 registry/name/description/tool_count를 제공하지 않음 |
| `tools.show` | H/tui_gateway/methods_tools.py:1236-1253; G:4018-4029 | 실제 허용 도구 정의가 주입되지 않음. 전체 원본 도구를 발견/import하는 우회 금지 |
| `paste.collapse` | H/tui_gateway/methods_complete.py:35-51; G:1983-1990 | 파일 쓰기와 필수 path/경로 포함 placeholder. 파일 기능 비활성과 경로 비공개를 만족할 원본 성공 계약이 없음 |

## 연결하지 않은 REST

모두 manifest 분류는 그대로이고 인증 후 503 handler_not_connected다.

| 경로 | 원본 근거 | 이유 |
|---|---|---|
| GET `/api/profiles` | H/hermes_cli/web_routers/profiles.py:84,795-810 | 원본 profile 행의 path/model/provider/skill metadata를 안전한 실제 값으로 구성할 공급자가 없음 |
| GET `/api/profiles/sessions/sidebar` | H/hermes_cli/web_routers/profiles.py:521-645 | 원본 profiles_usage와 세 가지 slice 의미까지 보장할 공개 사용량 집계가 없음. 가짜 0이나 빈 slice를 반환하지 않음 |
| GET `/api/health/idle` | H/hermes_cli/web_routers/status.py:152-157 | 원본 idle_proof는 호스트 lifecycle/retirement 진단. 이 서버에서 같은 증거를 제공하지 않음 |
| GET `/api/host/identity` | H/hermes_cli/web_routers/status.py:133-149 | pid,role,servesSpa는 호스트 소유 증명. 임의 role/pid를 가정하거나 공개하지 않음 |
| GET `/api/egress/status` | H/hermes_cli/web_routers/config_env.py:109-114 | 실제 프록시 설정·상태 text를 안전하게 공급할 어댑터가 없음 |
| GET `/api/config`, `/api/config/defaults` | H/hermes_cli/web_routers/config_env.py:80-96 | 실제 profile 설정/전체 기본 설정을 반환하면 호스트 설정 노출. 공개 필드와 값의 공급이 미정(PB:796) |
| GET `/api/config/schema` | H/hermes_cli/web_routers/config_env.py:99-106 | fields/category_order는 동적 provider/플러그인 설정과 결합. 검토된 공개 schema가 없음 |
| GET `/api/model/info` | H/hermes_cli/web_routers/models.py:90-135 | 실제 모델/provider/context_length가 없는데 미설정·0으로 응답하면 허위 상태 |
| GET `/api/model/options` | H/hermes_cli/web_routers/models.py:138-168 | model.options RPC와 같은 공급자 부재 |
| GET `/api/model/recommended-default` | H/hermes_cli/web_routers/models.py:176-216 | 실제 허용 provider/모델 및 요금제 판단 공급자가 없음 |
| GET `/api/tools/toolsets` | H/hermes_cli/web_routers/tools.py:223-270 | 실제 허용 registry의 configured/enabled/tools가 없음 |
| GET `/api/tools/toolsets/{name}/config` | H/hermes_cli/web_routers/tools.py:338-421 | provider/환경변수 이름/자격 상태를 읽는 계약. 공개 필터와 실제 공급 미정(PB:796) |
| GET `/api/tools/toolsets/{name}/models` | H/hermes_cli/web_routers/tools.py:424-461 | 실제 허용 toolset/model catalog 없음. 원본의 no_models를 알 수 없는 Yield 도구에 임의 적용하지 않음 |
| GET `/api/auth/me` | H/hermes_cli/dashboard_auth/routes.py:524-552 | Principal에는 user_id만 있음. email/display_name/org_id/provider/expires_at를 가정할 회사 인증 계약 Q1이 없음 |
| POST `/api/auth/ws-ticket`, `/auth/logout` | H/hermes_cli/dashboard_auth/routes.py:555,496 | 회사 인증 계약 Q1 미정. 원본 쿠키/토큰 방식을 추정하지 않음 |

위 외의 HTTP 행은 기존 http_rejection, 비활성 RPC는 rpc_rejection 그대로다.
terminal/shell/file/browser/sudo/secret 도구는 추가·활성화하지 않았다.

## 안전한 공개 규칙과 원본 차이

1. **권한:** 원본 host-wide 권한 대신 인증된 Principal만 사용한다(PB:416).
   세션 저장소와 실행 조회는 principal_id로 묶인다. 클라이언트 profile/principal 필드는
   권한을 바꾸지 못한다. profile 이름은 오직 services.profile_id_for에서 얻는다.
2. **공개 함수:** `SessionRPC.public_list_row`는 기존 listing의 같은 코드를 추출한 것뿐이다.
   `history` → `verified_answer` → publication.settle를 그대로 사용한다. PR-3 user 원문과
   host run이 함께 있으면 REST도 503으로 남는다. 별도 순서 추정/중복 제거를 하지 않는다.
3. **기록 변환:** Mongo host 실행을 Hermes REST 메시지에 맞추는 연결은 원본 대응 없음
   → PB:575,642,664,700. 사용자 질문과 검증된 최종 답변만 공개한다. 원본 메시지
   메타데이터·후보·reasoning·tool 본문·시스템 프롬프트·모델 설정은 복사하지 않는다.
4. **상태/프로필:** health/status 최소 사실과 active/current를 서버 매핑으로 제한하는 것은
   원본 대응 없음 → PB:416,642,796. CLI의 sticky profile이나 설치 상태를 대신 조회하지 않는다.
5. **오류:** 소유권 404, 원본 order/archived 400, 숫자/불리언 형식 422를 유지한다.
   422 진단에는 원문 query input을 되비추지 않는다. 예기치 않은 저장소 오류는
   500 `Internal server error`로 숨기며 빈 목록 성공으로 바꾸지 않는다.
6. **번역:** 원본의 내장 정적 자료만 사용한다. locale 별칭 표는 원본 프로토콜 데이터이며
   자연어 계획을 keyword/regex로 대체하는 분기가 아니다. 실행이나 도구 선택에 사용하지 않는다.

## 결정 필요·검증 범위

- 회사 인증 Q1: user_id 외 공개 속성, 티켓 발급과 로그아웃 수명 계약.
- 회사 profile 매핑: 현재는 기존 services의 서버 주입 함수를 사용하며 조직 규칙을 추정하지 않음.
- 실제 허용 모델/도구/설정의 공개 공급자 및 schema. 분류표를 실제 설정으로 간주하지 않음.
- path가 필수인 profiles.list/paste.collapse의 원본 계약과 경로 비공개 정책의 충돌.
- sidebar의 사용량 집계, 혼합 메시지의 공통 식별자/정렬, 대규모 공개 목록 조회 비용.
- 운영 마운트와 실제 Mongo/모델/도구 연결은 이번 검증에 포함되지 않음.

새 `test_gateway_pr7b2_unit.py`는 in-process TestClient로 전체 manifest HTTP 행을
순회하고, 모든 행의 401/403, 서비스 미주입, 각 정적 RPC/모드, 타인 404,
후보 비노출, 검증된 답변, 혼합 이력 미연결, 페이지·고정 세션, 예외 비노출,
17개 언어의 세 표면을 검사한다. HEAD는 원본 HTTP 의미대로 본문이 없다.

새 `test_gateway_pr7b2_mongo.py`는 UUID 데이터베이스/finally drop/앱 수명 안의 Motor와
가짜 launch를 사용한다. 두 principal의 WS 생성·실행 → REST 목록/메시지 격리와
검증 전후 공개를 확인하도록 작성했다. **실행하지 않았다.**
기존 수정 Mongo 테스트도 **실행하지 않았다.** 둘 다 ast.parse 구문 검사만 했다.

독립 읽기 전용 검토에서 발견된 보관된 고정 세션 페이지 처리와 include_compacted
미반영은 각각 실패하는 테스트를 먼저 추가해 수정했다. 목록 총계와 페이지의 고정
행 집합을 분리했고, 압축 행 선택은 공통 공개 함수에 전달했다.
수정 후 신규 28개와 기존 PR-7b 25개가 합쳐 **53 passed**였다.
정적 자료는 고정 원본 16개 YAML을 같은 flatten 규칙으로 변환한 결과와 전체 비교했고,
언어 정보·별칭 표도 원본 AST의 상수와 동일함을 확인했다.

## 파일 변경량

Git 작업 트리에는 작업 전부터 대량의 변경/미추적 파일이 있었다. 아래 차이는
이번 작업 시작 시 보관한 5개 기존 파일 사본과 비교한 값이다.

| 기존 파일 | 추가/삭제 | 이유 |
|---|---:|---|
| gateway/routes.py | +3/-0 | implemented HTTP에만 인증 후 서비스 분기 추가 |
| gateway/connection.py | +3/-0 | manifest 판정 뒤 정적 RPC 전달 |
| gateway/services.py | +4/-0 | REST 어댑터 호출 진입점 |
| gateway/session_rpc.py | +11/-4 | 공개 목록 함수 추출, REST 압축 행 옵션; WS 기본값 보존 |
| tests/test_gateway_pr7b_mongo.py | +13/-1 | A: 현재 상태와 영속 식별자·단일 실행을 별도로 단언 |

| 새 파일 | 줄 수 | 역할 |
|---|---:|---|
| gateway/rest.py | 172 | REST 계약·공개 변환·오류 처리 |
| gateway/static_rpc.py | 49 | 정적 번역 RPC |
| gateway/static_i18n.json | 22,294 | 고정 원본에서 변환한 번역/언어 자료, 약 1.84 MB |
| tests/test_gateway_pr7b2_unit.py | 284 | Mongo/실제 소켓/모델 없는 28개 테스트 |
| tests/test_gateway_pr7b2_mongo.py | 97 | 실행하지 않은 실제 Mongo 시나리오 |
| PR7B_REST_STATIC_RPC.md | 본 문서 | 원본 대응·미연결 이유·검증 기록 |

## 최종 실행 결과

마지막 코드 수정 후 허용된 16개 파일 전체: **902 passed, 1 deselected (42.08초)**.
제외 1개는 지정된 test_native_budget.py의 local_server 테스트다.
독립 재검토에서도 두 수정이 해결됐으며 추가 중요한 문제는 발견되지 않았다.

```sh
cd /Users/daehwankim/yield-agent/08-YieldAgent && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -p no:cacheprovider harness/tests/test_publication.py harness/tests/test_native_pr5.py harness/tests/test_native_pr4c.py harness/tests/test_native_pr6.py harness/tests/test_native_ipc.py harness/tests/test_native_stop_gate.py harness/tests/test_native_verification.py harness/tests/test_native_budget.py harness/tests/test_pending_controls_unit.py harness/tests/test_gateway_auth.py harness/tests/test_gateway_contract_frames.py harness/tests/test_gateway_contract_manifest.py harness/tests/test_gateway_routes.py harness/tests/test_gateway_pr7a_unit.py harness/tests/test_gateway_pr7b_unit.py harness/tests/test_gateway_pr7b2_unit.py -k 'not local_server' -q
```

허용 목록 밖 테스트는 실행하지 않았다. 모든 Mongo 테스트, engine 테스트, 실제 포트
바인딩 테스트, 실제 모델 호출은 미실행이다. 설치/Docker/커밋/push/stash/checkout과
기존 파일 삭제도 하지 않았다. Mongo 파일은 import/pytest 수집 없이 구문만 검사했다.
