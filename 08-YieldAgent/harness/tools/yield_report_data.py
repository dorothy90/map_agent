"""Product-scoped SQL aggregates; recent windows use DB measurement dates only."""
import re
from calendar import monthrange
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from ..types import Contract

Grain = Literal['month', 'week', 'day']
GRAINS = {'month': ('months', 'monthly'), 'week': ('weeks', 'weekly'), 'day': ('days', 'daily')}
MEASURE_DATE = "TO_DATE(YEAR||LPAD(MONTH,2,'0')||LPAD(DAY,2,'0'),'YYYYMMDD')"
BASE_DATE_SOURCE = f"MAX({MEASURE_DATE}) FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST'; measurement date, not LAST_UPDATE or host clock"


class RecentPeriod(Contract):
    kind: Literal['recent']
    weeks: int | None = Field(
        default=None, ge=1, le=26, strict=True,
        description='최근 N주 → weeks:N. PT1H TEST 최신 DB 측정일을 포함한 DB 주 단위다. '
                    '최근 4주 → {kind:"recent",weeks:4}; days:28로 환산하지 않는다.',
    )
    months: int | None = Field(
        default=None, ge=1, le=24, strict=True,
        description='최근 N개월/N달 → months:N. PT1H TEST 최신 DB 측정일을 포함한 DB 월 단위다. '
                    '최근 3개월 → months:3; days:90이나 weeks:12로 환산하지 않는다.',
    )
    days: int | None = Field(
        default=None, ge=1, le=92, strict=True,
        description='최근 N일 → days:N. PT1H TEST 최신 DB 측정일을 포함한 DB 일 단위다. '
                    '사용자가 말한 최근 28일은 days:28로 허용하며 주·개월을 일로 환산하지 않는다.',
    )
    grains: list[Grain] | None = Field(
        default=None, min_length=1, max_length=3,
        description='주별/월별/일별 표시 집계 단위(week/month/day)이며 기간 단위가 아니다. '
                    '최근 4주 주별 → {kind:"recent",weeks:4,grains:["week"]}.',
    )

    @model_validator(mode='after')
    def one_window(self):
        if sum(getattr(self, key) is not None for key, _ in GRAINS.values()) != 1:
            raise ValueError('recent requires exactly one of weeks/months/days to define one window')
        if self.grains is not None and len(set(self.grains)) != len(self.grains):
            raise ValueError('duplicate grain')
        return self


def _normalize_buckets(values, grain):
    normalized = []
    for value in values:
        value = value.strip()
        if grain == 'week':
            match = re.fullmatch(r'(\d{4})(?:-W?|-)?(\d{2})', value, re.IGNORECASE)
            if not match:
                raise ValueError('week must be YYYY-Www, YYYY-WW, or YYYYWW')
            year, week = map(int, match.groups())
            date.fromisocalendar(year, week, 1)
            normalized.append(f'{year:04d}-{week:02d}')
        else:
            fmt = r'\d{4}-\d{2}' if grain == 'month' else r'\d{4}-\d{2}-\d{2}'
            length = 6 if grain == 'month' else 8
            if re.fullmatch(r'\d{' + str(length) + '}', value):
                value = value[:4] + '-' + value[4:6] + ('-' + value[6:] if grain == 'day' else '')
            if not re.fullmatch(fmt, value):
                raise ValueError('invalid calendar bucket')
            date.fromisoformat(value + '-01' if grain == 'month' else value)
            normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError('duplicate bucket')
    return sorted(normalized)


class ExplicitWeeks(Contract):
    kind: Literal['weeks']
    weeks: list[str] = Field(min_length=1, max_length=26)

    @field_validator('weeks')
    @classmethod
    def normalized(cls, values):
        return _normalize_buckets(values, 'week')


class ExplicitMonths(Contract):
    kind: Literal['months']
    months: list[str] = Field(min_length=1, max_length=24)

    @field_validator('months')
    @classmethod
    def normalized(cls, values):
        return _normalize_buckets(values, 'month')


class ExplicitDays(Contract):
    kind: Literal['days']
    days: list[str] = Field(min_length=1, max_length=92)

    @field_validator('days')
    @classmethod
    def normalized(cls, values):
        return _normalize_buckets(values, 'day')


ReportPeriod = Annotated[RecentPeriod | ExplicitWeeks | ExplicitMonths | ExplicitDays, Field(discriminator='kind')]


