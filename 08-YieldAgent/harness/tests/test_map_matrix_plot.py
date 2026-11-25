import base64
import json
import re
import zlib

from harness.tools.map_matrix_plot import matrix_artifact


def test_sparse_coordinate_counts_survive_encoding_and_labels_are_text_only():
    label = '</script><script>oops()</script>'
    matrix = dict(rows=dict(source='legend', field='FLOW_ID'), columns=dict(source='map', field='WF_ID'))
    groups = [dict(group_id='one', row_value=label, column_value=12, measurement_count=70000,
                   wafer_count=200, observation_count=70000, fail_count=0)]
    cells = [dict(group_id='one', row=-9, col=8, observation_count=70000, fail_count=0)]
    coverage = dict(measurement_count=70003, included_measurement_count=70000, missing_history_measurements=3)
    html = matrix_artifact(groups, cells, matrix, coverage)['data']
    assert label not in html
    data = json.loads(re.search(r'<script type="application/json" id="matrix-data">(.*?)</script>', html, re.S)[1])
    assert data['matrix'] == matrix
    assert data['coverage'] == coverage
    assert data['groups'][0]['row_value'] == label
    assert data['bounds'] == [-9, 8, -9, 8]
    assert json.loads(zlib.decompress(base64.b64decode(data['groups'][0]['points']))) == [-9, 8, 0, 70000]


def test_axis_values_keep_numeric_order_and_large_number_identity_for_browser():
    matrix = dict(rows=dict(source='map', field='ROW'), columns=dict(source='map', field='COL'))
    values = [10, 9007199254740993, 2, 9007199254740992, -2]
    groups = [dict(group_id=v, row_value=v, column_value='x', measurement_count=1, wafer_count=1,
                   observation_count=1, fail_count=0) for v in values]
    html = matrix_artifact(groups, [], matrix, {})['data']
    data = json.loads(re.search(r'<script type="application/json" id="matrix-data">(.*?)</script>', html, re.S)[1])
    assert data['axis_values'][0] == ['-2', '2', '10', '9007199254740992', '9007199254740993']
    assert set(data['axis_values'][0]) == {g['row_value'] for g in data['groups']}


def test_packed_panels_stream_each_group_and_keep_counts_out_of_index(tmp_path):
    groups = [dict(group_id=k, wafer_count=2, measurement_count=2, observation_count=2, fail_count=i,
                   row_value=k, column_value=1) for i, k in enumerate(['A', 'B'])]
    def cells(group_id):
        yield dict(col=3, row=-2, fail_count=int(group_id == 'B'), observation_count=2)

    artifact = matrix_artifact(groups, cells, {}, {}, spool_dir=tmp_path)
    index = json.loads(re.search(r'<script type="application/json" id="matrix-data">(.*?)</script>',
                                 artifact['data'], re.S)[1])
    assert index['bounds'] == [-2, 3, -2, 3]
    with artifact['matrix_panels'].open('rb') as stream:
        for i, panel in enumerate(index['groups']):
            assert 'points' not in panel
            assert stream.tell() == panel['offset']
            assert json.loads(zlib.decompress(stream.read(panel['length']))) == [-2, 3, i, 2]
        assert not stream.read(1)
