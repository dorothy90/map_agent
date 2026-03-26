"""
Pydantic models for SSE events and REST responses.
React 프론트엔드가 채팅/아티팩트를 분리 렌더링할 수 있도록 타입별 이벤트 정의.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: str
    resume_value: str | None = None  # interrupt resume용


# ── SSE Event Types ──────────────────────────────────────

class ArtifactType(str, Enum):
    html = "html"
    image = "image"
    markdown = "markdown"


class StreamStartEvent(BaseModel):
    type: Literal["stream_start"] = "stream_start"
    session_id: str
    query: str


class NodeCompleteEvent(BaseModel):
    type: Literal["node_complete"] = "node_complete"
    node: str
    step: int = 0
    elapsed: float = 0.0


class MessageEvent(BaseModel):
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    agent: str
    content: str
    step: int = 0


class ArtifactEvent(BaseModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_type: ArtifactType
    mime: str = "text/html"
    title: str = ""
    agent: str = ""
    data: str = ""
    step: int = 0


class SuggestionEvent(BaseModel):
    type: Literal["suggestion"] = "suggestion"
    content: str
    step: int = 0


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    content: str
    agent: str = ""
    node: str = ""


class ThinkingEvent(BaseModel):
    type: Literal["thinking"] = "thinking"
    content: str
    agent: str = ""
    node: str = ""


class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    message: str
    node: str = ""


class StreamEndEvent(BaseModel):
    type: Literal["stream_end"] = "stream_end"
    total_steps: int = 0
    elapsed: float = 0.0


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str
    node: str = ""


class InterruptEvent(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    param: str          # 누락된 파라미터명
    message: str        # 사용자에게 보여줄 한국어 메시지
    route: str = ""     # 대상 에이전트


# ── REST Response Models (session history) ───────────────

class ArtifactData(BaseModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_type: ArtifactType
    mime: str = "text/html"
    title: str = ""
    agent: str = ""
    data: str = ""


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    agent: str = ""
    content: str = ""
    artifacts: list[ArtifactData] = []
    suggestion: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SessionHistory(BaseModel):
    session_id: str
    turns: list[HistoryMessage] = []


class SessionSummary(BaseModel):
    session_id: str
    last_query: str = ""
    turn_count: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)
