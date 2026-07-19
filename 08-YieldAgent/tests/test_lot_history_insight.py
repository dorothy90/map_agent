import datetime as dt
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

pytestmark = pytest.mark.no_server

from lot_history_insight import (
    analyze_common_process_history,
    build_common_process_history,
)


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


class FakeModel:
    def __init__(self, raw_json):
        self.raw_json = raw_json
        self.invoke_count = 0
        self.messages = None
        self.config = None

    def invoke(self, messages, config):
        self.invoke_count += 1
        self.messages = messages
        self.config = config
        return AIMessage(content=self.raw_json)


@pytest.fixture
def valid_payload():
    raw = {}
    for lot_id in ("LOT-A", "LOT-B"):
        sources = _empty_sources()
        sources["fdc_alarm"] = [
            {"lot_id": lot_id, "oper_id": "PHOTO", "alarm": "pressure"},
            {"lot_id": lot_id, "oper_id": "WET", "alarm": "temperature"},
        ]
        raw[lot_id] = sources
    return build_common_process_history(raw)


def valid_insight_json(payload):
    photo = next(
        item for item in payload["common_processes"] if item["process"] == "PHOTO"
    )
    photo_events = [
        photo["histories_by_lot"][lot_id][0]["event_id"]
        for lot_id in payload["lot_ids"]
    ]
    return json.dumps(
        {
            "summary": "PHOTO 공정에서 두 LOT의 공통 알람이 확인되었습니다.",
            "process_insights": [
                {
                    "process": "PHOTO",
                    "summary": "두 LOT 모두 pressure 알람이 있습니다.",
                    "common_patterns": [
                        {
                            "text": "두 LOT에서 동일한 알람이 반복되었습니다.",
                            "lot_ids": payload["lot_ids"],
                            "event_ids": photo_events,
                        }
                    ],
                    "lot_differences": [],
                    "hypotheses": [
                        {
                            "text": "설비 조건의 영향 가능성을 확인해야 합니다.",
                            "confidence": "low",
                            "lot_ids": payload["lot_ids"],
                            "event_ids": photo_events,
                        }
                    ],
                    "recommended_checks": ["PHOTO 설비 조건을 비교 확인하십시오."],
                }
            ],
            "priority_processes": [
                {
                    "process": "PHOTO",
                    "reason": "두 LOT에서 공통 알람이 확인되었습니다.",
                    "lot_ids": payload["lot_ids"],
                    "event_ids": photo_events,
                }
            ],
        },
        ensure_ascii=False,
    )


def insight_json_with_common_pattern_lot_ids(payload, lot_ids):
    data = json.loads(valid_insight_json(payload))
    data["process_insights"][0]["common_patterns"][0]["lot_ids"] = lot_ids
    return json.dumps(data, ensure_ascii=False)


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


def test_analyze_common_process_history_invokes_model_once(valid_payload):
    model = FakeModel(valid_insight_json(valid_payload))

    result = analyze_common_process_history(valid_payload, {}, model=model)

    assert model.invoke_count == 1
    assert result["process_insights"][0]["process"] == "PHOTO"
    assert isinstance(model.messages[0], SystemMessage)
    assert isinstance(model.messages[1], HumanMessage)
    human_content = str(model.messages[1].content)
    prompt_payload = json.loads(
        human_content.split("<common_process_history>\n", 1)[1].split(
            "\n</common_process_history>", 1
        )[0]
    )
    for common_process in valid_payload["common_processes"]:
        assert common_process in prompt_payload["common_processes"]


def test_analyze_rejects_unknown_event_reference(valid_payload):
    raw = valid_insight_json(valid_payload).replace(
        valid_payload["event_ids"][0], "invented:event"
    )

    with pytest.raises(ValueError, match="unknown event_id"):
        analyze_common_process_history(valid_payload, {}, model=FakeModel(raw))


def test_analyze_rejects_common_pattern_from_one_lot(valid_payload):
    raw = insight_json_with_common_pattern_lot_ids(valid_payload, ["LOT-A"])

    with pytest.raises(ValueError, match="at least two LOTs"):
        analyze_common_process_history(valid_payload, {}, model=FakeModel(raw))


def test_analyze_rejects_unknown_process_reference(valid_payload):
    data = json.loads(valid_insight_json(valid_payload))
    data["process_insights"][0]["process"] = "INVENTED"

    with pytest.raises(ValueError, match="unknown process"):
        analyze_common_process_history(
            valid_payload,
            {},
            model=FakeModel(json.dumps(data, ensure_ascii=False)),
        )


def test_analyze_rejects_unknown_lot_reference(valid_payload):
    data = json.loads(valid_insight_json(valid_payload))
    data["priority_processes"][0]["lot_ids"] = ["LOT-A", "INVENTED"]

    with pytest.raises(ValueError, match="unknown lot_id"):
        analyze_common_process_history(
            valid_payload,
            {},
            model=FakeModel(json.dumps(data, ensure_ascii=False)),
        )


def test_analyze_rejects_event_from_another_process(valid_payload):
    data = json.loads(valid_insight_json(valid_payload))
    wet = next(
        item for item in valid_payload["common_processes"] if item["process"] == "WET"
    )
    data["process_insights"][0]["common_patterns"][0]["event_ids"][0] = wet[
        "histories_by_lot"
    ]["LOT-A"][0]["event_id"]

    with pytest.raises(ValueError, match="unknown event_id"):
        analyze_common_process_history(
            valid_payload,
            {},
            model=FakeModel(json.dumps(data, ensure_ascii=False)),
        )


@pytest.mark.parametrize("confidence", ["certain", 0.9])
def test_analyze_rejects_invalid_hypothesis_confidence(valid_payload, confidence):
    data = json.loads(valid_insight_json(valid_payload))
    data["process_insights"][0]["hypotheses"][0]["confidence"] = confidence

    with pytest.raises(ValueError):
        analyze_common_process_history(
            valid_payload,
            {},
            model=FakeModel(json.dumps(data, ensure_ascii=False)),
        )


def test_analyze_rejects_extra_schema_fields(valid_payload):
    data = json.loads(valid_insight_json(valid_payload))
    data["unsupported"] = True

    with pytest.raises(ValueError):
        analyze_common_process_history(
            valid_payload,
            {},
            model=FakeModel(json.dumps(data, ensure_ascii=False)),
        )
