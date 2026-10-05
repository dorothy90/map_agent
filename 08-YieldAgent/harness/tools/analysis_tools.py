from ..runtime.domain_process import run_blocking
import os
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, model_validator

from ..types import Contract
from .registry import ToolResult, ToolSpec


class RelationInput(Contract):
    lotcd: str = Field(min_length=1)
    fail_type: str = Field(min_length=1)
    category: Literal["PT1H", "PT1C"] | None = None


class MiningInput(RelationInput):
    group_good: list[str] = Field(min_length=1, max_length=100)
    group_bad: list[str] = Field(min_length=1, max_length=100)
    rank_limit: int = Field(default=10, ge=1, le=50)
    tech: str = ""


class SearchInput(Contract):
    mode: Literal["bm25", "hybrid", "wiki"] = "bm25"
    query: str = Field(min_length=1, max_length=2000)
    product: str = ""
    fail_type: str = ""
    cause_oper: str = ""
    top_k: int = Field(default=5, ge=1, le=20)


class MapInput(Contract):
    lot_ids: list[str] = Field(default_factory=list, max_length=20)
    groupkey: str | None = Field(default=None, max_length=5000, description="조회 결과에서 확보한 LOT.wafer 식별자 목록")
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    wf_mod: int = Field(default=0, ge=0, le=1000)
    wf_rem: int = Field(default=0, ge=0, le=999)
    oper: Literal["PT1H", "PT1C"]
    map_type: Literal["binmap", "cummap"] = "cummap"

    @model_validator(mode="after")
    def target(self):
        if not self.lot_ids and not self.groupkey:
            raise ValueError("lot_ids or groupkey is required")
        if self.wf_mod and self.wf_rem >= self.wf_mod:
            raise ValueError("wf_rem must be smaller than wf_mod")
        return self


async def query_relation(args, ctx):
    from relation_tree_agent import _query_main_opers
    operations = await run_blocking(_query_main_opers, args.lotcd, args.fail_type, args.category or "")
    return ToolResult(rows=[{"main_oper": value} for value in operations], scope=args.model_dump(), summary="관련 main_oper 후보 조회. 이 결과만으로 인과관계를 입증하지 않는다.")


async def query_groups(args, ctx):
    from wt_resp_agent import _query_good_bad
    good, bad = await run_blocking(_query_good_bad, args.lotcd, args.fail_type, args.category or "")
    return ToolResult(rows=[{"group": group, "lot_id": lot} for group, lots in (("good", good), ("bad", bad)) for lot in lots], scope=args.model_dump(), summary="WADS 양품/불량 LOT 그룹 조회")


async def mining(args, ctx):
    endpoint = os.getenv("HARNESS_MINING_API_URL", "").strip()
    if not endpoint:
        raise ValueError("HARNESS_MINING_API_URL is not configured; no dummy fallback is allowed")
    async with httpx.AsyncClient(timeout=50) as client:
        response = await client.post(endpoint, json={**args.model_dump(), "user_id": ctx.run["principal_id"]})
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list) or not all(isinstance(row, dict) for row in payload["rows"]):
        raise ValueError("Mining API must return a rows array of objects")
    return ToolResult(rows=payload["rows"], scope=args.model_dump(), summary="마이닝 서비스 분석 결과")


def mining_availability():
    available = bool(os.getenv("HARNESS_MINING_API_URL", "").strip())
    return {"available": available, "reason": "" if available else "HARNESS_MINING_API_URL is not configured; no dummy fallback is allowed"}


def _search_wiki(args):
    from wiki_store import lookup, read_node
    payload = lookup(args.query, filters={k: getattr(args, k) for k in ("product", "fail_type", "cause_oper")}, max_episodes=args.top_k)
    rows = []
    for kind, items in payload.items():
        for item in items:
            row = {"wiki_kind": kind, **item}
            if item.get("id"):
                node = read_node(item["id"])
                if node:
                    row.update(content=node["body"], metadata=node["frontmatter"])
            rows.append(row)
    return rows


