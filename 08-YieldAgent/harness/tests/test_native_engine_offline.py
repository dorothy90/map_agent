"""Real pinned engine and worker over pipes; HTTP responses stay in memory."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest


MODEL_STUB = r'''
import json, runpy, sys
import httpx

def response(client, request, **kwargs):
    payload = json.loads(request.content)
    messages = payload.get('messages', [])
    text = json.dumps(messages, ensure_ascii=False)
    verifier = '독립적인 결과 검증 에이전트' in text
    tools = [m for m in messages if m.get('role') == 'tool']
    target = 'read_result' if verifier else 'synthetic_read'
    result_id = 'review-read' if verifier else 'stored-synthetic'
    observed = any(result_id in str(m.get('content')) for m in tools)
    described = any(target in str(m.get('content')) for m in tools)
    if not verifier and MODE == 'delegate' and 'delegate_disabled' not in text:
        function = {'name': 'delegate_task', 'arguments': json.dumps({'goal': 'forbidden-child'})}
    elif observed:
        content = json.dumps({'verdict': 'pass', 'issues': [], 'result_ids': ['review-read']}) if verifier else 'Synthetic verified answer'
        function = None
    elif described:
        function = {'name': 'tool_call', 'arguments': json.dumps({'calls': [{'name': target,
            'arguments': {}}]})}
    else:
        function = {'name': 'tool_describe', 'arguments': json.dumps({'names': [target]})}
    message = {'role': 'assistant', 'content': content} if function is None else {
        'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fake-' + str(len(messages)),
            'type': 'function', 'function': function}]}
    data = {'id': 'fake', 'object': 'chat.completion', 'created': 0, 'model': 'synthetic',
        'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop' if function is None else 'tool_calls'}],
        'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}}
    if payload.get('stream'):
        delta = dict(message)
        if delta.get('tool_calls'): delta['tool_calls'][0]['index'] = 0
        chunk = {**data, 'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
        finish = {**data, 'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop' if function is None else 'tool_calls'}]}
        wire = ('data: ' + json.dumps(chunk) + '\n\ndata: ' + json.dumps(finish) + '\n\ndata: [DONE]\n\n').encode()
        return httpx.Response(200, stream=httpx.ByteStream(wire), headers={'Content-Type': 'text/event-stream'}, request=request)
    return httpx.Response(200, json=data, request=request)
httpx.Client.send = response
async def forbidden(*args, **kwargs): raise AssertionError('Unexpected async HTTP')
httpx.AsyncClient.send = forbidden
runpy.run_path(sys.argv[1], run_name='__main__')
'''


@pytest.mark.parametrize('mode', ['read', 'delegate', 'cancel'])
def test_real_worker_verifier_and_denial_without_network(monkeypatch, tmp_path, mode):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.tools.registry import ToolRegistry, ToolSpec
    from harness.types import Contract, ToolObservation
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    wrapper = tmp_path / 'worker_probe.py'
    wrapper.write_text('MODE = ' + repr(mode) + '\n' + MODEL_STUB)
    calls, processes, events, updates = [], [], [], []
    spawn = asyncio.create_subprocess_exec
    async def wrapped_spawn(python, isolated, worker, **kwargs):
        process = await spawn(python, isolated, str(wrapper), worker, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', wrapped_spawn)
    entered = asyncio.Event()
    class Executor:
        def __init__(self, *args): pass
        async def publish(self, *args): pass
        async def execute(self, call):
            calls.append(call)
            entered.set()
            if mode == 'cancel': await asyncio.Future()
            return ToolObservation(principal_id='p', session_id='s', run_id='r',
                invocation_id=call['id'], result_id='review-read' if call['name'] == 'read_result' else 'stored-synthetic',
                tool_name=call['name'], status='success', preview_rows=[{'value': 7}], total_rows=1)
    monkeypatch.setattr(native, 'ToolExecutor', Executor)
    async def noop(*args, **kwargs): pass
    async def event(*args, **kwargs): events.append(args)
    async def update(query, value): updates.append((query, value))
    async def put_result(principal, session, obs, rows, artifacts):
        return ToolObservation(principal_id=principal, session_id=session, result_id='review-result', **obs)
    settings = Settings(engine='hermes', model='synthetic',
        base_url='http://127.0.0.1:1/v1', api_key='synthetic', model_limit=12)
    store = SimpleNamespace(event=event, put_result=put_result, runs=SimpleNamespace(update_one=update))
    context = ExecutionContext(settings=settings,
        run=dict(principal_id='p', session_id='s', run_id='r', epoch=1,
            query='Remember phrase PINEAPPLE-7XQ9-ALPHA'),
        store=store)
    context.check = noop
    context.remaining_seconds = lambda: 20
    registry = ToolRegistry()
    for name in ('synthetic_read', 'read_result'):
        registry.add(ToolSpec(name, name, Contract, lambda *args: None))
    async def scenario():
        task = asyncio.create_task(native.run_native(context, registry))
        if mode == 'cancel':
            await asyncio.wait_for(entered.wait(), 20)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 12)
        else:
            result = await asyncio.wait_for(task, 25)
            assert result['status'] == 'completed', result
            assert result['native_engine']['verification'][-1]['result_ids'] == ['review-read']
            assert result['native_usage']['total_tokens'] == result['native_usage']['api_calls'] * 40
            assert [c['name'] for c in calls] == ['synthetic_read', 'read_result']
            history_tool_ids = {call.get('id') or call.get('tool_call_id')
                for message in result['native_messages'] if message.get('role') == 'assistant'
                for call in message.get('tool_calls') or []}
            assert calls[0]['id'] in history_tool_ids
            if mode == 'delegate':
                assert 'delegate_disabled' in json.dumps(result['native_messages'])
        assert processes[0].returncode is not None
        assert not any(e[1] == 'commentary' for e in events)
        assert all(call['id'] for call in calls)
        return result if mode != 'cancel' else None
    first_result = asyncio.run(scenario())
    if mode == 'read':
        profile = tmp_path / '.runtime/hermes/profiles' / hashlib.sha256(b'p').hexdigest()
        state_db = profile / 'state.db'
        assert state_db.exists()
        with sqlite3.connect(state_db) as connection:
            session = connection.execute(
                'SELECT source, ended_at, end_reason, title FROM sessions WHERE id = ?', ('s',)).fetchone()
            messages = connection.execute(
                'SELECT role, content FROM messages WHERE session_id = ? ORDER BY id', ('s',)).fetchall()
            children = connection.execute(
                'SELECT source, parent_session_id FROM sessions WHERE parent_session_id = ?', ('s',)).fetchall()
        assert session is not None
        assert session[0] == 'api_server'
        assert session[1] is not None and session[2] == 'agent_close'
        assert {'user', 'assistant', 'tool'} <= {role for role, _ in messages}
        assert any('PINEAPPLE-7XQ9-ALPHA' in (content or '') for role, content in messages if role == 'user')
        assert ('subagent', 's') in children

        second = ExecutionContext(settings=settings,
            run=dict(principal_id='p', session_id='s', run_id='r2', epoch=1, query='Append exactly once'),
            store=store)
        second.check = noop
        second.remaining_seconds = lambda: 20
        second_result = asyncio.run(asyncio.wait_for(
            native.run_native(second, registry, history=first_result['native_messages']), 25))
        assert second_result['status'] == 'completed'
        with sqlite3.connect(state_db) as connection:
            resumed = connection.execute(
                'SELECT ended_at, end_reason, title FROM sessions WHERE id = ?', ('s',)).fetchone()
            appended = connection.execute(
                "SELECT count(*) FROM messages WHERE session_id = ? AND role = 'user' AND content LIKE ?",
                ('s', '%Append exactly once%')).fetchone()[0]
        assert resumed[0] is not None and resumed[1] == 'agent_close'
        assert resumed[2] == session[3]
        assert appended == 1

        other = ExecutionContext(settings=settings,
            run=dict(principal_id='q', session_id='other', run_id='r3', epoch=1, query='Unrelated principal turn'),
            store=store)
        other.check = noop
        other.remaining_seconds = lambda: 20
        other_result = asyncio.run(asyncio.wait_for(native.run_native(other, registry, history=[]), 25))
        assert other_result['status'] == 'completed'

        search_script = r'''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('native_search_probe', sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
from tools import session_search_tool
worker.constrain_session_search(session_search_tool)
kwargs = json.loads(sys.argv[2])
print(session_search_tool.session_search(**kwargs))
'''
        worker = Path(__file__).resolve().parents[1] / 'native_worker.py'
        def search(home, **kwargs):
            completed = subprocess.run([sys.executable, '-I', '-c', search_script, str(worker), json.dumps(kwargs)],
                cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(home)}, capture_output=True,
                text=True, timeout=20, check=True)
            return json.loads(completed.stdout.splitlines()[-1])
        same = search(profile, query='PINEAPPLE-7XQ9-ALPHA', current_session_id='different-session')
        assert any('PINEAPPLE-7XQ9-ALPHA' in json.dumps(item) for item in same.get('results', []))
        other_profile = tmp_path / '.runtime/hermes/profiles' / hashlib.sha256(b'q').hexdigest()
        isolated = search(other_profile, query='PINEAPPLE-7XQ9-ALPHA', current_session_id='different-session')
        assert not isolated.get('results')
        hidden = search(profile, query='독립적인 결과 검증 에이전트', current_session_id='different-session')
        assert not hidden.get('results')
        for kwargs in ({'profile': 'foreign', 'session_id': 's'}, {'session_id': 'foreign/s'}):
            rejected = search(profile, **kwargs)
            assert rejected['success'] is False
            assert 'cross-profile' in rejected['error']

    logs = list((tmp_path / '.runtime/hermes/logs').glob('native-worker-*.log'))
    diagnostics = ''.join(path.read_text(errors='replace') for path in logs)
    assert '#94736' not in diagnostics
    assert 'token drain' not in diagnostics.lower()
