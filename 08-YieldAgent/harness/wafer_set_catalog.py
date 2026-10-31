"""Allowlisted physical sources, public fields and registered relations."""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    physical_name: str
    grain: str
    columns: dict[str, str | None]
    time_columns: tuple[str, ...] = ()
    key_columns: tuple[str, ...] = ()
    binary_columns: tuple[str, ...] = ()


def _source(source_id, physical_name, grain, columns, *, time=(), keys=(), binary=()):
    return SourceSpec(source_id, physical_name, grain, columns, tuple(time), tuple(keys), tuple(binary))


SOURCES = {
    "map": _source("map", "__configured_map__", "measurement", {
        "lot_id": "LOT_ID", "wf_id": "WF_ID", "lot_cd": "LOT_CD", "fab_id": "FAB_ID",
        "process": "OPER_DET_DESC", "start_tm": "START_TM", "end_tm": "END_TM",
        "map_val_json": "MAP_VAL_JSON"}, time=("start_tm", "end_tm"), keys=("lot_id", "wf_id"),
        binary=("map_val_json",)),
    "wafer_parameter": _source("wafer_parameter", "DF_DIE_TO_WF_YLD", "wafer_parameter", {
        "lot_cd": "LOT_CD", "week": "WEEK", "year": "YEAR", "month": "MONTH",
        "lot_id": "LOTID", "source_wf_id": "WFID", "wf_id": "WFID", "parameter": "PARAM",
        "value": "VALUE", "process": "PROCESS", "measurement_start": "MEASURETIME_START"},
        time=("measurement_start",), keys=("lot_cd", "process", "lot_id", "wf_id", "parameter")),
    "symbol_definition": _source("symbol_definition", "DF_SYMBOL_BIN", "definition", {
        "lot_cd": "LOT_CD", "process": "PROCESS", "parameter": "PARAM_NM",
        "regex_pattern": "REGEX_PATTERN", "dbloadtime": "DBLOADTIME"},
        time=("dbloadtime",), keys=("lot_cd", "process", "parameter")),
    "parameter_catalog": _source("parameter_catalog", "DF_ALL_PARA_PRB", "definition", {
        "lot_cd": "LOT_CD", "process_code": "PROC_GBN_CD", "included": "YIELD_TABLE",
        "parameter": "PARAM_NM", "display_order": "PARAM_SEQNO"},
        keys=("lot_cd", "process_code", "parameter")),
    "bmw_image": _source("bmw_image", "DF_BMW_PNG", "wafer_image", {
        "alias_lot_id": "ALIAS_LOT_ID", "source_wf_id": "WF_ID", "wf_id": "WF_ID",
        "image_type": "IMG_TYP", "date_raw": "DT", "image_available": "IMAGE_B64",
        "pdf_available": "PDF_FILE_PATH"}, keys=("alias_lot_id", "wf_id"),
        binary=("image_available", "pdf_available")),
    "gms_daily": _source("gms_daily", "DF_GMS_YIELD_DAILY", "product_period", {
        "lot_cd": "LOT_CD", "period_raw": "PERIOD_DATE", "period_kind": None,
        "period_start": None, "period_end": None, "fab_yield": "FAB_YIELD",
        "prb_yield": "PRB_YIELD", "cum0_yield": "CUM0_YIELD"}, keys=("lot_cd", "period_raw")),
    "gms_monthly": _source("gms_monthly", "DF_GMS_YIELD_MONTHLY", "product_period", {
        "lot_cd": "LOT_CD", "period_raw": "PERIOD_DATE", "period_kind": None,
        "period_start": None, "period_end": None, "fab_yield": "FAB_YIELD",
        "prb_yield": "PRB_YIELD", "cum0_yield": "CUM0_YIELD"}, keys=("lot_cd", "period_raw")),
    "gms_weekly": _source("gms_weekly", "DF_GMS_YIELD_WEEKLY", "product_period", {
        "lot_cd": "LOT_CD", "period_raw": "PERIOD_DATE", "period_kind": None,
        "period_start": None, "period_end": None, "fab_yield": "FAB_YIELD",
        "prb_yield": "PRB_YIELD", "cum0_yield": "CUM0_YIELD"}, keys=("lot_cd", "period_raw")),
    "wafer_history": _source("wafer_history", "DF_LEGEND_LONG", "wafer_event", {
        "lot_id": "LOT_ID", "source_wf_id": "WF_ID", "wf_id": "WF_ID", "step": "STEP",
        "recipe": "RECIPE", "chamb_id": "CHAMB_ID", "eqp_id": "EQP_ID", "route": "ROUTE",
        "end_tm_raw": "END_TM", "end_tm": "END_TM"}, time=("end_tm",), keys=("lot_id", "wf_id")),
    "wads_report": _source("wads_report", "DF_WADS_REPORT", "report", {
        "lot_cd": "LOTCD", "category": "CATEGORY", "parameter": "PARAMETER", "end_tm": "END_TM",
        "report_id": None, "html_available": "HTML"}, time=("end_tm",), keys=("report_id",),
        binary=("html_available",)),
    "wads_wafer": _source("wads_wafer", "__configured_wads_wafer__", "wafer_event", {
        "lot_cd": "LOT_CD", "oper_para": "OPER_PARA", "end_tm": "END_TM", "groupkey_raw": "GROUP_KEY",
        "lot_id": "LOT_ID", "source_wf_id": "WF_ID", "wf_id": "WF_ID"},
        time=("end_tm",), keys=("lot_id", "wf_id")),
    "wads_main_oper": _source("wads_main_oper", "DF_WADS_MAIN_OPER", "definition", {
        "lot_cd": "LOTCD", "category": "CATEGORY", "parameter": "PARAMETER", "main_oper": "MAIN_OPER"}),
    "wads_good_bad": _source("wads_good_bad", "DF_WADS_GOOD_BAD_LOT", "comparison_group", {
        "lot_cd": "LOTCD", "category": "CATEGORY", "parameter": "PARAMETER",
        "good_lot_id": "GOOD_LOT_ID", "bad_lot_id": "BAD_LOT_ID"}),
    "fdc_alarm": _source("fdc_alarm", "DF_FDC_ALARM", "lot_event", {
        "lot_cd": "LOT_CD", "lot_id": "LOT_ID", "transfer_tm": "TRANSFER_TM", "oper_id": "OPER_ID",
        "eqp_id": "EQP_ID", "item_nm": "ITEM_NM", "alarm_level_cd": "ALARM_LEVEL_CD",
        "rslt_val": "RSLT_VAL", "spec_min_val": "SPEC_MIN_VAL", "spec_max_val": "SPEC_MAX_VAL"},
        time=("transfer_tm",), keys=("lot_id",)),
    "qtime_over": _source("qtime_over", "DF_QTIME_OVER", "lot_event", {
        "lot_cd": "LOT_CD", "lot_id": "LOT_ID", "from_oper": "FROM_OPER", "to_oper": "TO_OPER",
        "control_limit": "CONTROL_LIMIT", "q_time": "Q_TIME", "bal": "BAL", "event_tm": "EVENT_TM"},
        time=("event_tm",), keys=("lot_id",)),
    "trouble_lot": _source("trouble_lot", "DF_TROUBLE_LOT", "lot_event", {
        "lot_cd": "LOT_CD", "lot_id": "LOT_ID", "hold_time": "HOLD_TIME", "step_desc": "STEP_DESC",
        "cause_eq": "CAUSE_EQ", "delay_time": "DELAY_TIME", "contents": "CONTENTS", "h_code": "H_CODE"},
        time=("hold_time",), keys=("lot_id",)),
    "future_action": _source("future_action", "DF_FUTURE_ACTION", "lot_event", {
        "lot_id": "LOT_ID", "action_time": "ACTION_TIME", "action_step": "ACTION_STEP",
        "action_comment": "ACTION_COMMENT", "action_flag": "ACTION_FLAG", "reason_area": "REASON_AREA"},
        time=("action_time",), keys=("lot_id",)),
    "sample_split": _source("sample_split", "DF_SAMPLE_SPLIT", "lot_event", {
        "lot_id": "LOT_ID", "event": "EVENT", "step": "STEP", "oper_desc": "OPER_DESC",
        "sample_slot": "SAMPLE_SLOT", "sample_split_id": "SAMPLE_SPLIT_ID", "qty": "QTY"},
        keys=("lot_id",)),
}


