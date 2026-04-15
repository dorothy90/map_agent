#!/usr/bin/env python3
"""repl_agent mock 용 sample.csv 생성 스크립트.

스키마 (wafer × process long-format):
    lotcd, lot_id, wf_id, fail_name, fail_value, oper, legend, legend_value, end_tm

- lot_id       : '4SS' + 4글자 (총 7글자)
- wf_id        : lot_id 당 1..25
- fail_name    : 각 wafer 마다 하나 (OPEN/SHORT/LEAK 중 랜덤)
- fail_value   : 각 wafer 마다 하나의 수치 (5~25)
- oper         : wafer 당 oper1..oper500
- legend       : 각 oper 당 {Equip, Chamber, Recipe, Route} 4 종
- legend_value : 각 legend 당 {Equip,Chamber,Recipe,Route}{1..4} 중 랜덤
- end_tm       : wafer 당 하나의 랜덤 'YYYY-MM-DD HH:MM:SS' (최근 2개월 범위)
                 wafer 의 2000 행은 모두 같은 end_tm 을 공유 (wafer completion time 해석)

기본값 (2 lotcd × 25 wafer × 500 oper × 4 legend) = 100,000 행.

재생성:
    python repl_agent/mock/data/generate_sample.py                  # 기본값
    python repl_agent/mock/data/generate_sample.py --opers 50       # 빠른 검증용
    python repl_agent/mock/data/generate_sample.py --end-date 2026-05-01   # 기준일 변경
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

LEGENDS = ("Equip", "Chamber", "Recipe", "Route")
FAIL_NAMES = ("OPEN", "SHORT", "LEAK")
LOTS = [
    # (lotcd, lot_id) — lot_id 는 '4SS' + 4글자 (총 7글자).
    ("L12345", "4SSA001"),
    ("L67890", "4SSB001"),
]

# 기본 end_tm 범위: 기준일 포함해 2개월 구간
DEFAULT_END_DATE = "2026-04-16"
DEFAULT_WINDOW_DAYS = 60


def _random_end_tm(rng: random.Random, start: datetime, seconds: int) -> str:
    """[start, start + seconds) 구간의 랜덤 타임스탬프를 'YYYY-MM-DD HH:MM:SS' 로."""
    delta = timedelta(seconds=rng.randrange(seconds))
    return (start + delta).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lots", type=int, default=len(LOTS), help="사용할 (lotcd,lot_id) 개수")
    ap.add_argument("--wafers", type=int, default=25, help="lot_id 당 wafer 수")
    ap.add_argument("--opers", type=int, default=500, help="wafer 당 oper 수 (oper1..operN)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--end-date", default=DEFAULT_END_DATE, help="end_tm 기준일 (YYYY-MM-DD). 이 날짜에서 과거 2개월 구간에서 랜덤 추출.")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS, help="end_tm 랜덤 추출 구간 (일 수). 기본 60일.")
    ap.add_argument(
        "--out",
        default=str(Path(__file__).parent / "sample.csv"),
        help="출력 CSV 경로",
    )
    args = ap.parse_args()

    rng = random.Random(args.seed)
    lots = LOTS[: args.lots]
    out_path = Path(args.out)

    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    tm_start = end_date - timedelta(days=args.window_days)
    tm_seconds = args.window_days * 24 * 60 * 60 + 24 * 60 * 60  # +1 day 로 기준일 포함

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "lotcd",
                "lot_id",
                "wf_id",
                "fail_name",
                "fail_value",
                "oper",
                "legend",
                "legend_value",
                "end_tm",
            ]
        )
        row_count = 0
        for lotcd, lot_id in lots:
            for wf_id in range(1, args.wafers + 1):
                fail_name = rng.choice(FAIL_NAMES)
                fail_value = round(rng.uniform(5.0, 25.0), 2)
                end_tm = _random_end_tm(rng, tm_start, tm_seconds)
                for oper_idx in range(1, args.opers + 1):
                    oper = f"oper{oper_idx}"
                    for legend in LEGENDS:
                        legend_value = f"{legend}{rng.randint(1, 4)}"
                        w.writerow(
                            [
                                lotcd,
                                lot_id,
                                wf_id,
                                fail_name,
                                fail_value,
                                oper,
                                legend,
                                legend_value,
                                end_tm,
                            ]
                        )
                        row_count += 1

    print(f"wrote {row_count:,} rows → {out_path}")
    print(
        f"  {args.lots} lotcd × {args.wafers} wafer × {args.opers} oper × {len(LEGENDS)} legend"
    )
    print(f"  end_tm window: {tm_start.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()
