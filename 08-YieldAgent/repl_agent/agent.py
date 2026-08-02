"""REPL 검증 agent 의 LangChain v1 create_agent 인스턴스 (lazy).

설계 원칙(plan 파일 § "코드 설계 (핵심 스케치)"):
- 수동 StateGraph 조립 없이 `langchain.agents.create_agent` 만 사용.
- 체크포인터는 MVP 단계에서 `InMemorySaver` (세션 격리만 필요, 영속성 불필요).
- 모델은 기존 yield-agent와 동일하게 common.py의 OpenRouter Nemotron 전용
  get_llm 팩토리를 사용.

lazy 초기화 이유: ChatOpenAI는 생성 시 OpenRouter 설정을 검증한다. 모듈 import 시점에
바로 만들면 env 미설정 환경(예: 테스트/CLI)에서 agent_server 전체가 import 실패한다.
따라서 최초 요청 시에만 구성한다.
"""

from __future__ import annotations

import threading
from typing import Any

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from common import get_llm

from .prompts import SYSTEM_PROMPT
from .tools import run_python

_agent: Any = None
_lock = threading.Lock()


def get_agent():
    """lazy singleton — 첫 호출 시 agent 를 compile 한다."""
    global _agent
    if _agent is not None:
        return _agent
    with _lock:
        if _agent is not None:
            return _agent
        llm = get_llm()
        _agent = create_agent(
            model=llm,
            tools=[run_python],
            system_prompt=SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )
        return _agent
