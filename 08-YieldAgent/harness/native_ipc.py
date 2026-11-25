"""JSON-lines envelopes and worker-local RPC waiters (no engine dependencies)."""
import json
import threading
import uuid
from queue import Queue

CANCELLED = {'error': 'Backend invocation cancelled', 'status': 'cancelled'}


def correlation_id(kind):
    if kind not in ('call', 'control'):
        raise ValueError('Unknown ID space')
    return ('call-' if kind == 'call' else 'ctl-') + uuid.uuid4().hex


def call(method, call_id, **payload):
    return dict(payload, type='call', method=method, call_id=call_id)


def reply(call_id, result):
    return {'type': 'reply', 'call_id': call_id, 'result': result}


def control(action, **payload):
    return dict(payload, type='control', control_id=correlation_id('control'), action=action)


def valid(message):
    if not isinstance(message, dict):
        return False
    kind = message.get('type')
    if kind in ('call', 'reply', 'control'):
        field, prefix = ('control_id', 'ctl-') if kind == 'control' else ('call_id', 'call-')
        identity = message.get(field)
        if not isinstance(identity, str) or not identity.startswith(prefix) or len(identity) <= len(prefix):
            return False
        if ('call_id' if kind == 'control' else 'control_id') in message:
            return False
    if kind == 'call':
        if message.get('method') != 'tool_call':
            return False
        required = {'name': str, 'args': dict}
    elif kind == 'reply':
        return 'result' in message
    elif kind == 'control':
        return isinstance(message.get('action'), str)
    else:
        required = {'skill_read': {'name': str, 'resource': str, 'sha256': str},
            'session_title': {'title': str, 'source': str},
            'skill_result': {'invocation_id': str, 'name': str, 'args': dict, 'result': (str, dict)},
            'tool_start': {'invocation_id': str, 'name': str, 'args': dict},
            'tool_complete': {'invocation_id': str, 'name': str, 'args': dict, 'result': (str, dict, list, int, float, bool, type(None))},
            'wafer_item_saved': {'invocation_id': str, 'name': str, 'wafer_item': dict},
            'step': {'tokens': int, 'models': int}, 'commentary': {'content': str},
            'result': {'result': dict}, 'error': {'message': str}}.get(kind) if isinstance(kind, str) else None
        if required is None:
            return False
    return all(isinstance(message.get(key), expected) for key, expected in required.items())


def encode(message):
    if not valid(message):
        raise ValueError('Invalid IPC envelope')
    return json.dumps(message, ensure_ascii=False, default=str) + '\n'


def decode(line):
    try:
        message = json.loads(line)
        return message if valid(message) else None
    except (ValueError, TypeError, UnicodeError):
        return None


class Waiters:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}
        self.closed = None

    def register(self):
        identity, queue = correlation_id('call'), Queue()
        with self.lock:
            if self.closed is not None:
                queue.put(self.closed)
            else:
                self.pending[identity] = queue
        return identity, queue

    def discard(self, identity):
        with self.lock:
            self.pending.pop(identity, None)

    def deliver(self, message):
        if not valid(message) or message['type'] != 'reply':
            return False
        with self.lock:
            queue = self.pending.pop(message['call_id'], None)
            if queue is not None:
                queue.put(message['result'])
                return True
        return False

    def close(self, error):
        with self.lock:
            if self.closed is None:
                self.closed = error
            for queue in self.pending.values():
                queue.put(self.closed)
            self.pending.clear()


def receive(stream, waiters, on_control):
    try:
        for line in stream:
            message = decode(line)
            if message is None:
                continue
            if message['type'] == 'control':
                on_control(message)
            elif message['type'] == 'reply':
                waiters.deliver(message)
    finally:
        waiters.close({'error': 'Backend connection closed'})
