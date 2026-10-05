from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptanceItem(Contract):
    id: str
    description: str


class GoalContract(Contract):
    original_request: str
    revision: int = 1
    acceptance_items: list[AcceptanceItem] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    user_inputs: list[dict] = Field(default_factory=list)


class ResultTable(Contract):
    table_id: str = Field(min_length=1)
    title: str
    rows: list[dict]
    columns: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    complete: bool
    missing_reason: str | None = None


class TableRef(Contract):
    table_id: str
    title: str
    columns: list[str]
    total_rows: int
    preview_rows: list[dict]
    units: dict[str, str] = Field(default_factory=dict)
    complete: bool
    missing_reason: str | None = None
    data_ref: str


class ToolObservation(Contract):
    schema_version: str = "harness-observation/v2"
    tables: list[TableRef] = Field(default_factory=list)
    principal_id: str
    session_id: str
    run_id: str
    invocation_id: str
    result_id: str
    tool_name: str
    tool_version: str = "1"
    validated_arguments: dict = Field(default_factory=dict)
    status: Literal["success", "partial", "empty", "error", "cancelled"] = "success"
    summary: str = ""
    columns: list[str] = Field(default_factory=list)
    preview_rows: list[dict] = Field(default_factory=list)
    total_rows: int = 0
    truncated: bool = False
    data_ref: str = ""
    artifact_refs: list[dict] = Field(default_factory=list)
    source_result_ids: list[str] = Field(default_factory=list)
    scope: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    error: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def adapt_legacy_table(cls, value):
        if isinstance(value, dict) and "tables" not in value and value.get("data_ref"):
            value = dict(value)
            complete = value.get("status", "success") in ("success", "empty")
            value["tables"] = [{"table_id": "default", "title": value.get("tool_name", "Result"),
                "columns": value.get("columns", []), "total_rows": value.get("total_rows", 0),
                "preview_rows": value.get("preview_rows", []), "data_ref": value["data_ref"],
                "complete": complete, "missing_reason": None if complete else "Legacy result is partial or unsuccessful"}]
        return value

    def model_view(self):
        """Keep audit identifiers and repeated tool inputs out of model context."""
        payload = self.model_dump(mode="json", include={"result_id", "tool_name", "status", "summary", "columns", "preview_rows", "total_rows", "truncated", "artifact_refs", "source_result_ids", "scope", "error"})
        payload["preview_rows"] = payload["preview_rows"][:10]
        payload["truncated"] = self.truncated or len(self.preview_rows) > 10
        payload["tables"] = [{**table.model_dump(mode="json", exclude={"data_ref"}),
            "preview_rows": [] if self.status == "error" else table.preview_rows[:10],
            "truncated": table.total_rows > min(len(table.preview_rows), 10)} for table in self.tables]
        # A single table is already represented by the legacy preview fields.
        if len(self.tables) == 1:
            payload["tables"][0].pop("preview_rows")
        payload["data_origin"] = self.provenance.get("data_origin", "unknown")
        payload["summary"] = self.summary[:1200]
        if self.status == "error":
            payload["preview_rows"] = []
            if self.error:
                payload["error"] = {k: str(v)[:1200] if isinstance(v, str) else v for k, v in self.error.items()}
        payload["artifact_count"] = len(self.artifact_refs)
        payload["artifact_refs"] = self.artifact_refs[:5]
        return payload


class FinalCandidate(Contract):
    answer: str
    result_ids: list[str] = Field(default_factory=list, max_length=10, description="답변에 사용한 저장된 조회·계산 결과. 표와 원본 링크는 시스템이 표시한다.")
    scope: dict[str, Any] = Field(default_factory=dict, description="이 답변의 제품·기간 등 조회 조건. 원본 또는 계산 입력의 scope와 같은 키를 사용한다.")
    limitations: list[str] = Field(default_factory=list)


class CompletionReview(Contract):
    action: Literal["finish", "continue", "ask_user"]
    complete: bool = False
    issues: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list, max_length=10)


class Verification(Contract):
    accepted: bool
    complete: bool
    issues: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list, max_length=10, description="제공된 observations 중 이 답변에 필요한 출처 ID. 자료를 사용하지 않는 일반 대화는 빈 배열.")


class AskUser(Contract):
    message: str
    fields: list[dict] = Field(default_factory=list)


class Worklog(Contract):
    acceptance_items: list[AcceptanceItem] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict, description="사용자가 지정한 제품·기간 등 도구 인자와 같은 키의 제약. 여러 허용 값은 배열.")
    facts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class LoadTools(Contract):
    names: list[str] = Field(min_length=1, max_length=4, description="이번 조사 단계에 필요한 도구 이름. 상세 스키마를 이 목록으로 교체한다.")


class SelectResults(Contract):
    result_ids: list[str] = Field(max_length=10, description="현재 답변에 읽어야 하는 결과 ID 목록. 이전 선택을 교체하며, 빈 배열이면 본문을 모두 제외한다. 원본은 계속 저장된다.")


class RunState(TypedDict, total=False):
    run_id: str
    principal_id: str
    session_id: str
    goal: dict
    messages: list
    observations: list[dict]
    worklog: dict
    pending: list[dict]
    question: dict
    candidate: dict
    status: str
    answer: str
    stop_reason: str
    answer_text: str
    validation_issues: list[dict]
    corrections: int
    summary: str
    loaded_tools: list[str]
    loaded_skills: dict
    finalizing: bool
    force_finalize: bool
    user_profile: str
    result_ids: list[str]
    focus_result_ids: list[str]
    active_result_ids: list[str]
    context_archives: list[str]
    completion_review: dict
    review_output_tokens: int
    review_input_tokens: int
    evidence_tokens: int
