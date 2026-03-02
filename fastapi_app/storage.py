from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from random import Random
from typing import Any, Union

from .models import Pt1cPara, GmsPara, WeeklyRecordPt1h, WeeklyRecordPt1c, WeeklyRecordGms

WeeklyRecordAny = Union[WeeklyRecordPt1h, WeeklyRecordPt1c, WeeklyRecordGms]


def _iso_week_str(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_bounds(d: date) -> tuple[date, date]:
    # ISO week starts on Monday.
    weekday = d.isoweekday()  # Mon=1..Sun=7
    start = d - timedelta(days=weekday - 1)
    end = start + timedelta(days=6)
    return start, end


def _stable_seed_int(key: str) -> int:
    h = sha256(key.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _gen_weekly_record(*, lotcd: str, unit: str, process: str, week_start: date) -> WeeklyRecordPt1h:
    week = _iso_week_str(week_start)
    week_end = week_start + timedelta(days=6)
    seed_key = f"{lotcd}|{unit}|{process}|{week}"
    rng = Random(_stable_seed_int(seed_key))

    lotcount = rng.randint(30, 180)
    wfCount = rng.randint(lotcount * 18, lotcount * 32)

    def f(low: float, high: float) -> float:
        return round(rng.uniform(low, high), 2)

    # 모든 파라미터 값을 1 이하로 생성
    pt1hPara = {
        "VTH": f(0.35, 0.85),
        "IDSAT": f(0.005, 0.15),
        "IDLIN": f(0.002, 0.08),
        "IOFF": f(1e-12, 5e-7),
        "ION": f(0.01, 0.25),
        "IGATE": f(1e-12, 5e-8),
        "IDDQ": f(1e-6, 5e-3),
        "VMIN": f(0.6, 0.95),
        "FMAX": f(0.2, 0.95),
        "TPD": f(0.1, 0.95),
        "GM_MAX": f(0.1, 0.95),
        "SS": f(0.6, 0.95),
        "DIBL": f(0.01, 0.2),
        "RON": f(0.1, 0.95),
        "RDS_ON": f(0.1, 0.95),
        "BVDS": f(0.05, 0.8),
        "LEAK_ID": f(1e-12, 1e-7),
        "LEAK_IG": f(1e-12, 1e-8),
        "CGB": f(0.05, 0.5),
        "CGS": f(0.05, 0.5),
        "CGD": f(0.05, 0.5),
        "CDS": f(0.05, 0.5),
        "RSH": f(0.01, 0.95),
        "RD": f(0.1, 0.95),
        "RS": f(0.1, 0.95),
    }

    return WeeklyRecordPt1h(
        lotcd=lotcd,  # type: ignore[arg-type]
        unit=unit,  # type: ignore[arg-type]
        process="pt1h",
        week=week,
        week_start=week_start,
        week_end=week_end,
        lotcount=lotcount,
        wfCount=wfCount,
        pt1hPara=pt1hPara,  # type: ignore[arg-type]
    )


def _gen_weekly_record_pt1c(*, lotcd: str, unit: str, week_start: date) -> WeeklyRecordPt1c:
    """pt1c용 주간 레코드 생성 (pt1h와 동일 파라미터 구조)"""
    pt1h_rec = _gen_weekly_record(lotcd=lotcd, unit=unit, process="pt1h", week_start=week_start)
    return WeeklyRecordPt1c(
        lotcd=pt1h_rec.lotcd,
        unit=pt1h_rec.unit,
        process="pt1c",
        week=pt1h_rec.week,
        week_start=pt1h_rec.week_start,
        week_end=pt1h_rec.week_end,
        lotcount=pt1h_rec.lotcount,
        wfCount=pt1h_rec.wfCount,
        pt1cPara=Pt1cPara.model_validate(pt1h_rec.pt1hPara.model_dump()),
    )


def _gen_weekly_record_gms(*, lotcd: str, unit: str, week_start: date) -> WeeklyRecordGms:
    """GMS용 주간 레코드 생성"""
    week = _iso_week_str(week_start)
    week_end = week_start + timedelta(days=6)
    seed_key = f"{lotcd}|{unit}|gms|{week}"
    rng = Random(_stable_seed_int(seed_key))
    lotcount = rng.randint(30, 180)
    wfCount = rng.randint(lotcount * 18, lotcount * 32)

    def f(low: float, high: float) -> float:
        return round(rng.uniform(low, high), 2)

    gmsPara = {
        "cum0": f(0.96, 0.99),
        "cum2": f(0.95, 0.98),
        "fab": f(0.94, 0.97),
        "prb": f(0.93, 0.96),
        "pnt": f(0.92, 0.95),
    }
    return WeeklyRecordGms(
        lotcd=lotcd,  # type: ignore[arg-type]
        unit=unit,  # type: ignore[arg-type]
        process="gms",
        week=week,
        week_start=week_start,
        week_end=week_end,
        lotcount=lotcount,
        wfCount=wfCount,
        gmsPara=gmsPara,  # type: ignore[arg-type]
    )


@dataclass
class WeeklyStore:
    path: Path
    lotcd: str = "4SS"
    unit: str = "weekly"
    process: str = "pt1h"
    weeks_to_keep: int = 10  # ~2 months

    _by_week: dict[str, WeeklyRecordAny] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _record_model(self):
        if self.process == "pt1h":
            return WeeklyRecordPt1h
        if self.process == "pt1c":
            return WeeklyRecordPt1c
        return WeeklyRecordGms

    def load(self) -> None:
        if self._by_week is not None:
            return
        self._by_week = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw.get("items", [])
        model = self._record_model()
        for item in items:
            try:
                rec = model.model_validate(item)
            except Exception:
                continue
            self._by_week[rec.week] = rec

    def save(self) -> None:
        if self._by_week is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "lotcd": self.lotcd,
                "unit": self.unit,
                "process": self.process,
                "weeks_to_keep": self.weeks_to_keep,
            },
            "items": [self._by_week[k].model_dump(mode="json") for k in sorted(self._by_week.keys())],
        }
        # 고유 tmp 파일 사용 — 동시 쓰기 시 파일 충돌 방지
        tmp = self.path.with_name(f"{self.path.stem}_{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def ensure_window_for_date(self, d: date) -> None:
        """요청된 날짜가 포함된 주와 그 이전 weeks_to_keep-1주 데이터를 보장.
        오래된 주는 삭제하지 않음 — 동시 요청 간 race condition 방지.
        """
        self.load()
        assert self._by_week is not None

        week_start, _ = _week_bounds(d)
        starts: list[date] = [week_start - timedelta(days=7 * i) for i in range(self.weeks_to_keep - 1, -1, -1)]
        changed = False
        for s in starts:
            w = _iso_week_str(s)
            if w not in self._by_week:
                if self.process == "pt1h":
                    self._by_week[w] = _gen_weekly_record(
                        lotcd=self.lotcd, unit=self.unit, process="pt1h", week_start=s
                    )
                elif self.process == "pt1c":
                    self._by_week[w] = _gen_weekly_record_pt1c(
                        lotcd=self.lotcd, unit=self.unit, week_start=s
                    )
                else:
                    self._by_week[w] = _gen_weekly_record_gms(
                        lotcd=self.lotcd, unit=self.unit, week_start=s
                    )
                changed = True

        if changed:
            self.save()

    def get_week(self, d: date) -> WeeklyRecordAny:
        with self._lock:
            self.ensure_window_for_date(d)
            assert self._by_week is not None
            week_start, _ = _week_bounds(d)
            week = _iso_week_str(week_start)
            return self._by_week[week]


PROCESS_FILES: dict[str, str] = {
    "pt1h": "pt1h_weekly.json",
    "pt1c": "pt1c_weekly.json",
    "gms": "gms_weekly.json",
}


_store_cache: dict[str, WeeklyStore] = {}
_store_cache_lock = threading.Lock()


def get_store(process: str) -> WeeklyStore:
    """process별 싱글턴 WeeklyStore 반환 — 동시 요청에도 인스턴스 공유"""
    if process not in PROCESS_FILES:
        raise ValueError(f"process는 {list(PROCESS_FILES.keys())} 중 하나여야 합니다.")
    with _store_cache_lock:
        if process not in _store_cache:
            data_path = Path(__file__).resolve().parent / "data" / PROCESS_FILES[process]
            _store_cache[process] = WeeklyStore(path=data_path, process=process)
        return _store_cache[process]


def default_store() -> WeeklyStore:
    """하위 호환: pt1h store 반환"""
    return get_store("pt1h")

