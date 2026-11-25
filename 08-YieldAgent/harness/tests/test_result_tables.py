import asyncio
import json
import uuid

import pytest


def test_model_preview_limits_each_table_by_its_full_size():
    from harness.types import TableRef, ToolObservation

    def table(size):
        return TableRef(table_id=str(size), title=str(size), columns=['n'], total_rows=size,
                        preview_rows=[{'n': n} for n in range(size)], complete=True, data_ref='blob')

    small, large = table(25), table(26)
    obs = ToolObservation(principal_id='a', session_id='s', run_id='r', invocation_id='i',
                          result_id='x', tool_name='run_python', tables=[small, large])
    view = obs.model_view()
    assert [len(t['preview_rows']) for t in view['tables']] == [25, 10]
    assert [t['truncated'] for t in view['tables']] == [False, True]
    assert view['truncated']
    for source, expected in [(small, 25), (large, 10)]:
        single = obs.model_copy(update=dict(tables=[source], total_rows=source.total_rows,
                                            preview_rows=source.preview_rows)).model_view()
        assert single['preview_rows'] == source.preview_rows[:expected]
        assert single['truncated'] == (source.total_rows > expected)


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
    worker.main(['--input-dir', str(tmp_path), '--output-dir', str(tmp_path)])
    result = json.loads(output.read_text())
    assert result['status'] == 'success', result
    assert [t['table_id'] for t in result['tables']] == ['weekly', 'wafer']
    assert result['tables'][1]['rows'] == [{'lot': 'x'}]
    code.write_text("emit_table([{'x': 1}], name='same')\nemit_table([{'y': 1}], name='same')")
    worker.main(['--input-dir', str(tmp_path), '--output-dir', str(tmp_path)])
    result = json.loads(output.read_text())
    assert result['status'] == 'error' and 'Duplicate' in result['error']['message']


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


def test_process_reads_full_exported_table_and_saves_artifacts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    from harness.runtime.process import ProcessRuntime
    from harness.store import HarnessStore
    from harness.tools.python_tools import ReadInput, read_result
    from harness.types import ResultTable

    async def scenario():
        store = HarnessStore(database='harness_process_' + uuid.uuid4().hex)
        try:
            await store.setup()
            source = await store.put_result('a', 's', dict(run_id='r', invocation_id='input', tool_name='fixture'), [], [],
                tables=[ResultTable(table_id='data', title='Fixture', rows=[{'n': i} for i in range(120)], complete=True)])
            class Backend:
                async def upload_file(self, stream):
                    path = tmp_path / 'source.csv'
                    path.write_bytes(stream.read())
                    return str(path)

            monkeypatch.setattr('harness.tools.python_tools.isolation_backend', lambda *_: Backend())
            ctx = SimpleNamespace(store=store, run={'principal_id': 'a', 'session_id': 's', 'run_id': 'r'},
                                  settings=SimpleNamespace(isolation=object()))
            exported = await read_result(ReadInput(result_id=source.result_id, table_id='data',
                                                  limit=10, export_for_python=True), ctx)
            assert len(exported.tables[0].rows) == 10
            csv = exported.scope['python_csv']
            assert csv['row_count'] == 120 and csv['source_complete']
            result = await ProcessRuntime(sys.executable).execute(
                f"df = pd.read_csv({csv['path']!r})\n"
                "emit_table(pd.DataFrame([{'count': len(df), 'total': int(df.n.sum())}]))\n"
                "emit_plot(px.bar(df, y='n'))\n"
                "df.to_csv(publish_directory / 'rows.csv', index=False)", {})
            assert result['status'] == 'success', result
            artifacts = [{'title': 'Chart', 'mime': 'text/html', 'data': html} for html in result['plots']]
            artifacts.extend(result['generated_files'])
            try:
                saved = await store.put_result('a', 's', dict(run_id='r', invocation_id='output',
                    tool_name='execute_code', source_result_ids=[source.result_id]), [], artifacts,
                    tables=[ResultTable.model_validate({**table, 'complete': True}) for table in result['tables']])
            finally:
                for item in result['generated_files']:
                    item['data'].close()
            assert await store.rows('a', 's', saved.result_id, table_id='table_1') == [{'count': 120, 'total': 7140}]
            assert len(saved.artifact_refs) == 2
            assert any(a.get("output_path", "").endswith("/rows.csv") for a in saved.artifact_refs)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
