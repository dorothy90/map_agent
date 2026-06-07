"""
Pydantic models for SSE events and REST responses.
React 프론트엔드가 채팅/아티팩트를 분리 렌더링할 수 있도록 타입별 이벤트 정의.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class OptionItem(BaseModel):
    """interrupt 선택지 한 개.

    - label: 프론트 화면에 보여줄 텍스트
    - value: 사용자가 선택했을 때 백엔드(resume_value)로 돌려보낼 값

    하위호환: 'PT1H' 같은 단순 문자열을 넣으면 {label, value} 둘 다 그 값으로
    자동 변환한다. 따라서 기존 `options=['PT1H', 'PT2C']` 코드도 그대로 동작한다.
    """
    label: str
    value: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_str(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"label": data, "value": data}
        return data


class InterruptEvent(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    interrupt_type: str = "missing_param"  # "missing_param" | "plan_review" | HITL issue type
    param: str          # 누락된 파라미터명
    message: str        # 사용자에게 보여줄 한국어 메시지
    route: str = ""     # 대상 에이전트
    options: list[OptionItem] = Field(default_factory=list)


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
