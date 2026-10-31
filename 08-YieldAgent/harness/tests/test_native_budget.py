import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from harness.native_budget import install_http_budget, NativeBudgetExceeded


@pytest.fixture
def budget():
    instances = []
    def create(**kwargs):
        value = install_http_budget(model_limit=kwargs.pop('model_limit', 3),
            token_limit=kwargs.pop('token_limit', 10000), max_output_tokens=100, **kwargs)
        instances.append(value)
        return value
    yield create
    for value in reversed(instances):
        value.close()


def payload(**kwargs):
    return {'model': 'offline', 'messages': [{'role': 'user', 'content': 'value'}],
        'max_tokens': 100, **kwargs}


def test_all_clients_share_admission_and_metadata_is_free(budget):
    used = budget()
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}})
    def call(index):
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            try:
                client.post('https://offline/v1/chat/completions', json=payload())
                return True
            except NativeBudgetExceeded:
                return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(call, range(6)))
    assert sum(outcomes) == len(sent) == 3
    assert used.snapshot() == {'models': 3, 'tokens': 30, 'input_tokens': 24, 'output_tokens': 6}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        client.post('https://offline/api/show', json={'name': 'offline'})
    assert used.snapshot()['models'] == 3


def test_stream_chunks_reconcile_usage_and_cap_child_output(budget):
    used = budget()
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            data = b'data: {"usage":null}\n\ndata: {"usage":{"prompt_tokens":9,"completion_tokens":3,"total_tokens":12}}\n\ndata: [DONE]\n\n'
            for i in range(0, len(data), 7):
                yield data[i:i+7]
    def respond(request):
        assert json.loads(request.content)['max_tokens'] == 100
        return httpx.Response(200, stream=Stream(), headers={'content-type': 'text/event-stream'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with client.stream('POST', 'https://offline/v1/chat/completions', json=payload(max_tokens=8192, stream=True)) as response:
            list(response.iter_bytes())
    assert used.snapshot()['tokens'] == 12
    assert used.snapshot()['models'] == 1


def test_unknown_usage_retains_reservation_and_stops_before_send(budget):
    used = budget(token_limit=250)
    sent = []
    def respond(request):
        sent.append(request)
        return httpx.Response(500, json={'error': 'unavailable'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        client.post('https://offline/v1/chat/completions', json=payload())
        charged = used.snapshot()['tokens']
        assert charged > 100
        with pytest.raises(NativeBudgetExceeded):
            client.post('https://offline/v1/chat/completions', json=payload())
    assert len(sent) == 1
    assert used.snapshot()['tokens'] == charged


def test_async_auxiliary_uses_same_budget_and_usage(budget):
    used = budget(model_limit=1)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={'usage': {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7}}))) as client:
            await client.post('https://offline/v1/responses', json={'model': 'offline', 'input': 'value', 'max_output_tokens': 100})
            with pytest.raises(NativeBudgetExceeded):
                await client.post('https://offline/v1/responses', json={'model': 'offline', 'input': 'value'})
    asyncio.run(scenario())
    assert used.snapshot() == {'models': 1, 'tokens': 7, 'input_tokens': 5, 'output_tokens': 2}


def test_sdk_retries_are_admitted_individually_on_local_server(budget):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from openai import OpenAI, APIConnectionError
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['content-length']))))
            body = b'{"error":{"message":"retry","type":"server_error"}}'
            self.send_response(500)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1'
    used = budget(model_limit=2, base_url=url)
    try:
        with OpenAI(base_url=url, api_key='offline', max_retries=2) as client:
            with pytest.raises(APIConnectionError):
                client.chat.completions.create(model='offline', messages=[{'role': 'user', 'content': 'value'}], max_tokens=100)
        assert len(calls) == used.snapshot()['models'] == 2
        assert used.snapshot()['tokens'] > 200
        assert used.exhausted_reason == 'models_limit'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_abandoned_stream_keeps_unknown_reservation(budget):
    used = budget()
    closed = []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices": [{"delta": {"content": "partial"}}]}\n\n'
            raise AssertionError('caller cancelled before second chunk')
        def close(self): closed.append(True)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        stream=Stream(), headers={'content-type': 'text/event-stream'}))) as client:
        with client.stream('POST', 'https://offline/v1/chat/completions', json=payload(stream=True)) as response:
            next(response.iter_bytes())
            reservation = used.snapshot()['tokens']
    assert closed
    assert reservation > 100
    assert used.snapshot()['tokens'] == reservation


def test_usage_events_include_reservation_then_actual_and_block_other_endpoint(budget):
    events = []
    used = budget(base_url='https://offline/v1', on_usage=events.append)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}}))) as client:
        client.post('https://offline/v1/chat/completions', json=payload())
        with pytest.raises(NativeBudgetExceeded, match='unsupported_model_endpoint'):
            client.post('https://other/v1/chat/completions', json=payload())
    assert len(events) == 3
    assert events[0]['tokens'] > events[1]['tokens'] == 10
    assert used.snapshot()['models'] == 1


def test_openai_sdk_stops_at_done_and_still_settles_stream_usage(budget):
    from openai import OpenAI
    used = budget()
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"id":"x","object":"chat.completion.chunk","model":"offline","choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}\n\n'
            yield b'data: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream(), headers={'content-type': 'text/event-stream'}))
    with OpenAI(base_url='https://offline/v1', api_key='offline', http_client=httpx.Client(transport=transport)) as client:
        with client.chat.completions.create(model='offline', messages=[{'role': 'user', 'content': 'value'}], stream=True) as stream:
            list(stream)
    assert used.snapshot()['tokens'] == 10


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('timeout, expected', [
    (httpx.Timeout(600, connect=1, write=None, pool=30), {'connect': 1, 'read': 2, 'write': 2, 'pool': 2}),
    (None, {'connect': 2, 'read': 2, 'write': 2, 'pool': 2}),
])
def test_sdk_timeout_is_capped_at_http_boundary(budget, asynchronous, timeout, expected):
    from openai import OpenAI, AsyncOpenAI
    budget(call_timeout=2)
    seen = []
    def respond(request):
        seen.append(dict(request.extensions['timeout']))
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'choices': [],
            'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}})
    transport = httpx.MockTransport(respond)
    async def scenario():
        async with AsyncOpenAI(base_url='https://offline/v1', api_key='offline', timeout=timeout,
            http_client=httpx.AsyncClient(transport=transport)) as client:
            await client.chat.completions.create(model='offline', messages=[{'role': 'user', 'content': 'value'}])
    if asynchronous:
        asyncio.run(scenario())
    else:
        with OpenAI(base_url='https://offline/v1', api_key='offline', timeout=timeout,
            http_client=httpx.Client(transport=transport)) as client:
            client.chat.completions.create(model='offline', messages=[{'role': 'user', 'content': 'value'}])
    assert seen == [expected]
