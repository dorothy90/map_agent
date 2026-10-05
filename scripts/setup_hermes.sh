#!/usr/bin/env bash
# Compatibility entry point: verify bundled source; never install or download.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
backend_python="${BACKEND_PYTHON:-$root/.venv/bin/python}"
"$backend_python" -I - "$root/08-YieldAgent/harness/engine" <<'PYTHON'
import os
import sys
import tempfile
sys.path.insert(0, sys.argv[1])
with tempfile.TemporaryDirectory(prefix="yield-engine-check-") as profile:
    os.environ["HERMES_HOME"] = profile
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    from run_agent import AIAgent
    print("Bundled engine import verified; no Hermes installation required")
PYTHON
