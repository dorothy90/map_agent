# 오케스트레이션 재설계 — Agentic Planner + Deterministic Workflow

> 상태: **Draft / 검토용** · 대상 브랜치: `claude/langgraph-multi-agent-review-04y8ul`
> 범위: `08-YieldAgent/supervisor.py`(3941 LOC) 중심 오케스트레이션
> 핵심 프레이밍 전환: **"멀티에이전트 오케스트레이션"이 아니라 "LLM planner를 가진 결정론 분석 workflow"**

---

## 0. 포지셔닝 — 이 시스템은 무엇인가 (정직하게)

LangGraph / Anthropic(*Building Effective Agents*)의 공식 구분:

- **Workflow** = LLM·도구가 **미리 정해진 코드 경로**로 오케스트레이션됨 → 예측·재현·감사 가능.
- **Agent** = LLM이 **자기 프로세스를 동적으로 스스로 지휘** → 자율·비결정.

LangGraph 철학은 "전부 자율 에이전트로 만들라"가 **아니다.** "필요한 만큼의 agency를 명시적 그래프로 통제하라"이며, 그래서 일부러 low-level이다.

**이 시스템의 정직한 정체:**
- 본질은 **Deterministic Workflow**다. 워커 9개는 대부분 LLM 0~2회의 결정론 SQL/시각화 함수다(yield/map은 LLM 0회).
- **진짜로 agentic한 부분은 planner 하나** — LLM이 사용자 의도를 canonical plan으로 변환·라우팅을 결정한다.
- 수율 분석 도메인에선 **재현성·감사가능성 > 자율성**이므로, "workflow 중심 + agentic planner front-end"가 **옳은 선택**이다. 워커를 자율 에이전트로 분장시키지 않는 것이 오히려 LangGraph 철학에 맞다.

**현재 코드가 "에이전트인 척"으로 느껴지는 두 원인:**
1. **명령형 sentinel 라우팅**(`supervisor.py:2950-3700`) — 워크플로 엣지를 코드로 손그림한 것. *너무 하드코딩이라 agent도 아니고, 너무 ad-hoc 명령형이라 깨끗한 workflow도 아닌* 어정쩡한 중간 상태. ← 이게 "덕지덕지"의 정체.
2. **이름 차용** — `supervisor`/`agent`/`replanner`는 `langgraph-supervisor`/`swarm`의 *자율 에이전트* 패턴 명칭인데 속은 고정 파이프라인. 이름·구현 불일치.

→ **본 재설계의 철학적 목표: workflow임을 정직하게 소유하고, 명령형 patchwork를 선언적 workflow로 바꾼다.** 그 핵심 단계는 S2다.

---

## 1. 현재 아키텍처 (As-Is)

```
START → planner → task_normalizer_validator → plan_review
                                                   │
                                                   ▼
           ┌────────────────────────────────► supervisor ──Command(goto)──► [9 worker]
           │                                       │  ▲                          │
    should_end(response?)                       END │  └──────── replanner ◄──────┘
```

| 요소 | 위치 | 정체 |
|---|---|---|
| planner | `supervisor.py:563` | **유일하게 agentic** — LLM canonicalizer, 라우팅 결정 |
| validator / plan_review | `:861` / `:998` | 결정론 정규화 · HITL 승인(task ≥2) |
| supervisor | `:2545` | `pending[0]` 단건 dispatch + sentinel 치환 + HITL |
| replanner | `:1897` | chained-input fill + 명령형 후속 제안 |
| worker ×9 | 각 `*_agent.py` | **결정론 함수** (yield/map LLM 0회) |

### 1.1 사실 (코드 근거)

