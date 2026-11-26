# Windows 개발 PC에서 4SA 맵을 조회해 엑셀로 받기

이 절차는 Windows x64에서 단일 개발자의 브라우저와 백엔드를 같은 PC에 실행한다. MongoDB·Oracle·모델 공급자는 사내에서 접근 가능한 개발용 서비스를 사용한다. 분석용 Python은 Windows AppContainer에서 실행한다. Ubuntu 배포에는 기존 `local`과 `bwrap` 설정을 사용한다. [Ubuntu 배포 전 격리 검수](04-acceptance.md#ubuntu-배포-전-직원별-파일-격리)

## 1. 이전 이식본에 변경 사항 병합

[비교 절차](01-merge.md)에 따라 현재 참조 커밋의 FILES.tsv와 사내 브랜치를 비교한다. 이전에 반영한 프론트와 사내 인증·DB 연결은 보존한다. Windows 설정, 채팅 워커, 프로필 잠금, 세션별 CSV·Python 커널, 결과 파일 수집, 프로세스 실행기, PowerShell 설치 스크립트, 테스트와 의존성을 함께 병합한다. Windows 기능은 실행 시 운영체제를 감지한다. 이전 Ubuntu 예제에서 복사한 `HARNESS_ISOLATION_BACKEND=local`은 Windows 터미널에서 아래 값으로 재정의한다.

## 2. Python과 분석 환경 준비

PowerShell로 **사내 저장소 루트**를 연다. Windows용 64비트 CPython 3.11의 전체 설치 경로, uv, Node.js와 npm이 필요하다. 승인된 사내 패키지 미러가 있으면 uv와 pip가 해당 미러를 사용하도록 기존 사내 설정을 적용한다.

```powershell
$python = (py -3.11 -c "import sys; print(sys.executable)").Trim()
uv sync --locked --python $python
if ($LASTEXITCODE) { throw "서비스 패키지 설치 실패" }
& .\08-YieldAgent\harness\isolation\setup_windows.ps1 -Python $python
```

설치 스크립트는 전용 분석 가상환경을 기본적으로 `%LOCALAPPDATA%\YieldHarness\analysis-venv`에 만든다. 지정한 **Python 기본 설치 폴더와 전용 분석 가상환경**에 Windows 패키지 앱의 읽기·실행 권한을 부여한다. 전용 Python 설치를 사용하고, 두 디렉터리에 자격증명·사내 자료를 보관하지 않는다. 설치 경로에 대한 ACL 변경이 사내 정책상 제한되어 있으면 담당자에게 이 두 경로의 준비를 요청한다. 일반 사용자로 게이트웨이를 실행한다. 앱의 소스, .env, 개발자의 문서 폴더는 분석용으로 공유할 필요가 없다.

## 3. 로컬 실행기 검증과 서버 기동

첫 번째 PowerShell에서 실행한다. 기존 개발용 .env에는 모델 공급자 설정, `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`, `HARNESS_MONGO_URI`를 준비한다. 값과 테스트 로그는 사내에 둔다.

```powershell
$env:PYTHONPATH = (Resolve-Path .\08-YieldAgent).Path
$env:PYTHONUTF8 = '1'
$env:HARNESS_ISOLATION_BACKEND = 'windows'
$env:HARNESS_PRINCIPAL = 'windows-dev'
$env:HARNESS_MONGO_DB = 'yield_windows_dev'
$env:HARNESS_GATEWAY_PORT = '61402'
$env:HARNESS_WEB_PORT = '61413'
$service = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $service -m harness.evals.windows_isolation
if ($LASTEXITCODE) { throw "Windows 분석 실행기 검증 실패" }
& $service -m uvicorn harness.dev.local_gateway_app:app --host 0.0.0.0 --port 61402
```

첫 검사는 모델·DB 없이 제한 프로세스를 실제로 실행해 입력 CSV 읽기, 같은 채팅의 Python 변수 재사용, 픽셀과 좌표가 있는 한글 이름의 엑셀 생성·재개봉, 다른 채팅의 입력 접근 거부를 확인한다. 격리 확인에 실패하면 오류가 난 단계를 기록한다. 서버는 로컬 단일 프로세스로 실행한다.

두 번째 PowerShell에서 처음에 한 번 `npm ci`를 수행한 뒤 콘솔을 실행한다.

```powershell
Invoke-RestMethod http://127.0.0.1:61402/health/isolation
Set-Location .\08-YieldAgent\harness_console
npm ci
if ($LASTEXITCODE) { throw "프론트 패키지 설치 실패" }
$env:VITE_HARNESS_LOCAL_GATEWAY = '1'
$env:HARNESS_GATEWAY_TARGET = 'http://127.0.0.1:61402'
npm run dev -- --host 0.0.0.0 --port 61413 --strictPort
```

상태 응답에서 `backend=windows`와 `sandbox=appcontainer`를 확인한다. 브라우저에서 `http://127.0.0.1:61413`을 연다.

## 4. 같은 채팅에서 실제 자료 대조

1. 새 채팅에서 **“4SA 웨이퍼 맵 보여줘”**라고 보낸다. 선택이 필요하면 실제 DB에 있는 LOT·웨이퍼·검사를 고른다. 빠른 검증에는 웨이퍼 한 장이면 충분하다. 도구 기록에서 `load_wafer_maps`와 조회 조건을 확인하고 화면의 좌표·BIN을 사내 DB와 대조한다.
2. 같은 채팅에 **“그 맵을 엑셀에 넣어줘. 같은 시트나 별도 시트에 좌표와 BIN도 넣어줘.”**라고 보낸다. `execute_code` 호출 후 답변의 다운로드 버튼을 누른다. 내려받은 .xlsx를 실제로 열어 이미지가 들어 있는지, 같은 LOT·웨이퍼·검사인지, 좌표 행 수와 BIN 값이 DB와 일치하는지 확인한다.
3. 별도 새 채팅에서 첫 채팅의 다운로드 URL을 두 번째 채팅의 세션 ID로 요청해 거부되는지 확인한다. 분석 실행 중 취소도 시험한다. 취소 후 해당 분석 자식 프로세스가 종료되고 다음 질문에 답할 수 있어야 한다.
4. 사내 기록에 참조 커밋, Windows 버전, 실제 모델 ID, 채팅·실행 ID, DB/표/엑셀 비교 결과를 남긴다. Linux 배포 검수는 Ubuntu 서비스 계정에서 별도로 수행한다.

Windows 로컬에서 우선 지원하는 흐름은 수율 조회와 맵, 같은 채팅에서의 Python 분석·파일 반환이다. Unix 전용 개인·공용 파일 도구나 로컬 MCP·에이전트 위임·예약 등은 이 검수의 범위 밖이다. 여기에 필요한 사내 연결이나 권한을 확인하지 못했다면 해당 항목을 미검증으로 기록한다.
