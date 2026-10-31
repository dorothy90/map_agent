"""Direct map queries and numeric comparisons; no nested map-agent planner."""
from collections import defaultdict
from datetime import date
from io import BytesIO
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from ..types import Contract, ResultTable
from ..domain_metrics import select_measurements, wafer_pass_metric
from ..runtime.domain_process import run_blocking
from .registry import ToolResult, ToolSpec


class MapInput(Contract):
    lot_ids: list[str] = Field(default_factory=list, max_length=20)
    groupkey: str | None = Field(default=None, max_length=5000)
    lotcd: str | None = Field(default=None, max_length=20,
        description='Product/lot code, e.g. "4SS". For a product/period query, pass together with start_date and end_date.')
    start_date: date | None = Field(default=None,
        description='Period start, YYYY-MM-DD inclusive. The "부터" date in the user query.')
    end_date: date | None = Field(default=None,
        description='Exclusive end date, YYYY-MM-DD. For "까지 8월 31일" use 2026-09-01 (day after the last wanted date).')
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    wf_mod: int = Field(default=0, ge=0, le=1000)
    wf_rem: int = Field(default=0, ge=0, le=999)
    oper: Literal['PT1H', 'PT1C']
    map_type: Literal['binmap', 'cummap'] = 'cummap'
    target_bin: str | None = Field(default=None, min_length=1, max_length=20,
        description='For cummap: all dies other than this bin count as pass. Null means ?-bin pass.')
    symbol_bin_parameters: list[str] = Field(default_factory=list, max_length=25,
        description='Optional named product/process definitions used to classify die failures from the two source bin values.')
    bin_type: Literal['left_bin', 'right_bin'] = 'left_bin'
    measurement_policy: Literal['all_measurements', 'latest_per_wafer'] = 'all_measurements'

    @model_validator(mode='after')
    def target(self):
        by_date = any(v is not None for v in (self.lotcd, self.start_date, self.end_date))
        if by_date and not all(v is not None for v in (self.lotcd, self.start_date, self.end_date)):
            raise ValueError(
                'lotcd, start_date and end_date are required together: pass all three, '
                'e.g. lotcd="4SS", start_date="2026-08-28", end_date="2026-09-01" '
                '(exclusive end, day after the last wanted date)')
        if by_date and (self.lot_ids or self.groupkey):
            raise ValueError('Choose product/date or LOT/groupkey targeting')
        if self.lot_ids and self.groupkey:
            raise ValueError('Choose lot_ids or groupkey, not both')
        if not by_date and not self.lot_ids and not self.groupkey:
            raise ValueError('A product/date range or LOT/groupkey target is required')
        if by_date and self.end_date <= self.start_date:
            raise ValueError('end_date must be after start_date (exclusive)')
        if self.wf_mod and self.wf_rem >= self.wf_mod:
            raise ValueError('wf_rem must be smaller than wf_mod')
        if self.map_type == 'binmap' and self.target_bin is not None:
            raise ValueError('target_bin is a cumulative-map metric; binmap displays source categories')
        if self.symbol_bin_parameters and self.map_type != 'cummap':
            raise ValueError('symbol_bin_parameters is supported only for cummap')
        if self.symbol_bin_parameters and self.target_bin is not None:
            raise ValueError('symbol_bin_parameters and target_bin are mutually exclusive')
        return self


class MapPeriod(Contract):
    label: str = Field(min_length=1, max_length=100)
    lotcd: str = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date

    @model_validator(mode='after')
    def dates(self):
        if self.end_date <= self.start_date:
            raise ValueError('end_date must be after start_date (exclusive)')
        return self


class MapRegion(Contract):
    label: str = Field(min_length=1, max_length=100)
    row_min: int
    row_max: int
    col_min: int
    col_max: int

    @model_validator(mode='after')
    def bounds(self):
        if self.row_min > self.row_max or self.col_min > self.col_max:
            raise ValueError('Region bounds are reversed')
        return self


class CompareMapInput(Contract):
    periods: list[MapPeriod] = Field(min_length=2, max_length=12)
    oper: Literal['PT1H', 'PT1C']
    target_bin: str | None = Field(default=None, min_length=1, max_length=20)
    bin_type: Literal['left_bin', 'right_bin'] = 'left_bin'
    measurement_policy: Literal['all_measurements', 'latest_per_wafer']
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    wf_mod: int = Field(default=0, ge=0, le=1000)
    wf_rem: int = Field(default=0, ge=0, le=999)
    regions: list[MapRegion] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def labels(self):
        if len({p.label for p in self.periods}) != len(self.periods):
            raise ValueError('Period labels must be unique')
        if self.wf_mod and self.wf_rem >= self.wf_mod:
            raise ValueError('wf_rem must be smaller than wf_mod')
        return self


