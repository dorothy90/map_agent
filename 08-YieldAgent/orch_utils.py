"""orch_utils — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import logging
from typing import Any

from dotenv import load_dotenv

from common import get_llm

load_dotenv(override=True)


logger = logging.getLogger("yield_agent.supervisor")
_model = get_llm()



# worker AIMessage 판별용 name 집합 — supervisor_node/replanner_node 양쪽에서 사용.
_AGENT_NAMES = {
    "yield_agent",
    "wads_agent",
    "map_agent",
    "fail_history_agent",
    "ppt_export",
    "lot_history_agent",
    "relation_tree_agent",
    "wt_resp_agent",
    "mining_agent",
}


def _normalize_map_oper(raw: str) -> str:
    """interrupt 응답을 정규화: '1h'→'PT1H', 'pt1c'→'PT1C' 등"""
    v = raw.strip().upper()
    if v in ("PT1H", "PT1C"):
        return v
    if v in ("1H", "1C"):
        return f"PT{v}"
    if v.startswith("PT1") and len(v) > 3 and v[3] in ("H", "C"):
        return v[:4]
    return ""


def _groupkey_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _is_placeholder_or_empty(val) -> bool:
    """빈 값 + LLM이 흔히 출력하는 placeholder 패턴 감지 (#L1+L2 fix).

    Planner LLM이 chained input에 narrative placeholder를 박는 경우가 있음:
      - "<task_1 결과 lot IDs>", "<task_1_result_lot_ids>"
      - "{{from_task_1}}", "__from_task__"
      - "task_1 결과", "result of task_1"
    이 모든 경우를 빈 input으로 간주하여 replanner LLM이 채우도록 한다.
    """
    if val is None:
        return True
    if not isinstance(val, str):
        return not val
    v = val.strip()
    if not v:
        return True
    if (
        (v.startswith("<") and v.endswith(">"))
        or (v.startswith("{{") and v.endswith("}}"))
        or (v.startswith("__") and v.endswith("__"))
    ):
        return True
    lower = v.lower()
    if any(
        k in lower
        for k in (
            "task_1",
            "task_2",
            "task_3",
            "결과",
            "from_task",
            "result of",
            "from task",
        )
    ):
        return True
    return False
