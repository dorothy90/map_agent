"""검색 결과를 wiki episode/concept/alias로 응축하는 LLM summarizer.

plan v3 §wiki_summarizer.py:
- LangChain `with_structured_output(method="function_calling")`로 모델 독립성 확보
- get_llm + lf_callbacks 재사용
- 모델: WIKI_SUMMARIZE_MODEL > RETRIEVE_CHAIN_MODEL fallback
- redaction 패스 없음 (plan v3 §변경: 운영=사내 로컬 LLM, dev=OpenRouter라 외부 노출 차단 가드 PoC 외)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langfuse import observe
from pydantic import BaseModel, Field

from common import get_llm
from lf_utils import lf_callbacks as _lf_callbacks

logger = logging.getLogger("yield_agent.wiki_summarizer")


class AliasPair(BaseModel):
    """동일 의미의 표기 변형 1쌍 (예: EASY ↔ EASY(W))."""
    canonical: str = Field(description="짧은/표준형")
    variant: str = Field(description="긴/괄호 alias 포함형")


class SummarizeOut(BaseModel):
    """wiki episode 응축 결과."""
    episode_summary: str = Field(description="2-3문장 한국어 요약 (raw 결과만 인용)")
    episode_body_md: str = Field(
        description="마크다운 본문. 권장 섹션: ## 원인 / ## 조치 / ## 관찰 패턴. 4KB 이내",
    )
    alias_pairs: list[AliasPair] = Field(
        default_factory=list,
        description="raw에서 동일 엔티티가 두 가지 표기로 나타난 경우만. 의심되면 빈 list.",
    )


_SYSTEM_PROMPT = """당신은 반도체 불량이력 검색 결과를 wiki episode 노드로 응축하는 어시스턴트입니다.

[원칙]
- raw 결과에 명시된 정보만 인용. 추측·일반론 금지
- episode_body_md는 마크다운, 권장 섹션: `## 원인` / `## 조치` / `## 관찰 패턴`
- doc_id를 본문에 인용할 때는 `[FH-XXXXXX]` 형식
- alias_pairs는 raw 안에서 같은 엔티티가 두 표기로 등장한 경우만. 일반 동의어/번역어 금지
- 출력 언어는 한국어
"""


def _model():
    name = os.getenv("WIKI_SUMMARIZE_MODEL") or os.getenv("RETRIEVE_CHAIN_MODEL")
    return get_llm(model=name)


@observe(name="wiki_summarize")
def summarize(payload: dict[str, Any]) -> dict[str, Any] | None:
    """search 결과 → wiki_queue가 persist할 작업 dict.

    Returns: {"episode": {...}, "concept_filters": {...} | None, "alias_pairs": [(c,v),...]} or None.
    """
    raw = payload.get("raw_results") or []
    if not raw:
        return None
    filters = payload.get("filters") or {}
    query = payload.get("query", "") or ""

    # raw block (상위 5건만 prompt에 — token 비용 가드)
    raw_lines = []
    for r in raw[:5]:
        raw_lines.append(
            f"- doc_id={r.get('doc_id', '')} | cause={(r.get('cause') or '')[:200]} | "
            f"action={(r.get('action') or '')[:200]} | comment={(r.get('comment') or '')[:120]}"
        )
    raw_block = "\n".join(raw_lines) if raw_lines else "(none)"

    user_msg = (
        f"[검색 쿼리] {query}\n"
        f"[필터] product={filters.get('product', '')}, "
        f"fail_type={filters.get('fail_type', '')}, "
        f"cause_oper={filters.get('cause_oper', '')}\n"
        f"[Raw 결과 {len(raw)}건 (상위 {len(raw_lines)}건만 표시)]\n{raw_block}\n"
    )

    try:
        chain = _model().with_structured_output(SummarizeOut, method="function_calling")
        out: SummarizeOut | None = chain.invoke(
            [("system", _SYSTEM_PROMPT), ("human", user_msg)],
            config={"callbacks": _lf_callbacks()},
        )
    except Exception as e:
        logger.warning("[wiki_summarize] LLM 호출 실패: %s", e)
        return None
    if out is None:
        # function_calling이 빈 응답 반환하는 케이스 (간헐) — retry 위임
        logger.warning("[wiki_summarize] structured output None")
        return None

    doc_ids = [r.get("doc_id") for r in raw if r.get("doc_id")]
    return {
        "episode": {
            "query": query,
            "filters": filters,
            "doc_ids": doc_ids,
            "body": out.episode_body_md,
            "summary": out.episode_summary,
            "links": [],
        },
        "concept_filters": filters if all(
            filters.get(k) for k in ("product", "fail_type", "cause_oper")
        ) else None,
        "alias_pairs": [(p.canonical, p.variant) for p in out.alias_pairs if p.canonical and p.variant],
    }