def map_statistics(rows, target_bin=None, bin_type='left_bin', fail_matchers=None, default_lot_cd=None):
    from map_agent import _parse_wafer_for_cummap
    counts = defaultdict(lambda: [0, 0])
    total = passed = unmatched = 0
    for row in rows:
        matcher = None
        if fail_matchers is not None:
            lot_cd = row.get('lot_cd') or default_lot_cd
            if not lot_cd:
                raise ValueError('lot_cd is required for symbol bin matching')
            try:
                matcher = fail_matchers[lot_cd]
            except KeyError as exc:
                raise ValueError(f'No symbol bin definitions loaded for lot_cd {lot_cd!r}') from exc
        metric = wafer_pass_metric(row['map_val_json'], target_bin, bin_type, fail_matcher=matcher)
        total += metric['die_count']
        passed += metric['pass_count']
        unmatched += metric.get('unmatched_cells', 0)
        if matcher is None:
            rr, cc, pp = _parse_wafer_for_cummap((row['map_val_json'], bin_type, target_bin))
            observations = zip(rr, cc, pp)
        else:
            raw = json.loads(row['map_val_json']) if isinstance(row['map_val_json'], str) else row['map_val_json']
            observations = []
            coordinates = set()
            for entry in raw['MAP']:
                parts = entry.split(',')
                coordinate = (int(parts[0]), int(parts[1]))
                if coordinate in coordinates:
                    raise ValueError('Map contains duplicate die coordinates')
                coordinates.add(coordinate)
                left = parts[2] if len(parts) > 2 else None
                right = parts[3] if len(parts) > 3 else None
                valid_pair = (isinstance(left, str) and isinstance(right, str)
                              and len(left) == 1 and len(right) == 1)
                observations.append((*coordinate, not valid_pair or not matcher(left, right)))
        for r, c, is_pass in observations:
            counts[(r, c)][0] += is_pass
            counts[(r, c)][1] += 1
    cells = [{'row': r, 'col': c, 'pass_count': p, 'die_observations': n, 'pass_percent': 100*p/n}
             for (r, c), (p, n) in sorted(counts.items())]
    stats = {'measurement_count': len(rows), 'coordinate_count': len(cells), 'die_observations': total,
            'pooled_die_pass_percent': 100*passed/total if total else None,
            'mean_coordinate_pass_percent': sum(c['pass_percent'] for c in cells)/len(cells) if cells else None}
    if fail_matchers is not None:
        stats['unmatched_cells'] = unmatched
    return stats, cells


def _load_symbol_matchers(records, args):
    from symbol_bin import make_symbol_fail_matcher
    from symbol_bin_db import load_symbol_patterns

    lot_codes = {row.get('lot_cd') or args.lotcd for row in records}
    if args.lotcd:
        lot_codes.add(args.lotcd)
    lot_codes.discard(None)
    if not lot_codes:
        raise ValueError('lot_cd is required for symbol bin matching')
    return {lot_cd: make_symbol_fail_matcher(
        load_symbol_patterns(lot_cd, args.oper, args.symbol_bin_parameters)) for lot_cd in sorted(lot_codes)}


def _symbol_cummap_image(cells, oper):
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    rows = [cell['row'] for cell in cells]
    cols = [cell['col'] for cell in cells]
    grid = np.full((max(rows) - min(rows) + 1, max(cols) - min(cols) + 1), np.nan)
    for cell in cells:
        grid[cell['row'] - min(rows), cell['col'] - min(cols)] = cell['pass_percent']
    fig = Figure(figsize=(8, 7)); FigureCanvasAgg(fig)
    ax = fig.subplots()
    image = ax.imshow(grid, cmap='RdYlGn', vmin=0, vmax=100)
    ax.set_title(f'{oper} cumulative pass percent')
    ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(image, ax=ax, label='Pass percent')
    output = BytesIO(); fig.savefig(output, format='png', dpi=150, bbox_inches='tight')
    return output.getvalue()


