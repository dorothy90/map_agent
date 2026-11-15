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
                members.append(dict(grain=grain, bucket=bucket, lot_id=lot, gorupkey=f'{lot}.{wf:02}'))
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
        lot_population=population, lot_parameter_stats=stats, lot_wafer_members=members)


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
