import asyncio
import json
from types import SimpleNamespace

import pytest


def test_python_empty_table_keeps_schema_through_worker(tmp_path, monkeypatch):
    from harness.runtime import worker
    from harness.runtime.container import ContainerRuntime
    from harness.tools.python_tools import PythonInput, run_python
    from harness.types import ToolObservation, TableRef

    obs = ToolObservation(principal_id='owner', session_id='session', run_id='run',
        invocation_id='inv', result_id='source', tool_name='query', tables=[
            TableRef(table_id='empty', title='Empty', columns=['value'], total_rows=0,
                preview_rows=[], complete=True, data_ref='blob')])
    class Store:
        async def observation(self, *args):
            return obs
        async def rows(self, *args, **kwargs):
            return []
    paths = {name: tmp_path / name.rsplit('/', 1)[-1] for name in
        ['/input/data.json', '/input/code.py', '/output/result.json']}
    monkeypatch.setattr(worker, 'Path', lambda path: paths[path])
    async def execute(self, code, datasets):
        paths['/input/data.json'].write_text(json.dumps(datasets))
        paths['/input/code.py'].write_text(code)
        worker.main()
        return json.loads(paths['/output/result.json'].read_text())
    monkeypatch.setattr(ContainerRuntime, 'execute', execute)
    ctx = SimpleNamespace(store=Store(), run={'principal_id': 'owner', 'session_id': 'session'},
        settings=SimpleNamespace(python_image='unused'))
    result = asyncio.run(run_python(PythonInput(input_result_ids=['source'], code=
        "assert pd.isna(tables['source']['empty']['value'].mean())\n"
        "emit_table(tables['source']['empty'], name='roundtrip')"), ctx))
    assert result.status == 'success', result.summary
    assert result.tables[0].columns == ['value']
    assert result.tables[0].rows == []


@pytest.mark.parametrize('offset,limit,partial', [(0, 3, True), (3, 100, True), (20, 100, True), (0, 100, False)])
@pytest.mark.parametrize('kind', ['artifact', 'history'])
def test_document_pages_expose_source_completeness(monkeypatch, kind, offset, limit, partial):
    from harness.tools import document_tools as mod
    class Store:
        async def artifact(self, *args):
            return {'mime': 'text/plain', 'result_id': 'source'}, b'0123456789'
    ctx = SimpleNamespace(store=Store(), run={'principal_id': 'owner', 'session_id': 'session'})
    if kind == 'artifact':
        result = asyncio.run(mod.read_artifact_text(mod.ArtifactTextInput(
            artifact_id='stored', offset=offset, limit=limit), ctx))
    else:
        import fail_history_tools
        monkeypatch.setattr(fail_history_tools, '_fetch_results_by_doc_ids',
            lambda *args, **kwargs: [{'doc_id': 'doc', 'content': '0123456789'}])
        async def direct(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        monkeypatch.setattr(mod, 'run_blocking', direct)
        result = asyncio.run(mod.get_history_document(mod.HistoryDocumentInput(
            doc_id='doc', offset=offset, limit=limit), ctx))
    assert (result.status == 'partial') is partial
    assert result.scope['complete'] is not partial
    assert bool(result.scope['missing_reason']) is partial
