"""Persist immutable report inputs as owned evidence, not live query recipes."""
import uuid

from ..types import ResultTable
from .report_policy import ReportContractError
from .yield_report_data import PeriodReportData

CONTRACT = 'yield-report-scope/v1'
PREFIX = 'yield-report:'
METADATA_COLUMNS = ['PROC_GBN_CD', 'PARAM_NM', 'PARAM_SEQNO', 'PARAM_KIND_CD', 'YIELD_TABLE']
CAPTURE_COLUMNS = {
    'lot_population': ['lot_id', 'wafer_count', 'pt1h_mean', 'pt1h_value_count'],
    'lot_parameter_stats': ['grain', 'bucket', 'lot_id', 'process', 'param', 'value_sum', 'value_count'],
    'lot_wafer_members': ['grain', 'bucket', 'lot_id', 'gorupkey'],
}


def capture_tables(data):
    return [ResultTable(table_id=name, title=name.replace('_', ' ').title(),
                        rows=getattr(data, name), columns=columns, complete=True)
            for name, columns in CAPTURE_COLUMNS.items()]


async def save_scope(data, period, ctx):
    if ctx is None:
        # Pure renderer tests have no persistence. Never advertise this ID as reusable.
        return PREFIX + 'unpersisted-' + uuid.uuid4().hex
    tables = [
        ResultTable(table_id='report_metadata', title='Captured parameter metadata',
                    rows=[dict(zip(METADATA_COLUMNS, row)) for row in data.metadata],
                    columns=METADATA_COLUMNS, complete=True),
        ResultTable(table_id='report_yield_long', title='Captured period values', rows=data.yield_long,
                    columns=['grain', 'bucket', 'process', 'param', 'value'], complete=True),
        ResultTable(table_id='report_counts', title='Captured period counts',
                    rows=[dict(grain=grain, **row) for grain, rows in data.counts.items() for row in rows],
                    columns=['grain', *data.counts, 'lot_count', 'wafer_count', 'has_yield_data', 'has_gms'], complete=True),
        ResultTable(table_id='report_gms', title='Captured product GMS',
                    rows=[dict(grain=grain, **row) for grain, rows in data.gms.items() for row in rows],
                    columns=['grain', *data.gms, 'period_date', 'fab', 'prb', 'cum0'], complete=True),
        *capture_tables(data),
    ]
    observation = {
        'run_id': ctx.run['run_id'],
        'invocation_id': (getattr(ctx, 'invocation_id', '') or uuid.uuid4().hex) + ':report-scope:' + uuid.uuid4().hex,
        'tool_name': '_yield_report_scope', 'validated_arguments': {}, 'status': 'success',
        'summary': 'Captured report inputs; statement-level consistency',
        'scope': {'contract': CONTRACT, 'lot_cd': data.lot_cd, 'period': period,
                  'epoch': ctx.run.get('epoch'), 'runtime_id': ctx.run.get('runtime_id'),
                  'runtime_version': ctx.run.get('runtime_version'),
                  'window': data.window, 'grains': list(data.counts),
                  'period_lot_count': data.period_lot_count, 'period_wafer_count': data.period_wafer_count,
                  'default_table_id': 'lot_population'},
        'source_result_ids': [], 'provenance': {'data_origin': 'live'},
    }
    saved = await ctx.store.put_result(ctx.run['principal_id'], ctx.run['session_id'],
                                       observation, [], [], tables=tables)
    return PREFIX + saved.result_id


async def load_scope(scope_id, ctx):
    if ctx is None or not scope_id.startswith(PREFIX):
        raise ReportContractError('REPORT_SCOPE_UNAVAILABLE', 'A stored report_scope_id requires its owning session')
    identity = scope_id.removeprefix(PREFIX)
    try:
        obs = await ctx.store.observation(ctx.run['principal_id'], ctx.run['session_id'], identity)
    except (PermissionError, KeyError) as exc:
        raise ReportContractError('REPORT_SCOPE_UNAVAILABLE', 'Report scope unavailable in this session') from exc
    if obs.tool_name != '_yield_report_scope' or obs.scope.get('contract') != CONTRACT or obs.status != 'success':
        raise ReportContractError('INVALID_REPORT_SCOPE', 'report_scope_id must identify a captured report scope')
    if (obs.run_id != ctx.run['run_id'] or obs.scope.get('epoch') != ctx.run.get('epoch')
            or obs.scope.get('runtime_id') != ctx.run.get('runtime_id')
            or obs.scope.get('runtime_version') != ctx.run.get('runtime_version')):
        raise ReportContractError('REPORT_SCOPE_UNAVAILABLE', 'Report scope belongs to another run, epoch or runtime')
    required = {'report_metadata', 'report_yield_long', 'report_counts', 'report_gms', *CAPTURE_COLUMNS}
    refs = {t.table_id: t for t in obs.tables}
    if not required <= refs.keys() or any(not refs[name].complete for name in required):
        raise ReportContractError('INCOMPLETE_REPORT_SCOPE', 'Captured report scope is incomplete')
    records = {}
    for name in required:
        records[name] = await ctx.store.rows(ctx.run['principal_id'], ctx.run['session_id'], identity, table_id=name)
        if len(records[name]) != refs[name].total_rows:
            raise ReportContractError('INCOMPLETE_REPORT_SCOPE', f'Incomplete scope table: {name}')
    scope = obs.scope
    data = PeriodReportData(
        lot_cd=scope['lot_cd'], window=scope['window'],
        metadata=[tuple(row.get(key) for key in METADATA_COLUMNS) for row in records['report_metadata']],
        yield_long=records['report_yield_long'],
        counts={grain: [{k: v for k, v in row.items() if k != 'grain'}
                        for row in records['report_counts'] if row['grain'] == grain] for grain in scope['grains']},
        gms={grain: [{k: v for k, v in row.items() if k != 'grain'}
                     for row in records['report_gms'] if row['grain'] == grain] for grain in scope['grains']},
        period_lot_count=scope['period_lot_count'], period_wafer_count=scope['period_wafer_count'],
        **{name: records[name] for name in CAPTURE_COLUMNS},
    )
    return data, scope['period'], scope_id


def attach_population(result, data, scope_id):
    # Only compact per-lot scores are repeated on reports. Larger capture tables
    # live in the owned scope result and are read on demand, never put in scope JSON.
    result.tables.append(capture_tables(data)[0])
    result.scope.update(
        population_summary={'lot_count': len(data.lot_population),
                            'period_lot_count': data.period_lot_count,
                            'period_wafer_count': data.period_wafer_count,
                            'null_score_lot_count': sum(r['pt1h_mean'] is None for r in data.lot_population)},
        lot_population={'table_id': 'lot_population', 'complete': True,
                        'source_result_id': scope_id.removeprefix(PREFIX),
                        'definition': "PT1H population; wafer_count=distinct GORUPKEY; pt1h_mean=source-row AVG(VALUE) for PT1H TEST/PT1H; null scores retained"},
        tables_available=[t.table_id for t in result.tables],
    )
