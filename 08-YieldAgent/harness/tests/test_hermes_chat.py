import asyncio
from contextlib import AsyncExitStack
import json
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from harness import hermes_chat, native_hermes
from harness.config import Settings
from harness.control import RunController
from harness.gateway.pending_controls import PendingControls
from harness.gateway.session_rpc import SessionRPC
from harness.gateway.session_store import MongoSessionStore
from harness.instructions import load_domain_notes
from harness.store import HarnessStore
from harness.tools.registry import domain_registry
from harness.web_cron_dispatch import gateway_schedule_lifespan


def test_reopened_chat_only_refreshes_obsolete_authorized_tool_descriptions():
    from inspect import signature
    from harness.hermes_chat_worker import outdated_descriptions, require_current_descriptions

    def described(names):
        return {'role': 'tool', 'tool_name': 'tool_describe', 'content': json.dumps({'tools': names})}

    old = {'parameters': {'properties': {}}, 'description': 'older'}
    new = {'parameters': {'properties': {'id': {'type': 'string'}}}, 'description': 'Current description'}
    history = [described({'example': old, 'other_employee_tool': old})]
    pending = outdated_descriptions(history, {'example'}, lambda name: new)
    assert pending == {'example'}
    entry = SimpleNamespace(handler=lambda args: 'executed')
    require_current_descriptions(SimpleNamespace(get_entry=lambda name: entry), pending)
    assert tuple(signature(entry.handler).parameters) == ('args',)
    assert 'tool_describe' in json.loads(entry.handler({}))['error']
    pending.remove('example')
    assert entry.handler({}) == 'executed'
    assert not outdated_descriptions([*history, described({'example': new})], {'example'}, lambda name: new)
    assert not outdated_descriptions([{**history[0], 'role': 'user'}], {'example'}, lambda name: new)


