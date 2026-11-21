"""Public report contract with captured DB-shaped evidence, never a live DB."""
import asyncio
import copy
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from harness.tools import table_artifact as tool
from harness.tools.registry import ToolRegistry
from harness.tools.yield_report_data import PeriodReportData


class MemoryStore:
    def __init__(self):
        self.saved = {}
        self.reads = []

    async def put_result(self, principal, session, observation, rows, artifacts, *, tables):
        identity = str(len(self.saved) + 1)
        refs = [SimpleNamespace(table_id=t.table_id, columns=t.columns, complete=t.complete,
                                total_rows=len(t.rows)) for t in tables]
        obs = SimpleNamespace(**observation, result_id=identity, principal_id=principal,
                              session_id=session, tables=refs, artifact_refs=artifacts)
        self.saved[identity] = (obs, {t.table_id: copy.deepcopy(t.rows) for t in tables})
        return obs

    async def observation(self, principal, session, identity):
        obs = self.saved[identity][0]
        if (obs.principal_id, obs.session_id) != (principal, session):
            raise PermissionError('Result unavailable')
        return obs

    async def rows(self, principal, session, identity, table_id=None):
        await self.observation(principal, session, identity)
        self.reads.append((identity, table_id))
        return copy.deepcopy(self.saved[identity][1][table_id])

    def membership(self, rows, *, tables=None, complete=True):
        identity = 'members'
        tables = tables or {'group_members': rows}
        refs = [SimpleNamespace(table_id=k, columns=list(v[0]) if v else ['group_id', 'lot_id'],
                                complete=complete, total_rows=len(v)) for k, v in tables.items()]
        self.saved[identity] = (SimpleNamespace(result_id=identity, principal_id='p', session_id='s',
            run_id='run', status='success', tool_name='run_python', tables=refs, scope={},
            source_result_ids=[], provenance={'data_origin': 'live'}), copy.deepcopy(tables))
        return identity


@pytest.fixture
def population_data():
    weeks = ['2026-46', '2026-47', '2026-48', '2026-49']
    population, stats, members = [], [], []
    for i in range(57):
        lot = 'L' + str(i) if i != 56 else 'L55A'  # split lot counted separately
        wafers = (17 if i < 13 else 16) if i < 14 else (20 if i < 56 else 23)
        score = .60 + i / 1000 if i < 14 else .732337 if i == 14 else .80
        population.append(dict(lot_id=lot, wafer_count=wafers, pt1h_mean=score, pt1h_value_count=wafers))
        for grain, bucket in [('all', ''), ('week', weeks[i % 4])]:
            for param in ['PT1H', 'VTH']:
                stats.append(dict(grain=grain, bucket=bucket, lot_id=lot, process='PT1H TEST',
                                  param=param, value_sum=score*wafers, value_count=wafers))
            for wf in range(wafers):
                members.append(dict(grain=grain, bucket=bucket, lot_id=lot, gorupkey=f'{lot}.{wf:02}', wf_id=str(wf)))
    counts = []
    yields = []
    for week in weeks:
        ws = [r for r in members if r['grain'] == 'week' and r['bucket'] == week]
        counts.append(dict(week=week, lot_count=len({r['lot_id'] for r in ws}),
                           wafer_count=len({r['gorupkey'] for r in ws}), has_yield_data=True, has_gms=True))
        for param in ['PT1H', 'VTH']:
            ss = [r for r in stats if r['grain'] == 'week' and r['bucket'] == week and r['param'] == param]
            yields.append(dict(grain='week', bucket=week, week=week, process='PT1H TEST', param=param,
                               value=sum(r['value_sum'] for r in ss)/sum(r['value_count'] for r in ss)))
    return PeriodReportData(lot_cd='4SS', window=dict(start='2026-11-09', end='2026-11-30', weeks=weeks,
        base_date='2026-11-30', selection='closed date range'),
        metadata=[('PT1H', 'PT1H', 1, 'B', 'o'), ('PT1H', 'VTH', 2, 'I', 'o')],
        yield_long=yields, counts={'week': counts}, period_lot_count=57, period_wafer_count=1100,
        gms={'week': [dict(week=w, period_date=w.replace('-', ''), fab=90, prb=80, cum0=70) for w in weeks]},
        lot_population=population, lot_parameter_stats=stats, lot_wafer_members=members,
        wafer_parameter_stats=[dict(grain=m['grain'], bucket=m['bucket'], lot_id=m['lot_id'], wf_id=m['wf_id'],
            process=s['process'], param=s['param'], value_sum=s['value_sum']/s['value_count'], value_count=1)
            for m in members for s in stats if (s['grain'], s['bucket'], s['lot_id']) ==
            (m['grain'], m['bucket'], m['lot_id'])])


@pytest.fixture
def setup_report(monkeypatch, population_data):
    calls = []
    def fetch(lot, period):
        calls.append((lot, period))
        return copy.deepcopy(population_data)
    monkeypatch.setattr(tool, 'fetch_period_report', fetch)
    store = MemoryStore()
    ctx = SimpleNamespace(store=store, run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's'}, invocation_id='test')
    registry = ToolRegistry()
    tool.register(registry)
    def call(**args):
        return asyncio.run(registry.invoke('render_yield_table', args, ctx))
    return call, store, calls, ctx


def inline(*groups):
    return {'membership': {'kind': 'inline', 'groups': [
        {'group_id': name, 'lot_ids': ids} for name, ids in groups]}}


def table_rows(result, name):
    return next(t.rows for t in result.tables if t.table_id == name)


