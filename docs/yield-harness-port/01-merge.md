# 사내 최신 브랜치에 소스 옮기기

## 1. 대상 레포와 비교 작업 위치 정하기

대상 사내 Git 체크아웃 안에서 다음 명령을 실행한다. 아래 명령은 bash 기준이다. `WORK`는 사내 레포 옆에 만드는 새 디렉터리다. 첫 비교가 끝날 때까지 유지하고 다음 세션에서 쓸 수 있도록 경로를 기록한다. 아래 절은 같은 셸에서 순서대로 실행한다. 명령이 실패하면 그 단계에서 멈춘다.

```bash
export DEST="$(git rev-parse --show-toplevel)"
WORK=$(mktemp -d "$(dirname "$DEST")/yield-port.XXXXXXXX") || exit 1
export REF="$WORK/source"
export KIT="$REF/docs/yield-harness-port"
export AUDIT="$WORK/audit"
mkdir "$AUDIT"
git -C "$DEST" status --short --branch
git -C "$DEST" rev-parse HEAD > "$AUDIT/company-base.txt"
```

## 2. GitHub 브랜치 복제 및 확인

외부 GitHub 원격은 원본을 받을 때만 사용한다. 사내 레포의 원격 설정은 그대로 둔다. 내부 반입 미러를 쓰는 경우 아래 URL만 미러의 clone 주소로 바꾼다.

```bash
git clone -c core.autocrlf=false --single-branch --branch codex/goal-driven-harness \
  https://github.com/dorothy90/map_agent.git "$REF" &&
git -C "$REF" merge-base --is-ancestor b28862324779fc42086b89cfdc23c216f136fbc0 HEAD &&
test -f "$KIT/START_HERE.md" &&
git -C "$REF" rev-parse HEAD > "$AUDIT/source-head.txt" &&
python3 "$KIT/build_kit.py" verify-tree --root "$REF" --inventory "$KIT/FILES.tsv"
```

이미 해당 브랜치를 별도 폴더에 받았다면 `REF`를 그 폴더의 절대경로로, `KIT`를 `$REF/docs/yield-harness-port`로 지정하고 두 번째 명령인 `merge-base`부터 실행한다.

예상 결과는 `tree OK: 4848 listed files`다. 설명서와 업무 스킬은 현재 브랜치의 `docs/yield-harness-port/`에 있고, FILES.tsv는 앱 기준 커밋의 소스를 가리킨다. 브랜치가 이후 바뀌어 파일 검증이 실패하면 설명서 버전과 목록의 기준 커밋을 대조한 후 진행한다. 사내 코드·비교표·로그는 내부 작업 위치에만 둔다.

## 3. 파일별 대응표를 만든다

```bash
python3 "$KIT/port_plan.py" plan --manifest "$KIT/FILES.tsv" --target "$DEST" --sheet "$AUDIT/plan.tsv"
```

`FILES.tsv`는 외부 원본의 경로·목적지·분류·설명·해시·모드·바이트 수를 제공한다. `plan.tsv`는 **같은 상대경로**에 있는 사내 파일과 비교한 결과다. 이름이 다른 사내 파일은 자동으로 같은 역할이라고 판정할 수 없다. 아래 순서로 담당자가 대응시킨다.

| state | 내부 작업 |
|---|---|
| IDENTICAL | 내용과 실행 여부가 같다. action을 KEEP으로 두고 기존 파일 사용 |
| MISSING | 내부 전체에서 동일 역할을 먼저 검색. 필요한 새 파일이면 ADD, 타 경로라면 destination 열에 사내 루트 상대경로를 기록 |
| DIFFERENT | MERGE로 표시하고 양쪽을 비교. 내부 변경과 이번에 필요한 호출 계약을 함께 보존 |
| BLOCKED | 상위 경로의 파일 또는 심볼릭 링크 충돌. 실제 디렉터리 위치를 확인하고 목적지 재지정 |


`git -C "$DEST" grep -n -e 'register_yield_tools' -e 'domain_registry' -e 'build_gateway_router' -e 'HermesWebApp'`로 내부 진입점을 찾는다. 일치하는 이름이 없으면 Oracle 연결 함수, 사내 현재 사용자 반환 함수, ASGI 서버, 프론트 라우터를 대신 찾아 대응시킨다. 이름만 맞는 함수도 매개변수, 반환 형태, 예외, 사용자 범위, 동기/비동기 여부를 대조한다.

파일별 action이 모두 HOLD에서 시작한다. 수천 개 엔진 파일은 `08-YieldAgent/harness/engine/` 경로와 state로 TSV 필터를 걸어 묶어서 검토한다. 기존 내부 엔진이 다르면 새 참조 경로에 고정 버전을 나란히 놓고 검증한 뒤 호스트의 로딩 경로를 결정한다. 서로 다른 커밋의 엔진을 파일별로 섞으면 매니페스트 검증을 잃는다. 프론트 하위 경로를 옮길 때에는 진입점과 상대 import의 대응을 기록한다.

## 4. 파일을 반영한다

여기부터는 비교표 검토를 마친 뒤 진행한다. 사내 이식 브랜치에서 작업하고 이미 변경 중인 파일을 보존한다.

**Windows는 개발 PC, Ubuntu는 실행 서버다.** Ubuntu의 기본 분석 방식은 `local`이다. 하네스가 현재 서비스 계정으로 시스템의 `/usr/bin/bwrap`을 직접 실행해 사용자별 파일과 네트워크를 분리한다. SSH 서비스·전용 계정·키, Docker, WSL 설치는 필요 없다. 자세한 실행 조건은 [Ubuntu 로컬 실행](../../08-YieldAgent/harness/isolation/README.ko.md)을 따른다.

