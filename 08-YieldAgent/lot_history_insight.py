"""Build the exact common-process payload for multi-LOT history analysis."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field

from common import extract_json_from_llm, get_llm
from lf_utils import lf_callbacks as _lf_callbacks
from prompts import (
    LOT_HISTORY_COMMON_PROCESS_INSIGHT_SYSTEM_PROMPT,
    LOT_HISTORY_COMMON_PROCESS_INSIGHT_USER_PROMPT,
)


_SOURCE_PROCESS_FIELDS = {
    "fdc_alarm": (("oper_id", "fdc_alarm"),),
    "qtime_over": (
        ("from_oper", "qtime_outgoing"),
        ("to_oper", "qtime_incoming"),
    ),
    "trouble_lot": (("step_desc", "trouble_lot"),),
    "future_action": (("action_step", "future_action"),),
    "sample_split": (("step", "sample_split"),),
}

_SOURCE_TIME_FIELDS = {
    "fdc_alarm": "transfer_tm",
    "qtime_over": "event_tm",
    "trouble_lot": "hold_time",
    "future_action": "action_time",
    "sample_split": None,
}


class EvidenceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    lot_ids: list[str] = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    confidence: Literal["high", "medium", "low"]
    lot_ids: list[str] = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)


class ProcessInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process: str
    summary: str
    common_patterns: list[EvidenceFinding]
    lot_differences: list[EvidenceFinding]
    hypotheses: list[Hypothesis]
    recommended_checks: list[str]


class PriorityProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process: str
    reason: str
    lot_ids: list[str] = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)


class CommonProcessInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    process_insights: list[ProcessInsight]
    priority_processes: list[PriorityProcess]


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _process_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def build_common_process_history(all_results: dict) -> dict[str, Any]:
    """Return events grouped by process when that process exists for every LOT."""
    lot_ids = list(all_results)
    events_by_lot: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for lot_id in lot_ids:
        events_by_process: dict[str, list[dict[str, Any]]] = {}
        lot_sources = all_results[lot_id]
        for source, process_fields in _SOURCE_PROCESS_FIELDS.items():
            time_field = _SOURCE_TIME_FIELDS[source]
            for row_index, row in enumerate(lot_sources.get(source, [])):
                event_id = f"{lot_id}:{source}:{row_index}"
                event_time = _json_value(row.get(time_field)) if time_field else None
                details = _json_value(row)
                for process_field, role in process_fields:
                    process = _process_name(row.get(process_field))
                    if not process:
                        continue
                    events_by_process.setdefault(process, []).append(
                        {
                            "event_id": event_id,
                            "lot_id": lot_id,
                            "process": process,
                            "source": source,
                            "role": role,
                            "event_time": event_time,
                            "details": details,
                        }
                    )
        events_by_lot[lot_id] = events_by_process

    process_sets = [set(events_by_lot[lot_id]) for lot_id in lot_ids]
    common_names = sorted(set.intersection(*process_sets)) if process_sets else []
    common_processes = []
    event_ids = []
    seen_event_ids = set()

    for process in common_names:
        histories_by_lot = {}
        for lot_id in lot_ids:
            events = sorted(
                events_by_lot[lot_id][process],
                key=lambda event: (
                    event["event_time"] is None,
                    event["event_time"] or "",
                    event["event_id"],
                    event["role"],
                ),
            )
            histories_by_lot[lot_id] = events
            for event in events:
                if event["event_id"] not in seen_event_ids:
                    seen_event_ids.add(event["event_id"])
                    event_ids.append(event["event_id"])
        common_processes.append(
            {"process": process, "histories_by_lot": histories_by_lot}
        )

    return {
        "lot_ids": lot_ids,
        "common_processes": common_processes,
        "event_ids": event_ids,
    }


def _validate_references(
    *,
    process: str,
    lot_ids: list[str],
    event_ids: list[str],
    allowed_lots: set[str],
    histories_by_process: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if process not in histories_by_process:
        raise ValueError(f"unknown process: {process}")

    unknown_lots = set(lot_ids) - allowed_lots
    if unknown_lots:
        raise ValueError(f"unknown lot_id: {sorted(unknown_lots)[0]}")

    histories_by_lot = histories_by_process[process]
    allowed_events = {
        event["event_id"]
        for lot_id in set(lot_ids)
        for event in histories_by_lot.get(lot_id, [])
    }
    unknown_events = set(event_ids) - allowed_events
    if unknown_events:
        raise ValueError(f"unknown event_id: {sorted(unknown_events)[0]}")


def analyze_common_process_history(
    payload: dict[str, Any],
    config: RunnableConfig,
    model: Any | None = None,
) -> dict[str, Any]:
    """Analyze all common processes in one model call and validate its references."""
    human_prompt = LOT_HISTORY_COMMON_PROCESS_INSIGHT_USER_PROMPT.format(
        common_process_history_json=json.dumps(
            payload, ensure_ascii=False, indent=2
        )
    )
    llm = model or get_llm()
    response = llm.invoke(
        [
            SystemMessage(
                content=LOT_HISTORY_COMMON_PROCESS_INSIGHT_SYSTEM_PROMPT
            ),
            HumanMessage(content=human_prompt),
        ],
        config={**config, "callbacks": _lf_callbacks()},
    )
    parsed = extract_json_from_llm(str(response.content), CommonProcessInsight)

    allowed_lots = set(payload["lot_ids"])
    histories_by_process = {
        item["process"]: item["histories_by_lot"]
        for item in payload["common_processes"]
    }

    for process_insight in parsed.process_insights:
        process = process_insight.process
        if process not in histories_by_process:
            raise ValueError(f"unknown process: {process}")

        findings = [
            *process_insight.common_patterns,
            *process_insight.lot_differences,
            *process_insight.hypotheses,
        ]
        for pattern in process_insight.common_patterns:
            if len(set(pattern.lot_ids)) < 2:
                raise ValueError("common pattern must cite at least two LOTs")

        for finding in findings:
            _validate_references(
                process=process,
                lot_ids=finding.lot_ids,
                event_ids=finding.event_ids,
                allowed_lots=allowed_lots,
                histories_by_process=histories_by_process,
            )

    for priority in parsed.priority_processes:
        _validate_references(
            process=priority.process,
            lot_ids=priority.lot_ids,
            event_ids=priority.event_ids,
            allowed_lots=allowed_lots,
            histories_by_process=histories_by_process,
        )

    return parsed.model_dump()
