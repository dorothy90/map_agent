import asyncio
from copy import deepcopy

import pytest

from harness.native_verification import inspect_wafer_map_contracts
from harness.tests.test_wafer_map_aggregation_verification import (
    MemoryStore, SOURCE_ROWS, observation, table_ref, valid_tables,
)


def inspect(tables, tool_name='run_python'):
    source = observation('source', 'load_wafer_maps',
        tables=[table_ref('wafer_map_dies', SOURCE_ROWS)])
    plot = observation('plot', tool_name, source_ids=['source'],
        tables=[table_ref(name, rows) for name, rows in tables.items()],
        artifacts=[{'artifact_id': 'chart', 'title': 'chart', 'mime': 'text/html'}])
    stored = {('source', 'wafer_map_dies'): SOURCE_ROWS}
    stored.update({('plot', name): rows for name, rows in tables.items()})
    return asyncio.run(inspect_wafer_map_contracts(MemoryStore(stored), 'p', 's', [source, plot]))


def unnamed_tables():
    return {f'table_{i + 1}': rows for i, rows in enumerate(valid_tables().values())}


@pytest.mark.parametrize('tool_name', ['run_python', 'execute_code'])
def test_older_unnamed_evidence_is_recalculated_without_mutating_stored_tables(tool_name):
    tables = unnamed_tables()
    before = deepcopy(tables)
    report = inspect(tables, tool_name)
    assert report['verdict'] == 'pass'
    assert report['normalizations'] == [{'result_id': 'plot', 'table_names': {
        'table_1': 'wafer_map_manifest', 'table_2': 'wafer_map_members',
        'table_3': 'wafer_map_cells'}}]
    assert tables == before


@pytest.mark.parametrize('tool_name', ['run_python', 'execute_code'])
def test_renaming_never_repairs_tampered_math_or_members(tool_name):
    tables = unnamed_tables()
    tables['table_3'][0]['fail_rate'] = .99
    assert inspect(tables, tool_name)['verdict'] == 'fail'
    tables = unnamed_tables()
    tables['table_2'][0]['measurement_id'] = 'absent'
    assert inspect(tables, tool_name)['verdict'] == 'fail'


@pytest.mark.parametrize('tool_name', ['run_python', 'execute_code'])
def test_ambiguous_or_canonical_invalid_evidence_is_not_replaced(tool_name):
    tables = unnamed_tables()
    tables['other_manifest'] = deepcopy(tables['table_1'])
    assert inspect(tables, tool_name)['verdict'] == 'fail'
    tables = unnamed_tables()
    tables['wafer_map_manifest'] = [{'invalid': True}]
    assert inspect(tables, tool_name)['verdict'] == 'fail'


@pytest.mark.parametrize('tool_name', ['run_python', 'execute_code'])
def test_one_table_matching_multiple_roles_is_not_inferred(tool_name):
    tables = unnamed_tables()
    combined = {**tables['table_1'][0], **tables['table_2'][0]}
    tables['table_1'] = [combined]
    del tables['table_2']
    assert inspect(tables, tool_name)['verdict'] == 'fail'


@pytest.mark.parametrize('tool_name', ['run_python', 'execute_code'])
def test_missing_table_feedback_includes_exact_emission_method(tool_name):
    report = inspect({'output': [{'stdout': 'no evidence'}]}, tool_name)
    assert 'emit_table' in ' '.join(report['issues'])
    assert "name='wafer_map_manifest'" in ' '.join(report['issues'])
