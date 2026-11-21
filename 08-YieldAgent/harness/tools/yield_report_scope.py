"""Persist immutable report inputs as owned evidence, not live query recipes."""
import math
import uuid
from collections import Counter

from ..types import ResultTable
from .report_policy import LOT_POPULATION_PROJECTION, REPORT_SCOPE_CONTRACT, ReportContractError
from .yield_report_data import GRAINS, PeriodReportData

CONTRACT = REPORT_SCOPE_CONTRACT
PREFIX = 'yield-report:'
METADATA_COLUMNS = ['PROC_GBN_CD', 'PARAM_NM', 'PARAM_SEQNO', 'PARAM_KIND_CD', 'YIELD_TABLE']
CAPTURE_COLUMNS = {
    'lot_population': ['lot_id', 'wafer_count', 'pt1h_mean', 'pt1h_value_count'],
    'lot_parameter_stats': ['grain', 'bucket', 'lot_id', 'process', 'param', 'value_sum', 'value_count'],
    'lot_wafer_members': ['grain', 'bucket', 'lot_id', 'gorupkey', 'wf_id'],
    'wafer_parameter_stats': ['grain', 'bucket', 'lot_id', 'wf_id', 'process', 'param', 'value_sum', 'value_count'],
}
PACKED_KEY = ['grain', 'bucket', 'lot_id', 'wf_id', 'process']
PACKED_COLUMNS = [*PACKED_KEY, 'values']
WAFER_KEY = ['lot_id', 'wf_id', 'process', 'param']


def _rebuild_wafer_all(rows, grain):
    totals = {}
    # Use the same bucket addition order before packing and after decoding.
    for row in sorted(rows, key=lambda row: row['bucket']):
        if row['grain'] != grain:
            continue
        key = tuple(row[k] for k in WAFER_KEY)
        if key not in totals:
            totals[key] = dict(row, grain='all', bucket='')
        else:
            totals[key]['value_sum'] += row['value_sum']
            totals[key]['value_count'] += row['value_count']
    return list(totals.values())


def _pack_wafer_stats(data):
    rows = data.wafer_parameter_stats
    # resolve_window/total_filter cover the same closed range (recent) or
    # listed buckets (explicit). Compare captured totals too: NULL identities
    # can span dates, and floating-point bucket sums need not equal SQL SUM.
    grain = next(iter(data.counts), None)
    buckets = data.window.get(GRAINS[grain][0], []) if grain in GRAINS else []
    canonical = lambda rs: Counter(tuple(r[k] for k in CAPTURE_COLUMNS['wafer_parameter_stats']) for r in rs)
    rebuild = (bool(buckets)
               and all(r['bucket'] in buckets for r in rows if r['grain'] == grain)
               and canonical(_rebuild_wafer_all(rows, grain)) == canonical(r for r in rows if r['grain'] == 'all'))
    params = {}
    for row in rows:
        names = params.setdefault(row['process'], [])
        if row['param'] not in names:
            names.append(row['param'])
    indexes = {process: {param: i for i, param in enumerate(names)} for process, names in params.items()}
    packed = {}
    for row in rows:
        if rebuild and row['grain'] == 'all':
            continue
        key = tuple(row[k] for k in PACKED_KEY)
        if key not in packed:
            packed[key] = dict(zip(PACKED_KEY, key), values=[None] * len(params[row['process']]))
        target = packed[key]
        index = indexes[row['process']][row['param']]
        if target['values'][index] is not None:
            raise ReportContractError('INCOMPLETE_REPORT_SCOPE', 'Duplicate wafer parameter statistics')
        target['values'][index] = (row['value_sum'] if row['value_count'] == 1
                                   else [row['value_sum'], row['value_count']])
    return list(packed.values()), dict(wafer_capture_encoding='packed-v1', wafer_params=params,
                                      wafer_all_from_grain=grain if rebuild else None)


def _unpack_wafer_stats(rows, scope):
    def invalid():
        raise ReportContractError('INCOMPLETE_REPORT_SCOPE', 'Invalid packed wafer statistics')

    def number(value):
        return type(value) is int or (type(value) is float and math.isfinite(value))

    params = scope.get('wafer_params')
    grain = scope.get('wafer_all_from_grain')
    if (not isinstance(params, dict) or 'wafer_all_from_grain' not in scope
            or (grain is not None and (not isinstance(grain, str) or grain not in GRAINS or not scope['window'].get(GRAINS[grain][0])))):
        invalid()
    for process, names in params.items():
        if (not isinstance(process, str) or not isinstance(names, list)
                or not all(isinstance(p, str) for p in names) or len(names) != len(set(names))):
            invalid()
    decoded, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not set(PACKED_COLUMNS) <= row.keys():
            invalid()
        if (not all(isinstance(row[k], str) for k in ('grain', 'bucket', 'process'))
                or row['grain'] not in ('all', *GRAINS)
                or any(row[k] is not None and not isinstance(row[k], str) for k in ('lot_id', 'wf_id'))
                or row['process'] not in params
                or not isinstance(row['values'], list)
                or len(row['values']) != len(params[row['process']])
                or (grain is not None and row['grain'] == 'all')):
            invalid()
        key = tuple(row[k] for k in PACKED_KEY)
        if key in seen:
            invalid()
        seen.add(key)
        for param, value in zip(params[row['process']], row['values']):
            if value is None:
                continue
            value_sum, count = value, 1
            if isinstance(value, list):
                if len(value) != 2:
                    invalid()
                value_sum, count = value
            if not number(value_sum) or type(count) is not int or count < 0:
                invalid()
            decoded.append(dict(zip(PACKED_KEY, key), param=param,
                                value_sum=value_sum, value_count=count))
    if grain is not None:
        decoded.extend(_rebuild_wafer_all(decoded, grain))
    return decoded


