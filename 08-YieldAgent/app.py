"""
Yield Query Agent - Streamlit UI
=================================
자연어로 수율 데이터를 조회하고, WADS 열화 리포트까지 연계하는 에이전트 UI입니다.

실행: streamlit run 08-YieldAgent/app.py
(FastAPI 서버가 127.0.0.1:8000 에서 실행 중이어야 합니다)
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import time
from datetime import date

# ── 에이전트 모듈 임포트 ──────────────────────────────
import sys
from pathlib import Path

# 08-YieldAgent 폴더를 기준으로 프로젝트 루트에서 실행해도 동작하도록 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yield_query_agent import (
    yield_supervisor,
    langfuse_handler,
    PARA_COLUMNS,
    _iso_week_str,
)
from langchain_core.messages import HumanMessage


# ── 페이지 설정 ──────────────────────────────────────
st.set_page_config(
    page_title="Yield Query Agent",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Yield Query Agent")
st.caption(f"오늘: {date.today().strftime('%Y-%m-%d')}  |  자연어로 수율 데이터를 조회하세요")

# ── 사이드바 : 예시 버튼 ─────────────────────────────
with st.sidebar:
    st.header("예시 쿼리")
    examples = [
        "오늘 4SS 수율 알려줘",
        "저번주 수율 알려줘",
        "2주전 4SS 수율 보여줘",
        "이번주 4SS 데이터 조회해줘",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state["query_input"] = ex

    st.divider()
    st.markdown(
        "**사용법**\n"
        "1. FastAPI 서버 실행 확인\n"
        "2. 자연어로 쿼리 입력\n"
        "3. Supervisor가 시간/제품코드 파싱\n"
        "4. 최근 4주 데이터 테이블 출력\n"
        "5. '보여줘' 입력 시 WADS 열화 리포트 조회"
    )

# ── 세션 State 초기화 ────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# 대화 간 유지되는 State (lotcd 등)
if "persistent_state" not in st.session_state:
    st.session_state.persistent_state = {
        "lotcd": "",
        "ref_date": "",
        "weeks_data": [],
        "table_result": "",
        "analysis_result": "",
        "wads_end_tm": "",
        "wads_artifacts": [],
        "next_agent": "",
    }


# ── 히스토리 렌더링 헬퍼 ─────────────────────────────
def _render_entry(entry):
    """하나의 채팅 히스토리 항목을 렌더링"""
    if entry.get("error"):
        st.error(entry["error"])
        return

    st.info(entry.get("supervisor_msg", ""))

    # yield 결과
    if entry.get("df") is not None:
        st.dataframe(entry["df"], use_container_width=True, hide_index=True)
    if entry.get("analysis"):
        st.divider()
        st.markdown(entry["analysis"])
    if entry.get("suggestion"):
        st.markdown(f"> {entry['suggestion']}")

    # WADS 결과
    if entry.get("wads_answer"):
        st.markdown(entry["wads_answer"])
    if entry.get("wads_html"):
        components.html(entry["wads_html"], height=600, scrolling=True)


# ── 이전 대화 표시 ───────────────────────────────────
for entry in st.session_state.chat_history:
    with st.chat_message("user"):
        st.write(entry["query"])
    with st.chat_message("assistant"):
        _render_entry(entry)

# ── 입력 ─────────────────────────────────────────────
query = st.chat_input(
    "예: 오늘 4SS 수율 알려줘",
    key="chat_input",
)

# 사이드바 예시 버튼에서 온 값 적용
if "query_input" in st.session_state and st.session_state["query_input"]:
    query = st.session_state.pop("query_input")

if query:
    # 사용자 메시지 표시
    with st.chat_message("user"):
        st.write(query)

    # 에이전트 실행
    with st.chat_message("assistant"):
        # 이전 대화 맥락을 유지하기 위해 persistent_state 사용
        prev = st.session_state.persistent_state
        initial_state = {
            "messages": [HumanMessage(content=query)],
            "lotcd": prev.get("lotcd", ""),
            "ref_date": prev.get("ref_date", ""),
            "weeks_data": [],
            "table_result": "",
            "analysis_result": "",
            "wads_end_tm": "",
            "wads_artifacts": [],
            "next_agent": "",
        }

        # stream() 모드로 노드별 진행 상황 표시 + Langfuse 트레이싱
        try:
            with st.status("에이전트 처리 중...", expanded=True) as status:
                start_time = time.time()
                final_state = {}
                all_messages = []  # messages는 노드별로 분리되므로 별도 누적

                for step_output in yield_supervisor.stream(
                    initial_state,
                    config={"callbacks": [langfuse_handler]},
                ):
                    for node_name, node_state in step_output.items():
                        elapsed = time.time() - start_time
                        st.write(f"`[{elapsed:.1f}s]` **{node_name}** 노드 완료")
                        # messages 누적 (add_messages reducer 대신 수동 누적)
                        if "messages" in node_state:
                            all_messages.extend(node_state["messages"])
                        # 나머지 state 필드 업데이트
                        final_state.update(node_state)

                # 누적된 전체 messages로 덮어쓰기
                final_state["messages"] = all_messages

                total_time = time.time() - start_time
                status.update(
                    label=f"완료! (총 {total_time:.1f}초)",
                    state="complete",
                )

            # Langfuse 트레이스 비동기 전송
            langfuse_handler.flush()

        except Exception as e:
            st.error(f"에이전트 실행 오류: {e}")
            st.session_state.chat_history.append(
                {"query": query, "error": str(e)}
            )
            st.stop()

        # ── persistent state 업데이트 ────────────────
        ps = st.session_state.persistent_state
        if final_state.get("lotcd"):
            ps["lotcd"] = final_state["lotcd"]
        if final_state.get("ref_date"):
            ps["ref_date"] = final_state["ref_date"]

        # ── 결과 파싱 ────────────────────────────────
        lotcd = final_state.get("lotcd", "4SS")
        ref_date = final_state.get("ref_date", "")
        weeks_data = final_state.get("weeks_data", [])
        analysis_result = final_state.get("analysis_result", "")
        wads_artifacts = final_state.get("wads_artifacts", [])

        # supervisor 메시지 추출
        supervisor_msg = ""
        for msg in final_state.get("messages", []):
            if getattr(msg, "name", None) == "supervisor":
                supervisor_msg = msg.content
                break
        if not supervisor_msg:
            supervisor_msg = "처리 완료"

        # wads_agent 메시지 추출
        wads_answer = ""
        for msg in final_state.get("messages", []):
            if getattr(msg, "name", None) == "wads_agent":
                wads_answer = msg.content
                break

        st.info(f"🔍 {supervisor_msg}")

        # ── Yield 결과 표시 ──────────────────────────
        history_entry = {"query": query, "supervisor_msg": f"🔍 {supervisor_msg}"}

        if weeks_data:
            rows = []
            for wd in weeks_data:
                row = {
                    "WEEK": wd.get("week", "?"),
                    "LOT": wd.get("lotcount", "-"),
                    "WF": wd.get("wfCount", "-"),
                }
                for col in PARA_COLUMNS:
                    row[col] = wd.get(col, "-")
                rows.append(row)

            df = pd.DataFrame(rows)

            st.subheader(f"[{lotcd}] Weekly pt1h Metrics (최근 4주)")
            if ref_date:
                st.caption(f"기준 주차: {ref_date}")
            st.dataframe(df, use_container_width=True, hide_index=True)

            if analysis_result:
                st.divider()
                st.subheader("📋 주차별 비교 분석")
                st.markdown(analysis_result)

            # 첨언 표시
            suggestion = "오늘 검출된 열화 Parameter를 보여드릴까요?"
            st.markdown(f"> {suggestion}")

            history_entry["df"] = df
            history_entry["analysis"] = analysis_result
            history_entry["suggestion"] = suggestion

        # ── WADS 결과 표시 ───────────────────────────
        if wads_answer:
            st.markdown(wads_answer)
            history_entry["wads_answer"] = wads_answer

        if wads_artifacts:
            for artifact in wads_artifacts:
                html_data = artifact.get("data", "")
                if html_data:
                    components.html(html_data, height=600, scrolling=True)
                    history_entry["wads_html"] = html_data

        # ── 결과 없음 처리 ───────────────────────────
        if not weeks_data and not wads_answer and not wads_artifacts:
            st.warning("데이터를 가져올 수 없습니다. 서버 상태를 확인하세요.")
            history_entry["error"] = "데이터 없음"

        st.session_state.chat_history.append(history_entry)