1. 워커는 **공유 scalar state**에서 입력을 읽는다(`map_agent.py:944-955`, `wads_agent.py:383-387` 등). supervisor가 `_project_task_params`(`:2129`)로 **공유 scalar에 smear**하고 **agent별 상속 규칙을 하드코딩**(`:2142-2163`, `:2206-2222`).
2. 출력은 타입드 `ResultEnvelope`로 통일(전 9 agent + agent_server). 결과 경계는 이미 좋음.
3. reducer 채널: `messages`/`past_steps`/`*_artifacts`. overwrite 충돌 키: `agent_suggestion`(전 워커 write, reader 1곳 `:615`) 외 agent-고유.
4. **워크플로 그래프가 supervisor에 명령형으로 하드코딩** — sentinel 4종 + `_confirm_or_drop`/`_maybe_propose_*`/`_choose_*` = `:2894-3700`(~700 LOC).
5. plan 고정 크기 — replanner는 fill+fan-out만, 추가 거부(`:2073-2096`), 종료는 "pending 비면 끝"(`:1962`).

### 1.2 근본 이슈 (재프레이밍)

| # | 이슈 | 성격 | 해결 |
|---|---|---|---|
| **I** | 입출력 비대칭 — 입력이 공유 scalar god-state | **위생** (배관) | **S1** |
| **II** | 워크플로가 god-node에 명령형 하드코딩 = "에이전트인 척"의 정체 | **철학** | **S2 (핵심)** |
| III | plan 고정, 적응형 replan 불가 | 기능 | S5 |
| IV | 워커 무자율 | *의도된 장점* — 유지 | — |

---

## 2. 목표 아키텍처 (To-Be)

방향: **"god-state + 명령형 라우팅" → "task-scoped I/O + 선언적 workflow + agentic planner".**

```
[agentic]                        [deterministic workflow]
 planner → validator → plan_review → supervisor ──Send[ ]──► [worker subgraph ×N] ──► replanner
                                          ▲  (task.params 동봉, 공유 write 0)              │
                                          └──────── envelope.followups 기반 선언적 dispatch ◄┘
```

원칙:
- 입력은 **task에 동봉**(`current_task.params` / Send payload). 공유 scalar 제거.
- 체이닝은 **envelope의 `followups` 데이터로 선언**, dispatcher는 일반 로직 1벌.
- 병렬은 위 둘의 부산물.
- **planner만 agentic, 나머지는 정직한 결정론 workflow.** 워커를 자율화하지 않는다.
- 결정론·HITL 멱등성·관측성 보존.

---

## 3. 단계별 설계

### S1 — task-scoped params (위생 / 전제) — *철학 전환 아님*

> S1은 god-state 제거다. **그 자체로 "에이전트인 척"을 없애진 못한다.** 병렬·선언적체이닝의 전제일 뿐임을 명시.

**관찰:** params는 이미 dispatch 시 `current_task.params`에 들어있다(`supervisor.py:2690-2701`). 워커가 그걸 안 읽고 smear된 scalar를 읽을 뿐. **읽는 출처만 바꾸면 됨.**

**변경:**
1. 워커 진입부를 `p = state["current_task"]["params"]` 기반으로 (9개, 각 5~10줄).
2. agent별 상속을 **task 빌드 시점으로 이전**(아래 3.1).
3. supervisor의 `_project_task_params` 호출 제거(`:2676`), `current_task.params`만 채워 dispatch.

**점진 롤아웃:** S1-a(task.params 우선 + scalar fallback 유지, 동작 불변 검증) → S1-b(projection·fallback 제거).

#### 3.1 상속 보존 — 2경로 (질문 반영: "wads→map lotcd 이어받기")

이어받기는 **두 종류**이고 S1은 둘 다 보존한다:

