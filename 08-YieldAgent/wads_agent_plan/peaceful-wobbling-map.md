# WADS Agent: ReAct → ReAct + text2sql 전환 종합 리뷰

## Context

**현재 상태**: `wads_agent_node`가 supervisor에서 추출한 `lotcd/wads_start_tm/wads_end_tm`만으로 쿼리를 재조립하여, 사용자의 세부 조건(step 필터, 복합 조건)이 손실됨.

**목표**: ReAct 구조(`create_react_agent`)를 유지하면서 `wads_query_sql` @tool을 추가하여 복잡한 SQL 쿼리를 지원.

**리뷰 대상 문서**:
- 원본 계획: `replicated-honking-meteor.md` (3개 변경사항)
- 기존 리뷰: `joyful-foraging-frog.md` (13개 항목)

**핵심 파일**:
- `wads_tools.py` (208줄) — 2개 @tool + `_query_wads_data()` + ContextVar
- `wads_agent.py` (334줄) — ReAct graph + `wads_agent_node` + HTML 렌더러
- `prompts.py` (319줄) — `_SAFETY_RULES`, `WADS_SYSTEM_PROMPT_TEMPLATE`
- `supervisor.py` (~22KB) — `YieldQueryState`, graph 정의
- `common.py` — `get_oracle_connection()` pool 패턴

---

## 1. 추가 (Add) — 기존 계획/리뷰에 없는 새 항목

### A-1. [HIGH] `wads_query_sql` @tool docstring 품질 — 도구 선택 정확도 결정

**파일**: `wads_tools.py`

**문제**: 계획의 docstring이 "조회 내용을 자연어로 설명"으로만 되어 있음. `create_react_agent`는 docstring을 그대로 LLM에 전달하여 도구 선택에 사용. 기존 2개 도구(`wads_query_data`:106-111줄, `wads_get_html_report`:159-164줄)는 구체적 Args 블록과 예시가 있어 잘 동작함.

**필요 조치**:
```python
@tool
def wads_query_sql(query_description: str) -> str:
    """WADS 데이터에 대한 복잡한 SQL 쿼리를 생성하고 실행합니다.
    wads_query_data/wads_get_html_report로 처리할 수 없는 복잡한 조건에만 사용하세요.
    이 도구는 내부 LLM 호출이 추가되어 다른 도구보다 느립니다.

    Args:
        query_description: 조회 내용을 자연어로 설명
            (예: "4SS 로트의 3월 step01, step02 건수를 step별 집계")
            (예: "step03 제외한 전체 스텝 목록")
            (예: "로트별 HIGH severity 건수 비교")

    Returns:
        조회 결과 요약 (건수 + 컬럼 정보). 실제 데이터는 화면에 별도 표시됩니다.
    """
```

### A-2. [HIGH] `recursion_limit` 고갈 위험

**파일**: `wads_agent.py:252`

**문제**: 현재 `recursion_limit: 10`. ReAct 1회 반복 = 2 step (LLM + tool). `wads_query_sql` 실패 → 재시도 → `wads_query_data` 폴백 = 최소 6 step. `_RETRY_RULES`("최대 3회 재시도")까지 합치면 `GraphRecursionError` 발생 가능.

**필요 조치**: `recursion_limit`을 15~20으로 상향하고, WADS 프롬프트에 "wads_query_sql 실패 시 1회만 재시도, 그래도 실패하면 wads_query_data로 전환" 가이드 추가.

### A-3. [HIGH] Tool 반환 문자열 길이 — ContextVar 저장 패턴 필수

**파일**: `wads_tools.py`

**문제**: `FETCH FIRST 500 ROWS ONLY`로 최대 500행 반환 가능. 이 결과를 문자열로 ReAct LLM에 반환하면 context window 오염. 기존 도구는 이미 올바른 패턴 사용 중:
- `wads_query_data` (144줄): "총 N건의 데이터가 조회되었습니다" 요약만 반환, 실제 데이터는 ContextVar 저장
- `wads_get_html_report` (203줄): 같은 패턴

