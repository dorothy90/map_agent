# 멀티에이전트 개선 계획 — #5 부분 실패 복구 / #4 에이전트 레지스트리

> 배경: MS *AI Agents for Beginners* 8강(다중 에이전트 설계 패턴)과 현재 YieldAgent
> 아키텍처를 대조한 gap 분석의 후속. 검증(critique/eval)은 엔지니어 실사용으로 커버 중이므로
> 구조 개선 두 건에 집중한다.
>
> 우선순위: **#5(부분 실패 복구) → #4(에이전트 레지스트리)**. #4는 골든 라우팅 회귀셋을
> 선행 조건으로 둔다(자동 회귀 안전망 없이 hot-path 리팩터 금지).

---

## 현황 요약 (코드 근거)

- 오케스트레이션: `planner → task_normalizer_validator → plan_review → supervisor →
  [worker] → replanner → (supervisor | END)` — 순차 plan-and-execute.
- 워커는 결과를 `AIMessage.additional_kwargs["result"]` envelope으로 싣고,
  `status ∈ {"success","empty","error","invalid"}`를 남긴다 (예: `map_agent.py:1036`).
- **replanner는 직전 task의 status를 `_emit_task_outcome_trace`로 트레이스에만 기록하고
  실행 분기에는 쓰지 않는다** (`node_replanner.py:31-51`). → 실패/0-row여도 plan은 다음
  chained task로 그대로 진행한다. **= #5가 해결할 공백.**
- 에이전트별 특수 처리(`_project_task_params`, `_missing_required_fields`,
  `_ambiguous_fields`)가 `node_supervisor.py`에 거대한 `if/elif` 체인으로 흩어져 있고,
  agent 목록은 `query_state.py`의 `Literal`, planner 프롬프트, `AGENT_SLOT_SCHEMAS`에도
  중복 선언돼 있다. **= #4가 해결할 결합도.**

---

# #5 부분 실패 복구 (Partial Failure Recovery) — 우선순위 1

## 목표
직전 task가 `error`/`empty`/`invalid`로 끝났고, 그 결과가 **남은 pending task의
chained-input 소스**일 때, 빈 입력으로 하류를 무음 실행하지 않는다. 대신 (a) 하류를
건너뛰고 (b) 사용자에게 원인을 설명하며 턴을 정상 종료한다.

**핵심 원칙**: 노드 추가 0개. 기존 `response` set → `should_end` conditional edge 패턴에
그대로 얹는다. 판단은 replanner 안에서 결정론적으로 수행(LLM 불필요).

## 현재 실패 시나리오 (재현)
1. `task_1`(wads)이 0-row → envelope `status="empty"`.
2. replanner가 status를 로깅만 하고 통과 → supervisor가 `task_2`(map) dispatch.
3. `_resolve_chained_params`가 wads 결과에서 채울 lot_ids가 없음 → map도 empty.
4. 사용자는 원인 설명 없이 빈 답을 받음 (**silent cascade**).

## 설계

### 5-1. 실패 판정 헬퍼 (`node_replanner.py`)
```
def _last_task_failed(state) -> tuple[str, dict] | None:
    """직전 task_id의 envelope status가 error/invalid/empty면 (task_id, envelope) 반환."""
    # past_steps[-1]의 task_id → _latest_result_envelope_for_task로 envelope 조회
    # status in {"error","invalid","empty"}이면 반환, 아니면 None
```
- `empty`를 실패로 볼지 여부는 **downstream 의존성이 있을 때만** 문제이므로, empty는
  단독으로 조기종료하지 않고 5-2의 "하류가 이 결과에 의존하는가" 조건과 AND로 묶는다.
  (empty지만 하류가 없으면 정상 종료 — 기존 동작 유지 = 회귀 방지.)

### 5-2. chained-input 의존성 판정
남은 pending 중 하나라도, 방금 실패한 task의 산출을 chained-input으로 필요로 하는가?
- 기존 `_needs_replan(pending)` 휴리스틱을 재사용: pending에 빈 chained-input(map의
  lot_ids/groupkey, lot_history의 lot_ids, fail_history의 fail_type 등)이 있고, 그 소스가
  방금 실패한 task라면 → 의존 있음.
- 보수적 시작: `_resolve_chained_params`를 시뮬레이션해도 여전히 빈 필수 입력이 남으면
  "하류가 상류 실패로 채울 수 없다"로 간주 (이미 replanner가 쓰는 시뮬레이션 로직 재사용,
  `node_replanner.py:292-300`).

### 5-3. 조기 종료 분기 (replanner 진입부, followup 제안보다 먼저)
```
failed = _last_task_failed(state)
if failed and _downstream_depends_on(failed, pending):
    reason = _explain_skip(failed, skipped_pending)   # 한국어 설명 생성
    return {**scratchpad_update,
            "pending_tasks": [],          # 하류 드롭
            "response": reason}           # should_end → END
```
- 배치 위치: `_propose_followups`(`node_replanner.py:243`) **이전**. 실패 시 후속 제안·재plan을
  건너뛰고 즉시 사용자 설명으로 종료.
