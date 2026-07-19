import datetime as dt
import json

import pytest

pytestmark = pytest.mark.no_server

from lot_history_insight import build_common_process_history


def _empty_sources():
    return {
        "fdc_alarm": [],
        "qtime_over": [],
        "trouble_lot": [],
        "future_action": [],
        "sample_split": [],
    }


def make_two_lot_history_with_qtime(from_oper, to_oper):
    histories = {}
    for hour, lot_id in enumerate(("LOT-A", "LOT-B"), start=10):
        sources = _empty_sources()
        sources["qtime_over"] = [
            {
                "lot_id": lot_id,
                "from_oper": from_oper,
                "to_oper": to_oper,
                "event_tm": dt.datetime(2026, 7, 18, hour),
            }
        ]
        histories[lot_id] = sources
    return histories


def make_histories_with_processes(first_process, second_process):
    histories = {}
    for lot_id, process in (("LOT-A", first_process), ("LOT-B", second_process)):
        sources = _empty_sources()
        sources["fdc_alarm"] = [{"lot_id": lot_id, "oper_id": process}]
        histories[lot_id] = sources
    return histories


def test_build_common_process_history_uses_all_lot_intersection():
    raw = {
        "LOT-A": {
            "fdc_alarm": [
                {
                    "lot_id": "LOT-A",
                    "oper_id": " PHOTO ",
                    "transfer_tm": dt.datetime(2026, 7, 18, 10),
                    "eqp_id": "EA",
                },
                {
                    "lot_id": "LOT-A",
                    "oper_id": "ONLY-A",
                    "transfer_tm": dt.datetime(2026, 7, 18, 11),
                },
            ],
            "qtime_over": [],
            "trouble_lot": [],
            "future_action": [],
            "sample_split": [],
        },
        "LOT-B": {
            "fdc_alarm": [
                {
                    "lot_id": "LOT-B",
                    "oper_id": "PHOTO",
                    "transfer_tm": dt.datetime(2026, 7, 18, 9),
                    "eqp_id": "EB",
                }
            ],
            "qtime_over": [],
            "trouble_lot": [],
            "future_action": [],
            "sample_split": [],
        },
        "LOT-C": {
            "fdc_alarm": [
                {
                    "lot_id": "LOT-C",
                    "oper_id": "PHOTO",
                    "transfer_tm": dt.datetime(2026, 7, 18, 8),
                    "eqp_id": "EC",
                }
            ],
            "qtime_over": [],
            "trouble_lot": [],
            "future_action": [],
            "sample_split": [],
        },
    }

    payload = build_common_process_history(raw)

    assert payload["lot_ids"] == ["LOT-A", "LOT-B", "LOT-C"]
    assert [item["process"] for item in payload["common_processes"]] == ["PHOTO"]
    histories = payload["common_processes"][0]["histories_by_lot"]
    assert list(histories) == ["LOT-A", "LOT-B", "LOT-C"]
    assert histories["LOT-A"][0]["event_time"] == "2026-07-18T10:00:00"
    assert payload["event_ids"] == [
        "LOT-A:fdc_alarm:0",
        "LOT-B:fdc_alarm:0",
        "LOT-C:fdc_alarm:0",
    ]


def test_qtime_views_share_event_id_but_keep_from_and_to_roles():
    raw = make_two_lot_history_with_qtime("PHOTO", "ETCH")
    payload = build_common_process_history(raw)
    by_process = {item["process"]: item for item in payload["common_processes"]}

    outgoing = by_process["PHOTO"]["histories_by_lot"]["LOT-A"][0]
    incoming = by_process["ETCH"]["histories_by_lot"]["LOT-A"][0]
    assert outgoing["role"] == "qtime_outgoing"
    assert incoming["role"] == "qtime_incoming"
    assert outgoing["event_id"] == incoming["event_id"]
    assert payload["event_ids"] == [
        "LOT-A:qtime_over:0",
        "LOT-B:qtime_over:0",
    ]


def test_process_matching_preserves_case_and_internal_whitespace():
    raw = make_histories_with_processes("PHOTO A", "Photo A")
    assert build_common_process_history(raw)["common_processes"] == []

    raw = make_histories_with_processes("PHOTO A", "PHOTO  A")
    assert build_common_process_history(raw)["common_processes"] == []


def test_source_specific_process_fields_exclude_sample_oper_desc():
    raw = {}
    for lot_id in ("LOT-A", "LOT-B"):
        sources = _empty_sources()
        sources["trouble_lot"] = [{"lot_id": lot_id, "step_desc": "TROUBLE"}]
        sources["future_action"] = [{"lot_id": lot_id, "action_step": "ACTION"}]
        sources["sample_split"] = [
            {"lot_id": lot_id, "step": "SAMPLE", "oper_desc": "DESCRIPTION"}
        ]
        raw[lot_id] = sources

    payload = build_common_process_history(raw)

    assert [item["process"] for item in payload["common_processes"]] == [
        "ACTION",
        "SAMPLE",
        "TROUBLE",
    ]


def test_event_details_are_recursively_json_serializable_and_events_are_sorted():
    raw = {}
    for lot_id in ("LOT-A", "LOT-B"):
        sources = _empty_sources()
        sources["fdc_alarm"] = [
            {
                "lot_id": lot_id,
                "oper_id": "PHOTO",
                "transfer_tm": None,
                "nested": {
                    "date": dt.date(2026, 7, 19),
                    "time": dt.time(9, 30),
                    "unsupported": object(),
                },
            },
            {
                "lot_id": lot_id,
                "oper_id": "PHOTO",
                "transfer_tm": dt.datetime(2026, 7, 18, 8),
            },
        ]
        raw[lot_id] = sources

    payload = build_common_process_history(raw)
    events = payload["common_processes"][0]["histories_by_lot"]["LOT-A"]

    assert [event["event_id"] for event in events] == [
        "LOT-A:fdc_alarm:1",
        "LOT-A:fdc_alarm:0",
    ]
    assert events[1]["details"]["nested"]["date"] == "2026-07-19"
    assert events[1]["details"]["nested"]["time"] == "09:30:00"
    json.dumps(payload)
