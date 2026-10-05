#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
commit=e473f5a9c976a0b5bc292aa415dae28c638a47c3
source_dir="$root/.runtime/hermes/src"
mkdir -p "$root/.runtime/hermes"
if [[ ! -d "$source_dir" ]]; then
  git clone https://github.com/NousResearch/hermes-agent.git "$source_dir"
  git -C "$source_dir" checkout --detach "$commit"
fi
if [[ "$(git -C "$source_dir" rev-parse HEAD)" != "$commit" ]] || [[ -n "$(git -C "$source_dir" status --porcelain --untracked-files=no)" ]]; then
  echo 'Hermes checkout differs from the pinned source; preserve and inspect it before setup.' >&2
  exit 1
fi
if [[ ! -x "$root/.venv-hermes/bin/python" ]]; then
  uv venv --python 3.14 "$root/.venv-hermes"
fi
uv pip install --python "$root/.venv-hermes/bin/python" -e "$source_dir"
HERMES_HOME="$root/.runtime/hermes/setup-check" "$root/.venv-hermes/bin/python" -c 'from run_agent import AIAgent; print("Native Hermes import verified")'
