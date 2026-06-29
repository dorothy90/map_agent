"""
Mining API 더미 데이터 소스
===========================
실제 mining API(HTTP/Oracle)가 아직 없으므로, 호출 시 pandas DataFrame 묶음
(dict[str, pd.DataFrame])을 반환하는 더미 함수를 둔다.

추후 실제 API가 생기면 `fetch_mining_dataframes` 본문만 requests 호출 등으로 교체하면
되도록 경계를 깨끗이 둔다. (호출부 `_call_minig_api`는 무수정)
"""

from __future__ import annotations

from typing import Dict

import pandas as pd


def _count_lots(group_str: str) -> int:
    """콤마 결합 그룹 문자열("alias.wf,alias.wf")의 LOT 개수."""
    return len([t for t in (group_str or "").split(",") if t.strip()])


def fetch_mining_dataframes(
    lot_cd: str,
    group_good: str,
    group_bad: str,
    fail_name: str,
    mode: str,
    tech: str,
    user_id: str,
    rank_limit: int,
) -> Dict[str, pd.DataFrame]:
    """더미: 입력 파라미터를 살짝 반영한 DataFrame 묶음을 반환한다.

    반환 키:
      - "df_GINI.parq"    : gini 분석용 (parameter, gini 컬럼)
      - "df_summary.parq" : 요약 테이블
      - "df_empty.parq"   : 빈 DataFrame (files_downloaded 필터 동작 확인용)
    """
    # 실제 mining API df_GINI.parq 스키마를 모사 (추후 실제 호출로 교체 시에도 컬럼 동일).
    n = max(1, rank_limit)
    df_gini = pd.DataFrame(
        {
            "oper_det_desc": [f"{tech or 'T'}_OPER_{i:02d}" for i in range(1, n + 1)],
            "Key Value": [f"KV{i:02d}" for i in range(1, n + 1)],
            "Operation Type": ["PHOTO" if i % 2 else "ETCH" for i in range(n)],
            "Score": [round(0.95 - i * 0.04, 4) for i in range(n)],
            "Rank_Sum": [i + 1 for i in range(n)],
            "GINI": [round(0.9 - i * 0.05, 4) for i in range(n)],
            "Commonality": [round(0.8 - i * 0.03, 4) for i in range(n)],
            "Purity": [round(0.85 - i * 0.02, 4) for i in range(n)],
            "JSD": [round(0.5 - i * 0.02, 4) for i in range(n)],
            "WRAcc": [round(0.3 - i * 0.01, 4) for i in range(n)],
            f"{fail_name}_AREA": [round(100.0 - i * 5, 2) for i in range(n)],
            "Left_Sum": [10 + i for i in range(n)],
            "Bad_Left": [5 + i for i in range(n)],
            "Good_Left": [5 for _ in range(n)],
            "Right_Sum": [20 - i for i in range(n)],
            "Bad_Right": [2 for _ in range(n)],
            "Good_Right": [18 - i for i in range(n)],
            "Bad_Progress": [round(0.5 + i * 0.01, 3) for i in range(n)],
            "Good_Progress": [round(0.4 - i * 0.01, 3) for i in range(n)],
            "min_end_tm": ["2026-06-01"] * n,
            "max_end_tm": ["2026-06-15"] * n,
            "Rank": [i + 1 for i in range(n)],
            "Ratio": [round(0.6 - i * 0.02, 4) for i in range(n)],
            "Rank_Ratio": [round((i + 1) / n, 4) for i in range(n)],
            "Mode": [mode] * n,
            "FailName": [fail_name] * n,
        }
    )

    df_summary = pd.DataFrame(
        {
            "group": ["good", "bad"],
            "n_lots": [_count_lots(group_good), _count_lots(group_bad)],
            "mode": [mode, mode],
            "user_id": [user_id, user_id],
        }
    )

    return {
        "df_GINI.parq": df_gini,
        "df_summary.parq": df_summary,
        "df_empty.parq": pd.DataFrame(),
    }
