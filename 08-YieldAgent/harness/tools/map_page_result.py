"""Publish sequential MAP pages as one session-owned result."""
from collections import Counter, defaultdict
from contextlib import ExitStack
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..runtime.domain_process import safe_error_message
from ..types import ResultTable, StreamedResultTable
from .registry import ToolResult


def map_summary(args, facts, *, complete_scan=False, pages=1):
    parts = []
    if args.lot_result_id:
        matched = facts["matched_lot_count"]
        dates = "전체 맵 측정 기간"
        if args.start_time is not None or args.end_time is not None:
            lo = args.start_time.isoformat() if args.start_time else "-∞"
            hi = args.end_time.isoformat() if args.end_time else "+∞"
            dates = f"MAP.{args.time_column.upper()} [{lo}, {hi})"
        parts.append(f"Oracle MAP 대조 완료. 확정 연결 키 lot_summary.lot_id = MAP.LOT_ID, "
                     f"검사 {args.oper or '전체 검사'}, {dates}. 전체 대상 {len(args.lot_ids)} LOT 중 "
                     f"일치 {matched}, 맵 없음 {len(args.lot_ids) - matched}.")
    if complete_scan:
        parts.append(f"동일 조건 {pages}페이지 조회. LOT별 표시 수량은 lot_map_counts에 저장됨.")
    elif facts["measurement_offset"]:
        parts.append(f"측정 위치 {facts['measurement_offset']}부터 읽은 한 페이지의 수량.")
    parts.append(f"measurements={facts['measurement_count']}, wafers={facts['wafer_count']}, "
                 f"dies={facts['die_count']}, truncated={facts['truncated']}")
    if facts.get("scan_error"):
        parts.append(f"조회 중단. 다음 측정 위치 {facts['next_measurement_offset']}. {facts['scan_error']}")
    return " ".join(parts)


def _union_range(current, interval):
    if not interval:
        return current
    return [min(current[0], interval[0]), max(current[1], interval[1])] if current else interval


