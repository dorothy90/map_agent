"""DF_SYMBOL_BIN 생성, 검증 및 선택적 적재 스크립트.

bin 문자 대응은 ``bin_mapping.py``의 통합 대응표를 사용한다. 모든 행은 사용자
정의 bin 비교 문자열인 left_bin+right_bin 중 어느 쪽이든 매칭(either)하며,
Oracle/Python 호환 ``[^ ]``와 ``^...$``를 쓴다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import re

from bin_mapping import BIN_CHAR_TO_PARAM


TABLE_NAME = "DF_SYMBOL_BIN"
LOT_CODES = ("4SA", "4SS")

BASE_PARAMS = tuple(
    (f"{param_name}({symbol})", symbol)
    for symbol, param_name in BIN_CHAR_TO_PARAM.items()
)

# 소문자 d는 실데이터에 없어 전 제품·공정에서 0건을 의도하는 대조군이다.
DDA_PARAM = ("DDA(d)", "d")


def make_regex(symbol: str, kind: str) -> str:
    """left_bin+right_bin 이어붙인 문자열 전체를 ``^...$``로 판정하는
    테스트용 더미 패턴(Oracle/Python 호환 ``[^ ]`` 표기).
    """
    assert len(symbol) == 1 and symbol.isalnum(), (
        "symbol은 정규식 메타문자가 아닌 영숫자 한 글자여야 합니다."
    )
    if kind == "left":
        return f"^{symbol}[^ ]$"
    if kind == "right":
        return f"^[^ ]{symbol}$"
    if kind == "either":
        return f"^({symbol}[^ ]|[^ ]{symbol})$"
    raise ValueError(f"지원하지 않는 pattern kind: {kind!r}")


def pattern_kind(param_nm: str, process: str) -> str:
    """모든 행은 left+right 문자열 중 어느 쪽이든 매칭(either)한다."""
    return "either"


# 행 형식: (LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN). DBLOADTIME은 DB default 사용.
def params_for(lot_cd: str, process: str) -> tuple[tuple[str, str], ...]:
    if (lot_cd, process) == ("4SA", "PT1H"):
        return BASE_PARAMS + (DDA_PARAM,)
    uppercase_params = tuple(
        (param_nm, symbol)
        for param_nm, symbol in BASE_PARAMS
        if "B" <= symbol <= "Z"
    )
    return uppercase_params + (DDA_PARAM,)


SYMBOL_BIN_ROWS = tuple(
    (
        lot_cd,
        process,
        param_nm,
        make_regex(
            symbol,
            pattern_kind(param_nm, process),
        ),
    )
    for lot_cd in LOT_CODES
    for process in ("PT1H", "PT1C")
    for param_nm, symbol in params_for(lot_cd, process)
)
TARGET_KEYS = frozenset((row[0], row[1], row[2]) for row in SYMBOL_BIN_ROWS)

CREATE_DDL = """
CREATE TABLE DF_SYMBOL_BIN (
    LOT_CD         VARCHAR2(20)  CONSTRAINT NN_DF_SYMBOL_BIN_LOTCD NOT NULL,
    PROCESS        VARCHAR2(10)  CONSTRAINT NN_DF_SYMBOL_BIN_PROCESS NOT NULL,
    PARAM_NM       VARCHAR2(100) CONSTRAINT NN_DF_SYMBOL_BIN_PARAM NOT NULL,
    REGEX_PATTERN  VARCHAR2(200) CONSTRAINT NN_DF_SYMBOL_BIN_REGEX NOT NULL,
    DBLOADTIME     TIMESTAMP(6) DEFAULT SYSTIMESTAMP
                                 CONSTRAINT NN_DF_SYMBOL_BIN_LOADTM NOT NULL,
    CONSTRAINT CK_DF_SYMBOL_BIN_LOT
        CHECK (LOT_CD IN ('4SA', '4SS')),
    CONSTRAINT CK_DF_SYMBOL_BIN_PROCESS
        CHECK (PROCESS IN ('PT1C', 'PT1H')),
    CONSTRAINT UQ_DF_SYMBOL_BIN_PARAM
        UNIQUE (LOT_CD, PROCESS, PARAM_NM)
)
""".strip()

COMMENT_DDLS = (
    "COMMENT ON TABLE DF_SYMBOL_BIN IS "
    "'제품/검사공정별 테스트용 더미 bin 정규식 설정 (업무 확정값 아님)'",
    "COMMENT ON COLUMN DF_SYMBOL_BIN.LOT_CD IS "
    "'제품 코드: 현재 4SA 또는 4SS'",
    "COMMENT ON COLUMN DF_SYMBOL_BIN.PROCESS IS "
    "'검사공정 구분: PT1C 또는 PT1H'",
    "COMMENT ON COLUMN DF_SYMBOL_BIN.PARAM_NM IS "
    "'통합 bin 문자-이름 대응의 이름(문자); WADS 기준 및 테스트데이터 임의 배정'",
    "COMMENT ON COLUMN DF_SYMBOL_BIN.REGEX_PATTERN IS "
    "'사용자 정의 bin 비교 문자열=left+right 중 어느 쪽이든 매칭(either); 전체 문자열 ^...$, 비공백 [^ ]'",
    "COMMENT ON COLUMN DF_SYMBOL_BIN.DBLOADTIME IS "
    "'DB 업로드 시각'",
)


def get_oracle_connection():
    """DB 작업을 요청한 경우에만 common과 접속 설정을 로드한다."""
    from common import get_oracle_connection as connect

    return connect()


def validate_seed_rows() -> None:
    symbols = tuple(BIN_CHAR_TO_PARAM)
    names = tuple(BIN_CHAR_TO_PARAM.values())
    if len(BIN_CHAR_TO_PARAM) != 50:
        raise ValueError("BIN_CHAR_TO_PARAM은 50개 대응이어야 합니다.")
    if len(set(symbols)) != 50 or len(set(names)) != 50:
        raise ValueError("BIN_CHAR_TO_PARAM의 문자와 이름은 각각 중복이 없어야 합니다.")
    if not set("BCDEFGHIJKLMNOPQRSTUVWXYZ").issubset(symbols) or not all(
        len(symbol) == 1 and symbol.isalnum() for symbol in symbols
    ):
        raise ValueError("대응 문자는 대문자 B~Z를 포함하는 영숫자 한 글자여야 합니다.")
    base_names = {param_nm for param_nm, _ in BASE_PARAMS}
    expected_names = {
        f"{param_name}({symbol})"
        for symbol, param_name in BIN_CHAR_TO_PARAM.items()
    }
    if base_names != expected_names:
        raise ValueError("50개 PARAM_NM은 BIN_CHAR_TO_PARAM과 정확히 일치해야 합니다.")
    if len(SYMBOL_BIN_ROWS) != 129 or len(TARGET_KEYS) != 129:
        raise ValueError("DF_SYMBOL_BIN seed는 중복 없는 129행이어야 합니다.")
    counts = Counter((row[0], row[1]) for row in SYMBOL_BIN_ROWS)
    expected = {
        ("4SA", "PT1H"): 51,
        ("4SA", "PT1C"): 26,
        ("4SS", "PT1H"): 26,
        ("4SS", "PT1C"): 26,
    }
    if counts != expected:
        raise ValueError(f"제품·공정별 seed 건수가 잘못됐습니다: {dict(counts)}")
    for lot_cd, process, param_nm, regex_pattern in SYMBOL_BIN_ROWS:
        symbols = dict(params_for(lot_cd, process))
        expected_pattern = make_regex(
            symbols[param_nm],
            pattern_kind(param_nm, process),
        )
        if regex_pattern != expected_pattern:
            raise ValueError(f"잘못된 정규식: {param_nm} -> {regex_pattern!r}")
    dda_rows = [row for row in SYMBOL_BIN_ROWS if row[2] == DDA_PARAM[0]]
    if len(dda_rows) != 4 or any(row[3] != "^(d[^ ]|[^ ]d)$" for row in dda_rows):
        raise ValueError(
            "DDA(d)는 모든 제품·공정에서 left+right 중 어느 쪽이든 "
            "매칭(either)하는 4행이어야 합니다."
        )


def validate_python_regex_samples() -> None:
    """사용자 정의 bin 비교 문자열=left+right의 either 매칭을 자체 점검한다."""
    samples = ("?B", "B?", "BB", "?7", "7?", "77", "F11F11", "D?")
    expected_matches = {
        "left": {"B?", "BB"},
        "right": {"?B", "BB"},
        "either": {"?B", "B?", "BB"},
    }
    for kind, expected in expected_matches.items():
        pattern = make_regex("B", kind)
        actual = {sample for sample in samples if re.fullmatch(pattern, sample)}
        if actual != expected:
            raise ValueError(
                f"Python regex 자체 점검 실패: {kind} {pattern!r} -> {sorted(actual)}"
            )


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

        # Oracle/Python 공통 표기 [^ ]의 앵커·길이 동작을 읽기 전용 확인한다.
        cursor.execute(
            """
            SELECT CASE WHEN REGEXP_LIKE('BA', '^B[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END,
                   CASE WHEN REGEXP_LIKE('B ', '^B[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END,
                   CASE WHEN REGEXP_LIKE('F11F11', '^F[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END
            FROM DUAL
            """
        )
        nonspace_result, space_result, long_result = cursor.fetchone()
        print(
            "[ORACLE REGEX DIALECT] BA:", nonspace_result,
            "/ B+공백:", space_result,
            "/ F11F11:", long_result,
            "(기대: MATCH / NO MATCH / NO MATCH)",
        )
        return True
    finally:
        cursor.close()


def fetch_target_rows(cursor) -> list[tuple[str, str, str, str]]:
    cursor.execute(
        f"""
        SELECT LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN
        FROM {TABLE_NAME}
        WHERE LOT_CD IN ('4SA', '4SS')
          AND PROCESS IN ('PT1H', 'PT1C')
        """
    )
    rows = [
        (
            str(lot_cd).strip(), str(process).strip(),
            str(param_nm).strip(), str(regex_pattern).strip(),
        )
        for lot_cd, process, param_nm, regex_pattern in cursor
    ]
    return [row for row in rows if (row[0], row[1], row[2]) in TARGET_KEYS]


def print_summary(rows, *, inserted: int, skipped: int, label: str) -> None:
    param_order = {
        param_nm: index
        for index, (param_nm, _) in enumerate(BASE_PARAMS + (DDA_PARAM,))
    }
    ordered = sorted(
        rows,
        key=lambda row: (row[0], 0 if row[1] == "PT1H" else 1, param_order[row[2]]),
    )
    by_process = Counter((row[0], row[1]) for row in ordered)
    print(f"[{label}] 대상 {len(ordered)}행 / 신규 {inserted}행 / 기존 키 skip {skipped}행")
    print(f"[제품·공정별] {dict(sorted(by_process.items()))}")
    print("[샘플 8행]")
    for row in ordered[:8]:
        print(row)


def insert_rows(conn) -> int:
    validate_seed_rows()
    cursor = conn.cursor()
    try:
        existing_rows = fetch_target_rows(cursor)
        existing_keys = {(row[0], row[1], row[2]) for row in existing_rows}
        pending_rows = [
            row for row in SYMBOL_BIN_ROWS if (row[0], row[1], row[2]) not in existing_keys
        ]

        if pending_rows:
            cursor.executemany(
                f"""
                INSERT INTO {TABLE_NAME}
                    (LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN)
                VALUES (:1, :2, :3, :4)
                """,
                pending_rows,
            )

        verified_rows = fetch_target_rows(cursor)
        verified_keys = {(row[0], row[1], row[2]) for row in verified_rows}
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
    parser = argparse.ArgumentParser(description="DF_SYMBOL_BIN 생성/검증/적재")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--dry-run", action="store_true",
        help="DB 연결 없이 결정적 테스트용 더미 129행을 검증하고 요약",
    )
    actions.add_argument(
        "--verify", action="store_true",
        help="테이블/제약/행 수와 Oracle 정규식 방언을 읽기 전용으로 확인",
    )
    actions.add_argument(
        "--insert", action="store_true",
        help="테스트용 더미 129행 중 기존 키를 제외하고 단일 트랜잭션으로 적재",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_seed_rows()

    if args.dry_run:
        validate_python_regex_samples()
        print_summary(SYMBOL_BIN_ROWS, inserted=0, skipped=0, label="DRY RUN")
        print("[PYTHON REGEX] 사용자 정의 bin 비교 문자열=left+right either 샘플 자체 점검 통과")
        print("안내: 비공백 한 글자는 Oracle/Python 호환 표기 [^ ]를 사용합니다.")
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
