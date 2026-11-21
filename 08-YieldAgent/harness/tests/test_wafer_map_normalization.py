import asyncio
from copy import deepcopy
from types import SimpleNamespace

from harness.native_verification import inspect_wafer_map_contracts
from harness.tests.test_wafer_map_aggregation_verification import (
    MemoryStore, SOURCE_ROWS, observation, table_ref, valid_tables,
)


def inspect(tables):
    source = observation('source', 'load_wafer_maps',
        tables=[table_ref('wafer_map_dies', SOURCE_ROWS)])
    plot = observation('plot', 'run_python', source_ids=['source'],
        tables=[table_ref(name, rows) for name, rows in tables.items()],
        artifacts=[{'artifact_id': 'chart', 'title': 'chart', 'mime': 'text/html'}])
    stored = {('source', 'wafer_map_dies'): SOURCE_ROWS}
    stored.update({('plot', name): rows for name, rows in tables.items()})
    return asyncio.run(inspect_wafer_map_contracts(MemoryStore(stored), 'p', 's', [source, plot]))


def unnamed_tables():
    return {f'table_{i + 1}': rows for i, rows in enumerate(valid_tables().values())}


def test_older_unnamed_evidence_is_recalculated_without_mutating_stored_tables():
    tables = unnamed_tables()
    before = deepcopy(tables)
    report = inspect(tables)
    assert report['verdict'] == 'pass'
    assert report['normalizations'] == [{'result_id': 'plot', 'table_names': {
        'table_1': 'wafer_map_manifest', 'table_2': 'wafer_map_members',
        'table_3': 'wafer_map_cells'}}]
    assert tables == before


def test_renaming_never_repairs_tampered_math_or_members():
    tables = unnamed_tables()
    tables['table_3'][0]['fail_rate'] = .99
    assert inspect(tables)['verdict'] == 'fail'
    tables = unnamed_tables()
    tables['table_2'][0]['measurement_id'] = 'absent'
    assert inspect(tables)['verdict'] == 'fail'


def test_ambiguous_or_canonical_invalid_evidence_is_not_replaced():
    tables = unnamed_tables()
    tables['other_manifest'] = deepcopy(tables['table_1'])
    assert inspect(tables)['verdict'] == 'fail'
    tables = unnamed_tables()
    tables['wafer_map_manifest'] = [{'invalid': True}]
    assert inspect(tables)['verdict'] == 'fail'


def test_one_table_matching_multiple_roles_is_not_inferred():
    tables = unnamed_tables()
    combined = {**tables['table_1'][0], **tables['table_2'][0]}
    tables['table_1'] = [combined]
    del tables['table_2']
    assert inspect(tables)['verdict'] == 'fail'


def test_missing_table_feedback_includes_exact_emission_method():
    report = inspect({'output': [{'stdout': 'no evidence'}]})
    assert 'emit_table' in ' '.join(report['issues'])
    assert "name='wafer_map_manifest'" in ' '.join(report['issues'])


def test_python_tool_persists_names_and_reports_normalization(monkeypatch):
    from harness.tools import python_tools

    tables = unnamed_tables()
    source = SimpleNamespace(status='success', tool_name='load_wafer_maps',
        provenance={'data_origin': 'fixture'},
        tables=[SimpleNamespace(table_id='wafer_map_dies', columns=list(SOURCE_ROWS[0]), complete=True)])

    class Store:
        async def observation(self, *args): return source
        async def rows(self, *args, **kwargs): return SOURCE_ROWS

    class Runtime:
        def __init__(self, *args): pass
        async def execute(self, code, datasets):
            return {'status': 'success', 'tables': [
                {'table_id': name, 'title': name, 'columns': list(rows[0]), 'rows': rows}
                for name, rows in tables.items()], 'plots': [], 'stdout': '', 'stderr': ''}

    monkeypatch.setattr(python_tools, 'ProcessRuntime', Runtime)
    ctx = SimpleNamespace(store=Store(), run={'principal_id': 'p', 'session_id': 's'},
        settings=SimpleNamespace(python_runtime='process', python_executable=None))
    result = asyncio.run(python_tools.run_python(
        python_tools.PythonInput(code='emit_table(...)', input_result_ids=['source']), ctx))
    assert [table.table_id for table in result.tables] == list(valid_tables())
    assert result.tables[0].rows == tables['table_1']
    assert 'table_1' in result.summary and 'wafer_map_manifest' in result.summary

    # Only one of four source measurements belongs to this binmap, including
    # one other retest of the same wafer in the source.
    manifest = dict(tables['table_1'][1], member_count=1, map_type='binmap')
    tables = {'wafer_map_manifest': [manifest], 'wafer_map_members': [tables['table_2'][2]]}
    result = asyncio.run(python_tools.run_python(
        python_tools.PythonInput(code='emit_table(...)', input_result_ids=['source']), ctx))
    evidence = result.scope['map_measurements']
    assert evidence['measurement_count'] == 1
    row, = evidence['preview_rows']
    assert (row['measurement_id'], row['lot_id'], row['wf_id'], row['source_result_id']) == ('m3', 'L2', 7, 'source')
    assert (row['pass_die_count'], row['die_count'], row['map_pass_fraction']) == (2, 3, 2/3)

    # A real measurement identifier combined with a different wafer has no matching source row.
    tables['wafer_map_members'][0]['wf_id'] = 99
    result = asyncio.run(python_tools.run_python(
        python_tools.PythonInput(code='emit_table(...)', input_result_ids=['source']), ctx))
    assert result.scope['map_measurements'] == {'selection_status': 'unverified', 'preview_rows': []}

    source.tool_name = 'query_db'
    result = asyncio.run(python_tools.run_python(
        python_tools.PythonInput(code='emit_table(...)', input_result_ids=['source']), ctx))
    assert [table.table_id for table in result.tables] == list(tables)
    assert 'map_measurements' not in result.scope
