"""Map-based defect yield over a complete period, with optional selected binmaps."""
from collections import Counter
from datetime import date
from io import BytesIO
from typing import Literal

from pydantic import Field, model_validator
from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec


class DefectYieldInput(Contract):
    lotcd: str = Field(min_length=1, max_length=20)
    start_date: date = Field(description='Inclusive period start; same range as the yield investigation.')
    end_date: date = Field(description='Exclusive period end. Map fab-out time is end_tm. Do not include dates beyond the user scope.')
    oper: Literal['PT1H', 'PT1C']
    parameters: list[str] = Field(default_factory=list, max_length=25,
        description='Exact PT1H map parameter names from the existing common.CATEGORY_TO_BIN definition, e.g. TPD. Not WADS report labels. Multiple parameters mean their UNION, yielding one overall bottom-N list, not N per parameter.')
    target_bins: list[str] = Field(default_factory=list, max_length=25,
        description='Alternative to parameters: explicitly verified fail-bin codes for this map/operation. Never infer a bin from parentheses in a WADS label. A is the pass bin and is forbidden here.')
    bin_type: Literal['left_bin', 'right_bin'] = 'left_bin'
    top_n: int = Field(default=10, ge=1, le=50)
    include_binmap: bool = Field(default=False,
        description='True returns a binmap grid of exactly the selected measurements, without a second DB query.')

    @model_validator(mode='after')
    def valid_scope(self):
        from common import CATEGORY_TO_BIN
        if self.end_date <= self.start_date:
            raise ValueError('end_date must follow start_date')
        if bool(self.parameters) == bool(self.target_bins):
            raise ValueError('Supply either parameters or verified target_bins')
        if self.parameters and (self.oper != 'PT1H' or self.bin_type != 'left_bin'):
            raise ValueError('Named mapping is defined only for PT1H left_bin; supply verified target_bins otherwise')
        unknown = set(self.parameters) - set(CATEGORY_TO_BIN)
        if unknown:
            raise ValueError(f'No map definition for {sorted(unknown)}. Supported PT1H parameters: {sorted(CATEGORY_TO_BIN)}. Do not substitute WADS labels.')
        if any(not b or len(b) > 20 or b == 'A' for b in self.target_bins):
            raise ValueError('target_bins must contain nonempty fail-bin codes, excluding A')
        return self


