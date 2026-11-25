"""Read LOT event histories and publish a complete, reusable LOT selection."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import tempfile
import threading
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, StringConstraints, model_validator

from ..types import Contract, ResultTable, StreamedResultTable
from .query_db import _connection, _serialize
from .registry import ToolResult, ToolSpec


Category = Literal["fdc_alarm", "qtime_over", "trouble_lot", "future_action", "sample_split"]
LotId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


@dataclass(frozen=True)
class HistorySource:
    table: str
    columns: str
    event_time: str | None


SOURCES: dict[Category, HistorySource] = {
    "fdc_alarm": HistorySource("DF_FDC_ALARM",
        "LOT_CD LOT_ID TRANSFER_TM OPER_ID EQP_ID ITEM_NM ALARM_LEVEL_CD RSLT_VAL SPEC_MIN_VAL SPEC_MAX_VAL",
        "TRANSFER_TM"),
    "qtime_over": HistorySource("DF_QTIME_OVER",
        "LOT_CD LOT_ID FROM_OPER TO_OPER CONTROL_LIMIT Q_TIME BAL EVENT_TM", "EVENT_TM"),
    "trouble_lot": HistorySource("DF_TROUBLE_LOT",
        "LOT_CD LOT_ID HOLD_TIME STEP_DESC CAUSE_EQ DELAY_TIME CONTENTS H_CODE", "HOLD_TIME"),
    "future_action": HistorySource("DF_FUTURE_ACTION",
        "LOT_ID ACTION_TIME ACTION_STEP ACTION_COMMENT ACTION_FLAG REASON_AREA", "ACTION_TIME"),
    "sample_split": HistorySource("DF_SAMPLE_SPLIT",
        "LOT_ID EVENT STEP OPER_DESC SAMPLE_SLOT SAMPLE_SPLIT_ID QTY", None),
}


class LotHistoryInput(Contract):
    lot_ids: list[LotId] = Field(default_factory=list, max_length=1000,
        description="Exact LOT IDs. Omit to discover all matching LOTs in a bounded event period.")
    categories: list[Category] = Field(default_factory=lambda: list(SOURCES), min_length=1,
        description="Only read the named event categories. Omit for full five-category LOT history or comparison.")
    start_time: datetime | None = Field(default=None, description="Inclusive event time in the plant timezone.")
    end_time: datetime | None = Field(default=None, description="Exclusive event time in the plant timezone.")

    include_future_events: bool = Field(default=False, description=(
        "For a period search, set true only if the user explicitly requests scheduled/future events "
        "beyond the request time. Otherwise only events observed as of the request are returned."))

    @model_validator(mode="after")
    def validate_scope(self):
        if not self.lot_ids and (self.start_time is None or self.end_time is None):
            raise ValueError("LOT discovery requires both start_time and end_time.")
        if self.start_time is not None or self.end_time is not None:
            if "sample_split" in self.categories:
                raise ValueError("sample_split has no event timestamp. Select timestamped categories or omit the period.")
        if self.start_time is not None and self.end_time is not None:
            if (self.start_time.utcoffset() is None) != (self.end_time.utcoffset() is None):
                raise ValueError("Use the same timezone convention for both bounds.")
            if self.start_time >= self.end_time:
                raise ValueError("end_time must be later than start_time.")
        return self


def _event_bounds(args, ctx):
    # The Oracle history columns store zero-padded local timestamps as VARCHAR2.
    run = ctx.run
    zone = ZoneInfo((run.get("request_context") or {}).get("timezone") or ctx.settings.timezone or "Asia/Seoul")
    requested = {}
    for key in ("start_time", "end_time"):
        value = getattr(args, key)
        if value is not None:
            if value.utcoffset() is not None:
                value = value.astimezone(zone).replace(tzinfo=None)
            requested[key] = value.isoformat(sep=" ")
    effective = requested.copy()
    if requested and not args.include_future_events:
        reference = datetime.fromisoformat(run["created_at"])
        if reference.utcoffset() is not None:
            reference = reference.astimezone(zone).replace(tzinfo=None)
        cutoff = reference.isoformat(sep=" ")
        effective["end_time"] = min(requested.get("end_time", cutoff), cutoff)
        if "start_time" in requested and requested["start_time"] > effective["end_time"]:
            effective["start_time"] = effective["end_time"]
    return requested, effective


def _read_source(cursor, category, lot_ids, bounds, stream, counts, cancelled):
    source = SOURCES[category]
    columns = source.columns.split()
    binds = {f"lot_{index}": lot_id for index, lot_id in enumerate(lot_ids)}
    predicates = ["LOT_ID IS NOT NULL"]
    if binds:
        predicates.append("LOT_ID IN (" + ", ".join(f":{key}" for key in binds) + ")")
    for key, operator in (("start_time", ">="), ("end_time", "<")):
        if key in bounds:
            predicates.append(f"{source.event_time} {operator} :{key}")
            binds[key] = bounds[key]
    sql = (f"SELECT {', '.join(columns)} FROM {source.table} WHERE {' AND '.join(predicates)} "
           f"ORDER BY LOT_ID, {source.event_time or 'STEP'}")
    cursor.execute(sql, binds)
    names = [column.lower() for column in columns]
    digest = hashlib.sha256()
    preview, preview_bytes, total = [], 0, 0
    for values in cursor:
        if cancelled.is_set():
            raise asyncio.CancelledError
        row = {name: _serialize(value) for name, value in zip(names, values)}
        counts.setdefault(row["lot_id"], Counter())[category] += 1
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        digest.update(encoded)
        stream.write(encoded)
        if total == len(preview) and total < 50 and preview_bytes + len(encoded) <= 32000:
            preview.append(row)
            preview_bytes += len(encoded)
        total += 1
    stream.seek(0)
    return StreamedResultTable(table_id=category, title=source.table, columns=names, rows=preview,
        source=stream, source_digest=digest.hexdigest(), total_rows=total, complete=True)


def _read_history(args, bounds, streams, cancelled, active, lock):
    lots = list(dict.fromkeys(args.lot_ids))
    counts = {lot: Counter() for lot in lots}
    details = []
    executed = []
    with _connection() as connection, connection.cursor() as cursor:
        with lock:
            active["connection"] = connection
        try:
            for category, stream in streams.items():
                if cancelled.is_set():
                    raise asyncio.CancelledError
                details.append(_read_source(cursor, category, lots, bounds, stream, counts, cancelled))
                executed.append(SOURCES[category].table)
        finally:
            with lock:
                active.clear()
    totals = {table.table_id: table.total_rows for table in details}
    summary = ResultTable(table_id="lot_summary", title="LOT별 이력 건수",
        columns=["lot_id", *streams], complete=True,
        rows=[{"lot_id": lot, **{category: counts[lot][category] for category in streams}}
              for lot in lots or sorted(counts)])
    scope = {"query_lot_history": {"executed_tables": executed, "categories": list(streams),
        **bounds, "selection_table": summary.table_id, "lot_count": len(counts), "event_counts": totals}}
    if not lots:
        # Discovery returns the LOT selection. Event-level questions can page the separate detail tables.
        scope["table_projections"] = {
            table.table_id: {"group_by": [], "aggregations": {}, "units": {}, "preview_policy": "read_result"}
            for table in details
        }
    return ToolResult(tables=[summary, *details], status="success" if any(totals.values()) else "empty",
        summary=f"조회 대상 LOT {len(counts)}개. " + ", ".join(f"{category} {count}건" for category, count in totals.items()),
        scope=scope)


async def query_lot_history(args, ctx):
    requested, bounds = _event_bounds(args, ctx)
    cancelled, active, lock = threading.Event(), {}, threading.Lock()
    with ExitStack() as files:
        streams = {category: files.enter_context(tempfile.TemporaryFile())
                   for category in dict.fromkeys(args.categories)}
        worker = asyncio.create_task(asyncio.to_thread(_read_history, args, bounds, streams, cancelled, active, lock))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            with lock, suppress(Exception):
                if connection := active.get("connection"):
                    connection.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise
        if requested:
            result.scope["query_lot_history"].update(requested_bounds=requested, effective_bounds=bounds)
            if requested != bounds:
                start = f"{bounds['start_time']} 이상, " if "start_time" in bounds else ""
                result.summary += f". 실제 이벤트 조회 기간(요청 시각 기준): {start}{bounds['end_time']} 미만."
        # The result store owns the open streams and closes them after publication.
        files.pop_all()
        return result


def register(registry):
    registry.add(ToolSpec("query_lot_history",
        "LOT_ID 기준 FDC 알람, Q-TIME 초과, 트러블·홀드, 예정 조치, 샘플 분할을 조회하는 전용 도구다. "
        "categories는 각각 fdc_alarm, qtime_over, trouble_lot, future_action, sample_split이고 "
        "선택한 종류의 원본 테이블만 읽어 건수와 상세 행을 반환한다. "
        "LOT 종합 이력 또는 LOT 이력 비교는 categories 생략 시 5종 전체를 조회한다. "
        "LOT 번호 없이 특정 기간의 이벤트가 있는 LOT들은 lot_ids를 생략하고 이벤트 종류와 기간을 지정한다. "
        "기간은 해당 이력의 이벤트 시각 기준이다. lot_summary에 선택된 전체 LOT와 종류별 건수를 저장한다. "
        "이 LOT들의 웨이퍼 맵은 load_wafer_maps의 lot_result_id에 이 조회의 result_id를 전달한다. "
        "이력 기간과 맵 측정 기간은 별개다.",
        LotHistoryInput, query_lot_history, read_only=True, effect="read"))
