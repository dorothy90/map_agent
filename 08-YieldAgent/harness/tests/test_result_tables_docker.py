"""Actual isolated worker + Mongo roundtrip (requires yield-harness-python:2)."""
import asyncio
import uuid


def test_docker_named_tables_plot_store_roundtrip():
    from harness.runtime.container import ContainerRuntime
    from harness.store import HarnessStore
    from harness.types import ResultTable

    async def scenario():
        runtime = ContainerRuntime('yield-harness-python:2')
        result = await runtime.execute("emit_table(tables['source']['weeks'], name='weekly')\nemit_table(tables['source']['wafers'], name='wafer')\nemit_plot(px.bar(tables['source']['weeks'], x='week', y='reports'))", {'source': {'weeks': [{'week': 'W1', 'reports': 3}], 'wafers': [{'lot': 'fixture', 'wafers': 7}]}})
        assert result['status'] == 'success', result
        assert [t['table_id'] for t in result['tables']] == ['weekly', 'wafer']
        store = HarnessStore(database='harness_tables_docker_' + uuid.uuid4().hex)
        try:
            await store.setup()
            obs = await store.put_result('a', 's', dict(run_id='r', invocation_id='i', tool_name='run_python'), [], [{'title': 'Chart', 'mime': 'text/html', 'data': result['plots'][0]}], tables=[ResultTable.model_validate(t) for t in result['tables']])
            assert obs.tables[0].columns == ['week', 'reports']
            assert obs.tables[1].columns == ['lot', 'wafers']
            assert await store.rows('a', 's', obs.result_id, 'wafer') == [{'lot': 'fixture', 'wafers': 7}]
            _, chart = await store.artifact('a', 's', obs.artifact_refs[0]['artifact_id'])
            assert b'plotly' in chart.lower()
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
