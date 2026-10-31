"""Exercise upstream batch scheduling unchanged in a dedicated interpreter."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('limit', [8, 3])
def test_original_nine_tool_batch_respects_cap_and_barriers(tmp_path, limit):
    worker = Path(__file__).parents[1] / 'native_worker.py'
    script = r'''
import ast, json, runpy, sys, threading, time
from pathlib import Path
from types import SimpleNamespace as NS
worker = runpy.run_path(sys.argv[1])
from agent import tool_executor as executor
from agent.tool_dispatch_helpers import _batch_admission, _plan_tool_batch_segments, _PARALLEL_SAFE_TOOLS
from tools.registry import registry
from tools import delegate_tool
class Agent: pass
worker['configure_engine'](registry, executor, int(sys.argv[2]), Agent)
assert registry.get_tool_names_for_toolset('delegation') == ['delegate_task']
assert executor._MAX_TOOL_WORKERS == int(sys.argv[2])

def call(name): return NS(function=NS(name=name, arguments='{}'))
yield_names = set()
for source in Path(sys.argv[1]).with_name('tools').glob('*.py'):
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'ToolSpec':
            yield_names.add(ast.literal_eval(node.args[0]))
assert len(yield_names) == 37  # query_db 포함 기존 22 + 15개 고유 wafer_set 추가 도구
for name in (*yield_names, 'clarify', 'manage_connections', 'unknown_tool'):
    assert _batch_admission(call(name), None) is None, name
    bridge = NS(function=NS(name='tool_call', arguments=json.dumps({'calls': [{'name': name, 'arguments': {}}]})))
    assert _batch_admission(bridge, None) is None, name
    batch = [call('web_search'), call('web_search'), call(name), call('web_search'), call('web_search')]
    assert [(kind, len(items)) for kind, items in _plan_tool_batch_segments(batch)] == [
        ('parallel', 2), ('sequential', 1), ('parallel', 2)]
assert 'yield_backend' not in _PARALLEL_SAFE_TOOLS and 'yield_evidence' not in _PARALLEL_SAFE_TOOLS
assert _plan_tool_batch_segments([call('web_search') for _ in range(9)])[0][0] == 'parallel'
lock = threading.Lock()
state = {'active': 0, 'maximum': 0, 'completed': 0}
ready = threading.Event()
limit = int(sys.argv[2])
class Batch(executor._ConcurrentBatch):
    def run_worker(self, index, order):
        with lock:
            state['active'] += 1
            state['maximum'] = max(state['maximum'], state['active'])
            if state['active'] == limit: ready.set()
        assert ready.wait(2)
        time.sleep(.02)
        with lock:
            state['active'] -= 1
            state['completed'] += 1
batch = Batch(NS(), [], 'synthetic', [NS(name='web_search', parse_error=None) for _ in range(9)], 3)
batch.run()  # Original run, submit_all, await_completion, daemon pool and cap.
assert state['maximum'] == limit, state
assert state['completed'] == 9, state
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(worker), str(limit)],
        cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