- **(A) 결과 파생 체이닝** (wads→map: `lot_ids`/`groupkey`/`map_oper`): 이미 `_resolve_chained_params`(`:1547-1626`)가 **상류 결과 envelope을 읽어 `task.params`에 직접 기록**. scalar 경로 아님 → S1과 자연 정합(오히려 강화). *주의: map_agent_node는 `lotcd`를 안 씀 — `lot_ids`/`groupkey`로 조회. wads→map 캐리오버는 lotcd가 아니라 wafer 식별자.*
- **(B) 컨텍스트 상속** (wads→wt_resp→mining: `lotcd`/`fail_type`/`wads_category`): 현재 `state.lotcd` fallback(`:2206-2222`). S1에선 **빌드/replan 시 명시적 turn-context(`ctx`)에서 `task.params`에 baking**:
  ```python
  _LOTCD_INHERITS = {"yield_agent","fail_history_agent","wt_resp_agent","relation_tree_agent","mining_agent"}
  def build_task(cr, task_id, *, ctx):           # ctx={"lotcd","fail_type","wads_category"} (read-only)
      slots = dict(cr["slots"])
      if cr["agent"] in _LOTCD_INHERITS and not slots.get("lotcd"):
          slots["lotcd"] = ctx.get("lotcd", "")   # 상속이 데이터에 박힘 (가시·테스트 가능)
      return {"task_id": task_id, "agent": cr["agent"], "params": slots, ...}
  ```
  | | Before | After |
  |---|---|---|
  | source | mutable 공유 scalar (워커도 읽음, 매 dispatch 재smear) | 명시적 read-only `ctx`, 빌드 시 1회 해소 |
  | 워커가 보는 것 | `state.lotcd` (출처 불투명) | `current_task.params.lotcd` (자기 task 값) |
  | 병렬 안전 | ✗ | ✓ |

  타이밍: lotcd가 사용자 입력에서 알려진 경우(대부분) 빌드 시 해소. 상류 결과에서야 나오는 경우 replanner 재해소(지금 `_resolve_chained_params` 시점과 동일).

**효과:** cross-agent 오염 제거 · 상속 규칙 가시화 · 병렬 전제. **위험:** 중(워커 9 read + 상속 이전, 회귀 0이 기준).

---

### S2 — 선언적 체이닝 (`ResultEnvelope.followups`) — **★ 철학 정합의 핵심**

> **"에이전트인 척"을 실제로 없애는 단계.** 명령형 sentinel patchwork(~700 LOC) → 정직한 선언적 workflow. S1이 위생이라면 S2가 철학이다.

**문제:** 현재 inter-agent 워크플로(wads→{map/fail/relation}, relation→wt_resp, wt_resp→mining, yield→wads)가 supervisor에 명령형으로 손그림돼 있다. 새 엣지 1개 = god-node에 sentinel 한 벌 추가 → 선형 비대화. 이 어정쩡함이 "agent도 workflow도 아닌" 냄새의 근원.

**계약 추가** (`result_contracts.py`): 워커가 자신이 제안할 후속을 **데이터로 선언**한다.
```python
class Followup(TypedDict):
    agent: str                    # 후속 대상 (또는 "__choice__": 사용자 택1)
    goal: str
    default_slots: dict           # 상류 결과/ctx에서 채울 기본 슬롯
    confirm: bool                 # dispatch 전 사용자 확인 필요?
    choice_options: list | None   # 택1 HITL 옵션
    guard_key: str                # 턴당 1회 가드 (기존 *_offered 대체)
```

**일반화:**
- replanner: `_maybe_propose_*` 4종 → `_propose_followups(state)` 1개 (최신 envelope.followups 읽어 guard로 중복차단 후 pending 추가).
- supervisor: `_choose_*_or_drop`/`_confirm_or_drop` 3종 → `_resolve_followup_or_drop` 1개 (dispatch 직전 멱등 구간 confirm/choice interrupt, **super-step 분리·replay 안전 패턴 유지**).

**마이그레이션 표:**

| 기존 sentinel | 함수 | → 신규 followup 선언 위치 |
|---|---|---|
| yield→wads | `_maybe_propose_wads_followup` `:2920` | yield envelope.followups |
| `__postwads_choice__` (2-step) | `_maybe_propose_postwads_choice` `:3267` / `_choose_postwads_or_drop` `:3342` | wads envelope.followups (choice) |
| `__mainoper_choice__` | `:3503` / `:3560` | relation_tree envelope.followups (choice) |
| `__mining_choice__` | `:3612` / `:3650` | wt_resp envelope.followups (confirm) |

**효과:** 새 체이닝 = "그 agent envelope에 followup 한 줄" → god-node 불변. ~700 LOC → ~150 LOC. **명령형 patchwork가 선언적 workflow 엣지로 전환 = 철학 정합.**

**위험:** 고. postwads 2-step HITL(`:3358`)의 super-step 분리를 일반화에서 보존해야 함. **체이닝 4종 e2e 테스트 선행 필수**(AGENTS.md §5).

