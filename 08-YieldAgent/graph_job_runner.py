"""HTTP-independent LangGraph execution and event translation."""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from langchain_core.messages import HumanMessage
from langgraph.types import Command, Overwrite

from artifact_context import drain_saved_refs, save_artifact
from artifact_store import ArtifactRef
from models import (
    ArtifactEvent,
    ArtifactType,
    InterruptEvent,
    MessageEvent,
    NodeCompleteEvent,
    StatusEvent,
    StreamEndEvent,
    StreamStartEvent,
    SuggestionEvent,
    ThinkingEvent,
    TokenEvent,
)
from settings import get_settings


logger = logging.getLogger("graph_job_runner")
Emit = Callable[[dict[str, Any]], Awaitable[None]]
Cancelled = Callable[[], Awaitable[bool]]
PersistArtifacts = Callable[[list[ArtifactRef]], Awaitable[list[ArtifactRef]]]


@dataclass(frozen=True)
class GraphRunRequest:
    job_id: str
    owner_id: str
    session_id: str
    thread_id: str
    query: str
    resume_value: str | dict[str, Any] | None = None


@dataclass(frozen=True)
class GraphRunResult:
    outcome: Literal["SUCCEEDED", "WAITING_INPUT", "CANCELLED"]
    latest_interrupt: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None


_AGENT_META: dict[str, tuple[str, int, int]] = {
    "yield_agent": ("PT1H/PT1C 수율 통계 조회 및 이상 감지", 5, 10),
    "wads_agent": ("WADS 열화 파라미터 리포트 조회", 10, 20),
    "map_agent": ("웨이퍼 불량 맵 시각화", 5, 10),
    "fail_history_agent": ("Fail 이력 OpenSearch 검색", 5, 10),
    "ppt_export": ("분석 결과 PPT 생성", 20, 30),
    "lot_history_agent": ("LOT 이력 조회", 10, 15),
    "relation_tree_agent": ("연관 LOT 관계 트리 분석", 10, 20),
}


def _event_dict(event: object) -> dict[str, Any]:
    return event.model_dump() if hasattr(event, "model_dump") else dict(event)


def _pending_interrupt_from_state(state_snapshot: object) -> dict[str, Any]:
    for task in getattr(state_snapshot, "tasks", []) or []:
        interrupts = getattr(task, "interrupts", []) or []
        if interrupts:
            value = getattr(interrupts[-1], "value", None)
            if isinstance(value, dict):
                return value
    return {}


def _build_plan_review_message(
    tasks: list[dict[str, Any]], missing_params: list[dict[str, Any]] | None = None
) -> str:
    missing_set = {m["param"] for m in (missing_params or [])}
    missing_task_ids = {m["task_id"] for m in (missing_params or [])}
    total_lo = total_hi = 0
    blocks: list[str] = []

    for i, task in enumerate(tasks, 1):
        agent = task.get("agent", "")
        goal = task.get("goal", "")
        task_id = task.get("task_id", "")
        params: dict[str, Any] = task.get("params") or {}
        desc, lo, hi = _AGENT_META.get(agent, (agent, 5, 10))
        total_lo += lo
        total_hi += hi

        def is_placeholder(value: Any) -> bool:
            text = str(value).strip() if value is not None else ""
            return not text or text.startswith("<") or "task_" in text.lower() or "{{" in text

        clean = {key: value for key, value in params.items() if not is_placeholder(value)}
        genuinely_missing = [
            key
            for key, value in params.items()
            if is_placeholder(value) and task_id in missing_task_ids and key in missing_set
        ]
        chained = [
            key
            for key, value in params.items()
            if is_placeholder(value) and key not in genuinely_missing
        ]
        lines = [f"**{i}단계** `{agent}` — {desc}", f"  - 목표: {goal}"]
        if clean:
            lines.append("  - 파라미터: " + ", ".join(f"{key}={value}" for key, value in clean.items()))
        if genuinely_missing:
            lines.append(f"  - ⚠️ 미입력: {', '.join(genuinely_missing)}")
        if chained:
            lines.append(f"  - ↳ {i - 1}단계 결과 자동 연결: {', '.join(chained)}")
        elif i > 1 and not genuinely_missing:
            lines.append(f"  - ↳ {i - 1}단계 완료 후 순차 실행")
        lines.append(f"  - 예상 소요: {lo}~{hi}초")
        blocks.append("\n".join(lines))

    header = f"**분석 계획을 확인해주세요** (총 {len(tasks)}개 작업 · 예상 {total_lo}~{total_hi}초)\n\n"
    if missing_set:
        warning = (
            f"⚠️ **필수 파라미터 미입력**: {', '.join(sorted(missing_set))}\n"
            "수정 입력란에 포함해주세요. (예: '4SS로 분석해줘')\n\n"
        )
        footer = "\n\n제품코드 등 필수 파라미터를 포함하여 입력해주세요 (예: '4SS로 진행') | '취소' 입력 시 중단"
    else:
        warning = ""
        footer = "\n\n그대로 실행하려면 아무 내용이나 입력 | 수정이 필요하면 직접 입력 (예: 'WADS 기간 1주일로') | '취소' 입력 시 중단"
    return warning + header + "\n\n".join(blocks) + footer


