"""
Fail History Agent — 함수형 노드 (B2, no ReAct)
================================================
search → wiki-first면 vault 합성본 그대로, 아니면 LLM 1회 합성 → 인용 문서 표시.
LLM에게 도구 결정을 맡기지 않는다. 코드가 직접 함수 호출.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Set

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import timed, get_llm, extract_suggestion, is_transient_error
from lf_utils import lf_callbacks as _lf_callbacks
from prompts import FAIL_HISTORY_SYNTH_SYSTEM_PROMPT_TEMPLATE
from fail_history_tools import (
    do_search,
    _wiki_payload_var,
    _supervisor_parsed_var,
)

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.fail_history_agent")

_fh_model = get_llm(model=os.getenv("RETRIEVE_CHAIN_MODEL"))



def _extract_cited_doc_ids(answer: str) -> Set[str]:
    return set(re.findall(r'\[FH-([^\]]+)\]', answer))


def _format_cited_results(results: List[Dict[str, Any]], cited_ids: Set[str]) -> str:
    if not results:
        return ""
    download_base = os.getenv("DOWNLOAD_BASE_URL", "").rstrip("/")
    display = [r for r in results if r.get("doc_id") in cited_ids] if cited_ids else results
    if not display:
        display = results

    lines = [f"### 🔍 출처 (총 {len(display)}건)\n"]
    for i, r in enumerate(display, start=1):
        product = r.get("product") or "-"
        fail_type = r.get("fail_type") or "-"
        oper = r.get("cause_oper") or "-"
        date = r.get("date") or "-"
        cause = (r.get("cause") or "").strip().replace("\n", " ")
        action = (r.get("action") or "").strip().replace("\n", " ")
        source_file = r.get("source_file") or r.get("filenm") or ""
        url = f"{download_base}/{source_file}" if download_base and source_file else ""
        doc_name = source_file.split('/')[-1] if source_file else "다운로드"

        lines.append(f"**{i}. {date} | Product: `{product}` | Fail: `{fail_type}` | Oper: `{oper}`**")
        lines.append(f"- **원인:** {cause}")
        lines.append(f"- **조치:** {action}")
        if url:
            lines.append(f"- **문서:** [{doc_name}]({url})")
        lines.append("")
        
    return "\n".join(lines).strip()


def _synthesize_answer(
    query: str,
    raw: Dict[str, Any],
    lotcd: str,
    dh_fail_type: str,
    dh_cause_oper: str,
    config: RunnableConfig,
) -> str:
    """raw 검색 결과 → 자연어 답변 (LLM 1회)."""
    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = FAIL_HISTORY_SYNTH_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    ctx_parts = []
    if lotcd:
        ctx_parts.append(f"product={lotcd}")
    if dh_fail_type:
        ctx_parts.append(f"불량유형={dh_fail_type}")
    if dh_cause_oper:
        ctx_parts.append(f"원인공정={dh_cause_oper}")
    if ctx_parts:
        system_prompt += f"\n\n[조회 컨텍스트] {', '.join(ctx_parts)}"

    results = raw.get("results", [])
    input_parts = [
        f"[사용자 쿼리]\n{query}",
        f"[검색 결과 ({len(results)}건)]\n" + json.dumps(results, ensure_ascii=False, indent=2),
    ]
    if raw.get("retrieval_mode") == "wiki-assisted":
        wiki_body = raw.get("wiki_concept_body", "")
        wiki_confidence = raw.get("wiki_concept_confidence", 0.0)
        if wiki_body:
            input_parts.append(
                f"[과거 누적 합성 본문 (confidence={wiki_confidence:.2f})]\n{wiki_body}"
            )
    human_msg = "\n\n".join(input_parts)

    sub_config = {**config, "callbacks": _lf_callbacks()}
    ai = _fh_model.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=human_msg)],
        config=sub_config,
    )
    return ai.content if hasattr(ai, "content") else str(ai)


@observe(name="fail_history_agent_node")
@timed
def fail_history_agent_node(state: dict, config: RunnableConfig) -> dict:
    """함수형 노드: search → (wiki-first 즉시 / 아니면 LLM 1회 합성) → 인용 문서 표시."""
    lotcd = state.get("lotcd", "")
    dh_query = state.get("dh_query", "")
    dh_fail_type  = state.get("fail_type", "")
    dh_cause_oper = state.get("cause_oper", "")

    logger.info(
        "[FH Agent] lotcd=%s, dh_query=%s, dh_fail_type=%s, dh_cause_oper=%s",
        lotcd, dh_query, dh_fail_type, dh_cause_oper,
    )

    # 요청별 격리 ContextVar 초기화
    wiki_storage: Dict[str, Any] = {"hit_ids": [], "last_status": "skipped", "queries": []}
    _wiki_payload_var.set(wiki_storage)
    _supervisor_parsed_var.set({
        "product": lotcd,
        "fail_type": dh_fail_type,
        "cause_oper": dh_cause_oper,
        "query_hint": dh_query,
    })

    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)),
        None,
    )
    task_goal = state.get("current_task_goal", "")
    query = task_goal or (last_human.content if last_human else f"{lotcd} 불량이력 조회")
    logger.info("[FH Agent] 쿼리: %s (task_goal=%r)", query, task_goal)

    # 1) 검색 (함수 직접 호출)
    try:
        raw = do_search(
            query=query,
            product=lotcd,
            fail_type=dh_fail_type,
            cause_oper=dh_cause_oper,
            top_k=5,
        )
    except Exception as e:
        if is_transient_error(e):
            logger.warning("[FH Agent] 검색 transient 오류, retry 위임: %s", e)
            raise
        logger.error("[FH Agent] 검색 영구 오류: %s", e, exc_info=True)
        error_message = AIMessage(
            content="불량이력 검색 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            name="fail_history_agent",
        )
        return {
            "messages": [error_message],
            "fail_history_artifacts": [],
            "fail_history_results": [],
            "past_steps": [(state.get("current_task_id", ""), f"불량이력 영구 오류: {e}")],
        }

    retrieval_mode = raw.get("retrieval_mode", "baseline")
    results = raw.get("results", [])
    logger.info("[FH Agent] retrieval_mode=%s, results=%d", retrieval_mode, len(results))

    # 2) 답변 합성
    if retrieval_mode == "wiki-first":
        answer = raw.get("rendered_answer", "wiki 합성 본문이 비어 있습니다.")
        logger.info("[FH Agent] wiki-first 경로 — LLM 호출 0회")
    elif not results:
        answer = "조건에 맞는 불량이력이 없습니다. 검색어나 필터를 조정해보세요. [SUGGESTION: ]"
        logger.info("[FH Agent] 결과 0건 — LLM 호출 0회")
    else:
        try:
            answer = _synthesize_answer(query, raw, lotcd, dh_fail_type, dh_cause_oper, config)
        except Exception as e:
            if is_transient_error(e):
                logger.warning("[FH Agent] 합성 transient 오류, retry 위임: %s", e)
                raise
            logger.error("[FH Agent] 합성 영구 오류: %s", e, exc_info=True)
            answer = "검색은 됐지만 답변 합성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요. [SUGGESTION: ]"

    # 2-b) super_concept "참고용" 보조 섹션 (env WIKI_SUPER_REFERENCE_ENABLED=true 시)
    # Karpathy 정신: 답변 근거 X, 별도 섹션으로만. LLM 합성에 섞이지 않도록 후처리.
    super_body = raw.get("super_reference_body") or ""
    if super_body:
        answer = answer.rstrip() + "\n\n---\n\n## 관련 패턴 (참고용)\n\n" + super_body

    artifacts = []

    answer, agent_suggestion = extract_suggestion(answer)

    cited_ids = _extract_cited_doc_ids(answer)
    result_block = _format_cited_results(results, cited_ids)
    if result_block:
        message_content = f"### 💡 [답변]\n\n{answer}\n\n---\n\n{result_block}"
    else:
        message_content = f"### 💡 [답변]\n\n{answer}"
    result_message = AIMessage(content=message_content, name="fail_history_agent")

    wiki_hit_ids = list(dict.fromkeys(wiki_storage.get("hit_ids") or []))
    wiki_update_status = wiki_storage.get("last_status", "skipped")

    return {
        "messages": [result_message],
        "fail_history_artifacts": artifacts,
        "fail_history_results": results,
        "agent_suggestion": agent_suggestion,
        "past_steps": [(state.get("current_task_id", ""), message_content[:300])],
        "wiki_hit_ids": wiki_hit_ids,
        "wiki_update_status": wiki_update_status,
    }
