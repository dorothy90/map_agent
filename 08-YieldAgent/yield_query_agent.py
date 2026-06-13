# ============================================================
# Yield Agent Node — 수율 조회 + 이상감지 + LLM 분석
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from langchain_core.messages import convert_to_messages, AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe, get_client

from lf_utils import lf_callbacks as _lf_callbacks
from common import stream_event, timed, get_llm, extract_suggestion
from models import TokenEvent
from prompts import ANALYSIS_SYSTEM_PROMPT, ANALYSIS_USER_PROMPT
from result_contracts import attach_result_envelope
from yield_db import (
    _fetch_periods, _fetch_wafer_scatter, _fetch_lot_sql,
    _merge_lot_data, _parse_lot_specs, DEFAULT_PERIODS,
)
from yield_viz import (
    _build_table, _build_html_table, _build_scatter_html,
    _detect_anomalies, _build_lot_table, _build_lot_html_table,
    _save_html_to_file, _build_cummap_grid_html,
)

# ── 로깅 설정 ────────────────────────────────────────────
logger = logging.getLogger("yield_agent")


# ============================================================
# 헬퍼 함수 (디버그용)
# ============================================================
def pretty_print_message(message, indent=False):
    """개별 메시지를 포맷팅하여 출력합니다."""
    pretty_message = message.pretty_repr(html=True)
    if not indent:
        print(pretty_message)
        return

    indented = "\n".join("\t" + c for c in pretty_message.split("\n"))
    print(indented)


def pretty_print_messages(update, last_message=False):
    """그래프 실행 결과의 메시지 업데이트를 포맷팅하여 출력합니다."""
    is_subgraph = False

    if isinstance(update, tuple):
        ns, update = update
        if len(ns) == 0:
            return

        graph_id = ns[-1].split(":")[0]
        print(f"[서브그래프 {graph_id}로부터 업데이트]")
        print("\n")
        is_subgraph = True

    for node_name, node_update in update.items():
        if node_update is None or "messages" not in node_update:
            continue
        update_label = f"[노드 {node_name}로부터 업데이트]"
        if is_subgraph:
            update_label = "\t" + update_label

        print(update_label)
        print("\n")

        messages = convert_to_messages(node_update["messages"])
        if last_message:
            messages = messages[-1:]

        for m in messages:
            pretty_print_message(m, indent=is_subgraph)
        print("\n")


# ============================================================
# LLM 분석 함수
# ============================================================
@observe(name="analyze_with_llm")
@timed
def _analyze_with_llm(weeks_data: list[dict], table_str: str, lotcd: str, llm,
                      anomaly_params: list[dict], n: int = 4, config=None) -> str:
    """_detect_anomalies 결과를 LLM에게 전달하여 정리하는 함수."""
    if len(weeks_data) < 2:
        return "분석에 필요한 2기간 이상의 데이터가 부족합니다."

    prev_week = weeks_data[-2]
    curr_week = weeks_data[-1]

    if anomaly_params:
        lines = []
        for a in anomaly_params:
            lines.append(
                f"- {a['param']} ({a['process']}): {a['prev_val']} → {a['curr_val']} "
                f"({a['change_pct']:+.1f}%, {a['direction']})"
            )
        anomaly_summary = "\n".join(lines)
    else:
        anomaly_summary = "(이상 파라미터 없음)"

    user_prompt = ANALYSIS_USER_PROMPT.format(
        lotcd=lotcd,
        table=table_str,
        n=n,
        prev_week=prev_week.get("week", "?"),
        curr_week=curr_week.get("week", "?"),
        anomaly_summary=anomaly_summary,
    )

    try:
        result_text = ""
        for chunk in llm.stream(
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config={**(config or {}), "callbacks": _lf_callbacks()},
        ):
            token = chunk.content or ""
            if token:
                result_text += token
                stream_event("token", TokenEvent(
                    content=token, agent="yield_agent", node="yield_agent",
                ))
        return result_text
    except Exception as e:
        logger.error("[Yield Agent] LLM 분석 실패: %s", e, exc_info=True)
        return f"LLM 분석 중 오류가 발생했습니다: {e}"


