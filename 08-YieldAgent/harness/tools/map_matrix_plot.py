"""Package group metadata separately from viewport-sized coordinate reads."""
import base64
from collections import defaultdict
from contextlib import nullcontext
import io
import json
from pathlib import Path
import zlib


def _write_panel(rows, destination, bounds):
    encoder = zlib.compressobj(level=1)
    chunk = bytearray(b'[')
    separator = ''
    for cell in rows:
        row, col = cell['row'], cell['col']
        if bounds[0] is None:
            bounds[:] = [row, col, row, col]
        else:
            bounds[0], bounds[1] = min(row, bounds[0]), min(col, bounds[1])
            bounds[2], bounds[3] = max(row, bounds[2]), max(col, bounds[3])
        chunk.extend(f"{separator}{row},{col},{cell['fail_count']},{cell['observation_count']}".encode('ascii'))
        separator = ','
        if len(chunk) >= 65536:
            destination.write(encoder.compress(chunk))
            chunk.clear()
    chunk.extend(b']')
    destination.write(encoder.compress(chunk) + encoder.flush())


def matrix_artifact(groups, cells, matrix, coverage, *, spool_dir=None):
    if callable(cells):
        group_cells = cells
    else:
        by_group = defaultdict(list)
        for cell in cells:
            by_group[cell['group_id']].append(cell)
        group_cells = by_group.__getitem__

    packed_path = Path(spool_dir) / 'matrix-panels.deflate' if spool_dir is not None else None
    panels, bounds = [], [None] * 4
    with (packed_path.open('xb') if packed_path else nullcontext(None)) as stream:
        for group in groups:
            panel = {key: group[key] for key in ('measurement_count', 'wafer_count', 'observation_count', 'fail_count')}
            panel.update(row_value=str(group['row_value']), column_value=str(group['column_value']))
            if stream is not None:
                start = stream.tell()
                _write_panel(group_cells(group['group_id']), stream, bounds)
                panel.update(offset=start, length=stream.tell()-start)
            else:
                with io.BytesIO() as buffer:
                    _write_panel(group_cells(group['group_id']), buffer, bounds)
                    panel['points'] = base64.b64encode(buffer.getvalue()).decode('ascii')
            panels.append(panel)
    fields = ('measurement_count', 'included_measurement_count', 'missing_history_measurements',
              'missing_value_measurements', 'conflicting_group_measurements', 'source_exhausted')
    summary = {key: coverage[key] for key in fields if key in coverage}
    if coverage.get('missing_requests'):
        summary['missing_requests'] = {key: len(values) for key, values in coverage['missing_requests'].items()}
    axis_values = [[str(value) for value in sorted({g[key] for g in groups})]
                   for key in ('row_value', 'column_value')]
    payload = dict(matrix=matrix, coverage=summary, groups=panels, axis_values=axis_values,
                   bounds=bounds, packed=packed_path is not None)
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace('<', r'\u003c')
    template = Path(__file__).with_name('map_matrix.html').read_text(encoding='utf-8')
    artifact = dict(title='웨이퍼 누적 맵', mime='text/html', data=template.replace('<!-- MATRIX_DATA -->', data))
    if packed_path:
        artifact['matrix_panels'] = packed_path
    return artifact