def test_title_only_rejected_before_db(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")
    monkeypatch.setattr(tool, "_fetch_parameter_metadata", forbidden)
    monkeypatch.setattr(tool, "fetch_period_report", forbidden)
    registry = ToolRegistry()
    tool.register(registry)
    result = asyncio.run(registry.invoke('render_yield_table', {'title': 'report'}, None))
    assert result.status == 'error'
    assert 'legal_shapes' in result.scope
    assert 'lot_cd' in str(result.scope) and 'report_scope_id' in str(result.scope)


def test_public_schema_has_no_report_values():
    properties = tool.YieldTableInput.model_json_schema()['properties']
    assert not {'rows', 'periods', 'product_label', 'period_label', 'long_result_id', 'long_columns',
                'counts_result_id', 'counts_columns', 'gms_result_id', 'gms_columns', 'parameter_formats'} & properties.keys()
    for args in ({'title': 'x'}, {'lot_cd': '4SS'}, {'report_scope_id': 'x', 'lot_cd': '4SS'},
                 {'lot_cd': '4SS', 'period': {'kind': 'recent', 'weeks': 4}, 'layout': 'group'}):
        with pytest.raises(ValidationError):
            tool.YieldTableInput(**args)


def test_approved_split_and_reuse(setup_report, population_data):
    call, store, calls, _ = setup_report
    period = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    assert period.status == 'success'
    assert len(table_rows(period, 'lot_population')) == 57
    projections = period.scope['table_projections']
    assert projections['lot_population'] == {'group_by': ['lot_id'], 'aggregations': {'pt1h_mean': 'mean'},
                                            'units': {'pt1h_mean': 'fraction'}, 'preview_policy': 'read_result'}
    captured, _ = store.saved[period.scope['lot_population']['source_result_id']]
    assert captured.scope['table_projections'] == projections
    scores = [r['pt1h_mean'] for r in population_data.lot_population]
    q1 = np.quantile(scores, .25, method='linear')
    assert q1 == .732337
    membership = [dict(group_id='LOW' if row['pt1h_mean'] < q1 else 'REST', lot_id=row['lot_id'])
                  for row in population_data.lot_population]
    assert next(r['group_id'] for r in membership if r['lot_id'] == 'L14') == 'REST'
    rid = store.membership(membership)
    result = call(report_scope_id=period.scope['report_scope_id'], grouping={
        'membership': {'kind': 'table', 'result_id': rid}, 'coverage': 'partition',
        'definition': 'strictly below linear Q1'}, comparison={'current': 'LOW', 'baseline': 'REST'})
    assert result.status == 'success', result.summary
    assert len(calls) == 1
    counts = {r['period']: r for r in table_rows(result, 'group_counts')}
    assert (counts['LOW']['lot_count'], counts['LOW']['wf_count']) == (14, 237)
    assert (counts['REST']['lot_count'], counts['REST']['wf_count']) == (43, 863)
    cells = table_rows(result, 'displayed_yield_table')
    assert all(r['shown'] == '-' for r in cells if r['column'].startswith('GMS/'))
    assert 'not_available_at_lot_group_granularity' in str(result.scope)
    assert table_rows(result, 'group_membership') == membership
    assert result.scope['membership']['table_id'] == 'group_members'
    assert '<tr class="delta">' in result.artifacts[0]['data']
    low_cell = next(r for r in cells if r['row'] == 'LOW' and r['column'] == 'PT1H TEST/PT1H')
    low = population_data.lot_population[:14]
    assert low_cell['raw'] == pytest.approx(sum(r['pt1h_mean']*r['wafer_count'] for r in low)/237)


def test_subset_empty_group_and_group_period(setup_report):
    call, _, _, _ = setup_report
    grouping = inline(('LOW', ['L0']))
    grouping['groups'] = [{'id': 'LOW', 'label': '<low>'}, {'id': 'EMPTY'}]
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4}, grouping=grouping, layout='group_period',
                  comparison={'current': 'LOW', 'baseline': 'EMPTY'})
    assert result.status == 'partial'
    assert result.scope['uncovered_lot_count'] == 56
    counts = table_rows(result, 'group_counts')
    assert len(counts) == 8
    assert all(r['lot_count'] == 0 for r in counts if r['period'] == 'EMPTY')
    assert {r['bucket'] for r in counts} == {'2026-46', '2026-47', '2026-48', '2026-49'}
    assert '&lt;low&gt;' in result.artifacts[0]['data']
    assert result.artifacts[0]['data'].count('<tr class="delta">') == 4


@pytest.mark.parametrize('grouping,match', [
    (inline(('LOW', ['unknown'])), 'unknown'),
    (inline(('LOW', ['L0', 'L0'])), 'duplicate'),
    (inline(('LOW', ['L0']), ('REST', ['L0'])), 'overlap'),
    ({**inline(('LOW', ['L0'])), 'coverage': 'partition'}, 'partition'),
])
def test_invalid_membership(setup_report, grouping, match):
    call, _, _, _ = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4}, grouping=grouping)
    assert result.status == 'error'
    assert match in result.summary.lower()


def test_table_selection_full_read_and_extras(setup_report):
    call, store, _, _ = setup_report
    rows = [dict(group_id='LOW', lot_id='L0', note='ignored')]
    rid = store.membership(rows, tables={'members': rows, 'stats': [{'threshold': .73}]})
    args = dict(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                grouping={'membership': {'kind': 'table', 'result_id': rid}})
    result = call(**args)
    assert result.status == 'error'
    assert 'members' in result.summary and 'stats' in result.summary and 'table_id' in result.summary
    args['grouping']['membership']['table_id'] = 'members'
    result = call(**args)
    assert result.status == 'success'
    assert table_rows(result, 'group_membership') == [{'group_id': 'LOW', 'lot_id': 'L0'}]
    assert ('members', 'members') in store.reads


def test_incomplete_membership_rejected(setup_report):
    call, store, _, _ = setup_report
    rid = store.membership([dict(group_id='LOW', lot_id='L0')], complete=False)
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                  grouping={'membership': {'kind': 'table', 'result_id': rid}})
    assert result.status == 'error' and 'complete' in result.summary.lower()


