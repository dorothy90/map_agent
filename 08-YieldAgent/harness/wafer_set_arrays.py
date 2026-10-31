"""Bounded parsing and vectorized calculations for stored map payloads."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
import hashlib
import json
from typing import Any

import numpy as np

from symbol_bin import make_symbol_fail_matcher
from .wafer_set_core import match_memberships

MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_DIE_PER_MEASUREMENT = 100_000
MAX_DIE_OBSERVATIONS = 5_000_000
MAX_COORDINATE = 100_000
_CACHE_BYTES = 128 * 1024 * 1024
_PARSER_VERSION = "wafer-map/v1"
_PARSE_CACHE: OrderedDict[tuple, tuple[int, "ParsedMap"]] = OrderedDict()
_PARSE_CACHE_SIZE = 0


@dataclass(frozen=True)
class ParsedMap:
    rows: np.ndarray
    cols: np.ndarray
    left_codes: np.ndarray
    right_codes: np.ndarray
    left_values: tuple[str | None, ...]
    right_values: tuple[str | None, ...]
    malformed: np.ndarray
    payload_hash: str
    payload_sha256: str

    @property
    def die_count(self) -> int:
        return int(self.rows.size)

    @property
    def nbytes(self) -> int:
        return sum(value.nbytes for value in
                   (self.rows, self.cols, self.left_codes, self.right_codes, self.malformed))


def clear_parse_cache() -> None:
    global _PARSE_CACHE_SIZE
    _PARSE_CACHE.clear()
    _PARSE_CACHE_SIZE = 0


def _immutable(values, dtype):
    result = np.asarray(values, dtype=dtype)
    result.flags.writeable = False
    return result


def _parse_payload(row: dict) -> ParsedMap:
    raw = row.get("map_val_json")
    if not isinstance(raw, str):
        raise ValueError("Stored map payload must be text")
    encoded = raw.encode()
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("Map payload exceeds byte limit")
    full = hashlib.sha256(encoded).hexdigest()
    declared_full = row.get("payload_sha256")
    declared_short = row.get("payload_hash")
    if declared_full and declared_full != full:
        raise ValueError("Stored map payload hash does not match content")
    if declared_short and declared_short != full[:16]:
        raise ValueError("Stored map payload hash does not match content")
    try:
        entries = json.loads(raw)["MAP"]
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("Invalid stored map payload") from exc
    if not isinstance(entries, list) or len(entries) > MAX_DIE_PER_MEASUREMENT:
        raise ValueError("Map die limit exceeded")
    rows, cols, left, right, malformed, coordinates = [], [], [], [], [], set()
    for entry in entries:
        parts = str(entry).split(",")
        if len(parts) < 2:
            raise ValueError("Map cell is missing coordinates")
        try:
            coordinate = (int(parts[0]), int(parts[1]))
        except ValueError as exc:
            raise ValueError("Map coordinate is invalid") from exc
        if max(abs(coordinate[0]), abs(coordinate[1])) > MAX_COORDINATE:
            raise ValueError("Map coordinate exceeds limit")
        if coordinate in coordinates:
            raise ValueError("Map contains duplicate coordinates")
        coordinates.add(coordinate)
        left_value = parts[2] if len(parts) > 2 and len(parts[2]) == 1 else None
        right_value = parts[3] if len(parts) > 3 and len(parts[3]) == 1 else None
        rows.append(coordinate[0]); cols.append(coordinate[1])
        left.append(left_value); right.append(right_value)
        malformed.append(left_value is None or right_value is None)
    left_values = tuple(dict.fromkeys(left))
    right_values = tuple(dict.fromkeys(right))
    left_index = {value: index for index, value in enumerate(left_values)}
    right_index = {value: index for index, value in enumerate(right_values)}
    return ParsedMap(
        rows=_immutable(rows, np.int32), cols=_immutable(cols, np.int32),
        left_codes=_immutable([left_index[value] for value in left], np.int32),
        right_codes=_immutable([right_index[value] for value in right], np.int32),
        left_values=left_values, right_values=right_values,
        malformed=_immutable(malformed, np.bool_), payload_hash=full[:16], payload_sha256=full)


def parse_maps(payloads: list[dict], *, namespace: tuple) -> dict[str, ParsedMap]:
    """Parse payloads once per owner/run/epoch namespace and enforce die limits."""
    global _PARSE_CACHE_SIZE
    output, total_die = {}, 0
    for row in payloads:
        raw = row.get("map_val_json")
        if not isinstance(raw, str):
            raise ValueError("Stored map payload must be text")
        full = hashlib.sha256(raw.encode()).hexdigest()
        key = (*namespace, full, _PARSER_VERSION)
        cached = _PARSE_CACHE.get(key)
        if cached is None:
            parsed = _parse_payload(row)
            size = parsed.nbytes + len(raw.encode())
            if size <= _CACHE_BYTES:
                _PARSE_CACHE[key] = (size, parsed)
                _PARSE_CACHE_SIZE += size
                while _PARSE_CACHE_SIZE > _CACHE_BYTES:
                    _, (removed, _) = _PARSE_CACHE.popitem(last=False)
                    _PARSE_CACHE_SIZE -= removed
        else:
            _PARSE_CACHE.move_to_end(key)
            parsed = cached[1]
        total_die += parsed.die_count
        if total_die > MAX_DIE_OBSERVATIONS:
            raise ValueError("Map die observation limit exceeded")
        output[parsed.payload_hash] = parsed
        output[parsed.payload_sha256] = parsed
    return output


def _spec_value(spec: Any, name: str, default=None):
    return getattr(spec, name, spec.get(name, default) if isinstance(spec, dict) else default)


def _definition_rows(definitions: list[dict], parameter: str) -> list[dict]:
    rows = [row for row in definitions if row.get("parameter") == parameter and row.get("regex_pattern")]
    if len(rows) != 1:
        raise ValueError(f"Stored definition is missing or ambiguous for parameter: {parameter}")
    return rows


def _metric_evaluations(parsed: ParsedMap, specs: list, definitions: list[dict]):
    left = np.asarray([parsed.left_values[index] for index in parsed.left_codes], dtype=object)
    right = np.asarray([parsed.right_values[index] for index in parsed.right_codes], dtype=object)
    observed = np.ones(parsed.die_count, dtype=np.bool_)
    unique_pairs: dict[tuple[str | None, str | None], np.ndarray] = {}
    for left_value, right_value in set(zip(left.tolist(), right.tolist())):
        unique_pairs[(left_value, right_value)] = (left == left_value) & (right == right_value)
    for spec in specs:
        mode, quantity = _spec_value(spec, "mode"), _spec_value(spec, "quantity")
        metric_id = _spec_value(spec, "metric_id")
        better = _spec_value(spec, "better_when", "unknown")
        if mode == "bin":
            if quantity == "fail_rate":
                raise ValueError(
                    "Failure metrics require symbol definitions. "
                    "Reselect with symbol_bin_parameters and use a symbol metric."
                )
            side = left if _spec_value(spec, "bin_side") == "left_bin" else right
            selected_valid = np.asarray([value is not None for value in side], dtype=np.bool_)
            selected = selected_valid & (side == _spec_value(spec, "pass_bin"))
            definition = hashlib.sha256(json.dumps({"metric_id": metric_id, "mode": mode,
                "quantity": quantity, "side": _spec_value(spec, "bin_side"),
                "pass_bin": _spec_value(spec, "pass_bin")}, sort_keys=True).encode()).hexdigest()[:16]
            yield metric_id, f"bin:{definition}", selected, observed, better, int(parsed.malformed.sum())
        elif mode == "symbol":
            parameters = list(_spec_value(spec, "parameters", []))
            parameter_masks = []
            parameter_ids = []
            for parameter in parameters:
                selected_defs = _definition_rows(definitions, parameter)
                matcher = make_symbol_fail_matcher({row["definition_id"]: row["regex_pattern"]
                                                    for row in selected_defs})
                mask = np.zeros(parsed.die_count, dtype=np.bool_)
                for pair, positions in unique_pairs.items():
                    if pair[0] is not None and pair[1] is not None and matcher(*pair):
                        mask |= positions
                parameter_masks.append((parameter, mask & observed))
                parameter_ids.append((parameter, "+".join(sorted(row["definition_id"] for row in selected_defs))))
            if _spec_value(spec, "combine", "separate") == "union":
                union = np.zeros(parsed.die_count, dtype=np.bool_)
                for _, mask in parameter_masks: union |= mask
                raw = json.dumps(parameter_ids, ensure_ascii=False, sort_keys=True)
                definition = "symbol-union:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
                yield metric_id, definition, union, observed, better, int(parsed.malformed.sum())
            else:
                for (parameter, mask), (_, definition_id) in zip(parameter_masks, parameter_ids):
                    yield metric_id, definition_id, mask, observed, better, int(parsed.malformed.sum())
        else:
            raise ValueError("Map calculations require bin or symbol mode")


def _counts(quantity: str, selected: np.ndarray, valid: np.ndarray, mode: str):
    denominator = int(valid.sum())
    selected_count = int((selected & valid).sum())
    if mode == "symbol":
        fail_count = selected_count
        numerator = fail_count if quantity == "fail_rate" else denominator - fail_count
        pass_count = denominator - fail_count
    else:
        pass_count = selected_count
        numerator = pass_count
        fail_count = None
    value = None if denominator == 0 else 100.0 * numerator / denominator
    return numerator, denominator, value, pass_count, fail_count


def compute_map_metrics(parsed: ParsedMap, specs: list, definitions: list[dict]):
    rows, incomplete = [], False
    for spec, evaluation in ((spec, item) for spec in specs
                             for item in _metric_evaluations(parsed, [spec], definitions)):
        metric_id, definition_id, selected, valid, better, unmatched = evaluation
        quantity, mode = _spec_value(spec, "quantity"), _spec_value(spec, "mode")
        numerator, denominator, value, pass_count, fail_count = _counts(quantity, selected, valid, mode)
        incomplete |= unmatched > 0 or denominator == 0
        rows.append({"metric_id": metric_id, "definition_id": definition_id, "value": value,
            "numerator": numerator, "denominator": denominator, "unit": "percent",
            "pass_die_count": pass_count, "fail_die_count": fail_count,
            "unmatched_cells": unmatched, "better_when": better})
    return rows, incomplete


def reduce_map_metrics(measurements: list[dict], parsed: dict[str, ParsedMap], specs: list,
                       definitions: list[dict], memberships: list[dict], *, process_links: list[dict] | None = None,
                       membership_match=None, coordinate_policy: str, grid_id: str | None):
    if coordinate_policy == "explicit_same_grid" and not grid_id:
        raise ValueError("explicit grid policy requires grid_id")
    membership_match = membership_match or match_memberships(measurements, memberships)
    groups, member_rows, seen_members = defaultdict(list), [], set()
    for index, measurement in enumerate(measurements):
        _, identity_value = membership_match.identities_by_row[index]
        reference = measurement.get("measurement_ref") or measurement.get("member_id") or identity_value
        cohorts = membership_match.cohorts_by_row[index] if memberships else [None]
        for cohort in dict.fromkeys(cohorts):
            if coordinate_policy == "explicit_same_grid":
                key = (cohort, grid_id, None, None)
            else:
                natural_grid = json.dumps([measurement.get("lot_cd"), measurement.get("process")],
                                          ensure_ascii=False)
                key = (cohort, natural_grid, measurement.get("lot_cd"), measurement.get("process"))
            identity = (key, reference)
            if identity in seen_members: continue
            seen_members.add(identity)
            groups[key].append(measurement)
            member_rows.append({"cohort_id": cohort, "measurement_ref": reference,
                                "payload_hash": measurement.get("payload_hash")})
    cells, summaries, incomplete = [], [], False
    for (cohort, active_grid, lot_cd, process), group in groups.items():
        lots = {row.get("lot_cd") for row in group}
        processes = {row.get("process") for row in group}
        output_lot = next(iter(lots)) if len(lots) == 1 else None
        output_process = next(iter(processes)) if len(processes) == 1 else None
        for spec in specs:
            accumulated = defaultdict(lambda: {"coordinates": [], "numerators": [],
                                                 "denominators": [], "unmatched": 0})
            for measurement in group:
                source = parsed.get(measurement.get("payload_sha256")) or parsed.get(measurement.get("payload_hash"))
                if source is None: raise ValueError("Stored payload reference is unavailable")
                applicable_definitions = definitions
                if _spec_value(spec, "mode") == "symbol":
                    applicable_definitions = [row for row in applicable_definitions
                                              if row.get("lot_cd") == measurement.get("lot_cd")]
                    target_process = measurement.get("process")
                    links = [row for row in (process_links or [])
                             if row.get("source_process") == target_process]
                    if len({row.get("definition_process") for row in links}) > 1:
                        raise ValueError("Stored process relation is ambiguous")
                    if links: target_process = links[0].get("definition_process")
                    applicable_definitions = [row for row in applicable_definitions
                                              if row.get("process") == target_process]
                evaluations = list(_metric_evaluations(source, [spec], applicable_definitions))
                for metric_id, current_definition, selected, valid, _, bad in evaluations:
                    mode, quantity = _spec_value(spec, "mode"), _spec_value(spec, "quantity")
                    numerator_mask = selected if (mode == "symbol" and quantity == "fail_rate") or \
                        (mode == "bin" and quantity != "fail_rate") else valid & ~selected
                    bucket = accumulated[current_definition]
                    bucket["coordinates"].append(np.column_stack((source.rows, source.cols)))
                    bucket["numerators"].append(numerator_mask.astype(np.int64, copy=False))
                    bucket["denominators"].append(valid.astype(np.int64, copy=False))
                    bucket["unmatched"] += bad
            for definition_id, bucket in accumulated.items():
                coordinates = np.concatenate(bucket["coordinates"]) if bucket["coordinates"] else np.empty((0, 2), np.int32)
                numerators = np.concatenate(bucket["numerators"]) if bucket["numerators"] else np.empty(0, np.int64)
                denominators = np.concatenate(bucket["denominators"]) if bucket["denominators"] else np.empty(0, np.int64)
                unique, inverse = np.unique(coordinates, axis=0, return_inverse=True)
                numerator_counts = np.bincount(inverse, weights=numerators, minlength=len(unique)).astype(np.int64)
                denominator_counts = np.bincount(inverse, weights=denominators, minlength=len(unique)).astype(np.int64)
                values = np.divide(100.0 * numerator_counts, denominator_counts,
                    out=np.full(len(unique), np.nan), where=denominator_counts != 0)
                if len(cells) + len(unique) > 250_000: raise ValueError("Map cell output exceeds limit")
                for coordinate, numerator, denominator, value in zip(
                        unique, numerator_counts, denominator_counts, values):
                    cells.append({"cohort_id": cohort, "lot_cd": output_lot, "process": output_process,
                        "grid_id": active_grid, "metric_id": _spec_value(spec, "metric_id"),
                        "definition_id": definition_id, "row": int(coordinate[0]), "col": int(coordinate[1]),
                        "numerator": int(numerator), "denominator": int(denominator),
                        "value": None if np.isnan(value) else float(value), "unit": "percent"})
                numerator_sum = int(numerator_counts.sum())
                denominator_sum = int(denominator_counts.sum())
                valid_values = values[~np.isnan(values)]
                unmatched = bucket["unmatched"]
                incomplete |= unmatched > 0 or denominator_sum == 0
                summaries.append({"cohort_id": cohort, "metric_id": _spec_value(spec, "metric_id"),
                    "unique_wafer_count": len({row.get("member_id") for row in group}),
                    "measurement_count": len(group), "coordinate_count": len(unique),
                    "die_observations": denominator_sum,
                    "pooled_value": None if denominator_sum == 0 else 100.0 * numerator_sum / denominator_sum,
                    "mean_coordinate_value": None if not len(valid_values) else float(np.mean(valid_values)),
                    "unmatched_cells": unmatched})
    return cells, summaries, member_rows, incomplete
