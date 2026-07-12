# yield-agent 대대적 리팩토링 계획 — Model-Driven Multi-Agent Supervisor

> 상태: **계획 수립 중 (trace 착수 직전)** · 작성일 2026-06-30
> 목표: 유저의 **열린 멀티에이전트 골(탐색적 원인분석)** 을 더 잘 답하기. 대규모 변경 허용.
> 방향: plan-and-execute → **model-driven supervisor + 격리 sub-agent + virtual-FS** (deepagents-shaped 토폴로지)

---

## 0. 한 줄 결론

deepagents **패키지를 쓰자는 게 아니라**, 그 **토폴로지(supervisor + 격리 sub-agent + virtual FS)로 네 LangGraph를 재설계**한다.
네 가장 어려운 버그들이 plan-and-execute 경직성의 *증상*이고, 유저의 멀티에이전트 골이 바로 그 경직성에서 천장에 막힌다.
**단 두 가지는 타협 불가: (a) 골든 eval셋을 먼저 짠다, (b) 단순질의 결정론 fast-path를 남긴다.**

---

## 1. 핵심 통찰 — 힘들게 고친 버그들은 전부 "경직성의 증상"

| 힘들게 고친 것 | 진짜 원인 | model-driven supervisor에선 |
|---|---|---|
| god-state stale leak → 매-턴 리셋 | 워커가 부모 State 전체 공유 (`wads_agent.py:488,316`) | **격리 sub-agent라 애초에 안 생김** |
| 선언적 followups (`ResultEnvelope.followups`) | planner가 plan을 front-load → 못 본 후속을 사후 체이닝 | **orchestrator가 다음 agent 부르면 끝 — native** |
| canonical planner 슬롯 의미 누락 회귀 | 모든 슬롯을 dispatch 전에 미리 채워야 함 | **dispatch 순간에 채움 → 회귀 표면 축소** |

→ "열린 탐색적 분석"을 "front-loaded 결정론 플랜" 틀에 욱여넣느라 패치를 쌓아왔다.
이는 CLAUDE.md 원칙(*"실패 증상마다 코드 분기 쌓지 말고 계약을 고쳐라"*)을 **아키텍처 레벨에서** 위반한 것.
여기서 "계약을 고쳐라" = **오케스트레이션을 코드에서 모델로 옮겨라.**

---

## 2. 현 아키텍처 (확인됨, 증거 포함)

```
planner(canonicalizer LLM) → task_normalizer_validator → plan_review(HITL)
  → supervisor(Command goto dispatch) → [9개 워커 노드] → replanner → (END | supervisor 루프)
```

- **State** (`query_state.py` `YieldQueryState`): flat TypedDict, 도메인 슬롯(`lotcd`/`ref_date`/`wads_*`),
  `*_artifacts` = `operator.add` reducer 누적, `messages` = `add_messages`
- **Source of truth**: `messages` + `AIMessage.additional_kwargs["result"]` 의 `ResultEnvelope`
- **`recent_results`**: bounded, payload-free projection — 이미 원시적 scratchpad/index (= 원시 VFS)
- **followups** (`result_contracts.py:619,680`): `ResultEnvelope.followups` → `_propose_followups`가 pending/task_plan에 후속 추가
- **HITL** (`node_supervisor.py:447,542,700`): `interrupt()` + `confirm_tasks` pre-dispatch gate
- **워커 격리 = 0** (`wads_agent.py:488` 전체 state spread, `:316` 전체 messages 주입)
- 9 워커: yield_query / wads / map / fail_history / lot_history / relation_tree / mining / wt_resp / ppt_export

---

## 3. "유저의 멀티에이전트 골"이 실제로 뭐냐

수율 엔지니어의 최고가치 질의 = 단순조회가 아니라 **열린 원인분석**:

> "X랏 수율 왜 떨어졌어?" → map 봐야 알고 → 패턴 보고 fail_history → mining → relation_tree …
> **다음 스텝이 이전 결과에 의존. 경로를 미리 알 수 없다.**

- **현재(plan-and-execute):** planner가 경로를 미리 굳힘. replanner로 보정하지만 본질은 사전 계획 → emergent multi-step 추론에 **천장**.
- **목표(model-driven supervisor):** orchestrator LLM이 *실제 발견*을 보고 다음 agent 결정. depth 무제한, 경로 emergent → **멀티에이전트 골의 본질에 정합.**

---

## 4. 타깃 아키텍처

```
                       ┌─────────────────────────────────────┐
   user goal ──▶  Supervisor Deep Agent (loop)               │
                  │  tool: task(subagent, instructions)      │
                  │  tool: read_file / write_file (VFS)      │
                  │  interrupt() ← HITL when ambiguous       │
                  └───────────────┬─────────────────────────┘
        spawn (격리 컨텍스트) ▼        ▲ summary(작은 텍스트)
        ┌────────────────────────────────────┐
        │ yield / wads / map / fail_history / │  각자 own context,
        │ mining / relation / lot / wt_resp   │  own tools, typed I/O
        └────────────────────────────────────┘
              큰 산출물(map/대형테이블) → VFS에 기록, 컨텍스트엔 요약만
```

