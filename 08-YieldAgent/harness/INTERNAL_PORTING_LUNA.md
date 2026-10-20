# Luna용 사내 최신 main 하네스 이식 안내

## 0. 목표와 작업 범위

사내 최신 main에는 외부 기준 저장소에 없는 수정과 신규 에이전트가 있다. 사내 코드를 정본으로 보존하면서 이 문서와 같은 커밋의 Hermes 하네스, 일반 위임, 독립 검증, 채팅 화면 기능을 병합한다. 사내 레포를 외부로 보내지 않고 사내에서 조사·구현·검증한다.

이 문서는 이번 이식의 실행 지침이다. MIGRATION.md의 과거 커밋 기준 및 1단계 후 별도 지시를 기다리는 절차 대신, 아래 단계별 검증을 통과하면 다음 단계로 진행한다. 실제로 부족한 권한·업무 정의·접속 정보만 질문한다. 사내 AGENTS.md와 조직 정책은 준수한다.

- 기준 저장소: `https://github.com/dorothy90/map_agent.git`
- 기준 브랜치: `codex/goal-driven-harness`
- 입력 소스: 이 문서를 포함한 **동일 커밋의 전체 추적 파일 스냅샷**. 작업 시작 때 실제 커밋 SHA를 기록한다.
- 목표 런타임: `HARNESS_ENGINE=hermes`, 일반 위임 활성화, 환경 탐색 비활성화.
- Luna는 **이식 코드를 작성하는 모델**이다. 서비스가 사용할 LLM은 별도 설정이다. 사내 허용 모델의 실제 API 주소·모델 ID·인증을 확인하고 구성한다. 외부 기본 모델을 그대로 사용하지 않는다.
- 기존 서비스·에이전트·인증·UI 기능을 삭제하거나 원본 버전으로 되돌리지 않는다.
- 운영 main에 바로 작업하지 않는다. 사내 최신 main에서 이식 브랜치를 만들고 검증 후 사내 절차로 병합한다.

## 1. Luna에게 처음 전달할 지시

아래 내용을 사내 Luna에게 전달한다. 참조 소스 위치와 사내 레포 위치는 실제 경로로 지정한다.

> 사내 레포의 최신 main에 참조 소스의 Hermes 하네스와 채팅 화면 기능을 이식해라. 먼저 참조 소스의 harness/INTERNAL_PORTING_LUNA.md를 끝까지 읽어라. 참조 소스는 읽기 전용이고 사내 최신 코드를 정본으로 삼는다. 기존 서버·공통 코드·프런트 파일을 통째로 덮어쓰지 마라. 기존 및 새 에이전트 전체 목록과 원본→사내 파일 대응표를 작성한 뒤, 이 문서의 단계별 검증을 수행하며 진행해라. 일반 위임은 켜고 환경 탐색은 끈다. 독립 검증자 권한은 확대하지 마라. 사내 승인 LLM과 실제 DB로 최종 UI 시나리오까지 검증해라. 정상·실패·미검증을 구분해 기록하고, 사내 코드나 데이터는 외부 서비스·저장소에 전송하지 마라. 자연어 해석 실패를 키워드·정규식·예시 누적으로 우회하지 마라. 변경을 작은 단위로 유지하고 각 단계 종료 시 진행 기록을 갱신해라.

## 2. 시작 전에 만들 기록

사내 이식 브랜치에 `docs/harness-porting/STATUS.md`를 만들고 아래 항목을 유지한다. 외부에 업로드하지 않는다.

```text
참조 커밋 SHA:
사내 main 기준 SHA:
이식 브랜치:
현재 단계:
완료 단계와 검증 증거:
변경 파일 및 이유:
미해결 문제 / 필요한 사용자 정보:
다음 한 작업:
```

다음 표 두 개도 작성한다. 파일명이나 함수가 비슷하다는 이유로 계약이 같다고 추측하지 않는다.

| 참조 파일/기능 | 사내 대응 파일/함수 | 입력/반환 차이 | 병합 방법 | 검증 |
|---|---|---|---|---|
| 서버 수명주기 | 조사 후 기록 | 기존 초기화·종료 | 기존 기능 안에 결합 | 기동·종료 |
| 스트리밍/이력 | 조사 후 기록 | 이벤트·세션 | 기존 필드 유지하며 추가 | 재접속 |

