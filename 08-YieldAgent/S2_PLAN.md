# S2 실행 계획 — 선언적 체이닝 (ResultEnvelope.followups)

> 전제: S1(a+b) 완료·커밋됨 (branch `claude/s1-task-scoped-params`, commit 9536c79).
> 목표: 명령형 sentinel patchwork(~700 LOC, supervisor.py:2894–3700) → 선언적 followup.
> 위험: 고. **체인 4종 e2e 선행 게이트 필수.**

## 게이트 (착수 전 + 각 체인 마이그레이션 후)
서버 :8001 재기동 후 **격리(부하 없이)** 실행 — 부하 시 planner 플레이크함([[project_e2e_flaky_cases]]):
- `python tests/verify_failtype_inherit.py` (postwads fail_type/lotcd 상속)
- `python tests/verify_postwads_failtype.py` (postwads 2-step HITL)
- `python tests/verify_relation_chain.py` (relation→main_oper→wt_resp→mining 5-state)
- `python tests/verify_mining_artifact.py` (mining artifact/Q&A)
- `E2E_WORKERS=1 python tests/test_e2e_regression.py wads_map_chain reference sequential` (chaining 회귀)
- 안정 baseline: 메인 회귀 26/30 (실패는 yield periods/unit 하니스-stale 3종뿐)

## 현재 4개 체인 (As-Is, supervisor.py)
| 체인 | 제안(replanner) | 해소(supervisor dispatch) | 종류 |
|---|---|---|---|
| yield→wads | `_maybe_propose_wads_followup` :2924 | `_confirm_or_drop` :2954 | confirm |
| postwads | `_maybe_propose_postwads_choice` :3267 | `_choose_postwads_or_drop` :3342 (2-step) | choice |
| relation→main_oper | `_maybe_propose_mainoper_choice` :3503 | `_choose_mainoper_or_drop` :3560 | choice |
| wt_resp→mining | `_maybe_propose_mining_choice` :3612 | `_choose_mining_or_drop` :3650 | confirm |

공통 메커니즘: 제안=pending/task_plan append + `confirm_tasks[id]=msg` 또는 `__choice__` sentinel task 추가.
해소=dispatch 직전 멱등 구간에서 interrupt → 승인 시 dispatch / 거절 시 drop(remaining 재진입 or END).
choice는 sentinel task(`params.selected_idx`)로 운반, 재진입 시 `_latest_wads_reports`로 복원.

## 계약 (result_contracts.py 추가)
```python
class Followup(TypedDict, total=False):
    agent: str            # 후속 대상 (choice면 "__choice__")
    goal: str
    default_slots: dict   # 상류 결과/ctx에서 채울 기본 슬롯 (ctx 5종 포함: lotcd/fail_type/cause_oper/wads_category/ref_date)
    confirm: bool         # dispatch 전 확인?
    choice_options: list  # 택1 HITL 옵션 (없으면 None)
    guard_key: str        # 턴당 1회 가드 (기존 *_offered / 중복 제안 차단)
```
`ResultEnvelopeV1`에 `followups: list[Followup] = []` 필드 추가 (기존 envelope 하위호환: 기본 빈 list).

## 일반화 (2함수로 축약)
- replanner: `_propose_followups(state)` — 최신 result envelope의 `followups`를 읽어 guard_key로
  중복 차단 후 pending/task_plan에 추가, confirm은 `confirm_tasks` 등록. (4개 `_maybe_propose_*` 대체)
- supervisor: `_resolve_followup_or_drop(current_task, remaining, state, step_count)` —
  dispatch 직전 멱등 구간에서 confirm/choice interrupt 처리. **postwads 2-step super-step 분리 보존**
  (선택 인덱스만 운반 후 commit→재진입, replay-safe). (3개 choice/confirm 핸들러 대체)

## 마이그레이션 순서 (한 체인씩, 각 게이트 통과 후 다음)
1. **인프라**: Followup 계약 + envelope 필드 + `_propose_followups`/`_resolve_followup_or_drop`를
   기존 함수와 **병존**으로 추가 (아직 미사용). 회귀 불변 확인.
2. **yield→wads** (최단, confirm): yield envelope에 followup 선언 → 신규 경로로 처리,
   `_maybe_propose_wads_followup`/`_confirm_or_drop`의 wads 분기 제거. verify + 회귀.
3. **wt_resp→mining** (confirm): 동형. verify_relation_chain.
4. **relation→main_oper** (choice): verify_relation_chain.
5. **postwads** (choice, 2-step — 최난도, super-step 분리 보존): verify_postwads_failtype + verify_failtype_inherit.
6. 4종 모두 통과 후 잔여 sentinel 코드(`__postwads_choice__`/`__mainoper_choice__`/`__mining_choice__`
   치환 분기 supervisor.py:2618-2624) 제거. 최종 회귀.

## 보존 불변 (깨지 말 것)
- HITL 멱등성 / super-step 분리 (postwads 2-step) / replay 안전성
- canonical_request 계약 · ResultEnvelope 하위호환 · planner agency · 관측성(trace 이벤트)
- S1에서 남긴 ctx 5종: followup.default_slots로 명시 운반하면 god-state ctx 의존도 추가로 줄일 수 있음(S2 부수효과)
