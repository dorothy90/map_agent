"""
Pydantic models for SSE events and REST responses.
React 프론트엔드가 채팅/아티팩트를 분리 렌더링할 수 있도록 타입별 이벤트 정의.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: str
    # interrupt resume — structured HITL contract: a {slot: value} dict (React form /
    # e2e). A bare str is the degraded Streamlit fallback (fills only the first slot).
    resume_value: str | dict[str, Any] | None = None


# ── SSE Event Types ──────────────────────────────────────

class ArtifactType(str, Enum):
    html = "html"
    image = "image"
    markdown = "markdown"
    pptx = "pptx"


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
    interrupt_type: str = "missing_param"  # "missing_param" | "plan_review" | HITL issue type
    param: str          # 대표 슬롯명(관측성/하위호환). batch에선 첫 슬롯일 뿐 — 권위는 fields.
    message: str        # 사용자에게 보여줄 한국어 메시지(Streamlit fallback 표시용)
    route: str = ""     # 대상 에이전트
    options: list[dict[str, Any]] = Field(default_factory=list)
    # Structured HITL contract: the missing slots to collect in ONE interrupt.
    # Each: {slot, label, type, required_any_group?, validation_hint?}. The source of
    # truth for "what is missing" — never infer "only this slot" from `param` in batch.
    fields: list[dict[str, Any]] = Field(default_factory=list)


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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionHistory(BaseModel):
    session_id: str
    turns: list[HistoryMessage] = []


class SessionSummary(BaseModel):
    session_id: str
    last_query: str = ""
    turn_count: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