def _check_hybrid_index():
    from fail_history_tools import _get_opensearch_client, _OPENSEARCH_INDEX, _EMBEDDING_API_KEY, _EMBEDDING_DIM
    if not _EMBEDDING_API_KEY:
        raise ValueError("Hybrid search embedding provider is not configured; select bm25 or wiki")
    mapping = _get_opensearch_client().indices.get_mapping(index=_OPENSEARCH_INDEX)
    dimensions = [index.get("mappings", {}).get("properties", {}).get("embedding", {}).get("dimension") for index in mapping.values()]
    if not dimensions or any(d != _EMBEDDING_DIM for d in dimensions):
        raise ValueError("Embedding dimension does not match the search index; select bm25 or wiki")


async def search_history(args, ctx):
    if args.mode == "wiki":
        rows = await run_blocking(_search_wiki, args)
    else:
        from fail_history_tools import _search_opensearch
        if args.mode == "hybrid":
            await run_blocking(_check_hybrid_index)
        rows = await run_blocking(_search_opensearch, **args.model_dump(exclude={"mode"}), use_embeddings=args.mode == "hybrid")
    return ToolResult(rows=rows, scope={**args.model_dump(), "retrieval": args.mode,
        "content_complete": args.mode == "wiki", "ranking_fallback_possible": args.mode == "hybrid"},
        summary=f"불량 이력 검색 {len(rows)}건 ({args.mode}). " +
        ("위키 원문과 출처 ID 포함." if args.mode == "wiki" else "검색 본문은 미리보기이며 get_history_document(doc_id)로 원문을 읽는다.") +
        (" 기존 검색기의 BM25 fallback이 가능하다." if args.mode == "hybrid" else ""))


def _render_map(args):
    from map_agent import _query_wafer_data, _visualize_binmap, _visualize_cummap
    rows = _query_wafer_data(lot_ids=",".join(args.lot_ids) or None, groupkey=args.groupkey,
        wf_ids=",".join(map(str, args.wf_ids)) or None, wf_mod=args.wf_mod, wf_rem=args.wf_rem, oper=args.oper, strict=True)
    if not rows:
        return ToolResult(scope=args.model_dump(), summary="해당 조건의 웨이퍼 자료 없음")
    if args.map_type == "binmap":
        path = _visualize_binmap(rows, oper=args.oper)
    else:
        path, _ = _visualize_cummap(rows, oper=args.oper)
    if not path:
        raise ValueError("Map rendering failed")
    image = Path(path)
    try:
        artifact = {"title": args.map_type, "mime": "image/png", "data": image.read_bytes()}
    finally:
        image.unlink(missing_ok=True)
    return ToolResult(rows=rows, artifacts=[artifact], scope=args.model_dump(), summary=f"웨이퍼 {len(rows)}개 시각화", status="partial" if len(rows) >= 10000 else "success")


async def render_map(args, ctx):
    return await run_blocking(_render_map, args)


def register(registry):
    registry.add(ToolSpec("query_relation", "불량 항목과 관련된 main_oper 후보를 조회. 실제 상관계수나 원인 확정 결과가 아니다.", RelationInput, query_relation))
    registry.add(ToolSpec("query_good_bad_groups", "불량 항목의 양품/불량 LOT 그룹을 조회하여 비교 분석 입력을 확보한다.", RelationInput, query_groups))
    registry.add(ToolSpec("analyze_mining", "양품/불량 그룹을 실제 설정된 마이닝 API로 비교. API가 없으면 미설정 오류를 반환한다.", MiningInput, mining, availability=mining_availability))
    registry.add(ToolSpec("search_fail_history", "제품·불량·공정에 관련된 불량 이력을 BM25, 벡터 혼합(hybrid), 위키 원문(wiki) 모드로 검색한다. hybrid는 임베딩 공급자와 인덱스 차원이 맞아야 한다. 검색 doc_id의 전체 본문은 get_history_document로 읽는다.", SearchInput, search_history))
