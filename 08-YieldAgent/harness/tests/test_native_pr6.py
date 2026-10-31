"""PR-6b pure tests: no Mongo, sockets, engine startup or model calls."""
import asyncio
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from harness import native_ipc as ipc
from harness.config import Settings


def worker_module():
    # Restore global import paths after loading the isolated-worker adapter.
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location('pr6_worker', Path(__file__).parents[1] / 'native_worker.py')
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


def test_server_request_envelope():
    request = ipc.call('server_request', 'call-one', request='clarify', params={})
    assert ipc.decode(ipc.encode(request)) == request
    for key, value in [('request', None), ('request', 2), ('params', None), ('params', [])]:
        assert not ipc.valid({**request, key: value})
        assert not ipc.valid({k: v for k, v in request.items() if k != key})
    assert not ipc.valid({**request, 'control_id': 'ctl-one'})
    assert ipc.valid(ipc.call('tool_call', 'call-two', name='read', args={}))


def test_prompt_flag_defaults_and_environment(monkeypatch):
    assert Settings().native_prompts is False
    monkeypatch.setenv('HARNESS_NATIVE_PROMPTS', 'true')
    assert Settings.from_env().native_prompts is True
    monkeypatch.setenv('HARNESS_NATIVE_PROMPTS', 'false')
    assert Settings.from_env().native_prompts is False


@pytest.mark.parametrize('ending', ['submitted', 'cancelled', 'disconnect', 'interrupt'])
def test_worker_callback_fake_stream_round_trip(ending):
    worker = worker_module()
    waiters = ipc.Waiters()
    controller = worker.WorkerControl(waiters)
    sent = []
    def rpc(method, **payload):
        identity, queue = waiters.register()
        sent.append(ipc.decode(ipc.encode(ipc.call(method, identity, **payload))))
        response = {'answers': {'q': 'private'}, 'outcome': ending}
        line = ipc.encode(ipc.reply(identity, response))
        if ending == 'disconnect': line = ''
        if ending == 'interrupt': line = ipc.encode(ipc.control('interrupt'))
        ipc.receive(io.StringIO(line), waiters, controller.handle)
        return queue.get_nowait()
    options = worker.prompt_options({'native_prompts': True}, rpc, lambda: None)
    reply = options['clarify_callback']([{'qid': 'q', 'question': 'Question', 'choices': None,
                                        'multi_select': False, 'choices_offered': []}])
    assert sent[0]['request'] == 'clarify'
    assert set(sent[0]['params']['questions'][0]) == {'qid', 'question', 'choices', 'multi_select'}
    assert reply == ({'answers': {'q': 'private'}, 'outcome': ending} if ending == 'submitted'
                     else {'answers': {}, 'outcome': 'cancelled'})


def test_worker_disabled_and_review_no_rpc():
    worker = worker_module()
    def forbidden(*a, **kw): raise AssertionError('RPC must not run')
    assert worker.prompt_options({}, forbidden, lambda: None) == {}
    options = worker.prompt_options({'native_prompts': True}, forbidden, lambda: 'review')
    assert options['clarify_callback']([]) == {'answers': {}, 'outcome': 'cancelled'}


def test_bridge_capabilities_and_default_no_approval():
    from harness.native_hermes import NativeBridge
    from harness.tools.registry import ToolRegistry, ToolSpec
    from pydantic import BaseModel
    class Args(BaseModel): pass
    registry = ToolRegistry()
    for name, effect in [('read', 'read'), ('synthetic', 'artifact'), ('write', 'persistent_write')]:
        registry.add(ToolSpec(name, 'Synthetic operation', Args, None, effect=effect))
    bridge = NativeBridge(registry, SimpleNamespace())
    assert bridge.approval_tools == frozenset()
    assert bridge.allowed == {'read', 'synthetic'}
    class Observation:
        def model_dump(self, **kw): return {}
        def model_view(self): return {'status': 'success'}
    async def execute(call): return Observation()
    bridge.executor = SimpleNamespace(execute=execute)
    async def scenario():
        assert await bridge.invoke({'name': 'read'}) == {'status': 'success'}
        bridge.approval_tools = frozenset({'write'})
        with pytest.raises(PermissionError): await bridge.invoke({'name': 'write'})
        with pytest.raises(PermissionError): await bridge.invoke({'name': 'synthetic'}, review_id='v')
    asyncio.run(scenario())


def test_disabled_server_request_and_verifier():
    from harness.native_hermes import handle_server_request
    async def scenario():
        for name, expected in [('clarify', {'answers': {}, 'outcome': 'cancelled'}),
                               ('approval', {'choice': 'deny'}), ('sudo', {'value': ''}),
                               ('secret', {'value': ''}), ('unknown', {'error': {'code': -32601, 'message': 'Method not found'}})]:
            assert await handle_server_request(None, name, {}) == expected
        assert await handle_server_request(object(), 'clarify', {}, review_id='v') == {
            'answers': {}, 'outcome': 'cancelled'}
        assert await handle_server_request(object(), 'approval', {}, review_id='v') == {'choice': 'deny'}
    asyncio.run(scenario())