def _interrupt_events(interrupt_data: dict[str, Any]) -> list[dict[str, Any]]:
    if interrupt_data.get("type") == "plan_review":
        return [
            _event_dict(MessageEvent(
                agent="plan_review",
                content=_build_plan_review_message(
                    interrupt_data.get("tasks", []),
                    interrupt_data.get("missing_params", []),
                ),
            )),
            _event_dict(InterruptEvent(
                interrupt_type="plan_review",
                param="plan_review",
                message="분석 계획을 승인하시겠습니까?",
                route="",
            )),
        ]
    return [_event_dict(InterruptEvent(
        interrupt_type=interrupt_data.get(
            "interrupt_type", interrupt_data.get("type", "missing_param")
        ),
        param=interrupt_data.get("param", ""),
        message=interrupt_data.get("message", ""),
        route=interrupt_data.get("route", ""),
        options=interrupt_data.get("options", []),
        fields=interrupt_data.get("fields", []),
    ))]


def _detect_artifact_type(data: str) -> ArtifactType:
    if not data:
        return ArtifactType.html
    stripped = data.strip()
    if stripped.startswith("<") or stripped.startswith("<!"):
        return ArtifactType.html
    if stripped.startswith("data:image") or stripped.endswith((".png", ".jpg", ".svg")):
        return ArtifactType.image
    return ArtifactType.html


def _chunk_text(text: str, min_size: int = 3) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    for char in text:
        buffer.append(char)
        if len(buffer) >= min_size and char in (" ", "\n", ".", ",", "!", "?", ":", ";", ")", "」"):
            chunks.append("".join(buffer))
            buffer = []
    if buffer:
        chunks.append("".join(buffer))
    return chunks


def _initial_stream_input(
    request: GraphRunRequest, trace_id: str, turn_id: str
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "turn_id": turn_id,
        "messages": [HumanMessage(content=request.query)],
        "yield_artifacts": Overwrite([]),
        "wads_artifacts": Overwrite([]),
        "map_artifacts": Overwrite([]),
        "fail_history_artifacts": Overwrite([]),
        "ppt_artifacts": Overwrite([]),
        "lot_history_artifacts": Overwrite([]),
        "relation_tree_artifacts": Overwrite([]),
        "mining_artifacts": Overwrite([]),
        "wt_resp_artifacts": Overwrite([]),
        "past_steps": Overwrite([]),
        "step_count": 0,
        "task_plan": [],
        "pending_tasks": [],
        "current_task": {},
        "resolved_refs": {},
        "reference_issues": [],
        "canonical_request": {},
        "canonical_requests": [],
        "canonical_trace": [],
        "task_normalization_trace": [],
        "task_validation_issues": [],
        "hitl_issues": [],
        "hitl_responses": [],
        "memory_feedback": Overwrite([]),
        "response": "",
        "wiki_hit_ids": [],
        "wiki_update_status": "skipped",
        "fail_history_results": [],
        "anomaly_params": [],
        "postwads_offered": False,
        "mainoper_offered": False,
        "group_good": [],
        "group_bad": [],
        "fail_type": "",
        "cause_oper": "",
        "wads_category": "",
        "map_oper": "",
        "tech": "",
        "rank_limit": 10,
        "user_id": request.owner_id,
        "lotcd": "",
        "ref_date": datetime.now().strftime("%Y%m%d"),
        "confirm_tasks": {},
        "map_groups": [],
        "fail_groups": [],
        "rt_groups": [],
    }


def _first_turn_defaults(trace_id: str, turn_id: str) -> dict[str, Any]:
    return {
        "lotcd": "",
        "ref_date": "",
        "unit": "weekly",
        "periods": 0,
        "wads_start_tm": "",
        "wads_end_tm": "",
        "anomaly_params": [],
        "postwads_offered": False,
        "map_groups": [],
        "fail_groups": [],
        "rt_groups": [],
        "filter_params": [],
        "lot_ids": [],
        "wf_ids": [],
        "groupkey": "",
        "fail_type": "",
        "cause_oper": "",
        "map_type": "binmap",
        "map_oper": "",
        "map_result": "",
        "dh_query": "",
        "weeks_data": [],
        "table_result": "",
        "analysis_result": "",
        "agent_suggestion": "",
        "trace_id": trace_id,
        "turn_id": turn_id,
        "recent_results": [],
        "resolved_refs": {},
        "reference_issues": [],
        "canonical_request": {},
        "canonical_requests": [],
        "canonical_trace": [],
        "task_normalization_trace": [],
        "task_validation_issues": [],
        "hitl_issues": [],
        "hitl_responses": [],
        "memory_feedback": [],
        "task_plan": [],
        "pending_tasks": [],
        "current_task": {},
        "current_task_id": "",
        "current_task_goal": "",
        "response": "",
    }


