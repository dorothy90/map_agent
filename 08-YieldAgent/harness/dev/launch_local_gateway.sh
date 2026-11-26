#!/usr/bin/env bash
# Local development gateway with LAN access for this checkout.
# Loads the existing dotenv with override=False (never prints values; never
# commits .env). Bind to all interfaces by default.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
HOST="${HARNESS_GATEWAY_HOST:-0.0.0.0}"
PORT="${HARNESS_GATEWAY_PORT:-61302}"
cd "$ROOT"
exec python3 -m uvicorn harness.dev.local_gateway_app:app --host "$HOST" --port "$PORT"
