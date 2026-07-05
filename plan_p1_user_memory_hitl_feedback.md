# 사용자 선호 장기 메모리 + HITL 피드백 학습 루프

## Context

LangChain `agents-from-scratch` 검토에서 차용 1순위로 확정된 패턴. yield-agent는 현재 MongoDB checkpointer(thread별 대화 상태)만 있고 **세션을 넘어 살아남는 사용자 선호 기억이 없다**. 사용자가 HITL 게이트에서 거절/선택/수정한 피드백은 매번 버려진다. 이 계획은 그 피드백을 user_id별 자유 텍스트 프로필로 학습해 다음 턴 planner가 참고하게 만든다.

**사용자 확정 결정**: ① LangGraph BaseStore 안 씀(langgraph-checkpoint-mongodb 0.3.1에 MongoDBStore 없음) — 전용 모듈 `user_memory.py` + 기존 Mongo(`yield_agent.user_profiles` 컬렉션), ② user_id별 프로필, ③ 학습 접점 3곳: task_confirm 거절 / postwads_choice 선택·미선택 / plan_review cancel·modify (missing_param은 쿼리별 값이라 제외).

**god-state 교훈 준수(핵심 원칙)**: 프로필에는 **정성적 선호만** 저장. 슬롯 값(lotcd, lot_ids, 날짜, fail_type 구체값)은 절대 영속하지 않는다 — 값 도출은 언제나 planner가 매 턴 수행. 위반 시 stale leak 재발.

**LLM-first 준수**: 피드백 해석·프로필 갱신은 LLM이 수행(코드는 분기 사실만 이벤트로 기록, 키워드 분류 없음). 갱신 프롬프트는 튜토리얼의 MEMORY_UPDATE_INSTRUCTIONS 번안("전체 덮어쓰기 금지, 표적 추가만" + worked example + chain_of_thought).

## 아키텍처 (검증된 코드 사실 기반)

- **수집**: 각 접점에서 LLM 호출 없이 경량 이벤트 dict를 `state["memory_feedback"]`(operator.add reducer)에 Command update로 append. `user_answer` 원문이 빈 값이면 스킵(드레인 `resume=""` 오염 차단, agent_server.py:510-525 참고).
- **flush**: 턴 종료 1회 — agent_server.py `generate()`의 `chat_turns.insert_one`(1019) 직후, `interrupt_emitted`(721) False일 때만. `asyncio.create_task(asyncio.to_thread(...))` fire-and-forget → SSE 지연 0. interrupt로 멈춘 턴의 이벤트는 reducer로 누적되어 END 도달 턴에서 일괄 flush(resume 턴은 state 리셋 없음).
- **저장**: pymongo(sync, requirements.txt에 이미 있음). planner_node가 sync 함수(node_planner.py:180)라 READ도 동기 필요 — motor 부적합.
- **주입**: node_planner.py meta_parts 블록(214-240)에 "사용자 분석 선호" 텍스트 추가 + `memory_profile_injected` trace 이벤트(e2e 단언 앵커). replanner 주입은 이번 slice 제외.
- **실패 격리**: user_memory.py 모든 public 함수가 내부 try/except로 예외를 삼키고 log + 기본값 반환(`serverSelectionTimeoutMS=2000`). user_id 없으면 전 경로 무동작 → 기존 e2e 스위트(user_id 미전송)는 구조적으로 무회귀.

## 변경 파일

### 신규 `08-YieldAgent/user_memory.py` (~120줄)

```python
class MemoryUpdateResult(BaseModel):
    chain_of_thought: str = ""
    updated_profile: str

def _collection():            # lazy 싱글턴 MongoClient (스레드 세이프)
def get_profile(user_id) -> str:                 # 부재/예외 → ""
def save_profile(user_id, profile) -> None:      # upsert, 4000자 failsafe truncate
def make_feedback_event(*, touchpoint, decision, message, user_answer,
                        agent="", goal="", options=None) -> dict   # 순수 빌더, I/O 없음
def update_profile_from_feedback(user_id, events) -> None:
    # get_profile → get_llm().invoke(MEMORY_UPDATE_SYSTEM + 이벤트 JSON)
    # → extract_json_from_llm(raw, MemoryUpdateResult)  ← OpenRouter 호환 수동 파싱 패턴(common.py:274)
    # → 변경 시 save_profile. 전체 try/except → warning 후 return
```

문서 스키마: `{"_id": user_id, "profile": "<한국어 불릿>", "updated_at": ..., "updates": n}`. 초기 프로필 없음(빈 문자열 시작 — default 프로필은 근거 없는 편향이라 배제).

**MEMORY_UPDATE_SYSTEM 프롬프트** (같은 파일에 정의, 골자):
- 역할: 수율 분석 어시스턴트의 사용자 선호 프로필 관리자
- 절대 규칙: ① 전체 덮어쓰기 금지·표적 추가/수정만·나머지 글자 그대로 보존 ② **정성적 선호만 — 구체 값(제품코드/lot/날짜/파라미터 값) 기록 금지** (나쁜 예: "4SS를 주로 본다" / 좋은 예: "후속 제안 중 맵 시각화를 고르는 경향") ③ 1회 관찰 vs 반복 성향 구별, 상충 시 최신 쪽으로 완화 ④ 한국어 불릿 최대 12항목·1200자 ⑤ 신호 약하면 그대로 반환
- 출력: `{"chain_of_thought": "...", "updated_profile": "..."}` JSON 하나 + worked example 1개