def _trace_api():
    if not get_settings().enable_local_trace:
        return None
    import local_trace

    return local_trace


async def run_graph(
    graph: Any,
    request: GraphRunRequest,
    emit: Emit,
    cancelled: Cancelled,
    persist_artifacts: PersistArtifacts | None = None,
) -> GraphRunResult:
    """Run one fresh or resumed graph turn and emit transport-neutral events."""

    if await cancelled():
        return GraphRunResult(outcome="CANCELLED")

    trace = _trace_api()
    trace_id = trace.make_trace_id(request.session_id) if trace else request.job_id
    turn_id = trace.new_turn_id() if trace else str(uuid.uuid4())
    config = {
        "configurable": {
            "thread_id": request.thread_id,
            "trace_id": trace_id,
            "turn_id": turn_id,
        },
        "recursion_limit": 30,
    }
    pending_interrupt: dict[str, Any] = {}
    resume_value = request.resume_value

    if resume_value is not None:
        previous_state = await graph.aget_state(config)
        pending_interrupt = _pending_interrupt_from_state(previous_state)
        previous_turn_id = (getattr(previous_state, "values", None) or {}).get("turn_id", "")
        if previous_turn_id:
            turn_id = previous_turn_id
            config["configurable"]["turn_id"] = turn_id

    if (
        resume_value is not None
        and pending_interrupt.get("interrupt_type") in {"task_confirm", "postwads_choice"}
    ):
        from supervisor import _resume_is_interrupt_answer

        if not _resume_is_interrupt_answer(resume_value, pending_interrupt):
            await graph.ainvoke(Command(resume=""), config)
            resume_value = None

    stream_input: dict[str, Any] | Command
    if resume_value is not None:
        stream_input = Command(resume=resume_value)
    else:
        stream_input = _initial_stream_input(request, trace_id, turn_id)
        previous_state = await graph.aget_state(config)
        if not (previous_state and getattr(previous_state, "values", None)):
            stream_input.update(_first_turn_defaults(trace_id, turn_id))

    trace_tokens = trace.set_trace_context(trace_id, turn_id) if trace else None
    start = time.time()
    step_count = 0
    turn_messages: list[dict[str, Any]] = []
    turn_artifacts: list[dict[str, Any]] = []
    turn_suggestion = ""
    latest_interrupt: dict[str, Any] | None = None

    async def flush_saved_artifacts() -> None:
        if persist_artifacts is None:
            return
        saved = drain_saved_refs()
        if not saved:
            return
        persisted = await persist_artifacts(saved)
        for ref in persisted:
            event = {
                "type": "artifact",
                "artifact_id": ref.artifact_id,
                "artifact_type": ref.artifact_type,
                "mime": ref.mime,
                "title": ref.title,
                "agent": ref.agent,
                "url": f"/jobs/{request.job_id}/artifacts/{ref.artifact_id}",
            }
            await emit(event)
            turn_artifacts.append(event)

    try:
        if trace:
            trace.emit_trace_event(
                "user_turn_started",
                source="graph_job_runner",
                payload={
                    "job_id": request.job_id,
                    "resume": resume_value is not None,
                    "pending_interrupt": pending_interrupt,
                    "session_hash": trace_id,
                },
            )

        await emit(_event_dict(StreamStartEvent(
            session_id=request.session_id,
            query=request.query,
        )))

        async for mode, data in graph.astream(
            stream_input, config=config, stream_mode=["updates", "custom"]
        ):
            if await cancelled():
                return GraphRunResult(outcome="CANCELLED")

            if mode == "custom":
                custom = dict(data)
                kind = custom.pop("kind", "")
                event = None
                if kind == "thinking":
                    event = ThinkingEvent(**custom)
                elif kind == "token":
                    event = TokenEvent(**custom)
                elif kind == "status":
                    event = StatusEvent(**custom)
                if event is not None:
                    await emit(_event_dict(event))
                continue

            if not isinstance(data, dict):
                continue
            if "__interrupt__" in data:
                for interrupt in data["__interrupt__"] or []:
                    value = getattr(interrupt, "value", {})
                    if not isinstance(value, dict):
                        continue
                    latest_interrupt = value
                    for event in _interrupt_events(value):
                        await emit(event)
                continue

            for node_name, node_state in data.items():
                if not isinstance(node_state, dict):
                    continue
                elapsed = round(time.time() - start, 1)
                if "step_count" in node_state:
                    step_count = node_state["step_count"]
                await emit(_event_dict(NodeCompleteEvent(
                    node=node_name,
                    step=step_count,
                    elapsed=elapsed,
                )))

                for message in node_state.get("messages", []):
                    agent_name = getattr(message, "name", None) or node_name
                    content = message.content if hasattr(message, "content") else str(message)
                    if not content:
                        continue
                    for chunk in _chunk_text(content):
                        if await cancelled():
                            return GraphRunResult(outcome="CANCELLED")
                        await emit(_event_dict(TokenEvent(
                            content=chunk,
                            agent=agent_name,
                            node=node_name,
                        )))
                    await emit(_event_dict(MessageEvent(
                        agent=agent_name,
                        content=content,
                        step=step_count,
                    )))
                    turn_messages.append({"agent": agent_name, "content": content})

                await flush_saved_artifacts()
                artifact_sources = [
                    ("yield_artifacts", "yield_agent"),
                    ("wads_artifacts", "wads_agent"),
                    ("map_artifacts", "map_agent"),
                    ("fail_history_artifacts", "fail_history_agent"),
                    ("ppt_artifacts", "ppt_export"),
                    ("lot_history_artifacts", "lot_history_agent"),
                    ("relation_tree_artifacts", "relation_tree_agent"),
                    ("mining_artifacts", "mining_agent"),
                    ("wt_resp_artifacts", "wt_resp_agent"),
                ]
                if persist_artifacts is None:
                    for key, default_agent in artifact_sources:
                        for artifact in node_state.get(key, []):
                            artifact_data = artifact.get("data", "")
                            if not artifact_data:
                                continue
                            artifact_type = _detect_artifact_type(artifact_data)
                            mime = {
                                ArtifactType.html: "text/html",
                                ArtifactType.image: "image/png",
                                ArtifactType.markdown: "text/markdown",
                            }.get(artifact_type, "text/html")
                            event = ArtifactEvent(
                                artifact_type=artifact_type,
                                mime=mime,
                                title=artifact.get("title", key.replace("_artifacts", "")),
                                agent=artifact.get("agent", default_agent),
                                data=artifact_data,
                                step=step_count,
                            )
                            await emit(_event_dict(event))
                            stored_artifact = _event_dict(event)
                            stored_artifact["data"] = ""
                            turn_artifacts.append(stored_artifact)

                analysis = node_state.get("analysis_result", "")
                if analysis:
                    if persist_artifacts is not None:
                        save_artifact(
                            analysis,
                            "text/markdown",
                            "analysis",
                            node_name,
                            "markdown",
                        )
                        await flush_saved_artifacts()
                    else:
                        event = ArtifactEvent(
                            artifact_type=ArtifactType.markdown,
                            mime="text/markdown",
                            title="analysis",
                            agent=node_name,
                            data=analysis,
                            step=step_count,
                        )
                        await emit(_event_dict(event))
                        turn_artifacts.append(_event_dict(event))

                suggestion = node_state.get("agent_suggestion", "")
                if suggestion:
                    await emit(_event_dict(SuggestionEvent(
                        content=suggestion,
                        step=step_count,
                    )))
                    turn_suggestion = suggestion

        await flush_saved_artifacts()
        final_state = await graph.aget_state(config)
        if latest_interrupt is None:
            latest_interrupt = _pending_interrupt_from_state(final_state) or None
            if latest_interrupt:
                for event in _interrupt_events(latest_interrupt):
                    await emit(event)

        total_elapsed = round(time.time() - start, 1)
        if latest_interrupt is None:
            await emit(_event_dict(StreamEndEvent(
                total_steps=step_count,
                elapsed=total_elapsed,
            )))
        final_values = getattr(final_state, "values", None) or {}
        final_result = {
            "session_id": request.session_id,
            "query": request.query,
            "messages": turn_messages,
            "artifacts": turn_artifacts,
            "suggestion": turn_suggestion,
            "step_count": step_count,
            "elapsed": total_elapsed,
            "user_id": final_values.get("user_id") or request.owner_id,
            "memory_feedback": final_values.get("memory_feedback") or [],
        }
        return GraphRunResult(
            outcome="WAITING_INPUT" if latest_interrupt else "SUCCEEDED",
            latest_interrupt=latest_interrupt,
            final_result=final_result,
        )
    except Exception as exc:
        if trace:
            trace.emit_trace_event(
                "task_failed",
                source="graph_job_runner",
                severity="error",
                payload={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        raise
    finally:
        if trace:
            trace.reset_trace_context(trace_tokens)
