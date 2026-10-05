import json

from harness.completion import render_results
from harness.context import build_context


def observation():
    return {'principal_id': 'p', 'session_id': 's', 'run_id': 'r', 'invocation_id': 'i',
        'result_id': 'data', 'tool_name': 'run_python', 'status': 'success',
        'tables': [{'table_id': name, 'title': name, 'columns': [name], 'total_rows': 1,
                    'preview_rows': [{name: value}], 'complete': True, 'data_ref': name}
                   for name, value in [('weekly', 731), ('wafer', 829)]]}


def test_named_table_values_are_loaded_once_and_bounded():
    state = {'goal': {}, 'run_id': 'r', 'observations': [observation()], 'active_result_ids': ['data']}
    messages = build_context(state, 'instructions')
    meta = json.loads(messages[1].content)
    tables = meta['evidence'][0]['tables']
    assert [(table['table_id'], table['preview_rows']) for table in tables] == [
        ('weekly', [{'weekly': 731}]), ('wafer', [{'wafer': 829}])]
    assert meta['evidence'][0]['preview_rows'] == []
    assert all('preview_rows' not in table for table in meta['result_index'][0]['tables'])


def test_final_render_preserves_two_separate_table_headers():
    text = render_results([observation()])
    assert '| weekly |' in text and '| wafer |' in text
    assert '| 731 |' in text and '| 829 |' in text
