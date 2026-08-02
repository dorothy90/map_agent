"""검색 결과를 wiki episode/concept/alias로 응축하는 LLM summarizer.

plan v3 §wiki_summarizer.py:
- LangChain `with_structured_output(method="function_calling")`로 모델 독립성 확보
- get_llm + lf_callbacks 재사용
- 모델: WIKI_SUMMARIZE_MODEL > RETRIEVE_CHAIN_MODEL fallback
- Plugin Wiki item은 queue가 전달한 privacy context에서 decorator/callback payload capture를 차단
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any, Mapping

from pydantic import BaseModel, Field

from common import get_llm
from lf_utils import lf_callbacks as _lf_callbacks, observe_with_privacy
from wiki_graph_models import EntityCandidate, RelationCandidate

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
    episode_id: str = Field(description="episode vault ID (예: episode:abc123). doc_id(FH-xxx)와 다름. 직접 합성 시 빈 문자열 가능")
    doc_id: str = Field(default="", description="OpenSearch raw doc ID (예: FH-000238). FH-로 시작하는 실제 문서 ID를 여기에 채울 것")
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
    entities: list[EntityCandidate] = Field(
        default_factory=list,
        description="입력 근거에 명시된 정규화 엔티티만. 근거가 없으면 빈 목록",
    )
    relations: list[RelationCandidate] = Field(
        default_factory=list,
        description="입력 근거에 명시된 관계만. 각 관계는 인용된 doc_id를 source_doc_ids에 포함",
    )
    notes: str = Field(default="", description="합성 시 주의사항/한계")


def canonical_source_doc_ids(values: Iterable[Any]) -> list[str]:
    """Return non-empty Source IDs in stable, de-duplicated canonical form."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        doc_id = str(value or "").strip()
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


_AUTHORITATIVE_CITATION_FIELDS = (
    "episode_id",
    "source_file",
    "date",
    "natural_label",
    "download_url",
)


