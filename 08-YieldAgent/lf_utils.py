"""Langfuse 공통 유틸리티 — 모든 에이전트에서 공유"""
from langfuse import get_client
from langfuse.langchain import CallbackHandler as _LFHandler

from local_trace import llm_trace_handler


def lf_callbacks() -> list:
    """모델 invoke에 붙일 LangChain 콜백 목록.

    - local LLM I/O 핸들러는 항상 포함 (verbose일 때만 실제 출력; 터미널 트리에
      멀티턴 입력·AI content를 렌더).
    - Langfuse 핸들러는 @observe 컨텍스트(get_current_trace_id 존재) 안에서만 추가."""
    callbacks: list = [llm_trace_handler()]
    trace_id = get_client().get_current_trace_id()
    if trace_id:
        lf = get_client()
        callbacks.append(_LFHandler(trace_context={
            "trace_id": trace_id,
            "parent_span_id": lf.get_current_observation_id(),
        }))
    return callbacks