---

### S3 — worker = subgraph (private state)

각 워커를 자체 state 스키마 subgraph로 캡슐화, 부모엔 `ResultEnvelope`만 노출. 부모 `YieldQueryState`에서 agent-고유 입력 scalar(~30키)를 subgraph 내부로 이동 → 부모 ~60키 → ~15키. **위험:** 고(S1/S2 이후에만 안전).

### S4 — 병렬 fan-out (`Send`) — S1의 부산물

`agent_suggestion`을 "last-non-empty-wins" reducer로 전환. supervisor가 독립 batch(서로 다른 agent·read-only·chained/HITL 없음)를 `Send`로 1 super-step 병렬. **위험:** 저(S1 이후). **ROI는 독립 multi-task 빈도에 의존** → 착수 전 trace 집계 권장.

### S5 — 적응형 replan (선택)

종료판정을 plan-크기 고정 → ID 기반(`{task_id} ⊆ {past_steps id}`)으로, envelope.followups로 동적 확장. 우선순위 낮음.

---

## 4. State 스키마 마이그레이션

| 분류 | 키(예) | As-Is | To-Be |
|---|---|---|---|
| 공유 입력 scalar | lotcd, fail_type, cause_oper, wads_category, lot_ids, groupkey | overwrite god-state | **task.params 동봉**(S1) |
| agent 고유 입력 | map_oper, wf_mod, wads_*, dh_query, rt_groups, group_* | overwrite | **subgraph private**(S3) |
| 결과 누적 | *_artifacts, past_steps, messages | reducer | 유지 |
| advisory | agent_suggestion | overwrite(다중 writer) | **last-nonempty reducer**(S4) |
| 워크플로 가드 | *_offered, confirm_tasks | overwrite | **followup.guard_key 일반화**(S2) |
| 인덱스 | recent_results, canonical_request(s) | overwrite | 유지 |

---

## 5. 테스트 계획 (회귀 게이트)

각 단계는 아래 **동작 불변**이어야 진행. `tests/test_e2e_regression.py` 확장, 실제 graph 실행(AGENTS.md §5).
1. 단일: "4SS 수율" / "4SA 맵".
2. 독립 multi: "4SA 수율이랑 4SS 맵" (S4 병렬, 그 전엔 순차 — 결과 동일).
3. chained: "WADS 보고 그 lot 맵 그려".
4. 체이닝 4종: yield→wads / postwads(2-step) / relation→wt_resp / wt_resp→mining.
5. HITL: missing_param / plan_review(modify/cancel) / postwads 2-step / resume 새의도 드롭(`agent_server.py:510`).
6. follow-up 참조: "처음 거" / "N번째 리포트 parameter" (recent_results K=10).

---

## 6. 시퀀스 · 유지 · 비목표

**시퀀스:** S1 → S4 → **S2(철학 핵심)** → S3 (→ S5 선택).
- S1: 위생·전제(오염 제거 가치).
- S4: S1의 저비용 부산물(ROI 사전 집계).
- **S2: "에이전트인 척" 제거 = 본 재설계의 철학적 목표. 회귀 위험 최고 → 테스트 선행.**
- S3: 캡슐화 마무리.

**유지(깨지 말 것):** canonical_request 계약 · ResultEnvelope · HITL 멱등성/super-step 분리 · planner의 agency · 관측성.

**비목표(명시적으로 안 함):**
- 워커를 자율 에이전트로 전환 ❌ — 결정론·감사성 손상, 도메인 부적합(AGENTS.md §2).
- swarm식 워커 자율 handoff 도입 ❌.
- prebuilt `langgraph-supervisor` 전면 채택 ❌ — 현 canonical 계약이 더 적합(handoff 프리미티브는 S2 참고만).
- **"멀티에이전트"라는 과장된 프레이밍 유지 ❌** — 이 시스템은 *agentic planner + deterministic workflow*다. 정직한 명명이 철학 정합의 일부다(향후 `supervisor`→`dispatcher`, `*_agent`→`*_step` 같은 명칭 정리는 선택적 후속).
