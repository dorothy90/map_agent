"""
Mining Agent — 데이터 마이닝 분석 도구
======================================
양품/불량 그룹을 비교해 gini 기반 기여 파라미터를 마이닝한다.

본 단계: model + @tool + dummy api 만. (함수형 노드·supervisor 배선은 후속)
실제 mining API는 `mining_dummy_api.fetch_mining_dataframes`로 더미 대체.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import pandas as pd
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langfuse import observe
from pydantic import BaseModel, Field

from common import timed
from mining_dummy_api import fetch_mining_dataframes

logger = logging.getLogger("yield_agent.mining_agent")


def _as_list(value: Any) -> List[str]:
    """그룹값을 문자열 리스트로 정규화 (타입 가드 수준만)."""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


class MiningParsingResult(BaseModel):
    """mining_analysis 호출에 필요한 슬롯. 의미·예시를 명시해 LLM이 올바른 슬롯에 값을 넣게 한다."""

    lot_cd: str = Field(..., description='3자 제품코드, 예: "4SS"')
    group_good: List[str] = Field(
        default_factory=list,
        description='양품 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH083", "TSAH085"]',
    )
    group_bad: List[str] = Field(
        default_factory=list,
        description='불량 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH090", "TSAH092"]',
    )
    fail_name: str = Field(
        ...,
        description='파라미터(불량명), 괄호 안에 bin category 포함. 예: "DIBL(D)", "BVDS(B)", "TWT(T)". '
        "유효값: DIBL(D)/BVDS(B)/VMIN(M)/IDDQ(F)/GATE_OX(G)/FMAX(X)/TWT(T)/IGATE(P)/RON(R)",
    )
    mode: str = Field(..., description='분석 모드(공정), 예: "PT1H", "PT1C"')
    tech: str = Field(..., description="기술/공정 세대 코드")
    user_id: str = Field(..., description="요청 사용자 ID")
    rank_limit: int = Field(10, description="상위 N개 제한, 예: 10")


def _call_minig_api(
    lot_cd: str,
    group_good: List[str],
    group_bad: List[str],
    fail_name: str,
    mode: str,
    tech: str,
    user_id: str,
    rank_limit: int,
) -> Dict[str, pd.DataFrame]:
    """mining API 호출 → DataFrame 묶음(dict[str, pd.DataFrame]) 반환.

    현재는 더미(`fetch_mining_dataframes`)로 위임. 추후 실제 호출로 교체.
    """
    logger.info(
        "[_call_minig_api] lot_cd=%s fail_name=%s mode=%s tech=%s user_id=%s rank_limit=%s "
        "good=%d bad=%d",
        lot_cd,
        fail_name,
        mode,
        tech,
        user_id,
        rank_limit,
        len(group_good),
        len(group_bad),
    )
    dataframes = fetch_mining_dataframes(
        lot_cd=lot_cd,
        group_good=group_good,
        group_bad=group_bad,
        fail_name=fail_name,
        mode=mode,
        tech=tech,
        user_id=user_id,
        rank_limit=rank_limit,
    )
    return dataframes


def _analyze_gini(df_GINI: pd.DataFrame) -> Dict[str, Any]:
    """gini DataFrame → 상위 기여 파라미터 요약. 타입 가드 수준의 방어만."""
    if df_GINI is None or df_GINI.empty:
        return {"status": "empty", "rows": 0, "items": []}
    if "gini" not in df_GINI.columns:
        return {"status": "no_gini_column", "rows": int(len(df_GINI)), "items": []}

    ranked = df_GINI.sort_values("gini", ascending=False)
    return {
        "status": "ok",
        "rows": int(len(ranked)),
        "items": ranked.to_dict(orient="records"),
    }


@tool
def mining_analysis(
    lot_cd: str,
    group_good: List[str],
    group_bad: List[str],
    fail_name: str,
    mode: str,
    tech: str,
    user_id: str,
    rank_limit: int = 10,
) -> Dict[str, Any]:
    """양품/불량 그룹을 비교해 gini 기반 기여 파라미터를 마이닝한다.

    Args:
        lot_cd: 3자 제품코드, 예: "4SS".
        group_good: 양품 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH083", "TSAH085"].
        group_bad: 불량 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH090", "TSAH092"].
        fail_name: 파라미터(불량명), 괄호 안에 bin category 포함. 예: "DIBL(D)", "BVDS(B)", "TWT(T)". 유효값: DIBL(D)/BVDS(B)/VMIN(M)/IDDQ(F)/GATE_OX(G)/FMAX(X)/TWT(T)/IGATE(P)/RON(R).
        mode: 분석 모드(공정), 예: "PT1H", "PT1C".
        tech: 기술/공정 세대 코드.
        user_id: 요청 사용자 ID.
        rank_limit: 상위 N개 제한, 예: 10.

    Returns:
        result_summary dict (status / lot_cd / fail_name / mode / files_downloaded / gini_analysis).
    """
    dataframes = _call_minig_api(
        lot_cd,
        group_good,
        group_bad,
        fail_name,
        mode,
        tech,
        user_id,
        rank_limit,
    )

    for file_name, df in dataframes.items():
        logger.info("[mining_analysis] %s rows=%d", file_name, len(df))

    df_GINI = dataframes.get("df_GINI.parq", pd.DataFrame())
    gini_analysis = _analyze_gini(df_GINI)

    result_summary = {
        "status": "success",
        "lot_cd": lot_cd,
        "fail_name": fail_name,
        "mode": mode,
        "files_downloaded": [k for k, v in dataframes.items() if not v.empty],
        "gini_analysis": gini_analysis,
    }
    return result_summary


@observe(name="mining_agent_node")
@timed
def mining_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """함수형 노드: state(상류 wads→wt_resp 공유키)에서 슬롯을 읽어 mining_analysis 실행.

    공유키 재사용: lot_cd=lotcd, fail_name=fail_type, mode=wads_category.
    group_good/group_bad는 사용자 직접 입력 또는 상류 결과로 채워지는 chained-input.
    """
    lot_cd = (state.get("lotcd") or "").strip()
    fail_name = (state.get("fail_type") or "").strip()
    mode = (state.get("wads_category") or "").strip()
    group_good = _as_list(state.get("group_good"))
    group_bad = _as_list(state.get("group_bad"))
    tech = (state.get("tech") or "").strip()
    user_id = (state.get("user_id") or "").strip()
    rank_limit = state.get("rank_limit") or 10
    current_task_id = state.get("current_task_id", "")

    logger.info(
        "[Mining Agent] lot_cd=%s fail_name=%s mode=%s tech=%s good=%d bad=%d rank_limit=%s",
        lot_cd,
        fail_name,
        mode,
        tech,
        len(group_good),
        len(group_bad),
        rank_limit,
    )

    result = mining_analysis.invoke(
        {
            "lot_cd": lot_cd,
            "group_good": group_good,
            "group_bad": group_bad,
            "fail_name": fail_name,
            "mode": mode,
            "tech": tech,
            "user_id": user_id,
            "rank_limit": rank_limit,
        }
    )

    gini = result.get("gini_analysis", {})
    files = result.get("files_downloaded", [])
    summary = (
        f"{lot_cd or '지정 제품'} {fail_name or '지정 파라미터'} 마이닝 분석 완료 "
        f"(mode={mode or '-'}, gini={gini.get('status', '-')}, files={len(files)})"
    )
    return {
        "messages": [AIMessage(content=summary, name="mining_agent")],
        "agent_suggestion": "",
        "past_steps": [(current_task_id, summary[:300])],
    }


if __name__ == "__main__":
    # 커널 테스트: `python mining_agent.py` 또는 커널에서 `%run mining_agent.py`
    import json

    # 1) tool 직접 호출 (LLM이 부르는 경로와 동일)
    out = mining_analysis.invoke(
        {
            "lot_cd": "4SS",
            "group_good": ["TSAH083", "TSAH085"],
            "group_bad": ["TSAH090"],
            "fail_name": "DIBL(D)",
            "mode": "PT1H",
            "tech": "T1",
            "user_id": "dorothy90",
            "rank_limit": 5,
        }
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))

    # 2) 파싱 모델로 슬롯 묶어서 호출
    p = MiningParsingResult(
        lot_cd="4SS",
        group_good=["TSAH085"],
        group_bad=["TSAH092"],
        fail_name="TWT(T)",
        mode="PT1C",
        tech="T2",
        user_id="u9",
        rank_limit=3,
    )
    print(json.dumps(mining_analysis.invoke(p.model_dump()), ensure_ascii=False, indent=2))

    # 3) 원본 dataframes 확인
    dfs = _call_minig_api("4SS", ["TSAH083"], ["TSAH090"], "DIBL(D)", "PT1H", "T1", "u1", 4)
    for name, df in dfs.items():
        print(f"== {name} (rows={len(df)}, empty={df.empty}) ==")
        print(df)

    # 4) 함수형 노드: 상류 공유키(state) 경로 테스트
    state = {
        "lotcd": "4SS",
        "fail_type": "DIBL(D)",
        "wads_category": "PT1H",
        "group_good": ["TSAH083", "TSAH085"],
        "group_bad": ["TSAH090"],
        "tech": "T1",
        "user_id": "dorothy90",
        "rank_limit": 5,
        "current_task_id": "t1",
    }
    node_out = mining_agent_node(state, {})
    print("== NODE ==")
    print(node_out["messages"][0].content)
