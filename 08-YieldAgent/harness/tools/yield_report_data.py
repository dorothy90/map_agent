"""Weekly report requests and database aggregates under one product/week scope."""
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator

from ..types import Contract


class RecentWeeks(Contract):
    kind: Literal['recent_weeks']
    base_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    weeks: int = Field(ge=1, le=26, strict=True)

    @field_validator('base_date')
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


class ExplicitWeeks(Contract):
    kind: Literal['weeks']
    weeks: list[str] = Field(min_length=1, max_length=26)

    @field_validator('weeks')
    @classmethod
    def normalized_weeks(cls, values):
        normalized = []
        for value in values:
            match = re.fullmatch(r'(\d{4})(?:-W?|-)?(\d{2})', value.strip(), re.IGNORECASE)
            if not match:
                raise ValueError('week must be YYYY-Www, YYYY-WW, or YYYYWW')
            year, week = map(int, match.groups())
            date.fromisocalendar(year, week, 1)
            normalized.append(f'{year:04d}-{week:02d}')
        if len(set(normalized)) != len(normalized):
            raise ValueError('duplicate ISO week')
        return sorted(normalized)


WeeklyPeriod = Annotated[RecentWeeks | ExplicitWeeks, Field(discriminator='kind')]


def resolve_weeks(period: RecentWeeks | ExplicitWeeks) -> list[str]:
    if isinstance(period, ExplicitWeeks):
        return list(period.weeks)
    base = date.fromisoformat(period.base_date)
    weeks = []
    for offset in reversed(range(period.weeks)):
        year, week, _ = (base - timedelta(weeks=offset)).isocalendar()
        weeks.append(f'{year:04d}-{week:02d}')
    return weeks


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
class WeeklyReportData:
    lot_cd: str
    weeks: tuple[str, ...]
    metadata: list[tuple]
    yield_long: list[dict]
    weekly_counts: list[dict]
    period_lot_count: int
    period_wafer_count: int
    gms_weekly: list[dict]


def fetch_weekly_report(lot_cd: str, weeks: list[str]) -> WeeklyReportData:
    placeholders = ', '.join(f':w{i}' for i in range(len(weeks)))
    binds = {'lot': lot_cd, **{f'w{i}': week for i, week in enumerate(weeks)}}
    counts_filter = (
        "FROM DF_WF_ALL_PRB_V2 WHERE LOT_CD=:lot AND PROCESS='PT1H TEST' "
        f"AND WEEK_HYNIX IN ({placeholders})"
    )
    with _report_cursor() as cursor:
        metadata = _parameter_metadata(cursor, lot_cd)
        cursor.execute(
            "SELECT y.WEEK_HYNIX, y.PROCESS, y.PARAM_NM, AVG(y.VALUE) "
            "FROM DF_WF_ALL_PRB_V2 y WHERE y.LOT_CD=:lot "
            "AND y.PROCESS IN ('PT1H TEST','PT1C TEST') "
            f"AND y.WEEK_HYNIX IN ({placeholders}) "
            "AND EXISTS (SELECT 1 FROM DF_ALL_PARA_PRB m WHERE m.LOT_CD=y.LOT_CD "
            "AND m.PROC_GBN_CD||' TEST'=y.PROCESS AND m.PARAM_NM=y.PARAM_NM "
            "AND TRIM(m.YIELD_TABLE)='o') "
            "GROUP BY y.WEEK_HYNIX, y.PROCESS, y.PARAM_NM "
            "ORDER BY y.WEEK_HYNIX, y.PROCESS, y.PARAM_NM", binds,
        )
        yield_long = [dict(zip(('week', 'process', 'param', 'value'), row)) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT WEEK_HYNIX, COUNT(DISTINCT LOT_ID), COUNT(DISTINCT GORUPKEY) "
            + counts_filter + " GROUP BY WEEK_HYNIX ORDER BY WEEK_HYNIX", binds,
        )
        counts = {row[0]: row[1:] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT COUNT(DISTINCT LOT_ID), COUNT(DISTINCT GORUPKEY) " + counts_filter, binds,
        )
        lot_count, wafer_count = cursor.fetchall()[0]
        cursor.execute(
            "SELECT PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD "
            "FROM DF_GMS_YIELD_WEEKLY WHERE LOTCODE=:lot "
            f"AND PERIOD_DATE IN ({placeholders}) ORDER BY PERIOD_DATE",
            {'lot': lot_cd, **{f'w{i}': week.replace('-', '') for i, week in enumerate(weeks)}},
        )
        gms = [dict(week=f'{row[0][:4]}-{row[0][4:]}',
                    **dict(zip(('period_date', 'fab', 'prb', 'cum0'), row))) for row in cursor.fetchall()]
    yield_weeks = {row['week'] for row in yield_long}
    gms_weeks = {row['week'] for row in gms}
    weekly_counts = [dict(week=week, lot_count=counts.get(week, (None, None))[0],
                          wafer_count=counts.get(week, (None, None))[1],
                          has_yield_data=week in yield_weeks, has_gms=week in gms_weeks)
                     for week in weeks]
    return WeeklyReportData(lot_cd, tuple(weeks), metadata, yield_long, weekly_counts,
                            lot_count, wafer_count, gms)
