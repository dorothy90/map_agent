"""Pure predicate, set and cohort operations for stored result rows."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .wafer_set_contracts import PredicateGroup


EMPTY_FILTER_HINT = (
    "필터 결과가 0행입니다. list_values(source_id, field)로 해당 열의 실제 값을 확인하세요"
)


def has_string_filter(value) -> bool:
    if value is None:
        return False
    if hasattr(value, "where"):
        if any(isinstance(item, str) for name in ("lot_cd", "process", "lot_ids")
               for item in getattr(value, name, [])):
            return True
        value = value.where
    if value is None or not hasattr(value, "items"):
        return False
    for atom in value.items:
        if atom.op == "eq" and isinstance(atom.value, str):
            return True
        if atom.op == "in" and any(isinstance(item, str) for item in atom.value):
            return True
    return False


def empty_filter_limitation(table_id: str, requested_count: int,
                            source_handle=None) -> dict:
    return {"code": "EMPTY_STRING_FILTER", "table_id": table_id,
        "affected_count": requested_count, "requested_count": requested_count,
        "detail": EMPTY_FILTER_HINT, "source_handle": source_handle}


@dataclass(frozen=True)
class FilterOutput:
    rows: list[dict]
    null_predicate_rows: int
    duplicate_keys: int


@dataclass(frozen=True)
class CohortOutput:
    cohorts: list[dict]
    memberships: list[dict]
    audit: dict


def _type_matches(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return True
    return type(left) is type(right)


def _atom(row: dict, atom) -> bool | None:
    if atom.column not in row:
        raise ValueError(f"Unknown predicate column: {atom.column}")
    left = row.get(atom.column)
    if atom.op == "is_null":
        return left is None
    if atom.op == "not_null":
        return left is not None
    if left is None:
        return None
    if atom.op == "mod_eq":
        if isinstance(left, bool) or not isinstance(left, int):
            raise ValueError("mod_eq requires an integer column")
        return left % atom.divisor == atom.remainder
    values = atom.value if isinstance(atom.value, list) else [atom.value]
    if any(not _type_matches(left, value) for value in values):
        raise ValueError("Predicate value type does not match the column type")
    if atom.op == "eq": return left == atom.value
    if atom.op == "ne": return left != atom.value
    if atom.op == "lt": return left < atom.value
    if atom.op == "le": return left <= atom.value
    if atom.op == "gt": return left > atom.value
    if atom.op == "ge": return left >= atom.value
    if atom.op == "in": return left in atom.value
    if atom.op == "not_in": return left not in atom.value
    if atom.op == "between": return atom.value[0] <= left <= atom.value[1]
    raise ValueError(f"Unsupported predicate operation: {atom.op}")


def evaluate_predicate(rows: list[dict], predicate: PredicateGroup, null_policy: str) -> list[bool]:
    output = []
    for row in rows:
        states = [_atom(row, item) for item in predicate.items]
        has_null = any(state is None for state in states)
        if has_null and null_policy == "error":
            raise ValueError("Predicate encountered a null value")
        replacements = [null_policy == "include" if state is None else state for state in states]
        output.append(all(replacements) if predicate.mode == "all" else any(replacements))
    return output


def _row_fingerprint(row: dict) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)


def _key(row: dict, columns: list[str]) -> tuple:
    missing = [column for column in columns if column not in row]
    if missing:
        raise ValueError("Unknown key column: " + ", ".join(missing))
    return tuple(row.get(column) for column in columns)


def _unique(rows: list[dict], key_columns: list[str], null_policy: str) -> tuple[list[dict], dict, int, int]:
    result, indexed, duplicates, nulls = [], {}, 0, 0
    for row in rows:
        key = _key(row, key_columns)
        if any(value is None for value in key):
            nulls += 1
            if null_policy == "error":
                raise ValueError("Set key contains a null value")
            if null_policy == "exclude":
                continue
        fingerprint = _row_fingerprint(row)
        if key in indexed:
            duplicates += 1
            if indexed[key][1] != fingerprint:
                raise ValueError("Set key has conflicting rows")
            continue
        indexed[key] = (row, fingerprint)
        result.append(row)
    return result, indexed, duplicates, nulls


def filter_rows(rows: list[dict], *, operation: str, key_columns: list[str], null_policy: str,
                predicate: PredicateGroup | None = None, other: list[dict] | None = None) -> FilterOutput:
    left, left_index, duplicates, nulls = _unique(rows, key_columns, null_policy)
    if operation == "where":
        if predicate is None or other is not None:
            raise ValueError("where requires predicate only")
        predicate_nulls = sum(any(item.op not in ("is_null", "not_null") and row.get(item.column) is None
                                  for item in predicate.items) for row in left)
        mask = evaluate_predicate(left, predicate, null_policy)
        return FilterOutput([row for row, keep in zip(left, mask) if keep], nulls + predicate_nulls, duplicates)
    if predicate is not None or other is None:
        raise ValueError("Set operations require other only")
    right, right_index, right_duplicates, right_nulls = _unique(other, key_columns, null_policy)
    for key in set(left_index) & set(right_index):
        if left_index[key][1] != right_index[key][1]:
            raise ValueError("Set key has conflicting rows")
    if operation == "union":
        output = left + [row for row in right if _key(row, key_columns) not in left_index]
    elif operation == "intersection":
        output = [row for row in left if _key(row, key_columns) in right_index]
    elif operation == "difference":
        output = [row for row in left if _key(row, key_columns) not in right_index]
    else:
        raise ValueError(f"Unsupported set operation: {operation}")
    return FilterOutput(output, nulls + right_nulls, duplicates + right_duplicates)


def _cohort_id(dimensions: dict, rule_name: str | None) -> str:
    raw = json.dumps({"dimensions": dimensions, "rule": rule_name}, sort_keys=True,
                     ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_cohorts(rows: list[dict], *, key_columns: list[str], group_by: list[str], rules: list,
                 combine: str, null_group: str, max_cohorts: int, max_memberships: int) -> CohortOutput:
    if not group_by and not rules:
        raise ValueError("group_by or rules is required")
    for column in [*key_columns, *group_by]:
        if any(column not in row for row in rows):
            raise ValueError(f"Unknown cohort column: {column}")
    groups: list[tuple[dict, list[dict]]] = []
    by_group: dict[tuple, list[dict]] = {}
    for row in rows:
        values = tuple(row.get(column) for column in group_by)
        if any(value is None for value in values):
            if null_group == "error": raise ValueError("Group contains a null value")
            if null_group == "exclude": continue
        by_group.setdefault(values, []).append(row)
    for values, members in by_group.items():
        groups.append((dict(zip(group_by, values)), members))

    candidates = []
    if combine == "independent":
        candidates.extend((dimensions, None, members) for dimensions, members in groups if group_by)
        candidates.extend(({}, rule, rows) for rule in rules)
    elif combine == "cross":
        candidates.extend((dimensions, rule, members) for dimensions, members in groups for rule in rules)
    else:
        raise ValueError("Unsupported cohort combination")
    if len(candidates) > max_cohorts:
        raise ValueError("max_cohorts exceeded")

    cohort_rows, memberships, per_member = [], [], {}
    excluded_null = sum(any(row.get(column) is None for column in group_by) for row in rows) \
        if null_group == "exclude" else 0
    for dimensions, rule, population in candidates:
        selected = population
        if rule is not None:
            predicate = rule.predicate if hasattr(rule, "predicate") else rule["predicate"]
            if isinstance(predicate, dict): predicate = PredicateGroup.model_validate(predicate)
            selected = [row for row, keep in zip(population, evaluate_predicate(population, predicate, "exclude")) if keep]
            rule_name = rule.name if hasattr(rule, "name") else rule["name"]
        else:
            rule_name = None
        cohort_id = _cohort_id(dimensions, rule_name)
        label_parts = [f"{key}={value}" for key, value in dimensions.items()]
        if rule_name: label_parts.append(rule_name)
        seen = set()
        for row in selected:
            member_id = row.get("member_id") or json.dumps(_key(row, key_columns), ensure_ascii=False, default=str)
            measurement_ref = row.get("measurement_ref")
            identity = measurement_ref or member_id
            if identity in seen: continue
            seen.add(identity)
            membership = {"cohort_id": cohort_id, "member_id": member_id,
                          "measurement_ref": measurement_ref,
                          **{column: row.get(column) for column in key_columns}}
            memberships.append(membership)
            per_member.setdefault(member_id, set()).add(cohort_id)
            if len(memberships) > max_memberships:
                raise ValueError("max_memberships exceeded")
        cohort_rows.append({"cohort_id": cohort_id, "label": " | ".join(label_parts) or "all",
            "dimensions_json": json.dumps(dimensions, ensure_ascii=False, sort_keys=True, default=str),
            "predicate_json": json.dumps((rule.predicate if hasattr(rule, "predicate") else rule.get("predicate"))
                .model_dump(mode="json") if rule is not None and hasattr((rule.predicate if hasattr(rule, "predicate") else rule.get("predicate")), "model_dump")
                else (rule.get("predicate") if isinstance(rule, dict) else None), ensure_ascii=False, sort_keys=True),
            "member_count": len({item["member_id"] for item in memberships if item["cohort_id"] == cohort_id}),
            "measurement_count": len(seen), "complete": True})
    audit = {"input_members": len({row.get("member_id") or _key(row, key_columns) for row in rows}),
             "membership_rows": len(memberships),
             "overlapping_members": sum(len(value) > 1 for value in per_member.values()),
             "excluded_null_members": excluded_null}
    return CohortOutput(cohort_rows, memberships, audit)
