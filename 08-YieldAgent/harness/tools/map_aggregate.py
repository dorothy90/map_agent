"""Aggregate a filtered Oracle MAP cursor without writing intermediate die rows."""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from bin_mapping import PASS_BIN
from ..native_verification import WAFER_MAP_CONTRACT_VERSION
from ..types import Contract, ResultTable
from .map_matrix import MapMatrix, matrix_sql, resolve_matrix, resolve_operation
from .registry import ToolResult


_LEGEND_COLUMNS = {
    'recipe': 'RECIPE_ID', 'equipment': 'EQP_ID', 'chamber': 'EQP_CH',
    'stepper_recipe': 'STEPPER_RECIPE_ID',
}


class CummapAggregation(Contract):
    group_by: Literal['all', 'lot', 'wafer', 'product', 'test', 'day', 'week', 'month',
                      'recipe', 'equipment', 'chamber', 'stepper_recipe'] | None = Field(default=None, description=(
        'One cumulative map per distinct value of this dimension. For process history select recipe '
        '(RCP, DF_WF_LEGEND.RECIPE_ID), equipment (EQP_ID), chamber (EQP_CH), or stepper_recipe '
        '(STEPPER_RECIPE_ID) and supply group_oper. Other dimensions use MAP rows directly; '
        'all produces one combined map.'))
    group_oper: str | None = Field(default=None, min_length=1, max_length=500,
        description=('Manufacturing operation for recipe, equipment, chamber or stepper_recipe grouping. '
                     'For the first call, copy the process name or ID verbatim from the user message, '
                     'including any apparent misspelling. The tool matches DF_WF_LEGEND.OPER_DET_DESC '
                     'case-insensitively or OPER_ID exactly within the selected product. Only if it reports '
                     'no unique matching process, consult the product’s process list before retrying with a corrected value.'))
    matrix: MapMatrix | None = Field(default=None, description=(
        'For a two-dimensional cumulative map, set the row and column axes using eligible MAP or '
        'process-history fields. Each observed pair produces one cumulative map. Omit group_by and group_oper.'))
    selected_bins: list[str] = Field(default_factory=list,
        description='Optional exact combined MAP bin strings (left_bin + right_bin) to count. Empty counts all defined failures.')

    @model_validator(mode='after')
    def validate_group(self):
        if self.matrix is not None:
            if self.group_by is not None or self.group_oper is not None:
                raise ValueError('Use matrix alone or group_by with its applicable group_oper.')
            return self
        if self.group_by is None:
            self.group_by = 'all'
        if (self.group_by in _LEGEND_COLUMNS) != bool(self.group_oper and self.group_oper.strip()):
            raise ValueError('Supply group_oper for a manufacturing-process group only.')
        return self


def _group_query(cursor, args, matrix=None):
    from .load_wafer_maps import _filters

    spec = args.aggregate
    where, binds = _filters(args)
    base = f'(SELECT * FROM MAP WHERE {where or "1=1"}) m'
    if matrix is not None:
        return matrix_sql(base, binds, matrix)
    joins = ''
    variants = '1'
    if spec.group_by in _LEGEND_COLUMNS:
        operation_filter = 'LOT_CD = :lot_cd' if args.lot_cd else '1=1'
        binds['legend_oper_id'] = resolve_operation(cursor, spec.group_oper, args.lot_cd)
        field = _LEGEND_COLUMNS[spec.group_by]
        joins = f'''LEFT JOIN (SELECT LOT_CD, LOT_ID, WF_ID, MIN({field}) AS group_id,
                        COUNT(DISTINCT {field}) AS variants
                 FROM DF_WF_LEGEND WHERE OPER_ID = :legend_oper_id AND {operation_filter}
                 GROUP BY LOT_CD, LOT_ID, WF_ID) l
                 ON l.LOT_CD=m.LOT_CD AND l.LOT_ID=m.LOT_ID AND l.WF_ID=TO_CHAR(m.WF_ID)'''
        group, variants = 'l.group_id', 'NVL(l.variants, 0)'
    else:
        date_column = 'm.START_TM' if args.time_column == 'start_tm' else 'm.END_TM'
        group = {
            'all': "'All'", 'lot': 'm.LOT_ID', 'wafer': "m.LOT_ID || ' #' || TO_CHAR(m.WF_ID)",
            'product': 'm.LOT_CD', 'test': 'm.OPER_DET_DESC',
            'day': f"TO_CHAR({date_column}, 'YYYY-MM-DD')",
            'week': f"""TO_CHAR({date_column}, 'IYYY-"W"IW')""",
            'month': f"TO_CHAR({date_column}, 'YYYY-MM')",
        }[spec.group_by]
    return f'''SELECT m.LOT_CD, m.OPER_DET_DESC AS oper, m.LOT_ID, m.WF_ID, m.START_TM, m.END_TM,
                      {group} AS group_id, {variants} AS variants, m.MAP_VAL_JSON
               FROM {base} {joins}''', binds


