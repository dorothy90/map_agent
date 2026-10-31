"""Pure sufficient-statistic aggregation, pivoting, and comparison helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from statistics import mean, median, stdev


@dataclass(frozen=True)
class AggregateOutput:
    rows: list[dict]
    audit: dict


@dataclass(frozen=True)
class CompareOutput:
    rows: list[dict]
    audit: dict
    incomplete: bool


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _period(value, kind: str):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        from zoneinfo import ZoneInfo
        parsed = parsed.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    if kind == "daily":
        start = parsed.replace(hour=0, minute=0, second=0, microsecond=0); end = start + timedelta(days=1)
    elif kind == "weekly":
        start = (parsed - timedelta(days=parsed.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    elif kind == "monthly":
        start = parsed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + (start.month == 12), month=1 if start.month == 12 else start.month + 1)
    else:
        return None, None
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def aggregate_rows(rows: list[dict], *, group_by: list[str], operation: str, period: str,
                   time_column: str | None, overlap: str, max_groups: int) -> AggregateOutput:
    if period != "none" and not time_column:
        raise ValueError("Period aggregation requires a time column")
    if operation in {"mean", "median", "std"} and any(
            row.get("aggregation_level", "measurement") != "measurement" for row in rows):
        raise ValueError("Cannot re-aggregate an already aggregated value with this operation")
    for column in group_by + ([time_column] if time_column else []):
        if any(column not in row for row in rows): raise ValueError(f"Unknown aggregation column: {column}")

    overlap_rows = 0
    by_measurement = {}
    prepared = []
    for row in rows:
        measurement = row.get("measurement_ref") or row.get("fact_id") or row.get("member_id")
        identity = (measurement, row.get("metric_id"), row.get("definition_id"))
        prior = by_measurement.get(identity)
        if prior is not None:
            if prior == row: continue
            if prior.get("cohort_id") != row.get("cohort_id"):
                overlap_rows += 1
                if "cohort_id" not in group_by:
                    if overlap == "error": raise ValueError("Cohort overlap would double count a measurement")
                    if overlap == "deduplicate": continue
            elif json.dumps(prior, sort_keys=True, default=str) != json.dumps(row, sort_keys=True, default=str):
                raise ValueError(
                    "Duplicate measurement has conflicting values"
                    ". 동일 측정에 서로 다른 값이 있습니다"
                    "(여러 compute_metrics 결과나 측정 정책이 섞인 경우 등). "
                    "sources에는 단일 compute_metrics 결과의 metrics 핸들을 전달하거나, "
                    "filter_set 또는 rank_members의 selected 표로 측정별 한 행만 남기세요. "
                    "집계 구분 열은 group_by에 추가할 수 있지만, "
                    "현재 이 충돌은 그룹화 전에 검사하므로 group_by만으로 해결되지 않습니다."
                )
        by_measurement[identity] = row
        prepared.append(row)

    groups = {}
    excluded = 0
    for row in prepared:
        value = row.get("value")
        if value is not None and not _finite(value): raise ValueError("Metric values must be finite")
        if value is None:
            excluded += 1
            continue
        period_start, period_end = _period(row.get(time_column), period) if period != "none" else (None, None)
        semantic = (row.get("metric_id"), row.get("definition_id"), row.get("unit"),
                    row.get("better_when", "unknown"))
        key = tuple(row.get(column) for column in group_by) + (period_start, period_end) + semantic
        groups.setdefault(key, []).append(row)
    if len(groups) > max_groups: raise ValueError("Aggregation exceeds max_groups")
    output = []
    for key, members in groups.items():
        dimension_values = key[:len(group_by)]
        period_start, period_end = key[len(group_by):len(group_by) + 2]
        metric_id, definition_id, unit, better_when = key[-4:]
        values = [float(row["value"]) for row in members]
        numerators = [row.get("numerator") for row in members]
        denominators = [row.get("denominator") for row in members]
        if operation == "ratio_of_sums":
            if any(not _finite(value) for value in numerators + denominators):
                raise ValueError("ratio_of_sums requires finite numerators and denominators")
            numerator_sum, denominator_sum = sum(numerators), sum(denominators)
            result = None if denominator_sum == 0 else 100.0 * numerator_sum / denominator_sum
        else:
            numerator_sum = sum(value for value in numerators if _finite(value))
            denominator_sum = sum(value for value in denominators if _finite(value))
            if operation == "mean": result = mean(values)
            elif operation == "sum": result = sum(values)
            elif operation == "min": result = min(values)
            elif operation == "max": result = max(values)
            elif operation == "median": result = median(values)
            elif operation == "std": result = stdev(values) if len(values) > 1 else None
            elif operation == "identity":
                if len(values) != 1: raise ValueError("identity requires exactly one value per group")
                result = values[0]
            else: raise ValueError("Unsupported aggregation operation")
        dimensions = dict(zip(group_by, dimension_values))
        group_id = hashlib.sha256(json.dumps({"dimensions": dimensions, "period": period_start},
            sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        output.append({"group_id": group_id, **dimensions, "period_start": period_start,
            "period_end": period_end, "metric_id": metric_id, "definition_id": definition_id,
            "operation": operation, "value": result, "numerator_sum": numerator_sum,
            "denominator_sum": denominator_sum, "value_sum": sum(values), "value_count": len(values),
            "measurement_count": len({row.get("measurement_ref") or row.get("fact_id") or row.get("member_id")
                                      for row in members}),
            "unique_wafer_count": len({(row.get("lot_id"), row.get("wf_id")) for row in members}),
            "unit": unit, "better_when": better_when, "aggregation_level": "aggregate"})
    audit = {"source_rows": len(rows), "valid_rows": len(prepared) - excluded,
             "excluded_null_rows": excluded, "overlap_rows": overlap_rows,
             "duplicate_measurements": len(rows) - len(prepared), "complete": excluded == 0}
    return AggregateOutput(output, audit)


def _column_id(row: dict) -> str:
    raw = json.dumps([row.get("source_id"), row.get("process"), row.get("parameter")],
                     ensure_ascii=False, default=str)
    return "c_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def pivot_rows(rows: list[dict], catalog: list[dict]):
    column_rows, seen = [], set()
    candidates = [*catalog]
    catalog_semantics = {(row.get("process"), row.get("parameter")) for row in catalog}
    for row in rows:
        descriptor = {key: row.get(key) for key in
                      ("source_id", "process", "parameter", "unit", "better_when")}
        if (descriptor.get("process"), descriptor.get("parameter")) not in catalog_semantics:
            candidates.append(descriptor)
    for descriptor in candidates:
        identity = (descriptor.get("source_id"), descriptor.get("process"), descriptor.get("parameter"))
        if identity in seen: continue
        seen.add(identity)
        column_rows.append({"column_id": _column_id(descriptor),
            "display_label": str(descriptor.get("parameter") or descriptor.get("process") or "value"),
            "group_id": None, "group_label": None, "display_order": descriptor.get("display_order"),
            "source_id": descriptor.get("source_id"), "process": descriptor.get("process"),
            "parameter": descriptor.get("parameter"), "unit": descriptor.get("unit"),
            "better_when": descriptor.get("better_when", "unknown"),
            "included": descriptor.get("included"), "availability": "missing"})
    statistic_columns = {"group_id", "metric_id", "definition_id", "operation", "value",
        "numerator_sum", "denominator_sum", "value_sum", "value_count", "measurement_count",
        "unique_wafer_count", "unit", "better_when", "aggregation_level", "source_id",
        "process", "parameter"}
    base_columns = [key for key in (rows[0].keys() if rows else []) if key not in statistic_columns]
    grouped = {}
    for row in rows:
        key = tuple(row.get(column) for column in base_columns)
        grouped.setdefault(key, []).append(row)
    pivot = []
    for key, members in grouped.items():
        output = dict(zip(base_columns, key))
        for descriptor in column_rows:
            matches = [row for row in members if row.get("process") == descriptor["process"]
                       and row.get("parameter") == descriptor["parameter"]
                       and (row.get("source_id") in (None, descriptor["source_id"]))]
            if len(matches) > 1: raise ValueError("Pivot cell contains multiple values")
            output[descriptor["column_id"]] = matches[0]["value"] if matches else None
            if matches: descriptor["availability"] = "available"
        pivot.append(output)
    if not rows and column_rows:
        pivot = [{descriptor["column_id"]: None for descriptor in column_rows}]
    return pivot, column_rows


def compare_rows(baseline: list[dict], current: list[dict], *, keys: list[str], value_column: str,
                 methods: list[str], paired: bool, pair_keys: list[str], missing: str) -> CompareOutput:
    if paired:
        if not pair_keys or any(row.get("aggregation_level", "measurement") != "measurement"
                                or any(key not in row for key in pair_keys) for row in baseline + current):
            raise ValueError("Paired comparison requires individual observations and pair keys")
        active_keys = [*keys, *[key for key in pair_keys if key not in keys]]
    else:
        active_keys = keys
    def index(rows):
        result = {}
        for row in rows:
            if any(key not in row for key in active_keys): raise ValueError("Unknown comparison key")
            key = tuple(row.get(name) for name in active_keys)
            if key in result: raise ValueError("Comparison keys are not unique")
            result[key] = row
        return result
    left, right = index(baseline), index(current)
    ordered = list(left) + [key for key in right if key not in left]
    output, invalid = [], 0
    for key in ordered:
        before, after = left.get(key), right.get(key)
        if before is None or after is None:
            if missing == "error": raise ValueError("Comparison counterpart is missing")
            source = before or after
            output.append({**dict(zip(active_keys, key)), "baseline_value": before.get(value_column) if before else None,
                "current_value": after.get(value_column) if after else None, "delta": None,
                "delta_unit": "percentage_points" if source.get("unit") == "percent" else source.get("unit"),
                "relative_percent": None, "ratio": None, "baseline_n": 1 if before else 0,
                "current_n": 1 if after else 0, "paired_n": 0, "metric_id": source.get("metric_id"),
                "definition_id": source.get("definition_id"), "unit": source.get("unit"),
                "better_when": source.get("better_when", "unknown"), "direction": "unknown",
                "match_status": "baseline_only" if before else "current_only", "missing_reason": "MISSING_REFERENCE"})
            continue
        for semantic, label in (("definition_id", "definition"), ("metric_id", "metric"),
                                ("unit", "unit"), ("operation", "aggregation")):
            if before.get(semantic) != after.get(semantic): raise ValueError(f"Comparison {label} mismatch")
        baseline_value, current_value = before.get(value_column), after.get(value_column)
        if not _finite(baseline_value) or not _finite(current_value): raise ValueError("Comparison values must be finite")
        delta = current_value - baseline_value
        relative = ratio = None
        if baseline_value != 0:
            relative = 100.0 * delta / abs(baseline_value)
            ratio = current_value / baseline_value
        elif "relative_percent" in methods or "ratio" in methods: invalid += 1
        better = before.get("better_when", "unknown")
        direction = "unchanged" if delta == 0 else "improved" if \
            (better == "higher" and delta > 0) or (better == "lower" and delta < 0) else \
            "degraded" if better in {"higher", "lower"} else "unknown"
        output.append({**dict(zip(active_keys, key)), "baseline_value": baseline_value,
            "current_value": current_value, "delta": delta if "difference" in methods else None,
            "delta_unit": "percentage_points" if before.get("unit") == "percent" else before.get("unit"),
            "relative_percent": relative if "relative_percent" in methods else None,
            "ratio": ratio if "ratio" in methods else None, "baseline_n": 1, "current_n": 1,
            "paired_n": 1 if paired else 0, "metric_id": before.get("metric_id"),
            "definition_id": before.get("definition_id"), "unit": before.get("unit"),
            "better_when": better, "direction": direction, "match_status": "matched", "missing_reason": None})
    audit = {"baseline_rows": len(baseline), "current_rows": len(current),
             "matched_rows": len(set(left) & set(right)), "baseline_only": len(set(left) - set(right)),
             "current_only": len(set(right) - set(left)), "invalid_denominators": invalid}
    return CompareOutput(output, audit, bool(audit["baseline_only"] or audit["current_only"] or invalid))
