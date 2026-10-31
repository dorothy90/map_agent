"""DF_BMW_PNG 테이블 생성, 검증 및 테스트용 더미 적재 스크립트."""

from __future__ import annotations

import argparse
import base64
import struct
import time
import zlib


TABLE_NAME = "DF_BMW_PNG"
SEED_DT = "20260822"
IMG_TYPES = ("BMWC", "BMWG", "GMW")
BATCH_SIZE = 1000
MAX_DUMMY_PNG_BYTES = 512

# 실제 맵 이미지가 아닌, IMG_TYP 구분용 8x8 단색 테스트 더미 RGB 색상.
DUMMY_PNG_RGB_BY_IMG_TYPE = {
    "BMWC": (0, 102, 255),
    "BMWG": (0, 180, 80),
    "GMW": (220, 40, 40),
}

CREATE_DDL = """
CREATE TABLE DF_BMW_PNG (
    ALIAS_LOT_ID  VARCHAR2(50)
                  CONSTRAINT NN_DF_BMW_PNG_ALIAS NOT NULL,
    WF_ID         VARCHAR2(10)
                  CONSTRAINT NN_DF_BMW_PNG_WF_ID NOT NULL,
    IMG_TYP       VARCHAR2(10)
                  CONSTRAINT NN_DF_BMW_PNG_IMG_TYP NOT NULL,
    DT            VARCHAR2(8)
                  CONSTRAINT NN_DF_BMW_PNG_DT NOT NULL,
    IMAGE_B64     CLOB,
    PDF_FILE_PATH VARCHAR2(500),
    CONSTRAINT CK_DF_BMW_PNG_IMG_TYP
        CHECK (IMG_TYP IN ('BMWC', 'BMWG', 'GMW')),
    CONSTRAINT CK_DF_BMW_PNG_DT
        CHECK (REGEXP_LIKE(DT, '^[0-9]{8}$')),
    CONSTRAINT UQ_DF_BMW_PNG_KEY
        UNIQUE (ALIAS_LOT_ID, WF_ID, IMG_TYP, DT)
)
""".strip()

COMMENT_DDLS = (
    "COMMENT ON TABLE DF_BMW_PNG IS "
    "'Lot/wafer별 BMWC, BMWG, GMW 맵 이미지 및 PDF 경로'",
    "COMMENT ON COLUMN DF_BMW_PNG.ALIAS_LOT_ID IS "
    "'Lot ID 또는 T 접두 별칭; 기존 30자보다 여유 있는 50자'",
    "COMMENT ON COLUMN DF_BMW_PNG.WF_ID IS "
    "'웨이퍼 번호 문자열; 현재 데이터는 패딩 없는 정수 문자열(1~25); "
    "MAP.WF_ID(NUMBER)와 조인 시 TO_CHAR(MAP.WF_ID) 또는 TO_NUMBER(WF_ID) 필요; "
    "문자형 WFID와는 직접 비교 가능'",
    "COMMENT ON COLUMN DF_BMW_PNG.IMG_TYP IS "
    "'이미지 유형: BMWC, BMWG 또는 GMW'",
    "COMMENT ON COLUMN DF_BMW_PNG.DT IS "
    "'YYYYMMDD 형식 기준일; 문자열은 원문 보존이 쉽지만 날짜 연산에는 변환 필요'",
    "COMMENT ON COLUMN DF_BMW_PNG.IMAGE_B64 IS "
    "'맵 이미지 base64 문자열; 크기 한도 미정, 기본 CLOB 저장 옵션 사용'",
    "COMMENT ON COLUMN DF_BMW_PNG.PDF_FILE_PATH IS "
    "'관련 PDF 파일 경로'",
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def build_dummy_png(img_type: str) -> bytes:
    """IMG_TYP별로 색이 다른 유효한 8x8 RGB 테스트 더미 PNG를 만든다."""
    try:
        rgb = bytes(DUMMY_PNG_RGB_BY_IMG_TYPE[img_type])
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 IMG_TYP: {img_type}") from exc

    width = height = 8
    raw_scanlines = b"".join(b"\x00" + rgb * width for _ in range(height))
    png = b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(raw_scanlines)),
            _png_chunk(b"IEND", b""),
        )
    )
    assert len(png) <= MAX_DUMMY_PNG_BYTES, "테스트 더미 PNG가 크기 제한을 넘었습니다."
    return png