- `reason` 문구는 실패 task의 goal + 하류 task의 goal을 엮어 결정론으로 조립(키워드
  하드코딩 아님, envelope 사실만 사용). 예: "「PT1H 열화 리포트 조회」결과가 비어 있어,
  이어질 「웨이퍼 맵 조회」를 건너뛰었습니다. 기간/제품코드를 바꿔 다시 시도해 주세요."

### 5-4. 독립 task는 계속 진행 (부분 성공 보존)
- 남은 pending이 실패 task에 **의존하지 않으면** 드롭하지 않고 진행. (예: "A제품 수율 +
  B제품 수율" 중 A만 실패 → B는 정상 실행.) → `_downstream_depends_on`이 task별로 판정.
- 즉 "전체 abort"가 아니라 "의존 하류만 skip". 이게 8강 fault-tolerance의 요지.

## 단계별 실행 + 검증
1. `_last_task_failed` + `_downstream_depends_on` 헬퍼 추가 → **verify**: 유닛 수준으로
   empty/error envelope + 의존/비의존 pending 조합에 대해 판정이 맞는지.
2. replanner 조기 종료 분기 결선 → **verify(e2e 필수, AGENTS.md §5)**: 실제 0-row 나오는
   wads 조건으로 "wads→map" 2-step 요청 → map이 dispatch되지 않고 설명 메시지로 종료되는지
   실 DB/실 LLM으로 재현.
3. 회귀 확인 → **verify**: 정상 케이스(상류 성공)에서 하류가 예전대로 실행되는지, 독립
   병렬-의도 task(A/B 수율)에서 한쪽 실패가 다른 쪽을 죽이지 않는지.

## 리스크 / 가드
- **회귀 위험**: empty를 과하게 실패로 보면 정상 "결과 없음" 응답까지 잘림 → 반드시
  "downstream 의존" AND 조건으로만 조기종료. 의존 없으면 기존 동작 유지.
- `max_steps`/`recursion_limit` 정합: 조기종료는 step을 늘리지 않으므로 영향 없음.
- checkpointer: `response` set 후 END는 기존 HITL-취소 경로와 동일하므로 resume 안전.

## 완료 기준 (DoD)
- [ ] 상류 실패+의존 하류 → 하류 skip + 한국어 원인 설명 + 정상 종료.
- [ ] 상류 실패+독립 하류 → 하류 정상 실행(부분 성공).
- [ ] 상류 성공 → 기존 동작 완전 동일(무회귀).
- [ ] 트레이스에 skip 사유 이벤트 1건 기록(관측성).

---

# #4 에이전트 능력 레지스트리 (Agent Registry) — 우선순위 2

## 전제 조건 (선행)
자동 회귀 안전망이 없으므로, **골든 라우팅 회귀셋을 먼저 만든다**(#2의 축소판). 이게
없으면 hot-path(projection/missing-field/planner) 리팩터의 무회귀를 보장할 수 없다.

### 4-0. 골든 라우팅 회귀셋 (선행 작업)
- 형식: `[{query, expect_agent, expect_slots_subset}]` 수십 건 — 대표 라우팅 + slot 추출
  케이스(단일/멀티/모호/chained/postwads followup).
- 실행: planner_node를 실 LLM로 태워 canonical_requests의 agent/slots가 기대와 일치하는지
  비교. `tests/test_e2e_regression.py`에 케이스 추가 또는 인접 파일 신설.
- 목적: 4-1~4-4 리팩터 전/후로 이 스위트가 **동일 통과**해야 머지.

## 목표
에이전트 추가 시 손대는 지점을 **5곳 → 1곳(레지스트리)**로 줄인다. 능력·필수슬롯·모호슬롯·
projection 매핑·planner 힌트를 에이전트별 선언 한 곳에 모으고, 로직이 그 선언을 읽게 한다.
AGENTS.md "하드코딩 금지"·"단일 출처" 원칙과 정합.

## 현재 분산된 지점 (통합 대상)
| 지점 | 파일 | 역할 |
|---|---|---|
| 노드/엣지 등록 | `supervisor.py:103-133` | 그래프 배선 |
| Literal(허용 agent) | `query_state.py:26-36`, `node_supervisor.py` return 타입 | 타입 |
| projection 매핑 | `node_supervisor.py:_project_task_params` | task.params → state 필드 |
| 필수/모호 슬롯 | `node_supervisor.py:_missing_required_fields`, `_ambiguous_fields` | HITL 스펙 |
| slot 스키마 | `canonical_request.py:AGENT_SLOT_SCHEMAS` | 허용 slot |
| planner 힌트 | `prompts.py:CANONICAL_PLANNER_SYSTEM_PROMPT` | LLM 라우팅 지식 |

## 설계

### 4-1. 선언적 AgentCard (신규 `agent_registry.py`)
에이전트별 단일 선언:
```
@dataclass(frozen=True)
class AgentCard:
    name: str                      # "map_agent"
    node: Callable                 # map_agent_node
    slot_schema: set[str]          # AGENT_SLOT_SCHEMAS[name] 대체
    required_fields: list[FieldSpec]   # _missing_required_fields 대체 (조건부 포함)
    projection: Callable[[dict, dict], dict]   # _project_task_params의 per-agent 블록
    planner_hint: str              # 프롬프트에 주입할 능력 설명(선택)

REGISTRY: dict[str, AgentCard] = { ... }   # 단일 출처
```
- `required_fields`의 조건부(map의 `lot_ids OR groupkey`, wads의 "lotcd 있을 때만 검증")는
  `FieldSpec`에 `required_any_group`/`validate_when` 필드로 표현 — 기존 로직을 데이터로 옮김.

### 4-2. 소비부를 레지스트리 읽기로 전환 (behavior-preserving)
- `_project_task_params(agent, ...)` → `REGISTRY[agent].projection(task_params, state)`.
  공통 필드(lot_ids/wf_ids/groupkey/fail_type/cause_oper) 계산은 공통 함수로 유지하고,
  per-agent 블록만 카드로 이동.
- `_missing_required_fields`/`_ambiguous_fields` → 카드의 `required_fields` + state의
  `ambiguous_slots`를 순회하는 **일반 루프**로 대체(에이전트별 `elif` 제거).
- `AGENT_SLOT_SCHEMAS` → `{name: card.slot_schema}`로 레지스트리에서 파생.
- `query_state.py`의 Literal / supervisor return 타입 → `REGISTRY.keys()` 기반으로 좁히되,
  Literal은 정적 타입이라 유지하고 **런타임 검증**을 레지스트리로 일원화(그래프 배선 루프에서
  `for card in REGISTRY.values(): workflow.add_node(...)`).
- planner 프롬프트 → 카드 `planner_hint`를 조립해 주입(에이전트 능력 목록을 코드에서 생성).

### 4-3. 그래프 배선 자동화 (`supervisor.py`)
```
for card in REGISTRY.values():
    workflow.add_node(card.name, card.node, retry_policy=_retry)
    workflow.add_edge(card.name, "replanner")
```
- `__choice__`/`ppt_export` 등 특수 노드는 레지스트리 밖 예외로 명시(주석).

## 단계별 실행 + 검증
0. **골든 회귀셋(4-0) 작성 & 통과 기준 확립.** ← 게이트.
1. `agent_registry.py` 신설 + 카드 채우기(기존 값 그대로 이관, 동작 변경 0). → **verify**:
   레지스트리에서 파생한 `AGENT_SLOT_SCHEMAS`/projection 결과가 기존과 **바이트 동일**인지
   스냅샷 비교.
2. 소비부 1개씩 전환(projection → missing-fields → slot_schema → planner hint → 배선).
   각 전환마다 → **verify**: 골든 회귀셋 통과 + 대표 e2e(단일/멀티/모호/chained) 재현.
3. 죽은 코드 제거(내가 만든 orphan만, AGENTS.md §3). → **verify**: 전체 회귀셋 재통과.

## 리스크 / 가드
- **behavior-preserving 필수**: 각 단계는 순수 리팩터. 값 변경이 보이면 즉시 중단.
- 조건부 required(map `lot_ids OR groupkey`, wads lotcd 조건검증, map_oper 기본값)의 미묘함이
  최대 함정 — `FieldSpec`으로 옮길 때 1:1 매핑을 스냅샷 테스트로 고정.
- 특수 에이전트(`__choice__` sentinel, `ppt_export`, fan-out 확장)는 레지스트리 일반 경로에
  넣지 말고 예외로 문서화.

## 완료 기준 (DoD)
- [ ] 신규 에이전트 추가 시 `agent_registry.py` 1곳 + 노드 함수만 작성하면 동작.
- [ ] 골든 라우팅 회귀셋 리팩터 전/후 동일 통과.
- [ ] projection/missing-field/slot-schema가 레지스트리 단일 출처에서 파생.
- [ ] `_project_task_params`/`_missing_required_fields`의 per-agent `elif` 체인 제거.

---

## 실행 순서 (권장)
1. **#5 전체** (작고 신뢰 직결, 안전망 없이도 e2e로 검증 가능).
2. **#4-0 골든 회귀셋** (그 자체로 #5 무회귀 확인에도 재사용).
3. **#4-1~4-3 레지스트리 리팩터** (회귀셋 게이트 하에 단계 전환).

## 범위 밖 (이번 계획 제외)
- #3 일반 병렬 실행: HITL "한 노드 1 interrupt = replay-safe" 불변식과 충돌 → 보류.
  단 HITL을 타지 않는 fan-out 경로(`_expand_map_tasks_by_wads_map_oper` 산출물) 한정
  병렬화는 별도 계획으로 후속 검토.
- #1/#2 검증·평가 하네스: 엔지니어 실사용으로 커버 중(단, #4-0 골든셋은 #2의 최소 축소판).
