# P2: task_confirm 수정 승인(edit-approve) — HITL 응답 분류 확장

## Context

agents-from-scratch의 HITL 응답 분류(accept / edit / ignore / respond) 차용 2순위. 현재 task_confirm 게이트는 **yes/no 이진**이다(`_CONFIRM_SYSTEM`, node_supervisor.py:890-894 — "오직 'yes' 또는 'no' 한 단어만 출력"). 사용자가 "응, 근데 PT1C로 봐줘"처럼 **수정 조건을 달아 승인**하면 LLM이 yes로 접어버리고 수정 내용은 조용히 버려진다 — 현재 라이브 갭. plan_review는 modify를 지원하지만(node_plan_review.py:158-163) task_confirm에는 없다.

P1(사용자 선호 메모리)과 독립적으로 동작하며, P1이 먼저 구현돼 있으면 수정 승인을 memory_feedback 이벤트로도 기록한다.

**Scope v1: 자유 텍스트 경로만 — 프론트엔드 무변경.** 사용자는 이미 채팅 입력으로 confirm에 자유 응답할 수 있고(`_resume_is_interrupt_answer`, supervisor.py:48), 해석기만 3분류로 확장한다.

## 검증된 코드 사실

- confirm 흐름: dispatch 직전 `_confirm_or_drop(current_task, remaining, state, step_count)`(node_supervisor.py:927-983, 호출부 ~702) → `{}`(미해당) / `{"confirm_tasks": …}`(승인) / `Command`(거절 드롭). 승인 직후 호출부가 `task_params = _resolve_chained_params(current_task, state)`(~706)를 계산 → **수정 슬롯은 이 지점 전에 current_task.params에 merge해야 함**.
- `_interpret_confirmation(answer) -> bool`(899-925): dict resume이면 `task_confirm` 키 텍스트 추출, 빈 응답·LLM 실패 → 거절(안전 기본값).
- 슬롯 스키마: `AGENT_SLOT_RULES`(canonical_request.py:6-57) — agent별 allowed/required. 의미 문구는 `CANONICAL_PLANNER_SYSTEM_PROMPT`(prompts.py:37-296)에 있음. **3자 대조 계약**: 해석기 프롬프트의 슬롯 의미는 이 두 곳과 일치해야 함.
- 수정 값 검증: merge된 값은 기존 경로(`_require_agent_params` 포맷 가드, lotcd 검증, `_apply_time_range_dict`)를 그대로 통과 — **신규 검증 코드 불필요**.
- JSON 파싱: `extract_json_from_llm(raw, Model)`(common.py:274) — OpenRouter 호환 수동 파싱 패턴.
- 드레인: 게이트 대기 중 새 질문이 오면 `resume=""` 드레인(agent_server.py:510-525) — 빈 응답=거절 유지 필수.

## 금지사항 (CLAUDE.md / 과거 실사례)

- LLM이 낸 slot_updates를 코드 allow-list로 걸러 drop하지 않는다 (wads_category silent drop 재발 방지 — 기존 `_project_task_params` 투영이 이미 agent별 소비를 담당).
- 키워드로 승인/수정/거절 분류하지 않는다 — 전부 LLM 해석.
- 해석 LLM이 판단 근거를 갖도록 프롬프트에 슬롯 의미·타입·예시를 명시 (이름만 나열 금지).

## 변경 내용 (node_supervisor.py ~60줄 + 테스트)

### 1. 해석기 교체: `_interpret_confirmation` → `_interpret_confirm_response`

```python
class ConfirmDecision(BaseModel):
    reasoning: str = ""
    decision: Literal["approve", "approve_with_changes", "reject"]
    slot_updates: dict = {}          # approve_with_changes일 때만

def _interpret_confirm_response(answer: Any, current_task: dict) -> ConfirmDecision:
    # dict resume → task_confirm 키(기존 로직), 빈 텍스트 → reject
    # LLM: _CONFIRM_EDIT_SYSTEM + user(제안 message·응답 원문) → extract_json_from_llm(raw, ConfirmDecision)
    # 실패 → reject (기존 안전 기본값 유지)
```

