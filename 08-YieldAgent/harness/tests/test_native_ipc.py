import io
import json
from queue import Empty

import pytest


def ipc_module():
    from harness import native_ipc
    return native_ipc


def test_ids_and_duplicate_reply_isolation():
    ipc = ipc_module()
    table = ipc.Waiters()
    first, q1 = table.register()
    second, q2 = table.register()
    assert first.startswith('call-') and second != first
    control = ipc.control('interrupt')
    assert control['control_id'].startswith('ctl-')
    assert not table.deliver({**control, 'control_id': first, 'result': 'bad'})
    assert not table.deliver(ipc.reply('call-unknown', 'bad'))
    assert table.deliver(ipc.reply(first, 'ok'))
    assert not table.deliver(ipc.reply(first, 'duplicate'))
    assert q1.get_nowait() == 'ok'
    with pytest.raises(Empty): q2.get_nowait()
    table.discard(second)
    assert not table.deliver(ipc.reply(second, 'late'))


@pytest.mark.parametrize('line', [b'\xff\n', 'bad json', '{}', '[]', 'null', '{"type":"unknown"}',
    '{"type":"reply","result":1}', '{"type":"reply","call_id":"ctl-a","result":1}',
    '{"type":"control","control_id":"call-a","action":"interrupt"}'])
def test_invalid_messages_are_ignored(line):
    assert ipc_module().decode(line) is None


def test_receive_survives_invalid_lines_and_eof_releases_all():
    ipc = ipc_module()
    table = ipc.Waiters()
    first, q1 = table.register()
    _, q2 = table.register()
    controls = []
    stream = io.StringIO('bad\n{}\n' + ipc.encode(ipc.control('ping')) + ipc.encode(ipc.reply(first, 'ok')))
    ipc.receive(stream, table, controls.append)
    assert q1.get_nowait() == 'ok'
    assert q2.get_nowait()['error'] == 'Backend connection closed'
    assert len(controls) == 1
    _, late = table.register()
    assert late.get_nowait()['error'] == 'Backend connection closed'


def test_close_retains_cancel_reason_and_releases_future_waiters():
    ipc = ipc_module()
    table = ipc.Waiters()
    key, queue = table.register()
    table.close(ipc.CANCELLED)
    table.close({'error': 'Backend connection closed'})
    assert queue.get_nowait() == ipc.CANCELLED
    assert not table.deliver(ipc.reply(key, 'late'))
    assert table.register()[1].get_nowait() == ipc.CANCELLED


def test_call_roundtrip():
    ipc = ipc_module()
    message = ipc.call('tool_call', 'call-example', name='read_result', args={})
    assert ipc.decode(ipc.encode(message)) == message
    assert message['type'] == 'call' and message['method'] == 'tool_call'


def test_parallel_and_commentary_settings(monkeypatch, isolated_settings_env):
    from harness.config import Settings
    from pydantic import ValidationError
    assert Settings().parallel_tool_limit == 8
    assert Settings().publish_commentary is False
    for value in (0, 9):
        with pytest.raises(ValidationError): Settings(parallel_tool_limit=value)
    monkeypatch.setenv('HARNESS_PARALLEL_TOOLS', '3')
    monkeypatch.setenv('HARNESS_PUBLISH_COMMENTARY', 'true')
    settings = Settings.from_env()
    assert settings.parallel_tool_limit == 3 and settings.publish_commentary
