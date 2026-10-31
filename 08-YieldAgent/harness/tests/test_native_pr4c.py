import asyncio
import importlib.util
import json
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest


def worker_module():
    path = Path(__file__).parents[1] / 'native_worker.py'
    paths = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location('pr4c_worker', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = paths


def test_worker_interrupt_releases_rpc_and_new_agents():
    worker = worker_module()
    from harness.native_ipc import Waiters, CANCELLED
    waiters = Waiters()
    _, queue = waiters.register()
    calls = []
    controller = worker.WorkerControl(waiters)
    controller.add(SimpleNamespace(interrupt=lambda: calls.append('root')))
    controller.handle({'action': 'interrupt'})
    assert queue.get_nowait() == CANCELLED
    controller.add(SimpleNamespace(interrupt=lambda: calls.append('child')))
    assert calls == ['root', 'child']


def test_worker_controller_forgets_completed_child():
    worker = worker_module()
    controller = worker.WorkerControl(SimpleNamespace())
    parent = SimpleNamespace()
    child = SimpleNamespace()
    controller.add(parent)
    controller.add(child)
    controller.remove(child)
    assert controller.agents == [parent]


def test_worker_rejects_explicit_and_embedded_foreign_profiles():
    worker = worker_module()
    search = SimpleNamespace(_resolve_profile_db=lambda profile: 'foreign')
    worker.constrain_session_search(search)
    assert search._resolve_profile_db(None) is None
    assert search._resolve_profile_db('') is None
    with pytest.raises(ValueError, match='cross-profile'):
        search._resolve_profile_db('other-principal')


def test_delegation_registry_denial_does_not_replace_verifier_helpers(capsys):
    worker = worker_module()
    entry = SimpleNamespace(handler=lambda *a, **kw: 'old')
    other = SimpleNamespace(handler=lambda *a, **kw: 'evidence')
    registry = SimpleNamespace(get_tool_names_for_toolset=lambda name: ['delegate_task'],
        get_entry=lambda name: entry)
    executor = SimpleNamespace(_MAX_TOOL_WORKERS=8)
    class Agent: pass
    worker.configure_engine(registry, executor, 3, Agent)
    assert executor._MAX_TOOL_WORKERS == 3
    denied = json.loads(entry.handler({'goal': 'anything'}))
    assert denied['error'] == 'delegate_disabled' and denied['code'] == 'D09'
    assert json.loads(Agent()._dispatch_delegate_task({})) == denied
    assert other.handler({}) == 'evidence'
    assert 'D09' in capsys.readouterr().err


@pytest.mark.parametrize('cooperative', [True, False])
def test_stop_worker_order_and_deadline(monkeypatch, cooperative):
    import harness.native_hermes as native
    async def scenario():
        order = []
        stopped = asyncio.Event()
        process = SimpleNamespace(pid=123, returncode=None)
        async def wait():
            await stopped.wait()
            process.returncode = 0
        process.wait = wait
        async def send(message):
            order.append(message['action'])
            if cooperative: stopped.set()
        def killpg(pid, sig):
            order.append(sig)
            if sig == signal.SIGKILL: stopped.set()
        monkeypatch.setattr(native.os, 'killpg', killpg)
        async def invoke():
            try: await asyncio.Future()
            finally: order.append('released')
        pending = asyncio.create_task(invoke())
        await asyncio.sleep(0)
        await asyncio.wait_for(native.stop_worker(process, send, {pending}, grace=.02, terminate_grace=.02), .5)
        assert pending.done()
        assert order == (['released', 'interrupt'] if cooperative else
            ['released', 'interrupt', signal.SIGTERM, signal.SIGKILL])
    asyncio.run(scenario())


@pytest.mark.parametrize('publish', [False, True])
def test_commentary_is_gated_before_storage(monkeypatch, tmp_path, publish):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.tools.registry import ToolRegistry
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    events, sent = [], []
    async def event(*args, **kwargs): events.append(args)
    async def noop(*args, **kwargs): pass
    messages = iter([b'bad\n', b'{}\n',
        b'{"type":"commentary","content":"interim"}\n',
        b'{"type":"commentary","content":"repair"}\n',
        b'{"type":"result","result":{"completed":true,"final_response":"verified"}}\n'])
    async def readline(): return next(messages, b'')
    process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(readline=readline),
        stdin=SimpleNamespace(write=lambda data: sent.append(json.loads(data)), drain=noop))
    async def spawn(*a, **kw): return process
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    context = SimpleNamespace(settings=Settings(publish_commentary=publish, parallel_tool_limit=3),
        run=dict(principal_id='p', session_id='s', run_id='r', epoch=1, query='q'),
        remaining_seconds=lambda: 10, check=noop,
        store=SimpleNamespace(runs=SimpleNamespace(update_one=noop), event=event))
    result = asyncio.run(native.run_native(context, ToolRegistry()))
    assert result['status'] == 'partial'
    assert result['stop_reason'] == 'verification_unknown'
    assert 'verified' not in result['answer_text']
    assert [event[2]['content'] for event in events] == (['interim', 'repair'] if publish else [])
    assert sent[0]['parallel_tool_limit'] == 3


