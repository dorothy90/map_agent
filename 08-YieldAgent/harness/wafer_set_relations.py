"""Registered relation joins with cardinality and temporal preflight checks."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import json

from .wafer_set_catalog import RELATIONS


@dataclass(frozen=True)
class JoinOutput:
    joined: list[dict]
    audit: dict
    unmatched: list[dict]


def _relation(relation_id):
    relation = next((item for item in RELATIONS if item["relation_id"] == relation_id), None)
    if relation is None:
        raise ValueError(f"Unknown relation_id: {relation_id}")
    return relation


def _keys(relation, side):
    keys = json.loads(relation["keys_json"])
    if relation["relation_id"] == "bmw_exact_identity" and side == "right":
        return ["alias_lot_id" if key == "lot_id" else key for key in keys]
    if relation["relation_id"] == "wafer_parameter":
        return [*keys, "process"]
    if relation["relation_id"] == "wads_report_day":
        return [*keys, "__registered_operation", "__registered_day"]
    return keys


def _key(row, keys):
    values = []
    for key in keys:
        if key == "__registered_operation":
            if row.get("oper_para") is not None:
                values.append(str(row["oper_para"]))
            elif row.get("category") is not None and row.get("parameter") is not None:
                values.append(f"{row['category']}_{row['parameter']}")
            else:
                raise ValueError("Registered operation fields are unavailable")
        elif key == "__registered_day":
            value = _time(row.get("end_tm"))
            if value is None: raise ValueError("Registered day field is unavailable")
            values.append(value.date().isoformat())
        elif key in row:
            values.append(row.get(key))
        else:
            raise ValueError("Relation key is unavailable")
    if len(values) != len(keys):
        raise ValueError("Relation key is unavailable")
    values = tuple(values)
    return None if any(value is None for value in values) else values


def _time(value):
    if value is None: return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _actual_cardinality(left_counts, right_counts):
    shared = set(left_counts) & set(right_counts)
    left_many = any(left_counts[key] > 1 for key in shared)
    right_many = any(right_counts[key] > 1 for key in shared)
    if left_many and right_many: return "many_to_many"
    if left_many: return "many_to_one"
    if right_many: return "one_to_many"
    return "one_to_one"


def join_tables(left: list[dict], right: list[dict], *, relation_id: str, how: str,
                expected_cardinality: str, time_policy: str, left_time: str | None,
                right_time: str | None, tolerance_seconds: int | None,
                allow_approximate: bool, max_expansion: float, max_rows: int) -> JoinOutput:
    relation = _relation(relation_id)
    if relation["confidence"] == "approximate" and not allow_approximate:
        raise ValueError("An approximate relation requires explicit opt-in")
    left_keys = _keys(relation, "left")
    right_keys = _keys(relation, "right")
    if time_policy != "none" and (not left_time or not right_time):
        raise ValueError("Temporal joins require both time columns")
    left_counts = Counter(key for row in left if (key := _key(row, left_keys)) is not None)
    right_counts = Counter(key for row in right if (key := _key(row, right_keys)) is not None)
    actual = _actual_cardinality(left_counts, right_counts)
    if time_policy == "none":
        if actual == "many_to_many":
            raise ValueError("many-to-many joins are not allowed")
        allowed = {"one_to_one": {"one_to_one"}, "one_to_many": {"one_to_one", "one_to_many"},
                   "many_to_one": {"one_to_one", "many_to_one"}}
        if actual not in allowed[expected_cardinality]:
            raise ValueError(f"Declared cardinality violated: {actual}")

    right_by_key = defaultdict(list)
    for row in right:
        key = _key(row, right_keys)
        if key is not None: right_by_key[key].append(row)
    matches: list[tuple[dict, list[dict]]] = []
    predicted = 0
    for row in left:
        candidates = list(right_by_key.get(_key(row, left_keys), []))
        if relation_id == "wads_report_day":
            if "end_tm" not in row or any("end_tm" not in item for item in candidates):
                raise ValueError("wads_report_day requires end_tm on both sides")
            left_day = _time(row.get("end_tm")).date() if _time(row.get("end_tm")) else None
            candidates = [item for item in candidates
                          if _time(item.get("end_tm")) is not None
                          and _time(item.get("end_tm")).date() == left_day]
        if time_policy == "exact":
            target = _time(row.get(left_time))
            candidates = [item for item in candidates if _time(item.get(right_time)) == target]
        elif time_policy == "latest_before":
            target = _time(row.get(left_time))
            eligible = [item for item in candidates if _time(item.get(right_time)) is not None
                        and target is not None and _time(item.get(right_time)) <= target]
            if tolerance_seconds is not None:
                eligible = [item for item in eligible
                            if (target - _time(item.get(right_time))).total_seconds() <= tolerance_seconds]
            if eligible:
                latest = max(_time(item.get(right_time)) for item in eligible)
                candidates = [item for item in eligible if _time(item.get(right_time)) == latest]
                fingerprints = {json.dumps({key: value for key, value in item.items()
                    if key not in ("fact_id", "source_row_hash")}, sort_keys=True,
                    ensure_ascii=False, default=str) for item in candidates}
                if len(fingerprints) > 1:
                    raise ValueError("latest_before has a same-time content conflict")
                candidates = candidates[:1]
            else:
                candidates = []
        matches.append((row, candidates))
        predicted += len(candidates) or (1 if how in ("left", "full") else 0)
    if time_policy != "none":
        left_many = any(len(candidates) > 1 for _, candidates in matches)
        selected_right = Counter(id(candidate) for _, candidates in matches for candidate in candidates)
        right_many = any(count > 1 for count in selected_right.values())
        actual = "many_to_many" if left_many and right_many else \
            "one_to_many" if left_many else "many_to_one" if right_many else "one_to_one"
        if actual == "many_to_many":
            raise ValueError("many-to-many joins are not allowed")
        allowed = {"one_to_one": {"one_to_one"}, "one_to_many": {"one_to_one", "one_to_many"},
                   "many_to_one": {"one_to_one", "many_to_one"}}
        if actual not in allowed[expected_cardinality]:
            raise ValueError(f"Declared cardinality violated: {actual}")
    if how == "full":
        matched_ids = {id(item) for _, candidates in matches for item in candidates}
        predicted += sum(id(item) not in matched_ids for item in right)
    if predicted > max_rows:
        raise ValueError("join output exceeds max_rows")
    if left and predicted / len(left) > max_expansion:
        raise ValueError("join output exceeds max_expansion")

    joined, unmatched, matched_right = [], [], set()
    right_columns = list(dict.fromkeys(key for row in right for key in row))
    for left_row, candidates in matches:
        if not candidates:
            unmatched.append({"side": "left", "fact_id": left_row.get("fact_id"),
                              "member_id": left_row.get("member_id"), "reason": "no_match"})
            if how in ("left", "full"):
                joined.append({**left_row, **{f"right__{key}": None for key in right_columns},
                    "left_fact_id": left_row.get("fact_id"), "right_fact_id": None,
                    "relation_id": relation_id, "match_status": "left_only", "time_distance_seconds": None})
            continue
        for right_row in candidates:
            matched_right.add(id(right_row))
            distance = None
            if time_policy != "none":
                distance = abs((_time(left_row.get(left_time)) - _time(right_row.get(right_time))).total_seconds())
            joined.append({**left_row, **{f"right__{key}": value for key, value in right_row.items()},
                "left_fact_id": left_row.get("fact_id"), "right_fact_id": right_row.get("fact_id"),
                "relation_id": relation_id, "match_status": "matched", "time_distance_seconds": distance})
    for right_row in right:
        if id(right_row) in matched_right: continue
        unmatched.append({"side": "right", "fact_id": right_row.get("fact_id"),
                          "member_id": right_row.get("member_id"), "reason": "no_match"})
        if how == "full":
            joined.append({**{key: None for key in dict.fromkeys(k for row in left for k in row)},
                **{f"right__{key}": value for key, value in right_row.items()},
                "left_fact_id": None, "right_fact_id": right_row.get("fact_id"),
                "relation_id": relation_id, "match_status": "right_only", "time_distance_seconds": None})
    audit = {"left_rows": len(left), "right_rows": len(right),
        "matched_left": sum(bool(candidates) for _, candidates in matches), "matched_right": len(matched_right),
        "unmatched_left": sum(not candidates for _, candidates in matches),
        "unmatched_right": len(right) - len(matched_right), "output_rows": len(joined),
        "expansion_ratio": len(joined) / len(left) if left else 0.0,
        "actual_cardinality": actual, "relation_confidence": relation["confidence"]}
    return JoinOutput(joined, audit, unmatched)
