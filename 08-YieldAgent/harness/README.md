# 목표 기반 Yield Harness

웹 채팅은 고정된 Hermes 원본의 `AIAgent.run_conversation`으로 실행한다. [채팅 진입점](hermes_chat.py)이 사용자별 프로필과 실행을 연결하고 [도구 등록](native_tools.py)이 같은 프로세스의 Hermes 레지스트리에 업무 도구를 추가한다.

[2026-10-31 이식 안내](INTERNAL_MIGRATION_2026_10_31.md)와 [기존 이식 문서](MIGRATION.md)는 과거 구성의 참고 기록이다. 현재 실행·도구·격리 계약은 이 README를 따른다.

모델이 조회·분석 도구를 선택하고 결과를 읽어 다음 행동이나 최종 답변을 결정한다. 기존 웹 콘솔은 게이트웨이에서 세션 이벤트와 최종 답변을 받고, 사용자와 세션에 귀속된 URL로 표와 웨이퍼 맵을 표시한다.

## 실행

`08-YieldAgent` 디렉터리에서 실행한다. 운영 환경에는 MongoDB·Oracle·모델 공급자와 [격리 backend](isolation/README.ko.md) 설정이 필요하다. `Settings.from_env()`는 항상 격리 설정을 읽고 `no_yield_tools=True`를 반환한다.

```bash
../.venv/bin/python -m uvicorn harness.app:app --port 8000
```

이 명령은 `/runs`·결과·산출물 API를 제공한다. 웹 채팅에는 저장소에 포함된 `harness.dev.local_gateway_app`이 이 API와 Hermes 게이트웨이를 함께 연결한다.

### Ubuntu 또는 macOS에서 웹 채팅 실행

새 체크아웃에서 Python 3.11과 Node.js 22.12 이상을 사용한다. 개발 환경의 모델 공급자, Oracle, MongoDB, `HARNESS_PRINCIPAL` 설정을 환경 변수나 저장소 루트의 로컬 `.env`에 준비한다. 로컬 게이트웨이는 Ubuntu에서 `bwrap`, macOS에서 Seatbelt를 사용한다. [분석 실행 환경](isolation/README.ko.md)에서 OS별 준비와 확인 방법을 확인한다. 별도 검증 환경을 띄우면 전용 `HARNESS_MONGO_DB`, principal, 격리 작업 디렉터리를 사용한다. 사용자 스킬과 메모리는 프로필 데이터로 별도 보관한다.

저장소 루트에서 첫 번째 터미널로 게이트웨이를 실행한다.

```bash
uv sync --locked
HARNESS_GATEWAY_PORT=61402 HARNESS_WEB_PORT=61413 \
  uv run --locked bash 08-YieldAgent/harness/dev/launch_local_gateway.sh
```

