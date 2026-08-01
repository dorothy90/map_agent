import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agent_sessions import list_session_summaries, load_session_history
from models import (
    InternalChatRequest,
    PluginChatRequest,
    PluginRelatedResponse,
    PluginReview,
    PluginReviewCreate,
    PluginReviewUpdate,
    PluginSearchResponse,
    PluginSourceResponse,
    ReviewStatus,
    SessionHistory,
    SessionSummary,
)
from wiki_config import resolve_wiki_paths
from wiki_plugin_auth import require_plugin_token
from wiki_plugin_notes import NoteNotFound, load_note_context, read_source, related_notes
from wiki_plugin_search import search_wiki
from wiki_review_store import ReviewConflict, ReviewNotFound, WikiReviewStore

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


@router.post("/chat")
async def plugin_chat(body: PluginChatRequest, request: Request):
    context = None
    if body.current_note_id:
        try:
            note = load_note_context(resolve_wiki_paths(), body.current_note_id)
        except NoteNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail="현재 Wiki 노트를 찾을 수 없습니다.",
            ) from exc
        metadata = note.get("metadata") or {}
        context = {
            "id": metadata.get("id"),
            "path": note["note_path"],
            "metadata": metadata,
            "body": note["body_markdown"],
        }
    chat_body = InternalChatRequest(
        query=body.query,
        session_id=body.session_id,
        user_id=body.user_id,
        resume_value=body.resume_value,
        wiki_context=context,
    )
    return await request.app.state.chat_stream_handler(chat_body, request)


@router.get("/sessions", response_model=list[SessionSummary])
async def plugin_sessions(request: Request) -> list[SessionSummary]:
    return await list_session_summaries(request.app.state.motor_db)


@router.get("/sessions/{session_id}", response_model=SessionHistory)
async def plugin_session_history(
    session_id: str, request: Request
) -> SessionHistory:
    return await load_session_history(request.app.state.motor_db, session_id)


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


@router.get("/reviews", response_model=list[PluginReview])
def plugin_reviews(status: ReviewStatus | None = None) -> list[PluginReview]:
    return WikiReviewStore(resolve_wiki_paths()).list(status=status)


@router.post("/reviews", response_model=PluginReview, status_code=201)
def create_plugin_review(body: PluginReviewCreate) -> PluginReview:
    return WikiReviewStore(resolve_wiki_paths()).create(body)


@router.patch("/reviews/{review_id:path}", response_model=PluginReview)
def update_plugin_review(review_id: str, body: PluginReviewUpdate) -> PluginReview:
    try:
        return WikiReviewStore(resolve_wiki_paths()).update(review_id, body)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Review를 찾을 수 없습니다.") from exc
    except ReviewConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Review가 다른 사용자에 의해 변경되었습니다.",
        ) from exc