| 사내 에이전트 | 공개 실행 함수 | 입력·출력 | DB/API/LLM 의존성 | 읽기/산출물/영구쓰기 | 연결 상태 |
|---|---|---|---|---|---|
| 기존 및 신규 모두 | 실제 코드에서 확인 | 스키마 기록 | 숨은 LLM 포함 | 효과 분류 | 연결/유지/미연결 사유 |

기준 소스의 도구 수에 맞춰 사내 에이전트를 줄이지 않는다. 지원하지 못한 에이전트를 성공으로 처리하지 않는다. 사내 변경이 남아 있으면 그 소유와 범위를 기록하고 보존한다. `reset --hard`, `clean -fd`, 전체 디렉터리 덮어쓰기를 사용하지 않는다.

## 3. 파일 반영 목록

아래 경로는 참조 저장소 루트 기준이다. 사내 디렉터리명이 다르면 대응표에 따라 변환한다.

### 3.1 새 기능으로 가져올 폴더

`08-YieldAgent/harness/` 전체를 가져온다. 사내에 같은 폴더가 이미 있다면 차이를 비교해 병합한다. `__pycache__`는 제외한다.

| 구성 | 역할 / 유지 조건 |
|---|---|
| `engine/` | 내장 Hermes 원본 및 내부 의존 모듈. 사용하지 않아 보이는 모듈도 초기 이식에서 삭제하지 않는다. |
| `engine/LICENSE`, `UPSTREAM.json`, `PATCHES.diff` | 라이선스, 고정 원본 버전, 기존 엔진 수정 기록 |
| `native_hermes.py`, `native_worker.py` | 백엔드와 분리된 Hermes 프로세스, 도구 RPC, 프로필, 이력, 취소 |
| `native_verification.py`, `instructions/verifier.md` | 최종 답변 독립 검증 및 실패 시 한 번 보완 |
| `config.py`, `app.py`, `router.py`, `control.py`, `store.py` | 설정, 서버 연결, 실행·저장·복구 |
| `executor.py`, `types.py`, `completion.py` 등 나머지 Python 파일 | 결과 계약과 예산·관측·기존 경로 의존성. 초기에는 함께 유지 |
| `tools/`, `domain_metrics.py` | 업무 연결 어댑터와 지표 계산. 사내 업무 정의와 대조 |
| `instructions/`, `skills/` | 도구 사용 지침·업무 지식. 연결되지 않은 도구 안내를 남기지 않는다. |
| `runtime/` | 기존 동기 업무 함수 격리, 생성 Python 코드의 컨테이너 실행 |
| `tests/`, `evals/` | 합성 검증과 실제 서비스 평가 도구 |

Hermes를 별도 설치하거나 최신 upstream으로 교체하지 않는다. 현재 내장 버전은 `e473f5a9c976a0b5bc292aa415dae28c638a47c3`이다. `native_hermes.py`의 루트 경로 계산, `scripts/setup_hermes.sh`의 경로는 사내 디렉터리 구조에 맞게 점검한다.

### 3.2 기존 파일에 필요한 부분만 병합

| 참조 파일 | 반영 목적 |
|---|---|
| `08-YieldAgent/agent_server.py` | lifespan, router 등록, 채팅 분기, 세션 목록/이력/삭제, 산출물 연결 |
| `08-YieldAgent/models.py` | 실행 ID, 이벤트 순번, 추가 입력, 상태, 표, 이력 복구 계약 |
| `08-YieldAgent/common.py` | LLM 설정 연결과 DB 타임아웃 등. 사내 인증·풀·상수는 보존 |
| `yield_db.py`, `yield_viz.py`, `map_agent.py` | 도구에서 사용하는 조회·렌더 함수와 범위/지표 계약 |
| `wads_tools.py`, `fail_history_tools.py`, `ppt_builder.py` | 업무별 데이터·문서·보고서 도구 연결에 필요한 함수 보완 |
| `wiki_queue.py`, `wiki_summarizer.py` | 하네스 메모리 연동을 사용하는 경로에서만 대응 변경 검토 |
| `fail_history_agent.py`, `ppt_llm_designer.py` | 내부 LLM 모델 지정 차이 검토. 사내 모델 정책 보존 |
| `pyproject.toml`, `requirements.txt`, `uv.lock` | 사내 패키지 관리 방식으로 의존성 병합 및 잠금 재생성 |
| `.env.example`, `.gitignore` | 설정 항목과 런타임 파일 제외 규칙 |
| `scripts/setup_hermes.sh` | 내장 엔진 import 확인. 설치/다운로드 스크립트가 아님 |
| `08-YieldAgent/start_servers.sh` | 로컬 실행 참고. 사내 배포 스크립트와 포트를 덮어쓰지 않음 |

