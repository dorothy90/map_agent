"""
Typed result contracts for agent-to-agent handoff.

This module defines the machine-readable observation payload that agents can
attach to AIMessage.additional_kwargs["result"]. It is intentionally separate
from models.py, which contains API/SSE response models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Any, Literal, TypedDict
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from local_trace import emit_trace_event, summarize_result_envelope


RESULT_ENVELOPE_SCHEMA_VERSION = "result-envelope/v1"
SUPPORTED_RESULT_ENVELOPE_SCHEMA_VERSIONS = frozenset({RESULT_ENVELOPE_SCHEMA_VERSION})
CONTRACT_MODEL_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)
AGENT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_RECENT_RESULTS = 10  # 축3: resolver index depth (display stays [-3:]; resolution uses K)
MAX_RECENT_RESULT_ROWS = 50
MAX_RESULT_ENVELOPE_ROWS = 50
MAX_RECENT_RESULT_STRING_CHARS = 500
MAX_RECENT_ARTIFACT_REFS = 10
RECENT_RESULT_PAYLOAD_KEYS = frozenset({"data", "content", "payload"})
RECENT_RESULT_PAYLOAD_KEY_PARTS = ("html", "base64", "bytes", "image")
_DROP_RECENT_VALUE = object()


class ResultContractError(ValueError):
    """Raised when a result payload violates the ResultEnvelope contract."""


class ResultKind(str, Enum):
    table = "table"
    summary = "summary"
    document = "document"
    image = "image"
    report = "report"
    artifact = "artifact"
    unknown = "unknown"


class ResultStatus(str, Enum):
    success = "success"
    partial = "partial"
    empty = "empty"
    error = "error"
    invalid = "invalid"


class ColumnRole(str, Enum):
    dimension = "dimension"
    metric = "metric"
    identifier = "identifier"
    timestamp = "timestamp"
    text = "text"
    unknown = "unknown"


class SemanticType(str, Enum):
    lot_id = "lot_id"
    product = "product"
    wafer_id = "wafer_id"
    parameter = "parameter"
    map = "map"
    fail_type = "fail_type"
    process = "process"
    cause_oper = "cause_oper"
    map_oper = "map_oper"
    count = "count"
    date = "date"
    document_id = "document_id"
    artifact_id = "artifact_id"
    unknown = "unknown"


def _ensure_json_compatible(value: Any, path: str) -> None:
    """Keep envelope payloads checkpoint-safe and API-serializable."""

    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _ensure_json_compatible(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} must use string object keys")
            _ensure_json_compatible(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains non-JSON value: {type(value).__name__}")


def _is_payload_like_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in RECENT_RESULT_PAYLOAD_KEYS or any(
        part in normalized for part in RECENT_RESULT_PAYLOAD_KEY_PARTS
    )


def _is_payload_like_string(value: str) -> bool:
    stripped = value.lstrip()
    head = stripped[:256].lower()
    if head.startswith(("data:image/", "data:application/", "<!doctype html", "<html", "<svg")):
        return True
    if "<body" in head or "<img" in head:
        return True
    compact = re.sub(r"\s+", "", stripped)
    known_payload_prefixes = ("ivbor", "/9j/", "r0lgod", "jvber", "uesdb")
    if len(compact) >= 512 and compact[:16].lower().startswith(known_payload_prefixes):
        return True
    if len(compact) >= 4096 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact):
        return True
    return False


def _sanitize_recent_index_value(value: Any) -> Any:
    """Remove payload-like fields before storing a resolver index copy."""

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if not isinstance(key, str) or _is_payload_like_key(key):
                continue
            sanitized_item = _sanitize_recent_index_value(item)
            if sanitized_item is not _DROP_RECENT_VALUE:
                sanitized[str(key)] = sanitized_item
        return sanitized
    if isinstance(value, list):
        return [
            item
            for item in (_sanitize_recent_index_value(item) for item in value)
            if item is not _DROP_RECENT_VALUE
        ]
    if isinstance(value, str):
        if _is_payload_like_string(value):
            return _DROP_RECENT_VALUE
        if len(value) > MAX_RECENT_RESULT_STRING_CHARS:
            return value[:MAX_RECENT_RESULT_STRING_CHARS]
    return value


def _unique_non_empty(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _as_entity_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _sanitize_result_row_value(value: Any) -> Any:
    sanitized = _sanitize_recent_index_value(value)
    if sanitized is _DROP_RECENT_VALUE:
        return None
    return sanitized


def _sanitize_result_rows(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if isinstance(rows, dict):
        iterable = [rows]
    elif isinstance(rows, str | bytes):
        iterable = [{"value": rows.decode(errors="replace") if isinstance(rows, bytes) else rows}]
    else:
        try:
            iterable = list(rows)
        except TypeError:
            iterable = [{"value": rows}]

    result: list[dict[str, Any]] = []
    for item in iterable[:MAX_RESULT_ENVELOPE_ROWS]:
        if not isinstance(item, dict):
            item = {"value": item}
        sanitized = _sanitize_result_row_value(item)
        if isinstance(sanitized, dict):
            result.append(sanitized)
    return result


def _semantic_for_column(name: str) -> str:
    normalized = name.lower()
    if normalized in {"lot_id", "lotid", "lot"}:
        return SemanticType.lot_id.value
    if normalized in {"lot_cd", "lotcd", "product"}:
        return SemanticType.product.value
    if normalized in {"wf_id", "wfid", "wafer_id"}:
        return SemanticType.wafer_id.value
    if normalized in {"parameter", "param", "anomaly_param"}:
        return SemanticType.parameter.value
    if normalized in {"fail_type", "dh_fail_type"}:
        return SemanticType.fail_type.value
    if normalized in {"cause_oper", "dh_cause_oper", "oper", "map_oper", "process"}:
        return SemanticType.process.value
    if normalized in {"doc_id", "document_id"}:
        return SemanticType.document_id.value
    if normalized in {"artifact_id"}:
        return SemanticType.artifact_id.value
    if "date" in normalized or normalized.endswith("_tm") or normalized.endswith("_time"):
        return SemanticType.date.value
    if normalized.endswith("_count") or normalized in {"count", "cnt"}:
        return SemanticType.count.value
    return SemanticType.unknown.value


def _role_for_column(name: str, sample_value: Any) -> str:
    semantic = _semantic_for_column(name)
    if semantic in {
        SemanticType.lot_id.value,
        SemanticType.product.value,
        SemanticType.wafer_id.value,
        SemanticType.document_id.value,
        SemanticType.artifact_id.value,
    }:
        return ColumnRole.identifier.value
    if semantic == SemanticType.date.value:
        return ColumnRole.timestamp.value
    if isinstance(sample_value, int | float):
        return ColumnRole.metric.value
    if semantic != SemanticType.unknown.value:
        return ColumnRole.dimension.value
    return ColumnRole.unknown.value


def infer_result_columns(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Infer lightweight column metadata from adapter rows."""

    sample_by_name: dict[str, Any] = {}
    for row in rows:
        for key, value in row.items():
            sample_by_name.setdefault(key, value)
    return [
        {
            "name": name,
            "role": _role_for_column(name, sample),
            "semantic": _semantic_for_column(name),
        }
        for name, sample in sample_by_name.items()
    ]


