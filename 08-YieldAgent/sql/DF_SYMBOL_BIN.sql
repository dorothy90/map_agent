-- 이 테이블에 seed_symbol_bin.py가 넣는 값과 정규식은 테스트용 더미다.
-- bin 문자 대응은 DF_WADS_REPORT/prompts의 NAME(문자) 표기를 우선하고,
-- WADS에 없는 이름과 남는 실제 문자는 테스트데이터용으로 임의 배정했다.
-- common.BIN_CATEGORY_MAP/CATEGORY_TO_BIN은 bin_mapping.py에서 파생하며, 옛
-- B=VTH 등 25개 대응은 폐기했다. DDA(d)는 0건 대조군이다.
-- REGEX_PATTERN은 사용자 정의 bin 비교 문자열=left_bin+right_bin 중 어느
-- 쪽이든 매칭(either)하고 이어붙인 문자열 전체를 ^...$로 판정한다.
-- 비공백 한 글자는 Oracle/Python 호환 표기 [^ ]를 사용하는 테스트용 더미다.
--
-- 통합 대응표(문자 -> 이름), 50개 + 대조군:
-- B->BVDS, C->CONTACT, D->DIBL, F->IDDQ, G->GATE_OX, H->TPD, I->IDSAT,
-- J->JUNCTION, L->LEAK_ID, M->VMIN, N->LATCH, O->ION, P->IGATE, R->RON,
-- S->SCAN, T->TWT, V->VTH, W->EASY, X->FMAX, 7->ISB_CMOS,
-- U->CGS, Y->RD, Z->RS, E->IDLIN, K->IOFF, Q->SS, A->GM_MAX, 0->CGB,
-- 1->CGD, 2->CDS, 3->RSH, 4->RDS_ON, 5->LEAK_IG,
-- 6->SPARE_6, 8->SPARE_8, 9->SPARE_9,
-- a->F01, b->F02, c->F03, e->F04, f->F05, g->F06, h->F07, i->F08,
-- j->F09, k->F10, l->F11, m->F12, n->F13, o->F14; 대조군 d->DDA.
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
);

COMMENT ON TABLE DF_SYMBOL_BIN IS
    '제품/검사공정별 테스트용 더미 bin 정규식 설정 (업무 확정값 아님)';
COMMENT ON COLUMN DF_SYMBOL_BIN.LOT_CD IS
    '제품 코드: 현재 4SA 또는 4SS';
COMMENT ON COLUMN DF_SYMBOL_BIN.PROCESS IS
    '검사공정 구분: PT1C 또는 PT1H';
COMMENT ON COLUMN DF_SYMBOL_BIN.PARAM_NM IS
    '통합 bin 문자-이름 대응의 이름(문자); WADS 기준 및 테스트데이터 임의 배정';
COMMENT ON COLUMN DF_SYMBOL_BIN.REGEX_PATTERN IS
    '사용자 정의 bin 비교 문자열=left+right 중 어느 쪽이든 매칭(either); 전체 문자열 ^...$, 비공백 [^ ]';
COMMENT ON COLUMN DF_SYMBOL_BIN.DBLOADTIME IS
    'DB 업로드 시각';

-- 향후 제품/공정 코드를 확장할 때 기존 CHECK 제약을 먼저 제거한 뒤
-- 더 넓은 허용 목록의 CHECK 제약을 다시 추가한다.
-- ALTER TABLE DF_SYMBOL_BIN DROP CONSTRAINT CK_DF_SYMBOL_BIN_LOT;
-- ALTER TABLE DF_SYMBOL_BIN DROP CONSTRAINT CK_DF_SYMBOL_BIN_PROCESS;

