-- ALIAS_LOT_ID는 기존 DF_TROUBLE_LOT의 VARCHAR2(30)보다 여유 있게 50자로
-- 잡았다. 확인된 실제 최대 길이 9자를 충분히 수용하면서 불필요한 100자는 피한다.
-- WF_ID는 문자열(VARCHAR2); MAP.WF_ID(NUMBER)와 조인 시 TO_CHAR(MAP.WF_ID) 또는
-- TO_NUMBER(WF_ID) 필요; DF_LEGEND_LONG.WFID 등 문자형 WFID와는 직접 비교 가능.
-- 현재 데이터 형식은 패딩 없는 정수 문자열(1~25)이다.
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
);

COMMENT ON TABLE DF_BMW_PNG IS
    'Lot/wafer별 BMWC, BMWG, GMW 맵 이미지 및 PDF 경로';
COMMENT ON COLUMN DF_BMW_PNG.ALIAS_LOT_ID IS
    'Lot ID 또는 T 접두 별칭; 기존 30자보다 여유 있는 50자';
COMMENT ON COLUMN DF_BMW_PNG.WF_ID IS
    '웨이퍼 번호 문자열; 현재 데이터는 패딩 없는 정수 문자열(1~25); MAP.WF_ID(NUMBER)와 조인 시 TO_CHAR(MAP.WF_ID) 또는 TO_NUMBER(WF_ID) 필요; 문자형 WFID와는 직접 비교 가능';
COMMENT ON COLUMN DF_BMW_PNG.IMG_TYP IS
    '이미지 유형: BMWC, BMWG 또는 GMW';
COMMENT ON COLUMN DF_BMW_PNG.DT IS
    'YYYYMMDD 형식 기준일; 문자열은 원문 보존이 쉽지만 날짜 연산에는 변환 필요';
COMMENT ON COLUMN DF_BMW_PNG.IMAGE_B64 IS
    '맵 이미지 base64 문자열; 크기 한도 미정, 기본 CLOB 저장 옵션 사용';
COMMENT ON COLUMN DF_BMW_PNG.PDF_FILE_PATH IS
    '관련 PDF 파일 경로';

-- IMG_TYP 값을 늘릴 때 기존 CHECK 제약을 제거한 뒤 허용 목록을 확장해 재생성한다.
-- ALTER TABLE DF_BMW_PNG DROP CONSTRAINT CK_DF_BMW_PNG_IMG_TYP;

-- DT CHECK는 8자리 숫자 형식만 검사하며 실제 달력 날짜 유효성은 검사하지 않는다.

-- 테이블 생성 자체 롤백(Oracle DDL은 자동 커밋되므로 별도 실행):
-- DROP TABLE DF_BMW_PNG PURGE;

-- 2026-10-27 pr25: WF_ID NUMBER(4)→VARCHAR2(10) 변환.
-- 변환 전 데이터는 /tmp/pr25_bmw_png_backup.json 과 DB 백업 테이블
-- DF_BMW_PNG_BAK_PR25 에 보관.
-- 롤백: DROP TABLE DF_BMW_PNG PURGE; 후 이전 DDL(WF_ID NUMBER(4))로 재생성하고
-- INSERT INTO DF_BMW_PNG SELECT * FROM DF_BMW_PNG_BAK_PR25; COMMIT;
--
-- 이전 NUMBER 버전 DDL:
-- CREATE TABLE DF_BMW_PNG (
--     ALIAS_LOT_ID  VARCHAR2(50)
--                   CONSTRAINT NN_DF_BMW_PNG_ALIAS NOT NULL,
--     WF_ID         NUMBER(4)
--                   CONSTRAINT NN_DF_BMW_PNG_WF_ID NOT NULL,
--     IMG_TYP       VARCHAR2(10)
--                   CONSTRAINT NN_DF_BMW_PNG_IMG_TYP NOT NULL,
--     DT            VARCHAR2(8)
--                   CONSTRAINT NN_DF_BMW_PNG_DT NOT NULL,
--     IMAGE_B64     CLOB,
--     PDF_FILE_PATH VARCHAR2(500),
--     CONSTRAINT CK_DF_BMW_PNG_IMG_TYP
--         CHECK (IMG_TYP IN ('BMWC', 'BMWG', 'GMW')),
--     CONSTRAINT CK_DF_BMW_PNG_DT
--         CHECK (REGEXP_LIKE(DT, '^[0-9]{8}$')),
--     CONSTRAINT UQ_DF_BMW_PNG_KEY
--         UNIQUE (ALIAS_LOT_ID, WF_ID, IMG_TYP, DT)
-- );

-- 테스트용 더미 데이터 롤백:
-- DELETE FROM DF_BMW_PNG WHERE DT='20260822' AND PDF_FILE_PATH LIKE '/dummy/pdf/%'; COMMIT;
