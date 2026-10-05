import asyncio
import json
import uuid

import pytest


def test_legacy_observation_adapts_without_claiming_partial_complete():
    from harness.types import ToolObservation
    obs = ToolObservation.model_validate(dict(schema_version='harness-observation/v1', principal_id='a', session_id='s', run_id='r', invocation_id='i', result_id='x', tool_name='q', status='partial', columns=['n'], preview_rows=[{'n': 1}], total_rows=120, truncated=True, data_ref='blob'))
    assert len(obs.tables) == 1
    assert obs.tables[0].table_id == 'default'
    assert not obs.tables[0].complete
    assert obs.tables[0].total_rows == 120


def test_named_tables_roundtrip_and_ambiguous_selection():
    from harness.store import HarnessStore
    from harness.types import ResultTable

    async def scenario():
        store = HarnessStore(database='harness_tables_' + uuid.uuid4().hex)
        try:
            await store.setup()
            tables = [ResultTable(table_id='weekly', title='Weeks', rows=[{'week': i} for i in range(120)], complete=True), ResultTable(table_id='wafer', title='Wafers', rows=[{'lot': 'fixture', 'wafers': 7}], complete=False, missing_reason='source limited')]
            obs = await store.put_result('a', 's', dict(run_id='r', invocation_id='i', tool_name='q'), [], [], tables=tables)
            assert obs.schema_version == 'harness-observation/v2'
            assert [t.table_id for t in obs.tables] == ['weekly', 'wafer']
            assert obs.tables[0].columns == ['week']
            assert obs.tables[0].complete and len(obs.tables[0].preview_rows) == 50
            assert obs.columns == [] and obs.preview_rows == []
            with pytest.raises(ValueError, match='weekly.*wafer'):
                await store.rows('a', 's', obs.result_id)
            assert await store.rows('a', 's', obs.result_id, table_id='wafer') == [{'lot': 'fixture', 'wafers': 7}]
            page = await store.read_result('a', 's', obs.result_id, table_id='wafer')
            assert page['complete'] is False and page['missing_reason'] == 'source limited'
            assert page['truncated'] is False
            again = await store.put_result('a', 's', dict(run_id='r', invocation_id='i', tool_name='q'), [], [], tables=tables)
            assert again == obs
            assert await store.db.harness_blobs.files.count_documents({}) == 2
            with pytest.raises(PermissionError):
                await store.rows('other', 's', obs.result_id, table_id='weekly')
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_tool_result_rejects_ambiguous_rows_and_tables():
    from harness.tools.registry import ToolResult
    from harness.types import ResultTable
    with pytest.raises(ValueError, match='rows.*tables'):
        ToolResult(rows=[{'n': 1}], tables=[ResultTable(table_id='t', title='T', rows=[], complete=True)])


def test_worker_keeps_tables_distinct_and_rejects_duplicate_names(tmp_path, monkeypatch):
    from harness.runtime import worker
    data, code, output = tmp_path / 'data.json', tmp_path / 'code.py', tmp_path / 'result.json'
    data.write_text(json.dumps({'r': {'weekly': [{'week': 1}], 'wafer': [{'lot': 'x'}]}}))
    code.write_text("assert 'r' not in datasets\nemit_table(tables['r']['weekly'], name='weekly')\nemit_table(tables['r']['wafer'], name='wafer')")
    paths = {'/input/data.json': data, '/input/code.py': code, '/output/result.json': output}
    monkeypatch.setattr(worker, 'Path', lambda path: paths[path])
    worker.main()
    result = json.loads(output.read_text())
    assert result['status'] == 'success', result
    assert [t['table_id'] for t in result['tables']] == ['weekly', 'wafer']
    assert result['tables'][1]['rows'] == [{'lot': 'x'}]
    code.write_text("emit_table([{'x': 1}], name='same')\nemit_table([{'y': 1}], name='same')")
    worker.main()
    result = json.loads(output.read_text())
    assert result['status'] == 'error' and 'Duplicate' in result['error']['message']


def test_python_inherits_incomplete_input_even_when_stdout_claims_complete(monkeypatch):
    from types import SimpleNamespace
    from harness.types import ToolObservation
    from harness.tools.python_tools import PythonInput, run_python
    from harness.runtime.container import ContainerRuntime
    obs = ToolObservation(principal_id='a', session_id='s', run_id='r', invocation_id='i', result_id='x', tool_name='q', status='partial', data_ref='blob')
    class Store:
        async def observation(self, *args):
            return obs
        async def rows(self, *args, **kwargs):
            return [{'n': 1}]
    async def execute(self, code, datasets):
        assert datasets == {'x': {'default': {'rows': [{'n': 1}], 'columns': ['n']}}}
        return {'status': 'success', 'tables': [{'table_id': 'summary', 'title': 'summary', 'rows': [{'n': 1}], 'complete': True}], 'plots': [], 'stdout': 'all data complete', 'stderr': ''}
    monkeypatch.setattr(ContainerRuntime, 'execute', execute)
    ctx = SimpleNamespace(store=Store(), run={'principal_id': 'a', 'session_id': 's'}, settings=SimpleNamespace(python_image='unused'))
    result = asyncio.run(run_python(PythonInput(code='print(1)', input_result_ids=['x']), ctx))
    assert result.status == 'partial'
    assert not result.tables[0].complete
    assert 'x/default' in result.tables[0].missing_reason
    assert result.source_result_ids == ['x']


def test_v1_gridfs_record_reads_without_rewrite():
    from harness.store import HarnessStore
    async def scenario():
        store = HarnessStore(database='harness_v1_tables_' + uuid.uuid4().hex)
        try:
            await store.setup()
            blob = await store.blobs.upload_from_stream('legacy', b'[{"n":7}]')
            legacy = dict(schema_version='harness-observation/v1', principal_id='a', session_id='s', run_id='r', invocation_id='i', result_id='legacy', tool_name='q', data_ref=str(blob), columns=['n'], preview_rows=[{'n': 7}], total_rows=1)
            await store.results.insert_one({'result_id': 'legacy', 'principal_id': 'a', 'session_id': 's', 'observation': legacy})
            assert await store.rows('a', 's', 'legacy') == [{'n': 7}]
            page = await store.read_result('a', 's', 'legacy')
            assert page['table_id'] == 'default' and page['complete']
            persisted = await store.results.find_one({'result_id': 'legacy'})
            assert 'tables' not in persisted['observation']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
