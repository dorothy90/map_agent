import asyncio
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace
from uuid import uuid4

from harness import hermes_chat, native_hermes
from harness.config import Settings
from harness.control import RunController
from harness.gateway.pending_controls import PendingControls
from harness.gateway.session_rpc import SessionRPC
from harness.gateway.session_store import MongoSessionStore
from harness.store import HarnessStore
from harness.tools.registry import domain_registry


def test_stock_chat_reads_native_tool_in_authenticated_session(monkeypatch, tmp_path):
    """Drive the real stock worker, its native tool and a real isolated Mongo DB."""
    principal, session = 'chat-test', str(uuid4())
    seen_tools = []
    title_requests = []
    generated_title = 'Read stored result'
    second_prompt = 'same session follow-up'
    answer = 'stored value 7921'
    answered_history = []
    local_media = 'MEDIA:/private/tmp/local.png'
    source_id = None
    entered, release = threading.Event(), threading.Event()

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'messages' not in request:
                self.send_error(404)
                return
            messages = request['messages']
            if messages[-1].get('content') == second_prompt:
                answered_history.append(messages)
            if messages[-1].get('content') == 'cancel this turn':
                chunk = {'id': 'cancel-test', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': 'test-model', 'choices': [{'index': 0, 'finish_reason': None,
                         'delta': {'role': 'assistant', 'content': 'stream before cancel\n'}}]}
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.flush()
                release.wait(35)
                return
            if request.get('tools'):
                seen_tools.append({tool['function']['name']: tool for tool in request['tools']})
            if request.get('response_format', {}).get('json_schema', {}).get('name') == 'session_title':
                title_requests.append(messages[-1]['content'])
                message = {'role': 'assistant', 'content': json.dumps({'title': generated_title})}
                reason = 'stop'
            elif any(m.get('role') == 'tool' and '7921' in str(m['content']) for m in messages):
                message = {'role': 'assistant', 'content': local_media + '\n\n' + answer}
                reason = 'stop'
            elif not any(m.get('role') == 'tool' and m.get('tool_call_id') == 'scope-call' for m in messages):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'index': 0, 'id': 'scope-call', 'type': 'function', 'function': {
                        'name': 'clarify', 'arguments': json.dumps({'questions': [{'question': 'Product?'}]})}}]}
                reason = 'tool_calls'
            else:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'index': 0, 'id': 'stock-call', 'type': 'function', 'function': {
                        'name': 'tool_call', 'arguments': json.dumps({'calls': [
                            {'name': 'read_result', 'arguments': {'result_id': source_id}}]})}}]}
                reason = 'tool_calls'
            payload = {'id': 'stock-test', 'object': 'chat.completion', 'model': 'test-model', 'created': 1,
                       'choices': [{'index': 0, 'message': message, 'finish_reason': reason}],
                       'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}}
            mime = 'application/json'
            data = json.dumps(payload).encode()
            if request.get('stream'):
                deltas = [message] if reason == 'tool_calls' else [
                    {'role': 'assistant', 'content': 'ME'}, {'content': 'DIA:/private/tmp/'},
                    {'content': 'local.png\n\n'}, {'content': answer}]
                chunks = []
                for delta in deltas + [{}]:
                    chunks.append({**payload, 'object': 'chat.completion.chunk', 'choices': [
                        {'index': 0, 'delta': delta, 'finish_reason': None if delta else reason}]})
                data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                mime = 'text/event-stream'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionResetError, BrokenPipeError):
                pass

    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    monkeypatch.setattr(hermes_chat, 'ROOT', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        nonlocal source_id
        store = HarnessStore(database='test_stock_chat_' + uuid4().hex)
        settings = Settings(model='test-model', api_key='local', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        controller = RunController(store, settings)
        try:
            await store.setup()
            await MongoSessionStore(store, principal).create_session(session, 'api_server')
            source = await store.put_result(principal, session, {'run_id': 'fixture', 'invocation_id': 'data',
                'tool_name': 'query_db', 'status': 'success'}, [{'value': 7921}], [])
            source_id = source.result_id
            for query in ('read the stored result', second_prompt):
                run = await controller.start(principal, session, str(uuid4()), query)
                if query != second_prompt:
                    pending = PendingControls(store, principal)
                    async with asyncio.timeout(40):
                        while not (row := await pending.pending_prompts.find_one({
                            'run_id': run['run_id'], 'status': 'pending'})):
                            await asyncio.sleep(.05)
                    await pending.respond(session, row['execution_generation'], row['authority_ids'][0],
                                          {'answer': '4SA'}, 'clarify')
                await controller.tasks[run['run_id']]
                saved = await store.get_run(principal, run['run_id'])
                assert saved['status'] == 'completed', saved['stop_reason']
                assert saved['engine'] == 'hermes'
                assert saved['native_engine']['commit'] == native_hermes.UPSTREAM_COMMIT
                assert saved['native_engine']['history_source'] == 'hermes_session_db'
                assert saved['native_engine']['entrypoint'] == 'stock_chat'
                assert saved['answer_text'].strip() == answer
                assert local_media not in json.dumps(saved['native_messages'])
                title_events = [e['payload'] for e in saved['events'] if e['type'] == 'session.title']
                if query != second_prompt:
                    assert [o['tool_name'] for o in saved['observations']] == ['read_result']
                    assert saved['observations'][0]['invocation_id'] == 'stock-call'
                    sessions = MongoSessionStore(store, principal)
                    assert await sessions.get_session_title(session) == generated_title
                    assert await sessions.get_session_title_source(session) == 'llm'
                    assert title_events == [
                        {'session_id': session, 'title': query},
                        {'session_id': session, 'title': generated_title},
                    ]
                    await sessions.set_session_title(session, 'My chosen title')
                    # Resume a pre-search pin with a narrower employee allowlist.
                    with sqlite3.connect(native_hermes.profile_path(principal) / 'state.db') as db:
                        old_direct = [tool for name, tool in seen_tools[0].items()
                                      if name not in {'tool_search', 'tool_describe', 'tool_call'}]
                        pin = {'version': 'previous', 'tools': old_direct + domain_registry().model_tools()}
                        assert db.execute('UPDATE sessions SET tool_names = ? WHERE id = ?',
                                          (json.dumps(pin), session)).rowcount == 1
                    settings.native_allowed_tools = ['read_result']
                else:
                    assert not title_events
                    assert any(m.get('role') == 'tool' for m in saved['native_messages'])
                    assert await sessions.get_session_title(session) == 'My chosen title'
                    assert await sessions.get_session_title_source(session) == 'user'
            assert title_requests == ['read the stored result']
            assert len(answered_history) == 1
            prior = answered_history[0]
            assert any(answer in str(m['content']) for m in prior if m['role'] == 'assistant')
            selected = [m for m in prior if m['role'] == 'tool' and m['tool_call_id'] == 'scope-call']
            assert len(selected) == 1
            assert json.loads(selected[0]['content'])['responses'][0]['user_response'] == '4SA'
            assert all('_row_id' not in m for m in prior)
            scope = SimpleNamespace(sessions=sessions, execution=SimpleNamespace(store=store, principal_id=principal))
            transcript = await SessionRPC(scope, SimpleNamespace(mode='authority')).history(session)
            cards = [m for m in transcript if m.get('name') == 'clarify']
            assert len(cards) == 1 and '4SA' in cards[0]['content']
            journal = await store.db.gateway_event_logs.find_one({'principal_id': principal, 'session_id': session})
            live_messages = [json.loads(item['frozen']) for item in journal['ring']]
            assert any(event['type'] == 'message.delta' for event in live_messages)
            assert '/private/tmp/local.png' not in json.dumps(live_messages)
            expected = {'clarify', 'skills_list', 'skill_view', 'skill_manage', 'memory',
                        'tool_search', 'tool_describe', 'tool_call', 'read_file', 'search_files',
                        'delegate_task'}
            # The native vision schema is gated on the provider's image capability.
            assert seen_tools and all(set(tools) - {'vision_analyze'} == expected for tools in seen_tools)
            catalogs = [tools['tool_search']['function']['description'] for tools in seen_tools]
            assert 'query_db' in catalogs[0] and 'query_db' not in catalogs[-1]
            assert 'read_result' in catalogs[-1]
            assert all('todo_list' in catalog for catalog in catalogs)
            # Cancel while the host delivers text and its next pipe read is outstanding.
            streams = []
            def observe_reader(reader, remaining):
                stream = native_hermes.coalesced_worker_messages(reader, remaining)
                streams.append(stream)
                return stream

            async def hold_delta(self, session_id, typ, payload):
                if typ == 'message.delta':
                    entered.set()
                    await asyncio.Event().wait()

            monkeypatch.setattr(hermes_chat.SessionEvents, 'publish_live', hold_delta)
            monkeypatch.setattr(hermes_chat, 'coalesced_worker_messages', observe_reader)
            run = await controller.start(principal, session, str(uuid4()), 'cancel this turn')
            assert await asyncio.to_thread(entered.wait, 40)
            stopped = await asyncio.wait_for(controller.cancel(principal, run['run_id']), 15)
            assert stopped['status'] == 'cancelled'
            assert stopped['stop_reason'] == 'user_cancelled'
            assert streams[0].ag_frame is None
            assert controller.tasks[run['run_id']].done()
        finally:
            release.set()
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_pre_migration_admission_keeps_legacy_entry_on_retry_and_recovery(monkeypatch):
    routes = []

    async def fake_stock(context):
        routes.append(('stock', context.run['run_id']))
        return {'status': 'completed', 'stop_reason': 'completed', 'answer': 'done'}

    async def fake_legacy(context, registry, *, history):
        routes.append(('legacy', context.run['run_id']))
        return {'status': 'completed', 'stop_reason': 'completed', 'answer': 'done'}

    monkeypatch.setattr(hermes_chat, 'run_chat', fake_stock)
    monkeypatch.setattr(native_hermes, 'run_native', fake_legacy)

    async def scenario():
        store = HarnessStore(database='test_chat_recovery_' + uuid4().hex)
        controller = RunController(store, Settings())
        try:
            await store.setup()
            before = await store.start_run('owner', 'old-session', 'original-request', 'before upgrade')
            await store.runs.update_one({'_id': before['run_id']}, {'$unset': {'chat_entrypoint': ''}})
            retry = await store.start_run('owner', 'old-session', 'original-request', 'before upgrade')
            assert retry['run_id'] == before['run_id'] and 'chat_entrypoint' not in retry
            await controller.recover()
            await controller.tasks[before['run_id']]
            after = await controller.start('owner', 'old-session', 'new-request', 'after upgrade')
            await controller.tasks[after['run_id']]
            assert after['chat_entrypoint'] == 'stock_chat'
            assert routes == [('legacy', before['run_id']), ('stock', after['run_id'])]
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    asyncio.run(scenario())