def capture_tables(data, *, wafer_packed=None):
    tables = [ResultTable(table_id=name, title=name.replace('_', ' ').title(),
                          rows=getattr(data, name), columns=columns, complete=True)
              for name, columns in CAPTURE_COLUMNS.items()
              if name != 'wafer_parameter_stats' and getattr(data, name) is not None]
    if data.wafer_parameter_stats is not None:
        rows = _pack_wafer_stats(data)[0] if wafer_packed is None else wafer_packed
        tables.append(ResultTable(table_id='wafer_parameter_stats', title='Wafer parameter stats',
                                  rows=rows, columns=PACKED_COLUMNS, complete=True))
    return tables


async def save_scope(data, period, ctx):
    if ctx is None:
        # Pure renderer tests have no persistence. Never advertise this ID as reusable.
        return PREFIX + 'unpersisted-' + uuid.uuid4().hex
    wafer_packed, encoding = _pack_wafer_stats(data) if data.wafer_parameter_stats is not None else (None, {})
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
        *capture_tables(data, wafer_packed=wafer_packed),
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
                  'default_table_id': 'lot_population',
                  'table_projections': {'lot_population': LOT_POPULATION_PROJECTION}},
        'source_result_ids': [], 'provenance': {'data_origin': 'live'},
    }
    if data.wafer_parameter_stats is not None:
        observation['scope']['wafer_capture'] = True
        observation['scope'].update(encoding)
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
    wafer_capture = obs.scope.get('wafer_capture', False)
    encoding = obs.scope.get('wafer_capture_encoding')
    if 'wafer_capture_encoding' in obs.scope and encoding != 'packed-v1':
        raise ReportContractError('INCOMPLETE_REPORT_SCOPE', 'Unknown wafer capture encoding')
    if not wafer_capture:
        required.remove('wafer_parameter_stats')
    refs = {t.table_id: t for t in obs.tables}
    if not required <= refs.keys() or any(not refs[name].complete for name in required):
        raise ReportContractError('INCOMPLETE_REPORT_SCOPE', 'Captured report scope is incomplete')
    for name, columns in CAPTURE_COLUMNS.items():
        if name not in required:
            continue
        if name == 'wafer_parameter_stats' and encoding == 'packed-v1':
            columns = PACKED_COLUMNS
        expected = columns if wafer_capture else [c for c in columns if c != 'wf_id']
        if not set(expected) <= set(refs[name].columns):
            raise ReportContractError('INCOMPLETE_REPORT_SCOPE', f'Missing scope columns: {name}')
    records = {}
    for name in required:
        records[name] = await ctx.store.rows(ctx.run['principal_id'], ctx.run['session_id'], identity, table_id=name)
        if len(records[name]) != refs[name].total_rows:
            raise ReportContractError('INCOMPLETE_REPORT_SCOPE', f'Incomplete scope table: {name}')
    scope = obs.scope
    if wafer_capture and encoding == 'packed-v1':
        records['wafer_parameter_stats'] = _unpack_wafer_stats(records['wafer_parameter_stats'], scope)
    data = PeriodReportData(
        lot_cd=scope['lot_cd'], window=scope['window'],
        metadata=[tuple(row.get(key) for key in METADATA_COLUMNS) for row in records['report_metadata']],
        yield_long=records['report_yield_long'],
        counts={grain: [{k: v for k, v in row.items() if k != 'grain'}
                        for row in records['report_counts'] if row['grain'] == grain] for grain in scope['grains']},
        gms={grain: [{k: v for k, v in row.items() if k != 'grain'}
                     for row in records['report_gms'] if row['grain'] == grain] for grain in scope['grains']},
        period_lot_count=scope['period_lot_count'], period_wafer_count=scope['period_wafer_count'],
        **{name: records.get(name) for name in CAPTURE_COLUMNS},
    )
    return data, scope['period'], scope_id


def attach_population(result, data, scope_id):
    # Only compact per-lot scores are repeated on reports. Larger capture tables
    # live in the owned scope result and are read on demand, never put in scope JSON.
    result.tables.append(ResultTable(table_id='lot_population', title='Lot Population',
                                     rows=data.lot_population, columns=CAPTURE_COLUMNS['lot_population'], complete=True))
    result.scope.update(
        population_summary={'lot_count': len(data.lot_population),
                            'period_lot_count': data.period_lot_count,
                            'period_wafer_count': data.period_wafer_count,
                            'null_score_lot_count': sum(r['pt1h_mean'] is None for r in data.lot_population)},
        lot_population={'table_id': 'lot_population', 'complete': True,
                        'source_result_id': scope_id.removeprefix(PREFIX),
                        'definition': "PT1H population; wafer_count=distinct GORUPKEY; pt1h_mean=source-row AVG(VALUE) for PT1H TEST/PT1H; null scores retained"},
        tables_available=[t.table_id for t in result.tables],
        table_projections={'lot_population': LOT_POPULATION_PROJECTION},
    )