-- Oracle REGEXP_LIKE 방언 확인 예시(기대 결과: MATCH / NO MATCH / NO MATCH):
-- SELECT CASE WHEN REGEXP_LIKE('BA', '^B[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END,
--        CASE WHEN REGEXP_LIKE('B ', '^B[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END,
--        CASE WHEN REGEXP_LIKE('F11F11', '^F[^ ]$') THEN 'MATCH' ELSE 'NO MATCH' END
-- FROM DUAL;
--
-- 2026-10-27 pr24 재설계 롤백:
-- 기존 50행을 /tmp/pr24_symbol_bin_backup.json으로 백업한 뒤 DELETE+INSERT로
-- 49행 교체함. 복원은 한 트랜잭션에서 아래 DELETE 후 백업 JSON의 각 행
-- (LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN, DBLOADTIME)을 INSERT하고 COMMIT한다.
-- DELETE FROM DF_SYMBOL_BIN WHERE LOT_CD IN ('4SA', '4SS');
-- -- 백업 JSON의 각 행을 INSERT
-- COMMIT;
--
-- 2026-10-27 pr25 재설계(실제 bin 대응 105행):
-- 직전 49행(pr24)을 /tmp/pr25_symbol_bin_backup_before.json에 백업한 뒤 한
-- 트랜잭션의 DELETE+INSERT로 105행 교체함. 복원은 한 트랜잭션에서 아래
-- DELETE 후 백업 JSON의 각 행(LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN,
-- DBLOADTIME)을 INSERT하고 COMMIT한다. 최초 50행 백업은
-- /tmp/pr24_symbol_bin_backup.json에 있다.
-- DELETE FROM DF_SYMBOL_BIN WHERE LOT_CD IN ('4SA', '4SS');
-- -- /tmp/pr25_symbol_bin_backup_before.json의 각 행을 INSERT
-- COMMIT;
--
-- 기존 더미 seed 롤백(주의: 아래 문장은 해당 LOT의 모든 행을 삭제한다. seed 전에
-- 테이블이 비어 있었고 이후 다른 행을 넣지 않은 경우에만 그대로 실행):
-- DELETE FROM DF_SYMBOL_BIN WHERE LOT_CD IN ('4SA', '4SS');
-- COMMIT;
--
-- 테이블 생성 자체 롤백(Oracle DDL은 자동 커밋되므로 별도 실행):
-- DROP TABLE DF_SYMBOL_BIN PURGE;
--
-- 2026-10-27 pr26 재설계(129행, 통합 대응표):
-- 직전 105행(pr25)을 /tmp/pr26_symbol_bin_backup_before.json에 백업한 뒤 한
-- 트랜잭션의 DELETE+INSERT로 129행 교체함. 복원은 pr25와 같이 한 트랜잭션에서
-- 아래 DELETE 후 백업 JSON의 각 행(LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN,
-- DBLOADTIME)을 INSERT하고 COMMIT한다.
-- DELETE FROM DF_SYMBOL_BIN WHERE LOT_CD IN ('4SA', '4SS');
-- -- /tmp/pr26_symbol_bin_backup_before.json의 각 행을 INSERT
-- COMMIT;
--
-- MAP 더미 치환 기록:
-- MAP.MAP_VAL_JSON의 다문자 bin F01~F14(4SA/PT1H, SYN4SA lot 40개,
-- wafer 467장, 셀 115,269개)를 소문자 a~o(d 제외)로 치환함.
-- F01->a, F02->b, F03->c, F04->e, F05->f, F06->g, F07->h, F08->i,
-- F09->j, F10->k, F11->l, F12->m, F13->n, F14->o.
-- 원본 행은 DB 백업 테이블 MAP_BAK_PR26과 /tmp/pr26_map_backup.json에 있음.
-- MERGE INTO MAP m USING MAP_BAK_PR26 b ON (m.LOT_ID=b.LOT_ID AND m.WF_ID=b.WF_ID AND m.OPER_DET_DESC=b.OPER_DET_DESC AND m.END_TM=b.END_TM) WHEN MATCHED THEN UPDATE SET m.MAP_VAL_JSON=b.MAP_VAL_JSON;
-- COMMIT;
--
-- 2026-10-27 pr27: PT1H도 either로 변경한 129행 재적재.
-- 직전 129행(PT1H=left)을 /tmp/pr27_symbol_bin_backup_before.json에 백업한 뒤
-- 한 트랜잭션 DELETE+INSERT. 복원은 pr25와 같은 방식으로 아래 DELETE 후 백업
-- JSON 각 행을 INSERT하고 COMMIT한다.
-- DELETE FROM DF_SYMBOL_BIN WHERE LOT_CD IN ('4SA', '4SS');
-- -- /tmp/pr27_symbol_bin_backup_before.json의 각 행을 INSERT
-- COMMIT;