위 목록은 기존 파일을 모두 교체하라는 뜻이 아니다. 각 도구의 import와 실제 호출 함수를 추적하여 필요한 변경만 이식한다. 기존 사내 함수가 이미 동일 계약을 만족하면 그대로 호출한다. `lot_history_*`, `relation_tree_agent.py`, `wt_resp_agent.py`, `wiki_store.py`, `result_contracts.py`, `local_trace.py`, `wafer_zones.py` 등 기존 의존 코드도 존재와 계약을 확인한다.

**업무 정의는 사내가 정본이다.** PASS bin 코드, 공정명, 날짜 기준, 재측정 정책, 단위, 수율 분모를 참조 데이터에 맞춰 덮어쓰지 않는다. 특히 PASS bin 문자를 확인하고 계산 코드·렌더러·도구 설명·스킬·테스트가 같은 정의를 사용하는지 대조한다. 불명확하면 업무 담당자에게 해당 정의만 확인한다.

### 3.3 동일 화면을 위한 프런트 변경

`08-YieldAgent/yield_frontend/`의 아래 변경을 사내 화면에 병합한다. 기존 메뉴·에이전트 화면·인증·디자인 체계는 유지한다.

- `src/App.tsx`: 실행 식별, SSE 처리, 추가 입력, 취소, 이력 복구.
- `src/components/AgentPlan.tsx`: 진행 상태 표시.
- `src/components/Artifacts.tsx`: 표·이미지·산출물과 결과 링크.
- `src/components/Hitl.tsx`: 추가 입력.
- `src/components/ToolDetail.tsx`, `src/lib/tool-detail.ts`: 실행 상세와 결과 연결.
- `src/lib/stream.ts`, `src/types.ts`: 스트림 파싱·타입 계약.
- `vite.config.ts`: `/runs`, `/harness` 등 백엔드 프록시. 사내 주소와 포트 유지.
- `package.json`, `package-lock.json`: 필요한 의존성과 테스트 스크립트 병합.
- `playwright.config.ts`, `tests/harness.e2e.spec.ts`, `tests/stream.test.mjs`, `tests/tool-detail.test.mjs`: 화면 회귀 검증.

직접 import하는 기존 컴포넌트가 사내에 없으면 그 의존 파일만 추가한다. 참조 화면의 하네스 기능과 표시 동작을 재현하고, 사내에서 추가한 UI 기능을 제거하지 않는다. `node_modules`, `dist`, 빌드 캐시를 복사하지 않는다.

## 4. 단계 A — 기존 상태와 실행 환경 확인

1. 사내 현재 서버와 화면을 실행하여 기존 대표 질의와 새 에이전트의 정상 사례를 기록한다.
2. 실제 Python 버전, 패키지 잠금 방식, LLM의 tool calling 지원, MongoDB, Oracle, OpenSearch, 프록시·인증을 확인한다.
3. 새 테스트용 MongoDB 이름과 테스트 계정을 준비한다. 운영 데이터 삭제·초기화를 하지 않는다.
4. 참조 코드에서 필요한 라이브러리를 사내 승인 저장소로 설치한다. 참조 requirements는 완전한 최소 의존성 목록이라고 가정하지 않는다. 예를 들어 검증 모듈은 `jsonschema`, 문서 도구는 `bs4`, PPT는 `python-pptx`를 사용한다. 실제 import 및 실행으로 누락을 찾고 사내 잠금 파일에 명시한다.
5. 기존 버전과 충돌하면 충돌 패키지·의존 경로를 기록하고 가장 작은 호환 변경을 적용한다. 사내 환경 전체를 참조 잠금 파일로 교체하지 않는다.

