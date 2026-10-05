"""Run the installed upstream engine against a local synthetic model server."""
import asyncio
import json
import threading
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from harness.config import Settings
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.types import Contract, ToolObservation
import harness.native_hermes as native


@pytest.mark.skipif(not (native.ROOT / '.venv-hermes/bin/python').exists(), reason='Native Hermes not installed')
@pytest.mark.parametrize("cancel", [False, True])
def test_actual_native_engine_roundtrips_backend_tool_without_external_model(monkeypatch, tmp_path, cancel):
    shutil.copytree(native.ROOT / '08-YieldAgent/harness/skills/yield-analysis', tmp_path / 'skills/yield-analysis')
    (tmp_path / 'config.yaml').write_text('skills:\n  auto_load:\n    - yield-analysis\n')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            observed = 'stored-synthetic' in json.dumps(request.get('messages', []))
            described = any(m.get('role') == 'tool' for m in request.get('messages', []))
            function = {'name': 'tool_call', 'arguments': json.dumps({'calls': [{'name': 'synthetic_read', 'arguments': {}}]})} if described else {'name': 'tool_describe', 'arguments': json.dumps({'names': ['synthetic_read']})}
            message = {'role': 'assistant', 'content': 'Synthetic result is 7.'} if observed else {
                'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'synthetic-call',
                    'type': 'function', 'function': function}]}
            payload = {'id': 'synthetic', 'object': 'chat.completion', 'created': int(time.time()),
                'model': 'synthetic', 'choices': [{'index': 0, 'message': message,
                    'finish_reason': 'stop' if observed else 'tool_calls'}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}}
            content_type = 'application/json'
            if request.get('stream'):
                delta = dict(message)
                if delta.get('tool_calls'):
                    delta['tool_calls'][0]['index'] = 0
                chunk = {**payload, 'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                finish = {**payload, 'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop' if observed else 'tool_calls'}]}
                data = ('data: ' + json.dumps(chunk) + '\n\ndata: ' + json.dumps(finish) + '\n\ndata: [DONE]\n\n').encode()
                content_type = 'text/event-stream'
            else:
                data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []
    entered = asyncio.Event()
    processes = []
    spawn = asyncio.create_subprocess_exec
    async def track_process(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", track_process)
    class Executor:
        def __init__(self, *args): pass
        async def execute(self, call):
            calls.append(call)
            entered.set()
            if cancel:
                await asyncio.sleep(30)
            return ToolObservation(principal_id='synthetic', session_id='s', run_id='r',
                invocation_id=call['id'], result_id='stored-synthetic', tool_name='synthetic_read',
                status='success', preview_rows=[{'value': 7}], total_rows=1)
    async def noop(*args, **kwargs): pass
    monkeypatch.setattr(native, 'ToolExecutor', Executor)
    monkeypatch.setattr(native, 'prepare_profile', lambda principal: tmp_path)
    registry = ToolRegistry()
    registry.add(ToolSpec('synthetic_read', 'Read the synthetic value', Contract, lambda *a: None))
    context = SimpleNamespace(run={'principal_id': 'synthetic', 'session_id': 's', 'run_id': 'r',
        'epoch': 1, 'query': 'Read the synthetic value'},
        settings=Settings(model='synthetic', base_url=f'http://127.0.0.1:{server.server_port}/v1',
            api_key='synthetic-not-a-secret', model_limit=6),
        check=noop, remaining_seconds=lambda: 45, store=SimpleNamespace(event=noop, runs=SimpleNamespace(update_one=noop)))
    try:
        async def scenario():
            task = asyncio.create_task(native.run_native(context, registry))
            if cancel:
                await asyncio.wait_for(entered.wait(), 30)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert processes[0].returncode is not None
                return None
            return await task
        result = asyncio.run(scenario())
        if cancel:
            return
        assert result['status'] == 'completed', result
        assert calls and calls[0]['name'] == 'synthetic_read', result['native_messages']
        assert result['result_ids'] == ['stored-synthetic']
        assert 'Synthetic result is 7.' in result['answer']
        assert any('stored-synthetic' in json.dumps(r) for r in requests)
        assert result['native_usage']['total_tokens'] > 0
        assert '합집합 수율' in json.dumps(next(r for r in requests if 'messages' in r), ensure_ascii=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
