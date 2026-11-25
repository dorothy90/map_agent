# 수율 분석 실행 환경

Ubuntu에서 하네스는 Hermes의 `local` 터미널을 사용한다. 분석 요청마다 웹 서버가 현재 사용자와 실행에 해당하는 폴더를 골라 `bwrap`으로 분석 프로세스를 직접 실행한다. 이 경로에는 SSH 서버, 로그인 계정, 키, Docker, WSL이 필요하지 않다. macOS의 로컬 실행에는 Seatbelt를 사용한다.

## Ubuntu 로컬 실행

1. 일반 서비스 계정에서 서버의 Python 가상환경을 준비한다. 분석에 사용할 가상환경에는 pandas, numpy, plotly 및 그 의존성이 있어야 한다.
2. Ubuntu 서버에서 `/usr/bin/bwrap`과 사용자 네임스페이스를 사용할 수 있는지 확인한다. 설치만으로 실행 권한까지 확인되지는 않는다. 실제 검사는 현재 서비스 계정으로 수행한다. Windows 개발 PC에서는 이 준비가 필요하지 않다.
3. `HARNESS_ISOLATION_BACKEND=local`을 명시할 수 있다. 기존 배포 설정이 명시적으로 `ssh`를 지정했다면 `local`로 바꾸고 SSH 배포에서 쓰던 경로 설정은 검토한다. 기본 작업 위치는 서비스 계정의 `~/.local/share/yield-harness/work`이고, 분석 가상환경은 현재 서버의 Python 가상환경이다. 기본 로컬 실행기는 저장소의 `harness/isolation/launcher.py`이다.
4. 배포 전 서비스 계정에서 [직원별 파일 격리 검사](../../../docs/yield-harness-port/04-acceptance.md#ubuntu-배포-전-직원별-파일-격리)를 실행한다. 통과한 뒤 서버를 시작하고 루프백 주소에서 `/health/isolation`을 조회한다. 성공 응답에 `backend=local`, `sandbox=bwrap`, Python 워커 출력이 있는지 확인한다. 이어서 채팅에서 수율을 조회해 CSV와 표가 생성되는지 검증한다.

## 기존 SSH 배포에서 명시적으로 SSH를 선택하는 경우

아래 절은 과거 SSH 배포를 유지하는 경우에만 적용한다. 이 방식은 `HARNESS_ISOLATION_BACKEND=ssh`로 선택한다.

1. 운영자가 별도 SSH 키 쌍을 만들고 공개키 경로를 준비합니다. 개인키와 localhost host key를 harness 프로세스만 읽게 합니다. 실제 `.env`를 복사하지 마십시오.
2. macOS는 `setup_macos.sh /absolute/repo/path /path/to/yieldrun.pub`, Ubuntu는 `setup_ubuntu.sh /absolute/repo/path /path/to/yieldrun.pub`를 root shell에서 실행합니다. 스크립트는 내부에서 `sudo`를 호출하지 않습니다.
3. `/etc/ssh/sshd_config`가 `/etc/ssh/sshd_config.d/*.conf`를 포함하는지 확인하고 `sshd -t`로 root 소유 `90-yieldrun-isolation.conf`를 검증한 뒤 운영 절차로 sshd를 reload합니다. 설치 스크립트는 sshd를 시작·재시작하지 않습니다. HOME 밖 root 소유 키, `ForceCommand`, `PermitUserEnvironment no`, root 소유 전용 login shell을 함께 사용합니다.
4. harness에 다음 값을 설정합니다.

```text
HARNESS_ISOLATION_SSH_HOST=127.0.0.1
HARNESS_ISOLATION_SSH_USER=yieldrun
HARNESS_ISOLATION_SSH_PORT=22
HARNESS_ISOLATION_SSH_KEY=/관리자/전용/개인키
HARNESS_ISOLATION_KNOWN_HOSTS=/관리자/전용/known_hosts
HARNESS_ISOLATION_HOME=/var/lib/yieldrun
HARNESS_ISOLATION_WORK_ROOT=/var/lib/yieldrun/work
HARNESS_ISOLATION_ANALYSIS_VENV=/opt/yieldrun/analysis-venv
HARNESS_ISOLATION_LAUNCHER=/usr/local/libexec/yieldrun-launcher
HARNESS_ISOLATION_SANDBOX=required
```

분석 venv와 HOME 최상위는 root 소유이며 `work`만 `yieldrun`이 쓸 수 있습니다. launcher는 인증된 principal/run hash로 profile과 workdir를 매 요청마다 고정합니다. 직접 패키지는 `pandas`, `numpy`, `plotly`, `openpyxl`, `Pillow`, `python-docx`, `python-pptx`입니다. 전용 HOME/workdir에는 실제 저장소, `.env`, 모델/DB 자격증명을 두지 마십시오.

## 필수 sandbox

- macOS: root 소유 `/etc/yieldrun.sb`에 현재 profile/workdir만 파라미터로 전달합니다. sibling metadata/list/read/write를 허용하지 않으며 `sandbox-exec`가 없거나 정책 적용이 실패하면 실행을 거부합니다.
- Linux: `bwrap`가 HOME/work_root를 빈 tmpfs로 가린 뒤 현재 profile/workdir만 bind합니다. network/PID/mount namespace를 분리하며 `bwrap`가 없거나 시작에 실패하면 실행을 거부합니다.
- Ubuntu 24.04는 AppArmor가 unprivileged user namespace를 막을 수 있습니다. 먼저 실제 `bwrap` probe를 수행하십시오. 필요한 경우에만 `apparmor-bwrap-local`을 검토하여 `/etc/apparmor.d/`에 설치하고 `apparmor_parser -r`로 적용합니다. 전역 userns 제한을 해제하지 마십시오.

`none`은 허용되지 않고 `auto`도 샌드박스 없는 실행으로 fallback하지 않습니다. background terminal/process_manage와 persistent remote kernel은 거부되며 `execute_code`는 격리된 per-call 경로를 사용합니다.

## 기존 SSH 배포의 설치 후 점검

DB나 secret fixture 없이 synthetic A/B scope를 만들고 양방향 침범을 검사합니다.

```sh
python3 check_bypass.py --work-root /var/lib/yieldrun/work \
  --ssh-key /관리자/전용/개인키 --known-hosts /관리자/전용/known_hosts
```

각 scope의 자체 profile/workdir 쓰기는 성공해야 합니다. 반대 scope에 대한 list, stat, read, overwrite, delete, traversal, symlink, subprocess 접근은 모두 실패하고 원본 canary는 유지되어야 합니다. 하나라도 다르면 nonzero로 종료합니다.
