"""DF_ALL_PARA_PRB 생성, 검증 및 선택적 적재 스크립트.

아래 seed 값은 모두 테스트용 더미다. 대환 님 지시에 따라 임의로 정한 값이며
업무 확정값이 아니다.
"""

from __future__ import annotations

import argparse
from collections import Counter


TABLE_NAME = "DF_ALL_PARA_PRB"
LOT_CODES = ("4SA", "4SS")

# common.PARA_COLUMNS와 같은 순서의 결정적 테스트용 더미 상수다.
PARA_COLUMNS = (
    "VTH", "IDSAT", "IDLIN", "IOFF", "ION", "IGATE", "IDDQ", "VMIN",
    "FMAX", "TPD", "GM_MAX", "SS", "DIBL", "RON", "RDS_ON", "BVDS",
    "LEAK_ID", "LEAK_IG", "CGB", "CGS", "CGD", "CDS", "RSH", "RD", "RS",
)

# common.PT1C_COLUMNS 순서와 동일하며 PT1C=26, CFTA=27을 만든다.
PT1C_COLUMNS = ("PT1C", "CFTA")

# YIELD_TABLE은 o=수율표 표시, x=수율표 제외다.
# 아래 제외 항목은 테스트용 더미(대환 님 지시, 업무 확정값 아님).
YIELD_TABLE_X_KEYS = frozenset(
    {
        ("4SA", "PT1H", "IOFF"),
        ("4SA", "PT1H", "IDDQ"),
        ("4SA", "PT1H", "RSH"),
        ("4SA", "PT1C", "CFTA"),
        ("4SS", "PT1H", "VMIN"),
        ("4SS", "PT1H", "CGB"),
        ("4SS", "PT1H", "RD"),
    }
)

# 행 형식: (LOT_CD, PROC_GBN_CD, YIELD_TABLE, PARAM_NM, PARAM_SEQNO)
ALL_PARA_ROWS = tuple(
    (
        lot_cd,
        proc_gbn_cd,
        "x" if (lot_cd, proc_gbn_cd, param_nm) in YIELD_TABLE_X_KEYS else "o",
        param_nm,
        param_seqno,
    )
    for lot_cd in LOT_CODES
    for proc_gbn_cd, columns, start_seqno in (
        ("PT1H", PARA_COLUMNS, 1),
        ("PT1C", PT1C_COLUMNS, 26),
    )
    for param_seqno, param_nm in enumerate(columns, start=start_seqno)
)
TARGET_KEYS = frozenset((row[0], row[1], row[3]) for row in ALL_PARA_ROWS)

CREATE_DDL = """
CREATE TABLE DF_ALL_PARA_PRB (
    LOT_CD       VARCHAR2(20)  CONSTRAINT NN_DF_ALL_PARA_PRB_LOTCD NOT NULL,
    PROC_GBN_CD  VARCHAR2(10)  CONSTRAINT NN_DF_ALL_PARA_PRB_PROC NOT NULL,
    YIELD_TABLE  CHAR(1)       CONSTRAINT NN_DF_ALL_PARA_PRB_YTBL NOT NULL,
    PARAM_NM     VARCHAR2(100) CONSTRAINT NN_DF_ALL_PARA_PRB_PARAM NOT NULL,
    PARAM_SEQNO  NUMBER(4)     CONSTRAINT NN_DF_ALL_PARA_PRB_SEQNO NOT NULL,
    CONSTRAINT CK_DF_ALL_PARA_PRB_LOT
        CHECK (LOT_CD IN ('4SA', '4SS')),
    CONSTRAINT CK_DF_ALL_PARA_PRB_PROC
        CHECK (PROC_GBN_CD IN ('PT1C', 'PT1H')),
    CONSTRAINT CK_DF_ALL_PARA_PRB_YTBL
        CHECK (YIELD_TABLE IN ('o', 'x')),
    CONSTRAINT CK_DF_ALL_PARA_PRB_SEQNO
        CHECK (PARAM_SEQNO BETWEEN 1 AND 1000),
    CONSTRAINT UQ_DF_ALL_PARA_PRB_PARAM
        UNIQUE (LOT_CD, PROC_GBN_CD, PARAM_NM)
)
""".strip()

