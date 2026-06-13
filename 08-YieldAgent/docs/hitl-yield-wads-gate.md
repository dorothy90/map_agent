# HITL 체이닝 확인 — 노드 폭증 방지 설계

## 문제 (왜 지금 구조가 안 되는가)

방금 추가한 `yield_wads_gate`는 **체이닝 1개당 노드 1개** 패턴이다.
체이닝 후보는 yield→WADS 말고도 많다:

- WADS 검출 → map/lot 이력
- fail_history → relation_tree
- map → lot_history
- 그 외 agent_suggestion 기반 후속

각각 게이트 노드를 만들면 노드/엣지가 선형 증가하고(현재 agent→replanner 엣지만 7개),
- 그래프 와이어링·`recursion_limit`·max_steps 계산이 매번 흔들리고,
- "확인 후 실행"이라는 **동일한 동작**이 N벌 복붙되며,
- 도메인 지식("이상이면 WADS 권유")이 노드마다 하드코딩된다(LLM-first 위배).

→ **확인(confirm)은 "노드"가 아니라 "메커니즘"으로 한 번만 구현**해야 한다.

## 핵심 통찰

이미 **단일 HITL 초크포인트**가 존재한다: `supervisor_node`의 dispatch 직전 interrupt
(`_require_agent_params` → `_ask_fields`, missing_param). 모든 task는 dispatch 전에
이 지점을 반드시 통과한다. 여기에 "이 task를 실행할까요?" 확인을 얹으면 **체이닝이 몇 개든
노드는 0개 추가**된다.

그리고 "무엇을 후속으로 제안할지"는 이미 **replanner LLM**의 일이다(pending_tasks 확장·
chained-input 채우기·fan-out). 도메인 판단을 코드가 아니라 replanner 프롬프트로 옮기면 LLM-first다.

---

## 권장안 B — 확인을 "task 속성"으로 (신규 노드 0개)

### 메커니즘
1. **제안**: replanner(LLM)가 후속 task를 `pending_tasks`에 넣을 때, 사용자 확인이 필요하면
   상태에 `confirm_tasks: {task_id: confirm_message}` 를 함께 emit한다.
   (task dict 자체는 `build_task_from_canonical_request`가 task_id/agent/params/goal만 남기고
   비표준 키를 버리므로, scenario 2의 `ambiguous_slots`와 **동일하게 task_id로 묶은 별도 state 맵**으로
   나른다. canonical 정규화를 건드리지 않음.)
2. **확인**: `supervisor_node`가 task를 pop한 뒤, dispatch/param검증 **전에**
   `state["confirm_tasks"]`에 이 task_id가 있고 아직 미확인이면:
   `interrupt({type:"confirm", message, options:[{label:예},{label:아니오}], route:task.agent})`.
   - 승인 → 해당 task_id를 confirm_tasks에서 제거하고 기존 dispatch 흐름 계속.
   - 거절 → 그 task를 pending에서 드롭하고 다음 task(없으면 END).
3. interrupt/SSE/resume는 missing_param과 **완전히 동일 경로** 재사용(이미 검증됨).
   yes/no 해석은 `_interpret_confirmation` LLM 재사용.

### 그래프 변경
- **없음.** 노드/엣지 추가 0. `yield_wads_gate` 노드와 그 엣지 2개는 **삭제**하고
  `yield_agent → replanner` 원복.

### yield→WADS가 이 위에서 동작하는 법
- yield_agent는 그대로 결과+`agent_suggestion`만 emit(현재도 "WADS에서 열화 원인 확인?" 제안 텍스트 있음).
- replanner(LLM)가 past_steps의 이상 파라미터를 보고 wads_agent task(`fail_type`=이상 파라미터)를
  `pending_tasks`에 추가 + `confirm_tasks[task_id]="WADS 열화 검출 리포트를 확인하시겠습니까?"`.