# ============================================================
# LLM 모델 설정
# ============================================================
model = get_llm()


def _yield_lot_rows(merged: dict) -> list[dict]:
    return [
        {"lot_id": lot_id, **values}
        for lot_id, values in sorted((merged or {}).items())
        if isinstance(values, dict) and not str(lot_id).startswith("_")
    ]


def _yield_parameters_from_lot_rows(rows: list[dict]) -> list[str]:
    params: list[str] = []
    for row in rows:
        for key in row:
            if key not in {"lot_id", "_wf_count"} and not key.startswith("_"):
                params.append(key.removeprefix("pt1c_"))
    return list(dict.fromkeys(params))


def _yield_artifact_metadata(artifacts: list[dict]) -> dict[str, str | int | float | bool | None]:
    return {"artifact_count": len(artifacts or [])}


# ============================================================
# Yield Agent 노드 구현
# ============================================================
@observe(name="yield_agent_node")
@timed
def yield_agent_node(state: dict, config: RunnableConfig) -> dict:
    """Yield Agent 노드: State에서 파라미터를 읽어 API 호출 + 테이블 생성"""
    lotcd = state.get("lotcd", "4SS")
    ref_date_str = state.get("ref_date", date.today().strftime("%Y%m%d"))
    # Yield agent now always returns the full artifact for lotcd + period.
    # Parameter-specific filtering is no longer part of the runtime contract.
    filter_params = None
    unit    = state.get("unit") or "weekly"
    # Tolerant parse: "3", "3w", 3 -> 3; anything non-numeric -> 0 (falls back to
    # DEFAULT_PERIODS[unit] below). Never hard-crash on a stray planner string.
    _raw_periods = state.get("periods", 0)
    if isinstance(_raw_periods, bool):
        periods = 0
    elif isinstance(_raw_periods, int):
        periods = _raw_periods
    else:
        _digits = "".join(ch for ch in str(_raw_periods or "") if ch.isdigit())
        periods = int(_digits) if _digits else 0
    # Yield runtime contract is lotcd + period only. Full-lot comparison inputs
    # are intentionally ignored so the agent always emits the full period artifact.
    yield_lot_ids = ""
    yield_groupkey = ""

    # ── LOT 비교 모드 ────────────────────────────────────────
    if yield_lot_ids or yield_groupkey:
        logger.info("[YieldAgent] lot 비교 모드: lot_ids=%s groupkey=%s", yield_lot_ids, yield_groupkey)
        lot_specs = _parse_lot_specs(yield_lot_ids, yield_groupkey)

        logger.info("[Yield Agent] LOT 비교 모드: lot_specs=%s", lot_specs)

        lot_mode = "groupkey" if yield_groupkey else "lot"

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_pt1h = ex.submit(_fetch_lot_sql, lot_specs, "pt1h")
            f_pt1c = ex.submit(_fetch_lot_sql, lot_specs, "pt1c")
            pt1h_lot = f_pt1h.result()
            pt1c_lot = f_pt1c.result()
        merged = _merge_lot_data(pt1h_lot, pt1c_lot)

        if not merged:
            error_message = AIMessage(
                content="해당 lot 데이터를 조회할 수 없습니다. lot ID와 process를 확인해주세요.",
                name="yield_agent",
            )
            attach_result_envelope(
                error_message,
                logger=logger,
                source_agent="yield_agent",
                kind="summary",
                status="empty",
                title="yield_lot_compare",
                summary=error_message.content,
                entities={"lot_ids": state.get("lot_ids") or []},
                provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
            )
            return {
                "messages": [error_message],
                "weeks_data": [], "table_result": "", "anomaly_params": [],
                "past_steps": [(state.get("current_task_id", ""), "lot 데이터 조회 실패")],
            }

        table_str  = _build_lot_table(merged, mode=lot_mode, filter_params=filter_params)
        html_table = _build_lot_html_table(merged, mode=lot_mode, filter_params=filter_params)
        logger.debug("[Yield Agent] lot table:\n%s", table_str)

        lot_label = yield_groupkey or yield_lot_ids
        result_msg = f"[LOT 비교] {lot_label} pt1h+pt1c 수율 비교 테이블입니다."

        yield_artifacts = [{
            "type": "html",
            "mime": "text/html",
            "data": _save_html_to_file(html_table, "lot_table"),
            "title": "lot_compare_table",
        }]

        result_message = AIMessage(content=result_msg, name="yield_agent")
        lot_rows = _yield_lot_rows(merged)
        attach_result_envelope(
            result_message,
            logger=logger,
            source_agent="yield_agent",
            kind="table",
            status="success",
            title="yield_lot_compare",
            summary=result_msg,
            rows=lot_rows,
            entities={
                "lot_ids": [row["lot_id"].split(".", 1)[0] for row in lot_rows if row.get("lot_id")],
                "parameters": _yield_parameters_from_lot_rows(lot_rows),
            },
            artifacts=yield_artifacts,
            provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
            metadata={**_yield_artifact_metadata(yield_artifacts), "row_count": len(lot_rows)},
        )
        return {
            "messages": [result_message],
            "weeks_data": [],
            "table_result": table_str,
            "analysis_result": "",
            "yield_artifacts": yield_artifacts,
            "anomaly_params": [],
            "agent_suggestion": "",
            "past_steps": [(state.get("current_task_id", ""), result_msg[:300])],
        }

    # ── 기존 period 모드 ────────────────────────────────────
    try:
        ref_date = datetime.strptime(ref_date_str, "%Y%m%d").date()
    except ValueError:
        ref_date = date.today()

    n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)

    logger.info("[Yield Agent] 파라미터: lotcd=%s ref_date=%s unit=%s periods=%d", lotcd, ref_date_str, unit, n)

    logger.info("[Yield Agent] %s 최근 %d%s 데이터 조회 시작", lotcd, n, dict(weekly='주', monthly='달', daily='일').get(unit, unit))
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_periods = ex.submit(_fetch_periods, lotcd, ref_date, unit, periods)
        f_sc_pt1h = ex.submit(_fetch_wafer_scatter, lotcd, ref_date, unit, periods, "pt1h")
        f_sc_pt1c = ex.submit(_fetch_wafer_scatter, lotcd, ref_date, unit, periods, "pt1c")
        try:
            weeks_data = f_periods.result()
        except Exception as e:
            logger.error("[Yield Agent] _fetch_periods 실패: %s", e, exc_info=True)
            weeks_data = []
        try:
            pt1h_rows = f_sc_pt1h.result()
            pt1c_rows = f_sc_pt1c.result()
            for r in pt1h_rows:
                r["process"] = "PT1H"
            for r in pt1c_rows:
                r["process"] = "PT1C"
            wafer_rows = pt1h_rows + pt1c_rows
        except Exception as e:
            logger.error("[Yield Agent] wafer fetch 실패: %s", e, exc_info=True)
            wafer_rows = []

    logger.info("[Yield Agent] fetch 완료: weeks=%d, wafer_rows=%d", len(weeks_data), len(wafer_rows))

    if not weeks_data or all(wd.get("lotcount") == "-" for wd in weeks_data):
        error_message = AIMessage(
            content="데이터를 조회할 수 없습니다. Oracle DB 연결 또는 해당 기간 데이터를 확인해주세요.",
            name="yield_agent",
        )
        attach_result_envelope(
            error_message,
            logger=logger,
            source_agent="yield_agent",
            kind="summary",
            status="empty",
            title="yield_period",
            summary=error_message.content,
            entities={"products": [lotcd] if lotcd else []},
            provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
        )
        return {
            "messages": [error_message],
            "weeks_data": [], "table_result": "", "anomaly_params": [],
            "past_steps": [(state.get("current_task_id", ""), "주간 데이터 조회 실패")],
        }

    anomaly_params = _detect_anomalies(weeks_data)
    logger.info("[Yield Agent] 이상 감지: %d개 파라미터", len(anomaly_params))

    table_str = _build_table(weeks_data, lotcd, filter_params, unit=unit)
    html_table = _build_html_table(weeks_data, lotcd, filter_params, anomaly_params, unit=unit)
    logger.debug("[Yield Agent] table:\n%s", table_str)

    # LLM 분석과 cummap grid 생성을 병렬 실행
    logger.info("[Yield Agent] LLM 분석 + cummap grid 병렬 시작...")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_analysis = ex.submit(
            _analyze_with_llm, weeks_data, table_str, lotcd, model,
            anomaly_params=anomaly_params, n=n, config=config,
        )
        f_cummap = ex.submit(
            _build_cummap_grid_html, lotcd, ref_date, unit, n, anomaly_params,
        )
        analysis = f_analysis.result()
        cummap_html = f_cummap.result()

    yield_artifacts = [{
        "type": "html",
        "mime": "text/html",
        "data": _save_html_to_file(html_table, "yield_table"),
        "title": "yield_table",
    }]

    scatter_html = _build_scatter_html(wafer_rows, anomaly_params)
    if scatter_html:
        yield_artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": _save_html_to_file(scatter_html, "yield_scatter"),
            "title": "yield_scatter",
        })

    if cummap_html:
        yield_artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": _save_html_to_file(cummap_html, "yield_cummap"),
            "title": "yield_cummap",
        })

    unit_label = {"weekly": f"주간 (최근 {n}주)", "monthly": f"월별 (최근 {n}달)", "daily": f"일별 (최근 {n}일)"}.get(unit, f"최근 {n}개")
    result_msg = f"[{lotcd}] {unit_label} pt1h 수율 데이터입니다.\n"
    result_msg += f"기준: {ref_date_str}"

    if anomaly_params:
        anomaly_lines = "\n".join(
            f"- {a['param']} ({a['process']}): {a['prev_val']} → {a['curr_val']} ({a['change_pct']:+.1f}%, {a['direction']})"
            for a in anomaly_params[:5]
        )
        result_msg += f"\n\n---\n\n**⚠️ 이상 감지된 파라미터 ({len(anomaly_params)}개)**\n{anomaly_lines}"
        result_msg += "\n\n> WADS 열화 검출 리포트를 확인하시겠습니까?"
    else:
        result_msg += "\n\n> ✅ 이상 파라미터 없음 (±10% 기준)"

    result_message = AIMessage(content=result_msg, name="yield_agent")
    attach_result_envelope(
        result_message,
        logger=logger,
        source_agent="yield_agent",
        kind="table",
        status="success",
        title="yield_period",
        summary=result_msg,
        rows=weeks_data,
        entities={
            "products": [lotcd] if lotcd else [],
            # yield anomaly_params, WADS parameter, and supervisor fail_type are the same domain parameter class.
            "parameters": [p.get("param") for p in anomaly_params if p.get("param")],
        },
        artifacts=yield_artifacts,
        provenance={"task_id": state.get("current_task_id", ""), "task_goal": state.get("current_task_goal", "")},
        metadata={**_yield_artifact_metadata(yield_artifacts), "row_count": len(weeks_data), "anomaly_count": len(anomaly_params)},
    )

    analysis, agent_suggestion = extract_suggestion(analysis)

    return {
        "messages": [result_message],
        "weeks_data": weeks_data,
        "table_result": table_str,
        "analysis_result": analysis,
        "yield_artifacts": yield_artifacts,
        "anomaly_params": anomaly_params,
        "agent_suggestion": agent_suggestion,
        "past_steps": [(
            state.get("current_task_id", ""),
            result_msg[:300] + (
                f" | anomaly_params: 열화={['%s(%s)' % (p['param'], p['process']) for p in anomaly_params if p.get('direction') == '열화']}"
                f", 개선={['%s(%s)' % (p['param'], p['process']) for p in anomaly_params if p.get('direction') == '개선']}"
                if anomaly_params else ""
            ),
        )],
    }
