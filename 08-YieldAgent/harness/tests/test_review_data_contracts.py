import asyncio
import sys


def test_python_empty_table_keeps_schema_through_worker():
    from harness.runtime.process import ProcessRuntime

    result = asyncio.run(ProcessRuntime(sys.executable).execute(
        "assert pd.isna(tables['source']['empty']['value'].mean())\n"
        "emit_table(tables['source']['empty'], name='roundtrip')",
        {'source': {'empty': {'rows': [], 'columns': ['value']}}}))
    assert result['status'] == 'success', result
    assert result['tables'][0]['columns'] == ['value']
    assert result['tables'][0]['rows'] == []
