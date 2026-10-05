from ..runtime.domain_process import run_blocking
import hashlib
import json
from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec


class WadsInput(Contract):
    lotcd: str | None = Field(default=None, min_length=1, max_length=20)
    start_tm: date | None = None
    end_tm: date | None = None
    parameter: str | None = None
    category: Literal['PT1H', 'PT1C'] | None = None
    limit: int = Field(default=5000, ge=1, le=100000, description='최신순 연결 행 상한. 잘린 결과는 전체 통계가 아니다.')

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.start_tm and self.end_tm:
            if self.end_tm < self.start_tm:
                raise ValueError('end_tm precedes start_tm')
            if (self.end_tm - self.start_tm).days > 366:
                raise ValueError('Date range exceeds one year')
        return self


class WadsReportInput(WadsInput):
    report_id: str | None = None
    source_result_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def exact_source(self):
        if bool(self.report_id) != bool(self.source_result_id):
            raise ValueError('report_id and source_result_id must be provided together')
        return self


def report_id(row):
    from wads_tools import _report_key
    return 'wads_' + hashlib.sha256(json.dumps(_report_key(row), ensure_ascii=False).encode()).hexdigest()[:24]


def filters(args):
    return args.model_dump(mode='json', exclude_none=True, exclude={'limit', 'report_id', 'source_result_id'})


async def query_wads(args, context):
    from wads_tools import _query_wads_data, _end_tm_expr, _wf_groupkey_expr, _wads_join_coverage, _split_groupkey, _wafer_groups_for_reports, _report_key
    params = filters(args)
    frame = await run_blocking(_query_wads_data, **params, join_wafers=True, row_limit=args.limit+1,
        columns=f"r.LOTCD, r.CATEGORY, r.PARAMETER, {_end_tm_expr('r')}, {_wf_groupkey_expr()}")
    complete = len(frame) <= args.limit
    frame = frame.iloc[:args.limit]
    coverage = _wads_join_coverage(frame)
    rows = json.loads(frame.to_json(orient='records', date_format='iso'))
    reports, wafers = {}, {}
    groups = _wafer_groups_for_reports(lotcd=None, start_tm=None, end_tm=None, parameter=None, wafer_df=frame)
    for row in rows:
        ident = report_id(row)
        reports.setdefault(ident, dict(report_id=ident, **{k: row.get(k) for k in ('lotcd','category','parameter','end_tm')}))
        for groupkey in groups.get(_report_key(row), {}).get('groupkeys', []):
            lot, wafer = _split_groupkey(groupkey)
            wafers[(ident, groupkey)] = dict(report_id=ident, groupkey=groupkey, lot_id=lot, wf_id=wafer)
    connected_reports = {key[0] for key in wafers}
    coverage['reports_without_wafers'] = len(set(reports) - connected_reports)
    coverage['missing_report_ratio'] = coverage['reports_without_wafers'] / len(reports) if reports else 0.0
    missing = coverage['missing_groupkey_rows'] > 0
    reason = None if complete else 'Latest joined row limit reached; report and wafer population may be incomplete.'
    return ToolResult(tables=[
        ResultTable(table_id='reports', title='고유 WADS 보고서', rows=list(reports.values()), complete=complete, missing_reason=reason),
        ResultTable(table_id='report_wafers', title='보고서와 웨이퍼 연결', rows=list(wafers.values()), complete=complete and not missing, missing_reason='Some reports have no GROUPKEY match.' if missing else reason),
        ResultTable(table_id='join_coverage', title='웨이퍼 조인 범위', rows=[coverage], complete=complete, missing_reason=reason)],
        scope=dict(params, row_limit=args.limit, order='END_TM DESC', complete=complete, join_semantics='Existing LOT_CD/OPER_PARA/END_TM date join; same-day reports can share wafer population.'),
        summary=f'고유 보고서 {len(reports)}개, 보고서-웨이퍼 연결 {len(wafers)}개' + (' (제한된 최신 표본)' if not complete else ''))


async def inspect_wads_coverage(args, context):
    result = await query_wads(args, context)
    reports = result.tables[0]
    products = {}
    for row in reports.rows:
        bucket = products.setdefault(row['lotcd'], {'lotcd': row['lotcd'], 'report_count': 0, 'latest_issuance': row['end_tm']})
        bucket['report_count'] += 1
        bucket['latest_issuance'] = max(bucket['latest_issuance'], row['end_tm'])
    result.tables.append(ResultTable(table_id='products', title='제품별 발행 범위', rows=list(products.values()), complete=reports.complete, missing_reason=reports.missing_reason))
    return result


def register(registry):
    registry.add(ToolSpec('query_wads', 'WADS 최신순 보고서·웨이퍼·조인 누락을 분리 조회한다. 제품/기간 생략 가능. reports의 고유 report_id와 result_id로 정확한 원문을 읽는다. limit 초과는 전체 통계가 아니다.', WadsInput, query_wads))
    registry.add(ToolSpec('inspect_wads_coverage', '제품/기간 미지정으로 WADS 제품 목록과 최신 발행 시각을 탐색한다. 최신순 행 제한과 complete를 확인하고 필요시 제품/기간을 좁힌다.', WadsInput, inspect_wads_coverage))
