#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec uv run --frozen python "$script_dir/enrich_wiki.py" \
  --apply \
  --allow-external-llm \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki \
  --source-index syld_gpt_2067627