def finalize_map_pages(pages, args, *, spool_dir):
    directory = Path(spool_dir)
    die_path = directory / "dies.jsonl"
    digest = hashlib.sha256()
    bins, measurements_by_lot = Counter(), Counter()
    wafers_by_lot = defaultdict(set)
    row_range = col_range = None
    members, manifests, artifacts = [], {}, []
    measurement_preview, die_preview = [], []
    count = duplicates = page_count = total_dies = 0
    last = scan_error = None
    pages = iter(pages)
    with die_path.open("xb") as out:
        while True:
            try:
                page = next(pages)
            except StopIteration:
                break
            except Exception as exc:
                if last is None:
                    raise
                scan_error = safe_error_message(exc)
                break
            page_count += 1
            facts = page.scope["wafer_maps"]
            count += facts["measurement_count"]
            duplicates += facts["duplicate_measurement_count"]
            total_dies += facts["die_count"]
            bins.update(facts["bin_value_counts"])
            row_range = _union_range(row_range, facts["row_range"])
            col_range = _union_range(col_range, facts["col_range"])
            measurement_preview.extend(page.scope["map_measurements"]["preview_rows"][:10-len(measurement_preview)])
            last_id = None
            for table in page.tables:
                if table.table_id == "wafer_map_dies":
                    die_preview.extend(table.rows[:50-len(die_preview)])
                    for row in table.rows:
                        if row["measurement_id"] != last_id:
                            last_id = row["measurement_id"]
                            lot = row["lot_id"]
                            wafers_by_lot[lot].add(row["wf_id"])
                            measurements_by_lot[lot] += 1
                        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
                        out.write(line)
                        digest.update(line)
                    table.rows.clear()
                elif table.table_id == "wafer_map_members":
                    members.extend(table.rows)
                elif table.table_id == "wafer_map_manifest":
                    for row in table.rows:
                        key = (row["selection_id"], row["group_id"])
                        if key in manifests:
                            manifests[key]["member_count"] += row["member_count"]
                        else:
                            manifests[key] = dict(row)
            for artifact in page.artifacts:
                path = directory / f"chart-{len(artifacts)}.html"
                path.write_text(artifact.pop("data"), encoding="utf-8")
                artifacts.append({**artifact, "data": path})
            last = page

    if last is None:
        raise ValueError("MAP reader returned no page or empty result")
    facts = last.scope["wafer_maps"].copy()
    incomplete = facts["truncated"] or scan_error is not None
    missing = facts["missing_requests"].copy()
    if args.wafer_groups:
        wanted = {(g.lot_id, w) for g in args.wafer_groups for w in g.wf_ids}
        missing["lot_wafers"] = [[lot, wf] for lot, wf in sorted(wanted) if wf not in wafers_by_lot[lot]]
    facts.update(wafer_count=sum(map(len, wafers_by_lot.values())), measurement_count=count, die_count=total_dies,
                 duplicate_measurement_count=duplicates, bin_value_counts=dict(bins), row_range=row_range,
                 col_range=col_range, missing_requests=missing, measurement_offset=args.measurement_offset,
                 next_measurement_offset=args.measurement_offset + count + duplicates if incomplete else None,
                 page_count=page_count, truncated=incomplete)
    if scan_error is not None:
        facts.update(scan_error=scan_error, truncation_reason="scan_error")

    scope = dict(last.scope, wafer_maps=facts)
    observed = sum(measurements_by_lot.values())
    scope["map_measurements"] = dict(scope["map_measurements"], measurement_count=observed,
                                     preview_rows=measurement_preview, truncated=observed > len(measurement_preview))
    die_table = next(t for t in last.tables if t.table_id == "wafer_map_dies")
    tables = [StreamedResultTable(table_id=die_table.table_id, title=die_table.title, columns=die_table.columns,
                                  rows=die_preview, total_rows=total_dies, source=die_path,
                                  source_digest=digest.hexdigest(), complete=not incomplete,
                                  missing_reason=facts["truncation_reason"] if incomplete else None)]
    if members:
        tables.extend([ResultTable(table_id=name, title=name, rows=rows, complete=not incomplete,
                                   missing_reason=facts["truncation_reason"] if incomplete else None)
                       for name, rows in [("wafer_map_members", members), ("wafer_map_manifest", list(manifests.values()))]])
    tables.extend(t for t in last.tables if t.table_id == "lot_map_coverage")
    lots = dict.fromkeys([*args.lot_ids, *[g.lot_id for g in args.wafer_groups], *wafers_by_lot])
    tables.append(ResultTable(table_id="lot_map_counts", title="LOT별 표시된 웨이퍼와 측정 수",
                              columns=["lot_id", "wafer_count", "measurement_count"],
                              rows=[dict(lot_id=lot, wafer_count=len(wafers_by_lot[lot]),
                                         measurement_count=measurements_by_lot[lot]) for lot in lots],
                              complete=not incomplete, missing_reason=facts["truncation_reason"] if incomplete else None))
    if len(artifacts) > 1:
        for i, artifact in enumerate(artifacts, 1):
            artifact["title"] += f" ({i}/{len(artifacts)})"
    status = "success"
    if incomplete or (count and any(missing.values())):
        status = "partial"
    elif count == 0:
        status = "empty"
    return ToolResult(tables=tables, artifacts=artifacts, scope=scope, status=status,
                      summary=map_summary(args, facts, complete_scan=True, pages=page_count))


async def run_map_query(run_blocking, function, *args):
    # A parent-owned directory also cleans up output if the Oracle worker is killed mid-page.
    with tempfile.TemporaryDirectory(prefix="yield-map-") as tmp, ExitStack() as opened:
        directory = Path(tmp)
        result = await run_blocking(partial(function, spool_dir=directory), *args)

        def open_source(path):
            if path.parent != directory:
                raise ValueError("Unexpected MAP spool location")
            if os.name == "nt":
                # Windows refuses to unlink an open file when the workspace closes.
                stream = tempfile.TemporaryFile()
                opened.callback(stream.close)
                with path.open("rb") as src:
                    shutil.copyfileobj(src, stream)
                stream.seek(0)
            else:
                stream = path.open("rb")
                opened.callback(stream.close)
            return stream

        for table in result.tables:
            if isinstance(table, StreamedResultTable) and isinstance(table.source, Path):
                table.source = open_source(table.source)
        for artifact in result.artifacts:
            for field in ("data", "matrix_panels"):
                if isinstance(artifact.get(field), Path):
                    artifact[field] = open_source(artifact[field])
        opened.pop_all()
        return result