COMMENT_DDLS = (
    "COMMENT ON TABLE DF_ALL_PARA_PRB IS "
    "'제품/검사공정별 테스트용 더미 수율 파라미터 설정 (업무 확정값 아님)'",
    "COMMENT ON COLUMN DF_ALL_PARA_PRB.LOT_CD IS "
    "'제품 코드: 현재 4SA 또는 4SS'",
    "COMMENT ON COLUMN DF_ALL_PARA_PRB.PROC_GBN_CD IS "
    "'검사공정 구분: PT1C 또는 PT1H'",
    "COMMENT ON COLUMN DF_ALL_PARA_PRB.YIELD_TABLE IS "
    "'수율표 표시 여부 o/x; 테스트용 더미이며 업무 확정값 아님'",
    "COMMENT ON COLUMN DF_ALL_PARA_PRB.PARAM_NM IS "
    "'DF_DIE_TO_WF_YLD.PARAM 값'",
    "COMMENT ON COLUMN DF_ALL_PARA_PRB.PARAM_SEQNO IS "
    "'테스트 편의용 더미 표시 순서; 업무 확정값 아님'",
)


def get_oracle_connection():
    """DB 작업을 요청한 경우에만 common과 접속 설정을 로드한다."""
    from common import get_oracle_connection as connect

    return connect()


def validate_seed_rows() -> None:
    if len(ALL_PARA_ROWS) != 54 or len(TARGET_KEYS) != 54:
        raise ValueError("DF_ALL_PARA_PRB seed는 중복 없는 54행이어야 합니다.")
    for lot_cd in LOT_CODES:
        lot_rows = [row for row in ALL_PARA_ROWS if row[0] == lot_cd]
        seqnos = [row[4] for row in lot_rows]
        if seqnos != list(range(1, 28)):
            raise ValueError(f"{lot_cd} PARAM_SEQNO가 1~27 연속이 아닙니다.")
    if {row[2] for row in ALL_PARA_ROWS} != {"o", "x"}:
        raise ValueError("YIELD_TABLE은 o/x를 모두 포함해야 합니다.")


def table_exists(conn) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME = :table_name",
            {"table_name": TABLE_NAME},
        )
        return cursor.fetchone()[0] == 1
    finally:
        cursor.close()


def create_table(conn) -> bool:
    if table_exists(conn):
        print(f"중단: {TABLE_NAME} 테이블이 이미 존재합니다.")
        return False

    cursor = conn.cursor()
    table_created = False
    try:
        cursor.execute(CREATE_DDL)
        table_created = True
        for ddl in COMMENT_DDLS:
            cursor.execute(ddl)
        print(f"생성 완료: {TABLE_NAME} (INSERT는 수행하지 않았습니다.)")
        return True
    except Exception:
        if table_created:
            print(
                f"부분 생성: {TABLE_NAME} 테이블은 생성됐지만 주석 DDL이 실패했습니다. "
                "Oracle DDL은 자동 커밋되므로 SQL 문서의 롤백 문을 검토하세요."
            )
        raise
    finally:
        cursor.close()