**필요 조치**: `wads_query_sql`도 동일 패턴 — LLM에는 "SQL 쿼리 실행 완료: 총 47건. 컬럼: CTN_DESC, COUNT(*)" 요약만 반환. 전체 결과는 `storage["sql_result"]`에 저장.

### A-4. [MEDIUM] `_wads_prompt`에 supervisor 파싱 날짜 주입 누락

**파일**: `wads_agent.py:32-38`

**문제**: `_wads_prompt`는 `current_date`만 시스템 프롬프트에 주입. supervisor가 파싱한 `wads_start_tm/wads_end_tm`는 전달되지 않음. 기존 리뷰 I-1은 쿼리 문자열에 `[조회기간: ...]` 프리픽스를 제안했으나, 이는 `wads_query_sql`의 `query_description` 전달 시 혼란 유발.

**더 나은 접근**: `_wads_prompt`가 `state` dict를 받으므로, 시스템 프롬프트에 날짜 컨텍스트 섹션 주입:
```python
def _wads_prompt(state: dict) -> list:
    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)
    # supervisor 파싱 날짜가 있으면 컨텍스트 추가
    lotcd = state.get("_lotcd", "")
    start_tm = state.get("_start_tm", "")
    end_tm = state.get("_end_tm", "")
    if lotcd or end_tm:
        ctx = f"\n\n[조회 컨텍스트] lotcd={lotcd}"
        if start_tm:
            ctx += f", 기간: {start_tm} ~ {end_tm}"
        elif end_tm:
            ctx += f", 날짜: {end_tm}"
        system_prompt += ctx
    return [SystemMessage(content=system_prompt)] + list(state.get("messages", []))
```

**주의**: `create_react_agent`의 내부 state schema는 `messages`만 포함. 추가 키는 prompt callable에서 읽을 수 있지만, ReAct 내부 state에는 저장 안 됨. closure 또는 별도 메커니즘 필요.

### A-5. [MEDIUM] Langfuse `@observe` 데코레이터 누락

**파일**: `wads_tools.py`

**문제**: 기존 `_query_wads_data`에는 `@observe(name="wads_query_oracle")`가 있음 (43줄). `wads_query_sql`은 중첩 LLM 호출(SQL 생성)이 포함되므로 tracing이 더 중요.

**필요 조치**: `@observe(name="wads_query_sql")` + 내부 LLM 호출에 `config={"callbacks": _lf_callbacks()}`.

### A-6. [MEDIUM] `wads_agent` 함수(162줄) 공유 그래프 부작용

**파일**: `wads_agent.py:162-225`

**문제**: `wads_agent`는 `_wads_graph`를 공유하는 별도 진입점. `wads_query_sql` 추가 시 이 함수도 자동으로 SQL 도구를 갖게 됨. 하지만 이 함수는 `state["question"]`을 직접 사용하여 supervisor의 날짜 파싱 없이 동작 → SQL 생성 정확도 하락.

**필요 조치**: `wads_agent`가 레거시/테스트 전용인지 확인. 프로덕션에서 사용되면 동일한 날짜 컨텍스트 주입 로직 적용 필요.

### A-7. [LOW] `create_react_agent` 내부 에러 핸들링 확인

**파일**: `wads_agent.py:41-44`

**문제**: LangGraph의 `create_react_agent`는 내부적으로 `ToolNode`를 생성. `handle_tool_errors` 기본값이 `True`인지 확인 필요. `wads_query_sql`은 LLM 호출 실패, SQL 검증 실패, Oracle 실행 실패 등 다양한 실패 모드 보유.

**필요 조치**: `wads_query_sql` 내부에서 try/except로 에러 문자열 반환 (raise하지 않음). LangGraph best practice: 에러를 ToolMessage로 반환하여 ReAct LLM이 추론할 수 있게 함.

### A-8. [LOW] SQL 생성 모델 분리 설정

**파일**: `wads_tools.py`

**문제**: 계획은 `get_llm(model=RETRIEVE_CHAIN_MODEL)` 사용. SQL 생성에 더 강력한 모델이 필요할 수 있음.

**필요 조치**: `WADS_SQL_GEN_MODEL` 환경변수로 분리, `RETRIEVE_CHAIN_MODEL`을 기본값으로.

---