def _summarize(rows, spec, patterns, source_result_id, *, lot_ids=(), wf_ids=(), matrix=None,
               spool_dir=None, render_matrix=False):
    from .load_wafer_maps import _measurement_id, _parse_payload

    compiled = {pair: {name: re.compile(expr) for name, expr in definitions.items()}
                for pair, definitions in patterns.items()}
    selected = set(spec.selected_bins)

    @lru_cache(maxsize=4096)
    def is_fail(pair, symbol):
        if selected:
            return int(symbol in selected)
        definitions = compiled.get(pair, {})
        if symbol == PASS_BIN * 2:
            return 0
        if not any(rule.fullmatch(symbol) for rule in definitions.values()):
            raise ValueError(f'No defined fail bin matches MAP symbol {symbol!r} for {pair}.')
        return 1

    if matrix:
        from .map_matrix_counts import summarize_matrix
        return summarize_matrix(rows, spec, is_fail, source_result_id, matrix, lot_ids, wf_ids, spool_dir, render_matrix)

    seen, wafers = set(), set()
    group_wafers = defaultdict(set)
    coordinates = defaultdict(dict)
    measurement_counts = Counter()
    missing = conflicts = duplicates = 0
    members, cells, groups, manifest = [], [], [], []
    evidence = dict(contract_version=WAFER_MAP_CONTRACT_VERSION, selection_id=source_result_id)
    for row in rows:
        canonical, observations = _parse_payload(row['map_val_json'])
        identity = _measurement_id(row, canonical)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        wafer = row['lot_cd'], row['lot_id'], int(row['wf_id'])
        wafers.add(wafer)
        if row['variants'] > 1:
            conflicts += 1
            continue
        if not row['variants'] or row['group_id'] is None:
            missing += 1
            continue
        group = str(row['group_id'])
        group_wafers[group].add(wafer)
        measurement_counts[group] += 1
        members.append(dict(**evidence, group_id=group, source_result_id=source_result_id,
                            lot_id=row['lot_id'], wf_id=wafer[-1], measurement_id=identity))
        counts = coordinates[group]
        pair = (row['lot_cd'], row['oper'])
        for y, x, left, right in observations:
            coord = y, x
            count = counts.get(coord)
            if count is None:
                count = counts[coord] = [0, 0]
            count[0] += 1
            count[1] += is_fail(pair, left + right)

    for group in sorted(coordinates):
        group_coordinates = coordinates.pop(group)
        observed = failed = 0
        for (y, x), (n, k) in sorted(group_coordinates.items()):
            cells.append(dict(**evidence, group_id=group, row=y, col=x, observation_count=n,
                              fail_count=k, fail_rate=k/n))
            observed += n
            failed += k
        groups.append(dict(group_id=group, wafer_count=len(group_wafers[group]), measurement_count=measurement_counts[group],
                           observation_count=observed, fail_count=failed, fail_rate=failed/observed if observed else None))
        manifest.append(dict(**evidence, map_type='cummap', group_id=group, member_count=measurement_counts[group],
            grouping_mode='selection_set' if spec.group_by == 'all' else 'explicit_groups',
            measurement_policy='All distinct MAP measurements, including retests',
            numerator_mode='selected_bins' if selected else 'defined_fail_bins',
            numerator_bins_json=json.dumps(sorted(selected)), source_result_ids_json=json.dumps([source_result_id])))

    found = {(lot, wf) for _, lot, wf in wafers}
    missing_lots = sorted(set(lot_ids) - {lot for lot, _ in found})
    missing_wafers = sorted(set(wf_ids) - {wf for _, wf in found})
    missing_pairs = [[lot, wf] for lot in lot_ids for wf in wf_ids if (lot, wf) not in found]
    coverage_gaps = bool(missing or conflicts or missing_lots or missing_wafers or missing_pairs)
    facts = dict(missing_requests=dict(lot_ids=missing_lots, wf_ids=missing_wafers, lot_wafers=missing_pairs),
                 source_exhausted=True, grouping_complete=not (missing or conflicts), group_by=spec.group_by, group_oper=spec.group_oper, wafer_count=len(wafers),
                 measurement_count=len(seen), included_measurement_count=len(members),
                 included_wafer_count=len(set().union(*group_wafers.values())),
                 missing_group_measurements=missing, conflicting_group_measurements=conflicts,
                 duplicate_measurement_count=duplicates, die_observations=sum(g['observation_count'] for g in groups))
    tables = [ResultTable(table_id=table_id, title=table_id, rows=records, complete=True)
              for table_id, records in [('wafer_map_groups', groups), ('wafer_map_cells', cells),
                                        ('wafer_map_members', members), ('wafer_map_manifest', manifest)]]
    status = 'partial' if coverage_gaps else ('success' if members else 'empty')
    return ToolResult(scope={'map_aggregate': facts}, tables=tables,
                      summary=f'Cummap: {len(groups)} groups, {len(members)}/{len(seen)} measurements grouped; '
                              f'{missing} missing and {conflicts} ambiguous group assignments.',
                      status=status)