def compare_statistics(period_cells):
    base_label, base_rows = period_cells[0]
    base = {(r['row'], r['col']): r for r in base_rows}
    result = []
    for label, rows in period_cells[1:]:
        current = {(r['row'], r['col']): r for r in rows}
        for key in sorted(set(base) | set(current)):
            before, after = base.get(key), current.get(key)
            result.append({'baseline_period': base_label, 'period': label, 'row': key[0], 'col': key[1],
                'baseline_pass_percent': before['pass_percent'] if before else None,
                'current_pass_percent': after['pass_percent'] if after else None,
                'baseline_die_observations': before['die_observations'] if before else 0,
                'current_die_observations': after['die_observations'] if after else 0,
                'delta_pp': after['pass_percent'] - before['pass_percent'] if before and after else None})
    return result


def _fetch_maps(args):
    from map_agent import _query_wafer_data, _query_wafer_data_by_date
    if args.lotcd:
        records = _query_wafer_data_by_date(args.lotcd, args.start_date.strftime('%Y%m%d'),
            args.end_date.strftime('%Y%m%d'), f'{args.oper} TEST', strict=True, limit=None)
        complete = True
    else:
        records = _query_wafer_data(lot_ids=','.join(args.lot_ids) or None, groupkey=args.groupkey,
            wf_ids=','.join(map(str, args.wf_ids)) or None, wf_mod=args.wf_mod, wf_rem=args.wf_rem,
            oper=args.oper, strict=True)
        complete = len(records) < 10000
    records = [r for r in records if (not args.wf_ids or int(r['wf_id']) in args.wf_ids)
               and (not args.wf_mod or int(r['wf_id']) % args.wf_mod == args.wf_rem)]
    return select_measurements(records, args.measurement_policy), complete


def _scope(args, complete):
    symbol_parameters = getattr(args, 'symbol_bin_parameters', [])
    input_scope = args.model_dump(mode='json')
    if not symbol_parameters:
        input_scope.pop('symbol_bin_parameters', None)
    scope = {**input_scope, 'complete': complete, 'interval': '[start_date, end_date)',
        'time_basis': 'end_tm', 'measurement_policy_scope': 'within_query_range; timestamp ties retained',
        'business_retest_policy': 'unconfirmed; explicit query policy used',
        'pass_definition': ('not selected symbol-bin regex union' if symbol_parameters else
                            f'bin != {args.target_bin}' if args.target_bin is not None else 'bin == ?'),
        'mean_coordinate_pass_percent': 'unweighted average of per-coordinate pass percentages (renderer metric)',
        'pooled_die_pass_percent': '100 * total passing die observations / total die observations',
        'missing_reason': None if complete else 'LOT query reached legacy 10000-row cap'}
    if symbol_parameters:
        scope.update({'bin_match': 'left+right', 'calculation_mode': 'symbol_bin_regex'})
    return scope


def _render_map(args):
    from map_agent import _visualize_binmap, _visualize_cummap
    records, complete = _fetch_maps(args)
    matchers = _load_symbol_matchers(records, args) if args.symbol_bin_parameters else None
    stats, cells = map_statistics(records, args.target_bin, args.bin_type, matchers, args.lotcd)
    artifacts = []
    if cells:
        if matchers is not None:
            artifacts.append({'title': args.map_type, 'mime': 'image/png',
                              'data': _symbol_cummap_image(cells, args.oper)})
        elif args.map_type == 'binmap':
            path = _visualize_binmap(records, bin_type=args.bin_type, oper=args.oper)
        else:
            path, _ = _visualize_cummap(records, bin_type=args.bin_type, target_bin=args.target_bin, oper=args.oper)
        if matchers is None:
            if not path:
                raise ValueError('Map rendering failed')
            image = Path(path)
            try:
                artifacts.append({'title': args.map_type, 'mime': 'image/png', 'data': image.read_bytes()})
            finally:
                image.unlink(missing_ok=True)
    reason = None if complete else 'LOT query reached legacy 10000-row cap'
    return ToolResult(tables=[ResultTable(table_id='measurements', title='맵 원본 측정', rows=records, complete=complete, missing_reason=reason),
        ResultTable(table_id='map_summary', title='맵 평균 및 분모', rows=[stats], complete=complete, missing_reason=reason,
                    units={'mean_coordinate_pass_percent': 'percent', 'pooled_die_pass_percent': 'percent'}),
        ResultTable(table_id='coordinates', title='좌표별 pass 비율', rows=cells, complete=complete, missing_reason=reason, units={'pass_percent': 'percent'})],
        artifacts=artifacts, scope=_scope(args, complete), status='partial' if not complete else ('success' if cells else 'empty'),
        summary=f"{len(records)}개 측정 맵. 좌표별 평균과 전체 die 관측 가중 평균을 구분합니다.")


