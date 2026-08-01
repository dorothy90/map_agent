from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from models import CitationData, HistoryMessage, SessionHistory, SessionSummary


def citations_from_fail_history_results(
    results: list[dict[str, Any]] | None,
    *,
    source_paths: Mapping[str, str] | None = None,
) -> list[CitationData]:
    """Build citations only from structured Fail History result rows."""

    materialized_paths = source_paths or {}
    citations: list[CitationData] = []
    by_doc_id: dict[str, CitationData] = {}
    for result in results or []:
        if not isinstance(result, dict):
            continue
        doc_id = str(result.get("doc_id") or "").strip()
        if not doc_id:
            continue
        download_url = str(result.get("download_url") or "")
        existing = by_doc_id.get(doc_id)
        if existing:
            if not existing.download_url and download_url:
                existing.download_url = download_url
            continue
        citation = CitationData(
            doc_id=doc_id,
            label=doc_id,
            source_path=materialized_paths.get(doc_id),
            download_url=download_url,
        )
        citations.append(citation)
        by_doc_id[doc_id] = citation
    return citations


async def list_session_summaries(db) -> list[SessionSummary]:
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$session_id",
                "last_query": {"$first": "$query"},
                "turn_count": {"$sum": 1},
                "updated_at": {"$first": "$timestamp"},
            }
        },
        {"$sort": {"updated_at": -1}},
        {"$limit": 50},
    ]
    return [
        SessionSummary(
            session_id=doc["_id"],
            last_query=doc.get("last_query", ""),
            turn_count=doc.get("turn_count", 0),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        )
        async for doc in db.chat_turns.aggregate(pipeline)
    ]


async def load_session_history(db, session_id: str) -> SessionHistory:
    turns: list[HistoryMessage] = []
    cursor = db.chat_turns.find(
        {"session_id": session_id},
        {"_id": 0},
    ).sort("timestamp", 1)
    async for doc in cursor:
        timestamp = doc.get("timestamp", datetime.now(timezone.utc))
        turns.append(
            HistoryMessage(
                role="user",
                content=doc.get("query", ""),
                timestamp=timestamp,
            )
        )
        for message in doc.get("messages", []):
            message_data = dict(message)
            message_data["role"] = "assistant"
            message_data["timestamp"] = timestamp
            turns.append(HistoryMessage(**message_data))
    return SessionHistory(session_id=session_id, turns=turns)
