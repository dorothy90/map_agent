"""
gpt-oss-120b + OpenRouter에서 with_structured_output이 동작하는지 테스트.

실행: python test_structured_output.py
"""

import os
import json
import re
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import Literal

load_dotenv()

from langchain_openai import ChatOpenAI


# ── Pydantic 스키마 (supervisor.py의 RouteResponse 축소 버전) ──
class RouteResponse(BaseModel):
    next: Literal["yield_agent", "wads_agent", "map_agent", "FINISH"] = Field(
        description="다음에 실행할 에이전트"
    )
    lotcd: str = Field(default="", description="제품코드")
    message: str = Field(default="", description="사용자에게 보낼 메시지")


# ── LLM 설정 ──
llm = ChatOpenAI(
    model=os.getenv("DEFAULT_MODEL", "gpt-oss-120b"),
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
)

test_messages = [
    {"role": "system", "content": "You are a router. Route user requests to the correct agent. Respond with JSON only."},
    {"role": "user", "content": "4SS 수율 보여줘"},
]


# ============================================================
# 테스트 1: with_structured_output (실패 예상)
# ============================================================
def test_structured_output():
    print("=" * 60)
    print("테스트 1: with_structured_output(RouteResponse)")
    print("=" * 60)
    try:
        structured_llm = llm.with_structured_output(RouteResponse)
        result = structured_llm.invoke(test_messages)
        print(f"  성공! result = {result}")
        print(f"  type  = {type(result)}")
        return True
    except Exception as e:
        print(f"  실패! error = {type(e).__name__}: {e}")
        return False


# ============================================================
# 테스트 2: with_structured_output + method="json_mode"
# ============================================================
def test_json_mode():
    print("\n" + "=" * 60)
    print("테스트 2: with_structured_output(method='json_mode')")
    print("=" * 60)
    try:
        structured_llm = llm.with_structured_output(RouteResponse, method="json_mode")
        result = structured_llm.invoke(test_messages)
        print(f"  성공! result = {result}")
        print(f"  type  = {type(result)}")
        return True
    except Exception as e:
        print(f"  실패! error = {type(e).__name__}: {e}")
        return False


# ============================================================
# 테스트 3: 일반 invoke + 수동 JSON 파싱 (현재 방식, 성공 예상)
# ============================================================
def test_manual_parsing():
    print("\n" + "=" * 60)
    print("테스트 3: 일반 invoke + 수동 JSON 파싱 (현재 우회 방식)")
    print("=" * 60)
    try:
        messages_with_format = [
            {"role": "system", "content": (
                "You are a router. Route user requests to the correct agent.\n"
                "Respond with ONLY a raw JSON object:\n"
                '{"next": "yield_agent"|"wads_agent"|"map_agent"|"FINISH", '
                '"lotcd": "<product code>", "message": "<message>"}'
            )},
            {"role": "user", "content": "4SS 수율 보여줘"},
        ]
        response = llm.invoke(messages_with_format)
        raw_text = response.content.strip()
        print(f"  LLM 원본 응답: {raw_text[:300]}")

        # JSON 추출
        json_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            result = RouteResponse(**data)
            print(f"  파싱 성공! result = {result}")
            return True
        else:
            print(f"  JSON을 찾을 수 없음")
            return False
    except Exception as e:
        print(f"  실패! error = {type(e).__name__}: {e}")
        return False


# ============================================================
# 테스트 4: bind_tools (function calling 자체 테스트)
# ============================================================
def test_bind_tools():
    print("\n" + "=" * 60)
    print("테스트 4: bind_tools (function calling 지원 확인)")
    print("=" * 60)
    try:
        from langchain_core.tools import tool

        @tool
        def route_decision(next: str, lotcd: str = "", message: str = "") -> str:
            """Route to the next agent."""
            return f"Routed to {next}"

        llm_with_tools = llm.bind_tools([route_decision])
        result = llm_with_tools.invoke(test_messages)
        print(f"  응답 content: {result.content[:200] if result.content else '(empty)'}")
        print(f"  tool_calls: {result.tool_calls}")
        return bool(result.tool_calls)
    except Exception as e:
        print(f"  실패! error = {type(e).__name__}: {e}")
        return False


# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    print(f"모델: {os.getenv('DEFAULT_MODEL', 'gpt-oss-120b')}")
    print(f"Base URL: {os.getenv('OPENROUTER_BASE_URL', '(not set)')}")
    print()

    results = {}
    results["with_structured_output"] = test_structured_output()
    results["json_mode"] = test_json_mode()
    results["manual_parsing"] = test_manual_parsing()
    results["bind_tools"] = test_bind_tools()

    print("\n" + "=" * 60)
    print("결과 요약")
    print("=" * 60)
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}")