def test_scope_is_owned_and_cannot_be_python_forged(setup_report):
    call, store, _, ctx = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    identity = result.scope['report_scope_id']
    ctx.run['session_id'] = 'other'
    assert call(report_scope_id=identity).status == 'error'
    ctx.run['session_id'] = 's'
    rid = store.membership([])
    assert call(report_scope_id='yield-report:' + rid).status == 'error'


def test_null_scores_remain_in_official_population(setup_report, population_data, monkeypatch):
    call, _, _, _ = setup_report
    population_data.lot_population[0].update(pt1h_mean=None, pt1h_value_count=0)
    monkeypatch.setattr(tool, 'fetch_period_report', lambda *args: population_data)
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    rows = table_rows(result, 'lot_population')
    assert len(rows) == 57 and rows[0]['pt1h_mean'] is None and rows[0]['pt1h_value_count'] == 0


def test_membership_over_one_thousand_rows_is_read_completely(setup_report, population_data, monkeypatch):
    call, store, _, _ = setup_report
    population_data.lot_population[:] = [dict(lot_id=f'W{i}', wafer_count=1, pt1h_mean=.8, pt1h_value_count=1)
                                      for i in range(1201)]
    population_data.lot_parameter_stats[:] = [dict(grain='all', bucket='', lot_id=r['lot_id'],
        process='PT1H TEST', param='PT1H', value_sum=.8, value_count=1) for r in population_data.lot_population]
    population_data.lot_wafer_members[:] = [dict(grain='all', bucket='', lot_id=r['lot_id'], gorupkey=r['lot_id']+'.01')
                                       for r in population_data.lot_population]
    monkeypatch.setattr(tool, 'fetch_period_report', lambda *args: population_data)
    rows = [dict(group_id='ALL', lot_id=r['lot_id'], additional_column='ignored') for r in population_data.lot_population]
    rid = store.membership(rows)
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                  grouping={'membership': {'kind': 'table', 'result_id': rid}, 'coverage': 'partition'})
    assert result.status == 'success'
    assert len(table_rows(result, 'group_membership')) == 1201
    assert table_rows(result, 'group_counts')[0]['lot_count'] == 1201
    assert table_rows(result, 'group_counts')[0]['wf_count'] == 1201
    assert len(str(result.scope)) < 6000  # identifiers belong in evidence, not scope preview


def test_period_scope_reuse_preserves_numbers_and_metadata(setup_report):
    call, _, calls, _ = setup_report
    original = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    repeated = call(report_scope_id=original.scope['report_scope_id'])
    assert repeated.status == 'success'
    assert len(calls) == 1
    assert repeated.scope['window'] == original.scope['window']
    assert repeated.scope['period'] == original.scope['period']
    assert table_rows(repeated, 'displayed_yield_table') == table_rows(original, 'displayed_yield_table')
    assert table_rows(repeated, 'column_metadata') == table_rows(original, 'column_metadata')


def test_declared_group_order_and_table_lineage(setup_report):
    call, store, _, _ = setup_report
    rid = store.membership([dict(group_id='LOW', lot_id='L0'), dict(group_id='REST', lot_id='L1')])
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4}, grouping={
        'membership': {'kind': 'table', 'result_id': rid},
        'groups': [{'id': 'REST', 'label': 'rest first'}, {'id': 'LOW', 'label': 'low second'}]})
    assert result.status == 'success'
    assert [r['period'] for r in table_rows(result, 'group_counts')] == ['REST', 'LOW']
    assert result.artifacts[0]['data'].index('rest first</td>') < result.artifacts[0]['data'].index('low second</td>')
    assert rid in result.source_result_ids


def test_five_changing_failures_stop_report_calls_and_keep_success(setup_report):
    call, _, calls, _ = setup_report
    success = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    for shape in ({'title': 'only'}, {'long_columns': {}}, {'report_scope_id': 'invalid'},
                  {'layout': 'group'}, {'lot_cd': '4SS'}):
        failure = call(**shape)
    assert failure.scope['code'] == 'report_budget_exhausted'
    assert failure.scope['count'] == 5
    assert success.scope['report_scope_id'] in str(failure.scope['successful_reports'])
    blocked = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    assert blocked.status == 'error' and not blocked.scope['retryable']
    assert len(calls) == 1


@pytest.mark.parametrize('group_id,lot_id', [('', 'L0'), (' ', 'L0'), (23, 'L0'), ('LOW', None), ('LOW', [])])
def test_malformed_table_membership_is_a_budgeted_contract_error(setup_report, group_id, lot_id):
    call, store, _, _ = setup_report
    rid = store.membership([dict(group_id=group_id, lot_id=lot_id)])
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                  grouping={'membership': {'kind': 'table', 'result_id': rid}})
    assert result.status == 'error'
    assert result.scope['code'] == 'INVALID_MEMBERSHIP'
    assert result.scope['count'] == 1


@pytest.mark.parametrize('field,value', [('run_id', 'other'), ('epoch', 2), ('runtime_id', 'other-runtime'), ('runtime_version', 'other-version')])
def test_scope_cannot_cross_run_epoch_or_runtime(setup_report, field, value):
    call, _, calls, ctx = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    ctx.run[field] = value
    rejected = call(report_scope_id=result.scope['report_scope_id'])
    assert rejected.status == 'error'
    assert 'another run' in rejected.summary
    assert len(calls) == 1


def test_undeclared_empty_inline_group_is_not_silently_discarded(setup_report):
    call, _, _, _ = setup_report
    grouping = inline(('LOW', ['L0']), ('UNDECLARED', []))
    grouping['groups'] = [{'id': 'LOW'}]
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4}, grouping=grouping)
    assert result.status == 'error' and result.scope['code'] == 'UNDECLARED_GROUP'