통과 조건: 기존 서비스 기준 동작 기록과 파일/에이전트 대응표가 있고, 사내 승인 LLM·DB 접속 방법이 확정되어 있다.

## 5. 단계 B — 하네스 기반 연결

1. 본체 폴더를 추가하고 기존 서버의 lifespan 안에 `harness.app.lifespan`을 결합한다. 기존 초기화·종료 순서를 유지한다.
2. `harness.router.router`를 등록한다. 원본 라우트는 `/runs`, `/runs/{id}/events`, `/runs/{id}/input`, `/runs/{id}/cancel`, `/harness/results/{id}`, `/harness/artifacts/{id}` 등이다. 일괄 prefix를 추가하면 프런트도 맞춰야 한다.
3. `/chat/stream` 및 세션 관련 기존 API의 호환 분기를 참조 `agent_server.py`에서 확인하여 병합한다. 사내 기존 라우트를 중복 등록하지 않는다.
4. 사내 인증에서 검증된 사용자 ID를 `request.state.principal_id`로 연결한다. 원본의 기본 `local` 사용자 fallback을 사내 다중 사용자 서비스에 그대로 쓰지 않는다. 기존 미인증 요청 처리 정책을 유지한다.
5. `HARNESS_ENGINE=hermes`, 사내 LLM 설정과 테스트 MongoDB를 지정한다. 기존 세션의 엔진은 유지되므로 새로운 테스트 세션을 만든다. 시험 환경에서만 `HARNESS_ENABLED=true`를 적용한다.
6. 신규 실행의 검증용 최소 도구 registry를 구성해 사내 LLM→도구→저장→화면 결과를 확인한다. 이 단계의 합성 도구를 운영 registry에 남기지 않는다. `RunController`의 registry_factory를 이용하는 평가 코드가 참고 구현이다.

설정은 `.env.example`와 `config.py`를 함께 읽는다. 실제 키를 코드·문서·스크린샷에 넣지 않는다. `HARNESS_MODEL`, `HARNESS_BASE_URL`, `HARNESS_API_KEY`, `HARNESS_MONGO_URI`, `HARNESS_MONGO_DB`는 사내 값으로 설정한다. 사내 공급자가 지원하지 않는 `reasoning_effort` 등은 실제 요청으로 확인해 맞춘다.

`.runtime/hermes/profiles/`는 사용자별 런타임 데이터다. 사내에서 쓰기 가능한 지속 저장 경로를 준비한다. 외부 개인 프로필·메모리를 옮기지 않는다. 기존 프로필 스킬은 소스 변경만으로 덮어쓰지 않으므로 버전과 갱신 방법을 별도로 확인한다.

통과 조건: 새 세션에서 실제 사내 LLM이 합성 도구를 호출하고 결과가 MongoDB에 저장되며 응답·취소·재접속이 동작한다. 단순 import 성공으로 단계 완료를 선언하지 않는다.

## 6. 단계 C — 기존 및 신규 사내 에이전트 연결

한 번에 에이전트 하나를 연결하고 성공한 뒤 다음으로 진행한다.

1. 기존 공개 실행 함수와 입력/출력·예외·취소·내부 LLM 호출을 읽는다. 내부 구현을 다시 작성하지 않는다.
2. `harness/tools/`에 어댑터를 추가하거나 기존 어댑터를 사내 함수에 맞춘다.
3. 입력은 `Contract` 기반 Pydantic 모델, handler는 `async handler(args, context)`, 반환은 `ToolResult`를 사용한다. `tools/lot_tools.py`가 기존 함수 연결 예시다.
4. 동기 DB/연산 함수는 기존 `runtime.domain_process.run_blocking` 패턴으로 격리한다. 생성 Python 코드를 호스트에서 직접 실행하는 경로를 만들지 않는다.
5. `ToolSpec(name, description, schema, handler, effect=...)`로 등록하고 `tools/registry.py`의 `domain_registry()`에 연결한다. 설명에는 목적·필수 입력·결과 범위·단위를 적는다.
6. 표는 `rows` 또는 `tables` 중 하나를 사용한다. 빈 결과, 잘림, 일부 실패, 범위·시간 기준·단위·출처를 보존한다. `result_id` 생성과 저장은 기존 executor/store 경계를 사용한다.
7. 현재 native bridge는 `read`와 `artifact`만 노출한다. 영구쓰기 에이전트를 읽기 도구로 위장하지 않는다. 해당 에이전트는 기존 경로를 유지하고 별도 쓰기 권한 계약이 필요한 미연결 항목으로 보고한다.
8. 내부 LLM 호출이 있는 함수는 실제 호출 횟수·예산·취소가 어떻게 관리되는지 확인한다. 하네스 밖의 숨은 무제한 LLM 루프를 만들지 않는다.
9. 직접 실행한 기존 에이전트 결과와 하네스 경유 결과를 같은 입력으로 비교한다. 정상/빈 결과/오류/시간 제한을 확인하고 전체 에이전트 표를 갱신한다.