이벤트 스키마 (decision은 코드 분기 이름 그대로 — 의미 해석 없음):
```python
{"touchpoint": "task_confirm|postwads_choice|plan_review",
 "decision": "rejected|selected|declined|cancelled|modified",
 "message": "<interrupt로 제시한 질문/계획 요약>", "options": [...],
 "user_answer": "<사용자 응답 원문>", "agent": "...", "goal": "..."}
```

### `08-YieldAgent/query_state.py` (+2줄)
artifacts reducer들(126~193) 근처에 `memory_feedback: Annotated[list, operator.add]` 추가.

### `08-YieldAgent/agent_server.py` (~15줄)
1. fresh 턴 리셋 블록(537-546 `Overwrite([])` 패턴)에 `"memory_feedback": Overwrite([])` 추가, 첫 턴 init dict(626-672)에 `"memory_feedback": []` 추가.
2. `chat_turns.insert_one`(1019) 직후: `if not interrupt_emitted:` → `graph.aget_state(config)`에서 user_id(state 우선, fallback request.user_id)와 memory_feedback을 읽어 둘 다 있으면 `asyncio.create_task(asyncio.to_thread(update_profile_from_feedback, uid, events))`. 태스크는 모듈 레벨 set에 보관(GC 방지). 전체 try/except.

### `08-YieldAgent/node_supervisor.py` (~25줄)
- `_confirm_or_drop`(927-983): 거절 분기 2곳(955-962 supervisor 재진입, 975-983 END)의 Command update에 `"memory_feedback": [make_feedback_event(touchpoint="task_confirm", decision="rejected", message=<interrupt message>, user_answer=<answer 원문>, agent=<route>)]` merge. answer 빈 값이면 스킵.
- `_resolve_single_choice`(1064-1143): 선택 시(1097, 1131 update) `decision="selected"`(선택 label 포함), 미선택 시 `_drop_choice_sentinel(986)`에 `feedback_event: dict | None = None` 파라미터를 추가해 `decision="declined"` 이벤트를 Command update에 merge.
- answer 원문 추출 소헬퍼 1개(기존 `_interpret_*` 상단 로직 재사용).

### `08-YieldAgent/node_plan_review.py` (~10줄)
- cancel(141-151): update에 `decision="cancelled"` 이벤트(message=[(agent,goal)] 축약, user_answer=응답 원문).
- modify(158-163): `decision="modified"` (수정 지시 원문). approve는 기록 안 함.

### `08-YieldAgent/node_planner.py` (~10줄)
meta_parts 블록 끝(238-239 뒤, 240 `meta = join` 앞):
```python
_profile = get_profile(state.get("user_id") or "") if state.get("user_id") else ""
if _profile:
    meta_parts.append("사용자 분석 선호 (정성 참고 — 작업 구성·제안 여부 판단에만 쓰고, "
        "slots 구체 값은 절대 여기서 가져오지 말고 이번 요청과 Structured context에서만 도출):\n" + _profile)
    emit_trace_event("memory_profile_injected", source="planner", payload={"profile_chars": len(_profile)})
```
(`emit_trace_event`는 이미 import됨 — node_planner.py:19)

### 신규 `08-YieldAgent/tests/test_user_memory.py`
서버 불필요, 로컬 Mongo만(부재 시 skip).

## 구현 순서 + 검증 (매 단계 실제 e2e — lint만으로 통과 금지)

1. **user_memory.py + 단위 테스트**: save/get 라운드트립, 미존재 user → "", LLM 몽키패치(canned JSON)로 표적-갱신 시 기존 불릿 보존 확인, Mongo 예외 시 무전파, truncate. → `pytest tests/test_user_memory.py -v`
2. **state + agent_server 배선**: uvicorn :8001 **PID kill 후 재기동**(`--reload` 없음) → `Application startup complete` 확인 → 임의 질의 1턴 정상 + flush 미발동(이벤트 0) 확인.
3. **접점 3곳 배선**: user_id="e2e_mem_user"로 postwads_choice 케이스 실행 → "안 함, 앞으로 이런 후속 제안은 필요 없어요"로 resume → END 후 `db.user_profiles.findOne({_id:"e2e_mem_user"})`에 정성 불릿 존재 + **구체 값 부재** 확인. task_confirm 거절·plan_review cancel 각 1회 동일 요령.
4. **planner 주입**: 같은 user_id·**새 session_id**로 단순 질의 → traces/*.jsonl에 `memory_profile_injected` 존재(e2e_client로 단언), 프로필 없는 user는 부재. planner_output slots에 프로필발 값 유입 없는지 확인.
5. **회귀**: `pytest tests/test_e2e_regression.py -v`(baseline 26/30 유지, 플레이크는 격리 재실행으로 판별) + `python tests/golden_exploratory.py` 무회귀. stream_end elapsed 변경 전후 동급 확인.

## 알려진 한계 (수용)

- 게이트 드롭 드레인(agent_server.py:510-525)으로 버려지는 턴의 이벤트는 flush 없이 소멸 — 드문 경로, minimal slice 제외.
- 프로필 이력 보관 없음(updates 카운터 + chain_of_thought 로그로 디버깅) — v1 YAGNI.
- deepagents 재플랫폼 시에도 user_memory.py(모듈+프롬프트)와 이벤트 스키마는 그대로 이식 가능 — supervisor 배선부만 재작성.
