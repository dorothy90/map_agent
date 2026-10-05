#!/bin/bash
# yield-agent 백엔드(8001) + React 프론트엔드(5174) 기동
# 실행: bash /Users/daehwankim/yield-agent/08-YieldAgent/start_servers.sh
set -u
REPO=/Users/daehwankim/yield-agent/08-YieldAgent
PY=/opt/anaconda3/bin/python

# 이전 프로세스 정리
[ -f /tmp/yield_backend.pid ] && kill "$(cat /tmp/yield_backend.pid)" 2>/dev/null
[ -f /tmp/yield_frontend.pid ] && kill "$(cat /tmp/yield_frontend.pid)" 2>/dev/null
pkill -f 'uvicorn agent_server:app' 2>/dev/null
pkill -f 'yield_frontend/node_modules' 2>/dev/null
pkill -f 'streamlit run app.py' 2>/dev/null
sleep 2

# 1) 백엔드 :8001
cd "$REPO"
nohup $PY -m uvicorn agent_server:app --host 127.0.0.1 --port 8001 > /tmp/yield_server.log 2>&1 &
echo $! > /tmp/yield_backend.pid
echo "backend PID: $(cat /tmp/yield_backend.pid)"

sleep 4

# 2) 프론트엔드 (React + Vite) :5174
if ! command -v npm >/dev/null 2>&1; then
  echo 'ERROR: npm 을 찾을 수 없습니다. Node.js 설치 후 다시 실행하세요.'
  exit 1
fi
cd "$REPO/yield_frontend"
# --host 127.0.0.1: vite 기본값은 localhost(IPv6 ::1)라 127.0.0.1 curl이 안 닿음
nohup npm run dev -- --host 127.0.0.1 > /tmp/yield_frontend.log 2>&1 &
echo $! > /tmp/yield_frontend.pid
echo "frontend PID: $(cat /tmp/yield_frontend.pid)"

sleep 8
echo '--- health check ---'
curl -s http://127.0.0.1:8001/health | head -c 300; echo
curl -s -o /dev/null -w 'frontend HTTP %{http_code}\n' http://127.0.0.1:5174/
echo '--- done. open http://127.0.0.1:5174 in browser ---'