def _bucket(day, grain):
    if grain == 'week':
        year, week, _ = day.isocalendar()
        return f'{year:04d}-{week:02d}'
    return day.isoformat()[:7] if grain == 'month' else day.isoformat()


def _bounds(bucket, grain):
    if grain == 'week':
        start = date.fromisocalendar(*map(int, bucket.split('-')), 1)
        return start, start + timedelta(days=6)
    start = date.fromisoformat(bucket + '-01' if grain == 'month' else bucket)
    return start, start.replace(day=monthrange(start.year, start.month)[1]) if grain == 'month' else start


def resolve_window(period, base: date | None = None):
    """DB MAX measurement date anchors consecutive buckets, including empty ones.

    Weeks are ISO Monday-start (Oracle IYYY-IW). Combined grains overlap the
    same closed date interval; leading/trailing weeks are clipped for aggregates.
    Explicit nonconsecutive buckets keep their gaps excluded via SQL IN.
    """
    if isinstance(period, RecentPeriod):
        if base is None:
            raise ValueError('No PT1H measurement date for this product')
        anchor = next(g for g, (key, _) in GRAINS.items() if getattr(period, key) is not None)
        count = getattr(period, GRAINS[anchor][0])
        if anchor == 'month':
            index = base.year * 12 + base.month - count
            start = date(index // 12, index % 12 + 1, 1)
        elif anchor == 'week':
            start = base - timedelta(days=base.weekday(), weeks=count - 1)
        else:
            start = base - timedelta(days=count - 1)
        end = base
        grains = period.grains or [anchor]
        buckets = {key: list(dict.fromkeys(_bucket(start + timedelta(days=i), grain)
                                         for i in range((end - start).days + 1)))
                   for grain, (key, _) in GRAINS.items() if grain in grains}
    else:
        grain = next(g for g, (key, _) in GRAINS.items() if key == period.kind)
        values = getattr(period, period.kind)
        buckets = {period.kind: values}
        start, end = _bounds(values[0], grain)[0], _bounds(values[-1], grain)[1]
    return {'start': start.isoformat(), 'end': end.isoformat(), **buckets,
            'base_date': base.isoformat() if base else None,
            'base_date_source': BASE_DATE_SOURCE if base else 'explicit user bucket list; no DB anchor',
            'selection': 'closed date range' if isinstance(period, RecentPeriod) else 'listed buckets only; gaps excluded'}


@contextmanager
def _report_cursor():
    from .query_db import _connection
    connection = cursor = None
    try:
        connection = _connection()
        connection.call_timeout = 60_000
        cursor = connection.cursor()
        yield cursor
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()


def _parameter_metadata(cursor, lot_cd):
    cursor.execute(
        "SELECT PROC_GBN_CD, PARAM_NM, PARAM_SEQNO, PARAM_KIND_CD, YIELD_TABLE FROM DF_ALL_PARA_PRB "
        "WHERE LOT_CD=:lot "
        "ORDER BY CASE PROC_GBN_CD WHEN 'PT1H' THEN 0 WHEN 'PT1C' THEN 1 ELSE 2 END, PARAM_SEQNO",
        {'lot': lot_cd},
    )
    return cursor.fetchall()


def fetch_parameter_metadata(lot_cd: str) -> list[tuple]:
    with _report_cursor() as cursor:
        return _parameter_metadata(cursor, lot_cd)


@dataclass(frozen=True)
class PeriodReportData:
    lot_cd: str
    window: dict
    metadata: list[tuple]
    yield_long: list[dict]
    counts: dict[str, list[dict]]
    period_lot_count: int
    period_wafer_count: int
    gms: dict[str, list[dict]]
    lot_population: list[dict] = field(default_factory=list)
    lot_parameter_stats: list[dict] = field(default_factory=list)
    lot_wafer_members: list[dict] = field(default_factory=list)


def _bucket_sql(grain, prefix=''):
    if grain == 'week':
        return prefix + 'WEEK_HYNIX'
    value = f"{prefix}YEAR||'-'||LPAD({prefix}MONTH,2,'0')"
    return value + (f"||'-'||LPAD({prefix}DAY,2,'0')" if grain == 'day' else '')


def fetch_period_report(lot_cd: str, period) -> PeriodReportData:
    with _report_cursor() as cursor:
        base = None
        if isinstance(period, RecentPeriod):
            cursor.execute('SELECT ' + BASE_DATE_SOURCE.split(';')[0], {'lot': lot_cd})
            base = cursor.fetchall()[0][0]
            if base is None:
                raise ValueError('No PT1H measurement date for this product')
            if isinstance(base, datetime):
                base = base.date()
        window = resolve_window(period, base)
        # :end is an Oracle reserved bind name (ORA-01745); use win_start/win_end.
        binds = {'lot': lot_cd, 'win_start': window['start'], 'win_end': window['end']}
        date_filter = f"{MEASURE_DATE} BETWEEN TO_DATE(:win_start,'YYYY-MM-DD') AND TO_DATE(:win_end,'YYYY-MM-DD')"
        total_filter = date_filter
        if not isinstance(period, RecentPeriod):
            grain = next(g for g, (key, _) in GRAINS.items() if key == period.kind)
            values = window[period.kind]
            binds.update({f'b{i}': value for i, value in enumerate(values)})
            total_filter += f" AND {_bucket_sql(grain)} IN ({', '.join(f':b{i}' for i in range(len(values)))})"
        metadata = _parameter_metadata(cursor, lot_cd)
        cursor.execute(
            "SELECT COUNT(DISTINCT LOT_ID), COUNT(DISTINCT GORUPKEY) "
            "FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST' AND " + total_filter, binds)
        lot_count, wafer_count = cursor.fetchall()[0]
        # Capture complete additive statistics and distinct identities while the
        # existing report connection is open. No later group query is necessary.
        population, stats, members = _capture_lots(cursor, total_filter, binds)
        yield_long, all_counts, all_gms = [], {}, {}
        for grain, (key, unit) in GRAINS.items():
            if key not in window:
                continue
            buckets = window[key]
            placeholders = ', '.join(f':b{i}' for i in range(len(buckets)))
            grain_binds = {'lot': lot_cd, 'win_start': window['start'], 'win_end': window['end'],
                           **{f'b{i}': value for i, value in enumerate(buckets)}}
            bucket_sql = _bucket_sql(grain)
            _, bucket_stats, bucket_members = _capture_lots(
                cursor, f"{date_filter} AND {bucket_sql} IN ({placeholders})",
                grain_binds, grain=grain)
            stats.extend(bucket_stats)
            members.extend(bucket_members)
            # A single source alias leaves YEAR/MONTH/DAY unambiguous in this predicate.
            cursor.execute(
                f"SELECT {_bucket_sql(grain, 'y.')}, y.PROCESS, y.PARAM_NM, AVG(y.VALUE) "
                "FROM DF_WF_ALL_PRB_V2 y WHERE y.LOT_CD=:lot "
                "AND y.PROCESS IN ('PT1H TEST','PT1C TEST') "
                f"AND {date_filter} AND {_bucket_sql(grain, 'y.')} IN ({placeholders}) "
                "AND EXISTS (SELECT 1 FROM DF_ALL_PARA_PRB m WHERE m.LOT_CD=y.LOT_CD "
                "AND m.PROC_GBN_CD||' TEST'=y.PROCESS AND m.PARAM_NM=y.PARAM_NM "
                "AND TRIM(m.YIELD_TABLE)='o') "
                f"GROUP BY {_bucket_sql(grain, 'y.')}, y.PROCESS, y.PARAM_NM "
                f"ORDER BY {_bucket_sql(grain, 'y.')}, y.PROCESS, y.PARAM_NM", grain_binds)
            rows = [dict(grain=grain, bucket=row[0], **dict(zip((grain, 'process', 'param', 'value'), row)))
                    for row in cursor.fetchall()]
            yield_long.extend(rows)
            cursor.execute(
                f"SELECT {bucket_sql}, COUNT(DISTINCT LOT_ID), COUNT(DISTINCT GORUPKEY) "
                "FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST' "
                f"AND {date_filter} AND {bucket_sql} IN ({placeholders}) "
                f"GROUP BY {bucket_sql} ORDER BY {bucket_sql}", grain_binds)
            counts = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD "
                f"FROM DF_GMS_YIELD_{unit.upper()} WHERE LOTCODE=:lot "
                f"AND PERIOD_DATE IN ({placeholders}) ORDER BY PERIOD_DATE",
                {'lot': lot_cd, **{f'b{i}': bucket.replace('-', '') for i, bucket in enumerate(buckets)}})
            by_period = {b.replace('-', ''): b for b in buckets}
            gms = [dict(**{grain: by_period[str(row[0])]},
                        **dict(zip(('period_date', 'fab', 'prb', 'cum0'), row))) for row in cursor.fetchall()]
            yield_buckets, gms_buckets = {r['bucket'] for r in rows}, {r[grain] for r in gms}
            all_counts[grain] = [dict(**{grain: b}, lot_count=counts.get(b, (None, None))[0],
                                      wafer_count=counts.get(b, (None, None))[1],
                                      has_yield_data=b in yield_buckets, has_gms=b in gms_buckets) for b in buckets]
            all_gms[grain] = gms
    return PeriodReportData(lot_cd, window, metadata, yield_long, all_counts, lot_count, wafer_count, all_gms, population, stats, members)