@pytest.mark.parametrize('layout', ['group', 'group_period'])
def test_lot_output_matches_pre_wafer_capture(setup_report, population_data, layout):
    import json
    from pathlib import Path
    expected = json.loads(Path(__file__).with_name('lot_membership_baseline.json').read_text())[layout]
    call, _, _, _ = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4}, layout=layout,
        grouping=inline(('LOW', [r['lot_id'] for r in population_data.lot_population[:14]]),
                        ('REST', [r['lot_id'] for r in population_data.lot_population[14:]])),
        comparison={'current': 'LOW', 'baseline': 'REST'})
    assert {'status': result.status, **{name: table_rows(result, name) for name in expected if name != 'status'}} == expected


def test_wafer_table_contract():
    source = tool.TableMembership(kind='table', result_id='members', member_grain='wafer')
    assert source.member_grain == 'wafer'


@pytest.fixture
def wafer_report(setup_report, population_data, monkeypatch):
    from dataclasses import replace
    data = replace(population_data, wafer_parameter_stats=[], lot_wafer_members=[])
    data.metadata[:] = [('PT1H', 'PT1H', 1, 'B', 'o'), ('PT1C', 'COLD', 1, 'B', 'o')]
    for grain, bucket in [('all', ''), ('week', '2026-46')]:
        for wf, process, param, value, count in [
            ('7', 'PT1H TEST', 'PT1H', .8, 1), ('8', 'PT1H TEST', 'PT1H', .6, 1),
            ('7', 'PT1C TEST', 'COLD', .4, 1), ('9', 'PT1C TEST', 'COLD', 1.8, 2)]:
            data.wafer_parameter_stats.append(dict(grain=grain, bucket=bucket, lot_id='L0', wf_id=wf,
                process=process, param=param, value_sum=value, value_count=count))
        data.lot_wafer_members.extend(dict(grain=grain, bucket=bucket, lot_id='L0', wf_id=wf,
            gorupkey='L0.'+wf) for wf in ['7', '8'])
    def fetch(lot, period):
        setup_report[2].append((lot, period))
        return copy.deepcopy(data)
    monkeypatch.setattr(tool, 'fetch_period_report', fetch)
    return setup_report


def wafer_call(setup, rows, **kwargs):
    call, store, _, _ = setup
    rid = store.membership(rows)
    grain = kwargs.pop('member_grain', 'wafer')
    coverage = kwargs.pop('coverage', 'disjoint_subsets')
    source = kwargs.pop('report_scope_id', None)
    return call(**({'report_scope_id': source} if source else {'lot_cd': '4SS', 'period': {'kind': 'recent', 'weeks': 4}}),
        grouping={'membership': {'kind': 'table', 'result_id': rid, 'member_grain': grain}, 'coverage': coverage}, **kwargs)


def test_wafer_split_independent_columns_and_reuse(wafer_report):
    rows = [dict(group_id=g, lot_id='L0', wf_id=w) for g, w in [('A', '7'), ('B', '8'), ('A', '9')]]
    result = wafer_call(wafer_report, rows, coverage='partition', comparison={'current':'A', 'baseline':'B'})
    assert result.status == 'success', result.summary
    assert result.scope['member_grain'] == 'wafer'
    assert result.scope['uncovered_lot_count'] == 56
    assert result.scope['uncovered_wafer_count'] == 0
    assert table_rows(result, 'group_membership') == rows
    values = {(r['period'],r['process']):r['value'] for r in table_rows(result,'group_yield_long')}
    assert values == {('A','PT1H TEST'):.8, ('A','PT1C TEST'):2.2/3, ('B','PT1H TEST'):.6}
    assert [(r['lot_count'],r['wf_count']) for r in table_rows(result,'group_counts')] == [(1,1),(1,1)]
    reused = wafer_call(wafer_report, rows, report_scope_id=result.scope['report_scope_id'])
    assert table_rows(reused, 'group_yield_long') == table_rows(result, 'group_yield_long')
    assert len(wafer_report[2]) == 1


def test_pt1c_only_wafer_and_bucket_absence(wafer_report):
    rows = [dict(group_id='C', lot_id='L0', wf_id='9')]
    result = wafer_call(wafer_report, rows)
    assert result.status == 'success', result.summary
    assert table_rows(result,'group_counts')[0]['wf_count'] == 0
    period = wafer_call(wafer_report, rows, layout='group_period')
    assert period.status == 'partial'
    counts = table_rows(period, 'group_counts')
    assert [r['has_yield_data'] for r in counts] == [True, False, False, False]
    assert period.scope['uncovered_wafer_count'] == 2


@pytest.mark.parametrize('wf,code', [(7,'INVALID_MEMBERSHIP'), ('','INVALID_MEMBERSHIP'),
    (None,'INVALID_MEMBERSHIP'), (' ','INVALID_MEMBERSHIP'), (' 7','INVALID_MEMBERSHIP'), ('07','UNKNOWN_WAFER'), ('99','UNKNOWN_WAFER')])
def test_invalid_wafer_identity(wafer_report, wf, code):
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id=wf)])
    assert result.scope['code'] == code


@pytest.mark.parametrize('group', ['A', 'B'])
def test_duplicate_wafer_rejected(wafer_report, group):
    result = wafer_call(wafer_report, [dict(group_id=g,lot_id='L0',wf_id='7') for g in ['A',group]])
    assert result.scope['code'] == 'INVALID_MEMBERSHIP'


def test_wafer_partition_requires_all_keys(wafer_report):
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id='7')], coverage='partition')
    assert result.scope['code'] == 'INCOMPLETE_PARTITION'