def test_generation_timeout_and_history_projection():
    from harness.native_hermes import prompt_generation, prompt_history
    from harness.gateway.pending_wire import effective_timeout
    assert prompt_generation({'epoch': 4, 'goal_revision': 9}) == 4
    assert effective_timeout(300, 8) == 7
    assert effective_timeout(300, .5) == 0
    history = [{'role': 'assistant', 'tool_calls': [
        {'id': 'c', 'function': {'name': 'clarify', 'arguments': 'private question'}},
        {'id': 'r', 'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'c', 'content': 'private answer'},
        {'role': 'tool', 'tool_call_id': 'r', 'content': 'evidence'}]
    assert 'private' not in str(prompt_history(history))
    assert 'evidence' in str(prompt_history(history))
    assert 'private' in str(history)


def test_settlement_events_can_finish_while_cancelling_but_remain_epoch_fenced():
    from harness.store import HarnessStore
    calls = []
    class Runs:
        async def find_one_and_update(self, query, update, **kw): calls.append(query)
    async def scenario():
        store = SimpleNamespace(runs=Runs())
        for kind in ('clarify.settled', 'approval.settled', 'approval.cancelled', 'clarify.request', 'progress'):
            await HarnessStore.event(store, 'r', kind, {}, epoch=3)
        for query in calls[:3]:
            assert query == {'_id': 'r', 'status': {'$in': ['running', 'cancelling']}, 'active': True, 'epoch': 3}
        assert all(q['status'] == 'running' for q in calls[3:])
    asyncio.run(scenario())


class FakeWorker:
    """JSON-lines streams only; usable by the separate real-Mongo integration file."""
    def __init__(self, request='clarify', params=None):
        import json
        self.json = json
        self.returncode = 0
        self.messages = asyncio.Queue()
        self.startup = None
        self.replies = []
        self.stdout = SimpleNamespace(readline=self.readline)
        self.stdin = SimpleNamespace(write=self.write, drain=self.drain)
        self.messages.put_nowait(ipc.call('server_request', 'call-prompt', request=request,
            params=params if params is not None else {'questions': [{'qid': 'q', 'question': 'Synthetic question'}]}))

    async def readline(self):
        message = await self.messages.get()
        return ipc.encode(message).encode() if message is not None else b''

    def write(self, data):
        value = self.json.loads(data)
        if 'type' not in value:
            self.startup = value
        elif value['type'] == 'reply':
            self.replies.append(value)
            self.messages.put_nowait({'type': 'result', 'result': {'completed': False, 'partial': True,
                'turn_exit_reason': 'synthetic_end', 'final_response': '', 'messages': []}})
            self.messages.put_nowait(None)

    async def drain(self): pass

    async def spawn(self, *args, **kwargs): return self


def test_flag_false_host_startup_and_events_unchanged(monkeypatch, tmp_path):
    import harness.native_hermes as native
    from harness.tools.registry import ToolRegistry
    async def scenario():
        worker = FakeWorker()
        events, updates = [], []
        class Store:
            def __init__(self): self.runs = self
            async def update_one(self, query, update): updates.append(update)
            async def event(self, *args, **kwargs): events.append(args)
        async def check(): pass
        context = SimpleNamespace(store=Store(), settings=Settings(),
            run={'run_id': 'r', 'session_id': 's', 'principal_id': 'p', 'epoch': 1, 'query': 'q'},
            check=check, remaining_seconds=lambda: 10)
        monkeypatch.setattr(native, 'ROOT', tmp_path)
        monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', worker.spawn)
        def forbidden(*a, **kw): raise AssertionError('Pending storage must not be created')
        monkeypatch.setattr(native, 'PendingControls', forbidden)
        result = await native.run_native(context, ToolRegistry())
        assert result['stop_reason'] == 'synthetic_end'
        assert 'native_prompts' not in worker.startup
        assert worker.startup['tools'] == []
        assert worker.replies[0]['result'] == {'answers': {}, 'outcome': 'cancelled'}
        assert events == []
        assert 'pending_input' not in str(updates)
    asyncio.run(scenario())


def test_no_yield_mode_enables_native_prompt_transport():
    import harness.native_hermes as native

    assert native.native_prompts_enabled(Settings(no_yield_tools=False)) is False
    assert native.native_prompts_enabled(Settings(no_yield_tools=False, native_prompts=True)) is True
    assert native.native_prompts_enabled(SimpleNamespace(no_yield_tools=True, native_prompts=False)) is True


def test_worker_callback_budget_expiry_cancels():
    from queue import Empty
    worker = worker_module()
    def expired(*a, **kw): raise Empty()
    callback = worker.prompt_options({'native_prompts': True}, expired, lambda: None)['clarify_callback']
    assert callback([{'qid': 'q', 'question': 'q'}]) == {'answers': {}, 'outcome': 'cancelled'}
