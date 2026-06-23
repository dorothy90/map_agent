"""
DF_LEGEND_LONG 생성/적재 스크립트
=================================
legend_long_view 쿼리 결과와 동일한 컬럼 구조의 테이블을 만든다.
    lotid, wfid, step, recipe, chamb_id, eqp_id, route, end_tm

- lotid, wfid : DF_DIE_TO_WF_YLD 의 (LOTID, WFID) 고유 쌍
- step        : DF_WADS_MAIN_OPER.MAIN_OPER (앞서 만든 공정명) — 공정 순서대로
- recipe / chamb_id / eqp_id / route : 반도체 팹에서 쓸 만한 mock 값
- end_tm      : 'YYYY-MM-DD HH24:MI:SS' 문자열 (wafer route 진행에 따라 증가)

사용법:
    python -m seed_legend_long           # 생성(없으면) 후 적재
    python -m seed_legend_long --drop    # 기존 테이블 DROP 후 재생성
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta

import oracledb
from dotenv import load_dotenv

load_dotenv(override=True)

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")

TABLE = "DF_LEGEND_LONG"
SEED = 42
BATCH = 5000

CREATE_DDL = f"""
CREATE TABLE {TABLE} (
    LOTID    VARCHAR2(100),
    WFID     VARCHAR2(100),
    STEP     VARCHAR2(200),
    RECIPE   VARCHAR2(200),
    CHAMB_ID VARCHAR2(50),
    EQP_ID   VARCHAR2(50),
    ROUTE    VARCHAR2(100),
    END_TM   VARCHAR2(19)
)
"""

# 공정 흐름 순서 (front-end → back-end). DF_WADS_MAIN_OPER 풀과 매칭.
STEP_ORDER = [
    "WELL IMPLANT",
    "STI CMP",
    "GATE OX GROWTH",
    "POLY DEP",
    "SPACER ETCH",
    "CONTACT ETCH",
    "PRE METAL CLN",
    "METAL1 DEP",
    "ILD CMP",
    "VIA1 ETCH",
    "IMD DEP",
    "BG ETCH",
    "BG CMP",
    "PASSIVATION",
]

# (route 이름, 테크노드) — lotid마다 1개 배정, recipe 노드와 일관
ROUTES = [
    ("R28-LOGIC-V2", "28N"),
    ("R28-LOGIC-V3", "28N"),
    ("R14-FINFET-V1", "14N"),
    ("R40-LOGIC-V1", "40N"),
]


def _tool_family(step: str) -> str:
    """공정 step → 장비 패밀리 (mock 장비 ID 접두)"""
    s = step.upper()
    if s.endswith("CMP"):
        return "CMP"
    if s.endswith("ETCH"):
        return "ETCH"
    if s.endswith("DEP") or "PASSIVATION" in s:
        return "DEP"
    if "GROWTH" in s or " OX" in s:
        return "FUR"
    if "CLN" in s or "CLEAN" in s:
        return "CLN"
    if "IMPLANT" in s:
        return "IMP"
    return "GEN"


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
        if error.code == 942:
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
        if error.code == 955:
            print(f"테이블 {TABLE} 이미 존재")
        else:
            raise
    finally:
        cur.close()


def seed(conn: oracledb.Connection) -> None:
    cur = conn.cursor()

    # 1) step 풀 = DF_WADS_MAIN_OPER.MAIN_OPER (공정 순서대로 정렬)
    cur.execute("SELECT DISTINCT MAIN_OPER FROM DF_WADS_MAIN_OPER")
    pool = {r[0].strip() for r in cur.fetchall()}
    steps = [s for s in STEP_ORDER if s in pool]
    steps += sorted(pool - set(STEP_ORDER))  # 순서 미지정 공정은 뒤에
    print(f"step 공정 순서({len(steps)}개): {steps}")

    # 2) (lotid, wfid) 고유 쌍
    cur.execute("SELECT DISTINCT LOTID, WFID FROM DF_DIE_TO_WF_YLD ORDER BY LOTID, WFID")
    pairs = cur.fetchall()
    print(f"(lotid, wfid) 고유 쌍: {len(pairs)}개 → 예상 {len(pairs) * len(steps)}행")

    rng = random.Random(SEED)

    # lotid마다 route 1개 고정
    lotids = sorted({lotid for lotid, _ in pairs})
    route_of = {lotid: rng.choice(ROUTES) for lotid in lotids}

    insert_sql = (
        f"INSERT INTO {TABLE} "
        "(LOTID, WFID, STEP, RECIPE, CHAMB_ID, EQP_ID, ROUTE, END_TM) "
        "VALUES (:1, :2, :3, :4, :5, :6, :7, :8)"
    )

    base = datetime(2026, 1, 1, 0, 0, 0)
    buf: list[tuple] = []
    total = 0
    for lotid, wfid in pairs:
        route, node = route_of[lotid]
        # wafer route 시작 시각: 2026-01 ~ 2026-06 사이 무작위
        t = base + timedelta(minutes=rng.randint(0, 60 * 24 * 150))
        for step in steps:
            fam = _tool_family(step)
            recipe = f"{step.replace(' ', '_')}_{node}_R{rng.randint(1, 5):02d}"
            chamb_id = f"PM{rng.randint(1, 6)}"
            eqp_id = f"{fam}{rng.randint(1, 8):02d}"
            end_tm = t.strftime("%Y-%m-%d %H:%M:%S")
            buf.append((lotid, wfid, step, recipe, chamb_id, eqp_id, route, end_tm))
            # 다음 step 까지 1~12시간 진행
            t += timedelta(minutes=rng.randint(60, 720))

        if len(buf) >= BATCH:
            cur.executemany(insert_sql, buf)
            conn.commit()
            total += len(buf)
            print(f"  배치 INSERT: {total}행")
            buf = []

    if buf:
        cur.executemany(insert_sql, buf)
        conn.commit()
        total += len(buf)

    print(f"적재 완료: {total}행")
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
    parser = argparse.ArgumentParser(description="DF_LEGEND_LONG 생성/적재")
    parser.add_argument("--drop", action="store_true", help="기존 테이블 DROP 후 재생성")
    args = parser.parse_args()
    main(drop=args.drop)
