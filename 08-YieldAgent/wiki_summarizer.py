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
    """wiki episode 응축 결과 (단일 검색 1회용, Day 3까지의 트리거)."""
    episode_summary: str = Field(description="2-3문장 한국어 요약 (raw 결과만 인용)")
    episode_body_md: str = Field(
        description="마크다운 본문. 권장 섹션: ## 원인 / ## 조치 / ## 관찰 패턴. 4KB 이내",
    )
    alias_pairs: list[AliasPair] = Field(
        default_factory=list,
        description="raw에서 동일 엔티티가 두 가지 표기로 나타난 경우만. 의심되면 빈 list.",
    )


# ── Day 2 신규: 같은 트리플 N건 누적 합성 ────────────────
class EpisodeRef(BaseModel):
    """plan v3 §C citation tracking — 사용자 향 + 운영 audit 분리."""
    episode_id: str = Field(description="내부 추적 (개발자 audit)")
    doc_id: str = Field(default="", description="raw doc ID (예: FH-000429)")
    source_file: str = Field(default="", description="PPT 파일명 (사용자 가시)")
    date: str = Field(default="", description="YYYY-MM-DD")
    natural_label: str = Field(default="", description="사용자 자연어 라벨")
    download_url: str = Field(default="", description="클릭 가능 URL")


class ConceptSynthesis(BaseModel):
    """plan v3 §A: 같은 트리플 N개 episode → concept body 통합 합성 결과."""
    body_markdown: str = Field(
        description="## 누적 패턴 / ## 검증된 조치 / ## 미해결 섹션 + inline [ep:xxx] 인용 필수"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="0~1 self-rated 신뢰도 (raw 일치도, source 다양성 기반)"
    )
    citations: list[EpisodeRef] = Field(
        default_factory=list,
        description="본문에 인용된 episode들. episode_id 필수, 나머지는 후처리에서 보강 가능"
    )
    notes: str = Field(default="", description="합성 시 주의사항/한계")


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


# ── Day 2: synthesize_concept — N건 episode → concept body 통합 ──
_SYNTHESIZE_SYSTEM = """당신은 반도체 fail_history wiki의 누적 분석 요약기입니다.

[원칙]
- 여러 episode를 읽고 메타 분석한 합성 markdown body를 생성합니다.
- raw episode들에 명시된 정보만 사용. 추측/일반론 금지.
- 각 주장 끝에 반드시 `[ep:xxx]` 형식 inline 인용. 인용 없는 주장은 출력 금지.
- 출력 구조:
  ```
  ## 누적 패턴 (N건 분석)
  - 주 원인: ... (X/N건) [ep:abc] [ep:def]
  - 부 원인: ... [ep:ghi]

  ## 검증된 조치
  - 조치 → 성공률 (X/Y) [ep:...]

  ## 미해결 / 이상 케이스
  - ... [ep:...]
  ```
- `confidence` (0~1) 자가 평가: source 다양성, raw 간 일치도, 인용 가능 evidence 수 기반.
  - 0.8+: source 5+, 일치도 높음, 모순 0
  - 0.5~0.7: source 3+, 부분 일치, 일부 모순
  - <0.5: source 2 미만 또는 모순 많음
- `citations`에는 본문에서 인용한 모든 episode_id를 채워라. doc_id가 있으면 같이.
- 출력 언어: 한국어
"""


@observe(name="wiki_synthesize_concept")
def synthesize_concept(
    concept_id: str,
    episodes: list[dict],
) -> ConceptSynthesis | None:
    """plan v3 §A: 같은 트리플 N개 episode → concept body 통합 합성.

    Args:
        concept_id: e.g. "concept:4SS|STI CMP|EASY(W)"
        episodes: list of {"id": "episode:xxx", "frontmatter": {...}, "body": str}
    Returns:
        ConceptSynthesis or None (실패 시).
    """
    if len(episodes) < 2:
        logger.info("[synthesize_concept] episodes < 2, skip: %s", concept_id)
        return None

    # episode block 구성
    blocks = []
    for ep in episodes[:10]:  # 토큰 가드 — 상위 10개
        eid_full = ep.get("id", "") or ep.get("frontmatter", {}).get("id", "")
        eid = eid_full.replace("episode:", "")
        meta = ep.get("frontmatter", {}) or {}
        doc_ids = meta.get("doc_ids", []) or []
        date = (meta.get("created", "") or "")[:10]
        query = (meta.get("query", "") or "")[:60]
        body = (ep.get("body") or "")[:500]  # 본문 cap
        blocks.append(
            f"--- [ep:{eid}] date={date} doc_ids={doc_ids} query={query} ---\n{body}"
        )
    blocks_str = "\n\n".join(blocks)

    user_msg = (
        f"[Concept] {concept_id}\n"
        f"[합성 source] {len(episodes)}개 episode (상위 {len(blocks)}건 표시)\n\n"
        f"{blocks_str}\n\n"
        f"위 episode들을 메타 분석해 markdown body + confidence + citations 생성하라.\n"
        f"각 주장에 [ep:xxx] inline 인용 필수."
    )

    try:
        chain = _model().with_structured_output(ConceptSynthesis, method="function_calling")
        out: ConceptSynthesis | None = chain.invoke(
            [("system", _SYNTHESIZE_SYSTEM), ("human", user_msg)],
            config={"callbacks": _lf_callbacks()},
        )
    except Exception as e:
        logger.warning("[synthesize_concept] LLM 호출 실패: %s", e)
        return None
    if out is None:
        logger.warning("[synthesize_concept] structured output None")
        return None

    # citations 후처리: LLM이 episode_id만 채우는 케이스가 많아 → episode frontmatter에서
    # doc_id/date/source_file 메타를 매핑으로 보강. 빈 citations면 episodes 기반 fallback도 적용.
    ep_meta_by_id: dict[str, dict] = {}
    for ep in episodes:
        fm = ep.get("frontmatter", {}) or {}
        eid_raw = ep.get("id", "") or fm.get("id", "")
        eid_short = eid_raw.replace("episode:", "")
        if eid_short:
            ep_meta_by_id[eid_short] = {
                "doc_ids": fm.get("doc_ids") or [],
                "date": (fm.get("created", "") or "")[:10],
                "query": fm.get("query", "") or "",
                "source_files": fm.get("source_files") or [],
            }

    def _enrich(c: EpisodeRef) -> EpisodeRef:
        eid_short = (c.episode_id or "").replace("episode:", "")
        meta = ep_meta_by_id.get(eid_short, {})
        if not c.doc_id and meta.get("doc_ids"):
            c.doc_id = meta["doc_ids"][0]
        if not c.date and meta.get("date"):
            c.date = meta["date"]
        if not c.source_file and meta.get("source_files"):
            c.source_file = meta["source_files"][0]
        if not c.natural_label:
            date_part = c.date or meta.get("date", "")
            q_part = (meta.get("query", "") or "")[:30]
            label = f"{date_part} {q_part}".strip()
            if label:
                c.natural_label = label
        return c

    if not out.citations:
        out.citations = [
            EpisodeRef(
                episode_id=(ep.get("id", "") or ep.get("frontmatter", {}).get("id", "")).replace("episode:", ""),
            )
            for ep in episodes[:10]
        ]
    out.citations = [_enrich(c) for c in out.citations]
    return out