def _capture_lots(cursor, predicate, binds, *, grain='all'):
    bucket = "''" if grain == 'all' else _bucket_sql(grain)
    population = []
    if grain == 'all':
        cursor.execute(
            "/* report_lot_population */ SELECT LOT_ID, COUNT(DISTINCT GORUPKEY), "
            "AVG(CASE WHEN PARAM_NM='PT1H' THEN VALUE END), "
            "COUNT(CASE WHEN PARAM_NM='PT1H' THEN VALUE END) "
            "FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST' AND "
            + predicate + " GROUP BY LOT_ID ORDER BY LOT_ID", binds)
        population = [dict(lot_id=row[0], wafer_count=int(row[1]),
                           pt1h_mean=float(row[2]) if row[2] is not None else None,
                           pt1h_value_count=int(row[3])) for row in cursor.fetchall() if row[0] is not None]
    cursor.execute(
        f"/* report_lot_stats */ SELECT {bucket}, y.LOT_ID, y.PROCESS, y.PARAM_NM, SUM(y.VALUE), COUNT(y.VALUE) "
        "FROM DF_WF_ALL_PRB_V2 y WHERE y.LOT_CD=:lot "
        "AND y.PROCESS IN ('PT1H TEST','PT1C TEST') AND " + predicate +
        " AND ((y.PROCESS='PT1H TEST' AND y.PARAM_NM='PT1H') OR EXISTS "
        "(SELECT 1 FROM DF_ALL_PARA_PRB m WHERE m.LOT_CD=y.LOT_CD "
        "AND m.PROC_GBN_CD||' TEST'=y.PROCESS AND m.PARAM_NM=y.PARAM_NM "
        "AND TRIM(m.YIELD_TABLE)='o')) "
        + ("GROUP BY " if grain == 'all' else f"GROUP BY {bucket}, ")
        + "y.LOT_ID, y.PROCESS, y.PARAM_NM", binds)
    stats = [dict(grain=grain, bucket=row[0] or '', lot_id=row[1], process=row[2],
                  param=row[3], value_sum=float(row[4]) if row[4] is not None else 0.0,
                  value_count=int(row[5])) for row in cursor.fetchall()]
    cursor.execute(
        f"/* report_lot_members */ SELECT DISTINCT {bucket}, LOT_ID, GORUPKEY "
        "FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST' AND "
        + predicate, binds)
    members = [dict(grain=grain, bucket=row[0] or '', lot_id=row[1], gorupkey=row[2])
               for row in cursor.fetchall()]
    return population, stats, members


