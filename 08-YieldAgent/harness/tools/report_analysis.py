"""Render all sections against one product, set of tests, and measurement date."""
from datetime import date, timedelta
from typing import Literal, get_args

from pydantic import Field, model_validator

from ..types import Contract
from .registry import ToolResult
from .yield_report_data import (
    GRAINS, MEASURE_DATE, RecentPeriod, _latest_test_source, _report_cursor, fetch_period_report,
)

ReportTest = Literal["PT1H TEST", "PT1C TEST"]


class ReportAnalysis(Contract):
    months: int = Field(ge=1, le=24)
    weeks: int = Field(ge=1, le=26)
    days: int = Field(ge=1, le=92)
    parameters_per_direction: int = Field(ge=1, le=20)
    bins_per_test: int = Field(ge=1, le=20)
    tests: list[ReportTest] | None = Field(default=None, min_length=1, max_length=2,
        description="Omit to include all supported product tests. Supply only for an explicit narrower scope.")
    as_of: date | None = Field(default=None, description="Default: latest product measurement in the selected tests.")

    @model_validator(mode="after")
    def distinct_tests(self):
        if self.tests and len(set(self.tests)) != len(self.tests):
            raise ValueError("tests must be distinct")
        return self


def analysis_scope(lot_cd, spec):
    tests = spec.tests or list(get_args(ReportTest))
    if spec.as_of:
        return tests, spec.as_of
    bindings = {f"test{i}": test for i, test in enumerate(tests)}
    placeholders = ", ".join(f":{key}" for key in bindings)
    with _report_cursor() as cursor:
        cursor.execute(f"SELECT MAX({MEASURE_DATE}) FROM {_latest_test_source()} "
                       f"WHERE PROCESS IN ({placeholders})", dict(lot=lot_cd, **bindings))
        latest = cursor.fetchone()[0]
    if latest is None:
        raise ValueError(f"{lot_cd}: no measurement date for {', '.join(tests)}")
    return tests, latest.date()


def build_report_analysis(lot_cd, spec):
    from .table_artifact import YieldTableInput, _render_period_report
    from .report_bin_maps import report_bin_maps
    from .report_scatter import build_report_scatter

    tests, base = analysis_scope(lot_cd, spec)
    split = base - timedelta(days=base.weekday())
    start, end = split - timedelta(weeks=1), base + timedelta(days=1)
    previous_week, current_week = (d.strftime("%G-W%V") for d in (start, split))
    artifacts, tables, windows, summaries = [], [], {}, {}
    status = "success"
    for grain, (key, _) in GRAINS.items():
        period = RecentPeriod(kind="recent", **{key: getattr(spec, key)})
        data = fetch_period_report(lot_cd, period, base=base, report_tests=tests)
        args = YieldTableInput(lot_cd=lot_cd, period=period,
                               title=f"{lot_cd} " + {"month": "월별 수율", "week": "주별 수율", "day": "일별 수율"}[grain])
        section = _render_period_report(args, data, report_tests=tests)
        artifacts.extend(section.artifacts)
        windows[grain] = data.window
        summaries[grain] = section.scope["report_summary"]
        tables.extend(t.model_copy(update={"table_id": f"{grain}_{t.table_id}"}) for t in section.tables)
        if section.status != "success":
            status = "partial"

    scatter, scatter_tables, trend = build_report_scatter(
        lot_cd, tests, start, split, end, top_n=spec.parameters_per_direction)
    maps, bins = report_bin_maps(lot_cd, tests, start, split, end, top_n=spec.bins_per_test)
    if not scatter or len(maps) < len(tests):
        status = "partial"
    return ToolResult(status=status, artifacts=[*artifacts, *scatter, *maps], tables=[*tables, *scatter_tables],
        summary=f"{lot_cd} · {', '.join(tests)} · {base.isoformat()} 기준 · 기간표 {len(windows)}개. "
                f"산점도와 BIN 맵 비교: 직전 {previous_week} ({start}~{split - timedelta(days=1)}), "
                f"최근 {current_week} ({split}~{base}). 기간별 수치와 Δ는 report_summary에서 인용한다. 표, 산점도, 검사별 BIN 맵은 artifact_refs 순서로 표시한다.",
        scope={"lot_cd": lot_cd, "tests": tests, "as_of": base.isoformat(), "windows": windows,
               "report_summary": summaries, "comparison": {"previous_week": previous_week, "current_week": current_week,
                              "start": start.isoformat(), "split": split.isoformat(),
                              "end_exclusive": end.isoformat()}, "scatter": trend, "bins": bins})
