"""REPL 검증 agent 의 단일 tool: run_python.

이전에는 `fetch_data` 와 `run_python` 두 개였지만, 입력 계약이 바뀌어 **데이터는 세션 시작 시점에
이미 로드** 되어 있다 (session_store.create_session). 따라서 agent 는 데이터를 재조회하지 않고,
주어진 df 위에서 여러 검증을 반복 수행한다.

run_python 은 `config["configurable"]["thread_id"]` 로 현재 세션의 네임스페이스를 찾아 그 안에서
exec 한다. 같은 세션 내에서 변수는 턴 간 유지된다 (exec 는 패스된 globals dict 를 그대로 변형).
그림은 `emit_plot(fig)` 로 방출하며 LangGraph custom stream writer 로 전달된다.

plan 파일 `~/.claude/plans/reactive-shimmying-lake.md` 의 "코드 설계" 섹션과 짝.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from .session_store import get_namespace


@tool
def run_python(code: str, config: RunnableConfig) -> str:
    """세션에 미리 로드된 데이터(`df`) 위에서 Python 코드를 실행.

    사용 가능한 이름:
      - pd, np, px (plotly.express), go (plotly.graph_objects), sm (statsmodels.api), scipy
      - df: 세션 시작 시점에 LOTCD/기간/fail_name 으로 적재된 pandas DataFrame
      - query: {"lotcd","start","end","fail_name"} dict (세션 메타)
      - emit_plot(fig): 이 턴에 주입되는 헬퍼. fig 는 plotly Figure. 프론트로 바로 전달됨.

    같은 세션 안에서 이 툴을 여러 번 호출해도 `df` 와 이전에 만든 변수(`summary` 등)가 유지된다.
    stdout 만 결과로 돌려준다 — 마지막 expression 자동 출력 없음. 필요하면 `print(...)`.

    주의: `exec()` 기반, 샌드박스 아님. 내부망/단일 사용자 환경 전제.
    """
    configurable = (config or {}).get("configurable") or {}
    session_id = configurable.get("thread_id", "")
    ns = get_namespace(session_id)
    if ns is None:
        return (
            f"ERROR: session '{session_id}' 를 찾을 수 없음. "
            "POST /repl/session 으로 LOTCD/기간/fail_name 을 먼저 등록해 세션을 시작해야 합니다."
        )

    writer = get_stream_writer()

    def emit_plot(fig: Any) -> None:
        writer({"kind": "plot", "spec": fig.to_json()})

    ns["emit_plot"] = emit_plot

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, ns)  # noqa: S102 — 검증용 REPL, 내부망 전용
    except Exception as e:
        out = buf.getvalue()
        return (out + "\n" if out else "") + f"ERROR: {type(e).__name__}: {e}"

    return buf.getvalue() or "OK"
