"""
Structured Output vs Raw JSON 파싱 비교 테스트
==============================================

OpenRouter 프록시를 통해 gpt-oss-120b를 사용할 때,
with_structured_output()이 실패하는 이유와 현재 raw JSON 방식의 동작을 비교합니다.

사용법:
    python test_structured_vs_raw.py
"""

import json
import os
import re
from typing import Literal

from dotenv import load_dotenv
from json_repair import repair_json
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv(override=True)


# ── 1. Pydantic 스키마 (supervisor.py의 RouteResponse 간소화 버전) ──────

class RouteResponse(BaseModel):
    """Supervisor의 라우팅 결정"""
    next: Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "FINISH"] = Field(
        description="다음에 실행할 에이전트"
    )
    lotcd: str = Field(default="", description="3~4자리 제품코드 (예: 4SS, 5NA)")
    ref_date: str = Field(default="", description="기준날짜 YYYYMMDD")
    filter_params: list[str] = Field(default=[], description="파라미터 필터 (예: ['VTH'])")
    unit: str = Field(default="weekly", description='"weekly" | "monthly" | "daily"')
    periods: int = Field(default=0, description="조회 기간 수")
    message: str = Field(default="", description="사용자에게 전달할 한국어 메시지")


# ── 2. LLM 초기화 ──────────────────────────────────────────────

