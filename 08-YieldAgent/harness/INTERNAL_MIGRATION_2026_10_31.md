# 현재 Yield Agent를 보안망에 이식하기

이 문서를 포함한 커밋을 참조 소스로 사용한다. 사내 최신 코드와 기존 업무 기능을 보존하면서 하네스, 도구, 저장소, 화면을 단계별로 병합한다. 사내 코드나 실제 업무 데이터는 외부 저장소에 업로드하지 않는다.

이 문서는 2026-10-31 코드의 실행 경로와 최근 `read_result` 수정을 반영한다. 기본 이식 절차는 [기존 사내 이식 안내](INTERNAL_PORTING_LUNA.md)를 따른다. 이전 문서와 현재 코드가 다르면 현재 코드의 조건을 확인하여 적용한다. 특히 [MIGRATION.md](MIGRATION.md)의 일반 process 실행 설명과 `HARNESS_NO_YIELD_TOOLS=1`의 SSH 격리 조건을 혼동하지 않는다.

## 1. 참조 커밋과 사내 기준점을 고정한다

참조 저장소는 `https://github.com/dorothy90/map_agent.git`, 브랜치는 `codex/goal-driven-harness`다. 브랜치 이름만 기록하면 이후 변경과 구분할 수 없다. 반입한 소스에서 다음 명령을 실행하고 전체 SHA를 사내 작업 기록에 남긴다.

```sh
git rev-parse HEAD
git status --short
git log -1 --format='%H %s'
```

이 문서에 미래 커밋 SHA를 미리 적지 않는다. 실제 전달 커밋은 위 명령의 결과다. Git 메타데이터 없이 소스만 반입했다면 외부 전달자가 기록한 SHA와 파일 목록을 함께 받는다.

사내 최신 main에서 별도 이식 브랜치를 만든다. 외부 소스는 읽기 전용으로 두고 기존 사내 파일과 대응표를 만든다. `agent_server.py`, `common.py`, 사내 인증, 기존 프런트엔드를 통째로 덮어쓰지 않는다. 하네스에 연결할 기존 및 신규 에이전트의 입력, 출력, 의존 서비스, 읽기·쓰기 효과를 먼저 기록한다.

사내 `docs/harness-porting/STATUS.md`에 다음 항목을 유지한다.

- 참조 전체 SHA, 사내 시작 SHA, 작업 브랜치.
- 원본 파일과 사내 대응 파일, 병합한 동작, 보존한 사내 동작.
- Python, Node.js, npm, 패키지 목록과 잠금 파일의 실제 버전.
- 실제 사내 모델 ID, 접속 대상 이름, 인증 방식. 비밀값은 제외한다.
- 실행 명령, 통과·실패·미검증 시나리오, 생성한 테스트 데이터 위치.
- 단계별 변경 커밋과 되돌릴 수 있는 직전 배포 버전.

## 2. 소스와 실행 데이터를 분리해 반입한다

인터넷 연결 구간에서 전달 커밋을 checkout한 뒤 소스 묶음과 SHA를 만든다. 사내 반입 절차로 두 파일을 함께 전달한다.

```sh
git rev-parse HEAD > yield-agent-migration.sha
git archive --format=tar.gz --output=yield-agent-migration.tar.gz HEAD
```

`git archive`에는 해당 커밋이 추적하는 파일만 들어간다. wheelhouse와 npm 캐시는 포함되지 않으므로 다음 절차에서 별도로 준비한다. 사내에서는 새 참조 디렉터리에 압축을 풀고 `.sha` 파일을 보관한다. 기존 사내 작업 디렉터리 위에 압축을 풀지 않는다.

같은 커밋의 다음 소스를 함께 읽는다.

