# Deep Agent 도입 분석 — 08-YieldAgent

> 작성 목적: `deepagents` 프레임워크(또는 deep-agent 패턴)를 본 레포에 "도입"하는 것이
> 실익이 있는지, 있다면 어디까지인지 코드 기반으로 판단하기 위한 분석.
> **결론 먼저: 전면 전환은 비추천. 현재 시스템이 이미 deep agent보다 정교하다.**

---

## 1. Deep Agent란 (요약)

ReAct 스타일의 "얕은" 에이전트(도구 호출 → 관찰 → 반복)는 장기·복잡 작업에서 컨텍스트를
잃고 헤맨다. Deep Agent는 Claude Code / Deep Research / Manus 등에서 영감을 받은 패턴으로,
4가지 요소로 "깊이"를 확보한다.

| # | 요소 | 설명 |
|---|------|------|
| 1 | 상세 시스템 프롬프트 | 지시·예시·휴리스틱이 풍부한 긴 프롬프트 |
| 2 | 계획 도구 (Planning) | `write_todos` 류 — 주로 컨텍스트 유지용 (실행보다 집중 유도) |
| 3 | 서브 에이전트 | 컨텍스트 격리(isolation)를 위한 하위 작업 위임 |
| 4 | 가상 파일시스템 | 상태를 파일로 오프로드(`write_file`/`read_file`/`ls`/`edit_file`) |

`deepagents` (Python) 패키지는 `create_deep_agent()` 한 줄로 위 4요소를 갖춘 LangGraph
에이전트를 생성한다.

---

## 2. 현재 레포 진단 — "이미 수제(hand-rolled) deep agent"

`08-YieldAgent`는 LangGraph로 직접 구축한 **plan-and-execute 멀티 에이전트**다.

```
START → planner → task_normalizer_validator → plan_review(HITL)
      → supervisor ⟷ [yield / wads / map / fail_history / lot_history /
                       relation_tree / wt_resp / mining / ppt_export]
      → replanner → (END | supervisor)
```

Deep Agent 4요소와 1:1 대조:

| Deep Agent 요소 | 본 레포 현황 | 비고 |
|---|---|---|
| 상세 시스템 프롬프트 | ✅ 보유 | `prompts.py` (~36KB, 중앙화) |
| 계획 도구 | ✅ **오히려 더 강력** | `node_planner.py` + `node_replanner.py` — 실제 실행되는 정식 plan-and-execute (deepagents의 기본 planner는 no-op todo 리스트) |
| 서브 에이전트 | ✅ 보유 | 9개 워커 + `wads_agent`는 서브그래프. ContextVar로 도구 페이로드 격리 |
| 가상 파일시스템 | ⚠️ **없음** | 대신 `*_artifacts` state 누적 + checkpointer(Mongo/PG/Redis) |

### 핵심 사실
- **`deepagents>=0.3.1`는 `pyproject.toml`/`requirements.txt`에 선언만 돼 있고,
  실제 소스에서 단 한 줄도 import되지 않는다.** (`grep` 확인)
- 즉 본 과제는 *신규 도입*이 아니라 **"수제 시스템을 프레임워크로 대체할 가치가 있는가"** 다.

### 본 레포가 deepagents보다 더 가진 것 (전환 시 손실 위험 자산)
- 구조화 HITL `missing_param` 계약 — `fields[]` + `{slot: value}` 원자적 resolve
  (`AGENTS.md`의 HITL Contract 참조)
- `ResultEnvelopeV1` 에이전트 간 핸드오프 계약 (`result_contracts.py`) — semantic typing
- S2 declarative followup chaining (`S2_PLAN.md`) — 700+ LOC sentinel 패치워크를
  `followups` 선언으로 일반화하는 진행 중 리팩터
- ordinal reference 해석 (`#1`, `#R1` → 실제 row/report)
- `canonical_request` / `task_normalizer_validator` 결정론적 정규화 계약
- Oracle quirk 처리, Langfuse 트레이싱, transient 재시도(`RetryPolicy`)
- LLM 기반 resume-intent 판정 (`supervisor._resume_is_interrupt_answer`)

