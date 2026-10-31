"""Plan or seed source-qualified wafer parameter metadata.

Dry-run is the default.  ``--apply`` creates missing metadata tables and uses
bound ``MERGE`` statements, preserving unrelated rows.  Oracle DDL commits at
the DDL boundary; a later DML rollback cannot undo table creation or comments.
``--descriptions-only`` requires existing metadata tables and limits changes to
parameter value-basis descriptions and source guides.
Domain wording is loaded from ``seed_data/param_meta_seed.json``.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path


SEED_PATH = Path(__file__).with_name("seed_data") / "param_meta_seed.json"
_KINDS = {"item", "bin", "yield"}
_DIRECTIONS = {"higher", "lower", "neutral", "unknown"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="source-qualified wafer parameter metadata seed")
    parser.add_argument("--apply", action="store_true", help="create/merge metadata rows")
    parser.add_argument("--descriptions-only", action="store_true",
                        help="update descriptions in existing metadata tables only")
    return parser.parse_args()


def load_seed_data(path: Path = SEED_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value: object, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{label} must not be empty")
    return result


def build_seed_plan(catalog_rows, symbol_rows, seed_data) -> dict:
    source_id = _text(seed_data["source_id"], "source_id")
    discarded = {_text(value, "discarded parameter") for value in seed_data.get("discarded_params", [])}
    kinds = seed_data["kinds"]
    if set(kinds) != _KINDS:
        raise ValueError(f"kind definitions must be exactly {sorted(_KINDS)}")
    for kind, definition in kinds.items():
        if definition.get("better_when") not in _DIRECTIONS:
            raise ValueError(f"invalid better_when for {kind}")
        for field in ("unit", "value_basis", "better_when"):
            _text(definition.get(field), f"{kind}.{field}")

    classified: dict[tuple[str, str, str], str] = {}

    def add(lot, process, parameter, kind):
        key = (_text(lot, "lot_cd"), _text(process, "process"), _text(parameter, "param_nm"))
        prior = classified.get(key)
        if prior is not None and prior != kind:
            raise ValueError(f"parameter has multiple parameter kinds for {key}: {prior}, {kind}")
        classified[key] = kind

    for row in catalog_rows:
        if len(row) < 4:
            raise ValueError("DF_ALL_PARA_PRB row must contain at least four columns")
        name = _text(row[3], "catalog param_nm")
        process = _text(row[1], "catalog process")
        if name not in discarded:
            add(row[0], process, name, "yield" if name == process else "item")
    for row in symbol_rows:
        if len(row) < 3:
            raise ValueError("DF_SYMBOL_BIN row must contain at least three columns")
        add(row[0], row[1], row[2], "bin")

    param_rows = []
    for (lot, process, parameter), kind in sorted(classified.items()):
        definition = kinds[kind]
        param_rows.append({"source_id": source_id, "lot_cd": lot, "process": process,
            "param_nm": parameter, "param_kind": kind, "unit": definition["unit"],
            "value_basis": definition["value_basis"], "better_when": definition["better_when"]})

    guides = []
    seen_guides = set()
    for raw in seed_data.get("source_guides", []):
        row = {field: _text(raw[field], field) for field in ("source_id", "question_type", "guidance")}
        key = (row["source_id"], row["question_type"])
        if key in seen_guides:
            raise ValueError(f"duplicate source guide: {key}")
        seen_guides.add(key)
        guides.append(row)
    comments = []
    for raw in seed_data.get("comments", []):
        comments.append({"target": _text(raw["target"], "comment target"),
                         "text": _text(raw["text"], "comment text")})
    return {"param_rows": param_rows, "source_guides": sorted(guides,
            key=lambda row: (row["source_id"], row["question_type"])), "comments": comments,
            "discarded_rows": [{"source_id": source_id, "param_nm": name}
                               for name in sorted(discarded)]}


def _existing_tables(connection) -> set[str]:
    with closing(connection.cursor()) as cursor:
        binds = {"table_0": "DF_PARAM_META", "table_1": "DF_SOURCE_GUIDE"}
        cursor.execute("SELECT TABLE_NAME FROM USER_TABLES "
                       "WHERE TABLE_NAME IN (:table_0, :table_1)", binds)
        return {str(row[0]).upper() for row in cursor}


def _comment_sql(target: str, text: str) -> str:
    parts = target.split()
    if len(parts) != 2 or parts[0] not in {"TABLE", "COLUMN"}:
        raise ValueError(f"invalid comment target: {target}")
    identifier = parts[1]
    if not all(part.replace("_", "").isalnum() for part in identifier.split(".")):
        raise ValueError(f"invalid comment identifier: {identifier}")
    return f"COMMENT ON {parts[0]} {identifier} IS '{text.replace(chr(39), chr(39) * 2)}'"


def apply_seed_plan(connection, plan) -> None:
    existing = _existing_tables(connection)
    with closing(connection.cursor()) as cursor:
        if "DF_PARAM_META" not in existing:
            cursor.execute("CREATE TABLE DF_PARAM_META ("
                "SOURCE_ID VARCHAR2(128) NOT NULL, LOT_CD VARCHAR2(128) NOT NULL, "
                "PROCESS VARCHAR2(256) NOT NULL, PARAM_NM VARCHAR2(256) NOT NULL, "
                "PARAM_KIND VARCHAR2(16) NOT NULL CHECK (PARAM_KIND IN ('item','bin','yield')), "
                "UNIT VARCHAR2(128) NOT NULL, VALUE_BASIS VARCHAR2(4000) NOT NULL, "
                "BETTER_WHEN VARCHAR2(16) NOT NULL CHECK (BETTER_WHEN IN ('higher','lower','neutral','unknown')), "
                "CONSTRAINT PK_DF_PARAM_META PRIMARY KEY (SOURCE_ID,LOT_CD,PROCESS,PARAM_NM))")
        if "DF_SOURCE_GUIDE" not in existing:
            cursor.execute("CREATE TABLE DF_SOURCE_GUIDE ("
                "SOURCE_ID VARCHAR2(128) NOT NULL, QUESTION_TYPE VARCHAR2(128) NOT NULL, "
                "GUIDANCE VARCHAR2(4000) NOT NULL, "
                "CONSTRAINT PK_DF_SOURCE_GUIDE PRIMARY KEY (SOURCE_ID,QUESTION_TYPE))")
        for comment in plan["comments"]:
            cursor.execute(_comment_sql(comment["target"], comment["text"]))
        if plan["discarded_rows"]:
            cursor.executemany(
                "DELETE FROM DF_PARAM_META WHERE SOURCE_ID=:source_id AND PARAM_NM=:param_nm",
                plan["discarded_rows"],
            )
        cursor.executemany(
            "MERGE INTO DF_PARAM_META target USING (SELECT :source_id SOURCE_ID, :lot_cd LOT_CD, "
            ":process PROCESS, :param_nm PARAM_NM, :param_kind PARAM_KIND, :unit UNIT, "
            ":value_basis VALUE_BASIS, :better_when BETTER_WHEN FROM DUAL) source "
            "ON (target.SOURCE_ID=source.SOURCE_ID AND target.LOT_CD=source.LOT_CD "
            "AND target.PROCESS=source.PROCESS AND target.PARAM_NM=source.PARAM_NM) "
            "WHEN MATCHED THEN UPDATE SET target.PARAM_KIND=source.PARAM_KIND, target.UNIT=source.UNIT, "
            "target.VALUE_BASIS=source.VALUE_BASIS, target.BETTER_WHEN=source.BETTER_WHEN "
            "WHEN NOT MATCHED THEN INSERT (SOURCE_ID,LOT_CD,PROCESS,PARAM_NM,PARAM_KIND,UNIT,VALUE_BASIS,BETTER_WHEN) "
            "VALUES (source.SOURCE_ID,source.LOT_CD,source.PROCESS,source.PARAM_NM,source.PARAM_KIND,source.UNIT,source.VALUE_BASIS,source.BETTER_WHEN)",
            plan["param_rows"],
        )
        cursor.executemany(
            "MERGE INTO DF_SOURCE_GUIDE target USING (SELECT :source_id SOURCE_ID, "
            ":question_type QUESTION_TYPE, :guidance GUIDANCE FROM DUAL) source "
            "ON (target.SOURCE_ID=source.SOURCE_ID AND target.QUESTION_TYPE=source.QUESTION_TYPE) "
            "WHEN MATCHED THEN UPDATE SET target.GUIDANCE=source.GUIDANCE "
            "WHEN NOT MATCHED THEN INSERT (SOURCE_ID,QUESTION_TYPE,GUIDANCE) "
            "VALUES (source.SOURCE_ID,source.QUESTION_TYPE,source.GUIDANCE)",
            plan["source_guides"],
        )
    connection.commit()


def build_description_plan(connection, plan) -> dict:
    required = {"DF_PARAM_META", "DF_SOURCE_GUIDE"}
    if not required.issubset(_existing_tables(connection)):
        raise ValueError("--descriptions-only requires existing metadata tables "
                         "DF_PARAM_META and DF_SOURCE_GUIDE")

    with closing(connection.cursor()) as cursor:
        cursor.execute(
            "SELECT SOURCE_ID,LOT_CD,PROCESS,PARAM_NM,VALUE_BASIS FROM DF_PARAM_META"
        )
        existing_params = {
            tuple(row[:4]): row[4]
            for row in cursor
        }
    param_rows = []
    for row in plan["param_rows"]:
        key = (row["source_id"], row["lot_cd"], row["process"], row["param_nm"])
        if key in existing_params and existing_params[key] != row["value_basis"]:
            param_rows.append({
                "source_id": key[0], "lot_cd": key[1], "process": key[2],
                "param_nm": key[3], "before": existing_params[key],
                "after": row["value_basis"],
            })

    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT SOURCE_ID,QUESTION_TYPE,GUIDANCE FROM DF_SOURCE_GUIDE")
        existing_guides = {
            tuple(row[:2]): row[2]
            for row in cursor
        }
    guide_rows = []
    for row in plan["source_guides"]:
        key = (row["source_id"], row["question_type"])
        before = existing_guides.get(key)
        if before != row["guidance"]:
            guide_rows.append({
                "source_id": key[0], "question_type": key[1],
                "before": before, "after": row["guidance"],
            })
    return {"param_rows": param_rows, "source_guides": guide_rows}


def apply_description_plan(connection, plan) -> None:
    with closing(connection.cursor()) as cursor:
        if plan["param_rows"]:
            cursor.executemany(
                "UPDATE DF_PARAM_META SET VALUE_BASIS=:after "
                "WHERE SOURCE_ID=:source_id AND LOT_CD=:lot_cd AND PROCESS=:process "
                "AND PARAM_NM=:param_nm",
                [{key: row[key] for key in
                  ("source_id", "lot_cd", "process", "param_nm", "after")}
                 for row in plan["param_rows"]],
            )
        if plan["source_guides"]:
            cursor.executemany(
                "MERGE INTO DF_SOURCE_GUIDE target USING (SELECT :source_id SOURCE_ID, "
                ":question_type QUESTION_TYPE, :after GUIDANCE FROM DUAL) source "
                "ON (target.SOURCE_ID=source.SOURCE_ID "
                "AND target.QUESTION_TYPE=source.QUESTION_TYPE) "
                "WHEN MATCHED THEN UPDATE SET target.GUIDANCE=source.GUIDANCE "
                "WHEN NOT MATCHED THEN INSERT (SOURCE_ID,QUESTION_TYPE,GUIDANCE) "
                "VALUES (source.SOURCE_ID,source.QUESTION_TYPE,source.GUIDANCE)",
                [{key: row[key] for key in ("source_id", "question_type", "after")}
                 for row in plan["source_guides"]],
            )
    connection.commit()


def _print_description_plan(plan) -> None:
    for row in plan["param_rows"]:
        key = (row["source_id"], row["lot_cd"], row["process"], row["param_nm"])
        print(f"[VALUE_BASIS] key={key!r} before={row['before']!r} after={row['after']!r}")
    for row in plan["source_guides"]:
        key = (row["source_id"], row["question_type"])
        print(f"[GUIDANCE] key={key!r} before={row['before']!r} after={row['after']!r}")


def _load_catalogs(connection):
    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT LOT_CD,PROC_GBN_CD,YIELD_TABLE,PARAM_NM,PARAM_SEQNO FROM DF_ALL_PARA_PRB")
        catalog = list(cursor)
        cursor.execute("SELECT LOT_CD,PROCESS,PARAM_NM,REGEX_PATTERN,DBLOADTIME FROM DF_SYMBOL_BIN")
        return catalog, list(cursor)


def main() -> int:
    args = parse_args()
    seed_data = load_seed_data()
    from common import get_oracle_connection

    connection = get_oracle_connection()
    try:
        catalog_rows, symbol_rows = _load_catalogs(connection)
        plan = build_seed_plan(catalog_rows, symbol_rows, seed_data)
        if args.descriptions_only:
            description_plan = build_description_plan(connection, plan)
            _print_description_plan(description_plan)
            if not args.apply:
                print("[DRY RUN] no DDL or DML executed")
                return 0
            apply_description_plan(connection, description_plan)
            print("[APPLY] description rows merged and committed")
            return 0
        counts = {kind: sum(row["param_kind"] == kind for row in plan["param_rows"]) for kind in sorted(_KINDS)}
        print(f"[PLAN] parameter rows={len(plan['param_rows'])} by kind={counts}; guides={len(plan['source_guides'])}")
        if not args.apply:
            print("[DRY RUN] no DDL or DML executed")
            return 0
        print("[APPLY] Oracle DDL has an implicit commit boundary; creating/annotating tables then merging rows")
        apply_seed_plan(connection, plan)
        print("[APPLY] metadata rows merged and committed")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
