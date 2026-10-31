"""Offline contracts for wafer selection, ranking, and map rendering."""
import asyncio
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest


def module():
    from harness.tools import wafer_set_tools
    return wafer_set_tools


def version(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def args(**changes):
    values = dict(process='PT1H', time_column='end_tm', time_from='2026-01-01',
                  time_to='2026-02-01', pass_bin='A', bin_side='left_bin')
    values.update(changes)
    return module().SelectWafersInput(**values)


def record(lot='A', pairs=('AA', 'AB', 'BB'), day=1, **changes):
    value = dict(lot_id=lot, wf_id=1, lot_cd='P', process='PT1H TEST',
                 start_tm=datetime(2026, 1, 1), end_tm=datetime(2026, 1, day),
                 map_val_json=json.dumps({'MAP': [f'0,{i},{p[0]},{p[1:]}' for i, p in enumerate(pairs)]}))
    value.update(changes)
    return value


def select(monkeypatch, records, **changes):
    m = module()
    monkeypatch.setattr(m, '_fetch_measurements', lambda a: records)
    monkeypatch.setattr(m, '_fetch_lot_codes', lambda: set())
    return m._select_wafers(args(**changes))


class Store:
    def __init__(self, result):
        self.result = result

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ('owner', 'session', 'source'):
            raise PermissionError('Result unavailable')
        return SimpleNamespace(scope=self.result.scope, tables=self.result.tables,
                               status=self.result.status, provenance={'data_origin': 'fixture'})

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return json.loads(json.dumps(next(t.rows for t in self.result.tables if t.table_id == table_id)))


def context(result):
    return SimpleNamespace(store=Store(result), run={'principal_id': 'owner', 'session_id': 'session'})


def handle(result, table='wafers', **changes):
    return dict(result_id='source', table_id=table, version=result.scope['table_versions'][table], **changes)


def rank(result, **changes):
    values = dict(source=handle(result), order_by='yield_percent', direction='asc', top_n=1)
    values.update(changes)
    return asyncio.run(module().rank_members(module().RankMembersInput(**values), context(result)))


def check_versions(result):
    assert result.scope['handle_contract'] == 'wafer-table/v1'
    for t in result.tables:
        assert result.scope['table_versions'][t.table_id] == version(json.loads(json.dumps(t.rows)))


def test_registration_flag_and_descriptions(monkeypatch):
    from harness.tools.registry import domain_registry
    new_names = {'select_wafers', 'rank_members', 'render_maps', 'describe_sources',
             'list_values', 'inspect_coverage', 'read_facts', 'join_facts',
             'filter_set', 'make_cohorts', 'compute_metrics', 'aggregate_metrics',
             'reduce_maps', 'compare_metrics', 'render_view'}
    names = new_names | {'export_report', 'read_result', 'run_python'}
    monkeypatch.delenv('HARNESS_WAFER_SET_TOOLS', raising=False)
    before = domain_registry().tools
    assert not new_names.intersection(before)
    monkeypatch.setenv('HARNESS_WAFER_SET_TOOLS', '1')
    after = domain_registry().tools
    assert set(after) == set(before) | new_names
    def descriptions(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == 'description':
                    yield value
                else:
                    yield from descriptions(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from descriptions(value)
    for name in names:
        spec = after[name]
        assert spec.eager == (name in {'select_wafers', 'rank_members', 'render_maps',
                                      'describe_sources', 'read_result', 'run_python'})
        if name in {'render_view', 'export_report', 'run_python'}:
            assert not spec.read_only and spec.effect == 'artifact'
        else:
            assert spec.read_only and spec.effect == 'read'
        texts = [spec.description, *descriptions(spec.schema.model_json_schema())]
        allowed = {'DF_SYMBOL_BIN.PARAM_NM 값(bin 파라미터 이름) 목록'}
        assert all(text in allowed or bad not in text
                   for text in texts for bad in ('PT1H', 'PT1C', '?', 'LANGGRAPH', 'MAP.', 'DF_'))


def test_selection_bin_sides_and_metadata(monkeypatch):
    for side, expected in [('left_bin', 200 / 3), ('right_bin', 100 / 3)]:
        result = select(monkeypatch, [record()], bin_side=side)
        table = result.tables[0]
        row = table.rows[0]
        assert row['yield_percent'] == pytest.approx(expected)
        assert row['calc_bin_side'] == side and row['calc_pass_bin'] == 'A'
        assert row['calc_mode'] == 'bin' and row['calc_symbol_parameters'] is None
        assert row['payload_hash'] == hashlib.sha256(record()['map_val_json'].encode()).hexdigest()[:16]
        assert row['time_value'] == '2026-01-01 00:00:00'
        assert table.columns == list(row)
        assert table.units == {'yield_percent': 'percent', 'pass_die_count': 'dies', 'die_count': 'dies'}
        assert result.scope['interval'] == '[time_from,time_to)'
        assert result.scope['time_basis'] == 'end_tm'
        check_versions(result)


def test_measurement_policy_and_ties(monkeypatch):
    records = [record(pairs=['BB']), record(pairs=['AA'], day=2), record(pairs=['AA'], day=2)]
    latest = select(monkeypatch, records)
    assert len(latest.tables[0].rows) == 1
    assert latest.tables[0].rows[0]['yield_percent'] == 100
    assert len(select(monkeypatch, records, measurement_policy='all').tables[0].rows) == 3
    with pytest.raises(ValueError, match='Conflicting'):
        select(monkeypatch, [record(pairs=['AA']), record(pairs=['BB'])])
    assert select(monkeypatch, [record(pairs=['AA']), record(pairs=['BB']), record(day=2)]).status == 'success'


def test_empty_maps_limit_and_empty_result(monkeypatch):
    result = select(monkeypatch, [record(pairs=[]), record('B')])
    assert result.status == 'partial' and not result.tables[0].complete
    assert result.tables[1].rows[0]['excluded_empty_maps'] == 1
    result = select(monkeypatch, [record('A'), record('B')], max_rows=1)
    assert result.status == 'partial' and len(result.tables[0].rows) == 1
    assert result.scope['missing_reason'] and result.tables[1].rows[0]['source_measurement_count'] == 2
    assert select(monkeypatch, []).status == 'empty'


def test_empty_measurements_reinterpret_process_lot_code_and_retry(monkeypatch):
    m = module()
    calls = []

    def fetch(value):
        calls.append(value)
        return [] if len(calls) == 1 else [record(lot_cd='4SA')]

    monkeypatch.setattr(m, '_fetch_measurements', fetch)
    monkeypatch.setattr(m, '_fetch_lot_codes', lambda: {'4SA', '4SS'})
    result = m._select_wafers(args(process='4SA PT1H'))

    assert len(calls) == 2
    assert calls[0].process == '4SA PT1H' and calls[0].lot_cd == []
    assert calls[1].process == 'PT1H' and calls[1].lot_cd == ['4SA']
    assert result.scope['process'] == 'PT1H' and result.scope['lot_cd'] == ['4SA']
    assert result.scope['limitations'] == [{
        'code': 'PROCESS_TOKEN_REINTERPRETED_AS_LOT_CD',
        'detail': "process token '4SA' was reinterpreted as lot_cd",
    }]


def test_empty_measurements_reject_process_that_is_only_lot_code(monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_fetch_measurements', lambda value: [])
    monkeypatch.setattr(m, '_fetch_lot_codes', lambda: {'4SS'})

    with pytest.raises(ValueError, match=r"4SS.*lot_cd.*process.*list_values"):
        m._select_wafers(args(process='4SS'))


def test_measurement_rows_do_not_reinterpret_process(monkeypatch):
    m = module()
    calls = []
    monkeypatch.setattr(m, '_fetch_measurements', lambda value: calls.append(value) or [record()])
    monkeypatch.setattr(m, '_fetch_lot_codes', lambda: pytest.fail('LOT_CD lookup must not run'))

    result = m._select_wafers(args(process='4SA PT1H'))

    assert len(calls) == 1
    assert calls[0].process == '4SA PT1H' and calls[0].lot_cd == []
    assert result.scope['process'] == '4SA PT1H' and 'limitations' not in result.scope


def test_symbol_union_unmatched_and_lot_loading(monkeypatch):
    calls = []
    def load(lot, process, names):
        calls.append((lot, process, names))
        return {'left': 'B.', 'right': '.B'}
    monkeypatch.setattr(module(), 'load_symbol_patterns', load)
    result = select(monkeypatch, [record(pairs=['BA', 'AB', 'BB', 'AA', 'AXX']), record('B', lot_cd='Q')],
                    pass_bin=None, bin_side=None, symbol_bin_parameters=['left', 'right'])
    row = result.tables[0].rows[0]
    assert row['yield_percent'] == 40 and row['unmatched_cells'] == 1
    assert result.status == 'partial' and not result.tables[0].complete
    assert {c[0] for c in calls} == {'P', 'Q'}
    assert row['calc_symbol_parameters'] == 'left,right'
    check_versions(result)


def test_symbol_failures_require_the_full_two_character_pair():
    m = module()
    cells = [(0, 0, 'A', 'B'), (0, 1, 'A', 'C'),
             (0, 2, 'C', 'B'), (0, 3, 'C', 'D')]
    definition = {'calc_mode': 'symbol'}

    metric, failures = m._calculate(
        cells, definition, {'pair': m.make_symbol_fail_matcher({'pair': 'AB'})})

    assert failures == [['pair'], [], [], []]
    assert metric['pass_die_count'] == 3
    assert metric['unmatched_cells'] == 0
    for pattern in ('A', 'B'):
        _, one_side = m._calculate(
            cells[:1], definition,
            {'pair': m.make_symbol_fail_matcher({'pair': pattern})})
        assert one_side == [[]]


@pytest.mark.parametrize('left,right', [(None, 'B'), ('', 'B'), ('AB', ''), ('A', 'BC')])
def test_malformed_pairs_are_unmatched_and_never_fail(left, right):
    m = module()
    metric, failures = m._calculate(
        [(0, 0, left, right)], {'calc_mode': 'symbol'},
        {'pair': m.make_symbol_fail_matcher({'pair': 'AB'})})

    assert failures == [[]]
    assert metric['unmatched_cells'] == 1
    labels, codes = m._image_categories(
        [(0, 0, left, right)], failures, {'calc_mode': 'symbol'},
        'pass_fail', ['pair'])
    assert labels[codes[0]] == 'unmatched'


def test_bin_value_categories_show_combined_pair_and_unmatched():
    m = module()
    cells = [(0, 0, 'A', 'B'), (0, 1, 'AB', '')]
    labels, codes = m._image_categories(
        cells, [[], []], {'calc_mode': 'bin', 'calc_bin_side': 'left_bin'},
        'bin_value', [])

    assert labels[codes[0]] == 'AB'
    assert labels[codes[1]] == 'unmatched'


@pytest.mark.parametrize('changes', [dict(pass_bin=None, bin_side=None), dict(bin_side=None),
    dict(pass_bin=None), dict(symbol_bin_parameters=['x']), dict(time_to='2025-01-01'),
    dict(symbol_bin_parameters=[]), dict(extra=1), dict(time_column='bad')])
def test_invalid_selection(changes):
    with pytest.raises(ValueError):
        args(**changes)


def test_time_inputs():
    assert args().time_from == datetime(2026, 1, 1)
    assert args(time_from='2025-12-31T15:00:00Z').time_from == datetime(2026, 1, 1)
    assert args(time_from='2026-01-01T09:00:00+09:00').time_from == datetime(2026, 1, 1, 9)


def test_ranking_ties_groups_and_nulls(monkeypatch):
    source = select(monkeypatch, [record('B'), record('A'), record('C', pairs=['BB']), record('D', pairs=['AA'])])
    exact = rank(source, direction='desc', top_n=2)
    assert [r['lot_id'] for r in exact.tables[1].rows] == ['D', 'A']
    assert [r['rank'] for r in exact.tables[0].rows] == [1, 2, 2, 3]
    tied = rank(source, direction='desc', top_n=2, tie_policy='include_ties')
    assert len(tied.tables[1].rows) == 3 and tied.scope['cutoff_tie_count'] == 2
    assert rank(source).tables[1].rows[0]['lot_id'] == 'C'
    grouped = rank(source, group_by=['lot_id'])
    assert len(grouped.tables[1].rows) == 4
    source.tables[0].rows[0]['yield_percent'] = None
    source.tables[0].rows[1]['yield_percent'] = 'bad'
    source.scope['table_versions']['wafers'] = version(source.tables[0].rows)
    result = rank(source)
    assert result.scope['excluded_null_count'] == 2 and not result.tables[0].complete
    assert len(result.tables[0].rows) == 4
    assert all(r['selected'] is False and r['rank'] is None for r in result.tables[0].rows[-2:])
    assert result.source_result_ids == ['source']
    assert result.scope['source_version'] == source.scope['table_versions']['wafers']
    assert all('payload_hash' in r for r in result.tables[1].rows)
    check_versions(result)


def test_handles_reject_wrong_version_contract_and_owner(monkeypatch):
    source = select(monkeypatch, [record()])
    h = handle(source)
    with pytest.raises(ValueError, match='version'):
        rank(source, source={**h, 'version': 'wrong'})
    assert rank(source, source={**h, 'version': None}).scope['source_version'] == h['version']
    ctx = context(source)
    ctx.run['principal_id'] = 'other'
    with pytest.raises(PermissionError):
        asyncio.run(module().rank_members(module().RankMembersInput(source=h, order_by='yield_percent', direction='asc', top_n=1), ctx))
    source.scope.pop('handle_contract')
    with pytest.raises(ValueError, match='contract'):
        rank(source)


@pytest.fixture
def inline(monkeypatch):
    async def blocking(fn, *a, **kw):
        return fn(*a, **kw)
    monkeypatch.setattr(module(), 'run_blocking', blocking)


def render(result, **changes):
    values = dict(source=handle(result, result.tables[0].table_id), view='pass_fail')
    values.update(changes)
    return asyncio.run(module().render_maps(module().RenderMapsInput(**values), context(result)))


@pytest.mark.parametrize('view', ['pass_fail', 'bin_value'])
def test_render_png_per_wafer(monkeypatch, inline, view):
    records = [record(), record('B')]
    source = select(monkeypatch, records)
    monkeypatch.setattr(module(), '_fetch_maps_by_ref', lambda rows: records)
    if view == 'pass_fail':
        with pytest.raises(ValueError, match='symbol_bin_parameters'):
            render(source, view=view)
        return
    result = render(source, view=view)
    assert len(result.artifacts) == 2
    assert all(a['data'].startswith(b'\x89PNG') for a in result.artifacts)
    assert all(r['yield_match'] for r in result.tables[0].rows)
    assert 'A.01' in result.artifacts[0]['title']
    assert result.source_result_ids == ['source']
    check_versions(result)


def test_render_symbol_parameters(monkeypatch, inline):
    monkeypatch.setattr(module(), 'load_symbol_patterns', lambda *a: {'left': 'B.', 'right': '.B'})
    records = [record(pairs=['BA', 'AB', 'BB', 'AA'])]
    source = select(monkeypatch, records, pass_bin=None, bin_side=None, symbol_bin_parameters=['left', 'right'])
    monkeypatch.setattr(module(), '_fetch_maps_by_ref', lambda rows: records)
    result = render(source)
    assert result.tables[0].rows[0]['yield_match']
    assert result.tables[0].rows[0]['recomputed_yield_percent'] == 25
    assert [r['fail_die_count'] for r in result.tables[1].rows] == [2, 2]
    assert [r['yield_percent'] for r in result.tables[1].rows] == [50, 50]


def test_render_rejections(monkeypatch, inline):
    records = [record(), record('B')]
    source = select(monkeypatch, records)
    with pytest.raises(ValueError, match='select/rank'):
        render(source, max_wafers=1)
    monkeypatch.setattr(module(), '_fetch_maps_by_ref', lambda rows: [record(pairs=['BB']), records[1]])
    with pytest.raises(ValueError, match='hash'):
        render(source, view='bin_value')
    source.tables[0].rows[0].pop('time_value')
    source.scope['table_versions']['wafers'] = version(source.tables[0].rows)
    with pytest.raises(ValueError, match='time_value'):
        render(source, view='bin_value')


class Cursor:
    description = [(n,) for n in ('lot_id', 'wf_id', 'lot_cd', 'process', 'start_tm', 'end_tm', 'map_val_json')]

    def __init__(self, values):
        self.values = values
        self.calls = []
        self.closed = False

    def execute(self, sql, binds):
        self.calls.append((sql, binds))

    def fetchmany(self, n):
        self.fetch_limit = n
        return self.values[:n]

    def __iter__(self):
        return iter(self.values)

    def close(self):
        self.closed = True


def mock_connection(monkeypatch, cursor):
    import sys
    connection = SimpleNamespace(cursor=lambda: cursor, closed=False)
    def close():
        connection.closed = True
    connection.close = close
    monkeypatch.setitem(sys.modules, 'map_agent', SimpleNamespace(
        _get_oracle_connection_common=lambda: connection, ORACLE_TABLE='TEST_MEASUREMENTS'))
    return connection


@pytest.mark.parametrize('process', ['PT1H', 'PT1H TEST'])
@pytest.mark.parametrize('column', ['end_tm', 'start_tm'])
def test_measurement_sql_bindings_clob_and_cleanup(monkeypatch, process, column):
    raw = record()
    clob = SimpleNamespace(read=lambda: raw['map_val_json'])
    cur = Cursor([tuple(raw[k] if k != 'map_val_json' else clob for k, in Cursor.description)])
    connection = mock_connection(monkeypatch, cur)
    result = module()._fetch_measurements(args(lot_cd=["P' OR 1=1"], process=process, time_column=column, max_rows=2))
    sql, binds = cur.calls[0]
    assert sql.lstrip().startswith('SELECT')
    assert "P' OR 1=1" not in sql and binds['lot_0'] == "P' OR 1=1"
    assert f'{column} >= :time_from AND {column} < :time_to' in sql
    assert f'ORDER BY lot_id, wf_id, {column}' in sql
    assert ('||' in sql) == (' ' not in process)
    assert cur.fetch_limit == 3 and binds['row_limit'] == 3
    assert result[0]['map_val_json'] == raw['map_val_json']
    assert cur.closed and connection.closed


def test_ref_sql_hash_and_cleanup(monkeypatch):
    source = select(monkeypatch, [record()])
    payload = record()['map_val_json']
    cur = Cursor([(SimpleNamespace(read=lambda: payload),)])
    connection = mock_connection(monkeypatch, cur)
    fetched = module()._fetch_maps_by_ref(source.tables[0].rows)
    sql, binds = cur.calls[0]
    assert "TO_TIMESTAMP(:t, 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert binds == {'lot': 'A', 'wf': 1, 'process': 'PT1H TEST', 't': '2026-01-01 00:00:00'}
    assert fetched[0]['map_val_json'] == payload and cur.closed and connection.closed
    cur.values = []
    with pytest.raises(ValueError, match='hash'):
        module()._fetch_maps_by_ref(source.tables[0].rows)
    assert cur.closed and connection.closed


def test_query_errors_are_not_empty_results(monkeypatch):
    cur = Cursor([])
    connection = mock_connection(monkeypatch, cur)
    def fail(*a):
        raise RuntimeError('database unavailable')
    cur.execute = fail
    with pytest.raises(RuntimeError, match='database unavailable'):
        module()._fetch_measurements(args())
    assert connection.closed and cur.closed
    with pytest.raises(RuntimeError, match='database unavailable'):
        module()._fetch_maps_by_ref([dict(time_column='start_tm', lot_id='A', wf_id=1,
                                       process='PT1H TEST', time_value='2026-01-01 00:00:00')])


def test_pipeline_preserves_definitions_and_order(monkeypatch, inline):
    records = [record('A', pairs=['AA']), record('B', pairs=['BB']), record('C')]
    source = select(monkeypatch, records)
    ranked = rank(source, top_n=2)
    original = json.dumps(source.tables[0].rows)
    def fetch(rows):
        assert [r['lot_id'] for r in rows] == ['B', 'C']
        return [records[1], records[2]]
    monkeypatch.setattr(module(), '_fetch_maps_by_ref', fetch)
    result = render(ranked, source=handle(ranked, 'selected'), view='bin_value')
    assert [r['lot_id'] for r in result.tables[0].rows] == ['B', 'C']
    assert all(r['yield_match'] for r in result.tables[0].rows)
    assert json.dumps(source.tables[0].rows) == original
    assert len(result.artifacts) == 2


def test_ranking_preserves_incomplete_source(monkeypatch):
    source = select(monkeypatch, [record(), record('B')], max_rows=1)
    result = rank(source)
    assert not result.tables[1].complete and result.status == 'partial'
    assert 'max_rows' in result.scope['missing_reason']


def test_renderer_reports_metric_mismatch(monkeypatch, inline):
    source = select(monkeypatch, [record()])
    source.tables[0].rows[0]['yield_percent'] = 0
    source.scope['table_versions']['wafers'] = version(source.tables[0].rows)
    monkeypatch.setattr(module(), '_fetch_maps_by_ref', lambda rows: [record()])
    assert not render(source, view='bin_value').tables[0].rows[0]['yield_match']


def test_latest_never_uses_incomplete_boundary_wafer(monkeypatch):
    result = select(monkeypatch, [record(day=1), record(day=2)], max_rows=1)
    assert result.tables[0].rows == [] and result.status == 'partial'
    assert 'boundary' in result.scope['missing_reason']
    result = select(monkeypatch, [record('A'), record('B', day=1), record('B', day=2)], max_rows=2)
    assert [r['lot_id'] for r in result.tables[0].rows] == ['A']
    # An all-measurements request may retain the bounded measurement prefix.
    assert len(select(monkeypatch, [record(day=1), record(day=2)], max_rows=1,
                      measurement_policy='all').tables[0].rows) == 1


def test_reference_query_uses_second_bucket_and_payload_hash(monkeypatch):
    raw = record(end_tm=datetime(2026, 1, 1, 0, 0, 0, 123456))
    source = select(monkeypatch, [raw])
    assert source.tables[0].rows[0]['time_value'] == '2026-01-01 00:00:00'
    # Different payloads within that second must not substitute for this map.
    cur = Cursor([(record(pairs=['BB'])['map_val_json'],), (raw['map_val_json'],)])
    mock_connection(monkeypatch, cur)
    assert module()._fetch_maps_by_ref(source.tables[0].rows)[0]['map_val_json'] == raw['map_val_json']
    sql = cur.calls[0][0]
    assert "end_tm >= TO_TIMESTAMP(:t, 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert "end_tm < TO_TIMESTAMP(:t, 'YYYY-MM-DD HH24:MI:SS') + INTERVAL '1' SECOND" in sql
