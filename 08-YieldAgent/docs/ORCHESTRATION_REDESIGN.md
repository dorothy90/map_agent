# 멀티에이전트 오케스트레이션 재설계 (Design Doc)

> 상태: **Draft / 검토용** · 대상 브랜치: `claude/langgraph-multi-agent-review-04y8ul`
> 범위: `08-YieldAgent/supervisor.py`(3941 LOC)를 중심으로 한 plan-and-execute 오케스트레이션
> 목표: god-state·god-node·고정 plan을 해체하고 task-scoped I/O + 선언적 체이닝 + 병렬 fan-out으로 전환

---

## 1. 현재 아키텍처 (As-Is)

```
START → planner → task_normalizer_validator → plan_review
                                                   │
                                                   ▼
           ┌────────────────────────────────► supervisor ──Command(goto)──► [9 worker]
           │                                       │  ▲                          │
    should_end(response?)                       END │  └──────── replanner ◄──────┘
           │
```

| 요소 | 구현 위치 | 특징 |
|---|---|---|
| planner | `supervisor.py:563` | LLM canonicalizer. raw history 미사용, `canonical_request`만 생성 |
| validator | `supervisor.py:861` | 결정론 정규화/검증 |
| plan_review | `supervisor.py:998` | task ≥2일 때만 HITL 승인 |
| supervisor | `supervisor.py:2545` | `pending[0]` 단건 dispatch ReAct 루프 + sentinel 치환 + HITL |
| replanner | `supervisor.py:1897` | chained-input fill + 결정론 후속 제안 |
| worker ×9 | 각 `*_agent.py` | **대부분 단발 함수** (yield/map LLM 0회) |

### 1.1 핵심 사실 (코드 근거)

1. **워커는 공유 scalar state에서 입력을 읽는다.**
   - `map_agent.py:944-955` → `state.get("lot_ids"/"map_oper"/"groupkey"/"map_type"…)`
   - `wads_agent.py:383-387` → `state.get("lotcd"/"wads_end_tm"/"fail_type"/"wads_category")`
   - `relation_tree_agent.py:62-74`, `mining_agent.py:456-466`, `fail_history_agent.py:260-264` 등 전부 동일 패턴.
   - supervisor가 `_project_task_params`(`supervisor.py:2129`)로 task params를 **공유 scalar에 smear**하고, 그 안에 **agent별 상속 규칙이 하드코딩**(yield는 lotcd 상속+lot_ids clear, wads는 상속 안 함 — `:2142-2163`).

2. **출력은 타입드 `ResultEnvelope`로 통일돼 있다.** 전 9개 agent + supervisor + agent_server가 `result_contracts`를 사용. → 결과 경계는 이미 좋음.

3. **워커 반환 키를 state reducer와 대조한 결과:**
   - reducer(안전): `messages`(add_messages), `past_steps`, 모든 `*_artifacts`(operator.add). `supervisor.py:3721,3738,3865`.
   - overwrite(병렬 시 충돌): `agent_suggestion`(**전 워커가 write**, reader는 `supervisor.py:615` 1곳), 그 외 agent-고유(`map_result`/`analysis_result`/`weeks_data`/`anomaly_params`/`fail_history_results`/`wiki_*`).

4. **inter-agent 워크플로 그래프가 supervisor에 명령형으로 하드코딩.** sentinel 4종(`__postwads_choice__`/`__mainoper_choice__`/`__mining_choice__`/yield→wads followup) + `_confirm_or_drop` + `_maybe_propose_*` + `_choose_*_or_drop` = **`supervisor.py:2894-3700`(~700 LOC)**.

5. **plan은 고정 크기.** replanner는 fill + fan-out(`_p{n}`)만 허용하고 task 추가는 거부(`supervisor.py:2073-2096`). 종료는 "pending 비면 끝"(`:1962`). 진짜 replan 아님(`:1960` TODO가 자인).

### 1.2 근본 이슈 (영향도순)

- **I. 입출력 비대칭** — 결과는 타입드 envelope, 입력은 공유 scalar god-state. → cross-agent 오염, 상속 hack, **병렬 fan-in 충돌의 직접 원인**.
- **II. 워크플로가 god-node에 명령형 하드코딩** — 새 체이닝 = supervisor에 sentinel 한 벌 추가(선형 비대화). AGENTS.md §6 정신과 충돌.
- **III. plan 고정** — 적응형 replan 불가.
- **IV. 워커 무자율** — *의도된 장점*(결정론/관측성). 유지.

---

## 2. 목표 아키텍처 (To-Be)

방향: **"중앙 god-state + 명령형 라우팅" → "task-scoped I/O + 선언적 체이닝 + agent=subgraph"**.

```
planner → validator → plan_review → supervisor ──Send[ ]──► [agent subgraph ×N] ──► replanner
                                         ▲  (task.params 동봉, 공유 scalar write 0)        │
                                         └──────────── envelope.followups 기반 일반 디스패치 ◄┘
```

