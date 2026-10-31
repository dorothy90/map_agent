#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "root로 실행해야 합니다 (스크립트 내부에서 sudo를 호출하지 않습니다)." >&2
  exit 1
fi
if [ "$#" -ne 2 ]; then
  echo "사용법: $0 /absolute/repo/path /path/to/yieldrun.pub" >&2
  exit 2
fi
REPO=$1
PUBLIC_KEY=$2
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

test "${REPO#/}" != "$REPO"
test -f "$PUBLIC_KEY"
install -d -o root -g root -m 0755 /opt/yieldrun /usr/local/libexec
install -o root -g root -m 0755 "$HERE/launcher.py" /usr/local/libexec/yieldrun-launcher
install -o root -g root -m 0755 "$HERE/yieldrun-login-shell" /usr/local/libexec/yieldrun-login-shell
grep -qxF /usr/local/libexec/yieldrun-login-shell /etc/shells || printf '%s\n' /usr/local/libexec/yieldrun-login-shell >> /etc/shells
id yieldrun >/dev/null 2>&1 || adduser --system --home /var/lib/yieldrun --shell /usr/local/libexec/yieldrun-login-shell --group yieldrun
usermod --shell /usr/local/libexec/yieldrun-login-shell yieldrun
install -d -o root -g root -m 0755 /var/lib/yieldrun
install -d -o yieldrun -g yieldrun -m 0700 /var/lib/yieldrun/work
python3 -m venv /opt/yieldrun/analysis-venv
/opt/yieldrun/analysis-venv/bin/python -m pip install --disable-pip-version-check pandas numpy plotly
chown -R root:root /opt/yieldrun/analysis-venv
chmod -R go-w /opt/yieldrun/analysis-venv
ssh-keygen -l -f "$PUBLIC_KEY" >/dev/null
KEY=$(tr -d '\r\n' < "$PUBLIC_KEY")
printf '%s\n' "$KEY" > /etc/ssh/yieldrun_authorized_keys
chown root:root /etc/ssh/yieldrun_authorized_keys
chmod 0644 /etc/ssh/yieldrun_authorized_keys
install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf <<'EOF'
Match User yieldrun
    AuthorizedKeysFile /etc/ssh/yieldrun_authorized_keys
    ForceCommand /usr/local/libexec/yieldrun-launcher
    DisableForwarding yes
    PermitTTY no
    PermitUserEnvironment no
    X11Forwarding no
EOF
chown root:root /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf
chmod 0644 /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf
cat > /etc/yieldrun-isolation.json <<'EOF'
{"home":"/var/lib/yieldrun","work_root":"/var/lib/yieldrun/work","analysis_venv":"/opt/yieldrun/analysis-venv","launcher":"/usr/local/libexec/yieldrun-launcher","sandbox":"required"}
EOF
chown root:root /etc/yieldrun-isolation.json
chmod 0644 /etc/yieldrun-isolation.json
if command -v setfacl >/dev/null 2>&1; then
  setfacl -m u:yieldrun:--- "$REPO"
else
  echo "경고: setfacl이 없어 저장소 ACL을 적용하지 못했습니다. 권한을 수동으로 차단하십시오." >&2
  exit 1
fi
echo "설치 완료. sshd 설정과 known_hosts를 검증한 뒤 check_bypass.py를 실행하십시오."
