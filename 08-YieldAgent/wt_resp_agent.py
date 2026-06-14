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

from common import timed, html_escape as _h

logger = logging.getLogger("yield_agent.wt_resp_agent")


def _as_list(value: Any) -> List[str]:
    """선택 그룹값을 문자열 리스트로 정규화 (타입 가드 수준만)."""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


@observe(name="wt_resp_agent_node")
@timed
def wt_resp_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    lotcode = (state.get("lotcd") or "").strip()
    wt_para = (state.get("fail_type") or "").strip()
    main_oper = (state.get("cause_oper") or "").strip()
    extra_good = _as_list(state.get("group_good"))
    extra_bad = _as_list(state.get("group_bad"))
    current_task_id = state.get("current_task_id", "")

    logger.info(
        "[WT Resp Agent] lotcode=%s wt_para=%s main_oper=%s extra_good=%d extra_bad=%d",
        lotcode,
        wt_para,
        main_oper,
        len(extra_good),
        len(extra_bad),
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

    html_content = (
        f"<h1>WT Resp 분석: {_h(lotcode)} / {_h(wt_para)}</h1>"
        f"<p>main_oper: {_h(main_oper)}</p>"
        f"<p>추가 양품 그룹: {_h(', '.join(extra_good)) or '-'}</p>"
        f"<p>추가 불량 그룹: {_h(', '.join(extra_bad)) or '-'}</p>"
        f"<p>(WT 응답 비교 분석 본구현 예정)</p>"
    )

    summary = (
        f"{lotcode} WT Resp 분석 (wt_para={wt_para}, main_oper={main_oper}, "
        f"extra_good={len(extra_good)}, extra_bad={len(extra_bad)}) HTML 리포트 생성 완료"
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