설계 원칙:
- **입력은 task에 동봉**한다(`current_task["params"]` / Send payload). 공유 scalar 제거.
- **agent는 private state subgraph**, 부모엔 `ResultEnvelope`만 노출.
- **체이닝은 envelope의 `followups` 데이터로 선언**, replanner는 일반 로직 1벌로 소비.
- **병렬은 위 3개의 부산물**.
- **결정론·HITL 멱등성·관측성은 보존**.

---

## 3. 단계별 상세 설계

### S1 — task-scoped params (키스톤)

**목표:** 워커가 공유 scalar 대신 task에 동봉된 params를 읽는다. `_project_task_params`의 scalar smear 제거.

**변경:**
1. 워커 진입부를 `p = state["current_task"]["params"]` 기반으로 전환.
   - 예: `map_agent.py:944` `state.get("lot_ids")` → `p.get("lot_ids")`.
   - 9개 워커 각 read 지점(파악 완료: §1.1-1)을 일괄 치환. provenance용 `current_task_id/goal`은 그대로 유지.
2. agent별 **상속 규칙을 canonical_request 빌드 시점으로 이동**.
   - 현재 `_project_task_params`의 상속(yield: lotcd 상속+lot_ids clear; wads: 미상속; fail_history: 상속)을 `build_task_from_canonical_request`(또는 validator) 단계에서 **task.params에 확정**해 넣는다. 실행은 순수해진다.
3. supervisor는 scalar projection을 멈추고 `current_task.params`만 갱신해 dispatch.

**호환 전략(점진):** S1-a에서 `_project_task_params`를 유지한 채 워커가 `current_task.params` **우선, 없으면 scalar fallback**으로 읽게 한 뒤 — 순차 동작 불변 검증 — S1-b에서 scalar projection·fallback 제거.

**효과:** cross-agent 오염 제거 · 상속 규칙 가시화 · 병렬 fan-in 충돌(공유 write) 소멸의 전제.

**위험/규모:** 중. 워커 9곳 read + 상속 이전. 동작 불변(회귀 0)이 검증 기준.

---

### S2 — 선언적 체이닝 (`ResultEnvelope.followups`)

**목표:** sentinel 명령형 ~700 LOC을 데이터 선언 + 일반 디스패처로 교체.

**계약 추가** (`result_contracts.py`):
```python
class Followup(TypedDict):
    agent: str                 # 후속 대상 (또는 "__choice__": 사용자 택1)
    goal: str                  # 한국어 목표
    default_slots: dict        # 후속 task의 기본 슬롯 (상류 결과에서 채움)
    confirm: bool              # dispatch 전 사용자 확인 필요?
    choice_options: list | None  # 택1 HITL용 옵션 (없으면 단일 후속)
    guard_key: str             # 턴당 1회 가드 (기존 *_offered 대체)
```
워커는 결과 envelope에 자신이 제안할 후속을 **선언만** 한다. 예:
- wads: 검출 시 `followups=[{agent:"__choice__", choice_options:[map/fail_history/relation_tree], confirm:True, guard_key:"postwads"}]`
- yield: 이상감지 시 `followups=[{agent:"wads_agent", default_slots:{어제 창}, confirm:True, guard_key:"yield_wads"}]`
- relation_tree: main_oper 산출 시 `followups=[{agent:"wt_resp_agent", choice_options:main_opers, confirm:False, guard_key:"mainoper"}]`
- wt_resp: group 산출 시 `followups=[{agent:"mining_agent", confirm:True, guard_key:"mining"}]`

**replanner 일반화:** `_maybe_propose_*` 4종 → `_propose_followups(state)` 1개. 최신 envelope의 `followups`를 읽어 guard_key로 중복 차단 후 pending에 sentinel/confirm task 추가.

**supervisor 일반화:** `_choose_*_or_drop`/`_confirm_or_drop` 3종 → `_resolve_followup_or_drop` 1개. dispatch 직전(멱등 구간)에서 confirm/choice interrupt 처리 — **기존 super-step 분리·replay 안전 패턴 유지**.

**매핑 표 (마이그레이션 체크리스트):**

| 기존 sentinel | 함수 | → 신규 followup |
|---|---|---|
| yield→wads | `_maybe_propose_wads_followup` `:2920` | yield envelope.followups |
| `__postwads_choice__` | `_maybe_propose_postwads_choice` `:3267` / `_choose_postwads_or_drop` `:3342` | wads envelope.followups (choice) |
| `__mainoper_choice__` | `_maybe_propose_mainoper_choice` `:3503` / `_choose_mainoper_or_drop` `:3560` | relation_tree envelope.followups (choice) |
| `__mining_choice__` | `_maybe_propose_mining_choice` `:3612` / `_choose_mining_or_drop` `:3650` | wt_resp envelope.followups (confirm) |

**효과:** 새 체이닝 = "그 agent envelope에 followup 한 줄" → god-node 불변. ~700 LOC → ~150 LOC 예상.

**위험/규모:** 고. 2-step HITL(fail_type 선택 → 분석종류 선택, `:3358`)의 super-step 분리를 일반화에서 보존해야 함. **체이닝 4종 e2e 테스트 선행 필수**(AGENTS.md §5).

---

### S3 — agent = subgraph (private state)

