# NAS 작업 폴더로 이식하기

이미 Pod에 마운트된 SYLDAIX 아래 전용 폴더를 사용한다. NAS의 절대 경로는 배포 환경에서 정한다. 이 코드는 NAS를 마운트하거나 Pod 권한을 변경하지 않는다.

## 저장 위치 설정

인증 계층이 검증한 사번을 `Principal.user_id`에 넣는다. URL이나 모델이 전달한 사번을 인증 값으로 사용하지 않는다. 로컬 시험의 고정 사용자 `wafer1`은 사내 SSO 연동을 대체하지 않는다.

배포 담당자가 확인한 SYLDAIX 절대 경로를 `SYLDAIX_ROOT`로 지정한다. 그 아래 `yield-agent`와 `yield-agent/work`를 생성하고 실행 계정이 쓸 수 있는 전용 권한을 부여한다. 기존 NAS 전체의 권한을 바꾸지 않는다.

```sh
export HARNESS_ISOLATION_HOME="$SYLDAIX_ROOT/yield-agent"
export HARNESS_ISOLATION_WORK_ROOT="$SYLDAIX_ROOT/yield-agent/work"
export HARNESS_ISOLATION_SANDBOX=required
export HARNESS_RETAIN_PYTHON_FILES=1
```

설정 경로는 존재하는 절대 경로여야 한다. `work_root`는 전용 `home`의 직접 하위 폴더여야 한다. 저장소와 분석 venv는 이 전용 home 밖에 둔다. symlink 경로를 사용하지 않는다.

사용자별 공유 보관 영역은 `profiles/<사용자 해시>/results`다. 작업 영역은 `runs/<사용자와 요청의 복합 해시>`다. 작업 폴더는 사용자 profile 아래에 넣지 않는다. 현재 사용자의 profile을 허용해도 다른 요청의 작업 폴더까지 노출되지 않도록 분리한다. 해시는 식별자이며 암호화나 접근 권한을 대신하지 않는다.

## Mac에서 실행하기

`harness/isolation/seatbelt_local.py`를 저장소와 sandbox home 밖의 신뢰된 실행 경로에 복사한다. 파일과 상위 설치 폴더는 실행 계정 소유이며 다른 사용자에게 쓰기 권한이 없어야 한다. 기존 분석 venv를 사용한다.

```sh
export HARNESS_ISOLATION_BACKEND=seatbelt
export HARNESS_ISOLATION_LAUNCHER="$LOCAL_LAUNCHER_PATH"
export HARNESS_ISOLATION_ANALYSIS_VENV="$ANALYSIS_VENV_PATH"
```

launcher는 backend가 전달한 home, work root, venv를 검사한다. 사용자 results 디렉터리를 안전하게 열고 그 FD만 Seatbelt 자식에게 전달한다. 상위 폴더의 읽기 권한이나 네트워크 권한을 추가하지 않는다. 기존 하드코딩 launcher와 새 backend를 혼합해서 실행하지 않는다.

실제 Mac 회귀 검사는 [전체 테스트 준비 절차](../README.md#macos에서-전체-하네스-테스트-실행)의 분석 가상환경을 자동으로 사용한다. 다른 환경을 검사할 때는 `YIELD_TEST_ANALYSIS_VENV`로 지정한다. 아래 명령은 `08-YieldAgent`에서 실행한다.

```sh
python -m pytest harness/tests/test_local_seatbelt_files.py -q
```

## Linux Pod에 연결하기

Ubuntu에서는 `HARNESS_ISOLATION_BACKEND=local`로 NAS에 마운트한 전용 HOME/work 경로를 지정한다. 서비스 계정이 해당 경로를 소유해야 하며 분석 프로세스는 같은 호스트의 `bwrap`에서 실행된다. [기본 실행 및 확인](README.ko.md)을 따른다.

과거 SSH 배포를 명시적으로 유지하는 경우에만 앱의 `HARNESS_ISOLATION_HOME`과 `HARNESS_ISOLATION_WORK_ROOT`를 `/etc/yieldrun-isolation.json`의 `home`과 `work_root`에 맞춘다. launcher 설정, 분석 venv, SSH key와 known_hosts는 기존 설치 계약에 맞춰 준비한다. `setup_ubuntu.sh`는 기본 로컬 경로용 설치 스크립트이며 NAS 경로를 자동 인식하지 않는다. 기존 Pod에 그대로 실행하지 말고 배포 담당자가 설정을 맞춘다.

Linux launcher는 `bwrap`으로 전체 home을 가리고 현재 profile과 현재 작업 폴더만 bind한다. NAS 전체를 분석 프로세스에 별도로 bind하지 않는다. namespace 생성이나 sandbox 시작에 실패하면 실행을 거부한다. `privileged`나 전역 보안 해제로 우회하지 않는다.

사내에서 다음 항목을 직접 검증한다.

- `bwrap` 설치와 Pod의 user, mount, PID, network namespace 허용 여부.
- NAS의 UID/GID 매핑, ACL, root squash와 실제 서비스 계정의 작업 폴더 소유권.
- `chmod`, `O_EXCL`, `O_NOFOLLOW`, hard link, fsync 및 동시 요청 동작.
- 사용자 A와 B 사이의 파일 읽기·쓰기 거부와 같은 사용자의 다른 작업 폴더 접근 거부.
- 같은 요청 재시도, 실패 시 임시 파일 정리, 기존 CSV 재읽기.

Mac 검증은 Linux Pod와 실제 NAS의 검증을 대체하지 않는다.

## 파일 수명과 MongoDB

`HARNESS_RETAIN_PYTHON_FILES=1`이면 요청 폴더의 Python invocation 디렉터리에 코드, 입력 JSON, 출력 파일을 남긴다. 반환된 workspace 경로로 같은 허용 작업 범위에서 직접 읽을 수 있다. 기본값은 꺼짐이며 기존 임시 작업 정리를 유지한다. 보관을 켠 Python 파일에는 자동 보관기간 삭제가 없으므로 운영자가 용량과 보관정책을 정해야 한다. 진행 중인 요청은 정리하지 않는다.

CSV는 사용자 results 폴더에 저장한다. 기존 7일 CSV 정리 정책을 유지한다. 새 요청은 사용자와 요청을 함께 해시한 경로를 사용한다. 이전 경로의 파일은 자동 이동하거나 삭제하지 않는다.

파일 쓰기와 격리 backend 검사는 MongoDB 없이 동작한다. 현재 채팅 앱은 세션, 도구 표, 기록을 위해 기존 MongoDB를 계속 사용한다. 이번 변경은 앱 전체의 MongoDB 제거가 아니다.
