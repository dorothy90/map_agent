"""Build the exact common-process payload for multi-LOT history analysis."""

from __future__ import annotations

import datetime as dt
from typing import Any


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
