# Native Hermes engine integration

The user rejected another partial reimplementation. Replace the production conversation engine with the pinned upstream AIAgent rather than copying selected mechanisms. Keep the existing database tools, authorization, result/artifact store and HTTP/SSE endpoints as an adapter.

Pinned upstream: NousResearch/hermes-agent e473f5a9c976a0b5bc292aa415dae28c638a47c3.

- Separate Python 3.14 environment and pinned editable source checkout; existing Python 3.11 backend remains intact.
- One subprocess per active run; upstream registry receives only the backend's explicitly exposed tool schemas. RPC dispatch goes through the existing ToolExecutor, never raw DB credentials in generated code.
- Hermes owns reasoning, tool discovery/execution planning, context compaction, provider handling and loop guards. No custom LangGraph reasoning loop is used in the native path.
- Use upstream skills and isolated per-principal Hermes memory; copy trusted packaged domain skills into that profile. Native tools are selected explicitly; this is a configured Hermes engine, not every CLI/gateway integration enabled.
- Persist native conversation history with the existing session. Preserve the original request, cancellation, run ownership, artifacts and SSE delivery.
- Existing v2 runs remain on their original engine. New runs record the engine choice; missing installation is an explicit error, never silent fallback.
- Verify protocol boundaries and engine selection, then run the exact DB/LLM/binmap scenario at least three times. Do not mark task success from engine import or a returned final string alone.

## Implementation and verification

- `scripts/setup_hermes.sh` installs the pinned source in a separate Python 3.14 environment. Startup verifies the commit and refuses tracked upstream modifications. The upstream checkout is unchanged.
- `Settings.from_env()` defaults to `HARNESS_ENGINE=hermes`; direct `Settings()` construction retains the legacy engine for existing scripted tests. Each new controller run records its engine. Existing records without an engine stay on LangGraph.
- The native adapter registers read/artifact backend tools in Hermes. Hermes owns its deferred `tool_describe`/`tool_call` mechanism. The backend retains result storage and epoch checks. Generated Python analysis uses the existing container-backed `run_python`; unrestricted native terminal/code execution is not exposed.
- Profiles contain native skills and memory. A separate temporary working directory prevents repository development instructions from entering the domain session. `skip_context_files` must remain false: upstream uses that flag to gate skill auto-loading too.
- Legacy history and subsequent cancelled turns are converted to native history. Steering passes the accumulated original request and correction. Partial upstream outcomes remain partial.
- Verification command: `PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests/test_native_transport.py 08-YieldAgent/harness/tests/test_native_hermes.py 08-YieldAgent/harness/tests/test_recovery.py -q` → **23 passed, 27.40 seconds**.
- The transport tests run the actual installed upstream engine against a localhost synthetic model. They verify automatic skill content reaches the model request, deferred tool execution crosses the backend RPC, stored result IDs return to the model, usage is reported, cancellation terminates the native process, and a recovered run with an exhausted budget cannot launch another engine. These are not live domain/LLM quality tests.
- Automatic approval initially blocked external transmission; the user explicitly approved sending actual wafer data to the existing OpenRouter LLM. Three live evaluations have now completed with unchanged model/budget settings.
- Live acceptance: **0/3 passed**. Trial 1: 57.10s, 139,637 tokens, interrupted before binmaps. Trial 2: 51.17s, 79,805 tokens, nominally completed with a ten-wafer grid, but included future dates, arbitrarily narrowed degraded metrics to five, and multiplied unit-unspecified PT1C values by 100. Trial 3: 201.38s, 132,477 tokens, used two weeks and a six-parameter percentile score, obtained ten empty map results, then was interrupted.
- Exact durations, run IDs and findings: `outputs/hermes-native-live-evaluation-20261005.json`. Original responses, events, stored observations and produced PNG: `outputs/backend-hermes-native-20261005-{1,2,3}/`.
- This verifies the native engine can execute real tools, not that the requested workflow is correct. The configured token ceiling is checked after calls and was exceeded before interruption. Do not describe the ceiling as strict admission control.
- The current application server has not been replaced. Remaining work: correct period boundaries and result/metric context, preserve full requested selection scope, ensure incomplete outcomes are explained, then repeat live acceptance before activation. The task is not end-to-end complete.
