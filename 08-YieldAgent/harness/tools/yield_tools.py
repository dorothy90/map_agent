from ..runtime.domain_process import run_blocking
from datetime import date
from typing import Literal
from pydantic import Field, model_validator

from ..types import Contract
from .registry import ToolResult, ToolSpec


class YieldInput(Contract):
    lotcd: str = Field(min_length=1, max_length=20)
    ref_date: date = Field(description="조회 기준 날짜, YYYY-MM-DD")
    unit: Literal["weekly", "monthly", "daily"] = "weekly"
    periods: int = Field(default=4, ge=1, le=52)


def _fetch_and_render(lotcd, ref_date, unit, periods):
    from yield_db import _fetch_periods
    from yield_viz import _build_html_table
    rows = _fetch_periods(lotcd, ref_date, unit, periods, strict=True)
    return rows, _build_html_table(rows, lotcd, unit=unit)


async def query_yield(args, context):
    rows, html = await run_blocking(_fetch_and_render, args.lotcd, args.ref_date, args.unit, args.periods)
    populated = any(row.get(key) not in (None, "-", 0, "0") for row in rows for key in ("lotcount", "pt1c_lotcount", "gms_lotcount"))
    # Oracle VALUE has no unit metadata in this query; do not infer a percent
    # conversion from the magnitude. GMS already supplies percentage values.
    scope = {**args.model_dump(mode="json"), "value_units": {
        "PT1H": "source_value_unit_unspecified", "PT1C": "source_value_unit_unspecified", "GMS": "percent"}}
    return ToolResult(rows=rows, scope=scope, status="success" if populated else "empty", summary=f"{args.lotcd} {args.unit} {len(rows)}개 기간 조회. 전체 PT1H/PT1C/GMS 수율표를 산출물로 제공했습니다. PT1H/PT1C는 원본값(단위 미확정), GMS는 %입니다.",
        artifacts=[{"title": f"{args.lotcd} 수율표", "mime": "text/html", "data": html}])


def register(registry):
    registry.add(ToolSpec("inspect_yield_coverage", "제품 미지정 탐색 가능. 파라미터 자료의 제품/공정별 최초·최신 측정 시점과 행 수를 조회한다.", CoverageInput, inspect_yield_coverage))
    registry.add(ToolSpec("query_wafer_yield", "전체 기간 모집단에서 웨이퍼 측정값과 동률 보존 순위를 조회. A-bin 전체 pass%와 파라미터 VALUE를 구분하고 재측정 정책을 명시해야 한다.", WaferYieldInput, query_wafer_yield))
    registry.add(ToolSpec("analyze_yield", "기존 수율 지표 방향에 따라 최신 두 기간 상대 변화 상위 항목과 표본 수를 계산한다. 원인/통계적 이상 검정이 아니다.", YieldInput, analyze_yield))
    registry.add(ToolSpec("render_yield_scatter", "기존 측정 산점도 생성. 기간별 파라미터 행 제한이 있는 표본이며 전체 순위용이 아니다.", YieldInput, render_yield_scatter))
    registry.add(ToolSpec("query_yield", "제품별 PT1H/PT1C/GMS 수율과 표본 수를 기간 단위로 조회하고 전체 HTML 수율표 산출물도 생성한다.", YieldInput, query_yield))


class CoverageInput(Contract):
    lotcd: str | None = Field(default=None, min_length=1, max_length=20)


class WaferYieldInput(YieldInput):
    oper: Literal['PT1H', 'PT1C'] = 'PT1H'
    metric: Literal['pass_rate', 'parameter']
    parameter: str | None = Field(default=None, min_length=1, max_length=100)
    measurement_policy: Literal['all_measurements', 'latest_per_wafer'] = Field(
        description='Explicit retest choice: all source measurements, or latest timestamp per wafer within each period (ties retained). Business retest policy is unconfirmed.')
    rank_order: Literal['ascending', 'descending'] = 'ascending'

    @model_validator(mode='after')
    def metric_parameter(self):
        if self.metric == 'parameter' and self.parameter is None:
            raise ValueError('parameter is required for the parameter metric')
        if self.metric == 'pass_rate' and self.parameter is not None:
            raise ValueError('pass_rate uses A-bin passes, not a parameter VALUE')
        return self


async def inspect_yield_coverage(args, ctx):
    from yield_db import _inspect_yield_coverage
    rows = await run_blocking(_inspect_yield_coverage, args.lotcd)
    return ToolResult(rows=rows, scope={**args.model_dump(), 'complete': True,
        'source': 'DF_DIE_TO_WF_YLD', 'time_basis': 'MEASURETIME_START', 'count_unit': 'parameter rows'},
        summary='제품/공정별 최초·최신 파라미터 측정 시점. 맵/GMS 최신 시점과는 별도입니다.', status='success' if rows else 'empty')