def test_real_stubborn_worker_receives_interrupt_before_term_and_kill(monkeypatch, tmp_path):
    import harness.native_hermes as native
    from harness import native_ipc as ipc
    marker = tmp_path / 'control.json'
    script = tmp_path / 'stubborn.py'
    script.write_text('''import json, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('ready', flush=True)
Path(sys.argv[1]).write_text(sys.stdin.readline())
while True: time.sleep(.01)
''')
    async def scenario():
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', str(script), str(marker),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, start_new_session=True)
        assert await process.stdout.readline() == b'ready\n'
        signals = []
        original = native.os.killpg
        def killpg(pid, sig):
            signals.append(sig)
            original(pid, sig)
        monkeypatch.setattr(native.os, 'killpg', killpg)
        async def send(message):
            process.stdin.write(ipc.encode(message).encode())
            await process.stdin.drain()
        try:
            await asyncio.wait_for(native.stop_worker(process, send, set(), grace=.1, terminate_grace=.1), 1)
            assert json.loads(marker.read_text())['action'] == 'interrupt'
            assert signals == [signal.SIGTERM, signal.SIGKILL]
            assert process.returncode == -signal.SIGKILL
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    asyncio.run(scenario())


@pytest.mark.parametrize('already_exited', [False, True])
def test_cancel_preserves_emitted_reservations_even_after_status_changes(monkeypatch, tmp_path, already_exited):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.tools.registry import ToolRegistry
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    updates = []
    async def scenario():
        stopped = asyncio.Event()
        lines = iter([b'{"type":"step","tokens":500,"models":1}\n',
            b'{"type":"step","tokens":1000,"models":2}\n'])
        async def readline():
            line = next(lines, None)
            if line is not None: return line
            if not already_exited:
                await stopped.wait()
            return b''
        async def wait():
            await stopped.wait()
            process.returncode = 0
        def write(data):
            if json.loads(data).get('type') == 'control': stopped.set()
        async def noop(*args, **kwargs): pass
        async def update(query, value): updates.append((query, value))
        async def check(): raise asyncio.CancelledError
        process = SimpleNamespace(returncode=0 if already_exited else None, wait=wait, stdout=SimpleNamespace(readline=readline),
            stdin=SimpleNamespace(write=write, drain=noop))
        async def spawn(*args, **kwargs): return process
        monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
        context = SimpleNamespace(settings=Settings(),
            run=dict(principal_id='p', session_id='s', run_id='r', epoch=1, query='q', usage={'tokens': 10, 'models': 1}),
            remaining_seconds=lambda: 10, check=check,
            store=SimpleNamespace(runs=SimpleNamespace(update_one=update)))
        with pytest.raises(asyncio.CancelledError): await native.run_native(context, ToolRegistry())
    asyncio.run(scenario())
    assert updates[-1] == ({'_id': 'r', 'epoch': 1}, {'$set': {'usage.tokens': 1010, 'usage.models': 3}})


def test_unconfirmed_model_calls_keep_entire_budget_on_rpc_cancellation():
    import httpx
    from harness.native_budget import NativeBudget
    from harness.native_ipc import Waiters, CANCELLED
    budget = NativeBudget(model_limit=3, token_limit=10000, max_output_tokens=100)
    request = httpx.Request('POST', 'https://fake.invalid/v1/chat/completions',
        json={'model': 'fake', 'messages': [{'role': 'user', 'content': 'q'}]})
    _, receipt = budget.admit(request, request.content)  # admission only; no send/network
    before = budget.snapshot()
    waiters = Waiters()
    _, queue = waiters.register()
    waiters.close(CANCELLED)
    receipt.configure(httpx.Response(200, headers={'Content-Type': 'text/event-stream'}))
    receipt.finish(complete=False)
    assert queue.get_nowait() == CANCELLED
    assert budget.snapshot() == before
    assert before['tokens'] == len(request.content) + 100 and before['models'] == 1