자연어의 특정 문장을 if/regex로 가로채지 않는다. 잘못된 계획이면 먼저 대화 이력, 이전 답변, 저장 결과 envelope, 도구 계약·설명이 LLM에 전달되는지 확인한다. 도구 선택과 위임은 원본 Hermes 루프에 맡긴다.

## 7. 단계 D — 위임과 독립 검증 유지

- 일반 실행은 `yield_backend`, `yield_evidence`, `skills`, `memory`, `delegation` 도구셋을 사용한다.
- 프로필 `delegation.orchestrator_enabled=true`, `agent.environment_probe=false`를 유지한다. 기존 프로필에도 적용되는지 확인한다.
- worker는 요청 후 종료된다. 원본 `declare_stateless_channel()`을 유지해 하위 결과를 기다린 뒤 최종 응답과 검증을 진행한다. 백그라운드 완료를 받을 별도 소비자가 없는데 이 선언을 지우지 않는다.
- 하위 작업 깊이·개수 제한은 원본 설정을 존중한다. 이식 중 임의로 무제한으로 늘리지 않는다.
- 메인 완료 뒤 독립 검증자는 저장된 근거를 `read_result`로만 읽는다. 일반 조회·코드 실행·메모리 쓰기·추가 위임을 허용하지 않는다.
- 검증 실패 시 한 번 보완·재검증, 판단 불가·예산 소진은 성공 완료와 구분한다. verifier의 `pass`는 업무 정답의 절대 보증이 아니므로 직접 결과 대조도 수행한다.
- 취소 시 worker와 하위 작업이 남지 않는지 확인한다. 최종 답변 문구만으로 위임 성공을 판정하지 않는다. 실제 `delegate_task` 호출, 하위 tool trace, 완료 결과, result_id, 검증 판정을 검사한다.

## 8. 단계 E — 화면 통합

프런트 변경과 서버 계약을 함께 맞춘다. 기존 타입 필드를 삭제하지 말고 실행 ID·이벤트 순번·표 등 필요한 계약을 추가한다.

확인할 화면 동작:

1. 질의를 전송하면 진행 설명과 도구별 상태가 표시된다.
2. 성공·일부 결과·빈 결과·오류가 서로 다르게 표시된다.
3. 도구 실행 상세를 열면 해당 실행의 결과와 연결된다.
4. 표의 페이지 이동과 이미지·HTML·보고서 열기가 동작한다.
5. 추가 입력 대기 중에도 취소할 수 있다.
6. 새로고침·재접속 시 대화와 진행 중 실행이 복구되며 메시지나 산출물이 중복되지 않는다.
7. 기존 사내 에이전트 화면·메뉴·세션 기능이 계속 작동한다.

참조 Playwright 테스트는 API 응답을 모킹한다. 통과해도 실제 서버·DB·LLM 연결 성공을 의미하지 않는다. 다음 단계의 실제 브라우저 실행이 별도로 필요하다.

## 9. 단계 F — 검증 명령과 실제 완료 기준

아래 명령은 참조 디렉터리 구조와 저장소 루트 기준이다. Python 경로·작업 디렉터리·포트는 사내 대응 경로로 바꾼다. 패키지·브라우저 설치는 사내 승인 경로를 사용한다.

```bash
BACKEND_PYTHON=.venv/bin/python bash scripts/setup_hermes.sh
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests/test_native_hermes.py 08-YieldAgent/harness/tests/test_native_transport.py 08-YieldAgent/harness/tests/test_native_verification.py -q
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests -q
```

