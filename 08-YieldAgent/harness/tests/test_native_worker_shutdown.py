"""Real worker/IPC shutdown over pipes, with engine dependencies stubbed out."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest


STUB_ENGINE = r'''
import runpy, sys, threading, time
from types import SimpleNamespace as NS

mode = sys.argv[2]
interrupted = threading.Event()
# Buffer diagnostics deliberately so the test also checks the final flush.
sys.stderr = open(sys.stderr.fileno(), 'w', buffering=8192, closefd=False)
class DB:
    def get_session(self, session_id): return None
    def reopen_session(self, session_id): raise AssertionError('new session must not be reopened')
db = DB()
class Agent:
    def __init__(self, **kwargs):
        assert kwargs['session_db'] is db
        assert kwargs['session_id'] == 'shutdown-test'
        assert kwargs['platform'] == 'api_server'
    def run_conversation(self, *args, **kwargs):
        print('ready', file=sys.__stdout__, flush=True)
        if mode == 'interrupt':
            assert interrupted.wait(5), 'interrupt was not delivered'
        else:
            time.sleep(.1)  # Allow the real receiver to block on the open pipe.
        return dict(final_response='done', messages=[], completed=not interrupted.is_set())
    def interrupt(self): interrupted.set()
    def close(self):
        sys.__stdout__.write('stdout-cleanup\n')
        sys.stderr.write('stderr-cleanup\n')
    def _persist_session(self, messages, history): pass
class Gate:
    def __init__(self, *args): pass
    def install(self, agent): return lambda: None
    def finish(self, result): return result
sys.modules.update({
    'native_budget': NS(install_http_budget=lambda **kw: NS(exhausted_reason=None,
        snapshot=lambda: dict(input_tokens=0, output_tokens=0, tokens=0, models=0))),
    'native_stop_gate': NS(NativeVerificationGate=Gate),
    'hermes_constants': NS(),
    'tools': NS(skills_tool=NS(skill_view=lambda *args: None), delegate_tool=NS(), session_search_tool=NS()),
    'tools.registry': NS(registry=NS(get_tool_names_for_toolset=lambda name: [])),
    'run_agent': NS(AIAgent=Agent),
    'agent': NS(tool_executor=NS(_MAX_TOOL_WORKERS=8)),
    'gateway.session_context': NS(declare_stateless_channel=lambda: None),
    'tools.delegate_tool': NS(_build_child_preserving_parent_tools=None, _run_single_child=None),
    'tools.delegation_output_schema': NS(extract_json_candidate=None),
    'hermes_state_registry': NS(acquire=lambda path: db,
        release_or_close=lambda value: sys.__stdout__.write('db-release\n')),
})
runpy.run_path(sys.argv[1], run_name='__main__')
'''


@pytest.mark.parametrize('mode', ['normal', 'eof', 'interrupt'])
def test_worker_exits_cleanly_and_flushes_output(tmp_path, mode):
    wrapper = tmp_path / 'worker_stub.py'
    wrapper.write_text(STUB_ENGINE)
    worker = Path(__file__).resolve().parents[1] / 'native_worker.py'
    startup = dict(run_budget_seconds=10, max_iterations=1, token_limit=100,
        max_tokens=10, base_url='https://unused.invalid', call_timeout=1,
        tools=[], model='synthetic', api_key='synthetic',
        session_id='shutdown-test', temperature=0, query='q', instructions='', history=[])

    async def scenario():
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', str(wrapper),
            str(worker), mode, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=tmp_path,
            env={**os.environ, 'HERMES_HOME': str(tmp_path)})
        try:
            process.stdin.write((json.dumps(startup) + '\n').encode())
            await process.stdin.drain()
            assert await asyncio.wait_for(process.stdout.readline(), 5) == b'ready\n'
            if mode == 'eof':
                process.stdin.close()
            elif mode == 'interrupt':
                process.stdin.write(b'{"type":"control","control_id":"ctl-test","action":"interrupt"}\n')
                await process.stdin.drain()
            # Keep stdin open until exit in normal/interrupt modes: communicate()
            # would close it early and hide the buffered-reader shutdown race.
            await asyncio.wait_for(process.wait(), 10)
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            assert b'Fatal' not in stderr
            assert b'stderr-cleanup\n' in stderr
            lines = stdout.splitlines()
            assert lines[:2] == [b'stdout-cleanup', b'db-release']
            result = json.loads(lines[-1])
            assert result['type'] == 'result'
            assert result['result']['completed'] == (mode != 'interrupt')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not process.stdin.is_closing():
                process.stdin.close()

    asyncio.run(scenario())