## 2. 개선 (Improve) — 기존 계획/리뷰 항목의 수정

### I-1. [HIGH] 기존 리뷰 I-1 (날짜 정밀도 손실) — 쿼리 프리픽스 대신 시스템 프롬프트 주입

**파일**: `wads_agent.py`

**기존 제안**: `[조회기간: {start_tm} ~ {end_tm}] {query}` 형태로 HumanMessage 프리픽스.

**문제점**: 프리픽스 문자열이 ReAct LLM의 `query_description` → SQL 생성 LLM으로 전달될 때 혼란 유발. 사용자 쿼리에 이미 "3월 2일" 같은 날짜가 있으면 충돌.

**개선안**: 위 A-4의 시스템 프롬프트 주입 방식 사용. HumanMessage는 rewrite된 원본 그대로 유지.

### I-2. [HIGH] 기존 리뷰 I-2 (SQL 결과 렌더링) — 동적 컬럼 렌더러 구체화

**파일**: `wads_agent.py:65-158`

**렌더링 우선순위 결정 트리**:
1. `storage["reports"]` → `_render_wads_report_html` (HTML 리포트)
2. `storage["sql_result"]` → `_render_wads_sql_html` (**신규**)
3. `storage["query"]` → `_render_wads_query_html` (고정 3컬럼)

**`_render_wads_sql_html` 요구사항**:
- dict 리스트에서 동적 `<th>` 헤더 생성 (dict keys 기반)
- 숫자 포맷팅 (COUNT 결과 vs 문자열)
- 동일 CSS 네임스페이스 (`wads-card`, `wads-table`) 유지
- 에러/빈 결과 상태 동일 처리

### I-3. [HIGH] 기존 리뷰 I-4 (SQL Injection 검증) — sqlparse 의존성 없는 다층 검증

**파일**: `wads_tools.py`

**`sqlparse` 문제**: 새 의존성 추가 필요 + Oracle 방언 지원 제한.

**대안 — 4계층 검증**:

| 계층 | 방법 | 구현 |
|------|------|------|
| 1 | 주석 제거 | `re.sub(r'--.*$', '', sql, flags=re.M)` + `re.sub(r'/\*.*?\*/', '', sql, flags=re.S)` |
| 2 | SELECT 확인 | `sql.strip().upper().startswith("SELECT")` |
| 3 | 테이블 허용목록 | `FROM\s+(\w+)`, `JOIN\s+(\w+)` 추출 → `_ORACLE_TABLE`만 허용 |
| 4 | 쿼리 타임아웃 | `cursor.callTimeout = 10000` (10초) |

+ 금지 키워드: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, MERGE, CREATE, GRANT, REVOKE (주석 제거 후 검사)

### I-4. [MEDIUM] 기존 리뷰 I-3 (`_SAFETY_RULES` 공유) — 더 단순한 해결

**파일**: `prompts.py:27-33`

**개선안**: `_SAFETY_RULES`를 수정하지 않음. 현재 문구 "사전 정의된 쿼리 함수만 사용"은 supervisor 관점에서 여전히 유효 (supervisor는 SQL을 직접 실행하지 않음). SQL 안전 규칙은 `wads_query_sql` 도구 내부의 SQL 생성 프롬프트에만 캡슐화.

---

## 3. 보완 (Supplement) — 기존 항목의 상세화

### S-1. [HIGH] 기존 리뷰 S-1 (ContextVar 키 충돌) — 정확한 실패 시나리오

**파일**: `wads_tools.py`

**시나리오**:
1. 사용자: "4SS step01 건수 알려줘"
2. ReAct → `wads_query_data(lotcd="4SS", parameter="step01")` → `storage["query"]` = `[{lotcd, end_tm, parameter}, ...]`
3. ReAct → `wads_query_sql(query_description="4SS step01 건수 집계")` → `storage["query"]` 덮어씀 (계획 원안대로면)
4. `_render_wads_query_html` → `{lotcd, end_tm, parameter}` 키 기대하지만 SQL 결과는 다른 구조 → **빈 셀 렌더링**

**해결**: `wads_query_sql`은 반드시 `storage["sql_result"]`에 저장. `wads_agent_node`에서 `sql_result` > `reports` > `query` 우선순위.

