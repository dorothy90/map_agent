from ..runtime.domain_process import run_blocking
from datetime import date
from typing import Literal
from pydantic import Field, model_validator

from ..types import Contract
from .registry import ToolResult, ToolSpec


class YieldInput(Contract):
    lotcd: str = Field(min_length=1, max_length=20)
    ref_date: date = Field(description="조회 기준 날짜, YYYY-MM-DD. weekly/monthly는 기준일이 속한 달력 기간 전체를 포함하므로 기준일 이후 자료도 포함될 수 있다. 정확한 일별 범위는 daily 사용.")
    unit: Literal["weekly", "monthly", "daily"] = "weekly"
    periods: int = Field(default=4, ge=1, le=52)


def register(registry):
    registry.add(ToolSpec("query_wafer_yield", "웨이퍼 ?-bin 수율과 기간별 순위. 특정 불량 bin 수율과 전체 기간 하위 N개는 query_defect_yield 사용.", WaferYieldInput, query_wafer_yield))


class WaferYieldInput(YieldInput):
    oper: Literal['PT1H', 'PT1C'] = 'PT1H'
    metric: Literal['pass_rate']
    parameter: str | None = Field(default=None, min_length=1, max_length=100)
    measurement_policy: Literal['all_measurements', 'latest_per_wafer'] = Field(
        description='Explicit retest choice: all source measurements, or latest timestamp per wafer within each period (ties retained). Business retest policy is unconfirmed.')
    rank_order: Literal['ascending', 'descending'] = 'ascending'

    @model_validator(mode='after')
    def metric_parameter(self):
        if self.metric == 'pass_rate' and self.parameter is not None:
            raise ValueError('pass_rate uses ?-bin passes, not a parameter VALUE')
        return self


def _query_wafer_yield(args):
    from yield_db import _get_period_date_ranges
    from map_agent import _query_wafer_data_by_date
    from ..domain_metrics import metric_metadata, wafer_pass_metric, select_measurements, rank_wafers
    from ..types import ResultTable
    periods = _get_period_date_ranges(args.ref_date, args.unit, args.periods)
    rows = []
    for period in periods:
        records = _query_wafer_data_by_date(args.lotcd, period['start'], period['end'],
            f'{args.oper} TEST', strict=True, limit=None)
        selected = select_measurements(records, args.measurement_policy)
        for row in selected:
            value = wafer_pass_metric(row['map_val_json'])
            rows.append({'period': period['label'], 'lot_id': row['lot_id'], 'wf_id': row['wf_id'],
                'wafer_id': f"{row['lot_id']}.{row['wf_id']}", 'measurement_time': str(row.get('end_tm')),
                'measurement_count': 1, **value})
    ranked = rank_wafers(rows, [p['label'] for p in periods], args.rank_order == 'descending')
    metadata = metric_metadata(args.metric)
    scope = {**args.model_dump(mode='json'), 'metric_definition': metadata, 'period_ranges': periods,
        'time_basis': 'end_tm',
        'interval': '[start, end)', 'complete': True, 'measurement_policy_scope': 'within_each_period',
        'timestamp_ties': 'retained', 'business_retest_policy': 'unconfirmed; explicit query policy used',
        'rank_population': 'all selected measurements, per period; not preview'}
    return ToolResult(tables=[ResultTable(table_id='wafer_ranking', title='웨이퍼 측정값 및 기간별 순위',
        rows=ranked, units={'value': metadata['unit']}, complete=True)], scope=scope,
        status='success' if rows else 'empty', summary=f'{len(rows)}개 측정값의 기간별 순위. 동률과 빈 기간을 보존하며 재측정 선택은 {args.measurement_policy}입니다.')


async def query_wafer_yield(args, ctx):
    return await run_blocking(_query_wafer_yield, args)
