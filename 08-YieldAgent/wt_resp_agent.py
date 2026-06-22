"""
WT Resp Agent — 함수형 노드 (state 직접)
========================================
WT 계측 파라미터(wt_para)를 main_oper 기준으로 양품/불량 그룹과 비교 분석한다.

state 입력 (상류 wads→mining 파이프라인과 공유키 통일):
  - lotcd      (필수) 제품/로트 코드 (구 wt_resp_lotcode)
  - fail_type  (필수) WT 계측 파라미터 (구 wt_resp_wt_para)
  - cause_oper (필수) 기준 공정(main_oper) (구 wt_resp_main_oper)
  - group_good (선택) 추가 양품 그룹 식별자 목록 (구 wt_resp_extra_good)
  - group_bad  (선택) 추가 불량 그룹 식별자 목록 (구 wt_resp_extra_bad)

본 단계: graph wiring·라우팅 검증용 placeholder. 실제 분석은 후속.
LLM에게 도구 결정을 맡기지 않는다. 코드가 직접 state를 읽는다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import timed, html_escape as _h, get_oracle_connection

logger = logging.getLogger("yield_agent.wt_resp_agent")


def _query_good_bad(lotcd: str, fail_type: str, category: str = "") -> tuple[List[str], List[str]]:
    """DF_WADS_GOOD_BAD_LOT에서 (lotcd, fail_type[, category])로 good/bad LOT_ID 목록 조회.
    키=(LOTCD,CATEGORY,PARAMETER). PARAMETER=fail_type. main_oper로는 필터 안 함(컬럼 없음).
    LIKE %val% — lotcd 제품코드 부분일치 (wads_tools _query_wads_data 패턴 미러)."""
    if not lotcd or not fail_type:
        return [], []
    # LOTCD에는 제품코드(예 "4SS")가 들어있고 입력 lotcd는 제품코드 또는 풀 lot(예 "4SS2DPD")일 수
    # 있다 → 테이블의 LOTCD가 입력 lotcd의 부분문자열인 행을 매칭(양쪽 모두 동작).
    sql = (
        "SELECT GOOD_LOT_ID, BAD_LOT_ID FROM DF_WADS_GOOD_BAD_LOT "
        "WHERE LOTCD IS NOT NULL AND UPPER(:lotcd) LIKE '%' || UPPER(LOTCD) || '%' "
        "AND UPPER(PARAMETER) LIKE UPPER(:fail_type)"
    )
    binds = {"lotcd": lotcd, "fail_type": f"%{fail_type}%"}
    if category:
        sql += " AND UPPER(CATEGORY) LIKE UPPER(:category)"
        binds["category"] = f"%{category}%"
    conn = get_oracle_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, binds)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    good: List[str] = []
    bad: List[str] = []
    for r in rows:
        g = str((r[0] if r else "") or "").strip()
        b = str((r[1] if len(r) > 1 else "") or "").strip()
        if g and g not in good:
            good.append(g)
        if b and b not in bad:
            bad.append(b)
    return good, bad


@observe(name="wt_resp_agent_node")
@timed
def wt_resp_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    state = {**state, **((state.get("current_task") or {}).get("params") or {})}  # S1-a: task params 우선, scalar fallback
    lotcode = (state.get("lotcd") or "").strip()
    wt_para = (state.get("fail_type") or "").strip()
    main_oper = (state.get("cause_oper") or "").strip()
    current_task_id = state.get("current_task_id", "")

    logger.info(
        "[WT Resp Agent] lotcode=%s wt_para=%s main_oper=%s",
        lotcode,
        wt_para,
        main_oper,
    )

    # 필수 슬롯 검증: lotcode / wt_para / main_oper
    missing = [
        name
        for name, val in (
            ("lotcode", lotcode),
            ("wt_para", wt_para),
            ("main_oper", main_oper),
        )
        if not val
    ]
    if missing:
        msg = AIMessage(
            content=(
                "WT Resp 분석에 필요한 필수 항목이 없습니다: "
                f"{', '.join(missing)}. lotcode·wt_para·main_oper 를 알려주세요."
            ),
            name="wt_resp_agent",
        )
        return {
            "messages": [msg],
            "wt_resp_artifacts": [],
            "agent_suggestion": "",
            "past_steps": [(current_task_id, f"필수 항목 누락: {', '.join(missing)}")],
        }

    # DF_WADS_GOOD_BAD_LOT에서 양품/불량 그룹 LOT_ID를 산출 → state에 기록(다음 mining이 상속).
    category = (state.get("wads_category") or "").strip()
    group_good, group_bad = _query_good_bad(lotcode, wt_para, category)
    logger.info(
        "[WT Resp Agent] group_good=%d group_bad=%d (DF_WADS_GOOD_BAD_LOT)",
        len(group_good),
        len(group_bad),
    )

    html_content = (
        f"<h1>WT Resp 분석: {_h(lotcode)} / {_h(wt_para)}</h1>"
        f"<p>main_oper: {_h(main_oper)}</p>"
        f"<p>양품 그룹(good): {_h(', '.join(group_good)) or '-'}</p>"
        f"<p>불량 그룹(bad): {_h(', '.join(group_bad)) or '-'}</p>"
        f"<p>(WT 응답 비교 분석 본구현 예정)</p>"
    )

    summary = (
        f"{lotcode} WT Resp 분석 (wt_para={wt_para}, main_oper={main_oper}, "
        f"good={len(group_good)}, bad={len(group_bad)}) HTML 리포트 생성 완료"
    )
    return {
        "messages": [AIMessage(content=summary, name="wt_resp_agent")],
        "wt_resp_artifacts": [
            {
                "type": "html",
                "mime": "text/html",
                "data": html_content,
                "title": "wt_resp",
            }
        ],
        # 산출한 그룹을 state에 기록 → mining_agent가 상속(5번째·4번째 state).
        "group_good": group_good,
        "group_bad": group_bad,
        "agent_suggestion": "",
        "past_steps": [(current_task_id, summary[:300])],
    }


if __name__ == "__main__":
    # 커널 테스트: `python wt_resp_agent.py` 또는 커널에서 `%run wt_resp_agent.py`
    # 1) 정상 케이스
    state_ok = {
        "lotcd": "4SS",
        "fail_type": "DIBL(D)",
        "cause_oper": "PT1H",
        "group_good": ["TSAH083", "TSAH085"],
        "group_bad": "TSAH090",
        "current_task_id": "t1",
    }
    out = wt_resp_agent_node(state_ok, {})
    print("== OK ==")
    print(out["messages"][0].content)
    print("artifacts:", len(out["wt_resp_artifacts"]))

    # 2) 필수 누락 케이스 (main_oper 없음)
    state_missing = {
        "lotcd": "4SS",
        "fail_type": "DIBL(D)",
        "current_task_id": "t2",
    }
    out2 = wt_resp_agent_node(state_missing, {})
    print("== MISSING ==")
    print(out2["messages"][0].content)