def get_llm():
    return ChatOpenAI(
        model=os.getenv("DEFAULT_MODEL", "gpt-oss-120b"),
        base_url=os.getenv("OPENROUTER_BASE_URL"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
    )


# ── 테스트 입력 ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are a routing supervisor.
Respond with a JSON object matching this schema:
{
  "next": "yield_agent" | "wads_agent" | "map_agent" | "fail_history_agent" | "FINISH",
  "lotcd": "<product code or empty>",
  "ref_date": "<YYYYMMDD or empty>",
  "filter_params": [],
  "unit": "weekly",
  "periods": 0,
  "message": "<Korean message>"
}
Output ONLY the JSON. No markdown, no explanation."""

TEST_QUERY = "오늘 4SS 수율 VTH만 보여줘"


# ═══════════════════════════════════════════════════════════════
# 방법 A: with_structured_output() — OpenRouter에서 실패하는 방식
# ═══════════════════════════════════════════════════════════════

def test_structured_output():
    """
    LangChain의 with_structured_output()은 내부적으로 OpenAI API의
    response_format 또는 function_calling 파라미터를 사용합니다.

    ── OpenAI 직접 연결 시 (정상 동작) ──
    POST https://api.openai.com/v1/chat/completions
    {
        "model": "gpt-4o",
        "messages": [...],
        "response_format": {           ← 이 파라미터가 핵심
            "type": "json_schema",
            "json_schema": {
                "name": "RouteResponse",
                "strict": true,
                "schema": {
                    "type": "object",
                    "properties": {
                        "next": {"type": "string", "enum": [...]},
                        "lotcd": {"type": "string"},
                        ...
                    },
                    "required": ["next", "message"]
                }
            }
        }
    }

    ── OpenRouter 프록시 경유 시 (실패) ──
    POST https://<openrouter-base>/v1/chat/completions
    {
        "model": "gpt-oss-120b",
        "messages": [...],
        "response_format": {           ← OpenRouter가 이걸 무시하거나 변환 실패
            "type": "json_schema",
            "json_schema": { ... }
        }
    }

    OpenRouter가 response_format을 제대로 전달하지 못하면:
    - LLM이 자유 형식 텍스트를 반환
    - LangChain이 Pydantic 파싱 시도 → ValidationError
    - except절에서 fallback "요청을 이해하지 못했습니다" 반환
    """
    print("=" * 60)
    print("방법 A: with_structured_output()")
    print("=" * 60)

    llm = get_llm()
    structured_llm = llm.with_structured_output(RouteResponse)

    try:
        result = structured_llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": TEST_QUERY},
        ])
        print(f"✅ 성공: {result}")
        print(f"   next={result.next}, lotcd={result.lotcd}")
        return result
    except Exception as e:
        print(f"❌ 실패: {type(e).__name__}: {e}")
        print()
        print("   이것이 supervisor.py 주석에서 말하는 버그입니다.")
        print("   OpenRouter가 response_format/function_calling을")
        print("   gpt-oss-120b에 제대로 전달하지 못해서 발생합니다.")
        return None


# ═══════════════════════════════════════════════════════════════
# 방법 B: 현재 사용 중인 Raw JSON 파싱 방식
# ═══════════════════════════════════════════════════════════════

def test_raw_json_parsing():
    """
    현재 supervisor_node가 사용하는 방식:

    1) LLM에게 "JSON만 출력해" 라고 프롬프트로 지시
    2) LLM이 자유 텍스트로 JSON 반환 (가끔 <think> 태그, 마크다운 포함)
    3) 정규식으로 JSON 블록 추출
    4) json_repair로 malformed JSON 복구
    5) RouteResponse(**data)로 Pydantic 검증

    ── 실제 LLM 응답 예시들 ──

    [정상 케이스]
    <think>4SS 수율 VTH 요청이므로 yield_agent로 라우팅</think>
    {"next": "yield_agent", "lotcd": "4SS", "ref_date": "20260401",
     "filter_params": ["VTH"], "unit": "weekly", "periods": 0,
     "message": "4SS VTH 수율 데이터를 조회하겠습니다."}

    [마크다운 래핑 케이스]
    <think>수율 조회 요청</think>
    ```json
    {"next": "yield_agent", "lotcd": "4SS", ...}
    ```

    [malformed JSON 케이스 — trailing comma]
    {"next": "yield_agent", "lotcd": "4SS", "filter_params": ["VTH"],}
                                                                    ^ 이거

    [think 안에 JSON이 섞인 케이스]
    <think>{"next": "yield_agent"} 이건 아니고... 수율 요청이니까</think>
    {"next": "yield_agent", "lotcd": "4SS", ...}
    """
    print("=" * 60)
    print("방법 B: Raw JSON 파싱 (현재 사용 중)")
    print("=" * 60)

    llm = get_llm()

    # Step 1: LLM 호출 (스트리밍)
    raw_text = ""
    for chunk in llm.stream(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": TEST_QUERY},
        ],
        max_tokens=2048,
    ):
        token = chunk.content or ""
        raw_text += token

    raw_text = raw_text.strip()
    print(f"\n[LLM raw 응답]\n{raw_text}\n")

    # Step 2: <think> 태그 제거
    clean_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    clean_text = re.sub(r"<think>.*", "", clean_text, flags=re.DOTALL).strip()
    print(f"[think 제거 후]\n{clean_text}\n")

    # Step 3: JSON 블록 추출 (```json ... ``` 우선, 없으면 { ... })
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\{.*\})", clean_text, re.DOTALL)
    if not json_match:
        # clean_text에서 못 찾으면 raw_text에서 재시도
        json_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)

    if not json_match:
        print("❌ JSON을 찾을 수 없습니다")
        return None

    json_str = json_match.group(1)
    print(f"[추출된 JSON]\n{json_str}\n")

    # Step 4: json_repair로 malformed JSON 복구 + 파싱
    repaired = repair_json(json_str)
    data = json.loads(repaired)
    print(f"[파싱된 dict]\n{json.dumps(data, ensure_ascii=False, indent=2)}\n")

    # Step 5: Pydantic 검증
    decision = RouteResponse(**data)
    print(f"[Pydantic 검증 완료]")
    print(f"  next          = {decision.next}")
    print(f"  lotcd         = {decision.lotcd}")
    print(f"  ref_date      = {decision.ref_date}")
    print(f"  filter_params = {decision.filter_params}")
    print(f"  unit          = {decision.unit}")
    print(f"  periods       = {decision.periods}")
    print(f"  message       = {decision.message}")

    return decision


# ═══════════════════════════════════════════════════════════════
# 방법 C: OpenRouter가 지원하는 경우의 대안 (참고용)
# ═══════════════════════════════════════════════════════════════

def test_openai_json_mode():
    """
    OpenRouter 일부 모델은 response_format={"type": "json_object"}를 지원합니다.
    (json_schema는 안 되지만 json_object는 되는 경우)

    이 경우 LLM이 반드시 JSON을 반환하도록 강제할 수 있지만,
    스키마 검증은 여전히 클라이언트에서 해야 합니다.
    """
    print("=" * 60)
    print("방법 C: json_object mode (OpenRouter 부분 지원)")
    print("=" * 60)

    llm = get_llm()

    try:
        # model_kwargs로 response_format 직접 전달
        response = llm.invoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": TEST_QUERY},
            ],
            max_tokens=2048,
            response_format={"type": "json_object"},  # json_schema가 아닌 json_object
        )

        raw_text = response.content.strip()
        print(f"\n[LLM 응답]\n{raw_text}\n")

        data = json.loads(raw_text)
        decision = RouteResponse(**data)
        print(f"✅ 성공: next={decision.next}, lotcd={decision.lotcd}")
        return decision
    except Exception as e:
        print(f"❌ 실패: {type(e).__name__}: {e}")
        print("   json_object mode도 이 모델/프록시에서 지원되지 않을 수 있습니다.")
        return None


# ═══════════════════════════════════════════════════════════════
# Edge Case 테스트: json_repair가 실제로 살려주는 케이스들
# ═══════════════════════════════════════════════════════════════

def test_json_repair_cases():
    """json_repair가 복구하는 실제 malformed JSON 케이스들"""
    print("=" * 60)
    print("json_repair 복구 테스트")
    print("=" * 60)

    cases = [
        # Case 1: trailing comma
        (
            "trailing comma",
            '{"next": "yield_agent", "lotcd": "4SS", "filter_params": ["VTH"],}',
        ),
        # Case 2: 작은따옴표
        (
            "single quotes",
            "{'next': 'yield_agent', 'lotcd': '4SS'}",
        ),
        # Case 3: 따옴표 없는 key
        (
            "unquoted keys",
            '{next: "yield_agent", lotcd: "4SS"}',
        ),
        # Case 4: 잘린 JSON (LLM max_tokens 초과 시)
        (
            "truncated JSON",
            '{"next": "yield_agent", "lotcd": "4SS", "message": "조회하겠',
        ),
        # Case 5: 앞뒤에 텍스트가 붙은 경우
        (
            "surrounding text",
            'Here is the result: {"next": "yield_agent", "lotcd": "4SS"} hope this helps',
        ),
    ]

    for name, bad_json in cases:
        print(f"\n  [{name}]")
        print(f"  입력: {bad_json[:80]}...")
        try:
            # 정규식으로 JSON 추출
            match = re.search(r"(\{.*\})", bad_json, re.DOTALL)
            extracted = match.group(1) if match else bad_json

            repaired = repair_json(extracted)
            data = json.loads(repaired)
            decision = RouteResponse(**data)
            print(f"  ✅ 복구 성공: next={decision.next}, lotcd={decision.lotcd}")
        except Exception as e:
            print(f"  ❌ 복구 실패: {e}")


# ═══════════════════════════════════════════════════════════════
# 비교 요약
# ═══════════════════════════════════════════════════════════════

def print_comparison():
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║                   Structured Output vs Raw JSON 비교                     ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  방법 A: with_structured_output(RouteResponse)                           ║
║  ─────────────────────────────────────────────                           ║
║  • LangChain이 response_format.json_schema를 API에 전달                  ║
║  • LLM이 스키마에 맞는 JSON만 생성하도록 강제                              ║
║  • 파싱/검증이 자동 → 코드가 깔끔                                         ║
║  • ⚠️ OpenRouter 프록시가 response_format을 gpt-oss-120b에 전달 실패     ║
║  • ⚠️ 스트리밍 + <think> 태그 파싱 불가                                   ║
║                                                                           ║
║  방법 B: Raw JSON + regex + json_repair (현재 사용 중)                    ║
║  ──────────────────────────────────────────────────                       ║
║  • 프롬프트로 "JSON만 출력해" 지시                                         ║
║  • stream()으로 토큰 받으면서 <think> 실시간 분리                          ║
║  • regex로 JSON 추출 → json_repair → Pydantic 검증                       ║
║  • ✅ OpenRouter/어떤 프록시든 동작                                        ║
║  • ✅ 스트리밍 + thinking 토큰 지원                                        ║
║  • ⚠️ LLM이 JSON 외 텍스트를 섞어 출력할 수 있음 (regex로 방어)           ║
║                                                                           ║
║  방법 C: response_format={"type": "json_object"}                         ║
║  ────────────────────────────────────────────────                         ║
║  • 스키마 강제는 아니지만 JSON 출력은 보장                                 ║
║  • OpenRouter 일부 모델에서 지원                                           ║
║  • 스키마 검증은 클라이언트에서 별도 필요                                   ║
║  • ⚠️ gpt-oss-120b 지원 여부 확인 필요                                    ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
""")


# ── 메인 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print_comparison()

    # json_repair 테스트 (LLM 호출 불필요)
    test_json_repair_cases()

    print("\n" + "─" * 60)
    print("아래 테스트는 LLM 호출이 필요합니다.")
    print("OPENROUTER_BASE_URL과 OPENROUTER_API_KEY가 .env에 설정되어야 합니다.")
    print("─" * 60)

    run_llm_tests = input("\nLLM 호출 테스트를 실행하시겠습니까? (y/N): ").strip().lower()
    if run_llm_tests == "y":
        print()
        test_structured_output()
        print()
        test_raw_json_parsing()
        print()
        test_openai_json_mode()
