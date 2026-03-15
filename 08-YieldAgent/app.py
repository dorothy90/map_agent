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

AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8000")

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
    """하나의 채팅 히스토리 항목을 렌더링 (이벤트 기반 구조)"""
    if entry.get("error"):
        st.error(entry["error"])
        return

    for msg in entry.get("messages", []):
        agent = msg.get("agent", "")
        content = msg.get("content", "")
        if not content:
            continue
        if agent == "supervisor":
            st.info(f"🔍 {content}")
        else:
            st.markdown(content)

    for art in entry.get("artifacts", []):
        art_type = art.get("artifact_type", "html")
        data = art.get("data", "")
        if not data:
            continue
        if art_type == "html":
            components.html(data, height=600, scrolling=True)
        elif art_type == "image":
            st.image(data, caption=art.get("title", ""))
        elif art_type == "markdown":
            st.divider()
            st.markdown(data)

    if entry.get("suggestion"):
        st.markdown(f"> {entry['suggestion']}")


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

if "query_input" in st.session_state and st.session_state["query_input"]:
    query = st.session_state.pop("query_input")

if query:
    with st.chat_message("user"):
        st.write(query)

    with st.chat_message("assistant"):
        collected_messages: list[dict] = []
        collected_artifacts: list[dict] = []
        collected_suggestion = ""

        try:
            with st.status("에이전트 처리 중...", expanded=True) as status:
                # 토큰 스트리밍용 상태
                token_buffer = ""
                token_placeholder = None
                token_agent = None

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
                            etype = event.get("type", "")

                            if etype == "node_complete":
                                st.write(
                                    f"`[{event.get('elapsed', 0):.1f}s]` "
                                    f"**{event.get('node', '?')}** 노드 완료"
                                )

                            elif etype == "token":
                                agent = event.get("agent", "")
                                # 새 에이전트의 토큰이면 새 placeholder 생성
                                if agent != token_agent:
                                    token_buffer = ""
                                    token_agent = agent
                                    token_placeholder = st.empty()
                                elif token_placeholder is None:
                                    token_placeholder = st.empty()
                                token_buffer += event.get("content", "")
                                if agent == "supervisor":
                                    token_placeholder.info(f"🔍 {token_buffer}▍")
                                else:
                                    token_placeholder.markdown(token_buffer + "▍")

                            elif etype == "message":
                                agent = event.get("agent", "")
                                content = event.get("content", "")
                                # 토큰 스트리밍 중이던 placeholder를 최종 내용으로 교체
                                if token_placeholder is not None and agent == token_agent:
                                    if agent == "supervisor":
                                        token_placeholder.info(f"🔍 {content}")
                                    else:
                                        token_placeholder.markdown(content)
                                    token_placeholder = None
                                    token_agent = None
                                    token_buffer = ""
                                collected_messages.append({
                                    "agent": agent,
                                    "content": content,
                                })

                            elif etype == "artifact":
                                collected_artifacts.append({
                                    "artifact_type": event.get("artifact_type", "html"),
                                    "mime": event.get("mime", "text/html"),
                                    "title": event.get("title", ""),
                                    "agent": event.get("agent", ""),
                                    "data": event.get("data", ""),
                                })

                            elif etype == "suggestion":
                                collected_suggestion = event.get("content", "")

                            elif etype == "error":
                                raise RuntimeError(event.get("message", "알 수 없는 오류"))

                status.update(label="완료!", state="complete")

        except Exception as e:
            st.error(f"에이전트 실행 오류: {e}")
            st.session_state.chat_history.append({"query": query, "error": str(e)})
            st.stop()

        # ── 수집된 아티팩트/서제스천 렌더링 (메시지는 이미 스트리밍됨) ──
        for art in collected_artifacts:
            art_type = art.get("artifact_type", "html")
            data = art.get("data", "")
            if not data:
                continue
            if art_type == "html":
                components.html(data, height=600, scrolling=True)
            elif art_type == "image":
                st.image(data, caption=art.get("title", ""))
            elif art_type == "markdown":
                st.divider()
                st.markdown(data)

        if collected_suggestion:
            st.markdown(f"> {collected_suggestion}")

        # ── 결과 없음 처리 ───────────────────────────────
        if not collected_messages and not collected_artifacts:
            st.warning("데이터를 가져올 수 없습니다. 서버 상태를 확인하세요.")

        # ── 히스토리 저장 ─────────────────────────────────
        st.session_state.chat_history.append({
            "query": query,
            "messages": collected_messages,
            "artifacts": collected_artifacts,
            "suggestion": collected_suggestion,
        })