def build_dummy_image_b64_by_type() -> dict[str, str]:
    return {
        img_type: base64.b64encode(build_dummy_png(img_type)).decode("ascii")
        for img_type in IMG_TYPES
    }


def get_oracle_connection():
    """DB 작업을 요청한 경우에만 common과 접속 설정을 로드한다."""
    from common import get_oracle_connection as connect

    return connect()


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
        print(f"생성 완료: {TABLE_NAME} (빈 테이블, INSERT는 수행하지 않았습니다.)")
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
        columns = list(cursor)
        for row in columns:
            print(row)

        wf_id_data_type = next(
            (row[2] for row in columns if row[1] == "WF_ID"), None
        )
        wf_id_type_ok = wf_id_data_type == "VARCHAR2"
        print(
            f"[WF_ID DATA_TYPE] {wf_id_data_type or 'NOT FOUND'} "
            f"({'OK' if wf_id_type_ok else 'EXPECTED VARCHAR2'})"
        )

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
        return wf_id_type_ok
    finally:
        cursor.close()


def fetch_map_pairs(cursor) -> list[tuple[str, str]]:
    cursor.execute(
        "SELECT DISTINCT LOT_ID, TO_CHAR(WF_ID) AS WF_ID "
        "FROM MAP ORDER BY LOT_ID, WF_ID"
    )
    return [(str(lot_id), str(wf_id)) for lot_id, wf_id in cursor]


def fetch_existing_keys(cursor) -> set[tuple[str, str, str, str]]:
    cursor.execute(
        f"""
        SELECT ALIAS_LOT_ID, WF_ID, IMG_TYP, DT
        FROM {TABLE_NAME}
        WHERE DT = :dt
          AND IMG_TYP IN ('BMWC', 'BMWG', 'GMW')
        """,
        {"dt": SEED_DT},
    )
    return {
        (str(lot_id), str(wf_id), str(img_type), str(dt))
        for lot_id, wf_id, img_type, dt in cursor
    }


def build_target_rows(
    map_pairs: list[tuple[str, str]], image_b64_by_type: dict[str, str]
) -> list[dict[str, object]]:
    return [
        {
            "alias_lot_id": lot_id,
            "wf_id": wf_id,
            "img_type": img_type,
            "dt": SEED_DT,
            "image_b64": image_b64_by_type[img_type],
            "pdf_file_path": f"/dummy/pdf/{lot_id}_{wf_id}_{img_type}.pdf",
        }
        for lot_id, wf_id in map_pairs
        for img_type in IMG_TYPES
    ]


def _row_key(row: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(row["alias_lot_id"]),
        str(row["wf_id"]),
        str(row["img_type"]),
        str(row["dt"]),
    )


def print_seed_summary(
    *, map_pair_count: int, target_count: int, skipped_count: int,
    inserted_count: int, pending_count: int,
    image_b64_by_type: dict[str, str], sample_rows: list[dict[str, object]],
    query_seconds: float, insert_seconds: float, total_seconds: float,
    dry_run: bool,
) -> None:
    print(f"[집계] MAP 소스 쌍: {map_pair_count}")
    print(f"[집계] 대상 행: {target_count}")
    print(f"[집계] 기존 키 skip: {skipped_count}")
    if dry_run:
        print(f"[집계] 삽입 수: 0 (DRY RUN, 실행 시 예정: {pending_count})")
    else:
        print(f"[집계] 삽입 수: {inserted_count}")
    print("[WF_ID] 문자열(TO_CHAR(MAP.WF_ID), 패딩 없음)")

    print("[테스트 더미 PNG]")
    for img_type in IMG_TYPES:
        image_b64 = image_b64_by_type[img_type]
        print(
            f"{img_type}: PNG {len(base64.b64decode(image_b64))} bytes, "
            f"base64 {len(image_b64)} chars, prefix={image_b64[:30]!r}"
        )

    print("[샘플 3행]")
    for row in sample_rows[:3]:
        print(
            (
                row["alias_lot_id"], row["wf_id"], row["img_type"], row["dt"],
                f"{str(row['image_b64'])[:30]}...", row["pdf_file_path"],
            )
        )
    print(
        f"[소요 시간] 조회 {query_seconds:.3f}초 / "
        f"삽입 {insert_seconds:.3f}초 / 전체 {total_seconds:.3f}초"
    )


