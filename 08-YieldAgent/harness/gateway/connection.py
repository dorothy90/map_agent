"""One authenticated /api/ws peer; H/tui_gateway/ws.py:440-510, server_requests.py.

No upstream equivalent for Mongo polling/connection aliases → PB:503-505,664,698.
No global principal state, authority fallback, engine creation or REST mutation.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import logging
import uuid

import anyio
from starlette.websockets import WebSocketDisconnect

from .events import event_frame, replay_gap
from .execution import RpcError
from .session_rpc import NotConnected, SessionRPC, validate
from .static_rpc import STATIC_METHODS, static_rpc
from .unsupported import rpc_rejection


EXECUTION_METHODS = frozenset({'prompt.submit', 'prompt.receipt', 'prompt.cancel',
    'prompt.resolve_unknown', 'session.interrupt', 'session.status', 'session.events.since',
    'clarify.respond', 'clarify.lock', 'approval.respond', 'approval.pending', 'request.answer'})
LIFECYCLE_METHODS = frozenset({'session.create', 'session.resume', 'session.activate',
                              'session.close', 'session.detach'})
SESSION_METHODS = LIFECYCLE_METHODS | {'session.list', 'session.most_recent',
    'session.history', 'session.title', 'session.info', 'session.usage'}
logger = logging.getLogger(__name__)


def error(rid, code, message):
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def unconnected(rid, method):
    frame = error(rid, -32005, 'handler_not_connected')
    frame['error']['data'] = {'code': 'handler_not_connected', 'reason': 'handler_not_connected',
                              'feature': method, 'upstream': '5894e750'}
    return frame


@dataclass
class Subscription:
    token: str
    epoch: str
    sequence: int
    ready: bool = False
    owner: object = None


class GatewayConnection:
    def __init__(self, websocket, principal, services, manifest, mode):
        self.ws, self.principal, self.services = websocket, principal, services
        self.manifest, self.mode = manifest, mode
        self.scope = None
        self.scope_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.subscription_lock = asyncio.Lock()
        self.subscriptions = {}
        self.run_bindings = {}
        self.requests = {}  # wire alias -> (session_id, durable pending ID)
        self.request_methods = {}
        self.server_requests = False
        self.tasks = set()
        self.closed = False

    async def send(self, frame):
        async with self.send_lock:
            if not self.closed:
                try:
                    await self.ws.send_json(frame)
                except (WebSocketDisconnect, RuntimeError):
                    self.closed = True
                    raise

    async def context(self):
        async with self.scope_lock:
            if self.scope is None:
                self.scope = await self.services.open(self.principal)
        return self.scope

    def bind_run(self, sid, run):
        # Only server-read snapshot bindings may fence run-local generations.
        if run:
            self.run_bindings[sid] = run['run_id']
        else:
            self.run_bindings.pop(sid, None)

    async def subscribe(self, sid, state=None, *, replace=False):
        scope = await self.context()
        if state is None:
            state = await scope.execution.events.watermark(sid)
        async with self.subscription_lock:
            existing = self.subscriptions.get(sid)
            if existing:
                if not replace:
                    return existing.token
                state = dict(state)
                if existing.epoch == state['replay_epoch']:
                    state['last_sequence'] = max(existing.sequence, state['last_sequence'])
                # Replace the object too: an in-flight poll of the old cursor
                # must fail the identity check, not overwrite this snapshot.
            sub = Subscription(uuid.uuid4().hex, state['replay_epoch'], state['last_sequence'],
                               owner=asyncio.current_task())
            self.subscriptions[sid] = sub
            return sub.token

    async def detach(self, sid, token=None):
        async with self.subscription_lock:
            sub = self.subscriptions.get(sid)
            if sub is None or (token is not None and sub.token != token):
                return False
            self.subscriptions.pop(sid)
            self.run_bindings.pop(sid, None)
        await self.release_requests(sid)
        return True

    async def open_requests(self, sid):
        if self.mode != 'legacy' or not self.server_requests:
            return []
        execution = (await self.context()).execution
        prompts = await execution._current_prompts(sid)
        frames, seen = [], set()
        for prompt in prompts:
            row = await execution._pending_row(sid, prompt['prompt_id'], prompt['execution_generation'])
            if not row or row['prompt_id'] in seen:
                continue
            seen.add(row['prompt_id'])
            frame = await execution.pending.legacy_request(sid, row['execution_generation'], row['prompt_id'])
            if frame:
                frames.append(frame)
        # Register aliases only after every pending read succeeds.
        return [self.alias(frame, sid) for frame in frames]

    def alias(self, frame, sid):
        frame = deepcopy(frame)
        durable = frame['id']
        alias = next((key for key, value in self.requests.items() if value == (sid, durable)), None)
        if alias is None:
            # Same original srq- format; unique per connection prevents cross-peer
            # response-frame settlement even when both subscribe to one session.
            alias = 'srq-' + uuid.uuid4().hex[:12]
            self.requests[alias] = (sid, durable)
            self.request_methods[alias] = frame['method']
            self.services.claim_request(self.principal.user_id, sid, durable, alias)
        frame['id'] = alias
        if 'request_id' in frame.get('params', {}):
            frame['params']['request_id'] = alias
        return {k: frame[k] for k in ('id', 'method', 'params')}

    def drop_request(self, alias):
        sid, durable = self.requests.pop(alias)
        self.request_methods.pop(alias, None)
        return self.services.release_request(self.principal.user_id, sid, durable, alias)

    async def release_requests(self, sid=None):
        if self.scope is None:
            return
        execution = self.scope.execution
        for alias, (session_id, durable) in list(self.requests.items()):
            if sid is None or session_id == sid:
                last_reader = self.drop_request(alias)
                # H/server_requests.py:337-362 cancellation wakes waiting tools.
                if last_reader:
                    try:
                        await execution.pending.cancel(durable, reason='disconnect')
                    except Exception:
                        # Continue releasing other aliases even when storage is
                        # unavailable. Durable timeout still bounds the waiter.
                        logger.error('Gateway pending cancellation failed')
        await execution._publish_pending()

    async def respond(self, frame):
        identity = frame.get('id')
        if not isinstance(identity, str) or identity not in self.requests:
            return  # H/server_requests.py:280-310: unmatched responses dropped.
        sid, durable = self.requests[identity]
        execution = (await self.context()).execution
        if 'error' in frame:
            await execution.pending.cancel(durable, reason='client_error')
            await execution._publish_pending()
        else:
            result = frame.get('result')
            await execution.request_answer(sid, {'id': durable, 'result': result if isinstance(result, dict) else {}})
        if identity in self.requests:
            self.drop_request(identity)

    async def request_session(self, params, key):
        identity = params.get(key)
        if not isinstance(identity, str) or not identity:
            raise RpcError('id and an object result required' if key == 'id' else
                           'request_id and question_id required', code=4002)
        if identity in self.requests:
            sid, durable = self.requests[identity]
            return sid, {**params, key: durable}
        # request.answer intentionally supports missed requests; scope by verified
        # principal, never by arbitrary client principal/session/profile fields.
        execution = (await self.context()).execution
        row = await execution.pending.pending_prompts.find_one({
            'principal_id': self.principal.user_id, 'prompt_id': identity, 'status': 'pending'})
        return (row['session_id'] if row else None), params

    async def dispatch(self, method, params, rid):
        if method in STATIC_METHODS:
            return static_rpc(method, params)
        if method in ('ping', 'gateway.capabilities', 'client.capabilities'):
            validate(params, ('server_requests', 'desktop_local') if method == 'client.capabilities' else (), mode=self.mode)
            if method == 'ping':
                return {'pong': True}
            if method == 'gateway.capabilities':
                # Legacy submit remains unsupported (PR-7a); never advertise it.
                return {'per_session_exclusive_submit': False}
            for key in params:
                if type(params[key]) is not bool:
                    raise RpcError('invalid_params', code=4000)
            if 'server_requests' in params:
                self.server_requests = params['server_requests']
            return {'server_requests': ['approval', 'clarify'], 'declines_not_shown': False}
        if method == 'approval.received':
            # PR-6 timeout starts at durable registration, not display ACK.
            # No fake acknowledgement of H/methods_prompt.py:1315-1324.
            raise NotConnected()
        if method not in EXECUTION_METHODS and method not in SESSION_METHODS:
            raise NotConnected()  # Deferred RPCs must not even open the store.
        scope = await self.context()
        if method not in EXECUTION_METHODS:
            return await SessionRPC(scope, self).dispatch(method, params)
        sid = params.get('session_id')
        if method in ('request.answer', 'clarify.lock'):
            validate(params, ('id', 'result') if method == 'request.answer' else
                     ('request_id', 'question_id', 'answer'), mode=self.mode)
            if method == 'request.answer' and not isinstance(params.get('result'), dict):
                raise RpcError('id and an object result required', code=4002)
            if method == 'clarify.lock' and not isinstance(params.get('question_id'), str):
                raise RpcError('request_id and question_id required', code=4002)
            sid, params = await self.request_session(params, 'id' if method == 'request.answer' else 'request_id')
            if sid is None:
                return {'status': 'expired'}
        if method == 'approval.respond' and self.mode == 'legacy':
            identity = params.get('request_id')
            if isinstance(identity, str) and identity in self.requests:
                bound_sid, durable = self.requests[identity]
                if bound_sid != sid:
                    raise RpcError('not_found')
                params = {**params, 'request_id': durable}
        # Establish a pre-submit cursor, but don't publish a subscription for an
        # invalid/failed submit. No await between reading and saving the mark.
        mark = None
        if method == 'prompt.submit' and self.mode == 'authority':
            await scope.execution._session(sid, params)
            mark = await scope.execution.events.watermark(sid)
        frame = await scope.execution.rpc(method, sid, params, rid=rid, mode=self.mode,
                                          expected_run_id=self.run_bindings.get(sid))
        if 'error' in frame:
            raise WireError(frame['error'])
        result = frame['result']
        if mark is not None:
            await self.subscribe(sid, mark)
            self.run_bindings[sid] = result['run_id']
        if method == 'session.events.since' and self.mode == 'legacy':
            result['open_requests'] = ([self.alias(item, sid) for item in result.get('open_requests', [])]
                                       if self.server_requests else [])
        return result

    async def process(self, frame):
        rid, method = frame['id'], frame['method']
        try:
            rejection = rpc_rejection(self.manifest, method, rid, mode=self.mode)
            if rejection is not None:
                await self.send(rejection)
                return
            params = frame.get('params', {})
            if not isinstance(params, dict):
                raise RpcError('invalid_params', code=4000 if self.mode == 'legacy' else 4001)
            # Host identity is immutable; wire profile selectors cannot elevate it.
            params = {k: v for k, v in params.items() if k not in
                      ('principal', 'principal_id', 'profile', 'profile_id')}
            if method in LIFECYCLE_METHODS:
                async with self.lifecycle_lock:
                    result = await self.dispatch(method, params, rid)
            else:
                result = await self.dispatch(method, params, rid)
            await self.send({'jsonrpc': '2.0', 'id': rid, 'result': result})
            # Snapshot/reply precedes its new subscription's first pushed frame.
            for sub in self.subscriptions.values():
                if sub.owner is asyncio.current_task():
                    sub.ready, sub.owner = True, None
        except (RpcError, WireError) as exc:
            await self.send({'jsonrpc': '2.0', 'id': rid, 'error': exc.error})
        except NotConnected:
            await self.send(unconnected(rid, method))
        except Exception:
            await self.send(error(rid, -32603, 'Internal error'))

    async def pump_once(self):
        if self.scope is None:
            return
        execution = self.scope.execution
        for sid, sub in list(self.subscriptions.items()):
            if not sub.ready:
                continue
            result = await execution.events.since(sid, sub.epoch, sub.sequence)
            async with self.subscription_lock:
                if self.subscriptions.get(sid) is not sub:
                    continue
                if result['truncated'] or result['snapshot_required']:
                    await self.send(replay_gap(sid, result['epoch'], result['latest_seq']))
                    sub.epoch, sub.sequence = result['epoch'], result['latest_seq']
                    sub.ready = False  # original overflow ends the subscription
                else:
                    for item in result['events']:
                        if item['seq'] <= sub.sequence:
                            continue
                        if item['seq'] != sub.sequence + 1:
                            await self.send(replay_gap(sid, result['epoch'], result['latest_seq']))
                            sub.sequence = result['latest_seq']
                            sub.ready = False
                            break
                        await self.send(event_frame(item))
                        sub.sequence = item['seq']
            if self.mode == 'legacy' and self.server_requests:
                async with self.subscription_lock:
                    if self.subscriptions.get(sid) is not sub or not sub.ready:
                        continue
                    previous = set(self.requests)
                    frames = await self.open_requests(sid)
                    current = {f['id'] for f in frames}
                    for alias in previous - current:
                        binding = self.requests.get(alias)
                        if binding and binding[0] == sid:
                            method = self.request_methods[alias]
                            row = await execution.pending.pending_prompts.find_one({
                                'principal_id': self.principal.user_id, 'session_id': sid,
                                'prompt_id': binding[1]})
                            reason = {'resolved': 'resolved', 'timed_out': 'timeout'}.get(
                                (row or {}).get('status'), 'interrupted')
                            self.drop_request(alias)
                            await self.send(event_frame({'type': 'request.cancel', 'session_id': sid,
                                'payload': {'id': alias, 'method': method, 'reason': reason}}))
                    for frame in frames:
                        if frame['id'] not in previous:
                            await self.send({'jsonrpc': '2.0', **frame})

    async def pump(self):
        try:
            while True:
                await asyncio.sleep(self.services.poll_interval)
                try:
                    await self.pump_once()
                except RpcError as exc:
                    # PR-7a pending snapshot uses the admission lock. A submit
                    # holding it is normal contention, not a broken transport.
                    if exc.error.get('data', {}).get('reason') != 'session_busy':
                        raise
        except asyncio.CancelledError:
            raise
        except Exception:
            # Storage/push failure is not an empty successful poll. Reconnect
            # recovers from durable replay; never expose driver errors or secrets.
            async with self.send_lock:
                if not self.closed:
                    self.closed = True
                    await self.ws.close(code=1011)

    def finished(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve send failures even if the peer just left.

    async def run(self):
        pump = asyncio.create_task(self.pump())
        try:
            while True:
                try:
                    frame = await self.ws.receive_json()
                except (ValueError, UnicodeDecodeError):
                    await self.send(error(None, -32700, 'Parse error'))
                    continue
                if (isinstance(frame, dict) and 'method' not in frame and 'id' in frame
                        and ('result' in frame or 'error' in frame)):
                    try:
                        await self.respond(frame)
                    except RpcError:
                        pass  # A response has no response; invalid answers stay open.
                    except Exception:
                        await self.send(error(None, -32603, 'Internal error'))
                    continue
                if (not isinstance(frame, dict) or frame.get('jsonrpc') != '2.0'
                        or not isinstance(frame.get('method'), str)
                        or ('id' in frame and isinstance(frame['id'], (bool, dict, list)))):
                    await self.send(error(None, -32600, 'Invalid Request'))
                    continue
                if 'id' not in frame:
                    continue
                if frame['method'] == 'ping':
                    await self.process(frame)
                    continue
                if len(self.tasks) >= self.services.max_requests:
                    await self.send(error(frame['id'], 4001, 'session_busy'))
                    continue
                task = asyncio.create_task(self.process(frame))
                self.tasks.add(task)
                task.add_done_callback(self.finished)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.closed = True
            pump.cancel()
            pending = list(self.tasks)
            for task in pending:
                task.cancel()
            # ASGI/TestClient can cancel the enclosing AnyIO scope immediately
            # after disconnect; cleanup must still settle pending tool waiters.
            with anyio.CancelScope(shield=True):
                await asyncio.gather(pump, *pending, return_exceptions=True)
                try:
                    await self.release_requests()
                except Exception:
                    logger.error('Gateway pending cleanup publication failed')
                finally:
                    self.subscriptions.clear()
                    self.run_bindings.clear()
                    self.requests.clear()
                    self.request_methods.clear()
                    self.tasks.clear()


class WireError(Exception):
    def __init__(self, wire_error):
        self.error = wire_error
