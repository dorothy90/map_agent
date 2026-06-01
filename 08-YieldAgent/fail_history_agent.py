"""
Fail History Agent — 함수형 노드 (B2, no ReAct)
================================================
search → wiki-first면 vault 합성본 그대로, 아니면 LLM 1회 합성 → 인용 문서 표시.
LLM에게 도구 결정을 맡기지 않는다. 코드가 직접 함수 호출.
"""
from __future__ import annotations

import html
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
from result_contracts import attach_result_envelope, derive_summary_from_rows
from fail_history_tools import (
    do_search,
    _wiki_payload_var,
    _supervisor_parsed_var,
)

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.fail_history_agent")

_fh_model = get_llm(model=os.getenv("RETRIEVE_CHAIN_MODEL"))

_PRODUCT_CODE_RE = re.compile(r"^[0-9][A-Za-z0-9]{2}$")
_LOT_ID_RE = re.compile(r"^[A-Za-z0-9]{7}$")


def _product_filter_from_lotcd(value: str) -> str:
    """Return a product metadata filter only when the value is a 3-char lot_cd."""

    text = (value or "").strip().upper()
    if not text:
        return ""
    if _PRODUCT_CODE_RE.fullmatch(text):
        return text
    if _LOT_ID_RE.fullmatch(text):
        logger.info("[FH Agent] LOT ID-like lotcd ignored for product metadata filter: %s", text)
        return ""
    logger.info("[FH Agent] non-product lotcd ignored for product metadata filter: %s", text)
    return ""



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


