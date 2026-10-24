"""Worker-local admission for OpenAI-compatible HTTP model calls, including SDK retries.

Installed before Hermes imports, this covers independent clients used by delegates
and auxiliary models. It does not change Hermes' per-agent iteration budgets.
Unknown billing retains the full reservation, including cancelled streams.
"""
import json
import threading
from urllib.parse import urlsplit

import httpx


class NativeBudgetExceeded(RuntimeError):
    pass


class NativeBudget:
    def __init__(self, *, model_limit, token_limit, max_output_tokens, base_url=None, on_usage=None, call_timeout=None):
        self.model_limit, self.token_limit = model_limit, token_limit
        self.max_output_tokens, self.on_usage = max_output_tokens, on_usage
        self.call_timeout = call_timeout
        self.base_url = urlsplit(base_url) if base_url else None
        self.lock = threading.RLock()
        self.totals = dict(models=0, tokens=0, input_tokens=0, output_tokens=0)
        self.exhausted_reason = None
        self.original_send = httpx.Client.send
        self.original_async_send = httpx.AsyncClient.send

    def snapshot(self):
        with self.lock:
            return dict(self.totals)

    def _notify(self):
        if self.on_usage:
            self.on_usage(dict(self.totals))

    def _deny(self, reason):
        with self.lock:
            self.exhausted_reason = reason
            self._notify()
        raise NativeBudgetExceeded(reason)

    def admit(self, request, body):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return request, None
        if not isinstance(payload, dict) or 'model' not in payload or not any(key in payload for key in ('messages', 'input', 'prompt')):
            return request, None  # e.g. Ollama metadata {name: ...}, never an LLM call
        if self.base_url:
            url = urlsplit(str(request.url))
            if (url.scheme, url.netloc) != (self.base_url.scheme, self.base_url.netloc) or not url.path.startswith(self.base_url.path.rstrip('/') + '/'):
                self._deny('unsupported_model_endpoint')
        with self.lock:
            if self.totals['models'] >= self.model_limit:
                self._deny('models_limit')
            # UTF-8 byte count is deliberately conservative for text tokenizers;
            # unlike character/3 heuristics it also covers non-ASCII prompts.
            input_bound = len(body)
            available = self.token_limit - self.totals['tokens'] - input_bound
            if available < 1:
                self._deny('tokens_limit')
            key = next((key for key in ('max_completion_tokens', 'max_output_tokens', 'max_tokens') if key in payload),
                'max_output_tokens' if 'input' in payload else 'max_tokens')
            requested = payload.get(key)
            cap = min(self.max_output_tokens, available, requested if isinstance(requested, int) and requested > 0 else self.max_output_tokens)
            payload[key] = cap
            # Remove contradictory output caps rather than forwarding two values.
            for other in ('max_completion_tokens', 'max_output_tokens', 'max_tokens'):
                if other != key:
                    payload.pop(other, None)
            if payload.get('stream') and 'messages' in payload and '/chat/completions' in str(request.url):
                payload['stream_options'] = {**(payload.get('stream_options') or {}), 'include_usage': True}
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            headers = dict(request.headers)
            headers.pop('content-length', None)
            headers['accept-encoding'] = 'identity'
            extensions = dict(request.extensions)
            if self.call_timeout is not None:
                timeout = extensions.get('timeout') or {}
                extensions['timeout'] = {
                    phase: min(timeout[phase], self.call_timeout) if timeout.get(phase) is not None else self.call_timeout
                    for phase in ('connect', 'read', 'write', 'pool')}
            replacement = httpx.Request(request.method, request.url, headers=headers,
                content=encoded, extensions=extensions)
            charge = input_bound + cap
            self.totals['models'] += 1
            self.totals['tokens'] += charge
            self._notify()  # Persist before sending; concurrent calls keep this order.
            return replacement, _Receipt(self, charge)

    def install(self):
        budget = self
        def send(client, request, **kwargs):
            request, receipt = budget.admit(request, request.read())
            if receipt is None:
                return budget.original_send(client, request, **kwargs)
            stream = kwargs.pop('stream', False)
            response = budget.original_send(client, request, stream=True, **kwargs)
            receipt.configure(response)
            if response.is_stream_consumed:
                receipt._parse(response.content)
                receipt.finish(complete=True)
            response.stream = _SyncStream(response.stream, receipt)
            if not stream:
                try:
                    response.read()
                finally:
                    response.close()
            return response
        async def async_send(client, request, **kwargs):
            request, receipt = budget.admit(request, await request.aread())
            if receipt is None:
                return await budget.original_async_send(client, request, **kwargs)
            stream = kwargs.pop('stream', False)
            response = await budget.original_async_send(client, request, stream=True, **kwargs)
            receipt.configure(response)
            if response.is_stream_consumed:
                receipt._parse(response.content)
                receipt.finish(complete=True)
            response.stream = _AsyncStream(response.stream, receipt)
            if not stream:
                try:
                    await response.aread()
                finally:
                    await response.aclose()
            return response
        httpx.Client.send = send
        httpx.AsyncClient.send = async_send
        return self

    def close(self):
        httpx.Client.send = self.original_send
        httpx.AsyncClient.send = self.original_async_send