def verify_table(conn) -> bool:
    if not table_exists(conn):
        print(f"검증 불가: {TABLE_NAME} 테이블이 없습니다.")
        return False

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COLUMN_ID, COLUMN_NAME, DATA_TYPE, DATA_LENGTH,
                   DATA_PRECISION, DATA_SCALE, NULLABLE
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = :table_name
            ORDER BY COLUMN_ID
            """,
            {"table_name": TABLE_NAME},
        )
        print("[USER_TAB_COLUMNS]")
        for row in cursor:
            print(row)

        cursor.execute(
            """
            SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE, SEARCH_CONDITION, STATUS
            FROM USER_CONSTRAINTS
            WHERE TABLE_NAME = :table_name
            ORDER BY CONSTRAINT_NAME
            """,
            {"table_name": TABLE_NAME},
        )
        print("[USER_CONSTRAINTS]")
        for row in cursor:
            print(row)

        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        print(f"[ROW COUNT] {cursor.fetchone()[0]}")
        return True
    finally:
        cursor.close()


def fetch_target_rows(cursor) -> list[tuple[str, str, str, str, int]]:
    cursor.execute(
        f"""
        SELECT LOT_CD, PROC_GBN_CD, YIELD_TABLE, PARAM_NM, PARAM_SEQNO
        FROM {TABLE_NAME}
        WHERE LOT_CD IN ('4SA', '4SS')
          AND PROC_GBN_CD IN ('PT1H', 'PT1C')
        """
    )
    rows = [
        (
            str(lot_cd).strip(),
            str(proc_gbn_cd).strip(),
            str(yield_table).strip(),
            str(param_nm).strip(),
            int(param_seqno),
        )
        for lot_cd, proc_gbn_cd, yield_table, param_nm, param_seqno in cursor
    ]
    return [row for row in rows if (row[0], row[1], row[3]) in TARGET_KEYS]


def print_summary(rows, *, inserted: int, skipped: int, label: str) -> None:
    ordered = sorted(rows, key=lambda row: (row[0], row[4], row[1], row[3]))
    by_process = Counter((row[0], row[1]) for row in ordered)
    by_yield_table = Counter(row[2] for row in ordered)
    print(f"[{label}] 대상 {len(ordered)}행 / 신규 {inserted}행 / 기존 키 skip {skipped}행")
    print(f"[제품·공정별] {dict(sorted(by_process.items()))}")
    print(f"[YIELD_TABLE별] {dict(sorted(by_yield_table.items()))}")
    print("[샘플 8행]")
    for row in ordered[:8]:
        print(row)


def insert_rows(conn) -> int:
    validate_seed_rows()
    cursor = conn.cursor()
    try:
        existing_rows = fetch_target_rows(cursor)
        existing_keys = {(row[0], row[1], row[3]) for row in existing_rows}
        pending_rows = [
            row
            for row in ALL_PARA_ROWS
            if (row[0], row[1], row[3]) not in existing_keys
        ]

        if pending_rows:
            cursor.executemany(
                f"""
                INSERT INTO {TABLE_NAME}
                    (LOT_CD, PROC_GBN_CD, YIELD_TABLE, PARAM_NM, PARAM_SEQNO)
                VALUES (:1, :2, :3, :4, :5)
                """,
                pending_rows,
            )

        verified_rows = fetch_target_rows(cursor)
        verified_keys = {(row[0], row[1], row[3]) for row in verified_rows}
        missing_keys = TARGET_KEYS - verified_keys
        if missing_keys:
            raise RuntimeError(f"INSERT 후 대상 키 {len(missing_keys)}개가 누락됐습니다.")

        conn.commit()
        print_summary(
            verified_rows,
            inserted=len(pending_rows),
            skipped=len(existing_keys),
            label="적재 결과",
        )
        print("[제약 확인] INSERT와 COMMIT 성공; 제약 위반 없음")
        return len(pending_rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DF_ALL_PARA_PRB 생성/검증/적재")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--dry-run", action="store_true",
        help="DB 연결 없이 결정적 테스트용 더미 54행을 검증하고 요약",
    )
    actions.add_argument(
        "--verify", action="store_true",
        help="테이블 컬럼, 제약 및 행 수를 읽기 전용으로 조회",
    )
    actions.add_argument(
        "--insert", action="store_true",
        help="테스트용 더미 54행 중 기존 키를 제외하고 단일 트랜잭션으로 적재",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_seed_rows()

    if args.dry_run:
        print_summary(ALL_PARA_ROWS, inserted=0, skipped=0, label="DRY RUN")
        print("DB 연결 및 INSERT를 수행하지 않았습니다.")
        return 0

    conn = get_oracle_connection()
    try:
        if args.verify:
            return 0 if verify_table(conn) else 1
        if args.insert:
            if not table_exists(conn):
                print(f"적재 불가: {TABLE_NAME} 테이블이 없습니다.")
                return 1
            insert_rows(conn)
            return 0
        return 0 if create_table(conn) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
