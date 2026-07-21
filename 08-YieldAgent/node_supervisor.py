"""node_supervisor — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import json
import re
from datetime import date, datetime
from typing import Any, Dict, Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from langchain_core.runnables import RunnableConfig
from langfuse import observe
from langgraph.graph import END
from langgraph.types import Command, interrupt

from common import extract_json_from_llm, stream_event
from canonical_request import AGENT_SLOT_SCHEMAS, build_task_from_canonical_request
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import HITLContractId, StatusEvent
from local_trace import emit_runtime_detail, emit_trace_event, preview_text, summarize_params

load_dotenv(override=True)

from orch_utils import _AGENT_NAMES, _model, _normalize_map_oper, logger
from query_state import TimeRange
from user_memory import make_feedback_event
from wads_context import _resolve_chained_params




def _parse_iso_week_label(label: str) -> tuple[int, int]:
    """'2026-W17' / '2026-w17' → (2026, 17)"""
    year_s, week_s = label.strip().replace("w", "W").split("-W", 1)
    return int(year_s), int(week_s)


def _parse_year_month_label(label: str) -> tuple[int, int]:
    """'2026-02' → (2026, 2)"""
    year_s, month_s = label.strip().split("-", 1)
    return int(year_s), int(month_s)


def resolve_time_range(tr: TimeRange) -> tuple[str, int, str]:
    """TimeRange 라벨 → (ref_date YYYYMMDD, periods, unit). end 시점이 기준일."""
    unit = tr.unit
    start, end = (tr.start or "").strip(), (tr.end or "").strip()
    if not start or not end:
        raise ValueError(f"time_range start/end empty: start={start!r} end={end!r}")

    if unit == "weekly":
        sy, sw = _parse_iso_week_label(start)
        ey, ew = _parse_iso_week_label(end)
        start_monday = date.fromisocalendar(sy, sw, 1)
        end_monday = date.fromisocalendar(ey, ew, 1)
        weeks = ((end_monday - start_monday).days // 7) + 1
        return end_monday.strftime("%Y%m%d"), max(1, weeks), "weekly"

    if unit == "monthly":
        sy, sm = _parse_year_month_label(start)
        ey, em = _parse_year_month_label(end)
        months = (ey - sy) * 12 + (em - sm) + 1
        return f"{ey:04d}{em:02d}01", max(1, months), "monthly"

    if unit == "daily":
        sd = datetime.strptime(start, "%Y-%m-%d").date()
        ed = datetime.strptime(end, "%Y-%m-%d").date()
        days = (ed - sd).days + 1
        return ed.strftime("%Y%m%d"), max(1, days), "daily"

    raise ValueError(f"unknown time_range.unit: {unit!r}")


def _apply_time_range_dict(params: dict) -> None:
    """task_params 안의 time_range를 ref_date/periods/unit으로 변환·치환(in-place).
    변환 성공 시 time_range 키는 제거한다(yield_agent_node는 ref_date/periods/unit만 읽음).
    변환 실패/부재 시 기존 슬롯을 건드리지 않아 backward-compatible."""
    tr_data = params.get("time_range")
    if not isinstance(tr_data, dict):
        return
    try:
        ref, periods, unit = resolve_time_range(TimeRange(**tr_data))
    except Exception as e:
        logger.warning("[Supervisor] task_params.time_range 변환 실패: %s | data=%r", e, tr_data)
        params.pop("time_range", None)
        return
    params["ref_date"] = ref
    params["periods"] = periods
    params["unit"] = unit
    params.pop("time_range", None)


# ── Replanner 노드 (#8 phase 3a) ──────────────────────────────
# 공식 LangGraph plan-and-execute 패턴의 replan 단계.
# Phase 3a: past_steps 결과를 LLM에게 보여주고 남은 pending_tasks의 빈 chained-input을 채운다.
# (예: task_1 wads 결과의 lot ID들을 task_2 map_agent의 map_lot_ids에 채움)
# DO NOT 추가/삭제/순서변경 — 단순 input 채우기. Phase 3b에서 plan 전체 재구성 추가 예정.
# 그래프 wiring: agent → replanner → supervisor (#8 phase 2에서 이미 wiring 완료).


def _parse_lot_ids(params: dict) -> list[str]:
    val = (
        params.get("lot_ids")
        or params.get("map_lot_ids")
        or params.get("lh_lot_ids")
        or params.get("yield_lot_ids")
        or params.get("map_lot_id")
        or ""
    )
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _parse_wf_ids(params: dict) -> list[str]:
    val = params.get("wf_ids") or params.get("map_wf_ids") or ""
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _parse_fail_type(params: dict) -> str:
    return (
        params.get("fail_type")
        or params.get("wads_parameter")
        or params.get("dh_fail_type")
        or ""
    )


def _parse_cause_oper(params: dict) -> str:
    return (
        params.get("cause_oper")
        or params.get("dh_cause_oper")
        or params.get("rt_main_oper_det_desc")
        or ""
    )


_PRODUCT_LOTCD_RE = re.compile(r"^[0-9][A-Z0-9]{2}$")


def _normalize_product_lotcd(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return text if _PRODUCT_LOTCD_RE.fullmatch(text) else ""


def _lotcd_prompt(agent: str, *, invalid_value: Any = "") -> str:
    if invalid_value:
        return (
            f"`{invalid_value}`는 제품코드 형식이 아닙니다. "
            "영문/숫자 3자리 제품코드를 다시 입력해주세요. 예: 4SA, 4SS"
        )
    if agent == "wads_agent":
        return "제품코드를 입력해주세요. 예: 4SA, 4SS"
    return "제품코드를 입력해주세요. 영문/숫자 3자리 형식입니다. 예: 4SS, 5NA, 6E2"


def _invalid_lotcd_update(
    *,
    agent: str,
    task_id: str,
    value: Any,
    message: str,
    step_count: int,
) -> Command:
    emit_runtime_detail(
        "param.invalid",
        {
            "agent": agent,
            "param": "lotcd",
            "value": value,
            "reason": "lotcd_must_be_ascii_product_code",
            "action": "abort",
        },
        task_id=task_id,
    )
    emit_trace_event(
        "validation_issue",
        source="supervisor",
        severity="error",
        task_id=task_id,
        payload={
            "type": "invalid_param",
            "agent": agent,
            "param": "lotcd",
            "reason": "lotcd_must_be_ascii_product_code",
            "value_preview": preview_text(value),
        },
    )
    return Command(
        update={
            "response": message,
            "step_count": step_count,
        },
        goto=END,
    )


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def _project_task_params(agent: str, task_params: dict, state: dict) -> dict:
    """Project a task's resolved params into the per-agent state fields the agent reads.

    Pure mapping only — no validation/HITL (that stays in supervisor_node). Two
    subtleties preserved from the inline version:
    - Order: the common fields set lot_ids/wf_ids/groupkey first, then yield_agent
      CLEARS them ([]/"") because yield queries by lotcd/period, not by lot.
    - lotcd inheritance from stale state differs per agent: yield and fail_history
      fall back to state["lotcd"]; wads does NOT (it can query by date/param alone,
      so it must not implicitly inherit a previous product code).
    """
    proj: dict = {}

    # lotcd 공유 — yield/wads/fail_history만, agent별 상속 차등 유지.
    if agent == "yield_agent":
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")
    elif agent == "wads_agent":
        proj["lotcd"] = task_params.get("lotcd", "")
    elif agent == "fail_history_agent":
        proj["lotcd"] = task_params.get("lotcd", state.get("lotcd", ""))
    elif agent == "wt_resp_agent":
        # main_oper 선택 후속: lotcd 상속(없으면 state). fail_type/cause_oper은 공통 블록이 채움.
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")

    # 통합 필드: 모든 agent에 공통 적용
    proj["lot_ids"] = _parse_lot_ids(task_params)
    proj["wf_ids"] = _parse_wf_ids(task_params)
    proj["groupkey"] = (
        task_params.get("groupkey")
        or task_params.get("map_groupkey")
        or task_params.get("yield_groupkey")
        or ""
    )
    proj["fail_type"] = _parse_fail_type(task_params)
    proj["cause_oper"] = _parse_cause_oper(task_params)

    if agent == "yield_agent":
        proj.update(
            {
                "ref_date": task_params.get("ref_date", state.get("ref_date", "")),
                "unit": task_params.get("unit", state.get("unit", "weekly")),
                "periods": task_params.get("periods", state.get("periods", 0)),
                "lot_ids": [],
                "wf_ids": [],
                "groupkey": "",
                "filter_params": [],
            }
        )

    elif agent == "wads_agent":
        proj.update(
            {
                "wads_start_tm": task_params.get("wads_start_tm", ""),
                "wads_end_tm": task_params.get("wads_end_tm")
                or date.today().strftime("%Y-%m-%d"),
                # 공정 필터 ("PT1H"/"PT1C") — yield 열화 fan-out 시 공정을 CATEGORY로 전달
                "wads_category": task_params.get("wads_category", ""),
            }
        )

    elif agent == "map_agent":
        proj.update(
            {
                "map_type": task_params.get("map_type", "binmap"),
                # god-state 폐기: 공정은 task.params(planner/chaining/postwads)에서만. stale 상속 금지.
                "map_oper": task_params.get("map_oper", ""),
                # wafer-number pattern filter (LLM fills wf_mod/wf_rem; SQL applies MOD).
                "wf_mod": task_params.get("wf_mod") or 0,
                "wf_rem": task_params.get("wf_rem") or 0,
                # cummap display label (backend-resolved, e.g. #RN report parameter).
                "map_label": task_params.get("map_label") or "",
                # WADS report별 cummap fan-out 입력 [{parameter, map_oper, groupkeys}, …].
                "map_groups": task_params.get("map_groups") or [],
            }
        )

    elif agent == "fail_history_agent":
        proj["dh_query"] = task_params.get("dh_query", "")
        # WADS report별 불량이력 fan-out 입력 [{lotcd, parameter, lot_ids}, …].
        proj["fail_groups"] = task_params.get("fail_groups") or []

    elif agent == "relation_tree_agent":
        # lotcd(3자)는 rt_lot_code와 동일 개념 — 표준 슬롯 우선, rt_lot_code는 구 필드 fallback
        if not proj.get("lotcd"):
            proj["lotcd"] = (
                task_params.get("lotcd")
                or task_params.get("rt_lot_code")
                or state.get("lotcd", "")
            )
        # WADS report별 연관분석 fan-out 입력 [{lotcd, parameter, lot_ids}, …].
        proj["rt_groups"] = task_params.get("rt_groups") or []

    elif agent == "mining_agent":
        # 상류(wads→wt_resp) 공유키는 wt_resp followup의 default_slots가 task.params로 운반한다
        # (god-state 상속 폐기). lotcd만 세션 앵커로 state fallback 유지.
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")
        proj["fail_type"] = _parse_fail_type(task_params)
        proj["cause_oper"] = _parse_cause_oper(task_params)
        proj["wads_category"] = task_params.get("wads_category")
        # group_good/group_bad: 사용자 직접 입력 또는 상류 결과 상속 (chained-input).
        proj["group_good"] = task_params.get("group_good") or state.get("group_good") or []
        proj["group_bad"] = task_params.get("group_bad") or state.get("group_bad") or []
        # mining 고유 슬롯. tech/rank_limit: god-state 상속 폐기(매 턴 디폴트로 리셋됨) → task.params만.
        # user_id: 멀티턴 기억이 아니라 매 요청 주입된 신원 → state(요청값) fallback 유지(stale 아님).
        proj["tech"] = task_params.get("tech", "")
        proj["user_id"] = task_params.get("user_id") or state.get("user_id", "")
        proj["rank_limit"] = task_params.get("rank_limit") or 10

    return proj


def _missing_required_fields(
    agent: str, update_dict: dict, rejections: dict | None = None
) -> list[dict]:
    """Required slots still missing for this agent, as fields[] specs — the source of
    truth for "what is missing". required_any (map lot_ids|groupkey) is ONE item.
    lotcd VALUE validity is NOT here; that is a separate validation step.

    `rejections` maps slot -> reason for a value just rejected by its format guard; the
    re-ask shows that reason so the user knows WHY they are being asked again."""
    fields: list[dict] = []
    if agent == "map_agent":
        if not update_dict.get("lot_ids") and not update_dict.get("groupkey"):
            fields.append({
                "slot": "lot_ids",
                "label": "맵을 조회할 Lot ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                "type": "lot_ids",
                "required_any_group": "lot_or_groupkey",
            })
        if not _normalize_map_oper(update_dict.get("map_oper", "")):
            fields.append({
                "slot": "map_oper",
                "label": "PT1H / PT1C 중 어떤 공정의 맵을 조회할까요?",
                "type": "map_oper",
                "validation_hint": "PT1H|PT1C",
            })
    elif agent == "relation_tree_agent":
        if not update_dict.get("lotcd"):
            fields.append({
                "slot": "lotcd",
                "label": "연관 분석할 LOT 코드를 입력해주세요. (예: 4SS)",
                "type": "lotcd",
            })
        # TEMP(relation_tree fail_type): required is now lotcd + fail_type (was cause_oper).
        if not update_dict.get("fail_type"):
            fields.append({
                "slot": "fail_type",
                "label": "연관 분석할 파라미터(불량유형)를 입력해주세요. (예: VTH 또는 VTH,IDSAT)",
                "type": "fail_type",
            })
    elif agent == "wt_resp_agent":
        if not update_dict.get("lotcd"):
            fields.append({
                "slot": "lotcd",
                "label": "WT Resp 분석할 LOT 코드를 입력해주세요. (예: 4SS)",
                "type": "lotcd",
            })
        if not update_dict.get("fail_type"):
            fields.append({
                "slot": "fail_type",
                "label": "WT Resp 분석할 파라미터(불량유형)를 입력해주세요. (예: VTH)",
                "type": "fail_type",
            })
        if not update_dict.get("cause_oper"):
            fields.append({
                "slot": "cause_oper",
                "label": "기준 공정(main_oper)을 입력해주세요.",
                "type": "cause_oper",
            })
    elif agent == "yield_agent":
        if not update_dict.get("lotcd"):
            fields.append({"slot": "lotcd", "label": _lotcd_prompt(agent), "type": "lotcd"})
    elif agent == "lot_history_agent":
        if not update_dict.get("lot_ids"):
            fields.append({
                "slot": "lot_ids",
                "label": "이력을 조회할 LOT ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                "type": "lot_ids",
            })
    for field in fields:  # re-ask shows WHY the previous value was rejected
        reason = (rejections or {}).get(field["slot"])
        if reason:
            field["label"] = reason
    return fields


def _ambiguous_fields(
    agent: str, task_id: str, update_dict: dict, state: dict
) -> list[dict]:
    """모호 슬롯(시나리오 2)을 choice 필드로 변환 — 누락이 아니라 '값이 모호'한 케이스.

    planner가 state["ambiguous_slots"]에 적은 항목 중 이 task의, 아직 안 채워진,
    이 agent가 받는 슬롯만 options 필드로 만든다. 이미 값이 있으면(=resume로 선택됨) skip."""
    out: list[dict] = []
    allowed = AGENT_SLOT_SCHEMAS.get(agent, set())
    for amb in state.get("ambiguous_slots") or []:
        if not isinstance(amb, dict) or amb.get("task_id") != task_id:
            continue
        slot = amb.get("slot")
        if not slot or slot not in allowed:
            continue
        if str(update_dict.get(slot) or "").strip():
            continue  # 이미 채워짐 (resume 선택값) → 다시 묻지 않음
        candidates = [str(c).strip() for c in (amb.get("candidates") or []) if str(c).strip()]
        if not candidates:
            continue
        out.append({
            "slot": slot,
            "label": amb.get("reason") or f"'{slot}' 값이 모호합니다. 선택해주세요.",
            "type": "choice",
            "options": candidates,
        })
    return out


def _merge_fields(*field_lists: list[dict]) -> list[dict]:
    """슬롯 기준 dedup — 같은 슬롯이 누락+모호로 겹치면 options가 있는 쪽(모호)을 우선한다."""
    by_slot: dict[str, dict] = {}
    for fields in field_lists:
        for field in fields:
            slot = field.get("slot")
            if slot not in by_slot or (field.get("options") and not by_slot[slot].get("options")):
                by_slot[slot] = field
    return list(by_slot.values())


def _compose_fields_message(fields: list[dict]) -> str:
    """Human prompt for the Streamlit fallback. Single field shows its own label;
    multiple shows them together so one answer can address all."""
    if len(fields) == 1:
        return fields[0]["label"]
    return "다음 정보를 입력해주세요 — " + " / ".join(f["label"] for f in fields)


def _ask_fields(fields: list[dict], current_task: dict) -> dict:
    """ONE interrupt for all missing slots; returns {slot: raw_value}.

    Canonical resume is a {slot: value} dict (React form / e2e client). A bare-string
    resume (degraded Streamlit fallback) fills ONLY the first slot — the rest stay
    missing and are re-asked next loop. No positional parsing of a string into multiple
    slots (that would silently mis-map values). `param` is the first slot for
    observability only; the source of truth is `fields`."""
    slots = [f["slot"] for f in fields]
    answer = interrupt({
        "type": HITLContractId.missing_param.value,
        "param": slots[0],
        "fields": fields,
        "message": _compose_fields_message(fields),
        "route": current_task.get("agent", ""),
    })
    if isinstance(answer, dict):
        return {s: answer.get(s, "") for s in slots}
    return {slots[0]: str(answer).strip()}


# REMOVE-IN-축4: this lot_ids format guard is an INTERIM backstop for the HITL
# str-fallback raw-insert silent-wrong. Remove it once 축4 unifies resume parsing
# through the planner (lot_ids single-path: planner-canonicalized values only) and
# that single path is verified — at which point a format guard here is redundant.
_LOT_ID_RE = re.compile(r"^[0-9Tt][A-Za-z0-9]{6,8}$")


def _validate_lot_ids(raw: Any) -> tuple[list[str] | None, list[str]]:
    """Split a comma-separated lot_ids answer, validate each token, UPPERCASE the valid
    ones so they match DB-stored lot ids (lot_id_variants only does 4<->T, not case —
    so a lowercased lot would otherwise miss and 0-row silently).

    Format (type sanity, not semantics): lotcd[3] + 4 chars, plus 1-2 for experimental
    lots = 7-9 alphanumeric, first char a digit or T (lotcd is digit-prefixed across
    products: 4SS/5QQ/6AG/…, with the 4<->T variant). All-or-nothing: if ANY token is
    malformed, returns (None, [bad tokens]) — never a partial fill (dropping a lot
    silently is itself a silent-wrong). Empty -> (None, [])."""
    tokens = [t.strip() for t in str(raw).split(",") if t.strip()]
    if not tokens:
        return None, []
    invalid = [t for t in tokens if not _LOT_ID_RE.match(t)]
    if invalid:
        return None, invalid
    return [t.upper() for t in tokens], []


def _apply_field(slot: str, value: Any, update_dict: dict) -> str | None:
    """Apply a HITL answer to its slot. Returns None on success, or a Korean rejection
    reason (slot left UNfilled) when the value fails its format guard — the caller
    re-asks with that reason instead of querying garbage (silent-wrong -> backstop)."""
    v = str(value).strip()
    if slot == "lot_ids":
        valid, invalid = _validate_lot_ids(v)
        if valid is None:
            bad = ", ".join(invalid) if invalid else v
            return (
                f"'{bad}'는 lot ID 형식이 맞지 않습니다 (공백·특수문자 없이 7~9자, "
                f"예: 4SS2DPD). 전체를 다시 입력해주세요. (다중: 4SS2DPD,4SSXCEW)"
            )
        update_dict["lot_ids"] = valid
        return None
    if slot == "map_oper":
        update_dict["map_oper"] = _normalize_map_oper(v) or "PT1H"
        return None
    update_dict[slot] = v
    return None


def _validate_lotcd_or_early_return(
    agent: str, update_dict: dict, current_task: dict, step_count: int
):
    """yield/wads lotcd VALUE validation — separate from the batch (an invalid value is
    not a missing slot). Invalid product code -> re-prompt interrupt -> persistent
    invalid -> early return. 6d ANCHOR: param=lotcd, message contains '형식이 아닙니다'."""
    normalized_lotcd = _normalize_product_lotcd(update_dict.get("lotcd"))
    if normalized_lotcd:
        update_dict["lotcd"] = normalized_lotcd
        return None
    invalid_value = update_dict.get("lotcd", "")
    emit_runtime_detail(
        "param.invalid",
        {
            "agent": agent,
            "param": "lotcd",
            "value": invalid_value,
            "reason": "lotcd_must_be_ascii_product_code",
            "action": "interrupt",
        },
        task_id=str(current_task.get("task_id") or ""),
    )
    emit_trace_event(
        "validation_issue",
        source="supervisor",
        severity="error",
        task_id=str(current_task.get("task_id") or ""),
        payload={
            "type": "invalid_param",
            "agent": agent,
            "param": "lotcd",
            "reason": "lotcd_must_be_ascii_product_code",
            "value_preview": preview_text(invalid_value),
        },
    )
    answer = interrupt({
        "type": HITLContractId.missing_param.value,
        "param": "lotcd",
        "fields": [{
            "slot": "lotcd",
            "label": _lotcd_prompt(agent, invalid_value=invalid_value),
            "type": "lotcd",
        }],
        "message": _lotcd_prompt(agent, invalid_value=invalid_value),
        "route": agent,
    })
    resumed = answer.get("lotcd", "") if isinstance(answer, dict) else answer
    normalized_lotcd = _normalize_product_lotcd(resumed)
    if not normalized_lotcd:
        return _invalid_lotcd_update(
            agent=agent,
            task_id=str(current_task.get("task_id") or ""),
            value=resumed,
            message=_lotcd_prompt(agent, invalid_value=resumed),
            step_count=step_count,
        )
    update_dict["lotcd"] = normalized_lotcd
    return None


def _require_agent_params(
    update_dict: dict, state: dict, current_task: dict, step_count: int
):
    """Validate per-agent required params via the structured HITL contract (축 1).

    Collects ALL missing required slots for the agent and asks them in ONE interrupt
    (fields[]) — two empty slots = one prompt, not two. Resume is a {slot: value} dict.
    Mutates update_dict in place; returns None to continue, or the invalid-lotcd
    early-return value to be returned as-is.

    interrupt() count: with a dict resume every slot fills in the first loop round, so
    there is exactly ONE interrupt() call. (A bare-string fallback fills one slot per
    round, re-asking the rest — never mis-mapping.) lotcd VALUE validation (yield always,
    wads if present) stays OUTSIDE the batch as a separate re-prompt + early-return.
    """
    agent = current_task["agent"]

    # 1. Batch-ask all missing required slots in one interrupt (dict resume). A value
    # that fails its format guard is NOT filled and is re-asked with the reason — so a
    # malformed answer becomes a backstop, never a silent garbage query.
    rejections: dict = {}
    task_id = str(current_task.get("task_id") or "")
    while True:
        fields = _merge_fields(
            _missing_required_fields(agent, update_dict, rejections),
            _ambiguous_fields(agent, task_id, update_dict, state),
        )
        if not fields:
            break
        answers = _ask_fields(fields, current_task)
        progressed = False
        rejections = {}
        for field in fields:
            value = answers.get(field["slot"], "")
            if not str(value).strip():
                continue  # unanswered (str fallback) — stays missing, re-asked next round
            progressed = True
            reason = _apply_field(field["slot"], value, update_dict)
            if reason:
                rejections[field["slot"]] = reason  # invalid -> re-ask with reason (unfilled)
        if not progressed:
            break  # nothing answered this round — avoid an infinite loop

    # map_oper is always normalized/defaulted (covers the present-but-not-asked case).
    if agent == "map_agent":
        update_dict["map_oper"] = _normalize_map_oper(update_dict.get("map_oper", "")) or "PT1H"

    # 2. lotcd value validation — separate from batch, early-return on persistent invalid.
    if agent == "yield_agent" or (agent == "wads_agent" and update_dict.get("lotcd")):
        early = _validate_lotcd_or_early_return(agent, update_dict, current_task, step_count)
        if early is not None:
            return early

    return None


def supervisor_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[
    Literal[
        "supervisor",  # confirm 거절 후 다음 pending task를 dispatch하러 재진입
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "lot_history_agent",
        "ppt_export",
        "relation_tree_agent",
        "wt_resp_agent",
        "mining_agent",
        "__end__",
    ]
]:
    """Supervisor 노드: ReAct 스타일 멀티스텝 루프.

    각 스텝마다 에이전트 결과를 확인하고 다음 행동을 결정합니다.
    Command를 반환하여 state 업데이트 + 라우팅을 하나로 통합합니다.
    """
    step_count = state.get("step_count", 0) + 1

    # 최대 스텝 강제 종료 (무한루프 방지) — recursion_limit=30과 정합 맞춤 (#R1 fix).
    # 사이클당 3노드(supervisor + agent + replanner) + setup 3노드 = 3n+3.
    # recursion_limit 30 안전 margin 유지를 위해 n+3 (cap 8)로 제한.
    # n=5 → max_steps=8 → 8 supervisor 진입 × 3노드 + setup 3 = 27 ≤ 30 ✅
    max_steps = min(len(state.get("task_plan", [])) + 3, 8) or 4
    if step_count > max_steps:
        logger.warning(
            "[Supervisor] 최대 스텝(%d/%d) 초과 → 강제 종료", step_count, max_steps
        )
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            severity="warning",
            payload={
                "target": "__end__",
                "reason": "max_steps_exceeded",
                "step_count": step_count,
                "max_steps": max_steps,
            },
        )
        return Command(
            update={
                "step_count": step_count,
                "messages": [
                    AIMessage(content="분석을 완료했습니다.", name="supervisor")
                ],
            },
            goto=END,
        )

    # HITL Gate나 plan_review가 실행 보류/취소를 결정한 경우 dispatch 전에 종료.
    if state.get("response"):
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            payload={
                "target": "__end__",
                "reason": "response_set",
                "step_count": step_count,
            },
        )
        return Command(update={"step_count": step_count}, goto=END)

    # ── pending_tasks 큐 기반 dispatch (task_builder가 생성한 작업 큐) ──
    pending = state.get("pending_tasks", [])
    if pending:
        current_task = pending[0]
        remaining = pending[1:]
        # ── S2: 선언적 __choice__ sentinel → dispatch 직전 택1 interrupt로 해소(택1 후속의 일반 경로) ──
        # 1-step/2-step(postwads 등) 모두 generic _resolve_followup_or_drop가 옵션 스펙으로 해소.
        if current_task.get("agent") == "__choice__":
            return _resolve_followup_or_drop(current_task, remaining, state, step_count)
        # ── 실행 확인 (B안): missing_param과 같은 dispatch 직전 멱등 구간에서 interrupt ──
        # confirm_tasks에 이 task가 있으면 확인. 거절 → Command(드롭 후 다음 pending/END) 반환.
        # 승인/미해당 → confirm_tasks 정리 dict(아래 update_dict에 merge).
        confirm_cleanup = _confirm_or_drop(current_task, remaining, state, step_count)
        if isinstance(confirm_cleanup, Command):
            return confirm_cleanup
        # 수정 승인(approve_with_changes) 슬롯을 dispatch 전에 merge — 이후 기존 검증·투영·
        # time_range 변환 경로를 그대로 탄다 (신규 가드 없음).
        edited = confirm_cleanup.pop("edited_params", None)
        if edited:
            current_task = {
                **current_task,
                "params": {**(current_task.get("params") or {}), **edited},
            }
        # C1: 코드 기반 chained input 해소 — wads_sql_result 메시지에서 lot_ids 자동 주입 (LLM 불필요)
        task_params = _resolve_chained_params(current_task, state)
        # planner가 yield 기간을 라벨 time_range로 넘긴 경우 → ref_date/periods/unit으로 변환(in-place).
        _apply_time_range_dict(task_params)

        task_message = AIMessage(
            content=f"[Task {current_task.get('task_id', '?')}] {current_task.get('goal', '')}",
            name="supervisor",
        )
        logger.info(
            "[Supervisor] queued task dispatch: %s → %s params=%s goal=%r (remaining=%d)",
            current_task.get("task_id"),
            current_task.get("agent"),
            summarize_params(task_params),
            current_task.get("goal", ""),
            len(remaining),
        )
        emit_runtime_detail(
            "supervisor.current_task",
            {
                "current_task": current_task,
                "remaining_tasks": remaining,
                "resolved_task_params": task_params,
            },
            task_id=str(current_task.get("task_id") or ""),
        )
        # task params를 state 필드로 projection — agent별 조건부로 관련 파라미터만 기록.
        # 전 agent 파라미터를 항상 덮으면 checkpoint의 다른 agent 값이 소실됨.
        agent = current_task["agent"]
        # S1-b: per-agent projection을 '작업셋'(proj)으로만 계산 — god-state로 smear하지 않는다.
        # 검증/HITL fill은 proj 위에서 수행하고, 결과는 current_task.params(워커가 읽는 단일 출처)로만 싣는다.
        proj = _project_task_params(agent, task_params, state)
        update_dict: dict = {
            "step_count": step_count,
            "current_task": {
                **current_task,
                "params": task_params,
            },
            "current_task_id": current_task.get("task_id", ""),
            "current_task_goal": current_task.get("goal", ""),
            "pending_tasks": remaining,
            "messages": [task_message],
            "agent_suggestion": "",
        }
        # confirm 승인 시 처리된 task_id를 confirm_tasks에서 제거 (미해당이면 {} → no-op).
        update_dict.update(confirm_cleanup)

        # Required-param validation + HITL interrupts (6c: extracted to a helper).
        # proj를 in-place로 검증/채움; None이면 계속, 아니면 invalid-lotcd early-return.
        # interrupt() call order는 그대로 — see _require_agent_params.
        early_return = _require_agent_params(
            proj, state, current_task, step_count
        )
        if early_return is not None:
            return early_return

        # 워커가 읽는 완전한 task.params = task_params + 해소된 proj (S1-a 단일 출처).
        final_task_params = {**task_params, **proj}
        current_params = {
            key: value
            for key, value in final_task_params.items()
            if value not in (None, "", [], {})
        }
        update_dict["current_task"] = {
            **current_task,
            "params": current_params,
        }
        # god-state 폐기: 의미 쿼리 슬롯(fail_type/cause_oper/wads_category)은 더 이상 영속화하지
        # 않는다 — 끈적한 글로벌이 옛 턴 값을 다음 턴에 stale하게 주입하던 leak의 원천이었다.
        # 각 워커는 자기 task.params에서만 읽고(S1-a), in-turn 상속은 followup default_slots(S2),
        # cross-turn 상속은 planner 재도출(recent_results)로 일어난다. lotcd/ref_date만 PPT 라벨·
        # planner 제품 힌트가 읽는 세션 표시 앵커로 유지.
        for _ck in ("lotcd", "ref_date"):
            if proj.get(_ck) not in (None, "", [], {}):
                update_dict[_ck] = proj[_ck]

        stream_event(
            "status",
            StatusEvent(
                message=f"▶ [{current_task['task_id']}] {current_task.get('goal', '')}",
                node="supervisor",
            ),
        )
        emit_runtime_detail(
            "supervisor.dispatch_state",
            {
                "goto": current_task["agent"],
                "update": update_dict,
            },
            task_id=str(current_task.get("task_id") or ""),
        )
        # supervisor_dispatch and agent_started report the same dispatched params —
        # build the summary once (6a: was duplicated across both emits).
        dispatched_params = summarize_params(
            {
                key: current_params.get(key)
                for key in (
                    "lotcd",
                    "lot_ids",
                    "wf_ids",
                    "groupkey",
                    "fail_type",
                    "cause_oper",
                    "map_type",
                    "map_oper",
                    "wf_mod",
                    "wf_rem",
                    "map_label",
                    "dh_query",
                    "ref_date",
                    "unit",
                    "periods",
                    "wads_start_tm",
                    "wads_end_tm",
                    "wads_category",
                )
                if key in current_params
            }
        )
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            task_id=str(current_task.get("task_id") or ""),
            payload={
                "target": current_task.get("agent", ""),
                "task_goal_preview": preview_text(current_task.get("goal", "")),
                "remaining_tasks": len(remaining),
                "step_count": step_count,
                "params": dispatched_params,
            },
        )
        emit_trace_event(
            "agent_started",
            source="supervisor",
            task_id=str(current_task.get("task_id") or ""),
            payload={
                "agent": current_task.get("agent", ""),
                "task_goal_preview": preview_text(current_task.get("goal", "")),
                "step_count": step_count,
                "params": dispatched_params,
            },
        )
        return Command(update=update_dict, goto=current_task["agent"])

    # planner가 빈 계획 반환 (JSON 파싱 실패 fallback)
    logger.warning(
        "[Supervisor] pending_tasks 없음 — canonical/task_builder 실패 fallback"
    )
    emit_trace_event(
        "supervisor_dispatch",
        source="supervisor",
        payload={
            "target": "__end__",
            "reason": "pending_tasks_empty",
            "step_count": step_count,
        },
    )
    messages = state.get("messages", [])
    planner_refusal = next(
        (
            m.content
            for m in reversed(messages)
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") == "planner"
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        None,
    )
    fallback_content = (
        planner_refusal or "요청을 이해하지 못했습니다. 다시 시도해 주세요."
    )
    return Command(
        update={
            "step_count": step_count,
            "messages": [AIMessage(content=fallback_content, name="supervisor")],
            "response": fallback_content,
        },
        goto=END,
    )


# ── HITL 확인 메커니즘 (task 속성 기반, B안) ──────────────────────────
# "확인 후 실행"은 그래프 노드가 아니라 task 속성으로 처리한다: replanner가 confirm이 필요한
# 후속 task를 pending에 넣으며 state["confirm_tasks"]에 메시지를 달면, supervisor가 dispatch
# 직전(무거운 부수효과 이전, missing_param과 같은 멱등 구간)에 interrupt로 확인한다. 체이닝이
# 늘어도 신규 노드 0개. 응답 해석은 전부 LLM이 하고(키워드 금지), 후속 task 생성은 결정론으로 둔다.
# 응답 3분류: approve(그대로 실행) / approve_with_changes(슬롯 수정 승인 — "응 근데 PT1C로"의
# 수정 조건이 조용히 버려지지 않게) / reject(드롭). 수정 값은 기존 검증·투영 경로를 그대로 탄다.

# 슬롯 사전(의미·형식) — 문구는 CANONICAL_PLANNER_SYSTEM_PROMPT(prompts.py)와 일치시킨다
# (프롬프트↔AGENT_SLOT_RULES↔supervisor 투영 3자 대조 계약). 여기 없는 슬롯(상류 chained-input
# 그룹 등)은 confirm 편집 대상으로 노출하지 않는다.
_CONFIRM_SLOT_SEMANTICS: dict[str, str] = {
    "lotcd": '3자 제품코드 (예: "4SS")',
    "time_range": '조회 기간 라벨 객체 {"unit":"weekly"|"monthly"|"daily","start":<라벨>,"end":<라벨>} '
                  '(라벨: weekly="YYYY-Www", monthly="YYYY-MM", daily="YYYY-MM-DD")',
    "unit": '"weekly"|"monthly"|"daily" 조회 단위',
    "periods": "조회 기간 수 (정수)",
    "ref_date": '기준일 "YYYYMMDD"',
    "wads_start_tm": '검출 조회 시작일 "YYYY-MM-DD"',
    "wads_end_tm": '검출 조회 종료일 "YYYY-MM-DD" (단일 날짜면 이것만)',
    "fail_type": '검출 파라미터/불량명 (예: "VTH", "TWT(T)")',
    "wads_category": '"PT1H"|"PT1C" — 검출/분석 공정 필터',
    "lot_ids": '7자 LOT ID 목록 (예: ["4SS2DPD","4SSXCEW"])',
    "wf_ids": '단일 lot 안의 wafer 번호(정수) 목록 (예: ["7","15"])',
    "groupkey": '"LOTID.WW" 점 구분 wafer 토큰, 여러 개면 콤마로 연결 (예: "4SAX9QA.07,4SSRUR0.01")',
    "map_type": '"binmap"|"cummap"|"all"',
    "map_oper": '"PT1H"|"PT1C" — 맵 조회 공정',
    "wf_mod": "wafer 번호 패턴 필터 N배수의 N (정수, 짝수=2)",
    "wf_rem": "wafer 번호 패턴 나머지 (정수, 홀수=1)",
    "cause_oper": '원인/기준 공정명 (예: "BG CMP")',
    "dh_query": "자연어 검색 질의 (불량 사례 서술 자유 텍스트)",
    "group_good": "양품 그룹 LOT ID/GROUPKEY 목록",
    "group_bad": "불량 그룹 LOT ID/GROUPKEY 목록",
    "tech": "기술/공정 세대 코드",
    "rank_limit": "상위 N개 제한 (정수, 기본 10)",
}

_CONFIRM_EDIT_RULES = (
    "사용자에게 아래 후속 작업을 실행할지 물었고, 사용자가 자유 응답을 보냈다.\n"
    "응답을 해석해 아래 JSON 하나만 출력해라:\n"
    '{"reasoning": "<판단 근거>", "decision": "approve"|"approve_with_changes"|"reject", '
    '"slot_updates": {}}\n'
    "규칙:\n"
    "- 순수 긍정(예/진행/확인/좋아) → decision=approve, slot_updates는 빈 객체.\n"
    "- 조건이나 수정을 달아 긍정 → decision=approve_with_changes, 사용자가 바꾼 슬롯만 "
    "slot_updates에 넣어라. 키는 아래 슬롯 사전에 있는 이름만 쓰고 값 형식은 사전을 따른다. "
    "응답에 없는 슬롯은 절대 지어내지 마라.\n"
    "- 부정/거절/무관심(아니오/취소/그만/됐어) → decision=reject, slot_updates는 빈 객체.\n"
    "예시:\n"
    '  "네 진행해주세요" → {"reasoning":"순수 긍정","decision":"approve","slot_updates":{}}\n'
    '  "응 근데 PT1C 검출만 봐줘" → {"reasoning":"승인하되 공정 필터를 PT1C로 변경",'
    '"decision":"approve_with_changes","slot_updates":{"wads_category":"PT1C"}}\n'
    '  "아니 됐어" → {"reasoning":"거절","decision":"reject","slot_updates":{}}\n'
)


class ConfirmDecision(BaseModel):
    reasoning: str = ""
    decision: Literal["approve", "approve_with_changes", "reject"] = "reject"
    slot_updates: dict = {}


def _interpret_confirm_response(answer: Any, current_task: dict) -> ConfirmDecision:
    """confirm 자유응답을 LLM으로 3분류 해석 + 수정 슬롯 추출 (키워드 매칭 금지).

    빈 응답(드레인 resume="")·LLM 실패·파싱 실패는 전부 안전하게 reject(후속 미실행)."""
    text = _answer_text(answer, "task_confirm")
    if not text:
        return ConfirmDecision(decision="reject")
    agent = str(current_task.get("agent") or "")
    allowed = AGENT_SLOT_SCHEMAS.get(agent) or set()
    slot_lines = "\n".join(
        f"- {s}: {_CONFIRM_SLOT_SEMANTICS[s]}"
        for s in sorted(allowed)
        if s in _CONFIRM_SLOT_SEMANTICS
    )
    system = (
        f"{_CONFIRM_EDIT_RULES}\n"
        f"제안한 작업: agent={agent}, 목표={current_task.get('goal', '')}\n"
        f"현재 파라미터: {json.dumps(current_task.get('params') or {}, ensure_ascii=False)}\n"
        f"이 작업에서 조정 가능한 슬롯 사전:\n{slot_lines or '(조정 가능한 슬롯 없음)'}"
    )
    try:
        raw = (
            _model.invoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                config={"callbacks": _lf_callbacks()},
            ).content
            or ""
        )
        return extract_json_from_llm(raw, ConfirmDecision)
    except Exception as e:
        logger.warning("[Confirm] 응답 해석 LLM 실패 (%s) — 거절 처리", e)
        return ConfirmDecision(decision="reject")


def _answer_text(answer: Any, key: str) -> str:
    """interrupt resume 원문 추출 (dict resume은 key 우선 — _interpret_* 상단 로직과 동일)."""
    if isinstance(answer, dict):
        return str(answer.get(key) or next(iter(answer.values()), "") or "").strip()
    return str(answer or "").strip()


def _confirm_or_drop(
    current_task: dict, remaining: list[dict], state: Dict[str, Any], step_count: int
):
    """dispatch 직전 confirm 처리 (missing_param과 같은 멱등 구간 — 무거운 부수효과 이전).
    confirm_tasks에 이 task가 없으면 {} 반환(그대로 진행).
    있으면 interrupt로 확인:
      - 승인 → {"confirm_tasks": <id 제거>} 반환(caller가 dispatch update에 merge).
      - 거절 → task 드롭. remaining 있으면 supervisor 재진입, 없으면 응답 set 후 END Command 반환."""
    confirm_tasks = state.get("confirm_tasks") or {}
    task_id = str(current_task.get("task_id") or "")
    message = confirm_tasks.get(task_id)
    if not message:
        return {}
    answer = interrupt({
        "type": "confirm",
        "interrupt_type": HITLContractId.task_confirm.value,
        "param": "task_confirm",
        "message": message,
        # InterruptEvent.options 스키마는 list[dict] — label/value 형태.
        "options": [{"label": "예", "value": "예"}, {"label": "아니오", "value": "아니오"}],
        "route": current_task.get("agent", ""),
    })
    new_confirm = {k: v for k, v in confirm_tasks.items() if k != task_id}
    decision = _interpret_confirm_response(answer, current_task)
    _ans = _answer_text(answer, "task_confirm")
    if decision.decision in ("approve", "approve_with_changes"):
        cleanup: dict = {"confirm_tasks": new_confirm}  # 승인 → dispatch 계속
        if decision.decision == "approve_with_changes" and decision.slot_updates:
            # 수정 승인: caller가 pop해서 current_task.params에 merge (state 필드 아님).
            cleanup["edited_params"] = dict(decision.slot_updates)
            emit_runtime_detail(
                "confirm.decision",
                {"decision": decision.decision, "slot_updates": decision.slot_updates},
                task_id=task_id,
            )
            if _ans:  # P1 연동: 수정 승인도 선호 학습 이벤트로 기록
                cleanup["memory_feedback"] = [make_feedback_event(
                    touchpoint="task_confirm",
                    decision="modified",
                    message=str(message),
                    user_answer=_ans,
                    agent=current_task.get("agent", ""),
                    goal=current_task.get("goal", ""),
                )]
        return cleanup
    logger.info("[Confirm] task %s 거절 → 드롭 (remaining=%d)", task_id, len(remaining))
    # 사용자 선호 학습: 거절 원문을 이벤트로 기록 (빈 응답=드레인은 오염 방지 위해 스킵).
    feedback = (
        [make_feedback_event(
            touchpoint="task_confirm",
            decision="rejected",
            message=str(message),
            user_answer=_ans,
            agent=current_task.get("agent", ""),
            goal=current_task.get("goal", ""),
        )]
        if _ans
        else []
    )
    if remaining:
        # 남은 task가 있으면 supervisor 재진입해 그걸 dispatch.
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": remaining,
                "confirm_tasks": new_confirm,
                "memory_feedback": feedback,
            },
            goto="supervisor",
        )
    # 남은 task 없음 → 직전 agent 결과로 턴 정상 종료 (replanner 완료판정과 동일 메시지 규칙).
    last_agent_msg = next(
        (
            m.content
            for m in reversed(state.get("messages", []))
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") in _AGENT_NAMES
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        "분석을 완료했습니다.",
    )
    return Command(
        update={
            "step_count": step_count,
            "pending_tasks": [],
            "confirm_tasks": new_confirm,
            "response": last_agent_msg,
            "memory_feedback": feedback,
        },
        goto=END,
    )


def _drop_choice_sentinel(
    remaining: list[dict],
    state: Dict[str, Any],
    step_count: int,
    feedback_event: dict | None = None,
) -> Command:
    """선택 미진행 → sentinel 드롭(remaining 재진입 / 없으면 직전 결과로 END). offer 플래그는
    제안 시 guard_key로 이미 set돼 있어 여기서 다시 set하지 않는다."""
    logger.info("[Choice] 미선택 → sentinel 드롭 (remaining=%d)", len(remaining))
    feedback = [feedback_event] if feedback_event else []
    if remaining:
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": remaining,
                "memory_feedback": feedback,
            },
            goto="supervisor",
        )
    last_agent_msg = next(
        (
            m.content
            for m in reversed(state.get("messages", []))
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") in _AGENT_NAMES
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        "분석을 완료했습니다.",
    )
    return Command(
        update={
            "step_count": step_count,
            "pending_tasks": [],
            "response": last_agent_msg,
            "memory_feedback": feedback,
        },
        goto=END,
    )


_SINGLE_CHOICE_SYSTEM = (
    "이어서 실행할 후속 작업 선택지를 사용자에게 제시했다. 아래 JSON은 제시된 선택지"
    "(options: label/value)와 사용자의 자유 응답(answer)이다. 사용자가 고른 선택지의 value를 "
    "정확히 하나만 출력해라. 아무것도 고르지 않거나 거절(안 함/취소/그만)이면 'none'을 출력해라. "
    "value 문자열 하나만 출력."
)


def _interpret_single_choice(answer: Any, options: list[dict]) -> dict | None:
    """사용자 응답 → 선택된 option dict(없으면 None). 키워드 매칭 금지.
    UI 클릭(value/label 정확일치) 즉시, 자유응답만 LLM이 value 분류(기존 choice 패턴 일반화)."""
    if isinstance(answer, dict):
        text = answer.get("followup_choice") or next(iter(answer.values()), "")
    else:
        text = answer
    text = str(text or "").strip()
    if not text:
        return None
    by_value = {str(o.get("value")): o for o in options if isinstance(o, dict)}
    by_label = {str(o.get("label")): o for o in options if isinstance(o, dict)}
    for opt in options:  # UI 클릭 경로 — LLM 불필요
        if text == str(opt.get("value")) or text == str(opt.get("label")):
            return opt if str(opt.get("value")) != "none" else None
    try:  # 자유응답 → LLM 분류
        verdict = (
            _model.invoke(
                [
                    {"role": "system", "content": _SINGLE_CHOICE_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"options": options, "answer": text}, ensure_ascii=False
                        ),
                    },
                ],
                config={"callbacks": _lf_callbacks()},
            ).content
            or ""
        ).strip()
    except Exception as e:
        logger.warning("[Followup] 선택 해석 LLM 실패 (%s) — 미선택 처리", e)
        return None
    opt = by_value.get(verdict) or by_label.get(verdict)  # LLM이 value 또는 label 반환 모두 수용
    return opt if opt and str(opt.get("value")) != "none" else None


# ctx 5종 — single-choice 선택 slots에 이 키가 있으면 god-state ctx로도 전파한다
# (다운스트림이 자기 schema 없이 god-state로 읽는 cause_oper 등 — S1 ctx 운반 보존).
_CTX_KEYS = ("lotcd", "fail_type", "cause_oper", "wads_category", "ref_date")


def _resolve_single_choice(
    current_task: dict,
    remaining: list[dict],
    state: Dict[str, Any],
    step_count: int,
    fu: dict,
) -> Command:
    """generic 택1 choice 해소 (한 노드 1 interrupt = replay-safe). 옵션 스펙만 보고 분기:
    - 1-step: `choice_options` + `choice_target_agent`로 한 번에 택1 → 그 agent task 치환.
    - 2-step: `prefilter_options`로 먼저 좁힌 뒤(선택 idx를 sentinel params에 실어 재진입=super-step
      분리), 그 idx의 `choice_option_sets[idx]`를 2차 택1로 제시. 선택지마다 per-option `agent` 허용.
    옵션·데이터(per-report 그룹 등)는 결과 생성 에이전트가 followup에 선언한다. 미선택/거절 → 드롭."""
    task_plan = state.get("task_plan") or []
    params = current_task.get("params") or {}
    selected_idx = params.get("selected_idx")

    # ── Step 1 (2-step 전용): prefilter로 먼저 한 번 좁힌다 (예: WADS report별 fail_type) ──
    prefilter = [o for o in (fu.get("prefilter_options") or []) if isinstance(o, dict)]
    if prefilter and selected_idx is None:
        options = prefilter + [{"label": "안 함", "value": "none"}]
        answer = interrupt({
            "type": "confirm",
            "interrupt_type": HITLContractId.postwads_choice.value,  # agent_server 가드/resume 호환 타입 재사용
            "param": "followup_choice",
            "message": str(fu.get("prefilter_message") or "어느 항목의 후속을 분석할까요?"),
            "options": options,
            "route": "",
        })
        chosen = _interpret_single_choice(answer, options)
        _ans = _answer_text(answer, "followup_choice")
        _opts = [{"label": o.get("label"), "value": o.get("value")} for o in options]
        _msg = str(fu.get("prefilter_message") or "어느 항목의 후속을 분석할까요?")
        if chosen is None:
            ev = (
                make_feedback_event(
                    touchpoint="postwads_choice", decision="declined",
                    message=_msg, user_answer=_ans, options=_opts,
                )
                if _ans
                else None
            )
            return _drop_choice_sentinel(remaining, state, step_count, feedback_event=ev)
        # 선택 idx만 sentinel params에 실어 재진입(super-step 분리). task_plan은 안 건드림.
        requeued = {**current_task, "params": {**params, "selected_idx": str(chosen.get("value"))}}
        update: dict = {"step_count": step_count, "pending_tasks": [requeued] + remaining}
        if _ans:
            update["memory_feedback"] = [make_feedback_event(
                touchpoint="postwads_choice", decision="selected",
                message=_msg, user_answer=_ans, options=_opts,
            )]
        # 사용자가 고른 fail_type을 dispatch 무관 전용 필드에 기록(사실만) → 다음 턴 planner 문맥 상속 판단용.
        ft = str(chosen.get("fail_type") or "").strip()
        if ft:
            update["selected_fail_type"] = ft
        logger.info("[Followup] prefilter 선택 idx=%s → 재진입", chosen.get("value"))
        return Command(update=update, goto="supervisor")

    # ── Step 2: 택1 메뉴 (1-step이면 곧장, 2-step이면 선택된 idx의 옵션셋) ──
    option_sets = fu.get("choice_option_sets")
    if isinstance(option_sets, list) and option_sets:
        try:
            base_options = option_sets[int(selected_idx)] if selected_idx is not None else option_sets[0]
        except (TypeError, ValueError, IndexError):
            base_options = option_sets[0]
    else:
        base_options = fu.get("choice_options") or []  # 1-step flat
    options = [o for o in base_options if isinstance(o, dict)] + [{"label": "안 함", "value": "none"}]
    answer = interrupt({
        "type": "confirm",
        "interrupt_type": HITLContractId.postwads_choice.value,
        "param": "followup_choice",
        "message": str(fu.get("choice_message") or "이어서 무엇을 할까요?"),
        "options": options,
        "route": "",
    })
    chosen = _interpret_single_choice(answer, options)
    _ans = _answer_text(answer, "followup_choice")
    _opts = [{"label": o.get("label"), "value": o.get("value")} for o in options]
    _msg = str(fu.get("choice_message") or "이어서 무엇을 할까요?")
    target_agent = str((chosen or {}).get("agent") or fu.get("choice_target_agent") or "")
    if chosen is not None and target_agent:
        slots = {**(fu.get("default_slots") or {}), **(chosen.get("slots") or {})}
        concrete = build_task_from_canonical_request(
            {"agent": target_agent, "slots": slots, "goal": str(chosen.get("goal") or "")},
            task_id=f"task_{len(task_plan) + 1}_{target_agent}",
        )
        update = {
            "step_count": step_count,
            "pending_tasks": [concrete] + remaining,
            "task_plan": task_plan + [concrete],
        }
        if _ans:
            update["memory_feedback"] = [make_feedback_event(
                touchpoint="postwads_choice", decision="selected",
                message=_msg, user_answer=_ans, options=_opts,
                agent=target_agent, goal=str(chosen.get("goal") or ""),
            )]
        for ck in _CTX_KEYS:  # 선택 slots의 ctx를 god-state로 전파(다운스트림 호환)
            if slots.get(ck) not in (None, "", [], {}):
                update[ck] = slots[ck]
        logger.info(
            "[Followup] choice 선택 → %s task 치환: %s", target_agent, concrete.get("task_id")
        )
        return Command(update=update, goto="supervisor")
    ev = (
        make_feedback_event(
            touchpoint="postwads_choice", decision="declined",
            message=_msg, user_answer=_ans, options=_opts,
        )
        if _ans
        else None
    )
    return _drop_choice_sentinel(remaining, state, step_count, feedback_event=ev)


def _resolve_followup_or_drop(
    current_task: dict, remaining: list[dict], state: Dict[str, Any], step_count: int
) -> Command:
    """dispatch 직전 __choice__ sentinel 해소. 옵션 스펙만 보는 generic 택1 해소기로 위임
    (1-step/2-step 모두 _resolve_single_choice가 처리)."""
    fu = (current_task.get("params") or {}).get("followup") or {}
    return _resolve_single_choice(current_task, remaining, state, step_count, fu)
