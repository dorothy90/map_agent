"""
Mining Agent — 데이터 마이닝 분석 도구 (ReAct)
==============================================
양품/불량 그룹을 비교해 gini 기반 기여 파라미터를 마이닝한다.

구조 (wads_agent 패턴 미러):
- mining_analysis @tool: API 호출 → df_GINI를 ContextVar storage에 stash(노드가 회수해
  artifact/envelope 빌드) + gini rows를 LLM에 반환(질의응답 가능).
- create_react_agent: LLM이 질문을 보고 (a) 컨텍스트의 이전 gini 표로 바로 답하거나
  (b) 새 그룹/파라미터면 mining_analysis를 호출.
- mining_agent_node: ReAct 실행 → 답변 추출 → df_GINI를 동적 HTML(행 선택·버튼) artifact로
  렌더 + result envelope 부착 + gini rows를 state(mining_rows)에 머금음(다음 turn Q&A 재사용).

실제 mining API는 `mining_dummy_api.fetch_mining_dataframes`로 더미 대체.
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Any, Dict, List

import pandas as pd
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langfuse import observe
from pydantic import BaseModel, Field

from lf_utils import lf_callbacks as _lf_callbacks
from common import timed, get_llm, html_escape as _h, extract_suggestion, is_transient_error
from prompts import MINING_SYSTEM_PROMPT
from result_contracts import attach_result_envelope
from mining_dummy_api import fetch_mining_dataframes

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.mining_agent")

# ── ContextVar (요청별 격리) — ReAct tool은 텍스트만 반환하므로 df는 여기로 빼낸다 ──
_mining_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "mining_payload"
)


def _get_mining_storage() -> Dict[str, Any] | None:
    try:
        return _mining_payload_var.get()
    except LookupError:
        return None


def _as_list(value: Any) -> List[str]:
    """그룹값을 문자열 리스트로 정규화 (타입 가드 수준만)."""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


def _mining_sig(
    lot_cd: str,
    group_good: List[str],
    group_bad: List[str],
    fail_name: str,
    mode: str,
    tech: str,
    user_id: str,
) -> str:
    """분석을 정의하는 입력 서명 (rank_limit 제외 — 그건 표의 view일 뿐).
    같은 서명 = 같은 분석 → API 재호출 불필요(기계적 memo)."""
    return "|".join(
        [
            lot_cd,
            ",".join(sorted(group_good or [])),
            ",".join(sorted(group_bad or [])),
            fail_name,
            mode,
            tech,
            user_id,
        ]
    )


class MiningParsingResult(BaseModel):
    """mining_analysis 호출에 필요한 슬롯. 의미·예시를 명시해 LLM이 올바른 슬롯에 값을 넣게 한다."""

    lot_cd: str = Field(..., description='3자 제품코드, 예: "4SS"')
    group_good: List[str] = Field(
        default_factory=list,
        description='양품 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH083", "TSAH085"]',
    )
    group_bad: List[str] = Field(
        default_factory=list,
        description='불량 그룹 LOT ID/GROUPKEY 목록 (LOT ID는 7자 영숫자), 예: ["TSAH090", "TSAH092"]',
    )
    fail_name: str = Field(
        ...,
        description='파라미터(불량명), 괄호 안에 bin category 포함. 예: "DIBL(D)", "BVDS(B)", "TWT(T)". '
        "유효값: DIBL(D)/BVDS(B)/VMIN(M)/IDDQ(F)/GATE_OX(G)/FMAX(X)/TWT(T)/IGATE(P)/RON(R)",
    )
    mode: str = Field(..., description='분석 모드(공정), 예: "PT1H", "PT1C"')
    tech: str = Field(..., description="기술/공정 세대 코드")
    user_id: str = Field(..., description="요청 사용자 ID")
    rank_limit: int = Field(10, description="상위 N개 제한, 예: 10")


def _call_minig_api(
    lot_cd: str,
    group_good: List[str],
    group_bad: List[str],
    fail_name: str,
    mode: str,
    tech: str,
    user_id: str,
    rank_limit: int,
) -> Dict[str, pd.DataFrame]:
    """mining API 호출 → DataFrame 묶음(dict[str, pd.DataFrame]) 반환.

    현재는 더미(`fetch_mining_dataframes`)로 위임. 추후 실제 호출로 교체.
    """
    logger.info(
        "[_call_minig_api] lot_cd=%s fail_name=%s mode=%s tech=%s user_id=%s rank_limit=%s "
        "good=%d bad=%d",
        lot_cd,
        fail_name,
        mode,
        tech,
        user_id,
        rank_limit,
        len(group_good),
        len(group_bad),
    )
    dataframes = fetch_mining_dataframes(
        lot_cd=lot_cd,
        group_good=group_good,
        group_bad=group_bad,
        fail_name=fail_name,
        mode=mode,
        tech=tech,
        user_id=user_id,
        rank_limit=rank_limit,
    )
    return dataframes


# df_GINI 표시/LLM 노출용 핵심 컬럼 (순서 유지). 실제 API 스키마 기준.
# 도메인에 맞게 가감 가능. {fail_type}_AREA 처럼 이름이 가변인 컬럼은 제외.
_CORE_COLS = [
    "Rank",
    "oper_det_desc",
    "Key Value",
    "Operation Type",
    "GINI",
    "Score",
    "Commonality",
    "Purity",
    "JSD",
    "WRAcc",
    "Ratio",
    "Rank_Ratio",
    "Mode",
    "FailName",
]


def _project_cols(rows: List[dict]) -> List[dict]:
    """행을 핵심 컬럼(_CORE_COLS, 존재하는 것만 순서대로)으로 투영해 경량화.
    _CORE_COLS가 하나도 없으면(더미/미지 스키마) 원본 유지 → 회귀안전."""
    if not rows:
        return rows
    present = [c for c in _CORE_COLS if c in rows[0]]
    if not present:
        return rows
    return [{c: r.get(c) for c in present} for r in rows]


def _analyze_gini(df_GINI: pd.DataFrame) -> Dict[str, Any]:
    """df_GINI → 순위 정렬된 행 목록. 타입 가드 수준의 방어만 (값/컬럼 변형 없음)."""
    if df_GINI is None or df_GINI.empty:
        return {"status": "empty", "rows": 0, "items": []}
    # 정렬: API가 매긴 Rank 오름차순 우선, 없으면 GINI 내림차순, 둘 다 없으면 원순서.
    df = df_GINI
    if "Rank" in df.columns:
        df = df.sort_values("Rank", ascending=True)
    elif "GINI" in df.columns:
        df = df.sort_values("GINI", ascending=False)
    return {
        "status": "ok",
        "rows": int(len(df)),
        "items": df.to_dict(orient="records"),
    }


@tool
def mining_analysis(
    lot_cd: str = "",
    group_good: List[str] | None = None,
    group_bad: List[str] | None = None,
    fail_name: str = "",
    mode: str = "",
    tech: str = "",
    user_id: str = "",
    rank_limit: int = 0,
) -> Dict[str, Any]:
    """양품/불량 그룹을 비교해 gini 기반 기여 파라미터를 마이닝한다.

    슬롯은 supervisor가 이미 확정해 두므로 인자를 몰라도 그냥 호출하면 된다 — 생략한 인자는
    확정 슬롯(storage 기본값)으로 자동 채워진다. 다른 그룹/파라미터로 새로 분석할 때만 인자를 명시.

    Args:
        lot_cd: 3자 제품코드, 예: "4SS". 생략 시 확정 슬롯 사용.
        group_good: 양품 그룹 LOT ID/GROUPKEY 목록, 예: ["TSAH083"]. 생략 시 확정 슬롯 사용.
        group_bad: 불량 그룹 LOT ID/GROUPKEY 목록, 예: ["TSAH090"]. 생략 시 확정 슬롯 사용.
        fail_name: 파라미터(불량명), 예: "DIBL(D)". 생략 시 확정 슬롯 사용.
        mode: 분석 모드(공정), 예: "PT1H", "PT1C". 생략 시 확정 슬롯 사용.
        tech: 기술/공정 세대 코드. 생략 시 확정 슬롯 사용.
        user_id: 요청 사용자 ID. 생략 시 확정 슬롯 사용.
        rank_limit: 상위 N개 제한. 생략 시 확정 슬롯(기본 10) 사용.

    Returns:
        result_summary dict. gini_analysis.items 가 gini 행(parameter/gini/...) 목록 — 이 표를 근거로 답하라.
    """
    # supervisor가 확정한 슬롯(storage 기본값)으로 미지정 인자 보완 (wads _defaults 패턴)
    storage = _get_mining_storage()
    d = (storage or {}).get("_defaults", {}) if storage else {}
    lot_cd = lot_cd or d.get("lot_cd", "")
    group_good = group_good if group_good else d.get("group_good", [])
    group_bad = group_bad if group_bad else d.get("group_bad", [])
    fail_name = fail_name or d.get("fail_name", "")
    mode = mode or d.get("mode", "")
    tech = tech or d.get("tech", "")
    user_id = user_id or d.get("user_id", "")
    rank_limit = rank_limit or d.get("rank_limit", 10)

    # 기계적 memo: 분석 입력이 머금은 결과와 동일하면 API 재호출 없이 그 표를 반환.
    # (의미 판단 아님 — 같은 입력은 결정론적으로 같은 결과. 재호출/표 잘림 방지.)
    cur_sig = _mining_sig(lot_cd, group_good, group_bad, fail_name, mode, tech, user_id)
    prior_rows = (storage or {}).get("_prior_rows") or []
    prior_sig = (storage or {}).get("_prior_sig") or ""
    if prior_rows and prior_sig and cur_sig == prior_sig:
        if storage is not None:
            storage["gini_rows"] = prior_rows
            storage["meta"] = {"lot_cd": lot_cd, "fail_name": fail_name, "mode": mode}
            storage["sig"] = cur_sig
            storage["cached"] = True
        logger.info(
            "[mining_analysis] 동일 입력 → 머금은 gini %d행 재사용(API 생략)", len(prior_rows)
        )
        return {
            "status": "success",
            "lot_cd": lot_cd,
            "fail_name": fail_name,
            "mode": mode,
            "files_downloaded": [],
            "gini_analysis": {"status": "ok", "rows": len(prior_rows), "items": prior_rows},
        }

    dataframes = _call_minig_api(
        lot_cd,
        group_good,
        group_bad,
        fail_name,
        mode,
        tech,
        user_id,
        rank_limit,
    )

    for file_name, df in dataframes.items():
        logger.info("[mining_analysis] %s rows=%d", file_name, len(df))

    df_GINI = dataframes.get("df_GINI.parq", pd.DataFrame())
    gini_analysis = _analyze_gini(df_GINI)
    gini_rows = _project_cols(gini_analysis.get("items", []))  # 핵심 컬럼으로 경량화
    # LLM 반환·envelope도 동일 subset으로 경량화 (artifact/머금기와 일관)
    gini_analysis["items"] = gini_rows
    gini_analysis["rows"] = len(gini_rows)

    # ReAct tool 반환은 텍스트뿐 → df_GINI 행을 storage에 stash. 노드가 회수해 artifact/머금기.
    if storage is not None:
        storage["gini_rows"] = gini_rows
        storage["meta"] = {"lot_cd": lot_cd, "fail_name": fail_name, "mode": mode}
        storage["sig"] = cur_sig
        storage["cached"] = False

    result_summary = {
        "status": "success",
        "lot_cd": lot_cd,
        "fail_name": fail_name,
        "mode": mode,
        "files_downloaded": [k for k, v in dataframes.items() if not v.empty],
        "gini_analysis": gini_analysis,
    }
    return result_summary


# ── df_GINI → 동적 HTML(행 선택·버튼) artifact ───────────────────────────────
# iframe(allow-scripts)에서 그대로 동작하는 자체완결 HTML+JS (lot_history_agent 패턴).
def _render_mining_gini_html(rows: List[dict], lot_cd: str, fail_name: str, mode: str) -> str:
    if not rows:
        return "<div class='mn-empty'>gini 결과가 없습니다.</div>"
    import json as _json

    cols = list(rows[0].keys())
    th = "".join(f"<th>{_h(str(c).upper())}</th>" for c in cols) + "<th>TAS</th>"
    body = ""
    for i, r in enumerate(rows):
        oper = str(r.get("oper_det_desc") or r.get("parameter") or r.get("param") or f"row{i}")
        kv = str(r.get("Key Value") or "")
        param = _h(oper)
        tds = ""
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                tds += f"<td style='text-align:right'>{v:,.4f}</td>"
            elif isinstance(v, int):
                tds += f"<td style='text-align:right'>{v:,}</td>"
            else:
                tds += f"<td>{_h(str(v))}</td>"
        # TAS 버튼: 그 행의 oper_det_desc/Key Value를 data-*로 운반 → 클릭 시 서버 호출.
        tas_td = (
            "<td class='tas-cell'>"
            f"<button class='tas-btn' data-oper='{_h(oper)}' data-kv='{_h(kv)}'>TAS</button>"
            "<span class='tas-out'></span></td>"
        )
        body += f"<tr class='mn-row' data-idx='{i}' data-param='{param}'>{tds}{tas_td}</tr>"

    style = (
        "<style>"
        ".mn-card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#fff;font-size:13px}"
        ".mn-title{font-weight:700;color:#1a1a2e;margin-bottom:8px}"
        ".mn-bar{margin:8px 0;display:flex;align-items:center;gap:8px}"
        ".mn-bar button{padding:4px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;cursor:pointer}"
        ".mn-bar button:disabled{opacity:.5;cursor:not-allowed}"
        ".mn-count{color:#64748b;font-size:12px}"
        ".mn-t{width:100%;border-collapse:collapse}"
        ".mn-t th,.mn-t td{border:1px solid #e5e7eb;padding:8px;text-align:left}"
        ".mn-t th{background:#f8f9fa;text-transform:uppercase;font-size:12px;letter-spacing:.04em}"
        ".mn-row{cursor:pointer}.mn-row:hover>td{background:#f1f5f9}"
        ".mn-row.sel>td{background:#e0f2fe}"
        ".tas-cell{white-space:nowrap}"
        ".tas-btn{padding:2px 8px;border:1px solid #c7d2fe;border-radius:6px;background:#eef2ff;cursor:pointer}"
        ".tas-btn:disabled{opacity:.5;cursor:not-allowed}"
        ".tas-out{margin-left:6px;font-size:12px;color:#475569}"
        "</style>"
    )
    toolbar = (
        "<div class='mn-bar'>"
        "<button id='mn-relation' disabled>선택 파라미터 연관분석</button>"
        "<span class='mn-count' id='mn-count'>0개 선택</span>"
        "</div>"
    )
    table = f"<table class='mn-t'><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"
    # 행 선택(토글) + TAS 버튼(서버 /mining/tas 호출 → 같은 행에 결과 표시).
    # 상대 URL → vite 프록시 경유(CORS 무관). srcDoc iframe(allow-scripts)에서 동작.
    js = (
        "<script>(function(){"
        "var sel=new Set();"
        "var rows=document.querySelectorAll('.mn-row');"
        "var rbtn=document.getElementById('mn-relation');"
        "var cnt=document.getElementById('mn-count');"
        "rows.forEach(function(tr){tr.addEventListener('click',function(){"
        "var k=tr.getAttribute('data-param');"
        "if(sel.has(k)){sel.delete(k);tr.classList.remove('sel');}"
        "else{sel.add(k);tr.classList.add('sel');}"
        "cnt.textContent=sel.size+'개 선택';rbtn.disabled=sel.size===0;});});"
        "rbtn.addEventListener('click',function(){console.log('[mining] selected',Array.from(sel));});"
        f"var LOTCD={_json.dumps(lot_cd)};var FAILNAME={_json.dumps(fail_name)};"
        "document.querySelectorAll('.tas-btn').forEach(function(b){"
        "b.addEventListener('click',function(e){"
        "e.stopPropagation();"  # 행 선택 토글과 충돌 방지
        "var out=b.parentElement.querySelector('.tas-out');"
        "b.disabled=true;out.textContent=' 실행중…';"
        "fetch('/mining/tas',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({lotcd:LOTCD,oper_det_desc:b.dataset.oper,key_value:b.dataset.kv,fail_name:FAILNAME})})"
        ".then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})"
        ".then(function(x){out.textContent=' '+(x.ok?('✓ '+(x.j.message||x.j.status||'ok')):('✗ '+(x.j.detail||'오류')));})"
        ".catch(function(err){out.textContent=' ✗ '+err;})"
        ".finally(function(){b.disabled=false;});"
        "});});"
        "})();</script>"
    )
    return (
        f"{style}<div class='mn-card'><div class='mn-title'>"
        f"Mining gini 기여 파라미터 — {_h(lot_cd)} / {_h(fail_name)} ({_h(mode) or '-'})</div>"
        f"{toolbar}{table}{js}</div>"
    )


def _format_prior_gini(rows: List[dict]) -> str:
    """머금은 이전 gini rows를 프롬프트 주입용 텍스트로 직렬화 (없으면 '')."""
    if not rows:
        return ""
    import json

    return json.dumps(rows[:30], ensure_ascii=False)


def _unique_params(rows: List[dict]) -> List[str]:
    out: List[str] = []
    for r in rows:
        p = str(r.get("oper_det_desc") or r.get("parameter") or r.get("param") or "").strip()
        if p and p not in out:
            out.append(p)
    return out


# ── ReAct 그래프 ─────────────────────────────────────────────────────────────
_mining_model = get_llm(
    model=os.getenv("MINING_AGENT_MODEL") or os.getenv("RETRIEVE_CHAIN_MODEL")
)


def _mining_prompt(state: dict) -> list:
    """슬롯 컨텍스트 + 머금은 이전 gini 표를 system prompt에 주입."""
    sp = MINING_SYSTEM_PROMPT
    lotcd = state.get("_lotcd", "")
    fail_name = state.get("_fail_name", "")
    mode = state.get("_mode", "")
    gg = state.get("_group_good", []) or []
    gb = state.get("_group_bad", []) or []
    ctx = (
        f"\n\n[실행 컨텍스트] lotcd={lotcd}, fail_name={fail_name}, mode={mode}, "
        f"good={len(gg)}개, bad={len(gb)}개"
    )
    sp += ctx
    prior = state.get("_prior_gini", "")
    if prior:
        sp += (
            "\n\n[이전 mining gini 결과 — 전체 표 (JSON)]\n" + prior +
            "\n\n위 표가 이미 완전한 gini 결과다. 상위 N개·특정 파라미터의 gini 값·정렬·필터·"
            "요약·비교 같은 질문은 반드시 이 표에서 직접 계산해 답하라. 이런 경우 mining_analysis 를 "
            "절대 다시 호출하지 마라(같은 분석 재실행은 낭비이고 표를 잘라먹는다). "
            "오직 사용자가 '다른 그룹/제품/파라미터로 새로 분석'을 명시적으로 요청할 때만 호출하라."
        )
    return [SystemMessage(content=sp)] + list(state.get("messages", []))


_mining_graph = create_react_agent(
    model=_mining_model,
    tools=[mining_analysis],
    prompt=_mining_prompt,
)


@observe(name="mining_agent_node", capture_input=False, capture_output=False)
@timed
def mining_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """ReAct 노드: state 슬롯으로 mining 실행/질의응답. df_GINI는 동적 HTML artifact +
    result envelope로 내보내고, gini rows는 state(mining_rows)에 머금어 다음 turn Q&A에 재사용."""
    state = {**state, **((state.get("current_task") or {}).get("params") or {})}  # S1-a: task params 우선, scalar fallback
    lot_cd = (state.get("lotcd") or "").strip()
    fail_name = (state.get("fail_type") or "").strip()
    mode = (state.get("wads_category") or "").strip()
    group_good = _as_list(state.get("group_good"))
    group_bad = _as_list(state.get("group_bad"))
    tech = (state.get("tech") or "").strip()
    user_id = (state.get("user_id") or "").strip()
    rank_limit = state.get("rank_limit") or 10
    current_task_id = state.get("current_task_id", "")
    task_goal = state.get("current_task_goal", "")
    prior_rows = state.get("mining_rows") or []

    logger.info(
        "[Mining Agent] lot_cd=%s fail_name=%s mode=%s tech=%s good=%d bad=%d rank_limit=%s prior_rows=%d",
        lot_cd,
        fail_name,
        mode,
        tech,
        len(group_good),
        len(group_bad),
        rank_limit,
        len(prior_rows),
    )

    # tool stash 저장소 — _defaults로 LLM 누락 인자 보완, gini_rows로 결과 회수
    storage: Dict[str, Any] = {
        "gini_rows": None,
        "meta": None,
        "_defaults": {
            "lot_cd": lot_cd,
            "group_good": group_good,
            "group_bad": group_bad,
            "fail_name": fail_name,
            "mode": mode,
            "tech": tech,
            "user_id": user_id,
            "rank_limit": rank_limit,
        },
        # 머금은 결과 + 그 서명 → tool이 동일 입력이면 API 생략하고 재사용
        "_prior_rows": prior_rows,
        "_prior_sig": state.get("mining_sig", ""),
    }
    _mining_payload_var.set(storage)

    messages = state.get("messages", [])
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    query = task_goal or (
        last_human.content if last_human else f"{lot_cd} {fail_name} mining 분석"
    )

    try:
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 12}
        result = _mining_graph.invoke(
            {
                "messages": [HumanMessage(content=query)],
                "_lotcd": lot_cd,
                "_fail_name": fail_name,
                "_mode": mode,
                "_group_good": group_good,
                "_group_bad": group_bad,
                "_tech": tech,
                "_user_id": user_id,
                "_rank_limit": rank_limit,
                "_prior_gini": _format_prior_gini(prior_rows),
            },
            config=sub_config,
        )
    except Exception as e:
        if is_transient_error(e):
            logger.warning("[Mining Agent] transient 오류, retry 위임: %s", e)
            raise
        logger.error("[Mining Agent] 영구 오류: %s", e, exc_info=True)
        err = AIMessage(content=f"Mining 분석 중 오류가 발생했습니다: {e}", name="mining_agent")
        attach_result_envelope(
            err,
            logger=logger,
            source_agent="mining_agent",
            kind="summary",
            status="error",
            title="mining_gini",
            summary=err.content,
        )
        return {
            "messages": [err],
            "agent_suggestion": "",
            "past_steps": [(current_task_id, "mining 오류")],
        }

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "Mining 분석에 실패했습니다."
    answer, agent_suggestion = extract_suggestion(answer)

    new_rows = storage.get("gini_rows")  # tool이 호출됐을 때만 채워짐
    cached = storage.get("cached", False)  # True면 머금은 표 재사용(새 분석 아님)
    msg = AIMessage(content=answer, name="mining_agent")

    if new_rows is not None and not cached:
        meta = storage.get("meta") or {}
        artifacts = [
            {
                "type": "html",
                "mime": "text/html",
                "data": _render_mining_gini_html(
                    new_rows,
                    meta.get("lot_cd", lot_cd),
                    meta.get("fail_name", fail_name),
                    meta.get("mode", mode),
                ),
                "title": "mining_gini",
            }
        ]
        attach_result_envelope(
            msg,
            logger=logger,
            source_agent="mining_agent",
            kind="table",
            status="success" if new_rows else "empty",
            title="mining_gini",
            summary=answer,
            rows=new_rows[:50],
            entities={"parameters": _unique_params(new_rows)},
            provenance={"task_id": current_task_id, "task_goal": task_goal},
            artifacts=artifacts,
        )
        logger.info("[Mining Agent] 새 분석 → artifact 1 + gini rows %d 머금음", len(new_rows))
        return {
            "messages": [msg],
            "mining_artifacts": artifacts,
            "mining_rows": new_rows,
            "mining_sig": storage.get("sig", ""),
            "agent_suggestion": agent_suggestion,
            "past_steps": [(current_task_id, answer[:300])],
        }

    # tool 미호출 또는 동일입력 memo(cached) → 머금은 표로 Q&A: 텍스트 답변만,
    # mining_rows/sig/artifact는 미반환(=불변) 유지. 새 분석이 아니므로 표를 다시 그리지 않는다.
    attach_result_envelope(
        msg,
        logger=logger,
        source_agent="mining_agent",
        kind="summary",
        status="success",
        title="mining_gini",
        summary=answer,
        provenance={"task_id": current_task_id, "task_goal": task_goal},
    )
    logger.info("[Mining Agent] 재호출 없이 머금은 표로 응답")
    return {
        "messages": [msg],
        "agent_suggestion": agent_suggestion,
        "past_steps": [(current_task_id, answer[:300])],
    }


if __name__ == "__main__":
    # 커널 테스트
    import json

    # 1) tool 직접 호출 (LLM이 부르는 경로와 동일)
    out = mining_analysis.invoke(
        {
            "lot_cd": "4SS",
            "group_good": ["TSAH083", "TSAH085"],
            "group_bad": ["TSAH090"],
            "fail_name": "DIBL(D)",
            "mode": "PT1H",
            "tech": "T1",
            "user_id": "dorothy90",
            "rank_limit": 5,
        }
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))

    # 2) HTML artifact 렌더 확인
    rows = out["gini_analysis"]["items"]
    print(_render_mining_gini_html(rows, "4SS", "DIBL(D)", "PT1H")[:600])

    # 3) 함수형 노드: 상류 공유키(state) 경로 테스트 (LLM 필요)
    state = {
        "lotcd": "4SS",
        "fail_type": "DIBL(D)",
        "wads_category": "PT1H",
        "group_good": ["TSAH083", "TSAH085"],
        "group_bad": ["TSAH090"],
        "tech": "T1",
        "user_id": "dorothy90",
        "rank_limit": 5,
        "current_task_id": "t1",
        "current_task_goal": "4SS DIBL(D) mining 분석",
        "messages": [HumanMessage(content="4SS DIBL(D) mining 분석해줘")],
    }
    node_out = mining_agent_node(state, {})
    print("== NODE ==")
    print(node_out["messages"][0].content)
    print("artifacts:", len(node_out.get("mining_artifacts", [])), "rows:", len(node_out.get("mining_rows", [])))
