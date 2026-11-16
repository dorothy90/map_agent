"""DF_WF_LEGEND 테스트용 더미 적재 (업무 확정값 아님).

DF_WF_ALL_PRB_V2의 모든 lot·wafer(GORUPKEY 기준 distinct)에 대해
wafer당 300개 공정 이력 행을 넣는다. OPER_ID 집합은 모든 wafer가 같고,
레전드 값(장비·챔버·레시피 등)은 wafer마다 오퍼별 후보 풀에서 고른다.

사용법:
    python -m seed_wf_legend            # 규모 확인 후 한도 이하면 적재
    python -m seed_wf_legend --plan     # 규모·검증만 (INSERT 없음)

이미 행이 있으면 INSERT 하지 않는다(멱등). 다른 테이블은 수정·삭제하지 않는다.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import oracledb
from dotenv import load_dotenv

TABLE = "DF_WF_LEGEND"
SOURCE = "DF_WF_ALL_PRB_V2"
SEED = 42
BATCH = 4000
OPS_PER_WAFER = 300
ROW_CAP = 5_000_000
END_TM_DAYS = 60
END_TM_LAST_MARGIN = timedelta(minutes=30)

# EQP family → (prefix, model pool). 오퍼마다 이 풀에서 3~6대를 고른다.
FAMILY_EQP = {
    "PHO": ("SCN", ("NXT1970Ci", "NXT1980Di", "XT1460E", "ATI750")),
    "ETCH": ("ETC", ("Kiyo45", "TCP9600", "CenturaEnabler", "Syndion")),
    "DEP": ("DEP", ("ProducerSE", "Endura2", "Vector3", "Eagle12")),
    "CMP": ("CMP", ("ReflexionLK", "MirraMesa", "F-REX300")),
    "IMP": ("IMP", ("VIISta3000", "OptimaXEx", "SENMC3-300")),
    "FUR": ("FUR", ("Alpha8S", "TELIndyo", "VitusPlus")),
    "CLN": ("CLN", ("Tactras", "NS300", "EXP300")),
    "ASH": ("ASH", ("TetraZ", "FusionES3", "Synapse")),
    "MET": ("MET", ("Aether", "Archer600", "SP5", "eDR7380")),
    "ECD": ("ECD", ("RaiderS", "SABRE3D", "Stratus")),
    "WET": ("WET", ("NS300W", "TactrasW", "FC3000")),
}


def _load_dotenv() -> None:
    """기존 seed와 같이 ORACLE_* 를 쓰되, 체크아웃 상위 .env도 찾는다. 경로·값은 출력하지 않는다."""
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    load_dotenv(override=False)


def get_connection() -> oracledb.Connection:
    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")
    dsn = os.getenv("ORACLE_DSN")
    missing = [name for name, val in (("ORACLE_USER", user), ("ORACLE_PASSWORD", password), ("ORACLE_DSN", dsn)) if not val]
    if missing:
        raise SystemExit(f"missing env keys: {missing}")
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    conn.call_timeout = 600_000
    return conn


def _litho(layer: str) -> list[tuple[str, str]]:
    return [
        (f"{layer} PRE CLEAN", "CLN"),
        (f"{layer} PHOTO", "PHO"),
        (f"{layer} ADC INSPECT", "MET"),
    ]


def _implant(layer: str) -> list[tuple[str, str]]:
    return _litho(layer) + [
        (f"{layer} IMPLANT", "IMP"),
        (f"{layer} PR STRIP", "ASH"),
        (f"{layer} POST CLEAN", "CLN"),
    ]


def _etch(layer: str) -> list[tuple[str, str]]:
    return _litho(layer) + [
        (f"{layer} ETCH", "ETCH"),
        (f"{layer} ASH", "ASH"),
        (f"{layer} POST CLEAN", "CLN"),
        (f"{layer} CD MEASURE", "MET"),
    ]


def _metal(i: int, *, has_via: bool, densify: bool, defect_inspect: bool) -> list[tuple[str, str]]:
    steps = [(f"IMD{i} DEP", "DEP")]
    if densify:
        steps.append((f"IMD{i} DENSIFY ANNEAL", "FUR"))
    if has_via:
        steps += _etch(f"VIA{i}")
    steps += _etch(f"METAL{i}")
    steps += [
        (f"METAL{i} BARRIER SEED DEP", "DEP"),
        (f"METAL{i} CU ECP", "ECD"),
        (f"METAL{i} CU CMP", "CMP"),
        (f"METAL{i} POST CMP CLEAN", "CLN"),
        (f"METAL{i} CAP DEP", "DEP"),
        (f"METAL{i} THICKNESS MEASURE", "MET"),
    ]
    if defect_inspect:
        steps.append((f"METAL{i} DEFECT INSPECT", "MET"))
    return steps


def build_operations() -> list[dict]:
    """앞단→뒷단 300개 공정. OPER_ID는 10 간격 5자리 코드."""
    raw: list[tuple[str, str]] = []
    raw += [
        ("INCOMING VISUAL INSPECTION", "MET"),
        ("WAFER START CLEAN", "CLN"),
        ("WAFER ID LASER MARK", "MET"),
        ("INITIAL THICKNESS MEASURE", "MET"),
        ("PAD OXIDE GROWTH", "FUR"),
        ("PAD NITRIDE DEP", "DEP"),
        ("PAD NITRIDE ANNEAL", "FUR"),
    ]
    raw += _etch("ZERO LAYER")
    raw += _etch("STI")
    raw += [
        ("STI GAP FILL OXIDE DEP", "DEP"),
        ("STI CMP", "CMP"),
        ("STI POST CMP CLEAN", "CLN"),
        ("STI NITRIDE STRIP", "WET"),
        ("STI SACRIFICIAL OXIDE GROWTH", "FUR"),
        ("STI SAC OXIDE WET ETCH", "WET"),
    ]
    for layer in ["PWELL", "NWELL", "VTN", "VTP", "POCKET N", "POCKET P"]:
        raw += _implant(layer)
    raw += [
        ("WELL ANNEAL", "FUR"),
        ("WELL OXIDE WET STRIP", "WET"),
        ("SACRIFICIAL GATE OX GROWTH", "FUR"),
        ("SACRIFICIAL GATE OX WET ETCH", "WET"),
        ("IL OXIDE GROWTH", "FUR"),
        ("HK DIELECTRIC DEP", "DEP"),
        ("DUMMY POLY DEP", "DEP"),
        ("DUMMY POLY ANNEAL", "FUR"),
        ("DUMMY POLY CMP", "CMP"),
        ("DUMMY POLY POST CLEAN", "CLN"),
    ]
    raw += _etch("GATE")
    raw += [
        ("GATE REOXIDATION", "FUR"),
        ("OFFSET SPACER DEP", "DEP"),
        ("OFFSET SPACER ETCH", "ETCH"),
    ]
    for layer in ["NLDD", "PLDD", "NHALO", "PHALO"]:
        raw += _implant(layer)
    raw += [
        ("MAIN SPACER DEP", "DEP"),
        ("MAIN SPACER ETCH", "ETCH"),
        ("MAIN SPACER CLEAN", "CLN"),
    ]
    for layer in ["NSD", "PSD"]:
        raw += _implant(layer)
    raw += [
        ("SD SPIKE RTA", "FUR"),
        ("SILICIDE PRE CLEAN", "CLN"),
        ("NISI DEP", "DEP"),
        ("SILICIDE RTA", "FUR"),
        ("UNREACTED METAL WET STRIP", "WET"),
        ("SILICIDE ANNEAL", "FUR"),
        ("CESL DEP", "DEP"),
        ("ILD0 OXIDE DEP", "DEP"),
        ("ILD0 CMP", "CMP"),
        ("ILD0 POST CMP CLEAN", "CLN"),
    ]
    raw += _etch("CONTACT")
    raw += [
        ("CONTACT WET CLEAN", "CLN"),
        ("CONTACT BARRIER DEP", "DEP"),
        ("CONTACT W DEP", "DEP"),
        ("CONTACT W CMP", "CMP"),
        ("CONTACT POST CMP CLEAN", "CLN"),
        ("CONTACT RES MEASURE", "MET"),
    ]
    raw += _metal(1, has_via=False, densify=True, defect_inspect=True)
    for i in range(2, 7):
        raw += _metal(i, has_via=True, densify=False, defect_inspect=False)
    raw += [
        ("PASSIVATION OX DEP", "DEP"),
        ("PASSIVATION NIT DEP", "DEP"),
    ]
    raw += _etch("PAD")
    raw += [("POLYIMIDE COAT CURE", "FUR")]
    raw += _litho("POLYIMIDE")
    raw += [
        ("POLYIMIDE CURE", "FUR"),
        ("UBM DEP", "DEP"),
    ]
    raw += _etch("BUMP")
    raw += [
        ("BUMP ECP", "ECD"),
        ("RESIST STRIP", "ASH"),
        ("UBM WET ETCH", "WET"),
        ("BUMP INSPECT", "MET"),
        ("BACKGRIND TAPE LAMINATE", "CLN"),
        ("BACKGRIND", "CMP"),
        ("BACKSIDE POLISH CMP", "CMP"),
        ("BACKGRIND DETAPE CLEAN", "CLN"),
        ("BACKSIDE METAL DEP", "DEP"),
        ("FINAL THICKNESS MEASURE", "MET"),
        ("FINAL DEFECT INSPECT", "MET"),
        ("OUTGOING VISUAL INSPECTION", "MET"),
    ]

    if len(raw) != OPS_PER_WAFER:
        raise RuntimeError(f"공정 수가 {len(raw)}개입니다. {OPS_PER_WAFER}개여야 합니다.")
    descs = [desc for desc, _ in raw]
    if len(set(descs)) != len(descs):
        raise RuntimeError("OPER_DET_DESC 중복이 있습니다.")

    ops: list[dict] = []
    pool_rng = random.Random(SEED)
    for i, (desc, family) in enumerate(raw):
        if family not in FAMILY_EQP:
            raise RuntimeError(f"unknown family: {family}")
        prefix, models = FAMILY_EQP[family]
        family_size = 12 if family in {"PHO", "ETCH", "DEP", "CLN", "MET"} else 8
        n_eqp = pool_rng.randint(3, 6)
        eqp_ids = [f"{prefix}{n:02d}" for n in pool_rng.sample(range(1, family_size + 1), n_eqp)]
        model_of = {eqp: models[(int(eqp[-2:]) - 1) % len(models)] for eqp in eqp_ids}
        n_recipe = pool_rng.randint(2, 4)
        recipes = [f"{11000 + i * 10:05d}_{family}_R{n:02d}" for n in range(1, n_recipe + 1)]
        reticles = [f"RET-{11000 + i * 10:05d}-{ch}" for ch in "ABC"[: pool_rng.randint(2, 3)]]
        stepper_recipes = [f"SR-{11000 + i * 10:05d}-R{n:02d}" for n in range(1, n_recipe + 1)]
        ops.append(
            {
                "oper_id": f"{11000 + i * 10:05d}",
                "oper_det_desc": desc,
                "family": family,
                "photo": family == "PHO",
                "eqp_ids": eqp_ids,
                "model_of": model_of,
                "recipes": recipes,
                "reticles": reticles,
                "stepper_recipes": stepper_recipes,
            }
        )
    return ops


def _lot_flags(lot_id: str) -> tuple[str, str]:
    rng = random.Random(f"{SEED}:lot:{lot_id}")
    trbl = "Y" if rng.random() < 0.08 else "N"
    p = rng.random()
    if p < 0.02:
        hot = "SUH"
    elif p < 0.12:
        hot = "HOT"
    else:
        hot = "NOR"
    return trbl, hot


def _end_times(meas: datetime, rng: random.Random) -> list[str]:
    last_limit = meas - END_TM_LAST_MARGIN
    start = last_limit - timedelta(days=END_TM_DAYS)
    span = (last_limit - start).total_seconds()
    step = span / (OPS_PER_WAFER - 1)
    offsets: list[float] = []
    for i in range(OPS_PER_WAFER):
        if i == 0 or i == OPS_PER_WAFER - 1:
            jitter = 0.0
        else:
            jitter = rng.uniform(-0.4 * step, 0.4 * step)
        offsets.append(i * step + jitter)
    offsets.sort()
    for i in range(1, OPS_PER_WAFER):
        if offsets[i] <= offsets[i - 1] + 1:
            offsets[i] = offsets[i - 1] + 1
    if offsets[-1] > span:
        scale = span / offsets[-1]
        offsets = [o * scale for o in offsets]
        for i in range(1, OPS_PER_WAFER):
            if offsets[i] <= offsets[i - 1] + 1:
                offsets[i] = offsets[i - 1] + 1
        if offsets[-1] > span:
            raise RuntimeError("END_TM 간격 생성 실패")
    times = [start + timedelta(seconds=o) for o in offsets]
    if times[-1] >= meas or len({t.strftime("%Y-%m-%d %H:%M:%S") for t in times}) != OPS_PER_WAFER:
        # 초 단위 충돌 시 1초씩 밀어 보되 측정 시각 앞을 유지
        uniq: list[datetime] = []
        for t in times:
            if uniq and t <= uniq[-1]:
                t = uniq[-1] + timedelta(seconds=1)
            uniq.append(t)
        if uniq[-1] >= last_limit:
            raise RuntimeError("END_TM이 측정 시각과 겹칩니다")
        times = uniq
    return [t.strftime("%Y-%m-%d %H:%M:%S") for t in times]


def fetch_wafers(cur: oracledb.Cursor) -> list[tuple]:
    cur.execute(
        f"""
        SELECT
            LOT_ID,
            ALIAS_LOT_ID,
            LOT_CD,
            PRODUCT,
            WFID,
            GORUPKEY,
            MIN(MEASURETIME_START) AS MEAS_TM,
            COUNT(*) OVER (PARTITION BY LOT_ID) AS LOT_WF_CNT
        FROM (
            SELECT DISTINCT
                LOT_ID, ALIAS_LOT_ID, LOT_CD, PRODUCT, WFID, GORUPKEY, MEASURETIME_START
            FROM {SOURCE}
        )
        GROUP BY LOT_ID, ALIAS_LOT_ID, LOT_CD, PRODUCT, WFID, GORUPKEY
        ORDER BY LOT_ID, TO_NUMBER(WFID)
        """
    )
    # COUNT(*) OVER after GROUP BY is lot's wafer count in this distinct set.
    return cur.fetchall()


def plan_counts(cur: oracledb.Cursor) -> dict:
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    existing = int(cur.fetchone()[0])
    cur.execute(
        f"""
        SELECT
            COUNT(DISTINCT LOT_ID),
            COUNT(DISTINCT GORUPKEY),
            COUNT(DISTINCT LOT_CD)
        FROM {SOURCE}
        """
    )
    lots, wafers, products = cur.fetchone()
    expected = int(wafers) * OPS_PER_WAFER
    return {
        "existing": existing,
        "lots": int(lots),
        "wafers": int(wafers),
        "products": int(products),
        "expected": expected,
    }


INSERT_SQL = f"""
INSERT INTO {TABLE} (
    LOT_CD, PROD_ID, OPER_ID, OPER_DET_DESC, LOT_ID, ALIAS_LOT_ID, WF_ID,
    EQP_ID, EQP_CH, RECIPE_ID, RETICLE_ID, CHUCK_ID, ZONE_ID, EQP_MODEL_CD,
    FLOW_ID, PORT_NO, BATCH_ID, STEPPER_RECIPE_ID, WF_CNT, RWK_CNT, HOT_TYP,
    TRBL_LOT_YN, DELAY_TIME, END_TM, DT, DBLOADTIME, STEPPER_ID, START_CHUCK_ID
) VALUES (
    :1, :2, :3, :4, :5, :6, :7,
    :8, :9, :10, :11, :12, :13, :14,
    :15, :16, :17, :18, :19, :20, :21,
    :22, :23, :24, :25, :26, :27, :28
)
"""


def build_rows_for_wafer(
    wafer: tuple,
    ops: list[dict],
    load_tm: datetime,
    lot_wf_cache: dict[str, tuple[str, str, int]],
) -> list[tuple]:
    lot_id, alias_lot_id, lot_cd, product, wf_id, gorupkey, meas_tm, lot_wf_cnt = wafer
    if meas_tm is None:
        raise RuntimeError(f"MEASURETIME_START 없음: {gorupkey}")
    if not isinstance(meas_tm, datetime):
        meas_tm = datetime.strptime(str(meas_tm)[:19], "%Y-%m-%d %H:%M:%S")

    if lot_id not in lot_wf_cache:
        lot_wf_cache[lot_id] = (*_lot_flags(lot_id), int(lot_wf_cnt))
    trbl, hot, wf_cnt = lot_wf_cache[lot_id]

    rng = random.Random(f"{SEED}:wf:{gorupkey}")
    end_tms = _end_times(meas_tm, rng)
    flow_id = f"FL-{lot_cd}-V{rng.randint(1, 3)}"
    rows: list[tuple] = []
    for op, end_tm in zip(ops, end_tms):
        eqp_id = rng.choice(op["eqp_ids"])
        eqp_ch = f"PM{rng.randint(1, 6)}"
        recipe_id = rng.choice(op["recipes"])
        model = op["model_of"][eqp_id]
        port_no = str(rng.randint(1, 4))
        batch_id = f"BAT-{eqp_id}-{end_tm[0:10].replace('-', '')}-{rng.randint(1, 20):02d}"
        r = rng.random()
        rwk = 2 if r < 0.01 else (1 if r < 0.05 else 0)
        delay = int(min(1440, abs(rng.gauss(40, 60))))
        zone_id = None
        if op["family"] in {"CMP", "FUR", "DEP"}:
            zone_id = f"Z{rng.randint(1, 4)}"
        if op["photo"]:
            reticle_id = rng.choice(op["reticles"])
            chuck_id = rng.choice(("CK1", "CK2"))
            start_chuck = chuck_id if rng.random() < 0.80 else ("CK2" if chuck_id == "CK1" else "CK1")
            stepper_id = eqp_id
            stepper_recipe = rng.choice(op["stepper_recipes"])
        else:
            reticle_id = chuck_id = start_chuck = stepper_id = stepper_recipe = None
        rows.append(
            (
                lot_cd,
                product,
                op["oper_id"],
                op["oper_det_desc"],
                lot_id,
                alias_lot_id,
                wf_id,
                eqp_id,
                eqp_ch,
                recipe_id,
                reticle_id,
                chuck_id,
                zone_id,
                model,
                flow_id,
                port_no,
                batch_id,
                stepper_recipe,
                wf_cnt,
                rwk,
                hot,
                trbl,
                delay,
                end_tm,
                end_tm[:10],
                load_tm,
                stepper_id,
                start_chuck,
            )
        )
    return rows


def _is_connected(conn: oracledb.Connection) -> bool:
    try:
        conn.ping()
        return True
    except oracledb.Error:
        return False


def _reconnect() -> oracledb.Connection:
    conn = get_connection()
    conn.call_timeout = 600_000
    return conn


def clear_table(conn: oracledb.Connection) -> None:
    """불완전 적재분만 이 테이블에서 제거한다. 다른 테이블은 건드리지 않는다."""
    cur = conn.cursor()
    try:
        cur.execute(f"TRUNCATE TABLE {TABLE}")
        print(f"{TABLE} TRUNCATE 완료 (이 테이블만)")
    finally:
        cur.close()


def seed(conn: oracledb.Connection, ops: list[dict], wafers: list[tuple]) -> oracledb.Connection:
    cur = conn.cursor()
    cur.arraysize = BATCH
    load_tm = datetime.now()
    buf: list[tuple] = []
    total = 0
    lot_wf_cache: dict[str, tuple[str, str, int]] = {}
    reconnect_every = 200_000
    next_reconnect = reconnect_every
    try:
        for i, wafer in enumerate(wafers, start=1):
            buf.extend(build_rows_for_wafer(wafer, ops, load_tm, lot_wf_cache))
            if len(buf) >= BATCH:
                cur.executemany(INSERT_SQL, buf)
                conn.commit()
                total += len(buf)
                buf = []
                if i % 200 == 0 or total % 100_000 < BATCH:
                    print(f"  배치 INSERT: {total}행 ({i}/{len(wafers)} wafer)", flush=True)
                if total >= next_reconnect:
                    cur.close()
                    conn.close()
                    conn = _reconnect()
                    cur = conn.cursor()
                    cur.arraysize = BATCH
                    next_reconnect += reconnect_every
                    print(f"  접속 재연결 ({total}행 이후)", flush=True)
        if buf:
            cur.executemany(INSERT_SQL, buf)
            conn.commit()
            total += len(buf)
    except Exception:
        if _is_connected(conn):
            conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except oracledb.Error:
            pass
    print(f"적재 완료: {total}행")
    return conn


def verify(cur: oracledb.Cursor, expected: int, yield_lots: int, yield_wafers: int) -> dict:
    out: dict = {}
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    out["rows"] = int(cur.fetchone()[0])

    cur.execute(f"SELECT COUNT(DISTINCT LOT_ID), COUNT(DISTINCT LOT_ID||'|'||WF_ID) FROM {TABLE}")
    out["legend_lots"], out["legend_wafers"] = (int(v) for v in cur.fetchone())
    out["lots_match"] = out["legend_lots"] == yield_lots
    out["wafers_match"] = out["legend_wafers"] == yield_wafers

    cur.execute(
        f"""
        SELECT MIN(cnt), MAX(cnt), COUNT(*) AS wafers,
               SUM(CASE WHEN cnt = :n THEN 1 ELSE 0 END) AS wafers_300
        FROM (
            SELECT LOT_ID, WF_ID, COUNT(*) cnt
            FROM {TABLE}
            GROUP BY LOT_ID, WF_ID
        )
        """,
        {"n": OPS_PER_WAFER},
    )
    out["rows_per_wf_min"], out["rows_per_wf_max"], out["grouped_wafers"], out["wafers_300"] = cur.fetchone()

    cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT LOT_ID, WF_ID
            FROM {TABLE}
            GROUP BY LOT_ID, WF_ID
            HAVING COUNT(DISTINCT END_TM) != COUNT(*) OR COUNT(DISTINCT END_TM) != :n
        )
        """,
        {"n": OPS_PER_WAFER},
    )
    out["wafers_end_tm_not_300_distinct"] = int(cur.fetchone()[0])

    cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT l.LOT_ID, l.WF_ID
            FROM {TABLE} l
            JOIN (
                SELECT LOT_ID, WFID, MIN(MEASURETIME_START) meas
                FROM {SOURCE}
                GROUP BY LOT_ID, WFID
            ) y
              ON y.LOT_ID = l.LOT_ID AND y.WFID = l.WF_ID
            GROUP BY l.LOT_ID, l.WF_ID, y.meas
            HAVING MAX(TO_DATE(l.END_TM, 'YYYY-MM-DD HH24:MI:SS')) >= y.meas
        )
        """
    )
    out["wafers_end_tm_not_before_meas"] = int(cur.fetchone()[0])

    cur.execute(
        f"""
        SELECT OPER_ID, COUNT(DISTINCT EQP_ID) n_eqp
        FROM {TABLE}
        GROUP BY OPER_ID
        """
    )
    eqp_counts = [int(r[1]) for r in cur.fetchall()]
    dist = Counter(eqp_counts)
    out["eqp_per_oper_min"] = min(eqp_counts) if eqp_counts else None
    out["eqp_per_oper_max"] = max(eqp_counts) if eqp_counts else None
    out["eqp_per_oper_dist"] = dict(sorted(dist.items()))

    cur.execute(
        f"""
        SELECT
            (SELECT COUNT(*) FROM (
                SELECT DISTINCT LOT_ID, WF_ID FROM {TABLE}
            )) legend_wf,
            (SELECT COUNT(*) FROM (
                SELECT DISTINCT l.LOT_ID, l.WF_ID
                FROM {TABLE} l
                JOIN {SOURCE} y
                  ON y.LOT_ID = l.LOT_ID AND y.WFID = l.WF_ID
            )) matched_wf,
            (SELECT COUNT(*) FROM (
                SELECT DISTINCT LOT_ID, WFID FROM {SOURCE}
            )) yield_wf
        FROM dual
        """
    )
    legend_wf, matched_wf, yield_wf = (int(v) for v in cur.fetchone())
    out["join_legend_wf"] = legend_wf
    out["join_matched_wf"] = matched_wf
    out["join_yield_wf"] = yield_wf
    out["join_match_rate"] = (matched_wf / legend_wf) if legend_wf else None

    cur.execute(
        f"""
        SELECT
            SUM(CASE WHEN family_photo = 1 AND reticle_ok = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN family_photo = 0 AND photo_null_ok = 0 THEN 1 ELSE 0 END)
        FROM (
            SELECT
                CASE WHEN OPER_DET_DESC LIKE '% PHOTO' THEN 1 ELSE 0 END family_photo,
                CASE WHEN RETICLE_ID IS NOT NULL AND STEPPER_ID IS NOT NULL
                      AND CHUCK_ID IS NOT NULL AND START_CHUCK_ID IS NOT NULL
                      AND STEPPER_RECIPE_ID IS NOT NULL THEN 1 ELSE 0 END reticle_ok,
                CASE WHEN RETICLE_ID IS NULL AND STEPPER_ID IS NULL
                      AND CHUCK_ID IS NULL AND START_CHUCK_ID IS NULL
                      AND STEPPER_RECIPE_ID IS NULL THEN 1 ELSE 0 END photo_null_ok
            FROM {TABLE}
        )
        """
    )
    out["photo_missing_fields"], out["nonphoto_nonnull_fields"] = cur.fetchone()

    cur.execute(
        f"""
        SELECT LOT_CD, PROD_ID, OPER_ID, OPER_DET_DESC, LOT_ID, ALIAS_LOT_ID, WF_ID,
               EQP_ID, EQP_CH, RECIPE_ID, RETICLE_ID, CHUCK_ID, END_TM, TRBL_LOT_YN, HOT_TYP
        FROM {TABLE}
        WHERE LOT_ID = (SELECT MIN(LOT_ID) FROM {TABLE})
          AND WF_ID = (
              SELECT MIN(WF_ID) FROM {TABLE}
              WHERE LOT_ID = (SELECT MIN(LOT_ID) FROM {TABLE})
          )
          AND OPER_ID IN (
              SELECT MIN(OPER_ID) FROM {TABLE}
              UNION ALL
              SELECT MIN(OPER_ID) FROM {TABLE} WHERE OPER_DET_DESC LIKE '% PHOTO'
              UNION ALL
              SELECT MAX(OPER_ID) FROM {TABLE}
          )
        ORDER BY OPER_ID
        """
    )
    cols = [d[0] for d in cur.description]
    out["sample"] = [dict(zip(cols, row)) for row in cur.fetchall()]
    out["expected"] = expected
    return out


def print_plan(counts: dict, ops: list[dict]) -> None:
    print("대상 규모")
    print(f"  수율 테이블 lot 수 (DISTINCT LOT_ID): {counts['lots']}")
    print(f"  수율 테이블 wafer 수 (DISTINCT GORUPKEY): {counts['wafers']}")
    print(f"  제품 수 (DISTINCT LOT_CD): {counts['products']}")
    print(f"  예상 행 수 (wafer×{OPS_PER_WAFER}): {counts['expected']}")
    print(f"  현재 {TABLE} 행 수: {counts['existing']}")
    fam = Counter(op["family"] for op in ops)
    photo = sum(1 for op in ops if op["photo"])
    print(f"  공정 수: {len(ops)} (PHOTO {photo}개)")
    print(f"  패밀리 분포: {dict(sorted(fam.items()))}")
    print(f"  오퍼별 장비 풀 크기: {Counter(len(op['eqp_ids']) for op in ops)}")


def print_verify(result: dict) -> None:
    print("검증")
    print(f"  총 행: {result['rows']} (기대 {result['expected']})")
    print(f"  lot 수: {result['legend_lots']} (일치 {result['lots_match']})")
    print(f"  wafer 수: {result['legend_wafers']} (일치 {result['wafers_match']})")
    print(
        "  wafer당 행: min={rows_per_wf_min} max={rows_per_wf_max} "
        "300행 wafer={wafers_300}/{grouped_wafers}".format(**result)
    )
    print(f"  END_TM이 300개 distinct가 아닌 wafer: {result['wafers_end_tm_not_300_distinct']}")
    print(f"  최대 END_TM >= 측정시각인 wafer: {result['wafers_end_tm_not_before_meas']}")
    print(
        "  오퍼별 EQP_ID 개수: min={eqp_per_oper_min} max={eqp_per_oper_max} 분포={eqp_per_oper_dist}".format(
            **result
        )
    )
    rate = result["join_match_rate"]
    rate_s = "n/a" if rate is None else f"{rate:.6f}"
    print(
        f"  LOT_ID+WF_ID 조인: legend={result['join_legend_wf']} "
        f"yield={result['join_yield_wf']} matched={result['join_matched_wf']} rate={rate_s}"
    )
    print(
        f"  PHOTO 필드 누락 행: {result['photo_missing_fields']}, "
        f"비PHOTO에 포토필드가 채워진 행: {result['nonphoto_nonnull_fields']}"
    )
    print("  샘플:")
    for row in result["sample"]:
        print(f"    {row}")


def main(plan_only: bool) -> int:
    _load_dotenv()
    ops = build_operations()
    conn = get_connection()
    try:
        cur = conn.cursor()
        counts = plan_counts(cur)
        print_plan(counts, ops)
        if counts["expected"] > ROW_CAP:
            print(f"예상 행이 {ROW_CAP}을 넘어 적재하지 않습니다. 확인이 필요합니다.")
            return 2
        if plan_only:
            return 0
        if counts["existing"] == counts["expected"]:
            print(f"{TABLE}에 이미 {counts['existing']}행이 있어 중복 적재하지 않습니다.")
            result = verify(cur, counts["expected"], counts["lots"], counts["wafers"])
            print_verify(result)
            return 0
        if counts["existing"] > 0:
            print(
                f"{TABLE}에 불완전 적재 {counts['existing']}행이 있어 "
                f"이 테이블만 비운 뒤 다시 넣습니다."
            )
            cur.close()
            clear_table(conn)
            cur = conn.cursor()
        wafers = fetch_wafers(cur)
        if len(wafers) != counts["wafers"]:
            raise RuntimeError(f"wafer 목록 수 {len(wafers)} != distinct GORUPKEY {counts['wafers']}")
        cur.close()
        conn = seed(conn, ops, wafers)
        cur = conn.cursor()
        result = verify(cur, counts["expected"], counts["lots"], counts["wafers"])
        print_verify(result)
        ok = (
            result["rows"] == counts["expected"]
            and result["lots_match"]
            and result["wafers_match"]
            and int(result["wafers_300"]) == counts["wafers"]
            and result["wafers_end_tm_not_300_distinct"] == 0
            and result["wafers_end_tm_not_before_meas"] == 0
            and result["join_match_rate"] == 1
            and result["photo_missing_fields"] == 0
            and result["nonphoto_nonnull_fields"] == 0
            and result["eqp_per_oper_min"] >= 1
            and result["eqp_per_oper_max"] <= 6
        )
        if not ok:
            print("검증 실패")
            return 1
        print("검증 통과")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DF_WF_LEGEND 테스트용 더미 적재")
    parser.add_argument("--plan", action="store_true", help="규모만 확인하고 적재하지 않는다")
    args = parser.parse_args()
    sys.exit(main(plan_only=args.plan))