**목표:** 각 워커를 자체 state 스키마 subgraph로 캡슐화, 부모엔 `ResultEnvelope`만 노출.

**변경:** 부모 `YieldQueryState`에서 agent-고유 입력 scalar(`map_oper`/`wads_*`/`dh_query`/`rt_groups`/`group_good`… 약 30키)를 제거 → 각 subgraph 내부 state로 이동. 부모는 `messages` + `past_steps` + `*_artifacts`(전부 reducer) + `recent_results` 인덱스 + `pending_tasks`/`current_task`만 유지(~60키 → ~15키).

**효과:** 입력/출력 누수 차단, 부모 state slim화, 테스트 용이.

**위험/규모:** 고. S1/S2 이후에만 안전(scalar 의존 제거가 전제).

---

### S4 — 병렬 fan-out (`Send`)

**목표:** 독립 multi-agent 요청을 1 super-step 병렬 실행.

**전제:** S1(공유 write 제거). `agent_suggestion`은 "last-non-empty-wins" 문자열 reducer로 전환(reader 1곳, advisory).

**변경(supervisor):** dispatch 직전 `_split_independent(pending, state)`로 **병렬 자격 batch** 분리:
- 자격: 서로 다른 agent · read-only(yield/wads/map/fail_history/lot_history) · chained-input 없음(`_needs_replan` `:1747`) · `confirm_tasks`/`ambiguous_slots`/sentinel 무관.
- batch ≥2 → `Command(goto=[Send(agent_i, payload_i), …])`. payload는 task.params 동봉, 공유 scalar write 0.
- batch <2 → 기존 단건 경로(회귀 0).

`Send`는 payload를 브랜치 입력 state로 전달 → **읽기 격리 자동**(워커 추가 수정 불필요, S1으로 이미 task.params 읽음). replanner는 fan-in 1회 수렴(표준).

**효과:** "A 수율 + B 맵" 류 latency 절감. **ROI는 독립 multi-task 빈도에 의존** → S4 착수 전 trace에서 빈도 집계 권장.

**위험/규모:** 저(S1 이후). 단 streaming 이벤트 interleave는 UI에서 수용 필요.

---

### S5 — 적응형 replan (선택)

종료판정을 plan 크기 고정(`:1962`)에서 **ID 기반**(`{t.task_id for t in task_plan} ⊆ {tid for tid,_ in past_steps}`)으로 전환 + envelope.followups로 plan 동적 확장. 우선순위 낮음.

---

## 4. State 스키마 마이그레이션 요약

| 분류 | 키(예) | As-Is | To-Be |
|---|---|---|---|
| 공유 입력 scalar | lotcd, fail_type, cause_oper, wads_category, lot_ids, groupkey | overwrite, god-state | **task.params 동봉**(S1) |
| agent 고유 입력 | map_oper, wf_mod, wads_*, dh_query, rt_groups, group_* | overwrite | **subgraph private**(S3) |
| 결과 누적 | *_artifacts, past_steps, messages | reducer | 유지 |
| advisory | agent_suggestion | overwrite(다중 writer) | **last-nonempty reducer**(S4) |
| 인덱스 | recent_results, canonical_request(s) | overwrite | 유지 |
| 워크플로 가드 | postwads_offered/mainoper_offered/mining_offered, confirm_tasks | overwrite | **followup.guard_key로 일반화**(S2) |

---

## 5. 테스트 계획 (회귀 게이트)

S1~S4 각 단계는 아래 시나리오 전부 **동작 불변**이어야 진행. `tests/test_e2e_regression.py` 확장.

1. 단일 조회: "4SS 수율" / "4SA 맵".
2. 독립 multi: "4SA 수율이랑 4SS 맵" (S4에서 병렬, 그 전엔 순차 — 결과 동일).
3. chained: "WADS 보고 그 lot 맵 그려" (순차 유지).
4. 체이닝 4종: yield→wads / postwads choice(2-step) / relation→wt_resp / wt_resp→mining confirm.
5. HITL: missing_param / plan_review(modify/cancel) / postwads 2-step / resume 새의도 드롭(`agent_server.py:510`).
6. follow-up 참조: "처음 거", "N번째 리포트 parameter" (recent_results K=10).

검증은 lint 아닌 **실제 graph 실행**(AGENTS.md §5).

---

## 6. 권장 시퀀스 & 비목표

**시퀀스:** S1 → S4 → S2 → S3 (→ S5 선택).
- S1이 전부의 전제이자 그 자체로 오염 제거 가치.
- S4는 S1의 저비용 부산물(ROI 사전 집계 권장).
- S2가 최대 유지보수 이득이나 회귀 위험 최고 → 테스트 선행.
- S3는 마지막 캡슐화.

**유지(깨지 말 것):** canonical_request 계약 · ResultEnvelope · HITL 멱등성/super-step 분리 · 결정론적 체이닝 의도 · 관측성(emit_trace).

**비목표(하지 않음):** swarm식 워커 자율 handoff 도입(결정론 손상). 워커를 ReAct로 일괄 전환. prebuilt `langgraph-supervisor` 전면 채택(현 canonical 계약이 더 적합) — 단 handoff 프리미티브는 S2 참고용으로만.