def test_stock_chat_reads_native_tool_in_authenticated_session(monkeypatch, tmp_path):
    """Drive the real stock worker, its native tool and a real isolated Mongo DB."""
    principal, session = 'chat-test', str(uuid4())
    seen_tools = []
    system_inputs = []
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
                content = next(m['content'] for m in messages if m['role'] == 'system')
                system_inputs.append(content if isinstance(content, str) else ''.join(p['text'] for p in content))
            if request.get('response_format', {}).get('json_schema', {}).get('name') == 'session_title':
                title_requests.append(messages[-1]['content'])
                message = {'role': 'assistant', 'content': json.dumps({'title': generated_title})}
                reason = 'stop'
            elif any(m.get('role') == 'tool' and '7921' in str(m['content']) for m in messages):
                message = {'role': 'assistant', 'content': local_media + '\n\n' + answer +
                           '<tool_call>tool_call<arg_key>calls</arg_key><arg_value>hidden frame'}
                reason = 'stop'
            elif not any(m.get('role') == 'tool' and m.get('tool_call_id') == 'mcp-status' for m in messages):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'index': 0, 'id': 'mcp-status', 'type': 'function', 'function': {
                        'name': 'manage_connections', 'arguments': json.dumps({'action': 'status'})}}]}
                reason = 'tool_calls'
            elif not any(m.get('role') == 'tool' and m.get('tool_call_id') == 'mcp-echo' for m in messages):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'index': 0, 'id': 'mcp-echo', 'type': 'function', 'function': {
                        'name': 'tool_call', 'arguments': json.dumps({'calls': [
                            {'name': 'mcp__yfd_echo__echo', 'arguments': {'text': 'hello'}}]})}}]}
                reason = 'tool_calls'
            elif not any(m.get('role') == 'tool' and 'USE_AUTHORIZED_RESULT' in str(m.get('content')) for m in messages):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'index': 0, 'id': 'skill-call', 'type': 'function', 'function': {
                        'name': 'skill_view', 'arguments': json.dumps({'name': 'test-report'})}}]}
                reason = 'tool_calls'
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
    directory = native_hermes.prepare_profile(principal) / 'skills' / 'test-report'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        '---\nname: test-report\ndescription: Read the stored report.\n---\nUSE_AUTHORIZED_RESULT')
    import sys
    import yaml
    mcp_server = tmp_path / 'echo_server.py'
    mcp_server.write_text("from mcp.server.fastmcp import FastMCP\n"
                          "server = FastMCP('echo')\n"
                          "@server.tool()\ndef echo(text: str) -> str:\n    return 'echo:' + text\n"
                          "server.run(transport='stdio')\n")
    config_path = native_hermes.profile_path(principal) / 'config.yaml'
    profile_config = yaml.safe_load(config_path.read_text()) or {}
    profile_config['mcp_servers'] = {'yfd_echo': {'command': sys.executable, 'args': [str(mcp_server)]}}
    config_path.write_text(yaml.safe_dump(profile_config))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        nonlocal source_id
        store = HarnessStore(database='test_stock_chat_' + uuid4().hex)
        settings = Settings(model='test-model', api_key='local', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        controller = RunController(store, settings)
        lifespans = AsyncExitStack()
        try:
            await store.setup()
            await MongoSessionStore(store, principal).create_session(session, 'api_server')
            app = SimpleNamespace(state=SimpleNamespace(gateway=SimpleNamespace(controller=controller)))
            await lifespans.enter_async_context(gateway_schedule_lifespan(app, principal=principal))
            source = await store.put_result(principal, session, {'run_id': 'fixture', 'invocation_id': 'data',
                'tool_name': 'query_db', 'status': 'success'}, [{'value': 7921}], [])
            source_id = source.result_id
            previous_request_time = None
            first_turn_tool_sets = 0
            for query in ('read the stored result', second_prompt):
                first_request = len(system_inputs)
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
                clock = saved['request_context']
                local_cutoff = datetime.fromisoformat(clock['requested_at']).replace(tzinfo=None)
                assert system_inputs[first_request:]
                for system in system_inputs[first_request:]:
                    assert system.count(load_domain_notes()) == 1
                    assert clock['requested_at'] in system and clock['timezone'] in system
                    assert f">= {local_cutoff.date()}T00:00:00" in system
                    assert f"<= {local_cutoff.isoformat()}" in system
                    if previous_request_time is not None:
                        assert previous_request_time not in system
                previous_request_time = clock['requested_at']
                assert saved['engine'] == 'hermes'
                assert saved['native_engine']['commit'] == native_hermes.UPSTREAM_COMMIT
                assert saved['native_engine']['history_source'] == 'hermes_session_db'
                assert saved['native_engine']['entrypoint'] == 'stock_chat'
                assert saved['answer_text'].strip() == answer
                assert local_media not in json.dumps(saved['native_messages'])
                title_events = [e['payload'] for e in saved['events'] if e['type'] == 'session.title']
                if query != second_prompt:
                    first_turn_tool_sets = len(seen_tools)
                    for cid, name in [('mcp-status', 'manage_connections'), ('mcp-echo', 'mcp__yfd_echo__echo')]:
                        tool_events = [e for e in saved['events'] if e.get('payload', {}).get('tool_id') == cid]
                        assert [e['type'] for e in tool_events] == ['tool_started', 'tool_finished']
                        start, finish = [e['payload'] for e in tool_events]
                        assert start['name'] == finish['name'] == name
                        assert finish['is_error'] is False
                        assert finish['duration_s'] >= 0
                        if cid == 'mcp-status':
                            assert finish['result']['local_mcp'] is True
                        else:
                            assert 'echo:hello' in json.dumps(finish['result'])
                    assert [(o['tool_name'], o['invocation_id']) for o in saved['observations']] == [
                        ('skill_view', 'skill-call'), ('read_result', 'stock-call')]
                    assert any(read['name'] == 'test-report' and read['resource'] == 'SKILL.md'
                               for read in saved['skill_reads'])
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
                    await lifespans.aclose()
                else:
                    assert not title_events
                    assert any(m.get('role') == 'tool' for m in saved['native_messages'])
                    assert await sessions.get_session_title(session) == 'My chosen title'
                    assert await sessions.get_session_title_source(session) == 'user'
            local_receipts = [m for m in saved['native_messages']
                              if m.get('role') == 'tool' and m.get('tool_call_id') == 'mcp-status']
            assert local_receipts and json.loads(local_receipts[0]['content'])['local_mcp'] is True
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
            mcp_rows = [m for m in transcript if m.get('name') == 'mcp__yfd_echo__echo']
            assert mcp_rows and 'echo:hello' in mcp_rows[0]['content']
            connection_rows = [m for m in transcript if m.get('name') == 'manage_connections']
            assert connection_rows and json.loads(connection_rows[0]['content'])['local_mcp'] is True
            cards = [m for m in transcript if m.get('name') == 'clarify']
            assert len(cards) == 1 and '4SA' in cards[0]['content']
            journal = await store.db.gateway_event_logs.find_one({'principal_id': principal, 'session_id': session})
            live_messages = [json.loads(item['frozen']) for item in journal['ring']]
            assert any(event['type'] == 'message.delta' for event in live_messages)
            assert '/private/tmp/local.png' not in json.dumps(live_messages)
            expected = {'clarify', 'skills_list', 'skill_view', 'skill_manage', 'memory',
                        'tool_search', 'tool_describe', 'tool_call', 'read_file', 'search_files',
                        'delegate_task', 'write_file', 'patch', 'manage_connections'}
            # The native vision schema is gated on the provider's image capability.
            board_tools = {'kanban_create', 'kanban_list', 'kanban_show', 'kanban_comment',
                           'kanban_attach', 'kanban_attach_url', 'kanban_attachments', 'kanban_complete',
                           'kanban_block', 'kanban_unblock', 'kanban_link', 'kanban_request_review',
                           'kanban_heartbeat', 'kanban_request_changes'}
            assert 0 < first_turn_tool_sets < len(seen_tools)
            for i, tools in enumerate(seen_tools):
                assert set(tools) - {'vision_analyze'} == (
                    expected | board_tools if i < first_turn_tool_sets else expected)
            catalogs = [tools['tool_search']['function']['description'] for tools in seen_tools]
            assert 'query_db' in catalogs[0] and 'query_db' not in catalogs[-1]
            assert 'cronjob_manage' in catalogs[0] and 'cronjob_manage' not in catalogs[-1]
            assert 'kanban_schedule' in catalogs[0] and 'kanban_schedule' not in catalogs[-1]
            assert 'read_result' in catalogs[-1]
            assert all('desktop_project' in catalog for catalog in catalogs)
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
            await lifespans.aclose()
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("stored_entrypoint", [None, "legacy_verified"])
def test_stock_chat_recovers_an_existing_request(monkeypatch, stored_entrypoint):
    routes = []

    async def fake_stock(context):
        routes.append(('stock', context.run['run_id']))
        return {'status': 'completed', 'stop_reason': 'completed', 'answer': 'done'}

    monkeypatch.setattr(hermes_chat, 'run_chat', fake_stock)

    async def scenario():
        store = HarnessStore(database='test_chat_recovery_' + uuid4().hex)
        controller = RunController(store, Settings())
        try:
            await store.setup()
            before = await store.start_run('owner', 'old-session', 'original-request', 'before upgrade')
            update = ({'$unset': {'chat_entrypoint': ''}} if stored_entrypoint is None else
                      {'$set': {'chat_entrypoint': stored_entrypoint}})
            await store.runs.update_one({'_id': before['run_id']}, update)
            retry = await store.start_run('owner', 'old-session', 'original-request', 'before upgrade')
            assert retry['run_id'] == before['run_id']
            assert retry.get('chat_entrypoint') == stored_entrypoint
            await controller.recover()
            await controller.tasks[before['run_id']]
            after = await controller.start('owner', 'old-session', 'new-request', 'after upgrade')
            await controller.tasks[after['run_id']]
            assert after['chat_entrypoint'] == 'stock_chat'
            assert routes == [('stock', before['run_id']), ('stock', after['run_id'])]
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    asyncio.run(scenario())


def test_recovered_stock_chat_does_not_spend_an_exhausted_model_or_token_budget(monkeypatch, tmp_path):
    """Recover persisted usage through the stock worker and count actual HTTP attempts."""
    posts = []

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            posts.append(self.path)
            self.send_error(503)

    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    monkeypatch.setattr(hermes_chat, 'ROOT', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        store = HarnessStore(database='test_stock_budget_' + uuid4().hex)
        settings = Settings(model='test-model', api_key='local', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        controller = RunController(store, settings)
        try:
            await store.setup()
            for resource, limit in [('models', settings.model_limit), ('tokens', settings.token_limit)]:
                run = await store.start_run('owner', resource, 'request', 'resume this turn')
                await store.runs.update_one({'_id': run['run_id']}, {'$set': {'usage.' + resource: limit}})
                await controller.recover()
                await controller.tasks[run['run_id']]
                saved = await store.get_run('owner', run['run_id'])
                assert saved['status'] == 'partial', saved['stop_reason']
                assert saved['stop_reason'] == resource + '_limit'
                assert saved['usage'][resource] == limit
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    try:
        asyncio.run(scenario())
        assert posts == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