- **9개 agent → sub-agent 정의** (프롬프트+툴+typed input schema 유지).
  도메인 슬롯 의미(`lotcd(3자 제품코드)` 등)는 **sub-agent 입력 스키마로 이전** — LLM-first 원칙 유지.
- **virtual FS** = 큰 artifact 오프로딩. supervisor는 요약만 보고 판단 → **10개+ agent를 한 질의에서 호출해도 컨텍스트 안 터짐** (스케일 언락).

### 유지 / 변환 / 소멸

| | |
|---|---|
| ✅ **유지** | LangGraph, checkpointer, RetryPolicy, 각 agent 프롬프트·툴·SQL, `ResultEnvelope`를 sub-agent 반환 계약으로 |
| 🔁 **변환** | planner/replanner/supervisor 노드(~90KB) → supervisor loop + `task` 툴. `confirm_tasks` HITL → 루프 내 `interrupt()` |
| 💀 **소멸(좋은 소멸)** | followups 사후 체이닝, god-state 리셋, 슬롯 front-load 강제 — model-driven에선 불필요 |

---

## 5. 정직한 단 하나의 진짜 리스크 — 그리고 해소법

**비결정성.** model-driven 라우팅은 같은 단순질의가 매번 다른 agent 조합으로 갈 수 있음.
운영 도구에서 같은 질문이 다른 답 → 신뢰 붕괴. (현 E2E 25/30 baseline 흔들림)

**해소 2단:**
1. **Hybrid fast-path** — 모호하지 않은 단일-agent 질의(~80%)는 싸구려 분류기로 결정론 직결, deep supervisor 루프는 복잡/탐색 질의에만.
2. **골든 eval셋 먼저** — 리팩토링 *착수 전에* 현재 25/30 + 탐색질의 케이스를 골든셋으로 고정. 비결정성을 *두려워하지 말고 측정*. (CLAUDE.md E2E 검증 규칙의 정석)

---

## 6. 마이그레이션 — big-bang 금지, strangler

1. **eval 골든셋 구축** (현 시스템 baseline 고정) → verify: 회귀 측정 가능
2. supervisor deep agent + `task` 툴 + VFS **신규 그래프**를 1개 조합(예: map+fail_history)으로 PoC → verify: 탐색질의 1종이 현 시스템보다 나은 답
3. sub-agent 1개씩 이관, 두 그래프 병행 + 복잡도 라우팅 → verify: 골든셋 ≥ baseline 유지하며 탐색질의 점수↑
4. plan-and-execute 노드 제거, fast-path만 잔존

---

## 7. 다음 단계 — Deep-Dive Trace 3 lane (확정됨)

리팩토링 스펙이 반드시 풀어야 할 진짜 결정/리스크를 증거 기반으로 추적:

- **L1 오케스트레이션 토폴로지 (code-path):** plan-and-execute 경직성이 정확히 어디서 비용을 무는가 — planner/replanner/supervisor + followups/god-state/slot-frontload 패치들이 경직성 증상이라는 증거 **정량화**.
- **L2 State·컨텍스트 결합 (config-orchestration):** 공유 `YieldQueryState` 결합도, 워커 격리 시 깨지는 것, virtual-FS 이관 표면(`recent_results`/`*_artifacts` reducer/`ResultEnvelope`).
- **L3 결정론·eval (measurement-assumption):** 진짜 결정론 요구 — 어떤 질의는 반드시 결정론(fast-path)이고 어떤 질의가 emergent 라우팅 수혜인가. 현 25/30 baseline → 비결정 리팩토링을 gate할 골든셋 갭.

→ 3 lane 사용자 **확인 완료.** trace 착수 대기 중. (state: `.omc/state/deep-interview-state.json`)

---

## 8. 핵심 파일 레퍼런스

| 파일 | 역할 |
|---|---|
| `08-YieldAgent/supervisor.py` | 그래프 배선 (planner→…→replanner→loop/END) |
| `08-YieldAgent/node_planner.py` | canonicalizer LLM, 슬롯 front-load |
| `08-YieldAgent/node_replanner.py` | plan-and-execute 루프 보정 |
| `08-YieldAgent/node_supervisor.py` (49KB) | dispatch + `interrupt()` + `confirm_tasks` HITL |
| `08-YieldAgent/query_state.py` | `YieldQueryState` TypedDict |
| `08-YieldAgent/result_contracts.py` (38KB) | `ResultEnvelope` + `followups` 선언적 체이닝 |
| `08-YieldAgent/recent_results.py` | bounded payload-free scratchpad (= 원시 VFS) |
| `08-YieldAgent/agent_server.py` (48KB) | FastAPI, checkpointer compile, interrupt emit |
| 9 워커 | yield_query / wads / map / fail_history / lot_history / relation_tree / mining / wt_resp / ppt_export |