def aggregate_groups(data, membership, group_ids, *, grain='all', bucket=''):
    """Aggregate solely from captured sufficient statistics and wafer identities."""
    rows, counts = [], []
    displayed = {(str(row[0]) + ' TEST', row[1]) for row in data.metadata
                 if str(row[4]).strip() == 'o'}
    for group in group_ids:
        lots = {row['lot_id'] for row in membership if row['group_id'] == group}
        selected = [row for row in data.lot_parameter_stats
                    if row['grain'] == grain and row['bucket'] == bucket and row['lot_id'] in lots
                    and (row['process'], row['param']) in displayed]
        identities = [row for row in data.lot_wafer_members
                      if row['grain'] == grain and row['bucket'] == bucket and row['lot_id'] in lots]
        totals = {}
        for row in selected:
            key = (row['process'], row['param'])
            value_sum, count = totals.get(key, (0, 0))
            totals[key] = (value_sum + (row['value_sum'] or 0), count + row['value_count'])
        for (process, param), (value_sum, count) in sorted(totals.items()):
            rows.append(dict(period=group, process=process, param=param, value=value_sum / count if count else None))
        counts.append(dict(period=group,
                           lot_count=len({r['lot_id'] for r in identities if r['lot_id'] is not None}),
                           wf_count=len({r['gorupkey'] for r in identities if r['gorupkey'] is not None}),
                           has_yield_data=bool(totals)))
    return dict(rows=rows, counts=counts)
