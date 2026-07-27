"""공유 Python 런타임에서 세션 코드를 실행하는 REPL agent 도구."""

from __future__ import annotations

import json
from typing import Annotated

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_stream_writer

from .runtime import runtime
from .session_store import mark_runtime_lost


@tool
def run_python(
    code: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    config: RunnableConfig,
) -> str:
    """세션에 미리 로드된 데이터(`df`) 위에서 Python 코드를 실행.

    사용 가능한 이름:
      - pd, np, px (plotly.express), go (plotly.graph_objects), sm (statsmodels.api), scipy
      - df: 세션 시작 시점에 LOTCD/기간/fail_name 으로 적재된 pandas DataFrame
      - query: {"lotcd","start","end","fail_name"} dict (세션 메타)
      - emit_plot(fig): 이 턴에 주입되는 헬퍼. fig 는 plotly Figure. 프론트로 바로 전달됨.

    같은 세션 안에서 이 툴을 여러 번 호출해도 `df` 와 이전에 만든 변수(`summary` 등)가 유지된다.
    결과는 JSON 문자열이다. `status`, `stdout`, `stderr`, `execution_time_ms`, `error`,
    `stdout_truncated`, `stderr_truncated` 필드를 포함한다. `error` 는 실패 코드, 예외 이름,
    안전한 공개 메시지를 담고 성공 시 null 이다. truncation 필드가 true 면 해당 출력은
    런타임 한도에서 잘린 것이다. 마지막 expression 은 자동 출력되지 않으므로 필요하면 `print(...)`.

    주의: `exec()` 기반, 샌드박스 아님. 내부망/단일 사용자 환경 전제.
    """
    configurable = (config or {}).get("configurable") or {}
    session_id = configurable.get("thread_id", "")
    run_id = configurable.get("run_id", "")
    result = runtime.execute(
        session_id=session_id,
        run_id=run_id,
        code=code,
        timeout_seconds=60,
    )

    if result.plots:
        get_stream_writer()({
            "kind": "artifacts",
            "tool_call_id": tool_call_id,
            "artifacts": [plot.model_dump() for plot in result.plots],
        })

    if result.status in {"timeout", "cancelled", "runtime_lost"}:
        mark_runtime_lost(session_id, run_id)

    return json.dumps(result.to_tool_payload(), ensure_ascii=False)
