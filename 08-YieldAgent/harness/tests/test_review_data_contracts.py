import asyncio
import json
from types import SimpleNamespace


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
    async def execute(self, code, datasets):
        paths['/input/data.json'].write_text(json.dumps(datasets))
        paths['/input/code.py'].write_text(code)
        worker.main(['--input-dir', str(tmp_path), '--output-dir', str(tmp_path)])
        return json.loads(paths['/output/result.json'].read_text())
    monkeypatch.setattr(ContainerRuntime, 'execute', execute)
    ctx = SimpleNamespace(store=Store(), run={'principal_id': 'owner', 'session_id': 'session'},
        settings=SimpleNamespace(python_runtime='docker', python_image='unused'))
    result = asyncio.run(run_python(PythonInput(input_result_ids=['source'], code=
        "assert pd.isna(tables['source']['empty']['value'].mean())\n"
        "emit_table(tables['source']['empty'], name='roundtrip')"), ctx))
    assert result.status == 'success', result.summary
    assert result.tables[0].columns == ['value']
    assert result.tables[0].rows == []
