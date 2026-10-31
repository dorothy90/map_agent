#!/usr/bin/env python3
"""Remap MAP to DF_DIE_TO_WF_YLD. Requires oracledb and python-dotenv.

Run from the directory containing .env; default is a read-only dry run.
Apply requires CREATE TABLE privilege. Scratch DDL is outside the single
data transaction; the existing MAP_BAK_20261023 is not modified.
"""

import argparse
import sys
import uuid

import oracledb
from dotenv import dotenv_values


WATCH = """4SAIV04.3 4SABJ9T.1 4SADTGV.1 4SA82NB.14 4SS5240.5
4SAVDN8.17 4SSWVWF.3 4SAPKFS.23 4SA6NMN.10 4SA4SLC.25""".split()

PLAN = """
WITH source_wafers AS (
    SELECT lot_cd, lot_id, wf_id,
           MIN(CASE WHEN oper_det_desc = 'PT1H TEST' THEN end_tm END) h_end
    FROM map
    WHERE lot_cd IN ('4SA', '4SS')
      AND oper_det_desc IN ('PT1H TEST', 'PT1C TEST')
    GROUP BY lot_cd, lot_id, wf_id
    HAVING COUNT(DISTINCT oper_det_desc) = 2
), s AS (
    SELECT source_wafers.*, ROW_NUMBER() OVER (
        PARTITION BY lot_cd ORDER BY h_end NULLS LAST,
        NLSSORT(lot_id, 'NLS_SORT=BINARY'), wf_id) pos
    FROM source_wafers
), target_wafers AS (
    SELECT lot_cd, lotid, TO_NUMBER(wfid) wfid,
           MIN(CASE WHEN process = 'PT1H TEST'
                    THEN measuretime_start END) h_start,
           MIN(CASE WHEN process = 'PT1C TEST'
                    THEN measuretime_start END) c_start
    FROM df_die_to_wf_yld
    WHERE lot_cd IN ('4SA', '4SS')
    GROUP BY lot_cd, lotid, TO_NUMBER(wfid)
), t AS (
    SELECT target_wafers.*, ROW_NUMBER() OVER (
        PARTITION BY lot_cd ORDER BY h_start NULLS LAST,
        NLSSORT(lotid, 'NLS_SORT=BINARY'), wfid) pos
    FROM target_wafers
), p AS (
    SELECT s.lot_cd, s.pos, s.lot_id old_lot, s.wf_id old_wf,
           t.lotid new_lot, t.wfid new_wf, t.h_start, t.c_start
    FROM s JOIN t ON t.lot_cd = s.lot_cd AND t.pos = s.pos
), c AS (
    SELECT ROWIDTOCHAR(m.ROWID) source_rid, p.new_lot, p.new_wf,
           m.oper_det_desc oper,
           CASE WHEN m.oper_det_desc = 'PT1C TEST'
                THEN COALESCE(p.c_start, p.h_start)
                ELSE p.h_start END new_end,
           m.end_tm old_end
    FROM p JOIN map m ON m.lot_cd = p.lot_cd
        AND m.lot_id = p.old_lot AND m.wf_id = p.old_wf
    WHERE m.oper_det_desc IN ('PT1H TEST', 'PT1C TEST')
)
"""


