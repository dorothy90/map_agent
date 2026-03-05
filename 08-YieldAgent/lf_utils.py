"""Langfuse 공통 유틸리티 — 모든 에이전트에서 공유"""
from langfuse import get_client
from langfuse.langchain import CallbackHandler as _LFHandler


def lf_callbacks() -> list:
    """현재 Langfuse span에 연결된 LangChain CallbackHandler 반환.
    @observe 컨텍스트 안에서 호출해야 generation이 child span으로 기록됨."""
    lf = get_client()
    trace_id = lf.get_current_trace_id()
    if not trace_id:
        return []
    return [_LFHandler(trace_context={
        "trace_id": trace_id,
        "parent_span_id": lf.get_current_observation_id(),
    })]