@pytest.mark.parametrize('wf', ['7', 7, 0])
def test_lot_rejects_stray_wafer(wafer_report, wf):
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id=wf)], member_grain='lot')
    assert result.scope['code'] == 'MEMBERSHIP_GRAIN_MISMATCH'


@pytest.mark.parametrize('wf', [None, '', ' '])
def test_lot_allows_empty_wafer(wafer_report, wf):
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id=wf)], member_grain='lot')
    assert result.status == 'success'
    assert table_rows(result, 'group_membership') == [dict(group_id='A',lot_id='L0')]


def test_old_scope_lot_works_wafer_requires_recapture(wafer_report):
    call, store, calls, _ = wafer_report
    original = call(lot_cd='4SS',period={'kind':'recent','weeks':4})
    scope_id = original.scope['report_scope_id']
    obs, tables = store.saved[scope_id.removeprefix('yield-report:')]
    legacy_wafer_scope(store, scope_id, wafer_report[3])
    obs.scope.pop('wafer_capture', None)
    obs.tables = [t for t in obs.tables if t.table_id != 'wafer_parameter_stats']
    tables.pop('wafer_parameter_stats', None)
    members = next(t for t in obs.tables if t.table_id == 'lot_wafer_members')
    members.columns = [c for c in members.columns if c != 'wf_id']
    for row in tables['lot_wafer_members']:
        row.pop('wf_id')
    lot = call(report_scope_id=scope_id, grouping=inline(('A',['L0'])))
    assert lot.status == 'success'
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id='7')], report_scope_id=scope_id)
    assert result.scope['code'] == 'WAFER_CAPTURE_REQUIRED'
    assert len(calls) == 1


@pytest.mark.parametrize('encoding', ['legacy', 'packed-v1'])
@pytest.mark.parametrize('damage', ['columns', 'complete', 'count', 'missing', 'member_columns'])
def test_wafer_scope_requires_complete_capture(wafer_report, damage, encoding):
    call, store, calls, _ = wafer_report
    original = call(lot_cd='4SS', period={'kind':'recent','weeks':4})
    scope_id = original.scope['report_scope_id']
    obs, _ = store.saved[scope_id.removeprefix('yield-report:')]
    assert obs.scope['contract'] == 'yield-report-scope/v1'
    assert obs.scope['wafer_capture'] is True
    ref = next(t for t in obs.tables if t.table_id == 'wafer_parameter_stats')
    if encoding == 'legacy':
        legacy_wafer_scope(store, scope_id, wafer_report[3])
        assert ref.columns == ['grain','bucket','lot_id','wf_id','process','param','value_sum','value_count']
    else:
        assert ref.columns == ['grain','bucket','lot_id','wf_id','process','values']
    if damage == 'columns':
        ref.columns = [c for c in ref.columns if c != 'wf_id']
    elif damage == 'complete':
        ref.complete = False
    elif damage == 'count':
        ref.total_rows += 1
    elif damage == 'member_columns':
        members = next(t for t in obs.tables if t.table_id == 'lot_wafer_members')
        members.columns = [c for c in members.columns if c != 'wf_id']
    else:
        obs.tables.remove(ref)
    result = call(report_scope_id=scope_id, grouping=inline(('A',['L0'])))
    assert result.scope['code'] == 'INCOMPLETE_REPORT_SCOPE'
    assert len(calls) == 1


def test_captured_empty_wafer_stats_is_not_old_scope(wafer_report):
    from harness.tools.yield_report_scope import load_scope
    call, store, _, ctx = wafer_report
    result = call(lot_cd='4SS',period={'kind':'recent','weeks':4})
    scope_id = result.scope['report_scope_id']
    obs, tables = store.saved[scope_id.removeprefix('yield-report:')]
    tables['wafer_parameter_stats'] = []
    next(t for t in obs.tables if t.table_id == 'wafer_parameter_stats').total_rows = 0
    data, _, _ = asyncio.run(load_scope(scope_id, ctx))
    assert data.wafer_parameter_stats == []
    # Identity-only wafers remain selectable even without displayed statistics.
    rendered = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id='7')], report_scope_id=scope_id)
    assert rendered.status == 'partial'


def test_wafer_requires_column_and_every_row(wafer_report):
    missing_column = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0')])
    assert missing_column.scope['code'] == 'MEMBERSHIP_COLUMNS'
    mixed = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id='7'), dict(group_id='B',lot_id='L0')])
    assert mixed.scope['code'] == 'INVALID_MEMBERSHIP'


@pytest.mark.parametrize('grain,key,buckets', [
    ('month','months',['2026-10','2026-11']), ('day','days',['2026-11-09','2026-11-10'])])
def test_wafer_group_period_uses_each_grain_slice(wafer_report, grain, key, buckets):
    call, store, _, _ = wafer_report
    original = call(lot_cd='4SS',period={'kind':'recent','weeks':4})
    scope_id = original.scope['report_scope_id']
    obs, tables = store.saved[scope_id.removeprefix('yield-report:')]
    legacy_wafer_scope(store, scope_id, wafer_report[3])
    obs.scope['window'] = {**obs.scope['window'], key: buckets}
    for name in ('wafer_parameter_stats','lot_wafer_members'):
        tables[name].extend(dict(row, grain=grain, bucket=buckets[0]) for row in list(tables[name]) if row['grain']=='all')
        next(t for t in obs.tables if t.table_id == name).total_rows = len(tables[name])
    result = wafer_call(wafer_report, [dict(group_id='A',lot_id='L0',wf_id='7'),dict(group_id='B',lot_id='L0',wf_id='8')],
        report_scope_id=scope_id, layout='group_period', comparison={'current':'A','baseline':'B'})
    counts = [r for r in table_rows(result,'group_counts') if r['grain']==grain]
    assert [(r['bucket'],r['lot_count'],r['wf_count']) for r in counts] == [
        (buckets[0],1,1),(buckets[0],1,1),(buckets[1],0,0),(buckets[1],0,0)]
    cells = [r for r in table_rows(result,'displayed_yield_table') if r['grain']==grain]
    assert any(r['raw'] == .8 for r in cells if r['bucket']==buckets[0])
    assert not any(r['raw'] == .8 for r in cells if r['bucket']==buckets[1])


