# yieldrun 격리 설치

`Settings.from_env()`는 항상 격리 설정을 요구합니다. `run_python`은 설정된 격리 backend에서 실행되며, 기본 SSH backend는 localhost의 전용 계정 `yieldrun`을 사용합니다. macOS에서는 `HARNESS_ISOLATION_BACKEND=seatbelt`와 `HARNESS_ISOLATION_SANDBOX=required`를 지정하고 HOME, WORK_ROOT, ANALYSIS_VENV, LAUNCHER 경로를 설정할 수 있습니다. SSH 또는 launcher 검증이 실패하면 도구가 거부되며 local 실행으로 돌아가지 않습니다.

## 설치

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

분석 venv와 HOME 최상위는 root 소유이며 `work`만 `yieldrun`이 쓸 수 있습니다. launcher는 인증된 principal/run hash로 profile과 workdir를 매 요청마다 고정합니다. 직접 패키지는 `pandas`, `numpy`, `plotly`뿐입니다. 전용 HOME/workdir에는 실제 저장소, `.env`, 모델/DB 자격증명을 두지 마십시오.

## 필수 sandbox

- macOS: root 소유 `/etc/yieldrun.sb`에 현재 profile/workdir만 파라미터로 전달합니다. sibling metadata/list/read/write를 허용하지 않으며 `sandbox-exec`가 없거나 정책 적용이 실패하면 실행을 거부합니다.
- Linux: `bwrap`가 HOME/work_root를 빈 tmpfs로 가린 뒤 현재 profile/workdir만 bind합니다. network/PID/mount namespace를 분리하며 `bwrap`가 없거나 시작에 실패하면 실행을 거부합니다.
- Ubuntu 24.04는 AppArmor가 unprivileged user namespace를 막을 수 있습니다. 먼저 실제 `bwrap` probe를 수행하십시오. 필요한 경우에만 `apparmor-bwrap-local`을 검토하여 `/etc/apparmor.d/`에 설치하고 `apparmor_parser -r`로 적용합니다. 전역 userns 제한을 해제하지 마십시오.

`none`은 허용되지 않고 `auto`도 샌드박스 없는 실행으로 fallback하지 않습니다. background terminal/process_manage와 persistent remote kernel은 거부되며 `execute_code`는 격리된 per-call 경로를 사용합니다.

## 설치 후 우회 점검

DB나 secret fixture 없이 synthetic A/B scope를 만들고 양방향 침범을 검사합니다.

```sh
python3 check_bypass.py --work-root /var/lib/yieldrun/work \
  --ssh-key /관리자/전용/개인키 --known-hosts /관리자/전용/known_hosts
```

각 scope의 자체 profile/workdir 쓰기는 성공해야 합니다. 반대 scope에 대한 list, stat, read, overwrite, delete, traversal, symlink, subprocess 접근은 모두 실패하고 원본 canary는 유지되어야 합니다. 하나라도 다르면 nonzero로 종료합니다.