def _binmap(records, selected, bins, oper, bin_type):
    import json
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import ListedColormap, BoundaryNorm
    from matplotlib.patches import Patch
    from matplotlib import colormaps

    index = 2 if bin_type == 'left_bin' else 3
    maps = [[entry.split(',') for entry in json.loads(r['map_val_json'])['MAP']] for r in records]
    codes = sorted({parts[index] for values in maps for parts in values})
    colors = ['#dce8df' if b == 'A' else colormaps['turbo'](i / max(1, len(codes) - 1)) for i, b in enumerate(codes)]
    ncols = min(5, len(records)); nrows = (len(records) + ncols - 1) // ncols
    fig = Figure(figsize=(3.6*ncols, 3.7*nrows + 1.3)); FigureCanvasAgg(fig)
    axes = fig.subplots(nrows, ncols, squeeze=False)
    for ax, values, row in zip(axes.flat, maps, selected):
        ys = [int(p[0]) for p in values]; xs = [int(p[1]) for p in values]
        grid = np.full((max(ys)-min(ys)+1, max(xs)-min(xs)+1), np.nan)
        for p in values:
            grid[int(p[0])-min(ys), int(p[1])-min(xs)] = codes.index(p[index])
        ax.imshow(grid, cmap=ListedColormap(colors), norm=BoundaryNorm(np.arange(-.5, len(codes)+.5), len(codes)))
        ax.set_title(f"{row['selection_order']}. {row['wafer_id']}\nDefect yield {row['defect_yield_percent']:.3f}%", fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
    for ax in list(axes.flat)[len(records):]:
        ax.set_visible(False)
    fig.suptitle(f'{oper} | Fail bins: {", ".join(bins)} | Latest measurement per wafer')
    fig.legend(handles=[Patch(facecolor=c, label=b) for b, c in zip(codes, colors)], loc='lower center', ncol=min(13, len(codes)))
    fig.tight_layout(rect=(0, .08, 1, .94))
    out = BytesIO(); fig.savefig(out, format='png', dpi=120)
    return out.getvalue()


def _query_defect_yield(args):
    import json
    from common import CATEGORY_TO_BIN
    from map_agent import _query_wafer_data_by_date

    bins = sorted(set(args.target_bins or [CATEGORY_TO_BIN[p] for p in args.parameters]))
    source = _query_wafer_data_by_date(args.lotcd, args.start_date.strftime('%Y%m%d'),
        args.end_date.strftime('%Y%m%d'), f'{args.oper} TEST', strict=True, limit=None)
    latest = {}
    # Inspect newest records first: an old conflicting tie cannot invalidate
    # a newer unambiguous measurement selected by this policy.
    for record in sorted(source, key=lambda r: str(r['end_tm']), reverse=True):
        key = (record['lot_id'], int(record['wf_id']))
        previous = latest.get(key)
        if previous and str(record['end_tm']) == str(previous['end_tm']) and record['map_val_json'] != previous['map_val_json']:
            raise ValueError(f'Conflicting same-time measurements for {key}; cannot select one silently')
        if previous is None or str(record['end_tm']) > str(previous['end_tm']):
            latest[key] = record
    ranked = []; empty = 0
    for (lot, wf), record in latest.items():
        entries = json.loads(record['map_val_json'])['MAP']
        if not entries:
            empty += 1
            continue
        index = 2 if args.bin_type == 'left_bin' else 3
        counts = Counter(); coords = set()
        for entry in entries:
            parts = entry.split(',')
            if len(parts) <= index or not parts[index]:
                raise ValueError('Map contains a die without the selected bin value')
            coord = (int(parts[0]), int(parts[1]))
            if coord in coords:
                raise ValueError('Map contains duplicate die coordinates')
            coords.add(coord); counts[parts[index]] += 1
        n = sum(counts.values()); failed = sum(counts[b] for b in bins)
        ranked.append({'lot_id': lot, 'wf_id': wf, 'wafer_id': f'{lot}.{wf:02d}',
            'oper': args.oper, 'measurement_time': str(record['end_tm']),
            'die_count': n, 'defect_die_count': failed, 'defect_rate_percent': 100*failed/n,
            'defect_yield_percent': 100*(n-failed)/n, 'overall_pass_percent': 100*counts['A']/n})
    ranked.sort(key=lambda r: (r['defect_yield_percent'], r['wafer_id']))
    dense = {v:i+1 for i,v in enumerate(sorted({r['defect_yield_percent'] for r in ranked}))}
    for order, row in enumerate(ranked, 1):
        row.update(rank=dense[row['defect_yield_percent']], selection_order=order)
    selected = ranked[:args.top_n]
    cutoff = selected[-1]['defect_yield_percent'] if selected else None
    complete = not empty
    reason = f'{empty} wafers have empty maps and cannot be ranked' if empty else None
    artifacts = []
    if args.include_binmap and selected:
        records = [latest[(r['lot_id'], r['wf_id'])] for r in selected]
        artifacts.append({'title': f'{args.oper} defect yield bottom {len(selected)} binmap',
            'mime': 'image/png', 'data': _binmap(records, selected, bins, args.oper, args.bin_type)})
    scope = {**args.model_dump(mode='json'), 'target_bins': bins, 'time_basis': 'end_tm',
        'mapping_source': 'common.CATEGORY_TO_BIN:PT1H:left_bin' if args.parameters else 'explicit_target_bins',
        'interval': '[start_date,end_date)', 'rank_population': 'entire_query_range',
        'metric': '100 * (all dies - union of selected fail-bin dies) / all dies',
        'measurement_policy': 'latest_per_wafer_within_full_range; identical ties deduplicated; conflicting ties rejected',
        'source_measurement_count': len(source), 'population_wafer_count': len(latest),
        'excluded_empty_maps': empty, 'complete': complete, 'missing_reason': reason,
        'cutoff_yield_percent': cutoff, 'cutoff_tie_count': sum(r['defect_yield_percent'] == cutoff for r in ranked),
        'tie_policy': 'dense ranks; exactly top_n by yield then wafer_id, or fewer if unavailable',
        'rendered_wafer_ids': [r['wafer_id'] for r in selected] if artifacts else []}
    units = {'defect_rate_percent': 'percent', 'defect_yield_percent': 'percent',
             'overall_pass_percent': 'percent', 'die_count': 'dies', 'defect_die_count': 'dies'}
    return ToolResult(tables=[ResultTable(table_id='wafer_ranking', title='전체 wafer 특정 불량 수율 순위', rows=ranked,
        units=units, complete=complete, missing_reason=reason), ResultTable(table_id='selected_wafers', title='선정 wafer', rows=selected,
        units=units, complete=complete, missing_reason=reason)], scope=scope, artifacts=artifacts,
        status='partial' if empty else 'success' if ranked else 'empty',
        summary=f'{args.oper} 전체 {len(latest)}개 wafer 중 불량 bin {bins} 합집합 기준 하위 {len(selected)}개 선정. 전체 A-bin 수율과 별도 지표입니다.' + (f' {reason}.' if reason else ''))


async def query_defect_yield(args, ctx):
    return await run_blocking(_query_defect_yield, args)


def register(registry):
    registry.add(ToolSpec('query_defect_yield',
        '특정 불량의 wafer별 수율(해당 fail-bin 제외 비율)과 전체 기간 하위 N개를 조회한다. 여러 불량은 합집합으로 총 N개 선정. include_binmap=true면 같은 측정 건의 binmap도 반환. PT1H 맵 파라미터 이름 또는 검증된 bin 코드를 사용하며 WADS 라벨을 추측 변환하지 않는다.',
        DefectYieldInput, query_defect_yield, eager=True))