def _artifact_refs_from_artifacts(
    artifacts: list[dict[str, Any]] | None,
    source_agent: str,
    result_id: str,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for i, artifact in enumerate(artifacts or [], start=1):
        if not isinstance(artifact, dict):
            continue
        title = str(artifact.get("title") or f"artifact_{i}")
        artifact_id = str(artifact.get("artifact_id") or artifact.get("id") or f"{result_id}:artifact:{i}")
        refs.append({
            "artifact_id": artifact_id,
            "title": title,
            "artifact_type": str(artifact.get("type") or artifact.get("artifact_type") or ""),
            "semantic": str(artifact.get("semantic") or SemanticType.unknown.value),
            "mime": str(artifact.get("mime") or ""),
            "agent": str(artifact.get("agent") or source_agent),
        })
    return refs


def _value_for_any_key(row: dict[str, Any], names: set[str]) -> Any:
    for key, value in row.items():
        if str(key).strip().lower() in names:
            return value
    return None


def _first_existing_key(rows: list[dict[str, Any]], names: set[str]) -> str:
    for row in rows:
        for key in row:
            if str(key).strip().lower() in names:
                return str(key)
    return ""


def extract_parameter_values(rows: list[dict[str, Any]]) -> list[str]:
    """Extract parameter/fail_type values from tabular rows without inference."""

    values: list[Any] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        values.append(_value_for_any_key(row, {"parameter", "param", "anomaly_param", "fail_type", "dh_fail_type"}))
    return _unique_non_empty(values)


def _format_count(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def derive_summary_from_rows(
    *,
    source_agent: str,
    rows: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    fallback: str = "",
    title: str = "",
    sort_direction: str = "desc",
) -> str:
    """Build a grounded user-facing summary from structured result data.

    LLM prose is allowed as fallback only. When rows/artifacts are present,
    this function derives the summary from those structures so message content,
    envelope summary, and follow-up references speak about the same facts.
    """

    rows = [row for row in rows or [] if isinstance(row, dict)]
    artifact_count = len(artifacts or [])
    source = source_agent.lower()

    if source == "wads_agent" and rows:
        param_key = _first_existing_key(rows, {"parameter", "param", "fail_type", "dh_fail_type"})
        count_key = _first_existing_key(rows, {"detection_count", "detected_count", "count", "cnt"})
        if param_key and count_key:
            def count_value(row: dict[str, Any]) -> float | None:
                value = row.get(count_key)
                if value in (None, ""):
                    return None
                if isinstance(value, int | float):
                    return float(value)
                try:
                    return float(str(value).replace(",", "").strip())
                except (TypeError, ValueError):
                    return None

            direction = str(sort_direction).lower()
            if direction in {"asc", "desc"}:
                ranked_rows = sorted(
                    rows,
                    key=lambda row: (
                        count_value(row) is None,
                        count_value(row)
                        if direction == "asc"
                        else -(count_value(row) or 0.0),
                        str(row.get(param_key) or ""),
                    ),
                )
            else:
                ranked_rows = rows
            top_parts = [
                f"{row.get(param_key)} {_format_count(row.get(count_key))}건"
                for row in ranked_rows[:3]
                if row.get(param_key) not in (None, "") and row.get(count_key) not in (None, "")
            ]
            if top_parts:
                label = (
                    "하위"
                    if direction == "asc"
                    else "상위"
                    if direction == "desc"
                    else "주요"
                )
                return (
                    f"WADS SQL 쿼리 결과 총 {len(rows)}건이 조회되었습니다. "
                    f"검출 건수 기준 {label} 파라미터는 {', '.join(top_parts)} 순입니다."
                )
        if param_key:
            params = _unique_non_empty(
                [row.get(param_key) for row in rows if row.get(param_key) not in (None, "")]
            )[:3]
            if params:
                return f"WADS 조회 결과 총 {len(rows)}건이 조회되었습니다. 주요 파라미터는 {', '.join(params)}입니다."
        return f"WADS 조회 결과 총 {len(rows)}건이 조회되었습니다."

    if source == "fail_history_agent":
        if rows:
            doc_ids = _unique_non_empty([
                _value_for_any_key(row, {"doc_id", "document_id"})
                for row in rows
                if isinstance(row, dict)
            ])
            suffix = f" 인용 문서 {len(doc_ids)}건을 포함합니다." if doc_ids else ""
            return f"불량이력 검색 결과 총 {len(rows)}건이 조회되었습니다.{suffix}"
        return fallback or "조건에 맞는 불량이력이 없습니다."

    if source == "lot_history_agent":
        if rows:
            first = rows[0]
            lot_id = _value_for_any_key(first, {"lot_id", "lotid", "lot"})
            if lot_id:
                return f"LOT 이력 조회 결과 총 {len(rows)}개 LOT이 조회되었습니다. 첫 번째 LOT은 {lot_id}입니다."
            return f"LOT 이력 조회 결과 총 {len(rows)}개 LOT이 조회되었습니다."
        return fallback or "LOT 이력 조회 결과가 없습니다."

    if source == "map_agent":
        if artifact_count:
            row = rows[0] if rows else {}
            map_type = row.get("map_type", "map") if isinstance(row, dict) else "map"
            png_count = row.get("png_count", artifact_count) if isinstance(row, dict) else artifact_count
            return f"{map_type} 결과 artifact {artifact_count}건이 생성되었습니다. PNG {png_count}건을 포함합니다."
        return fallback or "조건에 맞는 wafer map을 생성하지 못했습니다."

    if source == "yield_agent":
        if rows:
            return fallback or f"Yield 조회 결과 총 {len(rows)}건이 조회되었습니다."
        return fallback or "Yield 조회 결과가 없습니다."

    if rows:
        label = title or source_agent
        return fallback or f"{label} 결과 총 {len(rows)}건이 조회되었습니다."
    if artifact_count:
        label = title or source_agent
        return fallback or f"{label} artifact {artifact_count}건이 생성되었습니다."
    return fallback


def _summary_mentions_top_parameter(summary: str, top_parameter: str) -> bool:
    if not summary or not top_parameter:
        return False
    normalized_summary = summary.upper()
    normalized_param = top_parameter.upper()
    return normalized_param in normalized_summary


def validate_result_consistency(envelope: dict[str, Any], *, message_content: Any = "") -> list[dict[str, Any]]:
    """Return non-blocking consistency warnings for an agent result envelope."""

    warnings: list[dict[str, Any]] = []
    source_agent = str(envelope.get("source_agent") or "")
    rows = envelope.get("rows") or []
    summary = str(envelope.get("summary") or "")
    metadata = envelope.get("metadata") or {}
    artifact_refs = envelope.get("artifact_refs") or []

    if isinstance(metadata, dict) and "row_count" in metadata and isinstance(rows, list):
        try:
            expected = int(metadata.get("row_count") or 0)
        except (TypeError, ValueError):
            expected = -1
        truncated_by_contract = expected > len(rows) and len(rows) == MAX_RESULT_ENVELOPE_ROWS
        if expected != -1 and expected != len(rows) and not truncated_by_contract:
            warnings.append({
                "reason": "metadata_row_count_mismatch",
                "metadata_row_count": expected,
                "envelope_row_count": len(rows),
            })

    if isinstance(metadata, dict) and "artifact_count" in metadata:
        try:
            expected_artifacts = int(metadata.get("artifact_count") or 0)
        except (TypeError, ValueError):
            expected_artifacts = -1
        if expected_artifacts != -1 and expected_artifacts != len(artifact_refs):
            warnings.append({
                "reason": "metadata_artifact_count_mismatch",
                "metadata_artifact_count": expected_artifacts,
                "artifact_ref_count": len(artifact_refs),
            })

    if source_agent == "wads_agent" and isinstance(rows, list) and rows:
        param_key = _first_existing_key(rows, {"parameter", "param", "fail_type", "dh_fail_type"})
        if param_key:
            top_parameter = str(rows[0].get(param_key) or "")
            if top_parameter and summary and not _summary_mentions_top_parameter(summary, top_parameter):
                warnings.append({
                    "reason": "summary_missing_top_parameter",
                    "top_parameter": top_parameter,
                })

    content = str(message_content or "")
    if summary and content and source_agent in {"wads_agent", "lot_history_agent", "map_agent"} and summary not in content:
        warnings.append({
            "reason": "message_content_differs_from_envelope_summary",
        })

    return warnings


class ResultColumn(BaseModel):
    """Column metadata for tabular result rows."""

    model_config = CONTRACT_MODEL_CONFIG

    name: str = Field(min_length=1)
    role: ColumnRole = ColumnRole.unknown
    semantic: SemanticType = SemanticType.unknown
    label: str = ""
    unit: str = ""


class ResultEntitySet(BaseModel):
    """Canonical entities extracted from a result."""

    model_config = CONTRACT_MODEL_CONFIG

    lot_ids: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    wafer_ids: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    fail_types: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    cause_opers: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class ArtifactRef(BaseModel):
    """Metadata-only reference to a UI artifact.

    Raw artifact payloads such as HTML, image base64, or PPTX bytes must stay in
    the artifact channel. They are intentionally not part of this model.
    """

    model_config = CONTRACT_MODEL_CONFIG

    artifact_id: str = Field(min_length=1)
    title: str = ""
    artifact_type: str = ""
    semantic: SemanticType = SemanticType.unknown
    mime: str = ""
    agent: str = ""


class ResultProvenance(BaseModel):
    """Where the result came from and how it should be ordered in the UI."""

    model_config = CONTRACT_MODEL_CONFIG

    task_id: str = ""
    task_goal: str = ""
    source_tool: str = ""
    source_query: str = ""
    display_order: int = Field(default=0, ge=0)
    turn_index: int = Field(default=0, ge=0)


class RecentArtifactRef(BaseModel):
    """Payload-free artifact pointer for recent_results."""

    model_config = CONTRACT_MODEL_CONFIG

    artifact_ref: str = Field(min_length=1)
    title: str = ""
    kind: str = ""
    semantic: SemanticType = SemanticType.unknown
    result_id: str = Field(min_length=1)
    source_agent: str = Field(min_length=1, pattern=AGENT_KEY_PATTERN.pattern)
    created_at: str = Field(min_length=1)


class RecentResultIndexEntry(BaseModel):
    """Bounded, non-canonical resolver index derived from ResultEnvelopeV1.

    The full ResultEnvelope remains in AIMessage.additional_kwargs["result"].
    This model stores only compact lookup data and payload-free artifact refs.
    It is a lookup hint, not a source of truth for final answers.
    """

    model_config = CONTRACT_MODEL_CONFIG

    result_id: str = Field(min_length=1)
    source_agent: str = Field(min_length=1, pattern=AGENT_KEY_PATTERN.pattern)
    kind: ResultKind
    title: str = ""
    created_at: str = Field(min_length=1)
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[RecentArtifactRef] = Field(default_factory=list)
    # carry-both (i): per-report rows (html-stripped) for report-ordinal (#RN) resolution,
    # carried separately from `rows` (which stays per-wafer for #N and planner context).
    reports: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("rows")
    @classmethod
    def rows_must_be_bounded_json_objects(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(rows) > MAX_RECENT_RESULT_ROWS:
            raise ValueError(f"rows must contain at most {MAX_RECENT_RESULT_ROWS} rows")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"rows[{i}] must be an object")
            _ensure_json_compatible(row, f"rows[{i}]")
        return rows

    @field_validator("reports")
    @classmethod
    def reports_must_be_bounded_json_objects(cls, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(reports) > MAX_RECENT_RESULT_ROWS:
            raise ValueError(f"reports must contain at most {MAX_RECENT_RESULT_ROWS} reports")
        for i, report in enumerate(reports):
            if not isinstance(report, dict):
                raise ValueError(f"reports[{i}] must be an object")
            _ensure_json_compatible(report, f"reports[{i}]")
        return reports


class Followup(TypedDict, total=False):
    """선언적 후속 체이닝 지시 (S2). 에이전트가 자기 결과 envelope에 첨부하면
    supervisor 측 `_propose_followups`가 읽어 pending/task_plan에 후속을 추가하고,
    `_resolve_followup_or_drop`가 dispatch 직전 confirm/choice interrupt로 해소한다.

    트리거 판단(이상감지/검출수/main_oper 산출/group 산출)은 더 이상 supervisor가
    state를 뒤져서 하지 않고, 결과를 만든 에이전트가 선언한다. 코드는 의미 해석을 하지
    않고 이 선언을 기계적으로 enqueue/resolve 한다.

    필드는 모두 선택(total=False). 두 모드:
    - confirm: `agent`(구체 타깃)+`default_slots`로 후속 task를 바로 만들고 `confirm`이 True면
      dispatch 직전 yes/no interrupt. (yield→wads, wt_resp→mining)
    - choice : `agent="__choice__"`. dispatch 직전 택1 interrupt 후 구체 task로 치환.
      generic resolver(`_resolve_single_choice`)가 옵션 스펙만 보고 기계적으로 해소한다.
      옵션·데이터(per-report 그룹 등)는 결과 생성 에이전트가 선언한다(트리거+데이터 소유).
      · 1-step: `choice_options[{label,value,slots}]` + `choice_target_agent`(택1 → 그 agent build).
      · 2-step: `prefilter_options`(예: WADS report별 fail_type)로 먼저 한 번 좁힌 뒤,
        그 선택지(value=인덱스)에 대응하는 `choice_option_sets[idx]`를 택1 메뉴로 제시한다.
        per-option `agent`로 선택지마다 다른 타깃을 build할 수 있다.
    """

    agent: str                        # confirm: 구체 타깃 agent / choice: "__choice__"
    goal: str
    default_slots: dict[str, Any]     # 상류 결과/ctx에서 채운 기본 슬롯
    confirm: bool                     # dispatch 직전 확인 interrupt?
    confirm_message: str
    choice_kind: str                  # "single" (택1 후속 태그; resolver는 옵션 스펙으로 분기)
    choice_message: str               # 2차(또는 1-step) 택1 메뉴 질문
    choice_target_agent: str          # 1-step: 선택마다 build할 agent (per-option agent 없을 때 fallback)
    choice_options: list[dict[str, Any]]  # 1-step: [{label, value, slots}] (+ none)
    prefilter_message: str            # 2-step 1차(좁히기) 질문
    prefilter_options: list[dict[str, Any]]  # 2-step 1차: [{label, value=str(idx), fail_type?}] (+ none)
    choice_option_sets: list[list[dict[str, Any]]]  # 2-step 2차: prefilter value(idx)별 옵션 [{label,value,agent,slots,goal}]
    guard_key: str                    # 턴당 1회 가드 state 플래그명 ("" = 없음)
    guard_agents: list[str]           # 이 agent들이 이미 plan에 있으면 제안 안 함(중복 차단)


class ResultEnvelopeV1(BaseModel):
    """Versioned machine-readable result payload.

    This is the only supported top-level shape for structured agent results.
    Agent-specific data belongs under extensions[source_agent]. Downstream
    orchestration should prefer columns, rows, entities, provenance, and
    artifact_refs; extensions are not a cross-agent contract.
    """

    model_config = CONTRACT_MODEL_CONFIG

    schema_version: Literal["result-envelope/v1"]
    result_id: str = Field(default_factory=lambda: f"result_{uuid.uuid4().hex}")
    source_agent: str = Field(min_length=1, pattern=AGENT_KEY_PATTERN.pattern)
    kind: ResultKind
    status: ResultStatus
    title: str = ""
    summary: str = ""
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    entities: ResultEntitySet = Field(default_factory=ResultEntitySet)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    provenance: ResultProvenance = Field(default_factory=ResultProvenance)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    extensions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # S2: 선언적 후속 체이닝 지시. 기존 envelope는 빈 list 기본값으로 하위호환.
    followups: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("columns")
    @classmethod
    def column_names_must_be_unique(cls, columns: list[ResultColumn]) -> list[ResultColumn]:
        names = [column.name for column in columns]
        if len(names) != len(set(names)):
            raise ValueError("columns must have unique names")
        return columns

    @field_validator("rows")
    @classmethod
    def rows_must_be_plain_objects(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"rows[{i}] must be an object")
            _ensure_json_compatible(row, f"rows[{i}]")
        return rows

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def artifact_refs_must_not_include_payload(cls, refs: Any) -> Any:
        if refs is None:
            return []
        if isinstance(refs, list):
            forbidden = {"data", "html", "base64", "bytes", "content"}
            for i, ref in enumerate(refs):
                if isinstance(ref, dict):
                    found = forbidden & set(ref)
                    if found:
                        raise ValueError(
                            f"artifact_refs[{i}] contains payload fields: {sorted(found)}"
                        )
        return refs

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json_compatible(cls, value: Any) -> Any:
        _ensure_json_compatible(value, "metadata")
        return value

    @field_validator("followups")
    @classmethod
    def followups_must_be_json_objects(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"followups[{i}] must be an object")
            _ensure_json_compatible(item, f"followups[{i}]")
        return value

    @field_validator("extensions")
    @classmethod
    def extensions_must_be_namespaced_json(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for key in value:
            if not AGENT_KEY_PATTERN.fullmatch(key):
                raise ValueError(f"extensions key must be an agent namespace: {key!r}")
        _ensure_json_compatible(value, "extensions")
        return value


def validate_result_envelope(payload: Any) -> ResultEnvelopeV1:
    """Validate an arbitrary payload as a ResultEnvelopeV1.

    Raises:
        ResultContractError: if the payload does not match the typed contract.
    """

    if isinstance(payload, ResultEnvelopeV1):
        return payload
    if not isinstance(payload, dict):
        raise ResultContractError("ResultEnvelope payload must be an object")

    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_RESULT_ENVELOPE_SCHEMA_VERSIONS:
        raise ResultContractError(f"Unsupported ResultEnvelope schema_version: {schema_version!r}")

    try:
        return ResultEnvelopeV1.model_validate(payload)
    except ValidationError as exc:
        raise ResultContractError(str(exc)) from exc


def dump_result_envelope(payload: ResultEnvelopeV1 | dict[str, Any]) -> dict[str, Any]:
    """Return a validated serializable dict for AIMessage.additional_kwargs."""

    envelope = payload if isinstance(payload, ResultEnvelopeV1) else validate_result_envelope(payload)
    return envelope.model_dump(mode="json")


def build_result_envelope(
    *,
    source_agent: str,
    kind: str,
    status: str = ResultStatus.success.value,
    title: str = "",
    summary: str = "",
    rows: Any = None,
    columns: list[dict[str, Any]] | None = None,
    entities: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    provenance: dict[str, Any] | None = None,
    metadata: dict[str, str | int | float | bool | None] | None = None,
    extensions: dict[str, dict[str, Any]] | None = None,
    followups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build and validate a ResultEnvelope for agent adapters.

    The adapter copy stores at most MAX_RESULT_ENVELOPE_ROWS rows and only
    payload-free artifact references. UI artifact bodies remain in *_artifacts.
    """

    result_id = f"result_{uuid.uuid4().hex}"
    sanitized_rows = _sanitize_result_rows(rows)
    refs = list(artifact_refs or [])
    refs.extend(_artifact_refs_from_artifacts(artifacts, source_agent, result_id))
    entity_payload = dict(entities or {})
    for key in (
        "lot_ids",
        "products",
        "wafer_ids",
        "parameters",
        "fail_types",
        "processes",
        "cause_opers",
        "doc_ids",
        "artifact_ids",
    ):
        if key in entity_payload:
            entity_payload[key] = _unique_non_empty(_as_entity_values(entity_payload.get(key)))
    if refs:
        entity_payload["artifact_ids"] = _unique_non_empty(
            list(entity_payload.get("artifact_ids") or []) + [ref.get("artifact_id") for ref in refs]
        )
    payload = {
        "schema_version": RESULT_ENVELOPE_SCHEMA_VERSION,
        "result_id": result_id,
        "source_agent": source_agent,
        "kind": kind,
        "status": status,
        "title": title,
        "summary": summary[:MAX_RECENT_RESULT_STRING_CHARS] if isinstance(summary, str) else str(summary)[:MAX_RECENT_RESULT_STRING_CHARS],
        "columns": columns if columns is not None else infer_result_columns(sanitized_rows),
        "rows": sanitized_rows,
        "entities": entity_payload,
        "artifact_refs": refs,
        "provenance": provenance or {},
        "metadata": metadata or {},
        "extensions": extensions or {},
        "followups": followups or [],
    }
    return dump_result_envelope(payload)


def attach_result_envelope(message: Any, *, logger: Any | None = None, **kwargs: Any) -> Any:
    """Attach a validated ResultEnvelope to AIMessage.additional_kwargs["result"]."""

    source_agent = kwargs.get("source_agent", getattr(message, "name", "unknown"))
    try:
        envelope = build_result_envelope(**kwargs)
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs is None:
            message.additional_kwargs = {"result": envelope}
        else:
            additional_kwargs["result"] = envelope
        provenance = envelope.get("provenance") or {}
        consistency_warnings = validate_result_consistency(
            envelope,
            message_content=getattr(message, "content", ""),
        )
        for warning in consistency_warnings:
            emit_trace_event(
                "result_consistency_warning",
                source=str(source_agent),
                severity="warning",
                task_id=str(provenance.get("task_id") or ""),
                result_id=str(envelope.get("result_id") or ""),
                payload={
                    "source_agent": str(source_agent),
                    **warning,
                },
            )
            if logger is not None:
                logger.warning("[ResultConsistency] %s: %s", source_agent, warning)
        emit_trace_event(
            "agent_result_enveloped",
            source=str(source_agent),
            task_id=str(provenance.get("task_id") or ""),
            result_id=str(envelope.get("result_id") or ""),
            payload=summarize_result_envelope(envelope),
        )
    except Exception as exc:
        if logger is not None:
            logger.warning("[ResultEnvelope] %s validation failed: %s", source_agent, exc)
        emit_trace_event(
            "agent_result_enveloped",
            source=str(source_agent),
            severity="error",
            payload={
                "status": "validation_failed",
                "source_agent": str(source_agent),
                "error_type": type(exc).__name__,
            },
        )
    return message


def build_recent_result_index_entry(payload: ResultEnvelopeV1 | dict[str, Any]) -> dict[str, Any]:
    """Build a payload-free recent_results entry from a ResultEnvelope."""

    envelope = validate_result_envelope(payload)
    dumped = envelope.model_dump(mode="json")
    created_at = dumped["created_at"]
    rows = [
        _sanitize_recent_index_value(row)
        for row in dumped.get("rows", [])[:MAX_RECENT_RESULT_ROWS]
    ]
    artifact_refs = [
        {
            "artifact_ref": ref["artifact_id"],
            "title": ref.get("title", ""),
            "kind": ref.get("artifact_type", ""),
            "semantic": ref.get("semantic", SemanticType.unknown.value),
            "result_id": dumped["result_id"],
            "source_agent": dumped["source_agent"],
            "created_at": created_at,
        }
        for ref in dumped.get("artifact_refs", [])[:MAX_RECENT_ARTIFACT_REFS]
        if ref.get("artifact_id")
    ]
    # carry-both (i): surface the source agent's per-report rows (e.g. wads reports) so
    # report-ordinal (#RN) resolution can index the Nth report independently of `rows`.
    report_index = (dumped.get("extensions") or {}).get(dumped["source_agent"], {}).get("reports", [])
    reports = [r for r in report_index if isinstance(r, dict)][:MAX_RECENT_RESULT_ROWS]
    entry = RecentResultIndexEntry(
        result_id=dumped["result_id"],
        source_agent=dumped["source_agent"],
        kind=dumped["kind"],
        title=dumped.get("title", ""),
        created_at=created_at,
        columns=dumped.get("columns", []),
        rows=rows,
        artifact_refs=artifact_refs,
        reports=reports,
    )
    return entry.model_dump(mode="json")


def prune_recent_results(entries: list[Any]) -> list[dict[str, Any]]:
    """Normalize, dedupe, and bound recent_results for supervisor state.

    Accepts either full ResultEnvelope payloads or already-pruned index entries.
    The returned list keeps the newest occurrence per result_id and caps the
    index to MAX_RECENT_RESULTS.
    """

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, ResultEnvelopeV1) or (
            isinstance(entry, dict) and entry.get("schema_version")
        ):
            normalized.append(build_recent_result_index_entry(entry))
            continue

        index_entry = RecentResultIndexEntry.model_validate(entry)
        dumped = index_entry.model_dump(mode="json")
        dumped["rows"] = [
            _sanitize_recent_index_value(row)
            for row in dumped.get("rows", [])[:MAX_RECENT_RESULT_ROWS]
        ]
        dumped["artifact_refs"] = dumped.get("artifact_refs", [])[:MAX_RECENT_ARTIFACT_REFS]
        normalized.append(RecentResultIndexEntry.model_validate(dumped).model_dump(mode="json"))

    seen: set[str] = set()
    deduped_reversed: list[dict[str, Any]] = []
    for entry in reversed(normalized):
        result_id = entry["result_id"]
        if result_id in seen:
            continue
        seen.add(result_id)
        deduped_reversed.append(entry)

    result = list(reversed(deduped_reversed))[-MAX_RECENT_RESULTS:]
    dropped_count = max(0, len(normalized) - len(result))
    if dropped_count:
        emit_trace_event(
            "result_pruned",
            source="result_contracts",
            payload={
                "input_count": len(entries or []),
                "normalized_count": len(normalized),
                "output_count": len(result),
                "dropped_count": dropped_count,
                "max_results": MAX_RECENT_RESULTS,
                "max_rows": MAX_RECENT_RESULT_ROWS,
            },
        )
    return result