def authoritative_citations_from_documents(
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Build trusted citation fields keyed by canonical Source ID."""
    authoritative: dict[str, dict[str, str]] = {}
    for document in documents:
        doc_id = str(document.get("doc_id") or "").strip()
        if not doc_id:
            continue
        metadata = authoritative.setdefault(
            doc_id,
            {field: "" for field in _AUTHORITATIVE_CITATION_FIELDS},
        )
        for field in _AUTHORITATIVE_CITATION_FIELDS:
            value = str(document.get(field) or "").strip()
            if value and not metadata[field]:
                metadata[field] = value
    return authoritative


def authoritative_citations_from_episodes(
    episodes: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Build trusted citation fields from persisted Episode frontmatter."""
    documents: list[dict[str, str]] = []
    for episode in episodes:
        frontmatter = episode.get("frontmatter", {}) or {}
        episode_id = str(episode.get("id") or frontmatter.get("id") or "")
        episode_id = episode_id.replace("episode:", "", 1)
        doc_ids = list(frontmatter.get("doc_ids") or [])
        source_files = list(frontmatter.get("source_files") or [])
        date = str(frontmatter.get("created") or "")[:10]
        for index, doc_id in enumerate(doc_ids):
            documents.append(
                {
                    "doc_id": str(doc_id),
                    "episode_id": episode_id,
                    "source_file": (
                        str(source_files[index]) if index < len(source_files) else ""
                    ),
                    "date": date,
                }
            )
    return authoritative_citations_from_documents(documents)


def _model_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        try:
            return dict(dump(mode="json"))
        except TypeError:
            return dict(dump())
    return dict(value)


def restrict_concept_synthesis_sources(
    synthesis: Any,
    authoritative_sources: Mapping[str, Mapping[str, Any]] | Iterable[Any],
) -> Any:
    """Remove provider Source claims not present in the actual input set.

    Relations are strict: any out-of-set Source rejects the complete Relation
    instead of silently weakening its provenance by trimming individual IDs.
    """
    if isinstance(authoritative_sources, Mapping):
        authoritative = authoritative_citations_from_documents(
            {**dict(metadata), "doc_id": doc_id}
            for doc_id, metadata in authoritative_sources.items()
        )
    else:
        authoritative = authoritative_citations_from_documents(
            {"doc_id": doc_id}
            for doc_id in canonical_source_doc_ids(authoritative_sources)
        )
    allowed = set(authoritative)
    provider_citations = list(getattr(synthesis, "citations", []) or [])
    provider_relations = list(getattr(synthesis, "relations", []) or [])
    citations: list[EpisodeRef] = []
    seen_citations: set[str] = set()
    for value in provider_citations:
        try:
            citation = EpisodeRef.model_validate(_model_data(value))
        except (TypeError, ValueError):
            continue
        doc_id = str(citation.doc_id or "").strip()
        if doc_id not in allowed or doc_id in seen_citations:
            continue
        seen_citations.add(doc_id)
        metadata = authoritative[doc_id]
        citations.append(
            EpisodeRef(
                episode_id=metadata["episode_id"],
                doc_id=doc_id,
                source_file=metadata["source_file"],
                date=metadata["date"],
                natural_label=metadata["natural_label"],
                download_url=metadata["download_url"],
            )
        )

    relations: list[RelationCandidate] = []
    for value in provider_relations:
        try:
            relation = RelationCandidate.model_validate(_model_data(value))
        except (TypeError, ValueError):
            continue
        source_doc_ids = canonical_source_doc_ids(relation.source_doc_ids)
        if not source_doc_ids or any(doc_id not in allowed for doc_id in source_doc_ids):
            continue
        relations.append(
            relation.model_copy(update={"source_doc_ids": source_doc_ids})
        )

    synthesis.citations = citations
    synthesis.relations = relations
    dropped_citations = len(provider_citations) - len(citations)
    dropped_relations = len(provider_relations) - len(relations)
    if dropped_citations or dropped_relations:
        logger.warning(
            "[wiki_source_grounding] dropped citations=%d relations=%d",
            dropped_citations,
            dropped_relations,
        )
    return synthesis


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


@observe_with_privacy(name="wiki_summarize")
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
    source_files = [r.get("source_file") for r in raw if r.get("source_file")]
    return {
        "episode": {
            "query": query,
            "filters": filters,
            "doc_ids": doc_ids,
            "source_files": source_files,
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
_SYNTHESIZE_SYSTEM = """당신은 반도체 메모리 공장 수율팀의 fail_history wiki 누적 분석 요약기입니다.

[원칙]
- raw episode들에 명시된 정보만 사용. 추측/일반론 금지.
- 각 주장 끝에 반드시 [ep:xxx] inline 인용. 인용 없는 주장 출력 금지.
- 출력 언어: 한국어
- FMMEA(Failure Mode·Mechanism·Effect Analysis) + 8D 구조 준수

[출력 구조 — 아래 순서 그대로]

## 불량 메커니즘 다이어그램
```mermaid
flowchart LR
    A["근본원인"] --> B["공정이상\\n{cause_oper}"]
    B --> C["물리적 메커니즘"]
    C --> D["불량모드\\n{fail_type}"]
    D --> E["수율 영향"]
    F["기여인자1"] --> B
    G["기여인자2"] --> C
```
규칙: episode에서 파악된 실제 인과관계로 노드를 채울 것. 노드 텍스트 15자 이내, \\n으로 줄바꿈 가능. 기여인자 없으면 F/G 노드 생략. 근거 없는 노드 추가 금지.

## 원본 사례 요약
| Lot | 불량유형 | 공정 | 물리적 메커니즘 | 조치 | 재발방지 |
|-----|---------|------|--------------|------|---------|
episode마다 1행. Lot/불량유형/공정은 concept_id에서 추출. 없는 정보는 -

## 누적 패턴 ({N}건 분석)
- **주 원인**: ... (X/N건) [ep:abc] [ep:def]
- **기여 인자**: ... [ep:ghi]

## 검증된 조치
- **영구 조치 (8D D5)**: {조치} → 효과 (X/Y건) [ep:...]
- **임시 봉쇄 (8D D3)**: {조치} [ep:...]

## 표준화 권고 (Lessons Learned)
- ... [ep:...]

## 미해결 / 추가 분석 필요
- ... [ep:...]

[confidence 기준]
- 0.8+: source 5건 이상, 메커니즘 일치도 높음, 모순 없음
- 0.5~0.7: source 3건 이상, 부분 일치
- <0.5: source 2건 미만 또는 모순 많음

[citations] 본문에 인용한 모든 episode_id + doc_id 필수

[graph extraction]
- entities에는 episode 근거에 명시된 엔티티만 넣고, 근거가 없으면 빈 목록으로 둔다.
- relations에는 episode 근거에 명시된 관계만 넣는다. predicate는 `causes`, `contributes_to`, `resolved_by`, `prevents`, `associated_with` 중 하나만 사용한다.
- 모든 relation의 source_doc_ids에는 해당 관계를 뒷받침하는, citations에 포함된 doc_id만 넣는다.
"""


@observe_with_privacy(name="wiki_synthesize_concept")
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
        if not c.download_url and c.source_file:
            base = os.getenv("DOWNLOAD_BASE_URL",
                             "https://downloadendpoint-estress/download")
            c.download_url = f"{base.rstrip('/')}/{c.source_file}"
        return c

    if not out.citations:
        out.citations = [
            EpisodeRef(
                episode_id=(ep.get("id", "") or ep.get("frontmatter", {}).get("id", "")).replace("episode:", ""),
            )
            for ep in episodes[:10]
        ]
    out.citations = [_enrich(c) for c in out.citations]
    return restrict_concept_synthesis_sources(
        out,
        authoritative_citations_from_episodes(episodes),
    )


# ── Day 5: super_concept — cross-concept 추상화 (참고용 한정) ─────
class SuperConceptSynthesis(BaseModel):
    """plan v3 §B: 여러 concept → cross-concept 추상화 결과. 참고용 한정."""
    body_markdown: str = Field(
        description="공통 패턴 / 변별 요소 / 참고 권고 섹션 + 합성 근거 concept_id 인용"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="0~1 self-rated. concept body보다 0.1~0.2 낮게 권장."
    )
    notes: str = Field(default="", description="합성 시 주의사항/한계")


_SUPER_SYSTEM = """당신은 반도체 fail_history wiki의 cross-concept 추상화 합성기입니다.

[역할]
여러 concept(트리플 단위 누적 지식)을 입력 받아, 공통 패턴을 추상화한 super_concept 본문을 생성합니다.

[원칙]
- 입력 concept body에 명시된 정보만 사용. 추측·일반론 금지.
- 본문은 참고용으로만 사용 — 운영자가 "확정 근거"가 아닌 "유사 패턴 hint"로 읽음을 의식.
- 출력 구조:
  ```
  ## 공통 패턴 (N concepts 기반)
  - ...

  ## 변별 요소
  - [concept:...] : ...

  ## 참고 권고
  - 운영자가 참고할 패턴 가이드
  ```
- `confidence` (0~1):
  - source concept 수 / 일치도 / 모순 기반
  - 보통 0.4~0.7 권장. 모순 크면 <0.4, 명확 일치 + source ≥4면 >0.7.
- 출력 언어: 한국어
"""


@observe_with_privacy(name="wiki_synthesize_super_concept")
def synthesize_super_concept(
    axis: str,
    axis_value: str,
    concepts: list[dict],
) -> SuperConceptSynthesis | None:
    """여러 concept → cross-concept 메타 합성 (참고용 한정).

    Args:
        axis: 와일드카드 축 — "fail_type" | "cause_oper" | "product"
        axis_value: 고정된 다른 축 값 (예: axis="fail_type", value="EASY")
        concepts: list of {"id": "concept:...", "frontmatter": {...}, "body": str}

    Returns:
        SuperConceptSynthesis or None
    """
    if len(concepts) < 2:
        logger.info("[synthesize_super_concept] concepts < 2, skip: %s=%s", axis, axis_value)
        return None

    blocks = []
    for c in concepts[:10]:
        cid = c.get("id", "")
        fm = c.get("frontmatter", {}) or {}
        product = fm.get("product", "")
        cause_oper = fm.get("cause_oper", "")
        fail_type = fm.get("fail_type", "")
        conf = fm.get("confidence", 0.0)
        body = (c.get("body") or "")[:1500]
        blocks.append(
            f"--- [{cid}] product={product} cause_oper={cause_oper} fail_type={fail_type} concept_confidence={conf} ---\n{body}"
        )
    blocks_str = "\n\n".join(blocks)
    user_msg = (
        f"[Axis] {axis} = {axis_value} (다른 축은 와일드카드)\n"
        f"[Concepts] {len(concepts)}개 (상위 {len(blocks)}건 표시)\n\n"
        f"{blocks_str}\n\n"
        f"위 concept들을 메타 분석해 추상화된 markdown body + confidence 생성하라.\n"
        f"공통 패턴 / 변별 요소 / 참고 권고 섹션 필수."
    )

    try:
        chain = _model().with_structured_output(SuperConceptSynthesis, method="function_calling")
        out: SuperConceptSynthesis | None = chain.invoke(
            [("system", _SUPER_SYSTEM), ("human", user_msg)],
            config={"callbacks": _lf_callbacks()},
        )
    except Exception as e:
        logger.warning("[synthesize_super_concept] LLM 호출 실패: %s", e)
        return None
    if out is None:
        logger.warning("[synthesize_super_concept] structured output None")
        return None
    return out


# ── Phase 11: 직접 합성 (raw docs → concept body) — episode 단계 생략 ────
_SYNTHESIZE_FROM_DOCS_SYSTEM = """당신은 반도체 메모리 공장 수율팀의 fail_history wiki 직접 합성기입니다.

[원칙]
- raw docs에 명시된 cause/action/comment만 사용. 추측·일반론 금지.
- 각 주장 끝에 반드시 [FH-XXXXXX] 형식 doc_id inline 인용. 인용 없는 주장 출력 금지.
- 출력 언어: 한국어
- FMMEA(Failure Mode·Mechanism·Effect Analysis) + 8D 구조 준수

[출력 구조 — 아래 순서 그대로]

## 불량 메커니즘 다이어그램
```mermaid
flowchart LR
    A["근본원인"] --> B["공정이상\\n{cause_oper}"]
    B --> C["물리적 메커니즘"]
    C --> D["불량모드\\n{fail_type}"]
    D --> E["수율 영향"]
    F["기여인자1"] --> B
    G["기여인자2"] --> C
```
규칙: raw docs에서 파악된 실제 인과관계로 노드를 채울 것. 노드 텍스트 15자 이내, \\n으로 줄바꿈 가능. 기여인자 없으면 F/G 노드 생략. 근거 없는 노드 추가 금지.

## 원본 사례 요약
| Lot | 불량유형 | 공정 | 물리적 메커니즘 | 조치 | 재발방지 |
|-----|---------|------|--------------|------|---------|
doc마다 1행. Lot/불량유형/공정은 concept_id에서 추출. 없는 정보는 -

## 누적 패턴 ({N}건 분석)
- **주 원인**: ... (X/N건) [FH-...] [FH-...]
- **기여 인자**: ... [FH-...]

## 검증된 조치
- **영구 조치 (8D D5)**: {조치} → 효과 (X/Y건) [FH-...]
- **임시 봉쇄 (8D D3)**: {조치} [FH-...]

## 표준화 권고 (Lessons Learned)
- ... [FH-...]

## 미해결 / 추가 분석 필요
- ... [FH-...]

[confidence 기준]
- 0.8+: 5건 이상, 일치도 높음, 모순 없음
- 0.5~0.7: 2~4건, 부분 일치
- <0.5: 1~2건 또는 모순 많음

[citations] doc_id만 채워라. source_file은 절대 추측하지 말고 반드시 빈 문자열("")로 둘 것.

[graph extraction]
- entities에는 raw docs 근거에 명시된 엔티티만 넣고, 근거가 없으면 빈 목록으로 둔다.
- relations에는 raw docs 근거에 명시된 관계만 넣는다. predicate는 `causes`, `contributes_to`, `resolved_by`, `prevents`, `associated_with` 중 하나만 사용한다.
- 모든 relation의 source_doc_ids에는 해당 관계를 뒷받침하는, citations에 포함된 doc_id만 넣는다.
"""


@observe_with_privacy(name="wiki_synthesize_concept_from_docs")
def synthesize_concept_from_docs(
    concept_id: str,
    raw_docs: list[dict],
) -> ConceptSynthesis | None:
    """Phase 11: 직접 합성 — 같은 트리플 raw docs → concept body. episode 단계 생략.

    Args:
        concept_id: e.g. "concept:4SS|PRE METAL CLN|EASY"
        raw_docs: OpenSearch에서 fetch한 _source dict list
                  (doc_id / source_file / date / cause / action / comment / ...)
    Returns:
        ConceptSynthesis or None (실패 시).
    """
    if not raw_docs:
        logger.info("[synthesize_concept_from_docs] empty docs: %s", concept_id)
        return None

    blocks = []
    for d in raw_docs[:15]:  # 토큰 가드
        did = d.get("doc_id", "")
        date = d.get("date", "")
        cause = (d.get("cause") or "").strip()[:600]
        action = (d.get("action") or "").strip()[:600]
        comment = (d.get("comment") or "").strip()[:300]
        blocks.append(
            f"--- [{did}] date={date} ---\n"
            f"원인: {cause}\n"
            f"조치: {action}\n"
            f"코멘트: {comment}"
        )
    blocks_str = "\n\n".join(blocks)
    user_msg = (
        f"[Concept] {concept_id}\n"
        f"[Raw docs] {len(raw_docs)}건 (상위 {len(blocks)}건 표시)\n\n"
        f"{blocks_str}\n\n"
        f"위 raw docs를 메타 분석해 markdown body + confidence + citations 생성하라.\n"
        f"각 주장에 [FH-xxx] inline 인용 필수."
    )

    try:
        chain = _model().with_structured_output(ConceptSynthesis, method="function_calling")
        out: ConceptSynthesis | None = chain.invoke(
            [("system", _SYNTHESIZE_FROM_DOCS_SYSTEM), ("human", user_msg)],
            config={"callbacks": _lf_callbacks()},
        )
    except Exception as e:
        logger.warning("[synthesize_concept_from_docs] LLM 호출 실패: %s", e)
        return None
    if out is None:
        logger.warning("[synthesize_concept_from_docs] structured output None")
        return None

    # citation enrichment: LLM이 doc_id만 채워도 source_file/date 자동 보강
    doc_by_id = {d.get("doc_id", ""): d for d in raw_docs if d.get("doc_id")}

    def _enrich(c: EpisodeRef) -> EpisodeRef:
        meta = doc_by_id.get(c.doc_id, {})
        if meta:
            if not c.source_file and meta.get("source_file"):
                c.source_file = meta["source_file"]
            if not c.date and meta.get("date"):
                c.date = str(meta["date"])
            if not c.natural_label:
                date_part = c.date or str(meta.get("date", ""))
                ftype = meta.get("fail_type", "")
                c.natural_label = f"{date_part} {ftype}".strip()
        if not c.download_url and c.source_file:
            base = os.getenv("DOWNLOAD_BASE_URL",
                             "https://downloadendpoint-estress/download")
            c.download_url = f"{base.rstrip('/')}/{c.source_file}"
        return c

    if not out.citations:
        out.citations = [
            EpisodeRef(
                episode_id="",  # 직접 합성이라 episode 없음
                doc_id=d.get("doc_id", ""),
                source_file=d.get("source_file", ""),
                date=str(d.get("date", "")),
            )
            for d in raw_docs[:10] if d.get("doc_id")
        ]
    out.citations = [_enrich(c) for c in out.citations]
    return restrict_concept_synthesis_sources(
        out,
        authoritative_citations_from_documents(raw_docs),
    )
