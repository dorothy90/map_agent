"""Seed wafer bin ratios and yield into DF_DIE_TO_WF_YLD.

Dry-run is the default and performs database reads only.  ``--apply`` creates a
once-per-day CTAS backup, then replaces only configured bin/yield parameters in
one DML transaction.  Ratios use 12 decimal places (half-even), limiting the
absolute rounding error to 5e-13 while remaining well inside Oracle NUMBER.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
import json
from pathlib import Path
import random
import re
from typing import Iterable, Iterator, Pattern, Sequence

from bin_mapping import PASS_BIN


TARGET = "DF_DIE_TO_WF_YLD"
QUANTUM = Decimal("0.000000000001")
BACKUP_RE = re.compile(r"^DF_DIE_TO_WF_YLD_BAK_\d{8}$")
BACKUP_DAY = date(2026, 10, 28)
MIGRATION_PATH = Path(__file__).with_name("seed_data") / "wf_yld_migration.json"


@dataclass(frozen=True)
class Metadata:
    lot: str
    week: object
    year: object
    month: object
    lotid: object
    wfid: object
    process: str
    measured_at: object


@dataclass
class SeedPlan:
    rows: list[tuple]
    target_scopes: set[tuple[str, str, str]]
    item_scopes: set[tuple[str, str, str]]
    metadata_changes: "MetadataChanges"
    discarded_params: set[str]
    skipped_missing_item: int
    skipped_no_symbols: int
    eligible: dict[tuple, tuple[list[tuple[str, str]], list[tuple[str, Pattern[str]]]]]


@dataclass(frozen=True)
class MetadataChanges:
    renames: list[tuple]
    inserts: list[tuple]
    deletes: list[tuple]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="wafer bin/yield fraction seed")
    parser.add_argument("--apply", action="store_true", help="write after backup and verification")
    return parser.parse_args()


def load_migration(path: Path = MIGRATION_PATH) -> set[str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    names = {_valid_param(value) for value in document.get("discarded_params", [])}
    if not names:
        raise ValueError("migration discarded_params must not be empty")
    return names


def _valid_param(name: object) -> str:
    value = str(name)
    if not value or len(value.encode("utf-8")) > 100:
        raise ValueError(f"invalid parameter name: {value!r}")
    return value


def load_catalogs(conn) -> tuple[list[tuple], list[tuple]]:
    with closing(conn.cursor()) as cur:
        cur.execute("SELECT LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN FROM DF_SYMBOL_BIN")
        symbols = list(cur)
        cur.execute(
            "SELECT LOT_CD, PROC_GBN_CD, YIELD_TABLE, PARAM_NM, PARAM_SEQNO "
            "FROM DF_ALL_PARA_PRB"
        )
        return symbols, list(cur)


def compile_patterns(rows: Iterable[tuple]) -> dict[tuple[str, str], list[tuple[str, Pattern[str]]]]:
    definitions: dict[tuple[str, str, str], str] = {}
    for lot, prefix, param, expression in rows:
        key = (str(lot), str(prefix), _valid_param(param))
        expression = str(expression)
        if key in definitions and definitions[key] != expression:
            raise ValueError(f"conflicting regex definitions for {key}")
        try:
            re.compile(expression)
        except re.error as exc:
            raise ValueError(f"invalid regex for {key}: {exc}") from exc
        definitions[key] = expression
    result: dict[tuple[str, str], list[tuple[str, Pattern[str]]]] = defaultdict(list)
    for (lot, prefix, param), expression in sorted(definitions.items()):
        result[(lot, prefix)].append((param, re.compile(expression)))
    return dict(result)


def match_process(process: str, prefixes: Iterable[str]) -> str | None:
    process = str(process)
    matches = sorted({prefix for prefix in prefixes
                      if process == prefix or process.startswith(prefix + " ")})
    if len(matches) > 1:
        raise ValueError(f"ambiguous process prefixes for {process!r}: {matches}")
    return matches[0] if matches else None


def parse_map(payload: object) -> list[tuple[str, str]]:
    if hasattr(payload, "read"):
        payload = payload.read()
    try:
        document = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid MAP JSON") from exc
    if not isinstance(document, dict) or not isinstance(document.get("MAP"), list):
        raise ValueError("JSON must contain a MAP list")
    if not document["MAP"]:
        raise ValueError("empty MAP")
    pairs, coordinates = [], set()
    for entry in document["MAP"]:
        if not isinstance(entry, str) or len(entry.split(",")) != 4:
            raise ValueError("MAP entry must have four comma-separated fields")
        x, y, left, right = entry.split(",")
        if not x or not y:
            raise ValueError("MAP coordinates must be non-empty")
        if (x, y) in coordinates:
            raise ValueError(f"duplicate MAP coordinate: {(x, y)}")
        if len(left) != 1 or len(right) != 1:
            raise ValueError("bins must be single characters")
        coordinates.add((x, y))
        pairs.append((left, right))
    return pairs


def compute_ratios(
    pairs: Sequence[tuple[str, str]], patterns: Sequence[tuple[str, Pattern[str]]],
    pass_bin: str, process_param: str,
) -> dict[str, Decimal]:
    if not pairs:
        raise ValueError("empty die map")
    total = Decimal(len(pairs))
    result = {
        process_param: (Decimal(sum(left + right == pass_bin + pass_bin for left, right in pairs)) / total).quantize(
            QUANTUM, rounding=ROUND_HALF_EVEN
        )
    }
    for name, pattern in patterns:
        count = sum(pattern.fullmatch(left + right) is not None for left, right in pairs)
        result[name] = (Decimal(count) / total).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    return result


def _table_params(conn, table: str) -> set[str]:
    if table != TARGET and not BACKUP_RE.fullmatch(table):
        raise ValueError(f"unsafe table name: {table}")
    with closing(conn.cursor()) as cur:
        cur.execute(f"SELECT DISTINCT PARAM FROM {table}")
        return {str(row[0]) for row in cur if row[0] is not None}


def _baseline_table(conn) -> str:
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT TABLE_NAME FROM USER_TABLES "
            "WHERE TABLE_NAME LIKE 'DF_DIE_TO_WF_YLD_BAK_%' ORDER BY TABLE_NAME"
        )
        names = [str(row[0]) for row in cur if BACKUP_RE.fullmatch(str(row[0]))]
        fixed = f"DF_DIE_TO_WF_YLD_BAK_{BACKUP_DAY:%Y%m%d}"
        return fixed if fixed in names else TARGET


def load_item_baseline(
    conn, symbol_scopes: set[tuple[str, str, str]], catalog_rows: list[tuple],
    discarded_params: set[str],
) -> set[tuple[str, str, str]]:
    baseline = _baseline_table(conn)
    source_params = _table_params(conn, baseline)
    items = set()
    for lot, process, _, parameter, _ in catalog_rows:
        scope = (str(lot), str(process), _valid_param(parameter))
        if (scope[2] in source_params and scope[2] not in discarded_params
                and scope[2] != scope[1] and scope not in symbol_scopes):
            items.add(scope)
    return items


def load_wafer_metadata(conn, item_scopes: set[tuple[str, str, str]]) -> dict[tuple, Metadata]:
    if not item_scopes:
        raise ValueError("no protected item parameters found in baseline")
    item_names = {scope[2] for scope in item_scopes}
    binds = {f"p{i}": name for i, name in enumerate(sorted(item_names))}
    placeholders = ",".join(f":p{i}" for i in range(len(binds)))
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT LOT_CD,WEEK,YEAR,MONTH,LOTID,WFID,PROCESS,MEASURETIME_START,PARAM "
            f"FROM {TARGET} WHERE PARAM IN ({placeholders})", binds,
        )
        metadata: dict[tuple, Metadata] = {}
        for lot, week, year, month, lotid, wfid, process, measured_at, parameter in cur:
            if not any(str(lot) == scope_lot
                       and (str(process) == prefix or str(process).startswith(prefix + " "))
                       and str(parameter) == scope_param
                       for scope_lot, prefix, scope_param in item_scopes):
                continue
            try:
                numeric_wfid = int(str(wfid))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid WFID in {TARGET}: {wfid!r}") from exc
            value = Metadata(str(lot), week, year, month, lotid, wfid, str(process), measured_at)
            key = (str(lot), str(lotid), numeric_wfid, str(process))
            if key in metadata and metadata[key] != value:
                raise ValueError(f"inconsistent item metadata for {key}")
            metadata[key] = value
        return metadata


def iter_maps(conn) -> Iterator[tuple]:
    with closing(conn.cursor()) as cur:
        cur.arraysize = 100
        cur.execute("SELECT LOT_CD,LOT_ID,WF_ID,OPER_DET_DESC,END_TM,MAP_VAL_JSON FROM MAP")
        while True:
            batch = cur.fetchmany(100)
            if not batch:
                break
            for lot, lotid, wfid, process, end_tm, payload in batch:
                try:
                    numeric_wfid = int(wfid)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid MAP.WF_ID: {wfid!r}") from exc
                yield str(lot), str(lotid), numeric_wfid, str(process), end_tm, parse_map(payload)


def _metadata_changes(
    catalog_rows: list[tuple], discarded_params: set[str],
    migration_scopes: set[tuple[str, str]],
) -> MetadataChanges:
    groups = defaultdict(list)
    for lot, process, included, parameter, seq in catalog_rows:
        groups[(str(lot), str(process))].append(
            (included, _valid_param(parameter), int(seq)))
    renames, inserts, deletes = [], [], []
    for (lot, process), rows in sorted(groups.items()):
        if (lot, process) not in migration_scopes:
            continue
        names = {parameter for _, parameter, _ in rows}
        stale = sorted((row for row in rows if row[1] in discarded_params), key=lambda row: row[2])
        if process not in names:
            if stale:
                _, old_name, seq = stale[0]
                renames.append((process, lot, process, old_name, seq))
            else:
                used = {seq for _, _, seq in rows}
                seq = next((value for value in range(1, 1001) if value not in used), None)
                if seq is None:
                    raise ValueError(f"PARAM_SEQNO exhausted for {(lot, process)}")
                inserts.append((lot, process, rows[0][0], process, seq))
        deletes.extend((lot, process, parameter) for _, parameter, _ in stale)
    return MetadataChanges(renames, inserts, deletes)


def plan_seed(conn, discarded_params: set[str]) -> SeedPlan:
    symbol_rows, catalog_rows = load_catalogs(conn)
    compiled = compile_patterns(symbol_rows)
    symbol_scopes = {(lot, prefix, name) for (lot, prefix), patterns in compiled.items()
                     for name, _ in patterns}
    item_scopes = load_item_baseline(conn, symbol_scopes, catalog_rows, discarded_params)
    metadata = load_wafer_metadata(conn, item_scopes)
    prefixes_by_lot = defaultdict(set)
    for lot, prefix in compiled:
        prefixes_by_lot[lot].add(prefix)
    target_scopes = set()
    for meta in metadata.values():
        prefix = match_process(meta.process, prefixes_by_lot.get(meta.lot, ()))
        if prefix is not None:
            target_scopes.add((meta.lot, meta.process, prefix))
            target_scopes.update((meta.lot, meta.process, name)
                                 for name, _ in compiled[(meta.lot, prefix)])
    rows, eligible, seen = [], {}, set()
    missing_item = no_symbols = 0
    for lot, lotid, wfid, process, end_tm, pairs in iter_maps(conn):
        key = (lot, lotid, wfid, process)
        if key in seen:
            raise ValueError(f"duplicate MAP wafer-process key: {key}")
        seen.add(key)
        meta = metadata.get(key)
        if meta is None:
            missing_item += 1
            continue
        if meta.measured_at != end_tm:
            raise ValueError(f"MAP.END_TM differs from MEASURETIME_START for {key}")
        prefix = match_process(process, prefixes_by_lot.get(lot, ()))
        if prefix is None:
            no_symbols += 1
            continue
        patterns = compiled[(lot, prefix)]
        if any(name == prefix for name, _ in patterns):
            raise ValueError(f"yield parameter collides with a bin parameter for {(lot, prefix)}")
        ratios = compute_ratios(pairs, patterns, PASS_BIN, prefix)
        for param, value in ratios.items():
            rows.append((meta.lot, meta.week, meta.year, meta.month, meta.lotid, meta.wfid,
                         param, value, meta.process, meta.measured_at))
        eligible[key] = (pairs, patterns)
    expected = sum(len(patterns) + 1 for _, patterns in eligible.values())
    if len(rows) != expected or len({(r[0], str(r[4]), str(r[5]), r[6], r[8]) for r in rows}) != len(rows):
        raise RuntimeError("generated row cardinality or uniqueness check failed")
    if any(not Decimal(0) <= row[7] <= Decimal(1) for row in rows):
        raise RuntimeError("generated fraction outside [0,1]")
    changes = _metadata_changes(catalog_rows, discarded_params, set(compiled))
    return SeedPlan(rows, target_scopes, item_scopes, changes, discarded_params,
                    missing_item, no_symbols, eligible)


def ensure_backup(conn, day: date) -> tuple[str, int, bool]:
    backup = f"DF_DIE_TO_WF_YLD_BAK_{day:%Y%m%d}"
    if not BACKUP_RE.fullmatch(backup):
        raise ValueError("invalid backup identifier")
    with closing(conn.cursor()) as cur:
        cur.execute("SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME=:name", {"name": backup})
        created = cur.fetchone()[0] == 0
        if created:
            cur.execute(f"CREATE TABLE {backup} AS SELECT * FROM {TARGET}")
        cur.execute(f"SELECT COUNT(*) FROM {backup}")
        return backup, int(cur.fetchone()[0]), created


def update_yield_metadata(conn, plan: SeedPlan) -> None:
    changes = plan.metadata_changes
    with closing(conn.cursor()) as cur:
        if changes.renames:
            cur.executemany(
                "UPDATE DF_ALL_PARA_PRB SET PARAM_NM=:1 "
                "WHERE LOT_CD=:2 AND PROC_GBN_CD=:3 AND PARAM_NM=:4 AND PARAM_SEQNO=:5",
                changes.renames,
            )
        if changes.inserts:
            cur.executemany(
                "INSERT INTO DF_ALL_PARA_PRB "
                "(LOT_CD,PROC_GBN_CD,YIELD_TABLE,PARAM_NM,PARAM_SEQNO) VALUES (:1,:2,:3,:4,:5)",
                changes.inserts,
            )
        if changes.deletes:
            cur.executemany(
                "DELETE FROM DF_ALL_PARA_PRB "
                "WHERE LOT_CD=:1 AND PROC_GBN_CD=:2 AND PARAM_NM=:3",
                changes.deletes,
            )


def replace_rows(conn, plan: SeedPlan) -> int:
    with closing(conn.cursor()) as cur:
        deleted = 0
        if plan.target_scopes:
            cur.executemany(
                f"DELETE FROM {TARGET} WHERE LOT_CD=:1 AND PROCESS=:2 AND PARAM=:3",
                sorted(plan.target_scopes),
            )
            deleted += int(cur.rowcount)
        if plan.discarded_params:
            cur.executemany(
                f"DELETE FROM {TARGET} WHERE PARAM=:1",
                [(name,) for name in sorted(plan.discarded_params)],
            )
            deleted += int(cur.rowcount)
        sql = (f"INSERT INTO {TARGET} (LOT_CD,WEEK,YEAR,MONTH,LOTID,WFID,PARAM,VALUE,PROCESS,"
               "MEASURETIME_START) VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9,:10)")
        for start in range(0, len(plan.rows), 1000):
            cur.executemany(sql, plan.rows[start:start + 1000])
        return deleted


def verify_replacement_coverage(conn, plan: SeedPlan) -> None:
    clauses, binds = [], {}
    for index, (lot, process, parameter) in enumerate(sorted(plan.target_scopes)):
        clauses.append(
            f"(LOT_CD=:lot_{index} AND PROCESS=:process_{index} AND PARAM=:param_{index})")
        binds.update({f"lot_{index}": lot, f"process_{index}": process,
                      f"param_{index}": parameter})
    if plan.discarded_params:
        placeholders = []
        for index, parameter in enumerate(sorted(plan.discarded_params)):
            binds[f"old_{index}"] = parameter
            placeholders.append(f":old_{index}")
        clauses.append(f"PARAM IN ({','.join(placeholders)})")
    if not clauses:
        return
    generated = {(str(row[0]), str(row[4]), int(str(row[5])), str(row[8]), str(row[6]))
                 for row in plan.rows}
    missing = []
    with closing(conn.cursor()) as cur:
        cur.execute(
            f"SELECT LOT_CD,LOTID,WFID,PROCESS,PARAM FROM {TARGET} WHERE "
            + " OR ".join(clauses), binds,
        )
        for lot, lotid, wfid, process, parameter in cur:
            expected_param = str(process).split(maxsplit=1)[0] \
                if str(parameter) in plan.discarded_params else str(parameter)
            key = (str(lot), str(lotid), int(str(wfid)), str(process), expected_param)
            if key not in generated:
                missing.append(key)
    if missing:
        raise RuntimeError(
            f"existing replaced rows lack computed counterparts: {len(missing)}; first={missing[0]}"
        )


def _scope_exclusion(scopes: set[tuple[str, str, str]], discarded: set[str]) -> tuple[str, dict]:
    binds = {}
    clauses = []
    for index, (lot, process, parameter) in enumerate(sorted(scopes)):
        binds.update({f"lot_{index}": lot, f"process_{index}": process, f"param_{index}": parameter})
        clauses.append(
            f"(LOT_CD=:lot_{index} AND PROCESS=:process_{index} AND PARAM=:param_{index})")
    if discarded:
        names = []
        for index, name in enumerate(sorted(discarded)):
            binds[f"discarded_{index}"] = name
            names.append(f":discarded_{index}")
        clauses.append(f"PARAM IN ({','.join(names)})")
    return (" AND NOT (" + " OR ".join(clauses) + ")") if clauses else "", binds


def verify_items(conn, backup: str, scopes: set[tuple[str, str, str]], discarded: set[str]) -> None:
    if not BACKUP_RE.fullmatch(backup):
        raise ValueError("invalid backup identifier")
    where, binds = _scope_exclusion(scopes, discarded)
    columns = ("DUMP(LOT_CD,1016),DUMP(WEEK,1016),DUMP(YEAR,1016),DUMP(MONTH,1016),"
               "DUMP(LOTID,1016),DUMP(WFID,1016),DUMP(PARAM,1016),DUMP(VALUE,1016),"
               "DUMP(PROCESS,1016),DUMP(MEASURETIME_START,1016),COUNT(*)")
    grouped = f"SELECT {columns} FROM {{table}} WHERE 1=1{where} GROUP BY " + columns.rsplit(",COUNT(*)", 1)[0]
    with closing(conn.cursor()) as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TARGET} WHERE 1=1{where}", binds)
        current_count = int(cur.fetchone()[0])
        cur.execute(f"SELECT COUNT(*) FROM {backup} WHERE 1=1{where}", binds)
        if current_count != int(cur.fetchone()[0]):
            raise RuntimeError("protected item row count changed")
        left, right = grouped.format(table=TARGET), grouped.format(table=backup)
        difference = (
            f"SELECT * FROM ({left} MINUS {right}) "
            f"UNION ALL SELECT * FROM ({right} MINUS {left})"
        )
        cur.execute(f"SELECT COUNT(*) FROM ({difference})", binds)
        if cur.fetchone()[0]:
            raise RuntimeError("protected item values differ from backup")


def verify_sample(conn, plan: SeedPlan) -> int:
    if len(plan.eligible) < 20:
        raise RuntimeError(f"sample verification needs >=20 eligible wafers; found {len(plan.eligible)}")
    keys = set(random.SystemRandom().sample(list(plan.eligible), 20))
    target_names = {scope[2] for scope in plan.target_scopes}
    binds = {f"p{i}": name for i, name in enumerate(sorted(target_names))}
    names = ",".join(f":p{i}" for i in range(len(binds)))
    with closing(conn.cursor()) as cur:
        cur.execute(
            f"SELECT LOT_CD,LOTID,WFID,PROCESS,PARAM,VALUE FROM {TARGET} "
            f"WHERE PARAM IN ({names})", binds,
        )
        actual = {}
        for lot, lotid, wfid, process, param, value in cur:
            key = (str(lot), str(lotid), int(str(wfid)), str(process))
            if key in keys:
                actual[key + (str(param),)] = Decimal(str(value))
    checked = 0
    for lot, lotid, wfid, process, _, pairs in iter_maps(conn):
        key = (lot, lotid, wfid, process)
        if key not in keys:
            continue
        patterns = plan.eligible[key][1]
        total = Decimal(len(pairs))
        prefix = next(param for scope_lot, scope_process, param in plan.target_scopes
                      if scope_lot == lot and scope_process == process
                      and param == match_process(process, [param]))
        reference = {prefix: sum(left + right == PASS_BIN + PASS_BIN for left, right in pairs)}
        reference.update({name: sum(rx.fullmatch(a + b) is not None for a, b in pairs)
                          for name, rx in patterns})
        for name, count in reference.items():
            expected = (Decimal(count) / total).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
            if actual[(lot, lotid, wfid, process, name)] != expected:
                raise RuntimeError(f"sample verification failed for {key} {name}")
        checked += 1
    if checked != 20:
        raise RuntimeError(f"sample MAP reread found {checked}/20 wafers")
    return checked


def _target_count(conn, scopes: set[tuple[str, str, str]]) -> int:
    with closing(conn.cursor()) as cur:
        total = 0
        for lot, process, name in scopes:
            cur.execute(
                f"SELECT COUNT(*) FROM {TARGET} "
                "WHERE LOT_CD=:lot AND PROCESS=:process AND PARAM=:name",
                {"lot": lot, "process": process, "name": name},
            )
            total += int(cur.fetchone()[0])
        return total


def _verify_generated_counts(conn, plan: SeedPlan) -> None:
    expected = Counter((str(r[0]), str(r[8]), str(r[6])) for r in plan.rows)
    with closing(conn.cursor()) as cur:
        actual = Counter()
        for lot_scope, process_scope, name in plan.target_scopes:
            cur.execute(
                f"SELECT LOT_CD,PROCESS,PARAM,COUNT(*) FROM {TARGET} "
                "WHERE LOT_CD=:lot AND PROCESS=:process AND PARAM=:name "
                "GROUP BY LOT_CD,PROCESS,PARAM",
                {"lot": lot_scope, "process": process_scope, "name": name},
            )
            actual.update({(str(lot), str(process), str(param)): int(count)
                           for lot, process, param, count in cur})
        if actual != expected:
            raise RuntimeError("generated counts by lot/process/parameter differ from plan")


def _print_plan(plan: SeedPlan, existing_targets: int) -> None:
    print(f"[PLAN] delete rows={existing_targets}, insert rows={len(plan.rows)}")
    print(f"[PLAN] target scopes={len(plan.target_scopes)}, metadata inserts={len(plan.metadata_changes.inserts)}, "
          f"renames={len(plan.metadata_changes.renames)}, stale deletes={len(plan.metadata_changes.deletes)}")
    print(f"[PLAN] eligible MAP wafers={len(plan.eligible)}, skipped no item={plan.skipped_missing_item}, "
          f"skipped no symbols={plan.skipped_no_symbols}")
    print(f"[PLAN] generated by PARAM={dict(sorted(Counter(r[6] for r in plan.rows).items()))}")


def main() -> int:
    args = parse_args()
    discarded_params = load_migration()
    from common import get_oracle_connection

    conn = get_oracle_connection()
    try:
        plan = plan_seed(conn, discarded_params)
        existing = _target_count(conn, plan.target_scopes)
        _print_plan(plan, existing)
        if not args.apply:
            today = f"DF_DIE_TO_WF_YLD_BAK_{BACKUP_DAY:%Y%m%d}"
            with closing(conn.cursor()) as cur:
                cur.execute("SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME=:name", {"name": today})
                exists = bool(cur.fetchone()[0])
                if exists:
                    cur.execute(f"SELECT COUNT(*) FROM {today}")
                    backup_status = f"exists=True, rows={int(cur.fetchone()[0])}"
                else:
                    backup_status = "exists=False, rows=n/a"
                print(f"[DRY RUN] today's backup {backup_status}; no writes performed")
            return 0

        backup, backup_count, created = ensure_backup(conn, BACKUP_DAY)
        print(f"[BACKUP] {backup}: rows={backup_count}, created={created}")
        with closing(conn.cursor()) as cur:
            cur.execute(f"LOCK TABLE {TARGET} IN EXCLUSIVE MODE")
        plan = plan_seed(conn, discarded_params)
        verify_replacement_coverage(conn, plan)
        verify_items(conn, backup, plan.target_scopes, plan.discarded_params)
        update_yield_metadata(conn, plan)
        deleted = replace_rows(conn, plan)
        verify_items(conn, backup, plan.target_scopes, plan.discarded_params)
        inserted_count = _target_count(conn, plan.target_scopes)
        if inserted_count != len(plan.rows):
            raise RuntimeError(f"generated row count mismatch: {inserted_count} != {len(plan.rows)}")
        _verify_generated_counts(conn, plan)
        sampled = verify_sample(conn, plan)
        conn.commit()
        print(f"[APPLY] deleted={deleted}, inserted={inserted_count}, sample verified={sampled}, committed")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
