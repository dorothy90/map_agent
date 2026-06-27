"""recent_results — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import json
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately

from result_contracts import (
    ResultContractError,
    build_recent_result_index_entry,
    prune_recent_results,
)
from local_trace import emit_runtime_detail, preview_text

load_dotenv(override=True)

from orch_utils import _unique_texts, logger




_MAX_CONTEXT_TOKENS = 30_000


def _get_recent_turns(
    messages: list, max_turns: int = 5, exclude_last: HumanMessage | None = None
) -> list[dict]:
    """최근 N턴의 Human/AI 메시지를 chat format으로 변환.

    ToolMessage, SystemMessage 등은 스킵.
    exclude_last로 지정된 메시지는 제외 (rewrite 대상이므로 별도 전달).
    turn 수 제한 후 토큰 예산 초과 시 오래된 턴부터 추가 제거.
    """
    eligible = [
        m
        for m in messages
        if (exclude_last is None or m is not exclude_last)
        and isinstance(m, (HumanMessage, AIMessage))
        and (
            isinstance(m, HumanMessage)
            or (isinstance(m.content, str) and m.content.strip())
        )
    ]
    # 1차: 턴 수 제한
    turn_limited = eligible[-(max_turns * 2) :]
    # 2차: 토큰 예산 제한 (SQL 결과·아티팩트 JSON 등 긴 메시지 대응)
    trimmed = trim_messages(
        turn_limited,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=_MAX_CONTEXT_TOKENS,
    )
    result = []
    for m in trimmed:
        if isinstance(m, HumanMessage):
            result.append(
                {
                    "role": "user",
                    "content": m.content
                    if isinstance(m.content, str)
                    else str(m.content),
                }
            )
        elif isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                result.append({"role": "assistant", "content": content})

    # 멀티턴 중 LLM에 "안 들어간" 턴을 가시화 (verbose에서만 렌더)
    excluded = [m for m in eligible if all(m is not t for t in trimmed)]
    if excluded:
        emit_runtime_detail(
            "history.excluded",
            {
                "eligible": len(eligible),
                "kept_for_llm": len(trimmed),
                "excluded": [
                    {
                        "role": "user" if isinstance(m, HumanMessage) else "ai",
                        "preview": preview_text(
                            m.content if isinstance(m.content, str) else str(m.content)
                        ),
                    }
                    for m in excluded[:6]
                ],
            },
        )
    return result


# Full result blocks are emitted newest-first until this CHARACTER budget is hit;
# older results then fall back to a 1-line summary so the planner still knows they
# exist. A resource budget governs how much detail is shown — not a magic turn count
# (#2: the planner was capped to the last 3 of K=10 accumulated results, so it was
# blind to older referenceable results even though resolution covered all K).
_RECENT_CONTEXT_FULL_BUDGET_CHARS = 6000

_RECENT_CONTEXT_PREFERRED_KEYS = (
    "parameter", "param", "fail_type", "cnt", "count", "detection_count",
    "lot_id", "lot_ids", "wf_ids", "lotcd", "groupkey", "groupkeys",
    "map_oper", "category", "end_tm",
)


def _recent_result_full_block(result: dict[str, Any]) -> list[str]:
    """Full detail for one result: summary + per-report tags + up to 5 compact rows."""
    rows = result.get("rows") or []
    columns = [
        {"name": column.get("name"), "semantic": column.get("semantic")}
        for column in (result.get("columns") or [])
        if isinstance(column, dict)
    ][:8]
    block = [
        "result: "
        f"result_id={result.get('result_id', '')} "
        f"source_agent={result.get('source_agent', '')} "
        f"kind={result.get('kind', '')} "
        f"title={preview_text(result.get('title', ''), max_chars=80)} "
        f"row_count={len(rows)} "
        f"columns={json.dumps(columns, ensure_ascii=False)}"
    ]
    # carry-both: expose per-report ordinal/parameter/oper so the planner is aware of
    # "N번째 리포트" targets, not just the per-wafer rows.
    reports = result.get("reports") or []
    report_tags = [
        f"{rp.get('report_index', i)}:{rp.get('parameter', '')}/{rp.get('map_oper', '')}"
        for i, rp in enumerate(reports, start=1)
        if isinstance(rp, dict)
    ]
    if report_tags:
        block.append(f"reports: [{', '.join(report_tags)}]")
    for index, row in enumerate(rows[:5], start=1):
        if not isinstance(row, dict):
            continue
        compact_row = {
            key: row.get(key)
            for key in _RECENT_CONTEXT_PREFERRED_KEYS
            if row.get(key) not in (None, "")
        }
        if compact_row:
            block.append(f"row_{index}: {json.dumps(compact_row, ensure_ascii=False)}")
    return block


def _recent_result_condensed_line(result: dict[str, Any]) -> str:
    """One-line summary for older results kept for awareness (beyond the full budget)."""
    rows = result.get("rows") or []
    reports = result.get("reports") or []
    params = _unique_texts(
        [str(rp.get("parameter") or "") for rp in reports if isinstance(rp, dict)]
        or [str(r.get("parameter") or r.get("fail_type") or "") for r in rows if isinstance(r, dict)]
    )[:6]
    return (
        "result(condensed): "
        f"result_id={result.get('result_id', '')} "
        f"source_agent={result.get('source_agent', '')} "
        f"kind={result.get('kind', '')} "
        f"row_count={len(rows)} reports={len(reports)} "
        f"params={json.dumps(params, ensure_ascii=False)}"
    )


def _recent_results_prompt_context(recent_results: list[dict[str, Any]]) -> str:
    """Compact structured result context for follow-up planning.

    Exposes result metadata and row order (not raw assistant prose) so follow-ups can
    refer to prior tables. #2: shows ALL accumulated results so the planner is aware of
    every result it can reference — full detail for the most recent within a character
    budget, a 1-line summary for older ones (no magic turn cap; budget governs).
    """
    if not recent_results:
        return ""
    lines = [
        "Recent structured results are ordered as displayed to the user. Follow-up references to ranks, rows, or prior items refer to that displayed order.",
    ]
    # Newest-first: keep emitting full blocks until the character budget is exhausted;
    # always keep at least the newest result full.
    budget = _RECENT_CONTEXT_FULL_BUDGET_CHARS
    full_from = len(recent_results)
    for i in range(len(recent_results) - 1, -1, -1):
        cost = sum(len(x) for x in _recent_result_full_block(recent_results[i]))
        if i != len(recent_results) - 1 and cost > budget:
            break
        budget -= cost
        full_from = i
    for i, result in enumerate(recent_results):
        if i >= full_from:
            lines.extend(_recent_result_full_block(result))
        else:
            lines.append(_recent_result_condensed_line(result))
    return "\n".join(lines)


def _extract_result_payloads(message: Any) -> list[Any]:
    """Return full ResultEnvelope payloads stored on a message, if present."""

    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    result_payload = additional_kwargs.get("result")
    if result_payload is None:
        return []
    if isinstance(result_payload, list):
        return result_payload
    return [result_payload]


def _build_recent_results_index(messages: list) -> list[dict]:
    """Derive the bounded resolver index from message ResultEnvelopes.

    Source of truth stays in AIMessage.additional_kwargs["result"]. This index
    is rebuilt from messages, pruned to compact metadata/rows, and stored only
    as supervisor scratchpad for later reference resolution.
    """

    entries: list[dict] = []
    for message in messages:
        for payload in _extract_result_payloads(message):
            try:
                entries.append(build_recent_result_index_entry(payload))
            except ResultContractError as exc:
                logger.warning(
                    "[RecentResults] invalid ResultEnvelope skipped: %s",
                    str(exc).splitlines()[0],
                )
            except Exception as exc:
                logger.warning("[RecentResults] result index build failed: %s", exc)

    return prune_recent_results(entries)


def _accumulate_recent_results(current: list | None, messages: list) -> list:
    """축3: accumulate the resolver index across turns, decoupled from message prune.

    Merge the carried index (current) with the results currently in messages, dedup by
    result_id (prune_recent_results keeps the newest per id), and cap to
    MAX_RECENT_RESULTS (K). A result stays referenceable for K results even after its
    message is pruned (30-msg cap), so ordinal refs survive far longer than the last 3.
    Source of truth stays messages; the index is an accumulated projection. Lifetime:
    agent_server seeds recent_results=[] on a new session, so it clears per session.
    """
    return prune_recent_results((current or []) + _build_recent_results_index(messages))


def _recent_results_update_from_messages(
    messages: list, current_recent_results: list | None
) -> dict:
    """Return an overwrite update for the accumulated recent_results index."""

    recent_results = _accumulate_recent_results(current_recent_results, messages)
    if recent_results == (current_recent_results or []):
        return {}
    return {"recent_results": recent_results}


def _recent_results_update(state: dict) -> dict:
    """Return a state update only when the derived recent_results index changed."""

    return _recent_results_update_from_messages(
        state.get("messages", []),
        state.get("recent_results", []),
    )


def _latest_result_envelope_for_task(messages: list, task_id: str) -> dict | None:
    """Find the latest ResultEnvelope attached to an agent message for task_id."""

    if not task_id:
        return None
    for message in reversed(messages or []):
        for payload in reversed(_extract_result_payloads(message)):
            if not isinstance(payload, dict):
                continue
            provenance = payload.get("provenance") or {}
            if provenance.get("task_id") == task_id:
                return payload
    return None