RELATIONS = [
    {"relation_id": "measurement_identity", "left_grain": "measurement", "right_grain": "measurement",
     "keys_json": '["measurement_ref"]', "cardinality": "one_to_one", "temporal_policy": "exact",
     "confidence": "exact"},
    {"relation_id": "wafer_identity", "left_grain": "wafer", "right_grain": "wafer",
     "keys_json": '["lot_id","wf_id"]', "cardinality": "many_to_many", "temporal_policy": "none",
     "confidence": "identity_only"},
    {"relation_id": "wafer_parameter", "left_grain": "wafer", "right_grain": "wafer_parameter",
     "keys_json": '["lot_id","wf_id"]', "cardinality": "one_to_many", "temporal_policy": "registered_process",
     "confidence": "validated_mapping"},
    {"relation_id": "wafer_history", "left_grain": "wafer", "right_grain": "wafer_event",
     "keys_json": '["lot_id","wf_id"]', "cardinality": "one_to_many", "temporal_policy": "none",
     "confidence": "identity_only"},
    {"relation_id": "member_cohort", "left_grain": "member", "right_grain": "membership",
     "keys_json": '["member_id"]', "cardinality": "one_to_many", "temporal_policy": "none",
     "confidence": "exact"},
    {"relation_id": "product_period", "left_grain": "product_period", "right_grain": "product_period",
     "keys_json": '["lot_cd","period_kind","period_start","period_end"]', "cardinality": "many_to_one",
     "temporal_policy": "exact", "confidence": "exact"},
    {"relation_id": "bmw_exact_identity", "left_grain": "wafer", "right_grain": "wafer_image",
     "keys_json": '["lot_id","wf_id"]', "cardinality": "one_to_many", "temporal_policy": "none",
     "confidence": "exact_string"},
    {"relation_id": "wads_report_day", "left_grain": "report", "right_grain": "wafer_event",
     "keys_json": '["lot_cd"]', "cardinality": "one_to_many", "temporal_policy": "same_day",
     "confidence": "approximate"},
]


def source_spec(source_id: str) -> SourceSpec:
    try:
        return SOURCES[source_id]
    except KeyError:
        raise ValueError(
            f"Unknown source_id: {source_id}"
            ". describe_sources로 유효한 source_id를 확인하세요. "
            "열의 실제 값은 list_values로 확인하세요."
        ) from None


def physical_name(spec: SourceSpec) -> str:
    if spec.physical_name == "__configured_map__":
        try:
            from .wafer_set_map_source import _map_table
            configured = _map_table()
        except ImportError:
            configured = os.getenv("ORACLE_TABLE", "LANGGRAPH_DATA")
    elif spec.physical_name == "__configured_wads_wafer__":
        configured = os.getenv("WADS_WF_LIST_TABLE", "DF_WADS_WF_LIST")
    else:
        return spec.physical_name
    name = str(configured).upper()
    if not name.replace("_", "").isalnum():
        raise ValueError("Configured source name is invalid")
    return name