두 번째 터미널에서 같은 체크아웃의 콘솔을 실행하고 [새 채팅](http://127.0.0.1:61413/)을 연다. 포트를 바꿀 때는 양쪽 명령에 같은 포트 쌍을 적용한다.

```bash
cd 08-YieldAgent/harness_console
npm ci
VITE_HARNESS_LOCAL_GATEWAY=1 HARNESS_GATEWAY_TARGET=http://127.0.0.1:61402 \
  npm run dev -- --port 61413
```

2026-09-12 사용자가 크레딧을 추가한 뒤 지정한 공급자는 OpenRouter, 모델은 `z-ai/glm-5.3-flash`다.

```dotenv
HARNESS_MODEL=z-ai/glm-5.3-flash
HARNESS_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=...
HARNESS_MAX_OUTPUT_TOKENS=8192
HARNESS_ANSWER_OUTPUT_TOKENS=4096
HARNESS_REVIEW_OUTPUT_TOKENS=2048
HARNESS_EVIDENCE_TOKENS=2000
HARNESS_TEMPERATURE=1.0
HARNESS_REASONING_EFFORT=high
```

공급자 주소에 맞는 키만 선택한다. Groq를 선택할 때에는 `GROQ_API_KEY`와 소문자 `groq_api_key`도 인식한다. `HARNESS_API_KEY`는 명시적인 키 재정의다. 다른 호환 공급자를 사용할 때에는 모델·주소·키·출력 길이·추론 옵션을 함께 바꾼다. 모델 이름만 바꾸어 다른 공급자로 자동 전환하지 않는다.

이전 Qwen 무료 계정 시험의 1,000토큰 출력 제한을 해제했다. 현재 GLM은 추론 강도를 `high`로 명시한다. 공급자 기본 `max`를 사용한 실제 복합 요청에서 검증의 90초 시간 초과와 답변의 8,192토큰 출력 한도 소진이 관찰됐다. 동일 요청은 `high`에서 총 91,579토큰으로 답변·검증을 완료했다. 모델과 총 120,000토큰·출력 8,192토큰·호출 90초 한도는 유지했다. 이는 해당 시험 결과이며 모든 요청의 완료를 보증하지 않는다.

GLM 가상 데이터 시험에서 temperature=0의 반복 코드 생성이 관찰되어 temperature=1.0을 비교한 뒤 적용했다. 명시적으로 전달한 temperature는 환경 설정보다 우선한다. `Retry-After`가 있으면 이를 지켜 최대 3회 시도하고 실행 시간 한도와 취소는 계속 적용한다. 다른 모델로 자동 대체하지 않는다. 재시도 예산이 부족해도 앞선 공급자 오류를 보존하며, `model_usage`에는 오류 유형·HTTP 상태·경과 시간을 기록한다. 실패한 호출의 사용량을 받지 못하면 예약분을 유지하므로 `usage.tokens`에는 실제 확인량과 미확인 예약량이 함께 들어갈 수 있다.

## 실행 경계

Hermes/native가 유일한 실행 경로다. 이전 standalone supervisor 서버와 세션 선택 플래그에 의한 전환은 제거되었다.

현재 기본 principal은 서버가 지정하는 `local`이다. 기존 로컬 단일 사용자 운영을 지원한다. 다중 사용자 서버는 인증 미들웨어에서 검증한 `request.state.principal_id`를 제공해야 한다. 요청 본문의 사용자 ID를 데이터 접근 권한으로 사용하지 않는다.

## 실행 계약

- `POST /runs`: `session_id`, `request_id`, `query`로 실행 생성. 같은 요청 ID의 중복 전송을 합친다.
- `GET /runs/{run_id}`와 `GET /runs/{run_id}/events?after=...`: 현재 상태와 저장된 순서의 이벤트를 읽는다.
- `POST /runs/{run_id}/cancel`: 도메인 프로세스·Python 컨테이너·자식 조사를 함께 중지한다.
- `POST /runs/{run_id}/input`: `kind=input|steer`, `request_id`, `goal_revision`, `value`; 질문 답변에는 `interrupt_id`도 필요하다.
- `/harness/results/{result_id}`와 `/harness/artifacts/{artifact_id}`: 같은 principal과 대화의 원본·산출물만 읽는다.

웹 채팅의 `yield` 도구 묶음은 `query_db`, `list_tables`, `read_result`, `render_yield_table`, `load_wafer_maps`, `run_python`으로 구성된다. 각 핸들러는 현재 사용자의 세션과 실행에 연결된 `ToolExecutor`를 호출한다. 도구를 추가할 때는 `harness/tools/registry.py`의 `domain_registry()`에 정의를 등록한다. 기존 MCP 서버는 별도로 실행할 수 있다.

일반 채팅에서는 Hermes의 스킬, 메모리, 같은 프로필의 대화 검색, 사용자 질문도 사용한다. 브라우저에 표시할 파일은 도구 응답의 `artifact_refs[].markdown`으로 전달한다. `yield_web` 채널은 Hermes의 로컬 파일 전송 지시를 제거해 임시 작업 경로가 깨진 첨부로 표시되는 것을 방지한다. 합성 데이터를 주입하는 평가용 레지스트리는 기존 평가 경로를 유지한다.

도구 결과에는 전체 행 수, 범위, 출처, 원본 ID, 성공·부분·빈 결과·오류 구분이 있다. 작은 preview와 별개로 원본 전체를 저장한다. `read_result`는 원본 페이지를, `run_python`은 지정한 결과의 전체 데이터를 읽는다. 신규 채팅은 Hermes가 반환한 최종 답변을 게시한다. 구버전 실행 이력은 당시 저장한 검증 판정과 산출물 정책을 사용한다.

Mongo lease와 epoch가 실행 소유권을 정한다. 이전 서버의 늦은 저장이 새 서버의 상태를 덮지 않도록 실행·종료·결과 저장을 제한한다. Hermes 실행의 대화와 사용량은 저장소에 보존한다.

실행권 갱신이 실패하면 진행 중인 작업을 중지하고 `lease_renewal_failed`로 기록한다. 같은 epoch의 실행권이 만료되면 `lease_expired`로 종료하며, 이미 다른 실행자가 인계받았다면 이전 실행자가 종료 상태를 덮지 않는다. 종료 상태를 저장하는 동안 heartbeat를 중지해 반복 취소가 저장을 끊지 않게 한다.

## MCP

프로젝트 루트에서 실행한다.

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.mcp_server
```

MCP 호스트에는 이 명령과 작업 디렉터리를 등록한다. 공통 registry에 등록된 도구와 산출물 resource를 제공한다. 연결별 세션이 기본이며, 같은 세션을 지정하려면 `HARNESS_MCP_SESSION_ID`를 사용한다.

## 도메인 범위와 현재 제한

`list_tables`로 스키마를 확인하고 `query_db`로 조회하며, `read_result`·`run_python`으로 저장된 근거를 읽고 계산한다. `render_yield_table`은 수율 표를, `load_wafer_maps`는 웨이퍼 맵 자료를 제공한다. 이전 전용 도메인 bundle과 wafer-set 도구는 등록되거나 실행되지 않는다.

검증 명령과 실제/가상 자료의 구분은 [평가 안내](evals/README.md)를 따른다. 단위 테스트 통과나 일부 실제 시나리오 성공을 전체 운영 전환 승인으로 취급하지 않는다.

## v2 실행과 안전한 전환

신규 실행은 `harness/v2`, 표 계약은 `harness-observation/v2`이다. 완료된 과거 기록과 단일 표 결과는 읽기 어댑터로 유지한다. 다른 하네스 엔진의 저장된 실행은 Hermes로 바꾸지 않으며 실행 시 명시적으로 거부한다.

여러 표는 `tables[result_id][table_id]`로 계산한다. 단일 표에는 기존 `datasets[result_id]`도 제공한다. 문서 페이지와 표의 일부 자료는 전체성 플래그와 원본 계보가 후속 계산까지 전달된다.

고정 MCP 세션은 살아 있는 소유자가 있으면 충돌을 반환한다. lease가 만료된 연결은 epoch 경계에서 종료하고 저장 결과를 유지한 새 연결을 만든다. 연결의 유휴 시간은 조사 시간에 합산하지 않는다.

롤백은 v2 결과 읽기 어댑터가 있는 호환 빌드로만 한다. 새 실행 진입을 막고 활성 작업을 정리한 뒤 전환한다. v2 자료를 읽지 못하는 예전 빌드로의 즉시 롤백은 지원하지 않는다.


### Bundled Hermes engine (2026-10-06)

Only `HARNESS_ENGINE=hermes` is accepted. Historical run documents retain their engine metadata, but non-Hermes runs cannot be launched, recovered, or steered. The engine source is included in `harness/engine/`, currently pinned to upstream `5894e750c3dba980d43b25aa29e3958149ba2c61` (2026-10 sync; `engine/UPSTREAM.json`). It runs with the backend's Python 3.11 interpreter. No Hermes package, external checkout, Python 3.14, or separate server is required. A child process isolates upstream module names from the backend; the backend owns its lifetime.

Install the ordinary libraries declared in the repository `requirements.txt`/`pyproject.toml` through your approved package process. `scripts/setup_hermes.sh` now only verifies imports; it does not install anything. Do not install from `engine/pyproject.toml`: that is retained upstream metadata, not this backend's dependency specification.

The vendored engine matches the upstream hashes in `engine/UPSTREAM.json`. The web chat launches one Hermes process per run, with the authenticated user's persistent profile. The `yield` toolset uses Hermes' native registry and dispatch. The host persists sessions, results and artifact URLs for the existing web console.

For integration into a diverged internal main branch, including existing/new agents and the chat UI, follow [the Luna porting guide](INTERNAL_PORTING_LUNA.md).

For migration, see [MIGRATION.md](MIGRATION.md). Source portability and wafer-answer quality have separate acceptance checks. The three repeated live tests on the bundled engine also scored **0/3**: one budget interruption and two map outputs with incorrect period/parameter scope. Source relocation alone does not correct those errors. This is not a claim that all Hermes CLI, gateway or optional integrations are enabled or tested.