- `08-YieldAgent/harness/`는 설정, 실행 제어, 결과 저장, 도구, 프로필 연결, 격리 실행, 테스트를 포함한다.
- `08-YieldAgent/harness/engine/`는 내장 Hermes 소스다. `UPSTREAM.json`, `PATCHES.diff`, `LICENSE`를 함께 보존한다. 현재 원본 고정값은 `UPSTREAM.json`에서 읽는다. 이 문서 작성 시 값은 `5894e750c3dba980d43b25aa29e3958149ba2c61`이다.
- `08-YieldAgent/harness_console/`는 별도 웹 프런트엔드다. `src`, 설정, `package.json`, `package-lock.json`, 테스트를 함께 반입한다.
- `08-YieldAgent/agent_server.py`와 기존 업무 모듈은 서버 초기화, 라우터, Oracle·OpenSearch 연결의 병합 근거다.
- 루트 `.env.example`, `pyproject.toml`, `uv.lock`, `requirements.txt`를 확인한다. Python 의존성의 기준은 `pyproject.toml`과 `uv.lock`이다. `harness/engine/pyproject.toml`로 엔진 자체를 설치하지 않는다.

`08-YieldAgent/sql/`, seed 스크립트와 `seed_data/`는 스키마·합성 데이터 참고 자료다. DB 쓰기를 수행하는 스크립트가 있으므로 설치 단계에서 자동 실행하지 않는다. 운영 스키마에 적용할 변경은 사내 DB 담당 절차로 별도 검토한다.

개발 PC의 `.venv`, `node_modules`, 캐시, 실제 `.env`, 로컬 SSH 개인키, 실제 DB 덤프, 대화 로그는 소스 설치물이 아니다. 별도로 승인된 실행 데이터 이관 절차가 없으면 반입 대상으로 삼지 않는다. 필요 패키지는 대상 Linux와 CPU 아키텍처에 맞춰 준비한다. macOS 가상환경을 Linux로 복사하지 않는다.

MongoDB에는 실행, 결과 메타데이터, 전체 표와 산출물 GridFS, 체크포인트가 있다. 사용자 프로필은 현재 `native_hermes.py`가 `<저장소 루트>/.runtime/hermes/profiles/<principal_hash>` 아래에 만든다. 실행 데이터 이관이 필요하면 MongoDB와 해당 프로필을 함께 다룬다. 이 위치를 변경할 때 존재하지 않는 환경변수 이름을 임의로 만들지 말고 실제 경로 생성 코드를 사내 저장소 계약에 맞춰 병합한다. 프로필에는 메모리와 스킬 상태가 있으므로 소스 배포로 초기화하지 않는다.

## 3. 폐쇄망 의존성을 준비한다

Python 3.11을 기준으로 사내 서버 환경과 호환성을 확인한다. 내장 엔진은 서버의 Python 자식 프로세스로 실행되며 별도 Hermes 설치, 외부 Git checkout, Python 3.14가 필요하지 않다. 외부 자동 설치와 업데이트를 운영 시작 단계에 추가하지 않는다.

루트 `pyproject.toml`과 `uv.lock`을 먼저 비교한다. 이번 전달 준비에서 `uv lock --check --offline`은 통과했다. `requirements.txt`는 여러 튜토리얼과 기존 서비스의 의존성을 포함하고 다수 항목이 하한 버전이며, `python-pptx`와 MCP 상한 등에서 프로젝트 선언과 차이가 있다. 따라서 requirements 파일만으로 환경을 재현하지 않는다. 사내 기존 의존성과 잠금 목록을 병합하고, 사내에서 실제 통과한 버전을 `requirements.internal.lock.txt` 같은 고정 목록에 기록한다. 이 파일명은 사내에서 생성할 산출물이며 현재 저장소에 이미 존재한다는 뜻이 아니다.

사내 패키지 미러를 사용하거나 대상 플랫폼에서 준비한 wheelhouse를 반입한다. 승인된 고정 목록이 있을 때 다음과 같이 설치할 수 있다.

같은 커밋의 `uv.lock`에서 생성한 [참조용 고정 목록](../../docs/migration-evidence/2026-10-31/requirements.lock.txt)을 함께 제공한다. 개발 테스트 패키지는 제외된 목록이다. 사내 기존 패키지와 충돌을 검토하고 대상 Python·Linux에 맞는 wheel을 준비한다. 이 목록이 macOS에서 설치된 전체 환경을 복사한 것이거나 사내 설치 검증을 마친 목록이라고 해석하지 않는다. 재생성 명령은 다음과 같다.