- supervisor가 dispatch 전 확인 interrupt → 승인 시 WADS 실행. **하드코딩 `_build_wads_followup_task` 불필요.**

### 멱등성
- 확인 interrupt는 `supervisor_node` 안. supervisor는 retry_policy가 있지만 interrupt는 retry를 우회하고,
  missing_param도 이미 같은 노드에서 interrupt하므로 검증된 패턴. supervisor는 dispatch 전 단계라
  무거운 부수효과(DB/파일) 없음 → resume 재실행 안전. (이게 yield_agent 안에서 안 했던 이유와 동일 원칙.)

### 장점
- **노드 0개 추가, 체이닝 무한 확장 가능** — 확인이 필요한 어떤 task든 `confirm_tasks`에 넣기만 하면 됨.
- 도메인 판단이 replanner LLM으로 이동(LLM-first), 코드의 chain-specific 분기 제거.
- 단일 초크포인트라 추적/관측/프롬프트 일관.

### 비용/리스크
- replanner LLM이 후속을 **일관되게 제안**해야 함 → 프롬프트에 "확인이 필요한 후속은 confirm_tasks로" 명시 필요.
- 확인 시점이 "yield 직후"가 아니라 "다음 task dispatch 직전"으로 약간 늦음(UX상 무관).
- replanner 프롬프트 + supervisor pop 로직 수정 필요(중간 규모).

---

## 대안 A — 단일 제네릭 `followup_gate` 노드 1개

모든 agent → `followup_gate` → replanner. 게이트는 `state["pending_followup"]={message, task}`를 읽어
제네릭하게 interrupt. 신규 노드는 **딱 1개**(체이닝 수와 무관). B와 달리 노드가 1개 생기지만,
"확인 = 별도 노드"라는 멘탈모델이 더 명시적이고 supervisor pop 로직을 안 건드린다.

A vs B: **B 권장**(노드 0, 기존 HITL 자산 최대 재사용). 단 supervisor_node 수정이 부담스러우면 A로 타협.
둘 다 "체이닝당 노드 1개"는 제거한다.

---

## 현재 코드에서의 마이그레이션

1. `yield_wads_gate` 노드/엣지/헬퍼(`_build_wads_followup_task`, `_wads_already_planned`) 제거,
   `yield_agent → replanner` 원복. (`_interpret_confirmation`은 재사용하므로 유지.)
2. State에 `confirm_tasks: dict` 추가(overwrite, scenario 2 `ambiguous_slots`와 동형).
3. replanner: 이상감지 등 확인 필요한 후속을 pending에 넣을 때 `confirm_tasks` 채우도록 프롬프트+수집 추가.
4. `supervisor_node`의 task pop 직후(파라미터 검증 전)에 confirm 분기 삽입.
5. scenario 2(ambiguous)와 이 confirm은 **같은 supervisor interrupt 패턴**으로 수렴 → 코드 공유.

## 검증 (E2E 필수)
- yield 이상 → 다음 step에서 "WADS 확인?" interrupt → 승인 시 WADS dispatch / 거절 시 종료.
- 멱등성: resume가 yield_agent 재실행 안 함(supervisor pop은 무거운 작업 없음).
- 제2 체이닝(예: WADS→map)에 `confirm_tasks`만 채워도 **노드 추가 없이** 동일 확인 동작하는지.
- 기존 missing_param/ambiguous(scenario 2) 회귀 없음.

## 결론 (평가)

지적이 맞다 — 체이닝당 게이트 노드는 안티패턴이다. **권장은 B**: 확인을 task 속성으로 만들어
supervisor의 기존 단일 HITL 초크포인트가 처리하게 하면 노드 추가 없이 무한 확장되고,
도메인 판단도 replanner LLM으로 옮겨가 LLM-first에 부합한다. 방금 만든 `yield_wads_gate`는
이 메커니즘의 첫 사례를 검증한 셈이고, 이제 그 학습을 제네릭 메커니즘으로 흡수하면 된다.