def remap(conn, apply):
    scratch = "MAP_REMAP_" + uuid.uuid4().hex[:16].upper()
    created = False
    with conn.cursor() as cur:
        cur.arraysize = 1000
        try:
            if apply:
                # DDL commits implicitly: create before acquiring locks or doing DML.
                cur.execute(f"""
                    CREATE GLOBAL TEMPORARY TABLE {scratch}
                    ON COMMIT DELETE ROWS AS
                    SELECT ROWIDTOCHAR(ROWID) source_rid, lot_id new_lot,
                           wf_id new_wf, end_tm new_end
                    FROM map WHERE 1 = 0
                """)
                created = True
                cur.execute("LOCK TABLE map IN EXCLUSIVE MODE NOWAIT")
                cur.execute("LOCK TABLE df_die_to_wf_yld IN SHARE MODE NOWAIT")
            else:
                cur.execute("SET TRANSACTION READ ONLY")

            total = 0
            for product in ("4SA", "4SS"):
                cur.execute(PLAN + """
                    SELECT (SELECT COUNT(*) FROM s WHERE lot_cd = :product),
                           (SELECT COUNT(*) FROM t WHERE lot_cd = :product),
                           (SELECT COUNT(*) FROM p WHERE lot_cd = :product),
                           (SELECT COUNT(*) FROM (
                               SELECT lot_id, wf_id FROM map
                               WHERE lot_cd = :product
                                 AND oper_det_desc IN ('PT1H TEST', 'PT1C TEST')
                               GROUP BY lot_id, wf_id
                               HAVING COUNT(DISTINCT oper_det_desc) = 1
                                  AND MIN(oper_det_desc) = 'PT1H TEST'
                           ))
                    FROM dual
                """, product=product)
                sources, targets, pairs, h_only = cur.fetchone()
                total += pairs
                print(f"{product}: sources={sources}, targets={targets}, pairs={pairs}, "
                      f"source_leftovers={sources - pairs}, "
                      f"target_leftovers={targets - pairs}, PT1H_only_untouched={h_only}")
                cur.execute(PLAN + """
                    SELECT old_lot, old_wf, new_lot, new_wf, h_start, c_start
                    FROM p WHERE lot_cd = :product AND pos <= 5 ORDER BY pos
                """, product=product)
                for old_lot, old_wf, lot, wf, h, c in cur:
                    print(f"  {old_lot}.{old_wf} -> {lot}.{wf}; "
                          f"PT1H={h}, PT1C={c if c is not None else h}")

            cur.execute(PLAN + "SELECT lotid, wfid FROM t")
            targets = {(lot, int(wf)) for lot, wf in cur}
            cur.execute(PLAN + "SELECT new_lot, new_wf FROM p")
            paired = {(lot, int(wf)) for lot, wf in cur}
            for wafer in WATCH:
                lot, wf = wafer.rsplit('.', 1)
                key = (lot, int(wf))
                print(f"{wafer}: target={key in targets}, paired_target={key in paired}")

            cur.execute(PLAN + """
                SELECT DISTINCT c.new_lot, c.new_wf, c.oper
                FROM c JOIN map m ON m.lot_id = c.new_lot
                    AND m.wf_id = c.new_wf AND m.oper_det_desc = c.oper
                WHERE NOT EXISTS (
                    SELECT 1 FROM c moving
                    WHERE moving.source_rid = ROWIDTOCHAR(m.ROWID)
                )
                ORDER BY c.new_lot, c.new_wf, c.oper
            """)
            collisions = cur.fetchall()
            print(f"Collision count (target keys hitting untouched rows): {len(collisions)}")
            for lot, wf, oper in collisions:
                print(f"  COLLISION: {lot}.{wf} / {oper}")
            if collisions:
                raise RuntimeError("Aborted: target keys collide with untouched MAP rows")

            cur.execute(PLAN + """
                SELECT COUNT(*), COUNT(DISTINCT source_rid),
                       COUNT(CASE WHEN new_end IS NULL OR old_end IS NULL THEN 1 END)
                FROM c
            """)
            rows, unique_rows, missing_times = cur.fetchone()
            if rows != 2 * total or unique_rows != rows or missing_times:
                raise RuntimeError("Aborted: invalid source multiplicity or missing timestamps")
            cur.execute(PLAN + """
                SELECT COUNT(*) FROM (
                    SELECT new_lot, new_wf, oper FROM c
                    GROUP BY new_lot, new_wf, oper HAVING COUNT(*) > 1
                )
            """)
            if cur.fetchone()[0]:
                raise RuntimeError("Aborted: duplicate target keys within mapping")

            if not apply:
                conn.rollback()
                print(f"Dry run: {total} pairs, {rows} rows; no changes made")
                return

            cur.execute(f"INSERT INTO {scratch} " + PLAN +
                        "SELECT source_rid, new_lot, new_wf, new_end FROM c")
            if cur.rowcount != rows:
                raise RuntimeError("Scratch row count mismatch")
            cur.execute(f"""
                MERGE INTO map m USING {scratch} x
                ON (m.ROWID = CHARTOROWID(x.source_rid))
                WHEN MATCHED THEN UPDATE SET
                    m.lot_id = x.new_lot, m.wf_id = x.new_wf,
                    m.start_tm = m.start_tm + (x.new_end - m.end_tm),
                    m.end_tm = x.new_end
            """)
            updated = cur.rowcount
            if updated != 2 * total:
                raise RuntimeError(f"Updated {updated} rows; expected {2 * total}")
            # Capture overlap while both tables remain locked; print after commit.
            cur.execute("""
                SELECT COUNT(*) FROM (
                    SELECT lot_id, wf_id FROM map
                    WHERE lot_cd IN ('4SA', '4SS')
                    INTERSECT
                    SELECT lotid, TO_NUMBER(wfid) FROM df_die_to_wf_yld
                    WHERE lot_cd IN ('4SA', '4SS')
                )
            """)
            overlap = cur.fetchone()[0]
            conn.commit()
            print(f"Committed {updated} rows ({total} pairs); distinct wafer overlap={overlap}")
        except BaseException:
            conn.rollback()
            raise
        finally:
            if created:
                # Never issue DROP before commit/rollback: it would commit MAP DML.
                cur.execute(f"DROP TABLE {scratch}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    mode.add_argument('--apply', action='store_true', help='Update and commit')
    args = parser.parse_args()
    config = dotenv_values('.env')
    keys = ('ORACLE_USER', 'ORACLE_PASSWORD', 'ORACLE_DSN')
    missing = [key for key in keys if not config.get(key)]
    if missing:
        parser.error('Missing .env keys: ' + ', '.join(missing))
    if not oracledb.is_thin_mode():
        parser.error('python-oracledb thin mode is required')
    try:
        with oracledb.connect(user=config['ORACLE_USER'],
                              password=config['ORACLE_PASSWORD'],
                              dsn=config['ORACLE_DSN']) as conn:
            conn.autocommit = False
            remap(conn, args.apply)
    except Exception as exc:
        # Redact even if a driver error unexpectedly includes credentials.
        message = str(exc).replace(config['ORACLE_PASSWORD'], '[REDACTED]')
        print(f"ERROR: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