def test_wafer_stats_can_select_lot_outside_pt1h_population(wafer_report):
    call, store, _, _ = wafer_report
    original = call(lot_cd='4SS',period={'kind':'recent','weeks':4})
    scope_id = original.scope['report_scope_id']
    _, tables = store.saved[scope_id.removeprefix('yield-report:')]
    for row in tables['wafer_parameter_stats']:
        if row['wf_id']=='9':
            row['lot_id']='PT1C_ONLY'
    result = wafer_call(wafer_report, [dict(group_id='C',lot_id='PT1C_ONLY',wf_id='9')], report_scope_id=scope_id)
    assert result.status == 'success'
    assert result.scope['uncovered_lot_count'] == 57
    assert table_rows(result,'group_counts')[0]['lot_count'] == 0


def test_packed_scope_round_trip_and_size(population_data, setup_report):
    import json
    from collections import Counter
    from dataclasses import replace
    from harness.tools.yield_report_scope import save_scope, load_scope
    _, store, _, ctx = setup_report
    rows = [dict(grain=g, bucket=b, lot_id=f'4SS_LOT_{i // 25:04}', wf_id=str(i % 25 + 1),
                 process='PT1H TEST', param=f'PARAM_{j:02}', value_sum=0.0 if j < 2 else .8123456789,
                 value_count=0 if j == 0 else 2 if j == 2 else 1)
            for g, b in [('all', ''), ('week', '2026-46')]
            for i in range(1100) for j in range(25) if not (i == 0 and j == 3)]
    data = replace(population_data, wafer_parameter_stats=rows)
    identity = asyncio.run(save_scope(data, {}, ctx))
    obs, tables = store.saved[identity.removeprefix('yield-report:')]
    assert obs.scope['wafer_capture_encoding'] == 'packed-v1'
    packed = tables['wafer_parameter_stats']
    assert len(packed) == 1100
    values = dict(zip(obs.scope['wafer_params']['PT1H TEST'], packed[0]['values']))
    assert [values[f'PARAM_{i:02}'] for i in range(4)] == [[0.0, 0], 0.0, [.8123456789, 2], None]
    size = len(json.dumps(packed).encode())
    print(f'packed wafer table: {size:,} bytes')
    assert size < 900_000
    assert all('row_order' not in row and 'all_order' not in row for row in packed)
    loaded, _, _ = asyncio.run(load_scope(identity, ctx))
    canonical = lambda rs: Counter(tuple(sorted(r.items())) for r in rs)
    assert canonical(loaded.wafer_parameter_stats) == canonical(rows)
    assert loaded.lot_parameter_stats == data.lot_parameter_stats


@pytest.mark.parametrize('layout', ['group', 'group_period'])
def test_packed_reuse_preserves_wafer_outputs(wafer_report, layout):
    rows = [dict(group_id='A', lot_id='L0', wf_id='7'), dict(group_id='B', lot_id='L0', wf_id='8')]
    first = wafer_call(wafer_report, rows, layout=layout)
    second = wafer_call(wafer_report, rows, layout=layout, report_scope_id=first.scope['report_scope_id'])
    assert first.status == second.status == ('partial' if layout == 'group_period' else 'success')
    for name in ('displayed_yield_table', 'group_counts'):
        assert table_rows(first, name) == table_rows(second, name)


def legacy_wafer_scope(store, scope_id, ctx):
    """Convert a captured scope into the pre-encoding dict storage fixture."""
    from harness.tools.yield_report_scope import load_scope, CAPTURE_COLUMNS
    data, _, _ = asyncio.run(load_scope(scope_id, ctx))
    obs, tables = store.saved[scope_id.removeprefix('yield-report:')]
    for key in ('wafer_capture_encoding', 'wafer_params', 'wafer_all_from_grain'):
        obs.scope.pop(key, None)
    tables['wafer_parameter_stats'] = data.wafer_parameter_stats
    ref = next(t for t in obs.tables if t.table_id == 'wafer_parameter_stats')
    ref.columns = CAPTURE_COLUMNS['wafer_parameter_stats'][:]
    ref.total_rows = len(data.wafer_parameter_stats)
    return data


def test_old_dict_scope_readable(wafer_report):
    from harness.tools.yield_report_scope import load_scope
    call, store, _, ctx = wafer_report
    first = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    identity = first.scope['report_scope_id']
    expected = legacy_wafer_scope(store, identity, ctx)
    actual, _, _ = asyncio.run(load_scope(identity, ctx))
    assert actual == expected
    result = wafer_call(wafer_report, [dict(group_id='A', lot_id='L0', wf_id='7')], report_scope_id=identity)
    assert result.status == 'success'