`_CONFIRM_EDIT_SYSTEM` 프롬프트 (동적 조립):

- 컨텍스트: 제안한 후속 작업(agent, goal, 현재 params), 사용자 자유 응답
- 해당 agent의 allowed 슬롯 목록(AGENT_SLOT_RULES에서 도출)에 **의미·타입·예시를 붙인 슬롯 사전** — 문구는 CANONICAL_PLANNER_SYSTEM_PROMPT와 일치시킬 것 (예: `map_oper("PT1H"|"PT1C" 공정 단계)`, `lot_ids(쉼표구분 lot id, 예: "4SS2DPD,4SSXCEW")`)
- 규칙: 순수 긍정 → approve / 조건·수정을 달아 긍정 → approve_with_changes + 바뀐 슬롯만 slot_updates / 부정·무관심 → reject. 응답에 없는 슬롯은 절대 지어내지 않는다.
- worked example 3개: "네 진행해주세요"→approve / "응 근데 PT1C로 봐줘"→approve_with_changes {"map_oper":"PT1C"} / "아니 됐어"→reject

### 2. `_confirm_or_drop` 확장

- 승인(approve): 기존과 동일 `{"confirm_tasks": new_confirm}` 반환.
- 수정 승인(approve_with_changes): `{"confirm_tasks": new_confirm, "edited_params": decision.slot_updates}` 반환.
- 거절(reject): 기존 드롭 경로 그대로.
- 관측: `emit_runtime_detail("confirm.decision", {"decision": …, "slot_updates": …}, task_id=…)`.
- (P1 연동, P1 구현된 경우만) approve_with_changes → memory_feedback 이벤트 `decision="modified"` 추가.

### 3. 호출부(~702) merge

```python
confirm_cleanup = _confirm_or_drop(current_task, remaining, state, step_count)
if isinstance(confirm_cleanup, Command):
    return confirm_cleanup
edited = confirm_cleanup.pop("edited_params", None)
if edited:
    current_task = {**current_task, "params": {**(current_task.get("params") or {}), **edited}}
# 이하 기존: task_params = _resolve_chained_params(current_task, state) …
```

수정 값은 이후 기존 검증·투영·time_range 변환 경로를 그대로 타므로 추가 가드 없음. `update_dict["current_task"]`와 `pending→current` 전환도 기존 코드가 처리.

## v2 (이번 slice 제외 — 기록만)

confirm interrupt payload에 현재 params를 prefill한 `fields`를 실어 클릭 편집 폼 제공. `InterruptEvent`는 이미 fields를 통과시키고(agent_server.py:309-320) Hitl.tsx의 fields 폼(62-130)을 재사용할 수 있으나, prefill 값 표시 + `{"task_confirm": "예", …수정슬롯}` dict resume 프로토콜이 필요해 프론트 변경 수반 → 후속.

## 검증 (프롬프트 수정 게이트: e2e 동일실패셋 + golden 필수)

1. **단위**: LLM 몽키패치(canned JSON)로 3분류 파싱·slot_updates 추출·파싱 실패 시 reject 확인.
2. **e2e** (uvicorn :8001 PID kill 후 재기동, `Application startup complete` 확인): confirm 게이트가 뜨는 기존 회귀 케이스에서
   - "예" → 기존과 동일 dispatch (traces `supervisor.current_task`의 resolved_task_params 동일)
   - "아니오" → 드롭 (기존 동일)
   - "응 근데 PT1C로 봐줘" → dispatch된 task params에 `map_oper=PT1C` 반영 확인 (traces 단언)
   - 새 질문 입력(드레인 `resume=""`) → 거절 처리 유지
3. **회귀**: `pytest tests/test_e2e_regression.py -v` — baseline 26/30 동일실패셋 유지(플레이크는 격리 재실행 판별), `python tests/golden_exploratory.py` 무회귀.
