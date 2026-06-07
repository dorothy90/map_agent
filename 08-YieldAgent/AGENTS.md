# 08-YieldAgent Architecture

## Architecture Overview

```
START → rewrite → supervisor ⟷ [yield_agent, wads_agent, map_agent] → END
```

- **rewrite**: 사용자 메시지 리라이팅 (모호한 표현 → 명확한 쿼리)
- **supervisor**: ReAct 스타일 멀티스텝 라우터 (최대 4스텝)
- **yield_agent** / **wads_agent** / **map_agent**: 각각 수율 조회, WADS 리포트, 웨이퍼 맵 처리
- 라우팅: `Command(goto=...)` 기반, supervisor ← agent 루프

## Agent Registry

| Node | 역할 | 모델 | 입력 | 출력 |
|------|------|------|------|------|
| rewrite | 쿼리 리라이팅 | gpt-oss-120b | messages (최근 5턴) | HumanMessage (동일 ID 교체) |
| supervisor | 멀티스텝 라우팅 | gpt-oss-120b | messages + state | Command(goto, update) |
| yield_agent | pt1h/pt1c/gms 수율 조회 + 이상감지 + LLM 분석 | gpt-oss-120b | lotcd, ref_date, unit, periods | weeks_data, yield_artifacts, anomaly_params |
| wads_agent | WADS 열화 리포트 조회 (서브그래프) | RETRIEVE_CHAIN_MODEL | lotcd, wads_end_tm | wads_artifacts |
| map_agent | binmap/cummap 웨이퍼 맵 시각화 | — | map_lot_id, map_type 등 | map_artifacts |

## YieldQueryState 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| messages | list (add_messages) | 대화 히스토리 |
| step_count | int | supervisor 루프 카운터 |
| lotcd | str | 제품코드 (3-4자, 예: 4SS) |
| ref_date | str | 기준 날짜 YYYYMMDD |
| unit | str | weekly / monthly / daily |
| periods | int | 조회 기간 수 (0=기본값) |
| filter_params | list | 표시할 파라미터 필터 |
| yield_artifacts | list (operator.add) | Yield HTML 아티팩트 누적 |
| wads_artifacts | list (operator.add) | WADS HTML 아티팩트 누적 |
| map_artifacts | list (operator.add) | Map HTML 아티팩트 누적 |
| anomaly_params | list | 이상감지 결과 |
| agent_suggestion | str | 에이전트 후속 제안 |

## Known Oracle Quirks

- WEEK 컬럼: `"YYYY-WW"` 포맷 (예: `2026-06`), agent 내부는 `"2026-W06"` 포맷 → 변환 필요
- GMS 테이블: PERIOD_DATE `"YYYYWW"` / `"YYYYMM"` / `"YYYYMMDD"` (숫자 문자열)
- CLOB 컬럼: output type handler로 `DB_TYPE_LONG` 변환 필요 (wads_agent)
- LOT_ID: 대소문자 변환 (`lot_id_variants`) 필요

## HITL Contract (missing_param)

When `supervisor._require_agent_params` finds required slots empty, it asks the user
via a **single structured interrupt**. (plan_review is a *separate* contract — see below.)

**Interrupt payload (server → front), `InterruptEvent`:**
```jsonc
{
  "type": "missing_param",
  "fields": [                          // SOURCE OF TRUTH — every missing slot
    {
      "slot": "lot_ids",               // canonical state field to fill
      "label": "맵을 조회할 Lot ID …",  // human prompt for this field
      "type": "lot_ids",               // lot_ids | map_oper | lotcd | cause_oper
      "required_any_group": "lot_or_groupkey",  // optional: one of the group suffices
      "validation_hint": "PT1H|PT1C"            // optional
    }
    // … all other missing slots
  ],
  "param": "lot_ids",   // representative ONLY (single slot → that slot; batch → first).
                        // observability / back-compat. NEVER infer "only this slot is
                        // missing" from param in a batch — read `fields`.
  "message": "다음 정보를 입력해주세요 — …",  // combined one-liner for Streamlit/text UI
  "route": "map_agent"
}
```

**Resume (front → server), `ChatRequest.resume_value`:** a **`{slot: value}` dict** — the
single canonical path. React renders one input per `fields` entry and replies with the
dict. A bare **string** is the degraded Streamlit fallback: it fills **only the first
slot**, leaving the rest missing so they are re-asked next round. **No positional
parsing** of a string into multiple slots — that silently mis-maps values to slots.

**One interrupt() call per dispatch:** all missing slots for the agent are collected into
`fields` and asked with a single `interrupt()`. Never split into per-slot calls (that
regresses to N round-trips). Because only one interrupt is ever pending, LangGraph's
multiple-pending / interrupt-id-map resume path is never hit; a `{slot:value}` dict is
treated as a single resume value (its keys are slot names, not interrupt-id hexdigests).

**plan_review is a separate contract:** the structured `fields` contract is
missing_param-only. `plan_review` keeps its own path — approve / modify / cancel **text**
resume fed to the plan-review LLM.

**invalid-lotcd is outside the batch:** an invalid (non-product-code) lotcd is a *value*
problem, not a missing slot — it is a separate re-prompt + early-return
(`_validate_lotcd_or_early_return`, the 6d anchor), kept apart from slot collection.

**Status:** server + `tests/e2e_client.py` (React stand-in, sends dict resume) implement
this contract. The **React form client is not yet written** — it is the pending client
implementation of this section.

## File Map

```
08-YieldAgent/
├── agent_server.py       # FastAPI SSE backend
├── app.py                # Streamlit UI
├── supervisor.py         # LangGraph StateGraph + supervisor/rewrite nodes
├── prompts.py            # 모든 시스템/유저 프롬프트 (중앙화)
├── yield_query_agent.py  # Yield agent node (축소됨)
├── yield_db.py           # Yield Oracle SQL 쿼리
├── yield_viz.py          # Yield HTML 테이블, scatter plot, 이상감지
├── wads_agent.py         # WADS agent graph + node
├── wads_tools.py         # WADS @tool 함수
├── map_agent.py          # Map agent (binmap/cummap)
├── common.py             # Oracle pool, 날짜 유틸, 공유 상수
├── models.py             # SSE 이벤트 모델
├── lf_utils.py           # Langfuse 헬퍼
├── AGENTS.md             # 에이전트 아키텍처 문서
└── SKILL.md              # 스킬 레퍼런스
```