@pytest.mark.parametrize('damage', [
    'unknown', 'length', 'string', 'bool', 'negative_count', 'float_count', 'bad_pair',
    'null_sum', 'nan', 'duplicate_row', 'duplicate_param', 'missing_params', 'bad_identity', 'bad_grain',
    'null_encoding',
])
def test_packed_scope_rejects_corruption(wafer_report, damage):
    call, store, _, _ = wafer_report
    original = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    identity = original.scope['report_scope_id']
    obs, tables = store.saved[identity.removeprefix('yield-report:')]
    packed = tables['wafer_parameter_stats']
    if damage == 'unknown':
        obs.scope['wafer_capture_encoding'] = 'packed-v999'
    elif damage == 'null_encoding':
        obs.scope['wafer_capture_encoding'] = None
    elif damage == 'length':
        packed[0]['values'].append(1)
    elif damage == 'duplicate_row':
        packed.append(copy.deepcopy(packed[0]))
        next(t for t in obs.tables if t.table_id == 'wafer_parameter_stats').total_rows += 1
    elif damage == 'duplicate_param':
        obs.scope['wafer_params']['PT1H TEST'].append('PT1H')
    elif damage == 'missing_params':
        obs.scope.pop('wafer_params')
    elif damage == 'bad_identity':
        packed[0]['wf_id'] = 7
    elif damage == 'bad_grain':
        obs.scope['wafer_all_from_grain'] = []
    else:
        packed[0]['values'][0] = dict(string='0.8', bool=True, negative_count=[0, -1],
            float_count=[0, 1.5], bad_pair=[0], null_sum=[None, 0], nan=float('nan'))[damage]
    result = call(report_scope_id=identity)
    assert result.status == 'error'
    assert result.scope['code'] == 'INCOMPLETE_REPORT_SCOPE'


@pytest.mark.parametrize('case', ['no_buckets', 'null_identity', 'different_total'])
def test_packed_all_fallback_preserves_exact_captured_rows(population_data, setup_report, case):
    from collections import Counter
    from dataclasses import replace
    from harness.tools.yield_report_scope import save_scope, load_scope
    _, store, _, ctx = setup_report
    stats = [dict(grain=g, bucket=b, lot_id=None if case == 'null_identity' else 'L0', wf_id='007',
                  process='PT1H TEST', param='PT1H', value_sum=s, value_count=n)
             for g, b, s, n in [('all', '', .3, 2), ('week', '2026-46', .1, 1), ('week', '2026-47', .2, 1)]]
    if case == 'no_buckets':
        stats = stats[:1]
    data = replace(population_data, wafer_parameter_stats=stats,
                   window={} if case == 'no_buckets' else population_data.window)
    identity = asyncio.run(save_scope(data, {}, ctx))
    obs, tables = store.saved[identity.removeprefix('yield-report:')]
    assert obs.scope['wafer_all_from_grain'] is None
    assert any(r['grain'] == 'all' for r in tables['wafer_parameter_stats'])
    decoded, _, _ = asyncio.run(load_scope(identity, ctx))
    assert Counter(tuple(r.items()) for r in decoded.wafer_parameter_stats) == Counter(tuple(r.items()) for r in stats)


@pytest.mark.parametrize('period', [
    {'kind': 'recent', 'months': 2, 'grains': ['month', 'week', 'day']},
    {'kind': 'weeks', 'weeks': ['2026-43', '2026-46']},
])
def test_packed_rebuild_uses_one_grain_for_clipped_and_gapped_windows(population_data, setup_report, period):
    from collections import Counter
    from dataclasses import replace
    from datetime import date
    from harness.tools.yield_report_data import RecentPeriod, ExplicitWeeks, resolve_window, GRAINS
    from harness.tools.yield_report_scope import save_scope, load_scope
    _, store, _, ctx = setup_report
    request = RecentPeriod(**period) if period['kind'] == 'recent' else ExplicitWeeks(**period)
    window = resolve_window(request, date(2026, 11, 10) if period['kind'] == 'recent' else None)
    grains = [g for g, (key, _) in GRAINS.items() if key in window]
    # One test date per process; PT1C can occupy a different bucket than PT1H.
    stats = [dict(grain=g, bucket=b, lot_id='A', wf_id='007', process=process, param='P',
                  value_sum=value, value_count=count)
             for process, value, count, index in [('PT1H TEST', .75, 1, 0), ('PT1C TEST', 0.0, 0, -1)]
             for g, b in [('all', ''), *((g, window[GRAINS[g][0]][index]) for g in grains)]]
    data = replace(population_data, window=window, counts={g: [] for g in grains}, wafer_parameter_stats=stats,
                   metadata=[('PT1H', 'P', 1, 'P', 'o'), ('PT1C', 'P', 1, 'P', 'o')])
    identity = asyncio.run(save_scope(data, period, ctx))
    obs, tables = store.saved[identity.removeprefix('yield-report:')]
    assert obs.scope['wafer_all_from_grain'] == grains[0]
    assert all(r['grain'] != 'all' for r in tables['wafer_parameter_stats'])
    loaded, _, _ = asyncio.run(load_scope(identity, ctx))
    canonical = lambda rs: Counter(tuple(sorted(r.items())) for r in rs)
    assert canonical(loaded.wafer_parameter_stats) == canonical(stats)
    from harness.tools.yield_report_data import aggregate_groups
    membership = [dict(group_id='G', lot_id='A', wf_id='007')]
    before = aggregate_groups(data, membership, ['G'], member_grain='wafer')
    after = aggregate_groups(loaded, membership, ['G'], member_grain='wafer')
    assert before == after
    assert next(r for r in after['rows'] if r['process'] == 'PT1C TEST')['value'] is None
    assert after['counts'][0]['has_yield_data'] is True


def test_packed_reuse_has_identical_canonical_wafer_floats(population_data, setup_report):
    from dataclasses import replace
    from harness.tools.yield_report_scope import save_scope, load_scope
    from harness.tools.yield_report_data import aggregate_groups
    _, _, _, ctx = setup_report
    stats = []
    for grain, bucket, wafers in [('all', '', [('1', .1), ('2', .2), ('3', .3)]),
                                  ('week', '2026-46', [('3', .3), ('2', .2), ('1', .1)])]:
        for wf, value in wafers:
            stats.append(dict(grain=grain, bucket=bucket, lot_id='A', wf_id=wf,
                              process='PT1H TEST', param='PT1H', value_sum=value, value_count=1))
        # Parameter ordering can differ from the first parameter's wafer order.
        for wf, value in reversed(wafers):
            stats.append(dict(grain=grain, bucket=bucket, lot_id='A', wf_id=wf,
                              process='PT1H TEST', param='VTH', value_sum=value, value_count=1))
    data = replace(population_data, wafer_parameter_stats=stats)
    identity = asyncio.run(save_scope(data, {}, ctx))
    decoded, _, _ = asyncio.run(load_scope(identity, ctx))
    members = [dict(group_id='G', lot_id='A', wf_id=wf) for wf in ['1', '2', '3']]
    for grain, bucket in [('all', ''), ('week', '2026-46')]:
        assert aggregate_groups(data, members, ['G'], grain=grain, bucket=bucket, member_grain='wafer') == aggregate_groups(
            decoded, members, ['G'], grain=grain, bucket=bucket, member_grain='wafer')


