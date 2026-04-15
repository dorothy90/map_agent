"""REPL 검증 agent 의 LangChain v1 create_agent 인스턴스 (lazy).

설계 원칙(plan 파일 § "코드 설계 (핵심 스케치)"):
- 수동 StateGraph 조립 없이 `langchain.agents.create_agent` 만 사용.
- 체크포인터는 MVP 단계에서 `InMemorySaver` (세션 격리만 필요, 영속성 불필요).
- 모델은 기존 yield-agent 와 동일하게 OpenRouter 경유 ChatOpenAI — common.py 의 get_llm 과
  설정은 동일하되, "import 의존 없음" 원칙을 지키기 위해 직접 구성.

lazy 초기화 이유: ChatOpenAI 는 생성 시 OPENROUTER_API_KEY 를 검증한다. 모듈 import 시점에
바로 만들면 env 미설정 환경(예: 테스트/CLI)에서 agent_server 전체가 import 실패한다.
따라서 최초 요청 시에만 구성한다.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

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
        llm = ChatOpenAI(
            model=os.getenv("DEFAULT_MODEL", "gpt-oss-120b"),
            base_url=os.getenv("OPENROUTER_BASE_URL"),
            api_key=os.getenv("OPENROUTER_API_KEY"),
            temperature=0,
        )
        _agent = create_agent(
            model=llm,
            tools=[run_python],
            system_prompt=SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )
        return _agent
