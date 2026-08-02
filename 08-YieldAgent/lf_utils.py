"""Langfuse 공통 유틸리티 — 모든 에이전트에서 공유"""
import contextvars
from functools import wraps

from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler as _LFHandler

from local_trace import llm_trace_handler


_CAPTURE_DISABLED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "yield_lf_capture_disabled", default=False
)


def set_lf_capture_disabled(disabled: bool) -> contextvars.Token[bool]:
    """Disable payload-bearing callbacks for the current async execution context."""

    return _CAPTURE_DISABLED.set(bool(disabled))


def reset_lf_capture_disabled(token: contextvars.Token[bool]) -> None:
    _CAPTURE_DISABLED.reset(token)


def lf_capture_disabled() -> bool:
    return _CAPTURE_DISABLED.get()


def observe_with_privacy(**options):
    """Bypass the observation wrapper when the current item is private."""

    def decorate(function):
        observed = observe(**options)(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            if _CAPTURE_DISABLED.get():
                return function(*args, **kwargs)
            return observed(*args, **kwargs)

        return wrapped

    return decorate


def update_lf_span_output(output: dict) -> None:
    """Update a span only when the current request permits payload capture."""

    if _CAPTURE_DISABLED.get():
        return
    get_client().update_current_span(output=output)


def lf_callbacks() -> list:
    """모델 invoke에 붙일 LangChain 콜백 목록.

    - local LLM I/O 핸들러는 일반 요청에서 포함 (verbose일 때만 실제 출력; 터미널
      트리에 멀티턴 입력·AI content를 렌더).
    - Langfuse 핸들러는 @observe 컨텍스트(get_current_trace_id 존재) 안에서만 추가."""
    if _CAPTURE_DISABLED.get():
        return []

    callbacks: list = [llm_trace_handler()]
    trace_id = get_client().get_current_trace_id()
    if trace_id:
        lf = get_client()
        callbacks.append(_LFHandler(trace_context={
            "trace_id": trace_id,
            "parent_span_id": lf.get_current_observation_id(),
        }))
    return callbacks
