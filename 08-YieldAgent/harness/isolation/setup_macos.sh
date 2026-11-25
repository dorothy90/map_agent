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
UID_VALUE=${YIELDRUN_UID:-499}
GID_VALUE=${YIELDRUN_GID:-499}

test "${REPO#/}" != "$REPO"
test -f "$PUBLIC_KEY"
install -d -o root -g wheel -m 0755 /opt/yieldrun /usr/local/libexec
install -o root -g wheel -m 0755 "$HERE/launcher.py" /usr/local/libexec/yieldrun-launcher
install -o root -g wheel -m 0755 "$HERE/yieldrun-login-shell" /usr/local/libexec/yieldrun-login-shell
grep -qxF /usr/local/libexec/yieldrun-login-shell /etc/shells || printf '%s\n' /usr/local/libexec/yieldrun-login-shell >> /etc/shells
if ! dscl . -read /Groups/yieldrun >/dev/null 2>&1; then
  if dscl . -search /Groups PrimaryGroupID "$GID_VALUE" | grep -q .; then
    echo "YIELDRUN_GID가 이미 사용 중입니다." >&2
    exit 1
  fi
  dscl . -create /Groups/yieldrun
  dscl . -create /Groups/yieldrun PrimaryGroupID "$GID_VALUE"
fi
if ! id yieldrun >/dev/null 2>&1; then
  if dscl . -search /Users UniqueID "$UID_VALUE" | grep -q .; then
    echo "YIELDRUN_UID가 이미 사용 중입니다." >&2
    exit 1
  fi
  dscl . -create /Users/yieldrun
  dscl . -create /Users/yieldrun UniqueID "$UID_VALUE"
  dscl . -create /Users/yieldrun PrimaryGroupID "$GID_VALUE"
  dscl . -create /Users/yieldrun NFSHomeDirectory /var/lib/yieldrun
  dscl . -create /Users/yieldrun UserShell /usr/local/libexec/yieldrun-login-shell
  dscl . -create /Users/yieldrun IsHidden 1
  dscl . -passwd /Users/yieldrun '*'
fi
dscl . -create /Users/yieldrun PrimaryGroupID "$GID_VALUE"
dscl . -create /Users/yieldrun UserShell /usr/local/libexec/yieldrun-login-shell
install -d -o root -g wheel -m 0755 /var/lib/yieldrun
install -d -o yieldrun -g yieldrun -m 0700 /var/lib/yieldrun/work
/usr/bin/python3 -m venv /opt/yieldrun/analysis-venv
/opt/yieldrun/analysis-venv/bin/python -m pip install --disable-pip-version-check pandas numpy plotly openpyxl Pillow python-docx python-pptx
chown -R root:wheel /opt/yieldrun/analysis-venv
chmod -R go-w /opt/yieldrun/analysis-venv
ssh-keygen -l -f "$PUBLIC_KEY" >/dev/null
KEY=$(tr -d '\r\n' < "$PUBLIC_KEY")
printf '%s\n' "$KEY" > /etc/ssh/yieldrun_authorized_keys
chown root:wheel /etc/ssh/yieldrun_authorized_keys
chmod 0644 /etc/ssh/yieldrun_authorized_keys
install -d -o root -g wheel -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf <<'EOF'
Match User yieldrun
    AuthorizedKeysFile /etc/ssh/yieldrun_authorized_keys
    ForceCommand /usr/local/libexec/yieldrun-launcher
    DisableForwarding yes
    PermitTTY no
    PermitUserEnvironment no
    X11Forwarding no
EOF
chown root:wheel /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf
chmod 0644 /etc/ssh/sshd_config.d/90-yieldrun-isolation.conf
sed -e 's|@HOME@|/var/lib/yieldrun|g' -e 's|@WORK_ROOT@|/var/lib/yieldrun/work|g' \
  -e 's|@VENV@|/opt/yieldrun/analysis-venv|g' "$HERE/yieldrun.sb.in" > /etc/yieldrun.sb
chown root:wheel /etc/yieldrun.sb
chmod 0644 /etc/yieldrun.sb
cat > /etc/yieldrun-isolation.json <<'EOF'
{"home":"/var/lib/yieldrun","work_root":"/var/lib/yieldrun/work","analysis_venv":"/opt/yieldrun/analysis-venv","launcher":"/usr/local/libexec/yieldrun-launcher","sandbox":"required","sandbox_profile":"/etc/yieldrun.sb"}
EOF
chown root:wheel /etc/yieldrun-isolation.json
chmod 0644 /etc/yieldrun-isolation.json
chmod +a "yieldrun deny read,write,execute,delete,readattr,writeattr,readextattr,writeextattr,readsecurity,writesecurity,chown" "$REPO"
echo "설치 완료. sshd 설정과 known_hosts를 검증한 뒤 check_bypass.py를 실행하십시오."