def seed_from_map(conn, *, dry_run: bool) -> int:
    total_started = time.perf_counter()
    if not table_exists(conn):
        print(f"적재 불가: {TABLE_NAME} 테이블이 없습니다.")
        return -1

    image_b64_by_type = build_dummy_image_b64_by_type()
    cursor = conn.cursor()
    try:
        query_started = time.perf_counter()
        map_pairs = fetch_map_pairs(cursor)
        existing_keys = fetch_existing_keys(cursor)
        target_rows = build_target_rows(map_pairs, image_b64_by_type)
        pending_rows = [row for row in target_rows if _row_key(row) not in existing_keys]
        skipped_count = len(target_rows) - len(pending_rows)
        query_seconds = time.perf_counter() - query_started

        insert_seconds = 0.0
        inserted_count = 0
        if not dry_run and pending_rows:
            insert_started = time.perf_counter()
            # IMAGE_B64는 작지만 대상 컬럼이 CLOB이므로 명시적 CLOB 바인드가
            # 드라이버의 executemany 타입/길이 추론에 의존하지 않아 더 안정적이다.
            import oracledb

            cursor.setinputsizes(
                wf_id=oracledb.DB_TYPE_VARCHAR,
                image_b64=oracledb.DB_TYPE_CLOB,
            )
            insert_sql = f"""
                INSERT INTO {TABLE_NAME}
                    (ALIAS_LOT_ID, WF_ID, IMG_TYP, DT, IMAGE_B64, PDF_FILE_PATH)
                VALUES
                    (:alias_lot_id, :wf_id, :img_type, :dt, :image_b64, :pdf_file_path)
            """
            for offset in range(0, len(pending_rows), BATCH_SIZE):
                cursor.executemany(insert_sql, pending_rows[offset:offset + BATCH_SIZE])
            inserted_count = len(pending_rows)
            insert_seconds = time.perf_counter() - insert_started

        if not dry_run:
            conn.commit()

        print_seed_summary(
            map_pair_count=len(map_pairs), target_count=len(target_rows),
            skipped_count=skipped_count, inserted_count=inserted_count,
            pending_count=len(pending_rows), image_b64_by_type=image_b64_by_type,
            sample_rows=pending_rows or target_rows, query_seconds=query_seconds,
            insert_seconds=insert_seconds,
            total_seconds=time.perf_counter() - total_started, dry_run=dry_run,
        )
        if dry_run:
            print("DB INSERT/COMMIT을 수행하지 않았습니다.")
        return inserted_count
    except Exception:
        if not dry_run:
            conn.rollback()
        raise
    finally:
        cursor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DF_BMW_PNG 생성/검증/테스트 더미 적재")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--dry-run", action="store_true",
        help="MAP과 기존 키를 읽어 적재 대상을 계산하되 DB는 쓰지 않음",
    )
    actions.add_argument(
        "--verify", action="store_true",
        help="테이블 컬럼, 제약 및 행 수를 읽기 전용으로 조회",
    )
    actions.add_argument(
        "--insert", action="store_true",
        help="MAP의 Lot/wafer 쌍으로 테스트용 더미 PNG 행을 적재",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    conn = get_oracle_connection()
    try:
        if args.verify:
            return 0 if verify_table(conn) else 1
        if args.insert or args.dry_run:
            return 0 if seed_from_map(conn, dry_run=args.dry_run) >= 0 else 1
        return 0 if create_table(conn) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