def test_wafer_outputs_are_invariant_to_stat_row_permutations(population_data):
    from dataclasses import replace
    from itertools import permutations
    from harness.tools.yield_report_data import aggregate_groups
    stats = [dict(grain='all', bucket='', lot_id=lot, wf_id=wf, process='PT1H TEST',
                  param='PT1H', value_sum=value, value_count=1)
             for lot, wf, value in [('A', '1', .1), ('A', '2', .2), ('A', '3', .3), (None, '1', 0.), ('A', None, 0.)]]
    members = [dict(group_id='G', lot_id=r['lot_id'], wf_id=r['wf_id']) for r in stats]
    data = replace(population_data, wafer_parameter_stats=stats)
    expected = aggregate_groups(data, members, ['G'], member_grain='wafer')
    for permuted in permutations(stats):
        actual = aggregate_groups(replace(data, wafer_parameter_stats=list(permuted)),
                                  members, ['G'], member_grain='wafer')
        assert actual == expected


def test_packed_null_identity_multibucket_rebuild_is_exact(population_data, setup_report):
    from collections import Counter
    from dataclasses import replace
    from harness.tools.yield_report_scope import save_scope, load_scope
    _, store, _, ctx = setup_report
    stats = []
    for param, buckets in [('PT1H', [('2026-48', .3), ('2026-47', .2), ('2026-46', .1)]),
                           ('VTH', [('2026-46', .1), ('2026-47', .2), ('2026-48', .3)])]:
        for grain, bucket, value in [('all', '', .1 + .2 + .3),
                                     *(('week', bucket, value) for bucket, value in buckets)]:
            stats.append(dict(grain=grain, bucket=bucket, lot_id=None, wf_id='7',
                              process='PT1H TEST', param=param, value_sum=value,
                              value_count=3 if grain == 'all' else 1))
    data = replace(population_data, wafer_parameter_stats=stats)
    identity = asyncio.run(save_scope(data, {}, ctx))
    obs, _ = store.saved[identity.removeprefix('yield-report:')]
    assert obs.scope['wafer_all_from_grain'] == 'week'
    loaded, _, _ = asyncio.run(load_scope(identity, ctx))
    canonical = lambda rows: Counter(tuple(sorted(row.items())) for row in rows)
    assert canonical(loaded.wafer_parameter_stats) == canonical(stats)


@pytest.mark.parametrize('layout', ['group', 'group_period'])
@pytest.mark.parametrize('ordered', [False, True])
@pytest.mark.parametrize('explicit', [False, True])
def test_group_effective_comparison(setup_report, layout, ordered, explicit):
    call, _, _, _ = setup_report
    grouping = inline(('A', ['L0', 'L1', 'L2', 'L3']),
                      ('B', ['L4', 'L5', 'L6', 'L7']),
                      ('C', ['L8', 'L9', 'L10', 'L11']))
    if ordered:
        grouping['groups'] = [{'id': name} for name in ['C', 'A', 'B']]
    comparison = {'current': 'A', 'baseline': 'C'} if explicit else {
        'current': 'B' if ordered else 'C', 'baseline': 'C' if ordered else 'A'}
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                  layout=layout, grouping=grouping,
                  **({'comparison': comparison} if explicit else {}))
    assert result.status == 'success', result.summary
    assert result.scope['comparison'] == comparison
    assert result.scope['comparison_source'] == ('caller' if explicit else 'default_last_minus_first')
    cells = table_rows(result, 'displayed_yield_table')
    label = f"Δ {comparison['current']} − {comparison['baseline']}"
    assert label in result.artifacts[0]['data']
    deltas = [r for r in cells if r['row'] == 'Δ' and r['column'] == 'PT1H TEST/PT1H']
    assert len(deltas) == (4 if layout == 'group_period' else 1)
    for delta in deltas:
        values = {r['row']: r['raw'] for r in cells
                  if r['bucket'] == delta['bucket'] and r['column'] == delta['column']}
        assert delta['raw'] == pytest.approx(values[comparison['current']] - values[comparison['baseline']])
    assert result.artifacts[0]['data'].count('<tr class="delta">') == len(deltas)
    if not explicit:
        assert 'last group − first group (default)' in result.scope['delta_definition']


@pytest.mark.parametrize('layout', ['group', 'group_period'])
def test_single_group_has_no_default_delta(setup_report, layout):
    call, _, _, _ = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4},
                  layout=layout, grouping=inline(('A', ['L0', 'L1', 'L2', 'L3'])))
    assert result.scope['comparison'] is None
    assert result.scope['comparison_source'] is None
    assert '<tr class="delta">' not in result.artifacts[0]['data']


def test_period_comparison_remains_latest_minus_previous(setup_report):
    call, _, _, _ = setup_report
    result = call(lot_cd='4SS', period={'kind': 'recent', 'weeks': 4})
    assert result.scope['comparisons']['week'] == {'current': '2026-49', 'baseline': '2026-48'}
    assert 'latest week − previous week' in result.scope['delta_definition']