def _format_report_inline(text: str) -> str:
    escaped = html.escape(str(text or ""), quote=True)
    escaped = re.sub(
        r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _clean_report_heading(text: str) -> str:
    heading = re.sub(r"^#+\s*", "", text).strip()
    replacements = {
        "💡 [답변]": "답변",
        "🔍 출처": "출처",
    }
    for old, new in replacements.items():
        heading = heading.replace(old, new)
    return heading


def _render_report_body(content: str) -> str:
    parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line == "---":
            close_list()
            parts.append('<hr class="report-rule" />')
            continue
        if line.startswith("###") or line.startswith("##"):
            close_list()
            parts.append(f"<h2>{html.escape(_clean_report_heading(line))}</h2>")
            continue
        if line.startswith(">"):
            close_list()
            quote = line.lstrip("> ").strip()
            parts.append(f"<blockquote>{_format_report_inline(quote)}</blockquote>")
            continue
        if line.startswith("- "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_format_report_inline(line[2:].strip())}</li>")
            continue
        close_list()
        cls = ' class="source-title"' if line.startswith("**") and line.endswith("**") else ""
        parts.append(f"<p{cls}>{_format_report_inline(line)}</p>")

    close_list()
    return "\n".join(parts) or "<p>표시할 내용이 없습니다.</p>"


def _message_artifact(
    content: str,
    title: str = "fail_history_answer",
    *,
    query: str = "",
    status: str = "success",
    result_count: int = 0,
    retrieval_mode: str = "",
) -> dict[str, str]:
    """Package fail_history chat prose as a readable HTML report artifact."""

    status_labels = {
        "success": "검색 완료",
        "empty": "결과 없음",
        "error": "오류",
    }
    status_label = status_labels.get(status, status or "검색 완료")
    status_class = "error" if status == "error" else "empty" if status == "empty" else "success"
    query_html = html.escape(query or "Fail history query", quote=True)
    retrieval_html = html.escape(retrieval_mode or "baseline", quote=True)
    body_html = _render_report_body(content)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    report_html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Fail History Report</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f7f8fa;color:#17202a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.65}}
.report{{max-width:980px;margin:0 auto;padding:18px}}.paper{{background:#fff;border:1px solid #d8dee8;border-radius:8px;overflow:hidden}}
.report-header{{display:flex;justify-content:space-between;gap:18px;padding:18px 20px;border-bottom:1px solid #d8dee8;background:#fbfcfd}}
.eyebrow{{color:#0f766e;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}}h1{{margin:2px 0 0;font-size:19px;line-height:1.25;letter-spacing:0}}
.query{{margin-top:8px;color:#667085;font-size:13px}}.meta{{display:flex;flex-wrap:wrap;justify-content:flex-end;align-content:flex-start;gap:6px;min-width:180px}}
.badge{{display:inline-flex;align-items:center;min-height:26px;padding:4px 9px;border-radius:999px;background:#eef3f8;color:#344054;font-size:12px;font-weight:650;white-space:nowrap}}
.badge.success{{background:#e7f6f3;color:#0f766e}}.badge.empty{{background:#fff4dd;color:#a15c07}}.badge.error{{background:#fff0ed;color:#b42318}}
.report-body{{padding:18px 20px 22px}}.report-body h2{{margin:18px 0 9px;color:#111827;font-size:15px;line-height:1.35;letter-spacing:0}}.report-body h2:first-child{{margin-top:0}}
p{{margin:8px 0;color:#243142}}.source-title{{margin-top:14px;padding:10px 12px;border-left:3px solid #0f766e;background:#f8fbfb;color:#1f2937}}
ul{{margin:8px 0 12px;padding:0;list-style:none}}li{{position:relative;margin:6px 0;padding-left:18px;color:#2f3a4a}}li:before{{content:"";position:absolute;left:2px;top:.75em;width:6px;height:6px;border-radius:50%;background:#0f766e}}
strong{{font-weight:700;color:#111827}}code{{padding:1px 5px;border-radius:4px;background:#eef2f6;color:#243142;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px}}
a{{color:#0b63ce;text-decoration:none;font-weight:650}}a:hover{{text-decoration:underline}}blockquote{{margin:10px 0;padding:10px 12px;border-left:3px solid #9aa4b2;background:#f5f7fa;color:#475467}}
.report-rule{{border:0;border-top:1px solid #d8dee8;margin:18px 0}}.footer{{padding:10px 20px 14px;border-top:1px solid #d8dee8;color:#667085;font-size:11px;background:#fbfcfd}}
@media(max-width:640px){{.report{{padding:10px}}.report-header{{flex-direction:column;padding:16px}}.meta{{justify-content:flex-start;min-width:0}}.report-body{{padding:16px}}}}
</style></head><body>
<main class="report"><article class="paper">
<header class="report-header"><div><div class="eyebrow">Fail History Report</div><h1>불량이력 분석 결과</h1><div class="query">검색어: {query_html}</div></div>
<div class="meta"><span class="badge {status_class}">{html.escape(status_label)}</span><span class="badge">결과 {result_count}건</span><span class="badge">{retrieval_html}</span></div></header>
<section class="report-body">{body_html}</section>
<footer class="footer">Generated {html.escape(generated_at)} · fail_history_agent</footer>
</article></main>
<script>
function sendHeight(){{var h=(document.documentElement.scrollHeight||document.body.scrollHeight)+16;window.parent.postMessage({{type:'resize',height:h}},'*');window.parent.postMessage({{type:'set-height',height:h}},'*')}}
window.addEventListener('load',function(){{sendHeight();setTimeout(sendHeight,100);setTimeout(sendHeight,300)}});window.addEventListener('resize',sendHeight);if(window.ResizeObserver){{new ResizeObserver(sendHeight).observe(document.body)}}
</script></body></html>"""

    return {
        "type": "html",
        "mime": "text/html",
        "data": report_html,
        "title": title,
        "agent": "fail_history_agent",
        "semantic": "unknown",
    }


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
    product_filter = _product_filter_from_lotcd(lotcd)
    dh_query = state.get("dh_query", "")
    dh_fail_type  = state.get("fail_type", "")
    dh_cause_oper = state.get("cause_oper", "")

    logger.info(
        "[FH Agent] lotcd=%s, product_filter=%s, dh_query=%s, dh_fail_type=%s, dh_cause_oper=%s",
        lotcd, product_filter, dh_query, dh_fail_type, dh_cause_oper,
    )

    # 요청별 격리 ContextVar 초기화
    wiki_storage: Dict[str, Any] = {"hit_ids": [], "last_status": "skipped", "queries": []}
    _wiki_payload_var.set(wiki_storage)
    _supervisor_parsed_var.set({
        "product": product_filter,
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
            product=product_filter,
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
        error_artifacts = [
            _message_artifact(
                error_message.content,
                "fail_history_error",
                query=query,
                status="error",
                result_count=0,
            )
        ]
        attach_result_envelope(
            error_message,
            logger=logger,
            source_agent="fail_history_agent",
            kind="summary",
            status="error",
            title="fail_history",
            summary=error_message.content,
            entities={
                "products": [product_filter] if product_filter else [],
                "parameters": [dh_fail_type] if dh_fail_type else [],
                "fail_types": [dh_fail_type] if dh_fail_type else [],
                "cause_opers": [dh_cause_oper] if dh_cause_oper else [],
            },
            artifacts=error_artifacts,
            provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
            metadata={"artifact_count": len(error_artifacts)},
        )
        return {
            "messages": [error_message],
            "fail_history_artifacts": error_artifacts,
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
            answer = _synthesize_answer(query, raw, product_filter, dh_fail_type, dh_cause_oper, config)
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

    answer, agent_suggestion = extract_suggestion(answer)

    cited_ids = _extract_cited_doc_ids(answer)
    result_block = _format_cited_results(results, cited_ids)
    if result_block:
        message_content = f"### 💡 [답변]\n\n{answer}\n\n---\n\n{result_block}"
    else:
        message_content = f"### 💡 [답변]\n\n{answer}"
    artifacts = [
        _message_artifact(
            message_content,
            query=query,
            status="success" if results else "empty",
            result_count=len(results),
            retrieval_mode=retrieval_mode,
        )
    ]
    result_message = AIMessage(content=message_content, name="fail_history_agent")
    doc_ids = [r.get("doc_id") for r in results if isinstance(r, dict) and r.get("doc_id")]
    grounded_summary = derive_summary_from_rows(
        source_agent="fail_history_agent",
        rows=results,
        artifacts=artifacts,
        fallback=message_content,
        title="fail_history",
    )
    attach_result_envelope(
        result_message,
        logger=logger,
        source_agent="fail_history_agent",
        kind="document" if results else "summary",
        status="success" if results else "empty",
        title="fail_history",
        summary=grounded_summary,
        rows=results,
        entities={
            "products": [product_filter] if product_filter else [],
            "parameters": [dh_fail_type] if dh_fail_type else [],
            "fail_types": [dh_fail_type] if dh_fail_type else [],
            "cause_opers": [dh_cause_oper] if dh_cause_oper else [],
            "doc_ids": doc_ids,
        },
        artifacts=artifacts,
        provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
        metadata={
            "row_count": len(results),
            "artifact_count": len(artifacts),
            "cited_doc_count": len(cited_ids),
            "wiki_hit_count": len(wiki_storage.get("hit_ids") or []),
        },
    )

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