def _query_wafer_yield(args):
    from yield_db import _get_period_date_ranges, _fetch_wafer_values
    from map_agent import _query_wafer_data_by_date
    from ..domain_metrics import metric_metadata, wafer_pass_metric, select_measurements, rank_wafers
    from ..types import ResultTable
    periods = _get_period_date_ranges(args.ref_date, args.unit, args.periods)
    rows = []
    for period in periods:
        if args.metric == 'pass_rate':
            records = _query_wafer_data_by_date(args.lotcd, period['start'], period['end'],
                f'{args.oper} TEST', strict=True, limit=None)
        else:
            records = _fetch_wafer_values(args.lotcd, period['start'], period['end'], args.oper, args.parameter)
        selected = select_measurements(records, args.measurement_policy)
        for row in selected:
            value = wafer_pass_metric(row['map_val_json']) if args.metric == 'pass_rate' else {'value': row['value']}
            rows.append({'period': period['label'], 'lot_id': row['lot_id'], 'wf_id': row['wf_id'],
                'wafer_id': f"{row['lot_id']}.{row['wf_id']}", 'measurement_time': str(row.get('end_tm')),
                'measurement_count': 1, **value})
    ranked = rank_wafers(rows, [p['label'] for p in periods], args.rank_order == 'descending')
    metadata = metric_metadata(args.metric)
    scope = {**args.model_dump(mode='json'), 'metric_definition': metadata, 'period_ranges': periods,
        'time_basis': 'end_tm' if args.metric == 'pass_rate' else 'MEASURETIME_START',
        'interval': '[start, end)', 'complete': True, 'measurement_policy_scope': 'within_each_period',
        'timestamp_ties': 'retained', 'business_retest_policy': 'unconfirmed; explicit query policy used',
        'rank_population': 'all selected measurements, per period; not preview'}
    return ToolResult(tables=[ResultTable(table_id='wafer_ranking', title='웨이퍼 측정값 및 기간별 순위',
        rows=ranked, units={'value': metadata['unit']}, complete=True)], scope=scope,
        status='success' if rows else 'empty', summary=f'{len(rows)}개 측정값의 기간별 순위. 동률과 빈 기간을 보존하며 재측정 선택은 {args.measurement_policy}입니다.')


async def query_wafer_yield(args, ctx):
    return await run_blocking(_query_wafer_yield, args)


def _analyze_yield(args):
    from yield_db import _fetch_periods
    from yield_viz import _detect_anomalies
    from common import HIGHER_IS_BETTER, PARA_COLUMNS, PT1C_COLUMNS
    import math
    from ..types import ResultTable
    rows = _fetch_periods(args.lotcd, args.ref_date, args.unit, args.periods, strict=True)
    anomalies = _detect_anomalies(rows)
    comparable = []
    if len(rows) >= 2:
        for key in [*PARA_COLUMNS, *('pt1c_' + p for p in PT1C_COLUMNS)]:
            try:
                previous, current = float(rows[-2].get(key)), float(rows[-1].get(key))
                if math.isfinite(previous) and math.isfinite(current) and abs(previous) >= 1e-15:
                    comparable.append(key)
            except (TypeError, ValueError):
                continue
    analysis_status = 'findings' if anomalies else 'no_findings' if comparable else 'partial'

    for anomaly in anomalies:
        prefix = '' if anomaly['process'] == 'PT1H' else 'pt1c_'
        anomaly['prev_wafer_count'] = rows[-2].get(prefix + 'wfCount')
        anomaly['curr_wafer_count'] = rows[-1].get(prefix + 'wfCount')
        anomaly['metric_direction'] = 'higher_is_better' if anomaly['param'] in HIGHER_IS_BETTER else 'lower_is_better'
    return ToolResult(tables=[ResultTable(table_id='period_values', title='기간별 원본 집계', rows=rows, complete=True),
        ResultTable(table_id='changes', title='최신 두 기간의 상대 변화', rows=anomalies, units={'change_pct': 'percent'}, complete=True)],
        scope={**args.model_dump(mode='json'), 'comparison': 'last_two_requested_periods', 'analysis_status': analysis_status,
            'comparable_metrics': comparable,
            'formula': '(current - previous) / abs(previous) * 100', 'threshold': None,
            'selection': 'existing top N improvement and deterioration by absolute relative change',
            'direction_source': 'common.HIGHER_IS_BETTER; existing domain convention',
            'zero_or_missing_previous': 'excluded; relative change undefined',
            'aggregation': 'source AVG(VALUE), all source measurement rows; retest not deduplicated'},
        summary='최신 두 요청 기간의 상대 변화 상위 항목입니다. 통계적 이상 임계값 검정이나 원인 확정 결과는 아닙니다.',
        status='success' if anomalies else 'empty')


async def analyze_yield(args, ctx):
    return await run_blocking(_analyze_yield, args)


def _render_yield_scatter(args):
    from yield_db import _fetch_wafer_scatter
    from yield_viz import _build_scatter_html
    from ..types import ResultTable
    rows = []
    for process in ('PT1H', 'PT1C'):
        rows.extend({**r, 'process': process} for r in _fetch_wafer_scatter(args.lotcd, args.ref_date, args.unit, args.periods, process.lower(), strict=True))
    html = _build_scatter_html(rows, [])
    reason = 'Legacy scatter query caps parameter rows per period; this is not a complete wafer population.'
    return ToolResult(tables=[ResultTable(table_id='scatter_measurements', title='산점도 측정 표본', rows=rows,
        complete=False, missing_reason=reason, units={'value': 'source_value_unit_unspecified'})],
        scope={**args.model_dump(mode='json'), 'complete': False, 'missing_reason': reason,
            'per_process_period_parameter_row_limit': max(10000 // args.periods, 2000)},
        artifacts=[{'title': '수율 산점도', 'mime': 'text/html', 'data': html}] if html else [],
        status='partial' if rows else 'empty', summary='제한된 측정 표본 산점도. 전체 최저값/전체 웨이퍼 순위의 근거로 사용할 수 없습니다.')


async def render_yield_scatter(args, ctx):
    return await run_blocking(_render_yield_scatter, args)