def aggregate_wafer_maps(args, source_result_id='oracle:MAP', *, spool_dir=None):
    from .load_wafer_maps import _connection, _configure_cursor, _filters, _load_symbol_patterns
    from .map_aggregate_plot import cummap_artifact

    with _connection() as connection, connection.cursor() as cursor:
        _configure_cursor(cursor)
        matrix = resolve_matrix(cursor, args) if args.aggregate.matrix else None
        sql, binds = _group_query(cursor, args, matrix)
        if args.lot_cd and args.oper:
            dimensions = [(args.lot_cd, args.oper)]
        else:
            where, filters = _filters(args)
            cursor.execute('SELECT DISTINCT LOT_CD, OPER_DET_DESC FROM MAP WHERE ' + (where or '1=1'), filters)
            dimensions = cursor.fetchall()
        patterns = _load_symbol_patterns(cursor, [dict(lot_cd=p, oper=t) for p, t in dimensions])
        cursor.execute(sql, binds)
        columns = [entry[0].lower() for entry in cursor.description]
        result = _summarize((dict(zip(columns, values)) for values in cursor), args.aggregate, patterns,
                            source_result_id, lot_ids=args.lot_ids, wf_ids=args.wf_ids, matrix=matrix,
                            spool_dir=spool_dir, render_matrix=bool(matrix))
    result.scope['filters'] = args.model_dump(mode='json', exclude={'max_measurements', 'max_dies', 'measurement_offset'})
    tables = {t.table_id: t.rows for t in result.tables}
    if not matrix and tables['wafer_map_cells']:
        result.artifacts.append(cummap_artifact(tables['wafer_map_cells'], tables['wafer_map_groups']))
    return result