전체 테스트 중 Docker나 서비스 의존 테스트는 환경 요구를 확인한다. 누락·실패를 숨기거나 테스트를 삭제하지 않는다. 기존 실패와 이번 이식으로 생긴 실패를 구분한다.

프런트 폴더에서:

```bash
npm ci
npm test
npm run build
npm run test:e2e
```

사내에서 Python 도구도 사용할 경우:

```bash
docker build -t yield-harness-python:2 08-YieldAgent/harness/runtime
```

그 다음 **실제 사내 서버와 브라우저**로 아래 시나리오를 수행한다. 화면뿐 아니라 DB 결과·실행 로그도 대조한다.

| 시나리오 | 완료 증거 |
|---|---|
| 이번 주 PT1H 하위 10장 | 기준 날짜/시간대, 주 시작·종료, 포함 범위, 수율 정의, 실제 순위·동점 정책을 직접 DB 조회와 대조 |
| 그 목록 binmap | 직전 선정 wafer ID와 표시된 맵 ID 일치. 데이터 부족 시 사유 표시 |
| 4SS PT1H 보유기간을 하위 에이전트 1명에게 위임 | delegate_task 실제 호출→하위 DB 도구 실행→result_id 회수→검증 판정→화면 응답 |
| 추가된 사내 에이전트 각각 | 같은 입력의 기존 직접 실행과 하네스 결과 일치 |
| 실행 중 취소 및 추가 입력 | 실행 상태 종료/재개와 프로세스·도구 중단 확인 |
| 새로고침·이력 복원 | 이전 답변·결과·이벤트 중복 없음 |
| 사용자 A/B 분리 | 다른 사용자의 실행/결과/산출물 접근 불가 |
| 기존 서비스 회귀 | 단계 A에 기록한 기존 및 신규 에이전트 사례 유지 |

사내 데이터에 4SS/PT1H가 없으면 실제 존재하는 테스트 대상과 변경 이유를 기록한다. 외부 테스트의 날짜·수율·result_id를 정답으로 복사하지 않는다. 최초 실패 후 모델이 재시도한 경우 성공뿐 아니라 재시도도 기록한다.

`harness.evals.run_live`는 설정된 모델에 실제 DB 결과를 보낸다. 반드시 사내 승인 endpoint인지 확인한다. CLI의 `--allow-external-data`는 전달 허용 확인 플래그이며 사내 정책 승인이나 외부 전송 허가를 대신하지 않는다. 사내 레포·로그·DB 결과를 외부 LLM이나 이 기준 GitHub 저장소로 보내지 않는다.

## 10. 완료 보고와 되돌리기

단계별로 작은 커밋을 만든다: 기반→도구 연결→화면→검증 보완. 자동으로 운영 main을 merge/push하지 말고 사내 배포 절차를 따른다.

최종 보고에는 다음을 포함한다.

- 참조 SHA와 사내 기준 SHA, 변경 파일과 이유.
- 전체 에이전트 목록 및 연결/기존 유지/미연결 사유.
- 실제 실행한 명령과 결과, UI 확인 항목, run_id와 사내에서만 접근 가능한 증거 위치.
- 위임 실행과 독립 검증 증거, 현재 환경 탐색 설정.
- 실패·미검증·환경 의존 항목. lint/build/합성 테스트만으로 전체 완료를 선언하지 않는다.
- 되돌릴 커밋과 설정, 기존 세션 처리 방법.

`HARNESS_ENABLED=false`는 새 세션 선택에 영향을 주며 기존 하네스 세션을 자동으로 legacy로 변환하지 않는다. 되돌릴 때는 활성 실행을 확인하고 정상 종료하거나 취소한 후 이전 배포로 전환한다. 기존 세션·MongoDB·프로필을 삭제하는 방식으로 해결하지 않는다. 시험 DB는 운영 DB와 분리해 유지한다.

## 11. 이식에 포함하지 않을 것

개인 `.env`, API 키, `.venv`, `node_modules`, `.runtime`의 개인 메모리, `outputs/`의 실제 조회 결과, 스크린샷, `.superpowers`, 개인 `.codex`, 디자인 목업은 전달 소스에 포함하지 않는다. 소스는 커밋된 추적 파일을 기준으로 준비한다. 사내로의 반입 또한 회사의 승인된 절차를 따른다.
