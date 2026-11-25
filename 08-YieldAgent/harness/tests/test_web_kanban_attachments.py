import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bson import ObjectId
import pytest

from harness.web_kanban import install_web_kanban
from harness.web_kanban_attachments import Attach, install_attachment_tools, open_download, read_chat_artifact


@pytest.fixture
def web_chat(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from tools.registry import ToolRegistry
    artifacts = {}

    def start(employee, session, database='app'):
        registry = ToolRegistry()
        run = dict(principal_id=employee, session_id=session, run_id='r')
        published = {}
        def read_source(cmd, cap):
            try:
                return artifacts[(employee, session, cmd.artifact_id or cmd.output_path)]
            except KeyError:
                raise LookupError('No such artifact in this chat') from None
        install_web_kanban(registry, tmp_path / employee, database, run, invocation_id=lambda: None, emit=lambda _: None)
        install_attachment_tools(registry, tmp_path / employee, database, run, read_source=read_source,
                                 publish=published.__setitem__)
        def invoke(name, **args):
            return json.loads(registry.dispatch('kanban_' + name, args))
        return invoke, published
    return artifacts, start


def test_native_attachment_round_trip_in_new_chat_concurrent_retries_and_boundaries(web_chat, tmp_path):
    artifacts, start = web_chat
    chat, _ = start('Alice', 'one')
    second, published = start('Alice', 'two')
    tid = chat('create', title='Case')['task_id']
    blob = str(ObjectId())
    data = bytes(range(256))
    ref = dict(artifact_id=blob, title='검사 상세 #1.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    artifacts['Alice', 'one', blob] = ref, data
    with ThreadPoolExecutor(max_workers=4) as pool:
        res = list(pool.map(lambda _: chat('attach', task_id=tid, artifact_id=blob), range(4)))
    assert all(r['ok'] for r in res) and len({r['attachment_id'] for r in res}) == 1
    assert 'error' in second('attach', task_id=tid, artifact_id=blob)
    del artifacts['Alice', 'one', blob]
    file, = second('attachments', task_id=tid)['attachments']
    path = file['output_path']
    assert file['media'] == published[path] and '%' in path and 'stored_path' not in file
    meta, stream = open_download(tmp_path / 'Alice', 'app', path)
    with stream:
        assert stream.read() == data
    assert meta == dict(filename=ref['title'], content_type=ref['mime'], size=len(data))
    for employee, database in [('Bob', 'app'), ('Alice', 'other')]:
        wrong, _ = start(employee, 'one', database)
        assert 'error' in wrong('attachments', task_id=tid)
        with pytest.raises(LookupError):
            open_download(tmp_path / employee, database, path)
    with pytest.raises(LookupError):
        open_download(tmp_path / 'Alice', 'app', path + 'fake')
    original, = tmp_path.glob(f'Alice/web-kanban/*/attachments/{tid}/{blob}')
    original.unlink()
    replacement = tmp_path / 'outside.xlsx'
    replacement.write_bytes(data)
    original.symlink_to(replacement)
    with pytest.raises(LookupError):
        open_download(tmp_path / 'Alice', 'app', path)


def test_ref_is_matched_against_both_owner_and_session_before_read_and_size_check():
    blob_id = str(ObjectId())
    path = '/outputs/r/id/report.csv'
    ref = dict(output_path=path, title='report.csv', artifact_id=blob_id)
    matches = []
    async def find(filters, _projection):
        matches.append(filters)
        if filters == {'principal_id': 'Alice', 'session_id': 's', 'observation.artifact_refs.output_path': path}:
            return {'observation': {'artifact_refs': [ref]}}
    data = b'wafer,good'
    stream = SimpleNamespace(length=len(data), read=AsyncMock(return_value=data), close=lambda: None)
    store = SimpleNamespace(results=SimpleNamespace(find_one=find),
                            blobs=SimpleNamespace(open_download_stream=AsyncMock(return_value=stream)))
    cmd = Attach(task_id='t', output_path=path)
    run = dict(principal_id='Alice', session_id='s')
    assert asyncio.run(read_chat_artifact(store, run, cmd, 100)) == (ref, data)
    assert stream.read.await_count == 1
    with pytest.raises(ValueError, match='limit'):
        asyncio.run(read_chat_artifact(store, run, cmd, 2))
    assert stream.read.await_count == 1
    for run in [dict(principal_id='Bob', session_id='s'), dict(principal_id='Alice', session_id='other')]:
        with pytest.raises(LookupError):
            asyncio.run(read_chat_artifact(store, run, cmd, 100))
    assert store.blobs.open_download_stream.await_count == 2
    assert len(matches) == 4


def test_attach_url_requires_canonical_owned_chat_link(web_chat):
    from harness.artifact_delivery import artifact_url
    artifacts, start = web_chat
    chat, _ = start('Alice', 'with spaces')
    tid = chat('create', title='URL attachment')['task_id']
    blob = str(ObjectId())
    url = artifact_url('with spaces', blob)
    for foreign in ('http://127.0.0.1' + url, 'https://example.org' + url,
                    artifact_url('another', blob), url + '&extra=true', url):
        assert 'error' in chat('attach_url', task_id=tid, url=foreign)
    artifacts['Alice', 'with spaces', blob] = ({'artifact_id': blob, 'title': 'map.png'}, b'image')
    file = chat('attach_url', task_id=tid, url=url)
    assert file['ok'] and file['attachment_id'] == chat('attach', task_id=tid, artifact_id=blob)['attachment_id']