def _difference_image(deltas, label):
    import matplotlib.pyplot as plt
    rows = [r for r in deltas if r['period'] == label and r['delta_pp'] is not None]
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(8, 7))
    try:
        points = ax.scatter([r['col'] for r in rows], [r['row'] for r in rows],
            c=[r['delta_pp'] for r in rows], cmap='RdYlGn', vmin=-100, vmax=100, marker='s')
        ax.invert_yaxis()
        ax.set_aspect('equal')
        ax.set_title(f"{label} minus {rows[0]['baseline_period']} (percentage points)")
        ax.set_xlabel('Col')
        ax.set_ylabel('Row')
        fig.colorbar(points, ax=ax, label='Pass-rate difference (pp)')
        output = BytesIO()
        fig.savefig(output, format='png', dpi=150, bbox_inches='tight')
        return {'title': f'{label} difference', 'mime': 'image/png', 'data': output.getvalue()}
    finally:
        plt.close(fig)


def _compare_maps(args):
    summaries, period_cells, measurements = [], [], []
    for period in args.periods:
        target = MapInput(**period.model_dump(exclude={'label'}), oper=args.oper,
            target_bin=args.target_bin, bin_type=args.bin_type, measurement_policy=args.measurement_policy,
            wf_ids=args.wf_ids, wf_mod=args.wf_mod, wf_rem=args.wf_rem)
        records, _ = _fetch_maps(target)
        stats, cells = map_statistics(records, args.target_bin, args.bin_type)
        summaries.append({'period': period.label, **stats})
        measurements.extend({'period': period.label, **row} for row in records)
        period_cells.append((period.label, cells))
    deltas = compare_statistics(period_cells)
    regions = []
    for period in args.periods[1:]:
        for region in args.regions:
            matching = [r for r in deltas if r['period'] == period.label and region.row_min <= r['row'] <= region.row_max
                        and region.col_min <= r['col'] <= region.col_max]
            shared = [r['delta_pp'] for r in matching if r['delta_pp'] is not None]
            regions.append({'period': period.label, 'baseline_period': args.periods[0].label, 'region': region.label,
                'shared_coordinate_count': len(shared), 'union_coordinate_count': len(matching),
                'mean_delta_pp': sum(shared)/len(shared) if shared else None,
                'denominator': 'shared coordinates only; unweighted'})
    tables = [ResultTable(table_id='measurements', title='기간별 맵 원본', rows=measurements, complete=True),
        ResultTable(table_id='period_summary', title='모든 요청 기간의 맵 평균', rows=summaries, complete=True),
        ResultTable(table_id='coordinate_differences', title='첫 기간 대비 좌표별 차이', rows=deltas, units={'delta_pp': 'percentage_points'}, complete=True),
        ResultTable(table_id='region_differences', title='지정 영역의 공유 좌표 평균 차이', rows=regions, units={'mean_delta_pp': 'percentage_points'}, complete=True)]
    artifacts = [image for p in args.periods[1:] if (image := _difference_image(deltas, p.label))]
    scope = _scope(args, True)
    scope.update({'baseline_period': args.periods[0].label, 'difference': 'current - baseline',
        'missing_coordinates': 'null difference, never zero-filled', 'negative_delta': 'lower pass fraction, not a cause finding'})
    return ToolResult(tables=tables, artifacts=artifacts, scope=scope,
        status='success' if any(r['delta_pp'] is not None for r in deltas) else 'empty',
        summary=f'{len(args.periods)}개 요청 기간을 모두 보존했습니다. 첫 기간 대비 공유 좌표의 pass 차이(pp)를 제공합니다.')


async def render_map(args, ctx):
    return await run_blocking(_render_map, args)


async def compare_wafer_maps(args, ctx):
    return await run_blocking(_compare_maps, args)


def register(registry):
    registry.add(ToolSpec('render_wafer_map', '맵 조회 입력을 받아 좌표별 집계와 이미지를 반환한다. 선택적으로 제품·공정 정의의 정규식을 두 bin 값의 조합에 적용해 fail을 분류한다.', MapInput, render_map))
    registry.add(ToolSpec('compare_wafer_maps', '2~12개 제품/기간 맵 모두 보존. 첫 기간 대비 좌표별 차이(pp), 지정 사각 영역의 공유 좌표 평균 차이와 이미지를 반환.', CompareMapInput, compare_wafer_maps))
