import os

from fastapi import APIRouter, Depends, HTTPException, Query

from models import PluginRelatedResponse, PluginSearchResponse, PluginSourceResponse
from wiki_config import resolve_wiki_paths
from wiki_plugin_auth import require_plugin_token
from wiki_plugin_notes import NoteNotFound, read_source, related_notes
from wiki_plugin_search import search_wiki

router = APIRouter(dependencies=[Depends(require_plugin_token)])


def plugin_dependency_status() -> dict[str, str]:
    try:
        from fail_history_tools import _get_opensearch_client

        opensearch = "ok" if _get_opensearch_client().ping() else "unavailable"
    except Exception:
        opensearch = "unavailable"
    llm = "configured" if os.getenv("OPENROUTER_API_KEY", "") else "unconfigured"
    return {"backend": "ok", "opensearch": opensearch, "llm": llm}


@router.get("/health")
def plugin_health() -> dict:
    return {"status": "ok", "dependencies": plugin_dependency_status()}


@router.get("/search", response_model=PluginSearchResponse)
def plugin_search(
    q: str = Query(..., min_length=1),
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    limit: int = Query(20, ge=1, le=100),
) -> PluginSearchResponse:
    try:
        return search_wiki(q, product, fail_type, cause_oper, limit, resolve_wiki_paths())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="OpenSearch 검색에 실패했습니다.") from exc


@router.get("/related/{note_path:path}", response_model=PluginRelatedResponse)
def plugin_related(note_path: str) -> PluginRelatedResponse:
    try:
        return related_notes(resolve_wiki_paths(), note_path)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Wiki 노트를 찾을 수 없습니다.") from exc


@router.get("/sources/{doc_id}", response_model=PluginSourceResponse)
def plugin_source(doc_id: str) -> PluginSourceResponse:
    try:
        return read_source(resolve_wiki_paths(), doc_id)
    except NoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Source를 찾을 수 없습니다.") from exc
