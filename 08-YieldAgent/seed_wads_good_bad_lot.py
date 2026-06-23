"""
DF_WADS_GOOD_BAD_LOT 생성/적재 스크립트
=======================================
DF_WADS_REPORT의 고유 (LOTCD, CATEGORY, PARAMETER) triple마다
DF_TROUBLE_LOT.LOT_ID 풀에서 good_lot_id / bad_lot_id를 무작위로 1개씩 뽑아 적재한다.
(triple당 1행, good != bad)

사용법:
    python -m seed_wads_good_bad_lot           # 생성(없으면) 후 적재
    python -m seed_wads_good_bad_lot --drop    # 기존 테이블 DROP 후 재생성
"""
from __future__ import annotations

import argparse
import os
import random

import oracledb
from dotenv import load_dotenv

load_dotenv(override=True)

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

TABLE = "DF_WADS_GOOD_BAD_LOT"
SEED = 42  # 재현 가능하도록 고정

CREATE_DDL = f"""
CREATE TABLE {TABLE} (
    LOTCD       VARCHAR2(100),
    CATEGORY    VARCHAR2(100),
    PARAMETER   VARCHAR2(100),
    GOOD_LOT_ID VARCHAR2(100),
    BAD_LOT_ID  VARCHAR2(100)
)
"""


def get_connection() -> oracledb.Connection:
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def drop_table_if_exists(conn: oracledb.Connection) -> None:
    cur = conn.cursor()
    try:
        cur.execute(f"DROP TABLE {TABLE} PURGE")
        conn.commit()
        print(f"기존 테이블 {TABLE} 삭제 완료")
    except oracledb.DatabaseError as e:
        (error,) = e.args
        if error.code == 942:  # table does not exist
            print(f"테이블 {TABLE} 없음 → 새로 생성")
        else:
            raise
    finally:
        cur.close()


def create_table(conn: oracledb.Connection) -> None:
    cur = conn.cursor()
    try:
        cur.execute(CREATE_DDL)
        conn.commit()
        print(f"테이블 {TABLE} 생성 완료")
    except oracledb.DatabaseError as e:
        (error,) = e.args
        if error.code == 955:  # name already used
            print(f"테이블 {TABLE} 이미 존재")
        else:
            raise
    finally:
        cur.close()


def seed(conn: oracledb.Connection) -> None:
    cur = conn.cursor()

    # 1) LOT_ID 풀 = DF_TROUBLE_LOT.LOT_ID 고유값
    cur.execute(
        "SELECT DISTINCT LOT_ID FROM DF_TROUBLE_LOT "
        "WHERE LOT_ID IS NOT NULL AND TRIM(LOT_ID) IS NOT NULL"
    )
    pool = sorted(r[0].strip() for r in cur.fetchall())
    print(f"LOT_ID 풀: {len(pool)}개")

    # 2) DF_WADS_REPORT의 고유 triple
    cur.execute("SELECT DISTINCT LOTCD, CATEGORY, PARAMETER FROM DF_WADS_REPORT")
    triples = cur.fetchall()
    print(f"고유 triple: {len(triples)}개")

    # 3) triple마다 good/bad LOT_ID 무작위 1쌍 (good != bad)
    rng = random.Random(SEED)
    rows = []
    for lotcd, category, parameter in triples:
        good, bad = rng.sample(pool, 2)
        rows.append((lotcd, category, parameter, good, bad))

    cur.executemany(
        f"INSERT INTO {TABLE} (LOTCD, CATEGORY, PARAMETER, GOOD_LOT_ID, BAD_LOT_ID) "
        "VALUES (:1, :2, :3, :4, :5)",
        rows,
    )
    conn.commit()
    print(f"적재 완료: {len(rows)}행")

    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    print(f"검증: {TABLE} 총 {cur.fetchone()[0]}행")
    cur.close()


def main(drop: bool) -> None:
    conn = get_connection()
    try:
        if drop:
            drop_table_if_exists(conn)
        create_table(conn)
        seed(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DF_WADS_GOOD_BAD_LOT 생성/적재")
    parser.add_argument("--drop", action="store_true", help="기존 테이블 DROP 후 재생성")
    args = parser.parse_args()
    main(drop=args.drop)