class _Receipt:
    def __init__(self, budget, reserved):
        self.budget, self.reserved = budget, reserved
        self.pending = bytearray()
        self.usage = {}
        self.finished = False
        self.terminal = False

    def configure(self, response):
        self.success = response.is_success
        self.sse = 'text/event-stream' in response.headers.get('content-type', '')
        # Separate decoder state from the caller's response decoder (gzip/SSE).
        self.decoder = httpx.Response(200, headers=response.headers)._get_content_decoder()

    def _parse(self, raw):
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if not isinstance(value, dict):
            return
        if value.get('type') == 'response.completed':
            self.terminal = True
        usage = value.get('usage') or (value.get('response') or {}).get('usage')
        if isinstance(usage, dict):
            for target, names in [('input_tokens', ('prompt_tokens', 'input_tokens')),
                                  ('output_tokens', ('completion_tokens', 'output_tokens')),
                                  ('tokens', ('total_tokens',))]:
                for name in names:
                    count = usage.get(name)
                    if isinstance(count, int) and count >= 0:
                        self.usage[target] = count
                        break

    def feed(self, raw):
        self.pending.extend(self.decoder.decode(raw))
        if self.sse:
            while b'\n' in self.pending:
                line, _, rest = self.pending.partition(b'\n')
                self.pending = bytearray(rest)
                if line.startswith(b'data:'):
                    data = line[5:].strip()
                    if data == b'[DONE]':
                        self.terminal = True
                    else:
                        self._parse(data)

    def finish(self, complete=False):
        if self.finished:
            return
        self.finished = True
        if not self.sse:
            self._parse(bytes(self.pending))
        usage = self.usage
        total = usage.get('tokens')
        if total is None and all(key in usage for key in ('input_tokens', 'output_tokens')):
            total = usage['input_tokens'] + usage['output_tokens']
        if (complete or self.terminal) and self.success and total is not None:
            with self.budget.lock:
                self.budget.totals['tokens'] += total - self.reserved
                for key in ('input_tokens', 'output_tokens'):
                    self.budget.totals[key] += usage.get(key, 0)
                self.budget._notify()


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, stream, receipt):
        self.stream, self.receipt = stream, receipt

    def __iter__(self):
        for chunk in self.stream:
            self.receipt.feed(chunk)
            yield chunk
        self.receipt.finish(complete=True)

    def close(self):
        try:
            self.stream.close()
        finally:
            self.receipt.finish()


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, stream, receipt):
        self.stream, self.receipt = stream, receipt

    async def __aiter__(self):
        async for chunk in self.stream:
            self.receipt.feed(chunk)
            yield chunk
        self.receipt.finish(complete=True)

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.receipt.finish()


def install_http_budget(**kwargs):
    return NativeBudget(**kwargs).install()
