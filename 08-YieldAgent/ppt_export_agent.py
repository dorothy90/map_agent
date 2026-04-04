"""
PPT Export Agent Node
=====================
LangGraph 노드: state에 누적된 아티팩트를 PPTX로 변환.
supervisor가 "ppt_export" 라우팅 시 실행됩니다.

artifact의 data 필드가 file:// 참조일 경우 파일을 읽어서 처리하고,
인라인 HTML/base64인 경우 그대로 처리합니다.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import stream_event, timed
from models import StatusEvent

logger = logging.getLogger("yield_agent.ppt_export")


def _resolve_artifact_data(artifacts: list[dict]) -> list[dict]:
    """artifact의 file:// 참조를 실제 파일 내용으로 치환.

    agent_server.py가 SSE 전송 시 file://를 읽어서 inline화하지만,
    ppt_export는 SSE 이전에 실행되므로 직접 읽어야 합니다.
    파일을 읽은 후에는 삭제하지 않습니다 (SSE 전송에서 삭제됨).
    """
    resolved = []
    for art in artifacts:
        data = art.get("data", "")
        if data.startswith("file://"):
            file_path = data[7:]
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = f.read()
            except FileNotFoundError:
                logger.warning("[PPT Export] 파일 없음: %s", file_path)
                continue
        if data:
            resolved.append({**art, "data": data})
    return resolved


@observe(name="ppt_export_node")
@timed
def ppt_export_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """PPT Export 노드: state의 전체 아티팩트를 PPTX로 변환."""
    stream_event("status", StatusEvent(message="PPT 리포트 생성 중...", node="ppt_export"))

    # artifact file:// 참조 해소
    resolved_state = dict(state)
    for key in ("yield_artifacts", "wads_artifacts", "map_artifacts", "fail_history_artifacts"):
        raw = state.get(key, [])
        if raw:
            resolved_state[key] = _resolve_artifact_data(raw)

    # PPT 생성
    try:
        from ppt_builder import YieldReportPPTBuilder

        builder = YieldReportPPTBuilder()
        ppt_mode = state.get("ppt_mode", "full")
        if ppt_mode == "compact":
            pptx_bytes, fpath = builder.build_compact(resolved_state)
        else:
            pptx_bytes, fpath = builder.build(resolved_state)

        lotcd = state.get("lotcd", "Unknown")
        ref_date = state.get("ref_date", "")
        fname = os.path.basename(fpath)

        result_msg = (
            f"📊 **{lotcd} 수율 분석 리포트**가 PPT로 생성되었습니다.\n"
            f"파일: `{fname}` ({len(pptx_bytes) / 1024:.0f} KB)"
        )

        # pptx artifact로 전달 — agent_server.py에서 다운로드 링크로 변환
        ppt_artifact = {
            "type": "pptx",
            "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "data": f"file://{fpath}",
            "title": f"yield_report_{lotcd}_{ref_date}",
            "agent": "ppt_export",
        }

        return {
            "messages": [AIMessage(content=result_msg, name="ppt_export")],
            "ppt_artifacts": [ppt_artifact],
        }

    except Exception as e:
        logger.error("[PPT Export] 생성 실패: %s", e, exc_info=True)
        return {
            "messages": [AIMessage(
                content=f"PPT 생성 중 오류가 발생했습니다: {e}",
                name="ppt_export",
            )],
            "ppt_artifacts": [],
        }
