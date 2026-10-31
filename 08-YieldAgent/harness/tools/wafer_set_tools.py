"""Opt-in immutable wafer tables and their selection, ranking, and rendering tools."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
import math
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from symbol_bin import combine_bins, make_symbol_fail_matcher
from symbol_bin_db import load_symbol_patterns
from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_predicates import EMPTY_FILTER_HINT, empty_filter_limitation
from .registry import ToolResult, ToolSpec


class TableHandle(Contract):
    result_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    version: str | None = None


class SelectWafersInput(Contract):
    lot_cd: list[str] = Field(default_factory=list, max_length=20)
    process: str = Field(min_length=1, max_length=30)
    time_column: Literal['end_tm', 'start_tm']
    time_from: datetime
    time_to: datetime
    time_zone: Literal['Asia/Seoul'] = 'Asia/Seoul'
    pass_bin: str | None = Field(default=None, min_length=1)
    bin_side: Literal['left_bin', 'right_bin'] | None = None
    symbol_bin_parameters: list[str] | None = Field(
        default=None, min_length=1, max_length=25,
        description='DF_SYMBOL_BIN.PARAM_NM 값(bin 파라미터 이름) 목록')
    measurement_policy: Literal['all', 'latest_per_wafer'] = 'latest_per_wafer'
    max_rows: int = Field(default=5000, ge=1, le=20000)

    @model_validator(mode='after')
    def validate_selection(self):
        for name in ('time_from', 'time_to'):
            value = getattr(self, name)
            if value.tzinfo is not None:
                value = value.astimezone(ZoneInfo(self.time_zone)).replace(tzinfo=None)
            setattr(self, name, value)
        if self.time_to <= self.time_from:
            raise ValueError('time_to must follow time_from')
        bin_mode = self.pass_bin is not None or self.bin_side is not None
        if bin_mode == (self.symbol_bin_parameters is not None):
            raise ValueError(
                "Supply exactly one calculation mode"
                ". pass_bin과 bin_side('left_bin' 또는 'right_bin')를 함께 지정하고 "
                "symbol_bin_parameters는 생략하거나, "
                "symbol_bin_parameters만 지정하고 pass_bin과 bin_side는 생략하세요."
            )
        if bin_mode and (self.pass_bin is None or self.bin_side is None):
            raise ValueError('pass_bin and bin_side are both required')
        if self.symbol_bin_parameters and any(not p or ',' in p for p in self.symbol_bin_parameters):
            raise ValueError('Parameter names must be nonempty and cannot contain commas')
        return self


class RankMembersInput(Contract):
    source: TableHandle
    order_by: str = Field(min_length=1)
    direction: Literal['asc', 'desc']
    top_n: int = Field(ge=1, le=200)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    tie_policy: Literal['exact_n', 'include_ties'] = 'exact_n'


class RenderMapsInput(Contract):
    source: TableHandle
    view: Literal['pass_fail', 'bin_value']
    max_wafers: int = Field(default=12, ge=1, le=30)


WAFER_COLUMNS = ['lot_id', 'wf_id', 'lot_cd', 'process', 'start_tm', 'end_tm',
    'time_column', 'time_value', 'yield_percent', 'pass_die_count', 'die_count',
    'unmatched_cells', 'calc_mode', 'calc_pass_bin', 'calc_bin_side',
    'calc_symbol_parameters', 'payload_hash']
CALC_COLUMNS = ['calc_mode', 'calc_pass_bin', 'calc_bin_side', 'calc_symbol_parameters']
UNITS = {'yield_percent': 'percent', 'pass_die_count': 'dies', 'die_count': 'dies'}


def _version(rows):
    return sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _payload(value):
    return value.read() if hasattr(value, 'read') else value


def _payload_hash(value):
    return sha256(_payload(value).encode()).hexdigest()[:16]


def _time(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _time_column(value):
    if value not in ('end_tm', 'start_tm'):
        raise ValueError('Unsupported time_column')
    return value


def _fetch_measurements(args):
    from map_agent import _get_oracle_connection_common, ORACLE_TABLE
    column = _time_column(args.time_column)
    binds = {'time_from': args.time_from, 'time_to': args.time_to,
             'process': args.process, 'row_limit': args.max_rows + 1}
    sql = f'''SELECT lot_id, wf_id, lot_cd, OPER_DET_DESC AS process,
                     start_tm, end_tm, map_val_json
              FROM {ORACLE_TABLE}
              WHERE {column} >= :time_from AND {column} < :time_to'''
    if any(c.isspace() for c in args.process):
        sql += ' AND OPER_DET_DESC = :process'
    else:
        sql += " AND (OPER_DET_DESC = :process OR OPER_DET_DESC = :process || ' TEST')"
    if args.lot_cd:
        names = []
        for i, lot in enumerate(args.lot_cd):
            key = f'lot_{i}'
            names.append(f':{key}')
            binds[key] = lot
        sql += f" AND LOT_CD IN ({', '.join(names)})"
    sql += f' ORDER BY lot_id, wf_id, {column} FETCH FIRST :row_limit ROWS ONLY'
    connection = _get_oracle_connection_common()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, binds)
        names = [d[0].lower() for d in cursor.description]
        result = []
        for values in cursor.fetchmany(args.max_rows + 1):
            row = dict(zip(names, values))
            row['map_val_json'] = _payload(row['map_val_json'])
            result.append(row)
        return result
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def _fetch_lot_codes():
    from map_agent import _get_oracle_connection_common, ORACLE_TABLE
    connection = _get_oracle_connection_common()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(f'SELECT DISTINCT LOT_CD FROM {ORACLE_TABLE} WHERE LOT_CD IS NOT NULL', {})
        return {str(row[0]) for row in cursor}
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def _fetch_maps_by_ref(rows):
    from map_agent import _get_oracle_connection_common, ORACLE_TABLE
    if not rows:
        return []
    connection = _get_oracle_connection_common()
    cursor = None
    try:
        cursor = connection.cursor()
        result = []
        for row in rows:
            column = _time_column(row['time_column'])
            cursor.execute(f'''SELECT map_val_json FROM {ORACLE_TABLE}
                WHERE lot_id = :lot AND wf_id = :wf AND OPER_DET_DESC = :process
                  AND {column} >= TO_TIMESTAMP(:t, 'YYYY-MM-DD HH24:MI:SS')
                  AND {column} < TO_TIMESTAMP(:t, 'YYYY-MM-DD HH24:MI:SS') + INTERVAL '1' SECOND''',
                {'lot': row['lot_id'], 'wf': row['wf_id'], 'process': row['process'], 't': row['time_value']})
            matched = None
            for value in cursor:
                payload = _payload(value[0])
                if _payload_hash(payload) == row['payload_hash']:
                    matched = payload
            if matched is None:
                raise ValueError('Measurement unavailable or payload_hash mismatch')
            result.append({**row, 'map_val_json': matched})
        return result
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def _cells(payload):
    result = []
    coordinates = set()
    for entry in json.loads(payload)['MAP']:
        parts = entry.split(',')
        coordinate = (int(parts[0]), int(parts[1]))
        if coordinate in coordinates:
            raise ValueError('Map contains duplicate die coordinates')
        coordinates.add(coordinate)
        result.append((*coordinate, parts[2] if len(parts) > 2 else None,
                       parts[3] if len(parts) > 3 else None))
    return result


def _definition(args):
    return {'calc_mode': 'symbol' if args.symbol_bin_parameters else 'bin',
            'calc_pass_bin': args.pass_bin, 'calc_bin_side': args.bin_side,
            'calc_symbol_parameters': ','.join(args.symbol_bin_parameters) if args.symbol_bin_parameters else None}


def _matchers(row, cache):
    if row['calc_mode'] != 'symbol':
        return {}
    names = row['calc_symbol_parameters'].split(',')
    key = (row['lot_cd'], row['process'], tuple(names))
    if key not in cache:
        patterns = load_symbol_patterns(row['lot_cd'], row['process'], names)
        cache[key] = {name: make_symbol_fail_matcher({name: patterns[name]}) for name in dict.fromkeys(names)}
    return cache[key]


def _calculate(cells, definition, matchers):
    failures = []
    unmatched = 0
    for _, _, left, right in cells:
        pair = combine_bins(left, right)
        unmatched += pair is None
        if definition['calc_mode'] == 'symbol':
            failures.append([] if pair is None else
                            [name for name, matcher in matchers.items() if matcher(left, right)])
        else:
            failures.append([])
    if definition['calc_mode'] == 'symbol':
        passed = sum(not failed for failed in failures)
    else:
        index = 2 if definition['calc_bin_side'] == 'left_bin' else 3
        passed = sum(cell[index] == definition['calc_pass_bin'] for cell in cells)
    return {'yield_percent': 100 * passed / len(cells) if cells else None,
            'pass_die_count': passed, 'die_count': len(cells),
            'unmatched_cells': unmatched}, failures


def _result(tables, scope, **kwargs):
    scope = {**scope, 'handle_contract': 'wafer-table/v1',
             'table_versions': {t.table_id: _version(t.rows) for t in tables}}
    return ToolResult(tables=tables, scope=scope, **kwargs)


def _select_wafers(args):
    source = _fetch_measurements(args)
    limitations = []
    if not source:
        lot_codes = {value.casefold() for value in _fetch_lot_codes()}
        tokens = args.process.split()
        lot_tokens = [token for token in tokens if token.casefold() in lot_codes]
        if lot_tokens:
            process_tokens = [token for token in tokens if token.casefold() not in lot_codes]
            if not process_tokens:
                raise ValueError(
                    f"process value '{args.process}' is a lot_cd; supply a process such as one listed by list_values"
                )
            lot_cd = list(args.lot_cd)
            seen = {value.casefold() for value in lot_cd}
            for token in lot_tokens:
                if token.casefold() not in seen:
                    lot_cd.append(token)
                    seen.add(token.casefold())
            args = args.model_copy(update={'lot_cd': lot_cd, 'process': ' '.join(process_tokens)})
            source = _fetch_measurements(args)
            label = 'token' if len(lot_tokens) == 1 else 'tokens'
            verb = 'was' if len(lot_tokens) == 1 else 'were'
            values = ', '.join(repr(token) for token in lot_tokens)
            limitations.append({'code': 'PROCESS_TOKEN_REINTERPRETED_AS_LOT_CD',
                                'detail': f'process {label} {values} {verb} reinterpreted as lot_cd'})
    records = source[:args.max_rows]
    reasons = []
    if len(source) > args.max_rows:
        reasons.append(f'max_rows exceeded: only {args.max_rows} measurements used')
    if args.measurement_policy == 'latest_per_wafer':
        # The ordered overfetch row identifies a wafer whose history was cut.
        # Never label an earlier measurement from that prefix as its latest.
        if len(source) > args.max_rows:
            boundary = source[args.max_rows]
            key = (boundary['lot_id'], int(boundary['wf_id']))
            retained = [r for r in records if (r['lot_id'], int(r['wf_id'])) != key]
            if len(retained) != len(records):
                reasons.append('Incomplete boundary wafer excluded; latest measurement cannot be established')
            records = retained
        grouped = defaultdict(list)
        for row in records:
            grouped[(row['lot_id'], int(row['wf_id']))].append(row)
        records = []
        for key, group in grouped.items():
            latest = max(row[args.time_column] for row in group)
            ties = [row for row in group if row[args.time_column] == latest]
            if len({_payload(row['map_val_json']) for row in ties}) > 1:
                raise ValueError(f'Conflicting same-time measurements for {key}')
            records.append(ties[0])
    wafers = []
    empty = 0
    unmatched = 0
    cache = {}
    definition = _definition(args)
    for record in records:
        payload = _payload(record['map_val_json'])
        cells = _cells(payload)
        if not cells:
            empty += 1
            continue
        identity = {'lot_id': str(record['lot_id']), 'wf_id': int(record['wf_id']),
                    'lot_cd': str(record['lot_cd']), 'process': str(record['process'])}
        matchers = _matchers({**identity, **definition}, cache)
        metric, _ = _calculate(cells, definition, matchers)
        unmatched += metric['unmatched_cells']
        wafers.append({**identity, 'start_tm': _time(record['start_tm']), 'end_tm': _time(record['end_tm']),
            'time_column': args.time_column, 'time_value': _time(record[args.time_column]),
            **metric, **definition, 'payload_hash': _payload_hash(payload)})
    if empty:
        reasons.append(f'{empty} empty maps excluded')
    if unmatched:
        reasons.append(f'{unmatched} cells have unmatched bin pairs')
    complete = not reasons
    reason = '; '.join(reasons) or None
    summary = {'source_measurement_count': len(source), 'wafer_count': len(wafers),
        'excluded_empty_maps': empty, 'unmatched_cells': unmatched,
        'measurement_policy': args.measurement_policy,
        'time_column': args.time_column, 'time_from': _time(args.time_from), 'time_to': _time(args.time_to),
        'time_zone': args.time_zone, 'process': args.process, 'lot_cd': ','.join(args.lot_cd),
        **definition, 'complete': complete}
    tables = [ResultTable(table_id='wafers', title='Wafer measurements', rows=wafers,
        columns=WAFER_COLUMNS, units=UNITS, complete=complete, missing_reason=reason),
        ResultTable(table_id='selection_summary', title='Selection summary', rows=[summary],
        columns=list(summary), complete=complete, missing_reason=reason)]
    scope = {**args.model_dump(mode='json'), **summary, 'lot_cd': args.lot_cd,
        'yield_definition': '100 * (die_count - union_fail_die_count) / die_count' if args.symbol_bin_parameters
            else '100 * count(bin_side == pass_bin) / die_count',
        'interval': '[time_from,time_to)', 'time_basis': args.time_column, 'missing_reason': reason}
    if limitations:
        scope['limitations'] = limitations
    result_summary = (f'측정 {len(source)}건에서 wafer 측정 {len(wafers)}건을 선택했습니다. '
                      f'빈 맵 제외 {empty}건입니다.')
    if not source:
        result_summary += ' ' + EMPTY_FILTER_HINT
        limitations.append(empty_filter_limitation('wafers', 0))
        scope['limitations'] = limitations
    return _result(tables, scope, status='partial' if not complete else 'success' if wafers else 'empty',
        summary=result_summary)


async def select_wafers(args, ctx):
    return await run_blocking(_select_wafers, args)


async def _read_handle(source, ctx):
    principal, session = ctx.run['principal_id'], ctx.run['session_id']
    obs = await ctx.store.observation(principal, session, source.result_id)
    if obs.scope.get('handle_contract') != 'wafer-table/v1':
        raise ValueError('Unsupported handle_contract')
    table = next((t for t in obs.tables if t.table_id == source.table_id), None)
    if table is None:
        raise ValueError('Unknown table_id')
    rows = await ctx.store.rows(principal, session, source.result_id, table_id=source.table_id)
    version = _version(rows)
    if source.version is not None and version != source.version:
        raise ValueError('Table version mismatch')
    return rows, table, version, obs


async def rank_members(args, ctx):
    rows, table, version, obs = await _read_handle(args.source, ctx)
    columns = list(dict.fromkeys([*table.columns, *(key for row in rows for key in row)]))
    for key in [args.order_by, *args.group_by]:
        if key not in columns:
            raise ValueError(f'Unknown column: {key}')
    groups = defaultdict(list)
    invalid = []
    for row in rows:
        value = row.get(args.order_by)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            invalid.append({**row, 'rank': None, 'selection_order': None, 'selected': False})
        else:
            groups[tuple(row.get(k) for k in args.group_by)].append(row)
    ranked = []
    cutoffs = []
    for key, members in groups.items():
        members = sorted(members, key=lambda r: (str(r.get('lot_id', '')), str(r.get('wf_id', ''))))
        members.sort(key=lambda r: r[args.order_by], reverse=args.direction == 'desc')
        cutoff = members[min(args.top_n, len(members)) - 1][args.order_by]
        ranks = {v: i + 1 for i, v in enumerate(dict.fromkeys(r[args.order_by] for r in members))}
        cutoffs.append({'group': dict(zip(args.group_by, key)), 'cutoff_value': cutoff,
                        'cutoff_tie_count': sum(r[args.order_by] == cutoff for r in members)})
        for i, row in enumerate(members, 1):
            selected = i <= args.top_n or (args.tie_policy == 'include_ties' and row[args.order_by] == cutoff)
            ranked.append({**row, 'rank': ranks[row[args.order_by]], 'selection_order': i, 'selected': selected})
    ranked.extend(invalid)
    selected = [r for r in ranked if r['selected']]
    reasons = [table.missing_reason or 'Incomplete source table'] if not table.complete else []
    if invalid:
        reasons.append(f'{len(invalid)} null, nonnumeric or nonfinite values excluded from ranking')
    complete = not reasons
    reason = '; '.join(reasons) or None
    columns = list(dict.fromkeys([*columns, 'rank', 'selection_order', 'selected']))
    tables = [ResultTable(table_id=name, title=name, rows=data, columns=columns, units=table.units,
        complete=complete, missing_reason=reason) for name, data in [('ranked', ranked), ('selected', selected)]]
    scope = {**args.model_dump(mode='json', exclude={'source'}), 'source_version': version,
        'population_row_count': len(rows), 'excluded_null_count': len(invalid),
        'complete': complete, 'missing_reason': reason,
        'cutoff_value': cutoffs[0]['cutoff_value'] if cutoffs and not args.group_by else None,
        'cutoff_tie_count': cutoffs[0]['cutoff_tie_count'] if cutoffs and not args.group_by else 0,
        'group_cutoffs': cutoffs}
    return _result(tables, scope, source_result_ids=[args.source.result_id],
        data_origin=obs.provenance.get('data_origin', 'unknown'),
        status='partial' if not complete else 'success' if ranked else 'empty',
        summary=f'전체 {len(rows)}행 중 {len(selected)}행을 선택했습니다. 순위 제외 {len(invalid)}행입니다.')


def _image_categories(cells, failures, definition, view, names):
    pairs = [combine_bins(cell[2], cell[3]) for cell in cells]
    if view == 'bin_value':
        values = [pair if pair is not None else 'unmatched' for pair in pairs]
        labels = list(dict.fromkeys(values))
        return labels, [labels.index(value) for value in values]
    labels = ['pass', *names, 'multiple', 'unmatched']
    multiple = len(labels) - 2
    unmatched = len(labels) - 1
    codes = []
    for pair, failed in zip(pairs, failures):
        if pair is None:
            codes.append(unmatched)
        elif not failed:
            codes.append(0)
        elif len(failed) > 1:
            codes.append(multiple)
        else:
            codes.append(1 + names.index(failed[0]))
    return labels, codes


def _image(cells, failures, definition, view, title, names):
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import ListedColormap, BoundaryNorm
    from matplotlib.patches import Patch
    from matplotlib import colormaps

    labels, codes = _image_categories(cells, failures, definition, view, names)
    if view == 'bin_value':
        colors = [colormaps['turbo'](i / max(1, len(labels) - 1)) for i in range(len(labels))]
    else:
        colors = ['#b8dfc2', *[colormaps['turbo'](i / max(1, len(labels) - 2))
                               for i in range(len(labels) - 2)], '#9e9e9e']
    ys, xs = [c[0] for c in cells], [c[1] for c in cells]
    grid = np.full((max(ys) - min(ys) + 1, max(xs) - min(xs) + 1), np.nan)
    for cell, code in zip(cells, codes):
        grid[cell[0] - min(ys), cell[1] - min(xs)] = code
    fig = Figure(figsize=(7, 6)); FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.imshow(grid, cmap=ListedColormap(colors), norm=BoundaryNorm(np.arange(-.5, len(labels) + .5), len(labels)))
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])
    ax.legend(handles=[Patch(facecolor=color, label=str(label)) for label, color in zip(labels, colors)],
              loc='upper left', bbox_to_anchor=(1, 1))
    out = BytesIO()
    fig.savefig(out, format='png', dpi=120, bbox_inches='tight')
    return out.getvalue()


def _render_maps(rows, args, source_version, complete, missing_reason, data_origin):
    records = _fetch_maps_by_ref(rows)
    if len(records) != len(rows):
        raise ValueError('Measurement count mismatch')
    rendered, parameter_yields, artifacts, definitions = [], [], [], []
    unmatched_total = 0
    cache = {}
    for row, record in zip(rows, records):
        payload = _payload(record['map_val_json'])
        if _payload_hash(payload) != row['payload_hash']:
            raise ValueError('payload_hash mismatch')
        cells = _cells(payload)
        if not cells:
            raise ValueError('Cannot render an empty map')
        matchers = _matchers(row, cache)
        metric, failures = _calculate(cells, row, matchers)
        unmatched_total += metric['unmatched_cells']
        definition = {k: row[k] for k in CALC_COLUMNS}
        if definition not in definitions:
            definitions.append(definition)
        criterion = (f"{row['calc_bin_side']}={row['calc_pass_bin']}" if row['calc_mode'] == 'bin'
                     else f"symbol: {row['calc_symbol_parameters']}")
        title = f"{row['lot_id']}.{int(row['wf_id']):02d} | yield {metric['yield_percent']:.3f}% | {criterion}"
        artifacts.append({'title': title, 'mime': 'image/png',
            'data': _image(cells, failures, row, args.view, title, list(matchers) if matchers else ['fail'])})
        rendered.append({'lot_id': row['lot_id'], 'wf_id': row['wf_id'], 'yield_percent': row['yield_percent'],
            'recomputed_yield_percent': metric['yield_percent'],
            'yield_match': abs(metric['yield_percent'] - row['yield_percent']) <= 1e-9,
            'die_count': metric['die_count'], 'unmatched_cells': metric['unmatched_cells'],
            'artifact_title': title, 'view': args.view})
        for name in matchers:
            count = sum(name in failed for failed in failures)
            parameter_yields.append({'lot_id': row['lot_id'], 'wf_id': row['wf_id'], 'parameter': name,
                'fail_die_count': count, 'die_count': len(cells), 'yield_percent': 100 * (len(cells) - count) / len(cells)})
    output_complete = complete and unmatched_total == 0
    output_reason = missing_reason or ('INVALID_BIN_PAIR' if unmatched_total else None)
    tables = [ResultTable(table_id='rendered_wafers', title='Rendered wafers', rows=rendered,
        columns=['lot_id', 'wf_id', 'yield_percent', 'recomputed_yield_percent', 'yield_match',
                 'die_count', 'unmatched_cells', 'artifact_title', 'view'],
        units={'yield_percent': 'percent', 'recomputed_yield_percent': 'percent', 'die_count': 'dies'},
        complete=output_complete, missing_reason=output_reason)]
    if parameter_yields:
        tables.append(ResultTable(table_id='parameter_yields', title='Parameter yields', rows=parameter_yields,
            columns=['lot_id', 'wf_id', 'parameter', 'fail_die_count', 'die_count', 'yield_percent'],
            units={'yield_percent': 'percent', 'fail_die_count': 'dies', 'die_count': 'dies'},
            complete=output_complete, missing_reason=output_reason))
    return _result(tables, {'source_version': source_version, 'view': args.view, 'rendered_count': len(rows),
        'unmatched_cells': unmatched_total, 'calculation_definitions': definitions,
        'complete': output_complete, 'missing_reason': output_reason},
        artifacts=artifacts, source_result_ids=[args.source.result_id],
        status='success' if output_complete else 'partial', data_origin=data_origin,
        summary=f'wafer 맵 {len(rendered)}장을 생성했습니다.')


async def render_maps(args, ctx):
    rows, table, version, obs = await _read_handle(args.source, ctx)
    if len(rows) > args.max_wafers:
        raise ValueError('먼저 select/rank 로 줄이세요: max_wafers 초과')
    required = ['lot_id', 'wf_id', 'lot_cd', 'process', 'time_column', 'time_value', 'payload_hash',
                'yield_percent', *CALC_COLUMNS]
    for row in rows:
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"Missing required columns: {missing}. source에는 필수 열을 포함한 select_wafers의 wafers 표 또는 그 표에서 만든 rank_members의 selected 표를 전달하세요. 원본 read_facts 표는 계산 열이 부족합니다.")
        _time_column(row['time_column'])
        if row['calc_mode'] not in ('bin', 'symbol'):
            raise ValueError('Invalid calc_mode')
        if row['calc_mode'] == 'bin' and (row['calc_bin_side'] not in ('left_bin', 'right_bin') or row['calc_pass_bin'] is None):
            raise ValueError('Missing bin calculation definition')
        if row['calc_mode'] == 'symbol' and not row['calc_symbol_parameters']:
            raise ValueError('Missing symbol calculation definition')
    if args.view == 'pass_fail' and any(row['calc_mode'] != 'symbol' for row in rows):
        raise ValueError(
            "Failure rendering requires symbol definitions. "
            "Reselect the wafers with symbol_bin_parameters and use that handle."
        )
    return await run_blocking(_render_maps, rows, args, version, table.complete, table.missing_reason,
                              obs.provenance.get('data_origin', 'unknown'))


def register(registry):
    registry.add(ToolSpec('select_wafers',
        '기간·공정·제품 조건과 계산 기준을 입력받아 wafer별 수율 표와 표 핸들을 반환한다. 출력은 최대 20,000행이다.',
        SelectWafersInput, select_wafers, eager=True))
    registry.add(ToolSpec('rank_members',
        '표 핸들과 정렬·선택 조건을 입력받아 순위 표와 선택된 행의 표 핸들을 반환한다. 선택 수는 그룹별 최대 200개다.',
        RankMembersInput, rank_members, eager=True))
    registry.add(ToolSpec('render_maps',
        '입력 표의 각 행마다 wafer 맵 이미지 1개와 재계산 결과표를 반환한다. 입력은 최대 30행이다.',
        RenderMapsInput, render_maps, eager=True))