---

## 3. 장점 — `deepagents`로 전환 시

1. **보일러플레이트 감소**: `create_deep_agent()`로 planner/sub-agent/filesystem 기본 제공.
   현재 `supervisor.py` + `node_*.py` 오케스트레이션 코드 상당 부분이 대체될 여지.
2. **가상 파일시스템 무료 획득**: 현재 없는 부분. 긴 WADS/mining 리포트를 state에 이고
   다니는 대신 파일로 오프로드 → 컨텍스트 토큰 절약.
3. **서브에이전트 격리 표준화**: context isolation 패턴이 프레임워크 표준으로 정리.
4. **업스트림 동기화**: 커뮤니티 개선(`write_todos` 등)을 자동 흡수.

---

## 4. 단점 / 리스크 — 본 레포 기준

1. **이득이 작다 (핵심)**: deep agent가 푸는 문제(계획·서브에이전트·프롬프트)를 본 레포는
   **이미 더 정교하게** 풀었다. deepagents 기본 planner는 "no-op todo 리스트"인 반면,
   본 레포는 실제 실행되는 plan-and-execute + replanner다. **전환이 다운그레이드가 될 수 있다.**
2. **커스텀 자산 손실 위험**: §2의 고유 계약(HITL `fields`, ResultEnvelope, S2 followup,
   ordinal ref, Oracle quirk, Langfuse)을 프레임워크가 흡수하지 못한다. 직접 재이식 필요.
3. **마이그레이션 비용·검증 부담**: 50+ 파일, 9개 에이전트의 운영 시스템.
   `AGENTS.md §5`("실DB·실LLM end-to-end 검증 필수") 원칙상 전면 전환은 회귀 검증이 무겁다
   (현 기준선 `tests/test_e2e_regression.py` 26/30).
4. **AGENTS.md 원칙 충돌**: §2 Simplicity First / §3 Surgical Changes —
   잘 동작하는 시스템을 프레임워크에 맞추려 갈아엎는 것은 speculative refactor에 해당.

---

## 5. 권고

| 옵션 | 평가 | 비고 |
|------|------|------|
| **전면 전환** | ❌ 비추천 | 다운그레이드 + 고유 자산 손실 + 검증 부담 |
| **PoC (워커 1개)** | △ 조건부 | 학습용/비교용으로만. `mining_agent` 등 ReAct 워커 1개로 한정 |
| **가상 파일시스템만 선택 도입** | ✅ 최고 ROI | 현재 없는 유일한 요소. 전면 전환 없이 실익만 취함 |
| **분석 문서화 (본 문서)** | ✅ 즉시 가치 | 결정 근거 보존 |

### 결론
**전면 전환하지 말 것.** deep agent에서 본 레포에 실제로 빠진 단 하나 —
**가상 파일시스템(artifact 오프로드)** 만 선택적으로 도입하는 것이 ROI가 가장 높다.
나머지(계획·서브에이전트·프롬프트)는 이미 더 나은 형태로 존재한다.

### 가상 파일시스템 선택 도입 시 권장 접근 (전면 전환 없이)
- `*_artifacts` 누적 state를 파일 핸들/참조로 치환하되, 기존 ResultEnvelope 계약은 유지.
- 큰 payload(WADS/mining HTML, base64 이미지)만 파일로 오프로드, 메타데이터는 state 유지.
- deepagents 전체를 끌어오기보다, 필요한 파일시스템 미들웨어/툴만 부분 채택 검토.
- 도입 전후 `tests/test_e2e_regression.py` 기준선 비교로 회귀 가드.

---

*본 문서는 분석/의사결정 기록이며 코드는 변경하지 않았다. 다음 단계(PoC, 파일시스템 도입,
전면 전환 플랜)는 별도 승인 후 진행.*
