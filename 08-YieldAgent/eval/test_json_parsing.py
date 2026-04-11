"""
JSON 파싱 견고성 테스트
======================
extract_json_from_llm()의 다양한 malformed 입력 처리를 검증.
LLM 호출 없이 결정론적으로 실행 → CI PR fast lane 적합.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 08-YieldAgent 모듈 경로 추가
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from common import extract_json_from_llm
from supervisor import RouteResponse


# ── 정상 케이스 ──────────────────────────────────────────────

class TestCleanJson:
    """깔끔한 JSON 입력이 정상 파싱되는지 검증."""

    def test_plain_json(self):
        raw = '{"next": "yield_agent", "lotcd": "4SS", "message": "조회합니다"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"
        assert result.lotcd == "4SS"

    def test_with_filter_params(self):
        raw = '{"next": "yield_agent", "lotcd": "4SS", "filter_params": ["VTH", "IDSAT"], "message": "조회"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.filter_params == ["VTH", "IDSAT"]

    def test_finish_route(self):
        raw = '{"next": "FINISH", "message": "범위 밖 질문입니다"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "FINISH"


# ── <think> 태그 처리 ────────────────────────────────────────

class TestThinkTagRemoval:
    """<think> 태그가 포함된 LLM 응답에서 JSON 추출."""

    def test_think_before_json(self):
        raw = '<think>수율 조회 요청이므로 yield_agent로 라우팅</think>{"next": "yield_agent", "lotcd": "4SS", "message": "ok"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_think_with_newlines(self):
        raw = """<think>
사용자가 4SS 수율을 요청했습니다.
yield_agent로 라우팅합니다.
</think>
{"next": "yield_agent", "lotcd": "4SS", "message": "조회"}"""
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_unclosed_think_tag(self):
        """닫는 태그 없는 <think> (LLM이 가끔 생성)."""
        raw = '<think>수율 요청\n{"next": "yield_agent", "lotcd": "4SS", "message": "m"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_think_containing_json_like_text(self):
        """<think> 안에 JSON 비슷한 텍스트가 있고 밖에 실제 JSON."""
        raw = '<think>{"next": "yield_agent"} 이건 아니고... 수율 요청</think>{"next": "wads_agent", "lotcd": "4SA", "message": "ok"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "wads_agent"
        assert result.lotcd == "4SA"


# ── Markdown 코드 펜스 ───────────────────────────────────────

class TestMarkdownFence:
    """```json ... ``` 로 감싸진 JSON 추출."""

    def test_json_fence(self):
        raw = '```json\n{"next": "map_agent", "map_lot_id": "4SS2DPD", "map_oper": "PT1H", "message": "맵"}\n```'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "map_agent"
        assert result.map_lot_id == "4SS2DPD"

    def test_plain_fence(self):
        raw = '```\n{"next": "yield_agent", "lotcd": "4SS", "message": "m"}\n```'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_think_plus_fence(self):
        raw = """<think>맵 요청</think>
```json
{"next": "map_agent", "map_lot_id": "4SS2DPD", "map_oper": "PT1C", "map_type": "cummap", "message": "맵 생성"}
```"""
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "map_agent"
        assert result.map_oper == "PT1C"
        assert result.map_type == "cummap"


# ── Malformed JSON (json_repair 복구) ────────────────────────

class TestMalformedJson:
    """json_repair가 복구해야 하는 케이스들."""

    def test_trailing_comma(self):
        raw = '{"next": "yield_agent", "lotcd": "4SS", "filter_params": ["VTH"], "message": "조회",}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"
        assert result.filter_params == ["VTH"]

    def test_single_quotes(self):
        raw = "{'next': 'yield_agent', 'lotcd': '4SS', 'message': '조회'}"
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_unquoted_keys(self):
        raw = '{next: "yield_agent", lotcd: "4SS", message: "ok"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"

    def test_truncated_json(self):
        """max_tokens 초과로 잘린 JSON — 닫는 }가 없으면 regex 매칭 실패.
        현재 extract_json_from_llm은 이 케이스를 복구하지 못함 (ValueError).
        이것은 알려진 한계이며, 실 운영에서는 max_tokens를 충분히 설정하여 방지."""
        raw = '{"next": "yield_agent", "lotcd": "4SS", "message": "조회하겠'
        with pytest.raises(ValueError, match="No JSON found"):
            extract_json_from_llm(raw, RouteResponse)


# ── 주변 텍스트가 포함된 경우 ────────────────────────────────

class TestSurroundingText:
    """JSON 앞뒤에 텍스트가 붙어있는 경우."""

    def test_text_before_json(self):
        raw = 'Here is the routing decision: {"next": "wads_agent", "lotcd": "4SS", "message": "열화 조회"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "wads_agent"

    def test_text_after_json(self):
        raw = '{"next": "yield_agent", "lotcd": "4SS", "message": "ok"} 이 결과를 확인해주세요.'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == "yield_agent"


# ── 에러 케이스 ──────────────────────────────────────────────

class TestErrorCases:
    """JSON을 찾을 수 없는 경우 ValueError 발생."""

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON found"):
            extract_json_from_llm("이것은 JSON이 아닙니다.", RouteResponse)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON found"):
            extract_json_from_llm("", RouteResponse)


# ── 모든 에이전트 라우팅 ─────────────────────────────────────

class TestAllAgentRoutes:
    """7가지 next 값이 모두 정상 파싱되는지 검증."""

    @pytest.mark.parametrize("agent", [
        "yield_agent", "wads_agent", "map_agent",
        "fail_history_agent", "lot_history_agent",
        "ppt_export", "FINISH",
    ])
    def test_route_value(self, agent):
        raw = f'{{"next": "{agent}", "message": "test"}}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.next == agent


# ── 복합 파라미터 파싱 ───────────────────────────────────────

class TestComplexParams:
    """다양한 필드 조합이 올바르게 파싱되는지."""

    def test_wads_params(self):
        raw = '{"next": "wads_agent", "lotcd": "4SS", "wads_start_tm": "2026-03-01", "wads_end_tm": "2026-03-31", "message": "WADS 조회"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.wads_start_tm == "2026-03-01"
        assert result.wads_end_tm == "2026-03-31"

    def test_map_params(self):
        raw = '{"next": "map_agent", "map_lot_ids": "4SS2DPD,4SSXCEW", "map_type": "cummap", "map_oper": "PT1H", "message": "맵"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.map_lot_ids == "4SS2DPD,4SSXCEW"
        assert result.map_type == "cummap"

    def test_yield_lot_comparison(self):
        raw = '{"next": "yield_agent", "lotcd": "4SS", "yield_lot_ids": "4SS2DPD,4SSXCEW", "message": "비교"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.yield_lot_ids == "4SS2DPD,4SSXCEW"

    def test_fail_history_params(self):
        raw = '{"next": "fail_history_agent", "lotcd": "4SS", "dh_fail_type": "TWT", "dh_cause_oper": "M0C ETCH", "message": "불량이력"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.dh_fail_type == "TWT"
        assert result.dh_cause_oper == "M0C ETCH"

    def test_lot_history_params(self):
        raw = '{"next": "lot_history_agent", "lh_lot_ids": "4SS2DPD,4SSXCEW", "message": "이력 조회"}'
        result = extract_json_from_llm(raw, RouteResponse)
        assert result.lh_lot_ids == "4SS2DPD,4SSXCEW"