### S-2. [MEDIUM] 기존 리뷰 S-2 (중첩 LLM 지연) — 구체적 지연 예산

**현재 경로**: rewrite(1) + supervisor(1) + ReAct(2-3) = **4-5 LLM 호출**
**SQL 경로**: + SQL 생성(1) = **5-6 LLM 호출** (+1~3초 추가)

**최적화 대안 검토**:
- ReAct LLM이 직접 SQL 생성 → 1회 절약하지만 Oracle 방언 지식 필요
- **권장**: 중첩 LLM 유지 (정확도 우선), 지연 +2초 허용 문서화

### S-3. [MEDIUM] 기존 리뷰 A-1 (대화 히스토리) — 선택적 필터링

**파일**: `wads_agent.py:254`

**현재**: `{"messages": [HumanMessage(content=query)]}` — 단일 메시지만 전달
**문제**: follow-up 시 맥락 소실

**정확한 필터링 규칙**:
- 포함: `HumanMessage` (사용자 쿼리), `AIMessage(name="wads_agent")` (이전 WADS 응답)
- 제외: supervisor 메시지, `yield_agent` 메시지, `map_agent` 메시지, `ToolMessage`
- 최근 2-3턴으로 제한

### S-4. [MEDIUM] 기존 리뷰 A-2 (SQL 에러 핸들링) — 에러 분류표

| 에러 | 감지 | ReAct LLM에 반환할 메시지 |
|------|------|--------------------------|
| SQL 검증 실패 | `_validate_sql()` | "SQL 검증 실패: {reason}. wads_query_data를 사용하세요." |
| Oracle 문법 오류 (ORA-00933) | `oracledb.DatabaseError` | "SQL 문법 오류: {err}. query_description을 수정하여 재시도하세요." |
| 컬럼/테이블 없음 (ORA-00904/942) | `oracledb.DatabaseError` | "컬럼명 오류. 사용 가능: LOTCD, END_TM, CTN_DESC, HTML" |
| 타임아웃 | `callTimeout` | "쿼리 시간 초과. 조건을 좁혀 재시도하세요." |
| 결과 0건 | `len(rows) == 0` | "조건에 맞는 데이터가 없습니다." |
| LLM SQL 생성 실패 | `Exception` | "SQL 생성 실패. wads_query_data를 사용하세요." |

---

## 4. 삭제 (Delete) — 제거/회피해야 할 항목

### D-1. [HIGH] `_SAFETY_RULES` 상수 수정 금지

**파일**: `prompts.py:27-33`

원본 계획(변경3)이 `_SAFETY_RULES`를 "wads_query_sql 도구를 통한 SELECT 쿼리만 허용"으로 수정 제안. 이는 `SUPERVISOR_SYSTEM_PROMPT`에도 영향 → supervisor LLM 혼란. **수정하지 말 것**.

### D-2. [HIGH] SQL 결과를 `storage["query"]`에 저장 금지

**파일**: `wads_tools.py`

원본 계획(변경2, 4단계)의 "HTML 컬럼 없는 결과 → `storage['query']`"는 `_render_wads_query_html`의 하드코딩된 3컬럼(`lotcd`, `end_tm`, `parameter`)과 충돌. **반드시 `storage["sql_result"]` 별도 키 사용**.

### D-3. [MEDIUM] `WadsSqlResponse` Pydantic 모델 / `with_structured_output()` 사용 금지

OpenRouter 호환성 문제로 이미 폐기된 접근. 구현에 잔재가 남지 않도록 확인.

### D-4. [MEDIUM] SQL 생성 프롬프트를 `prompts.py`에 넣지 말 것

SQL 생성 프롬프트는 `wads_query_sql` 도구의 구현 세부사항. `wads_tools.py` 내부 상수로 캡슐화.

### D-5. [LOW] `wads_get_html_report`의 미사용 `limit` 파라미터 제거

**파일**: `wads_tools.py:154`

"하위호환을 위해 받지만, 현재는 무시됩니다" — 데드 코드. ReAct LLM이 불필요하게 이 파라미터를 설정하려 시도할 수 있음. text2sql 전환 시 정리 기회.

