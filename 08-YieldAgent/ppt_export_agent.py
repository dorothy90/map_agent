"""
PPT Export Agent Node
=====================
LangGraph 노드: state에 누적된 아티팩트를 PPTX로 변환.
supervisor가 "ppt_export" 라우팅 시 실행됩니다.

ArtifactStore 참조만 검증해 읽고, 생성한 PPTX도 같은 저장소에 기록합니다.
"""
from __future__ import annotations

import logging
import base64
from typing import Any, Dict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from artifact_context import active_artifact_store, save_artifact
from artifact_store import ArtifactRef, ArtifactStore
from common import stream_event, timed
from models import StatusEvent

logger = logging.getLogger("yield_agent.ppt_export")


def _resolve_artifact_data(
    store: ArtifactStore, artifacts: list[dict]
) -> list[dict]:
    """Validate stored references and expose their content only to the PPT builder."""
    resolved = []
    for art in artifacts:
        if "data" in art:
            raise ValueError("PPT source artifacts must be reference-only")
        try:
            ref = ArtifactRef.model_validate(art.get("artifact_ref"))
        except Exception as exc:
            raise ValueError("PPT source artifact_ref is invalid") from exc
        with store.open(ref) as source:
            content = source.read()
        if ref.mime.startswith("text/") or ref.mime in {
            "application/json",
            "application/xhtml+xml",
        }:
            data = content.decode("utf-8")
        elif ref.mime.startswith("image/"):
            image_data = f"data:{ref.mime};base64,{base64.b64encode(content).decode('ascii')}"
            data = f'<img src="{image_data}">'
        else:
            raise ValueError(f"unsupported PPT source MIME: {ref.mime}")
        resolved.append({**art, "data": data})
    return resolved


@observe(name="ppt_export_node")
@timed
def ppt_export_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """PPT Export 노드: state의 전체 아티팩트를 PPTX로 변환."""
    stream_event("status", StatusEvent(message="PPT 리포트 생성 중...", node="ppt_export"))

    store = active_artifact_store()
    resolved_state = dict(state)
    for key in (
        "yield_artifacts",
        "wads_artifacts",
        "map_artifacts",
        "fail_history_artifacts",
        "lot_history_artifacts",
        "relation_tree_artifacts",
        "mining_artifacts",
        "wt_resp_artifacts",
    ):
        raw = state.get(key, [])
        if raw:
            resolved_state[key] = _resolve_artifact_data(store, raw)

    # PPT 생성
    try:
        from ppt_builder import YieldReportPPTBuilder

        builder = YieldReportPPTBuilder()
        pptx_bytes = builder.build_compact(resolved_state)

        lotcd = state.get("lotcd", "Unknown")
        ref_date = state.get("ref_date", "")
        title = f"yield_report_{lotcd}_{ref_date}"

        result_msg = (
            f"📊 **{lotcd} 수율 분석 리포트**가 PPT로 생성되었습니다.\n"
            f"파일: `{title}.pptx` ({len(pptx_bytes) / 1024:.0f} KB)"
        )

        ref = save_artifact(
            pptx_bytes,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            title,
            "ppt_export",
            "pptx",
        )
        ppt_artifact = {
            "type": "pptx",
            "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "artifact_ref": ref.model_dump(),
            "title": title,
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
