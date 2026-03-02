"""
Yield Query Agent - Streamlit UI
=================================
자연어로 수율 데이터를 조회하고, WADS 열화 리포트까지 연계하는 에이전트 UI입니다.

실행:
  터미널 1: uvicorn 08-YieldAgent.agent_server:app --port 8001
  터미널 2: streamlit run 08-YieldAgent/app.py
"""

import json
import os
import uuid

import httpx
import streamlit as st
import streamlit.components.v1 as components
from datetime import date

AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8001")

# ── 페이지 설정 ──────────────────────────────────────────
st.set_page_config(
    page_title="Yield Query Agent",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Yield Query Agent")
st.caption(
    f"오늘: {date.today().strftime('%Y-%m-%d')}  |  자연어로 수율 데이터를 조회하세요"
)

# ── 사이드바 : 예시 버튼 ─────────────────────────────────
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
        "1. Agent 서버 실행 확인 (port 8001)\n"
        "2. 자연어로 쿼리 입력\n"
        "3. Supervisor가 시간/제품코드 파싱\n"
        "4. 최근 4주 데이터 테이블 출력\n"
        "5. '보여줘' 입력 시 WADS 열화 리포트 조회"
    )

# ── 세션 State 초기화 ────────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())


# ── 히스토리 렌더링 헬퍼 ────────────────────────────────
def _render_entry(entry):
    """하나의 채팅 히스토리 항목을 렌더링"""
    if entry.get("error"):
        st.error(entry["error"])
        return

    st.info(entry.get("supervisor_msg", ""))

    if entry.get("yield_html"):
        components.html(entry["yield_html"], height=500, scrolling=True)
    if entry.get("analysis"):
        st.divider()
        st.markdown(entry["analysis"])
    if entry.get("agent_suggestion"):
        st.markdown(f"> {entry['agent_suggestion']}")

    if entry.get("wads_answer"):
        st.markdown(entry["wads_answer"])
    if entry.get("wads_html"):
        components.html(entry["wads_html"], height=600, scrolling=True)

    if entry.get("map_answer"):
        st.markdown(entry["map_answer"])
    if entry.get("map_html"):
        components.html(entry["map_html"], height=700, scrolling=True)


# ── 이전 대화 표시 ───────────────────────────────────────
for entry in st.session_state.chat_history:
    with st.chat_message("user"):
        st.write(entry["query"])
    with st.chat_message("assistant"):
        _render_entry(entry)

# ── 입력 ─────────────────────────────────────────────────
query = st.chat_input(
    "예: 오늘 4SS 수율 알려줘",
    key="chat_input",
)

# 사이드바 예시 버튼에서 온 값 적용
if "query_input" in st.session_state and st.session_state["query_input"]:
    query = st.session_state.pop("query_input")

if query:
    with st.chat_message("user"):
        st.write(query)

    with st.chat_message("assistant"):
        try:
            with st.status("에이전트 처리 중...", expanded=True) as status:
                final_data: dict = {}

                with httpx.Client(timeout=120) as client:
                    with client.stream(
                        "POST",
                        f"{AGENT_BASE_URL}/chat/stream",
                        json={
                            "query": query,
                            "session_id": st.session_state.session_id,
                        },
                    ) as resp:
                        resp.raise_for_status()
                        for line in resp.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            event = json.loads(line[6:])
                            if event["type"] == "node":
                                st.write(
                                    f"`[{event['elapsed']:.1f}s]` **{event['node']}** 노드 완료"
                                )
                            elif event["type"] == "result":
                                final_data = event
                            elif event["type"] == "error":
                                raise RuntimeError(event["message"])

                status.update(label="완료!", state="complete")

        except Exception as e:
            st.error(f"에이전트 실행 오류: {e}")
            st.session_state.chat_history.append({"query": query, "error": str(e)})
            st.stop()

        supervisor_msg = final_data.get("supervisor_msg", "처리 완료")
        st.info(f"🔍 {supervisor_msg}")

        history_entry = {"query": query, "supervisor_msg": f"🔍 {supervisor_msg}"}

        # ── Yield 결과 표시 ──────────────────────────────
        if final_data.get("yield_html"):
            components.html(final_data["yield_html"], height=500, scrolling=True)
            history_entry["yield_html"] = final_data["yield_html"]

        if final_data.get("analysis"):
            st.divider()
            st.subheader("📋 주차별 비교 분석")
            st.markdown(final_data["analysis"])
            history_entry["analysis"] = final_data["analysis"]

        if final_data.get("agent_suggestion"):
            st.markdown(f"> {final_data['agent_suggestion']}")
            history_entry["agent_suggestion"] = final_data["agent_suggestion"]

        # ── WADS 결과 표시 ───────────────────────────────
        if final_data.get("wads_answer"):
            st.markdown(final_data["wads_answer"])
            history_entry["wads_answer"] = final_data["wads_answer"]

        if final_data.get("wads_html"):
            components.html(final_data["wads_html"], height=600, scrolling=True)
            history_entry["wads_html"] = final_data["wads_html"]

        # ── Map 결과 표시 ────────────────────────────────
        if final_data.get("map_answer"):
            st.markdown(final_data["map_answer"])
            history_entry["map_answer"] = final_data["map_answer"]

        if final_data.get("map_html"):
            components.html(final_data["map_html"], height=700, scrolling=True)
            history_entry["map_html"] = final_data["map_html"]

        # ── 결과 없음 처리 ───────────────────────────────
        if (
            not final_data.get("has_weeks_data")
            and not final_data.get("wads_answer")
            and not final_data.get("wads_html")
            and not final_data.get("map_html")
        ):
            st.warning("데이터를 가져올 수 없습니다. 서버 상태를 확인하세요.")
            history_entry["error"] = "데이터 없음"

        st.session_state.chat_history.append(history_entry)