---

## 우선순위 종합

| 우선순위 | ID | 분류 | 핵심 내용 | 파일 |
|---------|-----|------|----------|------|
| HIGH | A-1 | 추가 | @tool docstring 품질 → 도구 선택 정확도 | wads_tools.py |
| HIGH | A-2 | 추가 | recursion_limit 고갈 → 15-20으로 상향 | wads_agent.py |
| HIGH | A-3 | 추가 | Tool 반환 길이 → ContextVar 저장 패턴 | wads_tools.py |
| HIGH | I-1 | 개선 | 날짜 컨텍스트 → 시스템 프롬프트 주입 | wads_agent.py |
| HIGH | I-2 | 개선 | SQL 결과 렌더링 → 동적 컬럼 렌더러 | wads_agent.py |
| HIGH | I-3 | 개선 | SQL 검증 → sqlparse 없이 4계층 검증 | wads_tools.py |
| HIGH | S-1 | 보완 | ContextVar 키 충돌 → `sql_result` 별도 키 | wads_tools.py |
| HIGH | D-1 | 삭제 | `_SAFETY_RULES` 수정 금지 | prompts.py |
| HIGH | D-2 | 삭제 | `storage["query"]`에 SQL 결과 저장 금지 | wads_tools.py |
| MEDIUM | A-4 | 추가 | supervisor 날짜 → `_wads_prompt` 주입 | wads_agent.py |
| MEDIUM | A-5 | 추가 | Langfuse @observe 누락 | wads_tools.py |
| MEDIUM | A-6 | 추가 | `wads_agent` 함수 공유 그래프 부작용 | wads_agent.py |
| MEDIUM | I-4 | 개선 | `_SAFETY_RULES` → 수정 않고 도구 내 캡슐화 | prompts.py |
| MEDIUM | S-2 | 보완 | 중첩 LLM 지연 +2초 예산 명시 | 문서 |
| MEDIUM | S-3 | 보완 | 대화 히스토리 → agent name 필터링 | wads_agent.py |
| MEDIUM | S-4 | 보완 | SQL 에러 → 분류별 복구 가이드 | wads_tools.py |
| MEDIUM | D-3 | 삭제 | WadsSqlResponse 사용 금지 | - |
| MEDIUM | D-4 | 삭제 | SQL 프롬프트 prompts.py 배치 금지 | wads_tools.py |
| LOW | A-7 | 추가 | create_react_agent 에러 핸들링 확인 | wads_agent.py |
| LOW | A-8 | 추가 | SQL 생성 모델 분리 설정 | wads_tools.py |
| LOW | D-5 | 삭제 | `limit` 파라미터 제거 | wads_tools.py |

---

## 구현 순서

### Phase 1: 기반 (wads_agent_node 쿼리 전달 수정)
- 변경1: messages에서 원본 쿼리 추출 (I-1)
- `_wads_prompt` 날짜 컨텍스트 주입 (A-4)
- 선택적 히스토리 필터링 (S-3)
- `recursion_limit` 상향 (A-2)

### Phase 2: SQL 도구 (wads_query_sql 구현)
- @tool 구현 + 고품질 docstring (A-1)
- ContextVar `storage["sql_result"]` 패턴 (A-3, S-1, D-2)
- 4계층 SQL 검증 (I-3)
- 에러 분류별 복구 메시지 (S-4, A-7)
- Langfuse 트레이싱 (A-5)
- WADS_TOOLS 등록

### Phase 3: 렌더링 (동적 HTML 렌더러)
- `_render_wads_sql_html` 구현 (I-2)
- `wads_agent_node` 렌더링 우선순위 분기 (sql_result > reports > query)

### Phase 4: 프롬프트 (WADS 시스템 프롬프트 업데이트)
- 도구 선택 가이드 추가
- `_SAFETY_RULES` 미수정 확인 (D-1, I-4)
- SQL 생성 프롬프트는 도구 내부 (D-4)

### Phase 5: 검증
- 원본 계획의 7개 테스트 시나리오
- ContextVar 격리 (동시 요청)
- SQL 검증 로직 단위 테스트
- recursion_limit 경계 테스트
