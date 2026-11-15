#!/usr/bin/env bash
# Loopback-only local gateway for this checkout.
# Loads the existing dotenv with override=False (never prints values; never
# commits .env). Bind to 127.0.0.1 only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
HOST="${HARNESS_GATEWAY_HOST:-127.0.0.1}"
PORT="${HARNESS_GATEWAY_PORT:-61302}"
cd "$ROOT"
exec python3 -m uvicorn harness.dev.local_gateway_app:app --host "$HOST" --port "$PORT"
