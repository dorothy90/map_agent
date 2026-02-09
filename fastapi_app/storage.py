from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from random import Random
from typing import Any

from .models import WeeklyRecord


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


def _gen_weekly_record(*, lotcd: str, unit: str, process: str, week_start: date) -> WeeklyRecord:
    week = _iso_week_str(week_start)
    week_end = week_start + timedelta(days=6)
    seed_key = f"{lotcd}|{unit}|{process}|{week}"
    rng = Random(_stable_seed_int(seed_key))

    lotcount = rng.randint(30, 180)
    wfCount = rng.randint(lotcount * 18, lotcount * 32)

    def f(low: float, high: float) -> float:
        return round(rng.uniform(low, high), 2)

    # 값 범위는 "그럴듯한" 수준의 예시(단위는 각 항목별로 상이할 수 있음)
    # - 전압(V): ~0.3~1.2
    # - 전류(A): 누설은 1e-12~1e-6, 구동은 1e-3~1e-1 등
    # - 주파수(MHz): ~200~2000
    # - 지연(ps): ~10~500
    # - 저항(ohm): ~0.1~1000
    # - 커패시턴스(fF): ~0.5~50
    pt1hPara = {
        "VTH": f(0.35, 0.85),
        "IDSAT": f(0.005, 0.15),
        "IDLIN": f(0.002, 0.08),
        "IOFF": f(1e-12, 5e-7),
        "ION": f(0.01, 0.25),
        "IGATE": f(1e-12, 5e-8),
        "IDDQ": f(1e-6, 5e-3),
        "VMIN": f(0.6, 1.2),
        "FMAX": f(200, 2000),
        "TPD": f(10, 500),
        "GM_MAX": f(0.1, 10.0),
        "SS": f(60, 120),
        "DIBL": f(0.01, 0.2),
        "RON": f(0.1, 50),
        "RDS_ON": f(0.1, 100),
        "BVDS": f(5, 80),
        "LEAK_ID": f(1e-12, 1e-7),
        "LEAK_IG": f(1e-12, 1e-8),
        "CGB": f(0.5, 50),
        "CGS": f(0.5, 50),
        "CGD": f(0.5, 50),
        "CDS": f(0.5, 50),
        "RSH": f(1, 2000),
        "RD": f(0.1, 200),
        "RS": f(0.1, 200),
    }

    return WeeklyRecord(
        lotcd=lotcd,  # type: ignore[arg-type]
        unit=unit,  # type: ignore[arg-type]
        process=process,  # type: ignore[arg-type]
        week=week,
        week_start=week_start,
        week_end=week_end,
        lotcount=lotcount,
        wfCount=wfCount,
        pt1hPara=pt1hPara,
    )


@dataclass
class WeeklyStore:
    path: Path
    lotcd: str = "4SS"
    unit: str = "weekly"
    process: str = "pt1h"
    weeks_to_keep: int = 10  # ~2 months

    _by_week: dict[str, WeeklyRecord] | None = None

    def load(self) -> None:
        if self._by_week is not None:
            return
        self._by_week = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw.get("items", [])
        for item in items:
            try:
                rec = WeeklyRecord.model_validate(item)
            except Exception:
                # 스키마 변경(A~G -> 테스트명 25개 등) 시 구버전 데이터는 무시하고 재생성
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
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def ensure_window_for_date(self, d: date) -> None:
        self.load()
        assert self._by_week is not None

        week_start, _ = _week_bounds(d)
        # Keep a rolling window ending at requested week.
        starts: list[date] = [week_start - timedelta(days=7 * i) for i in range(self.weeks_to_keep - 1, -1, -1)]
        for s in starts:
            w = _iso_week_str(s)
            if w not in self._by_week:
                self._by_week[w] = _gen_weekly_record(
                    lotcd=self.lotcd, unit=self.unit, process=self.process, week_start=s
                )

        # Drop old weeks outside the window.
        keep = {_iso_week_str(s) for s in starts}
        for w in list(self._by_week.keys()):
            if w not in keep:
                del self._by_week[w]

        self.save()

    def get_week(self, d: date) -> WeeklyRecord:
        self.ensure_window_for_date(d)
        assert self._by_week is not None
        week_start, _ = _week_bounds(d)
        week = _iso_week_str(week_start)
        return self._by_week[week]


def default_store() -> WeeklyStore:
    data_path = Path(__file__).resolve().parent / "data" / "pt1h_weekly.json"
    return WeeklyStore(path=data_path)

