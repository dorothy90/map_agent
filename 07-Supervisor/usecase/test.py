"""
Load /Users/daehwankim/langgraph-v1-tutorial/data.csv into Oracle DB.

Assumptions
- Target table name: LANGGRAPH_DATA
- Create table if it doesn't exist
- map_val_json is stored as CLOB
- Connection info is provided via env vars (do NOT hardcode secrets)

Env vars
- ORACLE_USER
- ORACLE_PASSWORD
- ORACLE_DSN            (e.g. "host:1521/service_name")
- ORACLE_TABLE          (optional, default: LANGGRAPH_DATA)
- ORACLE_BATCH_SIZE     (optional, default: 1000)
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


CSV_PATH = Path(
    "/Users/daehwankim/langgraph-v1-tutorial/07-Supervisor/usecase/data.csv"
)


from dotenv import load_dotenv

load_dotenv(override=True)

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_TABLE = os.getenv("ORACLE_TABLE")
ORACLE_BATCH_SIZE = os.getenv("ORACLE_BATCH_SIZE")


def _maybe_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return int(v)


def ensure_table(conn: Any, table: str) -> None:
    """
    Create table if it does not exist.
    Oracle doesn't have CREATE TABLE IF NOT EXISTS, so we catch ORA-00955.
    """
    ddl = f"""
    CREATE TABLE {table} (
      fab_id        VARCHAR2(50),
      lot_cd        VARCHAR2(50),
      prod_id       VARCHAR2(200),
      oper_det_desc VARCHAR2(4000),
      lot_id        VARCHAR2(100),
      alias_lot_id  VARCHAR2(100),
      wf_id         NUMBER,
      start_tm      TIMESTAMP,
      end_tm        TIMESTAMP,
      rt_cnt        NUMBER,
      map_val_json  CLOB
    )
    """

    cur = conn.cursor()
    try:
        cur.execute(ddl)
    except Exception as e:  # noqa: BLE001
        # ORA-00955: name is already used by an existing object
        msg = str(e)
        if "ORA-00955" not in msg:
            raise
    finally:
        cur.close()


def parse_ts(s: str) -> datetime:
    # matches the CSV format: yyyy-mm-dd hh:mm:ss
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def load_rows(csv_path: Path) -> List[Dict[str, Any]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: List[Dict[str, Any]] = []
        for r in reader:
            rows.append(
                {
                    "fab_id": r["fab_id"],
                    "lot_cd": r["lot_cd"],
                    "prod_id": r.get("prod_id") or None,
                    "oper_det_desc": r.get("oper_det_desc") or None,
                    "lot_id": r["lot_id"],
                    "alias_lot_id": r["alias_lot_id"],
                    "wf_id": int(r["wf_id"]),
                    "start_tm": parse_ts(r["start_tm"]),
                    "end_tm": parse_ts(r["end_tm"]),
                    "rt_cnt": (
                        int(r["rt_cnt"])
                        if (r.get("rt_cnt") not in (None, ""))
                        else None
                    ),
                    "map_val_json": r.get("map_val_json") or None,  # CLOB
                }
            )
    return rows


def chunked(items: List[Dict[str, Any]], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    # Import here so the script fails fast if dependency missing
    import oracledb  # type: ignore

    user = ORACLE_USER
    password = ORACLE_PASSWORD
    dsn = ORACLE_DSN
    table = ORACLE_TABLE
    batch_size = 1000

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    # Thin mode by default is fine for most cases.
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    try:
        ensure_table(conn, table)

        sql = f"""
        INSERT INTO {table} (
          fab_id, lot_cd, prod_id, oper_det_desc, lot_id, alias_lot_id,
          wf_id, start_tm, end_tm, rt_cnt, map_val_json
        ) VALUES (
          :fab_id, :lot_cd, :prod_id, :oper_det_desc, :lot_id, :alias_lot_id,
          :wf_id, :start_tm, :end_tm, :rt_cnt, :map_val_json
        )
        """

        rows = load_rows(CSV_PATH)
        cur = conn.cursor()
        try:
            for batch in chunked(rows, batch_size):
                cur.executemany(sql, batch)
                conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()

    print(f"Loaded {CSV_PATH} into {table}. rows={10000}")


if __name__ == "__main__":
    main()