기존 사내 환경에서 `HARNESS_ISOLATION_BACKEND=ssh`를 명시했다면 `local`로 바꾼다. 이식 후 같은 Ubuntu 서비스 계정으로 루프백 `/health/isolation`을 확인하고 `backend=local` 및 `sandbox=bwrap`이 나오는지 본다. 다음으로 새 채팅에서 실제 DB 조회와 생성 Python 실행을 확인한다. 개발용 고정 사용자 게이트웨이는 로컬 검증에 사용하고, 사내 서비스에는 인증된 사용자 ID를 전달하는 기존 게이트웨이를 연결한다.

```bash
python3 "$KIT/port_plan.py" apply --manifest "$KIT/FILES.tsv" --source "$REF" --target "$DEST" --sheet "$AUDIT/plan.tsv"
git -C "$DEST" status --short
```

apply는 ADD로 선택한 파일만 SHA-256을 확인한 후 새 경로에 생성한다. 기존 파일의 내용 또는 실행 비트가 다르면 멈춘다. 이전 시도에서 이미 동일하게 생성된 파일은 건너뛴다. MERGE·KEEP·HOLD는 쓰기 대상에서 빠진다. 경로를 재배치했다면 import, 빌드 별칭, 게이트웨이 prefix, 자원 파일 상대경로도 함께 검토한다.

MERGE할 때는 예를 들어 `git diff --no-index -- "$DEST/08-YieldAgent/common.py" "$REF/08-YieldAgent/common.py"`로 내용을 비교한다. diff 종료값 1은 실제 차이가 있다는 뜻이다. Oracle 계정이나 모델 설정을 외부 값으로 교체하지 않는다. 사내 인증과 연결 풀을 살려서 도구가 기대하는 행 구조·날짜·반환형을 맞춘다. 오래된 `harness/skills/` 또는 이전 UI가 사내에 있으면 누가 읽는지 확인 후 라우팅을 정한다. 다른 에이전트가 쓰는 경로를 일괄 삭제하지 않는다.

## 5. 병합 순서와 확인 위치

| 순서 | 담당자가 먼저 읽을 파일 | 무엇을 맞추고 다음으로 넘어가는가 |
|---|---|---|
| 1. 실행 환경 | 루트 pyproject.toml, uv.lock, .python-version, requirements.txt, scripts/setup_hermes.sh | 프로젝트 범위와 잠금 파일을 짝지어 대응. 사내 루트에서 이미 쓰는 버전과의 차이는 MERGE에 기록 |
| 2. 엔진 | harness/engine/UPSTREAM.json, engine/LICENSE, hermes_chat.py | 원본 파일 해시와 Python 경로 확인. 작업 폴더에 있는 다른 Hermes 설치를 import하지 않는지 확인 |
| 3. 데이터·도구 | harness/tools/registry.py, native_tools.py, executor.py, tools/query_db.py, list_tables.py, python_tools.py, table_artifact.py, load_wafer_maps.py | 외부 계약의 도구명 6개와 JSON 입력, 소유권, 표·산출물 결과를 유지. 응답의 result_id가 가리키는 전체 자료에서 요약. 필수 공용 파일은 FILES.tsv 참조 |
| 4. 실행과 상태 | control.py, hermes_chat.py, hermes_chat_worker.py, store.py, runtime/, isolation/ | 새 채팅이 stock_chat 경로로 진입. 실제 사용자와 session/run에 결과 귀속, 모델 호출·취소·다중 턴 확인. 생성 Python은 격리 백엔드에서 실행 |
| 5. API·인증 | app.py, router.py, gateway/auth.py, routes.py, services.py, manifest.json, dev/local_gateway_app.py | 사내 호스트가 신뢰하는 사용자 ID와 WS 연결 정책을 주입. 개인 개발용 고정 사용자 게이트웨이는 격리된 단일 사용자 테스트에 사용. 권한이 다른 두 실제 테스트 계정으로 세션·산출물 분리 확인 |
| 6. UI | harness_console/src/main.tsx, src/web/entry.tsx, HermesWebApp.tsx, src/platform/, src/protocol/, src/hermes/, vite.config.ts | 운영 resolver, WebSocket 스트리밍, 세션 복구, 표·HTML 링크, 제품 선택, 스킬 목록. 서비스 라우트와 API 주소는 한 배포 대상으로 연결 |
| 7. 업무 프로필과 데이터 | 03-runtime-data.md 및 profile-seed/ | 수율일보 등 선택 스킬 적용, 필요시 개인 메모/대화 복원. 회사 데이터로 표·산점도·맵을 DB와 대조 |
| 8. 파일 검수 | 04-acceptance.md | ADD·KEEP·MERGE·HOLD 결정과 실제 변경 대조. 사내에서 이미 실행 가능한 경우에만 업무 동작도 확인 |

위 표에서 `harness/` 앞에는 `08-YieldAgent/`가 붙는다. 엔진의 유지보수 핀과 프론트의 이식 소스는 서로 별도다. 현 웹 콘솔 시작점은 `harness_console/src/main.tsx`다. 과거 계획 문서의 `yield_frontend/`·MCP·위임/검증 루프를 기본 채팅 시작점으로 삼으면 검증한 앱과 다른 경로가 된다. 사내에서 추가한 도구는 이식 전후 목록을 비교하고, 기존 실행 경로와 권한을 그대로 유지한 채 별도 작업으로 연결한다.

## 6. 복구 기준을 먼저 기록한다

단계마다 사내 기준 커밋과 변경 파일, 병합 근거를 내부에 기록한다. 선택한 파일만 검토 후 사내 브랜치에 커밋한다. 복구가 필요하면 해당 이식 커밋을 별도로 revert할 수 있도록 관련 없는 작업은 섞지 않는다.