```sh
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.reference.lock.txt
```

사내에서 검토한 최종 목록을 사용해 설치한다.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links /approved/wheelhouse -r requirements.internal.lock.txt
.venv/bin/python -m pip check
BACKEND_PYTHON="$PWD/.venv/bin/python" bash scripts/setup_hermes.sh
```

`/approved/wheelhouse`는 실제 반입 경로로 바꾼다. `setup_hermes.sh`는 import 확인용이다. 패키지를 설치하거나 사내 서버 통합을 검증하지 않는다. 회귀 테스트에는 `pytest`가 필요하다. 기존 `pytest.mark.asyncio` 테스트를 실행하려면 `pytest-asyncio`도 준비한다.

프런트엔드는 [Console README](../harness_console/README.md)의 Node.js 요구사항과 `package-lock.json`을 확인한다. Node.js 22.12 이상 또는 20.19 이상이 현재 문서 기준이다. 전달 준비 환경은 Python 3.11.14, Node.js 25.1.0, npm 11.6.2였으므로 사내에서 선택한 LTS 버전은 별도 빌드 검증 대상이다. 사내 npm 미러로 같은 잠금 파일을 설치한다.

```sh
cd 08-YieldAgent/harness_console
npm ci --registry https://npm.internal.example
npm run build
```

위 URL은 실제 사내 주소로 바꾼다. 네트워크가 전혀 없으면 잠금 파일이 요구하는 모든 npm tarball을 승인된 캐시에 준비한 뒤 `npm ci --offline --cache /approved/npm-cache`를 사용한다. 캐시 누락을 외부 자동 다운로드로 해결하지 않는다. 개발 서버의 프록시 설정은 빌드된 정적 파일에 포함되는 운영 프록시가 아니다.

## 4. 모델과 저장소를 사내 서비스에 연결한다

[config.py](config.py)의 `Settings.from_env()`가 실제 설정 진입점이다. 초기에는 별도 검증 DB와 새 세션을 사용한다. 다음 값은 이름과 형식의 예시이며 접속 가능한 실제 환경값은 아니다.

```dotenv
HARNESS_ENABLED=true
HARNESS_ENGINE=hermes
HARNESS_BASE_URL=https://llm.internal.example/v1
HARNESS_MODEL=internal-approved-model
HARNESS_API_KEY=
HARNESS_MONGO_URI=mongodb://mongo.internal.example:27017
HARNESS_MONGO_DB=yield_agent_porting_validation
HARNESS_TIMEZONE=Asia/Seoul
HARNESS_PYTHON_RUNTIME=process
HARNESS_PYTHON_EXECUTABLE=/app/.venv/bin/python
```

LLM 키는 사내 비밀 관리 방식으로 주입한다. 외부 기본값인 OpenRouter 주소와 모델명을 사내 환경에 그대로 두지 않는다. 내부 OpenAI 호환 API가 도구 호출, 스트리밍, 출력 토큰 제한, 사용량 반환, 설정한 temperature와 reasoning 옵션을 지원하는지 실제 요청으로 확인한다. 모델이 지원하지 않는 reasoning 옵션은 비워 두며 실패를 문자열 조작으로 우회하지 않는다. 기업 CA가 필요하면 검증 가능한 인증서 신뢰 설정을 적용한다. TLS 검증을 일괄 해제하지 않는다.

`HARNESS_ENABLED`는 문자열 `true`, `HARNESS_NO_YIELD_TOOLS`는 문자열 `1`을 사용한다. 기본 `Settings()`와 `Settings.from_env()`의 엔진 기본값이 다르므로 테스트에서도 엔진을 명시한다. 기존 실행과 세션은 저장된 엔진을 유지할 수 있으므로 새 세션에서 전환을 확인한다.

Oracle 연결은 `common.py`의 `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`, `ORACLE_CALL_TIMEOUT_MS`와 실제 사내 pool 생성 경로를 확인한다. 사내 기존 연결 모듈을 재사용할 수 있다. 수율·맵·파라미터의 스키마, 기간 정의, 단위, 빈 결과 처리도 대응표에 기록한다. 외부 개발용 seed 데이터나 DDL을 운영 DB에 자동 적용하지 않는다.

문서 검색을 연결할 때 `fail_history_tools.py`의 `OPENSEARCH_HOST`, `OPENSEARCH_PORT`, `OPENSEARCH_INDEX`, `OPENSEARCH_USER`, `OPENSEARCH_PASSWORD`, `OPENSEARCH_USE_SSL`과 클라이언트 생성 코드를 확인한다. 기존 기본 계정값을 운영 인증으로 사용하지 않는다. 사내 인덱스와 임베딩·검색 계약은 실제 서비스에 맞춘다. 환경변수만 바꿨다는 이유로 인증서나 인덱스 호환까지 검증됐다고 기록하지 않는다.

## 5. Docker 없는 Python 실행 방식을 고른다

웹 채팅에서는 `execute_code`가 격리된 `SessionKernel`에서 Python을 실행한다. 공유 `ProcessRuntime`과 `worker.py`는 별도 프로세스 실행 및 테스트용으로 유지한다. Docker 데몬이 필요하지 않다. API 서버 내부에서 생성 코드를 직접 실행하도록 바꾸지 않는다. 분석 환경에는 실제 분석에 필요한 pandas, numpy, scipy, plotly, statsmodels를 준비한다. 자세한 계약은 [Kubernetes Python 실행](MIGRATION.md#kubernetes-python-실행)을 따른다.

`.venv`는 보안 샌드박스가 아니다. 일반 process 방식은 API와 같은 OS 사용자 또는 Pod 권한을 공유할 수 있다. 읽기 전용 루트 파일시스템을 사용하면 작업용 임시 디렉터리에 쓰기 가능한 볼륨이 필요하다. 사내 실행 정책에 맞춰 Pod 자원과 네트워크 권한을 결정한다.

`Settings.from_env()`는 `no_yield_tools=True`를 설정한다. `domain_registry()`는 다섯 도메인 도구를 등록하며 Python 실행 도구는 포함하지 않는다. `HARNESS_NO_YIELD_TOOLS`로 이전 Python 도구를 활성화하지 않는다. 웹의 `execute_code`는 격리된 세션 커널을 사용하며 terminal·file 계열도 설정된 격리 backend를 사용한다. [격리 설치 안내](isolation/README.ko.md)를 함께 적용한다.

이 격리 모드는 localhost SSH, 고정 사용자 `yieldrun`, 관리자가 소유한 launcher·분석 venv, 저장소 밖의 절대 경로를 요구한다. `work_root`는 `home` 바로 아래에 있어야 한다. Linux에서는 `bwrap`와 namespace 지원이 필요하다. `auto`도 격리 없는 실행으로 전환하지 않는다. SSH가 없는 Kubernetes Pod에 이 옵션만 설정하면 작동하지 않는다. 대상 Pod에서 이 계약을 제공할 수 없으면 해당 모드 도입을 미검증으로 기록하고 사내 실행 서비스에 대한 별도 설계를 한다. 일반 process 방식으로 몰래 fallback하지 않는다.

## 6. 서버와 화면을 사내 인증에 병합한다

`native_worker.py`는 내장 `AIAgent`를 호출한다. `native_hermes.py`는 도구, 결과 저장, 이력, 취소, 사용자 프로필을 연결한다. `executor.py`와 `store.py`는 도구 실행 ledger, 소유권, 결과 전체 저장을 담당한다. 사내 서버의 시작·종료 처리에 `RunController`와 `HarnessStore` 수명을 연결한다.

`agent_server.py` 전체를 복사하지 말고 하네스 라우터, 초기화·복구·종료, 실행·취소·후속 입력, 결과·산출물 조회 부분을 병합한다. 기반만 검증하려면 `harness/app.py`가 독립 FastAPI 진입점이지만 전체 기존 서비스와 gateway를 대신하지는 않는다.

저장소 루트에서 개발 검증용으로 다음 진입점을 사용할 수 있다.

```sh
PYTHONPATH=08-YieldAgent .venv/bin/python -m uvicorn harness.app:app --host 127.0.0.1 --port 8001
```

전체 서비스는 `08-YieldAgent`를 작업 디렉터리로 하여 사내 가상환경의 Python으로 `-m uvicorn agent_server:app`을 실행한다. 기존 서버의 수명주기와 인증을 먼저 병합한 뒤 실행한다. Pod bind 주소와 ingress는 사내 배포 구성으로 정한다. 로컬 개발용 `start_servers.sh`를 운영 배포 명령으로 간주하지 않는다.

사내 인증 결과의 사용자 ID를 principal에 연결한다. `HARNESS_PRINCIPAL=local`을 모든 사용자의 운영 ID로 사용하지 않는다. 사용자·세션·실행·epoch 경계를 유지하고, 다른 사용자의 `result_id`와 artifact를 읽을 수 없는지 검증한다. 클라이언트가 보내는 사용자 ID를 서버 검증 없이 신뢰하지 않는다.

현재 Console은 두 진입 경로가 있다. `src/main.tsx`는 기본 `App`을 열고 URL의 `?ui=hermes`가 있을 때 `src/web/entry`를 선택한다. 새 Hermes 화면을 배포했다는 이유로 기본 경로까지 전환됐다고 판단하지 않는다.

새 화면의 `webConnectionResolver`는 같은 서비스의 `/api/ws`를 요구한다. 반면 현재 `agent_server.py`는 `GATEWAY_M1_ENABLED=true`일 때 `/gateway-m1` 아래에 gateway를 등록한다. 기본 `DenyPrincipalResolver`, origin 정책과 ticket 검증은 허용하지 않는 구성이므로 실제 사내 인증 구현을 주입해야 한다. `harness/gateway/routes.py`의 `build_gateway_router()`와 제공되는 session/run handler를 조사하고, 사내 경로·WebSocket upgrade·ticket·origin 계약을 연결한다. 기능 manifest에 존재한다고 모든 RPC가 연결된 것은 아니다. [Console 제한 사항](../harness_console/PR10_LIMITS.md)을 함께 확인한다.

`vite.config.ts`의 `HARNESS_GATEWAY_TARGET`은 일부 gateway·결과 경로에만 적용된다. `/runs`, `/harness`, `/sessions`, `/session`, `/health` 프록시 묶음에는 `127.0.0.1:8001`이 남아 있다. 개발 대상 주소 변경 시 이 묶음도 확인한다. 운영에서는 정적 웹과 API의 실제 origin, WebSocket, SSE의 버퍼링·timeout을 reverse proxy에 구성하고 검증한다.

## 7. 최근 read_result 수정을 함께 이식한다

이 수정은 아래 파일들을 하나의 동작 변경으로 병합한다. `types.py`의 10행 제한만 제거하면 저장소의 50행 제한과 대화 예산 단계에서 다시 잘린다.

- `types.py`의 `ResultPage`와 `ToolObservation.page`는 페이지 대상 표, 원본 행 수, offset, 요청 limit을 보존한다. 공통 `ResultPage.project()`가 최종 표시 행 수로 안내와 `visible_rows`, `next_offset`을 만든다.
- `tools/registry.py`의 `ToolResult.page`와 `executor.py`가 이 정보를 결과 저장까지 전달한다.
- `tools/python_tools.py`와 `tools/wafer_set_read.py`는 기존 result_id 경로와 source handle 경로에 같은 페이지 정보를 제공한다.
- `store.py`는 페이지 대상의 최대 요청 1,000행과 1MiB 미리보기 상한을 적용한다. 일반 도구의 저장 미리보기는 기존 50행·32KB 제한을 유지한다. 전체 원본 표는 별도로 저장한다.
- `ToolObservation.model_view()`는 페이지 대상에 요청 limit을 적용하고 일반 도구의 10행 제한을 유지한다.
- `context.py`는 토큰 예산으로 표시 행을 다시 줄인 뒤 같은 페이지 projection을 호출한다.
- `tests/test_read_result_projection.py`와 `evals/read_result_page.py`는 회귀와 실제 경로 확인용이다.

읽은 행 수와 표시한 행 수를 구분한다. handle의 저장된 `page_meta`, table version, 원본 GridFS를 표시 행 수에 맞춰 다시 쓰지 않는다. 기존 저장 결과에 `page` 정보가 없으면 자동 변환하지 않는다. 서버 코드를 재시작한 뒤 새 `read_result` 호출로 확인한다. 한 행 자체가 바이트 상한을 넘으면 컬럼을 줄여 조회해야 할 수 있다.

이식 과정에서 안내 문구를 파싱하거나 한국어 키워드, 정규식, 특정 실패 문구, few-shot 추가로 페이지 동작을 결정하지 않는다. 구조화된 페이지 정보를 사용한다.

## 8. 단계별 실제 검증을 수행한다

먼저 승인된 패키지 import와 사내 검증 MongoDB 연결을 확인한다. 이어서 최소 읽기 도구를 실제 LLM이 선택하고 전체 결과가 저장되는 경로를 검증한다. 업무 도구는 이 기반 검증 다음에 하나씩 연결한다. `registry_factory`를 사용한 검증 레지스트리를 운영 도구 목록으로 남기지 않는다.

관련 회귀 테스트는 저장소 루트에서 실행한다.

```sh
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest -q 08-YieldAgent/harness/tests/test_read_result_projection.py 08-YieldAgent/harness/tests/test_store.py 08-YieldAgent/harness/tests/test_result_tables.py 08-YieldAgent/harness/tests/test_context.py 08-YieldAgent/harness/tests/test_context_budget.py 08-YieldAgent/harness/tests/test_simplification.py 08-YieldAgent/harness/tests/test_publication.py 08-YieldAgent/harness/tests/test_empty_result_signal.py
```

이 테스트 묶음의 실제 MongoDB 테스트는 기본 localhost MongoDB와 UUID 이름의 테스트 DB를 사용하고 종료 시 자신이 만든 DB를 삭제한다. 운영 DB를 대상으로 바꾸지 않는다. localhost MongoDB를 제공할 수 없으면 테스트 fixture의 연결 주입을 사내 검증 환경에 맞춰 수정하고 그 차이를 기록한다. 환경변수 하나로 모든 기존 테스트의 연결 대상이 바뀐다고 가정하지 않는다.

실제 모델 검증은 사내 LLM과 별도 MongoDB 설정을 주입한 후 실행한다.

```sh
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.read_result_page --live-model --output outputs/read-result-page/internal-live-model.json
```

이 명령은 합성 158행을 실제 MongoDB에 저장하고, LLM이 `read_result(limit=158)`를 호출하게 한 뒤 마지막 행의 무작위 값을 읽었는지 검사한다. 별도 UUID 테스트 DB를 만들고 정리한다. 성공 조건은 `passed=true`, `model_rows=158`, `reload_rows=158`, `last_row_verified=true`다. 행 내용과 자격증명을 보고서에 남기지 않는다.

2026-10-31 로컬 근거는 관련 pytest 155개 통과, 기존 비동기 테스트 본문 4개 별도 통과, 실제 MongoDB → NativeBridge → 구성된 LLM에서 마지막 행 검증 성공이다. 당시 pytest-asyncio가 없어 비동기 테스트는 직접 실행했다. 반입된 [페이지 수정 검증 근거](../../docs/migration-evidence/2026-10-31/)를 함께 확인한다. 이 근거는 사내 LLM·DB·Linux·Kubernetes·전체 native worker·브라우저 UI가 통과했다는 뜻이 아니다. 사내에서 다시 실행한다.

최종 UI 검증은 실제 서버와 사내 LLM·DB를 연결하여 다음 시나리오를 수행한다.

1. 새 세션에서 업무 조회를 실행하고 표·차트·원본 결과가 화면과 저장소에서 일치하는지 확인한다.
2. 158행 결과의 전체 페이지, offset 후속 조회, 1,000행 상한, 빈 결과, 불완전 원본, 큰 행, 토큰 예산 축소를 확인한다.
3. 후속 질문, 세션 재열기, SSE 또는 WebSocket 재연결, 취소, 시간 초과, 모델 실패를 확인한다.
4. 다른 사용자·세션의 결과 접근이 거부되는지 확인한다.
5. 일반 위임과 독립 검증의 권한 경계를 확인한다. 검증자는 저장된 근거 읽기만 수행해야 한다.
6. 기존 사내 업무의 기간·수율 정의·열화 파라미터·맵 범위를 실제 기준값과 비교한다.
7. 서버 재시작과 복구, 프로필·메모리·스킬 유지, 롤백 경로를 확인한다.

통과한 단계와 아직 수행하지 못한 단계를 구분한다. 단위 테스트만으로 전체 이식 완료를 선언하지 않는다. 과거 실제 업무 평가에는 범위 해석 오류와 예산 중단이 있었으므로 이번 페이지 수정으로 전체 업무 정답이 보장된다고 기록하지 않는다.

## 9. 운영 전환과 롤백을 기록한다

승인된 사내 절차로 새 실행 유입을 제어하고 진행 중 실행을 정리한 뒤 버전을 전환한다. 설정 변경과 서버 재시작을 함께 수행한다. 기존 session의 엔진이 자동 변경됐다고 가정하지 않는다.

롤백 대상은 현재 결과 스키마를 읽을 수 있는 호환 빌드여야 한다. MongoDB와 프로필을 삭제해서 이전 상태로 되돌리지 않는다. 변경한 환경 설정, 인증 라우터, 프런트 진입 경로, process 실행 설정과 직전 호환 커밋을 기록한다. 작업 종료 시 사내 AI는 변경 파일·패키지·설정, 실제 실행 결과, 미검증 항목, 운영 전환 조건을 보고한다.

## 사내 AI에게 전달할 시작 지시

아래 문구에 실제 참조 소스 경로와 사내 저장소 경로를 추가한다.

> 참조 소스의 `08-YieldAgent/harness/INTERNAL_MIGRATION_2026_10_31.md`와 `INTERNAL_PORTING_LUNA.md`를 읽고 사내 최신 main에 현재 하네스를 이식하라. 참조 소스의 실제 전체 SHA와 사내 시작 SHA를 먼저 기록하라. 사내 기존 서버·에이전트·인증·프런트 코드를 정본으로 보존하고 파일을 통째로 덮어쓰지 마라. 원본→사내 대응표, 의존성 및 환경 차이, 단계별 검증 계획을 작성한 뒤 구현하라. 외부 모델 기본값을 사내 승인 LLM으로 교체하고 실제 MongoDB와 업무 DB를 연결하라. 공유 ProcessRuntime worker와 웹 execute_code의 격리된 SessionKernel 실행을 구분하라. 새 Hermes UI의 `/api/ws`, gateway mount, 인증·ticket·origin과 연결되지 않은 handler를 확인하라. read_result의 페이지 정보와 공통 표시 행 수 계산을 관련 파일 전체에 함께 반영하라. 자연어 키워드·정규식·실패 문구 하드코딩으로 planner를 보정하지 마라. 각 단계의 실제 검증을 통과하면 다음 단계로 진행하되 부족한 권한·업무 정의·접속 정보는 구체적으로 보고하라. 실제 LLM·도구·DB·UI 사용자 시나리오를 실행하고 정상·실패·미검증을 구분하라. 사내 코드·자격증명·업무 데이터를 외부로 전송하지 마라. 완료 시 커밋, 변경 파일, 설정, 검증 근거, 남은 제약과 롤백 절차를 보고하라.
