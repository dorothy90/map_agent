# Internal source migration — Python 3.11

The user cannot install Hermes in the company environment. Python 3.11 is required; approved ordinary Python dependencies may be added. The previous separate-install implementation does not meet this requirement.

## Design

Keep the pinned upstream conversation engine as project-owned source under `harness/engine/`, including its transitive internal modules and license. Do not replace it with selected look-alike rules. Preserve the existing backend tool adapter, storage, events and session isolation. Run the internal worker with the backend's Python interpreter, not a Hermes virtual environment. Separate processes isolate upstream absolute module names from the existing backend; this is not a second server.

Remove engine startup's package activation, self-update and automatic dependency installation. Dependencies are prepared explicitly with the company's normal Python package process. Runtime data lives separately from source. No startup Git checkout, runtime download or `.runtime/hermes/src` lookup is allowed.

## Execution and acceptance

1. Add a regression that copies the harness to a different folder, runs the worker using Python 3.11 and a local synthetic model, and verifies skills, tool discovery, backend RPC, history and cancellation without the old checkout or virtual environment. Run it red before changing execution paths.
2. Copy the pinned core source and its internal dependency closure; retain MIT notices and a provenance/patch manifest. Remove installation/update bootstrap, record every local source adaptation, and test import under the existing interpreter. Declare missing ordinary dependencies explicitly.
3. Change `native_hermes.py`/`native_worker.py` to internal paths and `sys.executable`, remove the external setup script requirement, and update installation/migration documentation. Keep old stored engine identities readable.
4. Exercise the actual copied engine through the synthetic transport tests. Check no runtime process invokes Git, pip, uv, self-update, or the old source/runtime paths. Run affected backend regressions and review the diff.
5. Repeat the approved live backend scenario on the internal engine. Report runtime independence and domain answer quality separately; the known 0/3 domain acceptance is not erased by source relocation. Do not claim full CLI/gateway product support from the backend engine port.

Success means the copied project runs its engine on Python 3.11 with explicit ordinary dependencies and no installed Hermes distribution. Existing company agents still need their tool adapters registered; they are not replaced by this migration.

## Validation record

The bundled runtime now executes with `sys.executable -I` on Python 3.11.14. Isolated mode prevents the backend's `types.py` from shadowing the standard library. Original file hashes and the four local source adaptations are recorded in `UPSTREAM.json` and `PATCHES.diff`. A separate reviewer verified the 2,121-file manifest, Python 3.11 syntax and actual AIAgent import. The adapter disables upstream host environment probes because it exposes no host terminal. Library versions are declared in both project dependency specifications and `uv.lock`; no Hermes distribution is installed.

The relocated synthetic test exercises the real bundled conversation engine, eager skill loading, discovery/description, backend RPC, result IDs, history, token reporting and process cancellation. A child audit hook rejects old external source/venv reads and subprocesses other than OS/interpreter metadata probes. No package installation/update subprocess is permitted. A profile test checks existing custom settings are preserved. Original-source hashes are tested against the recorded manifest.

The approved real question was rerun three times through the HTTP backend, OpenRouter and Oracle. Each used a new session, with a separate evaluation database. All three reached real tools through the bundled engine. Domain acceptance remains **0/3**:

| Run | Backend status | Tokens | Maps | Acceptance limitation |
|---|---|---:|---|---|
| 1 | partial | 121,371 | no | Token budget interrupted after queries/calculation; defect query returned an error. |
| 2 | completed | 75,019 | 10 | Window extends to October 12 despite October 6 execution; selects only ten degraded parameters. |
| 3 | completed | 78,784 | 10 | Changes reference to future dataset maximum November 30; restricts degraded parameters to three. |

The two `completed` statuses mean the engine returned text; they are not semantic acceptance. Test-only raw outputs and images remain local under `outputs/backend-hermes-embedded-20261006-{1,2,3}/`. No production rollout, commit or push is implied by this source-port validation.

Final relevant regression suite: **92 passed in 34.69 seconds** on Python 3.11.14. The import-only setup check passes with a temporary profile; `git diff --check` is clean for project adaptations; bundled upstream whitespace and unified-patch context are preserved verbatim. The temporary evaluation server was stopped after exporting results.
