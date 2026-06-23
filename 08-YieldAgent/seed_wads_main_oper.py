"""
DF_WADS_MAIN_OPER 생성/적재 스크립트
====================================
DF_WADS_REPORT의 고유 (LOTCD, CATEGORY, PARAMETER) triple마다
DF_SAMPLE_SPLIT.OPER_DESC 풀에서 메인공정명을 10~14개 추출해 적재한다.
(요청은 10~20개지만 OPER_DESC 고유값이 14개뿐이라 상한이 14)

사용법:
    python -m seed_wads_main_oper           # 생성(없으면) 후 적재
    python -m seed_wads_main_oper --drop    # 기존 테이블 DROP 후 재생성
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

TABLE = "DF_WADS_MAIN_OPER"
SEED = 42  # 재현 가능하도록 고정

CREATE_DDL = f"""
CREATE TABLE {TABLE} (
    LOTCD     VARCHAR2(100),
    CATEGORY  VARCHAR2(100),
    PARAMETER VARCHAR2(100),
    MAIN_OPER VARCHAR2(200)
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

    # 1) 메인공정명 풀 = DF_SAMPLE_SPLIT.OPER_DESC 고유값
    cur.execute(
        "SELECT DISTINCT OPER_DESC FROM DF_SAMPLE_SPLIT "
        "WHERE OPER_DESC IS NOT NULL AND TRIM(OPER_DESC) IS NOT NULL"
    )
    pool = sorted(r[0].strip() for r in cur.fetchall())
    print(f"OPER_DESC 풀: {len(pool)}개 → {pool}")

    # 2) DF_WADS_REPORT의 고유 triple
    cur.execute("SELECT DISTINCT LOTCD, CATEGORY, PARAMETER FROM DF_WADS_REPORT")
    triples = cur.fetchall()
    print(f"고유 triple: {len(triples)}개")

    # 3) triple마다 10~min(14, len(pool))개 랜덤 추출
    rng = random.Random(SEED)
    hi = min(20, len(pool))
    lo = min(10, hi)
    rows = []
    for lotcd, category, parameter in triples:
        k = rng.randint(lo, hi)
        for main_oper in rng.sample(pool, k):
            rows.append((lotcd, category, parameter, main_oper))

    cur.executemany(
        f"INSERT INTO {TABLE} (LOTCD, CATEGORY, PARAMETER, MAIN_OPER) "
        "VALUES (:1, :2, :3, :4)",
        rows,
    )
    conn.commit()
    print(f"적재 완료: {len(rows)}행 (triple {len(triples)}개)")

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
    parser = argparse.ArgumentParser(description="DF_WADS_MAIN_OPER 생성/적재")
    parser.add_argument("--drop", action="store_true", help="기존 테이블 DROP 후 재생성")
    args = parser.parse_args()
    main(drop=args.drop)
