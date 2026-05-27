# WADS Agent: 응답 자유도(Flexibility) 개선 플랜

## Context

**문제 (사용자 보고)**:
> "최근 3일치 검출 통계 알려줘" 처럼 자연어로 통계/요약을 물어도 항상 HTML 테이블 카드만 떨어진다. LLM이 데이터를 읽고 자연어로 답하지 못한다.

**증상**:
1. 통계성 질문("3일치 건수", "step별 비율", "어떤 lot이 많아?")에도 무조건 표가 렌더링됨.
2. LLM 응답은 "조회 완료. 총 N건. [SUGGESTION: ...]" 수준의 한 줄짜리 요약뿐 — 정작 숫자/패턴을 말로 풀어주지 않음.
3. WADS 외부 지식(wiki 개념 설명, 과거 fail history, yield 추세)과의 연계가 전혀 없어 "왜 늘었는지", "step01이 뭔지" 같은 후속 맥락 제공 불가.

## 핵심 파일 / 라인

- `wads_agent.py:57-61` — `create_react_agent` 그래프 정의 (도구 3개)
- `wads_agent.py:68-218` — `_render_wads_query_html` / `_render_wads_sql_html` / `_render_wads_report_html` 3종
- `wads_agent.py:302-345` — `wads_agent_node` 의 **렌더링 우선순위 분기** (reports > sql_result > query → 항상 artifact 발사)
- `wads_tools.py:128-169` — `wads_query_data`: LLM 반환 문자열에 상위 50건 LOTID 나열 (수치 통계 정보 없음)
- `wads_tools.py:391-411` — `wads_query_sql`: **LLM 반환은 "총 N건, 컬럼: a,b,c" 한 줄만**. 실제 집계값(COUNT 결과)은 ContextVar에만 저장 → LLM이 숫자를 못 보고 답함
- `prompts.py:526-616` — `WADS_SYSTEM_PROMPT_TEMPLATE`:
  - L594 "테이블이나 표를 직접 만들지 마세요"
  - L599 "2-3문장 요약하세요"
  - L601 "[SUGGESTION:] 형식 강제"

---

## 1. 진단: 왜 자유도가 낮은가 (4가지 근본 원인)

### R-1. 도구가 LLM에 데이터를 안 보여줌
`wads_query_sql`은 집계 결과(예: `[{step:"step01", cnt:12}, {step:"step02":8}, ...]`)를 ContextVar에 저장하고 LLM에는 **건수와 컬럼명만** 회신한다. 결국 LLM은 "3일치 통계 알려줘"에 답할 때 정작 12/8 같은 숫자를 모른다 → "별도 표시됩니다" 식의 회피 답변 외에는 못 한다.

### R-2. Node가 데이터 종류와 무관하게 항상 HTML artifact 발사
`wads_agent_node:314-345` 의 분기는 storage 키 존재 여부만 보고 무조건 `_render_*_html` 한 종을 골라 artifact 리스트에 넣는다. "통계 요약"이냐 "전체 리포트"냐의 **의도 구분 채널이 없다**.

### R-3. 프롬프트가 LLM의 표현을 봉쇄
- "표를 만들지 마세요" → 본문에 inline 마크다운 표 금지.
- "2-3문장 요약" → 분석/맥락 풀이 봉쇄.
- "[SUGGESTION:] 강제" → 자유 톤 차단.
세 규칙이 함께 작동하면서 LLM은 정해진 한 줄짜리 코멘트 외에 출력할 자리가 없다.

### R-4. 외부 데이터(wiki / fail_history / yield / lot_history) 미연동
같은 레포에 풍부한 보조 지식이 있다 (아래 §2.5 참조). 그러나 WADS 도구 리스트(`WADS_TOOLS`)는 Oracle 3종이 전부 → "왜 step02가 늘었지?" 같은 질문에 답할 외부 컨텍스트가 없다.

---

## 2. 개선안

### 2.1 [HIGH] R-1 해소: 도구 결과의 "LLM 가시성" 회복

**파일**: `wads_tools.py`

**원칙**: 도구 응답 = (a) 사람용 artifact는 ContextVar, (b) **LLM용 압축 요약은 문자열로 충분히 풍부하게** — 두 채널을 분리하되 LLM 채널을 굶기지 말 것.

**구체 변경**:

#### (a) `wads_query_data` (현 `wads_tools.py:128-169`)
- 현재: 상위 50건 LOT 라인 나열만 — 통계 정보 0.
- 변경: 데이터 후처리하여 LLM 반환 텍스트에 아래 요약 블록 추가
  ```
  WADS 데이터 조회 완료: 총 N건
  - 기간: {start_tm} ~ {end_tm}, 일평균 {avg_per_day:.1f}건
  - step 분포: step01=12건, step02=8건, ...   (상위 5개)
  - lotcd 분포: 4SS=20건, 5NA=7건, ...        (상위 5개)
  - 최신 3건: LOTID/STEP/END_TM ...
  ```
- 이러면 "최근 3일 검출 통계 알려줘"에 LLM이 직접 숫자 인용 가능.

#### (b) `wads_query_sql` (현 `wads_tools.py:391-411`)
- 현재: `"WADS SQL 쿼리 실행 완료: 총 47건. 컬럼: ctn_desc, cnt"` 한 줄.
- 변경: **결과 행 수가 작으면 (≤ 20행) 전체 JSON을 그대로 LLM에 노출**. 그 이상이면 상위 10행 + 통계 메타(`n_rows`, `n_cols`, 수치 컬럼의 sum/min/max/avg)를 직렬화.
- ContextVar 저장(=artifact용)은 그대로 유지 — 호환성 깨지지 않음.

#### (c) (신규) HTML 컬럼은 LLM 반환에서 제외
`wads_get_html_report` 의 LLM 회신 텍스트에 절대 HTML 본문이 섞이지 않도록 명시(현재도 그렇지만, SQL 경로에서 누락 위험 있음 — guard 추가).

---

### 2.2 [HIGH] R-2 해소: 응답 모드(artifact 발사 여부)를 LLM이 결정

**파일**: `wads_agent.py` (+ 신규 `@tool` 1개 또는 system prompt 명령어)

**옵션 A — 권장: "render mode" 시그널 토큰**
- 시스템 프롬프트에 명시: 답변 마지막 줄에 다음 중 하나의 **단일 토큰**을 출력
  - `[RENDER: text]` — artifact 없음, LLM 본문만 표시
  - `[RENDER: report]` — wads_get_html_report 결과 + 본문
  - `[RENDER: table]` — wads_query_data/sql 결과를 카드로
  - `[RENDER: auto]` — 현재 우선순위 로직 유지 (default)
- `wads_agent_node` 가 응답에서 토큰을 파싱(`extract_suggestion`과 동일 패턴)해서 artifact 발사를 분기. 토큰은 본문에서 strip.
- 장점: 도구 추가 없이 LLM의 의도 채널 확보. 그리고 강제 표 출력을 끌 수 있음.

**옵션 B — 대안: 별도 도구 `wads_summarize_stats(group_by, ...)`**
- 항상 통계 JSON만 반환, artifact 발사 안 함. LLM은 본문에서 자연어로 풀이.
- 단점: 도구 수 증가 + LLM이 선택 실수 가능.

**구현 권장**: 옵션 A. `extract_suggestion`처럼 정규식 한 줄 + `wads_agent_node:314-345` 분기에 `if render_mode == "text": artifacts = []` 한 줄 추가.

---

### 2.3 [HIGH] R-3 해소: 프롬프트 자유도 확대

**파일**: `prompts.py:526-616`

**삭제**:
- "테이블이나 표를 직접 만들지 마세요" — 조건부로 약화 (아래 참조).
- "2-3문장 요약하세요" 단일 규칙 → 의도별 가이드로 교체.

**대체 문구(요지)**:
```
## 응답 스타일 (의도에 맞게 적응)
- 사용자가 "리포트 보여줘" / 특정 step 리포트 요청 → wads_get_html_report 호출, 본문은 짧게 (1-2문장).
- 사용자가 "통계", "건수", "분포", "추세", "어떤 step이 많아?" 등 분석성 질의 → wads_query_data 또는 wads_query_sql 결과의 숫자를 본문에서 직접 인용해 자연어로 정리. 짧은 마크다운 bullet/표 사용 허용.
- 사용자가 "왜", "원인", "맥락" 등 해석성 질의 → 데이터 + 외부 컨텍스트(wads_lookup_*)를 종합하여 단락 형태로 풀이.
- 사용자가 "lot 리스트" → wads_query_data 호출 후 ID 나열 + 한 줄 코멘트.

## 응답 마무리 (둘 다 마지막 줄에 1회씩)
1) [RENDER: text|report|table|auto]  ← artifact 모드 시그널 (필수)
2) [SUGGESTION: ...]                  ← 후속 제안 (선택)
```

**전제**:
- 본문 마크다운 표/bullet 허용 (단, "리포트 자체"를 표로 재현하진 말 것 — artifact에 이미 있음).
- 길이 제약을 "의도 적응"으로 완화.

---

### 2.4 [HIGH] R-4 해소: 외부 데이터 연계 — 신규 도구 3종

WADS 외 같은 레포의 자산 (모두 read-only adapter 만 추가하면 됨):

| 데이터원 | 모듈 | 무엇을 줄 수 있나 |
|---------|------|--------------------|
| Wiki / Super-concept | `wiki_store.lookup`, `wiki_store.lookup_super_concept` | step / fail_type / cause_oper 의 의미·정의·과거 누적 본문 |
| Fail History (OpenSearch) | `fail_history_tools._search_opensearch`, `do_search` | 같은 product+step 의 과거 cause/action 사례 |
| Yield DB | `yield_db` / `yield_query_agent` 보조 함수 | 같은 lotcd의 같은 주의 yield 수치 — 상관 분석용 |
| Lot history (Inline-WT) | `lot_history_tools` | 검출된 lot의 inline-WT 이력 |

**제안: WADS에 추가할 도구 (모두 read-only, 결과는 LLM 회신 텍스트로만 노출 — artifact 발사 안 함)**

1. **`wads_lookup_step_context(step: str)`**
   - `wiki_store.lookup_super_concept(axis="fail_type", axis_value=step)` 또는 fallback `lookup(query=step)` 호출.
   - LLM에 step의 의미 / 측정 위치 / 관찰 포인트를 텍스트로 회신.
   - 사용 시점: 사용자가 "step01이 뭔데?" / "왜 늘었지?" 류 질의.

2. **`wads_lookup_related_failures(lotcd: str, step: str, top_k: int=3)`**
   - `fail_history_tools._search_opensearch` 를 product/cause_oper로 좁혀 호출.
   - 회신: 과거 doc_id 리스트 + 한 줄 요약(cause/action).
   - 사용 시점: "원인이 뭘까?" 류 질의.

3. **`wads_correlate_with_yield(lotcd: str, start_tm: str, end_tm: str)`**
   - 같은 기간의 yield 평균/변화율을 yield_db에서 조회 → "검출 N건 ↑, yield ↓0.3%p" 식 요약.
   - 사용 시점: "검출이 늘었는데 수율은?" 류 질의.

**중요 가드레일**:
- 이 3개 도구는 모두 **artifact 발사하지 않음** — LLM 본문 정합용 정보만. 그래야 R-2와 충돌 없음.
- `@observe(name=...)` Langfuse trace 필수.
- WADS 시스템 프롬프트의 TASK SCOPE는 유지하되, "lookup 도구는 본문 보강용으로만 호출, 1회씩만" 명시 → 무한 호출 방지.

---

### 2.5 [MEDIUM] 응답 합성 단계 (선택적 강화)

**현재**: ReAct 한 사이클에서 LLM이 도구 호출 → 곧장 사용자 답변 생성.

**옵션**: yield_agent 패턴(`yield_query_agent._analyze_with_llm`)처럼, 도구 결과가 모이면 **별도 합성 LLM 호출**로 자연어 답을 만든다.
- 장점: 도구 호출용 모델(빠른 모델)과 분석용 모델(강한 모델)을 분리 가능 (`WADS_SYNTH_MODEL` 환경변수).
- 단점: 지연 +1-2초, 호출 수 +1.
- **권장**: 우선 §2.1–2.4 만으로 검증 후, 자유 답변 품질이 더 필요할 때 도입. 1차 PR에는 포함하지 않음.

---

## 3. 보완 (Supplement)

### S-1. `wads_agent_node` 분기 단순화
- 현재 `reports > sql_result > query` 우선순위는 유지.
- 위에 `if render_mode == "text"` 분기 1줄 추가 → `artifacts = []`.
- `[RENDER:]` 태그 파싱 헬퍼는 `common.extract_suggestion` 옆에 `extract_render_mode` 로 페어 추가.

### S-2. `wads_artifacts` 빈 리스트 허용 검증
- `agent_server.py:557-602` 의 artifact emit 루프가 빈 리스트도 안전히 처리하는지 확인 (이미 안전 — `for art in []`).
- `ppt_builder._add_wads_slides` (현 `ppt_builder.py:958+`)는 빈 리스트 시 슬라이드 미생성 — 정상.

### S-3. 평가 보강
- `eval/` 폴더에 의도별 골든셋 추가:
  - 통계형 ("3일치 step별 건수")
  - 리포트형 ("4SS step01 리포트")
  - 해석형 ("왜 늘었지")
  - 리스트형 ("lot id 알려줘")
- 각 케이스에서 (a) 본문에 숫자 존재 여부, (b) artifact 모드, (c) 응답 길이 적정성 체크.

---

## 4. 삭제 / 회피 (Delete)

### D-1. 도구 결과를 LLM 메시지에 HTML 그대로 흘리지 말 것
LLM 컨텍스트가 HTML 폭탄을 맞으면 다음 턴에서 환각 / 응답 폭주 위험. 현재도 별도 카드로 격리되어 있으니 그대로 둘 것. §2.1 (a)/(c) 가드 유지.

### D-2. "표 금지" 강제 규칙 완전 제거 금지
artifact 카드가 이미 표인 경우(report/table 모드)에는 LLM이 본문에서 같은 표를 또 그리지 않도록 **조건부 금지**는 유지. 무조건 금지는 풀고, "artifact가 발사된 모드에서는 본문 표 금지" 로 좁힘.

### D-3. `with_structured_output()` / Pydantic 결과 스키마 추가 금지
OpenRouter 호환성 문제로 폐기됨 (기존 플랜 D-3 그대로 적용).

### D-4. 외부 데이터 lookup 도구의 결과를 ContextVar 에 쌓지 말 것
이 데이터는 본문 인용 용도. ContextVar에 쌓으면 R-2의 artifact 분기를 다시 더럽힘.

---

## 5. 우선순위 종합

| 우선순위 | ID | 내용 | 파일 |
|---------|-----|------|------|
| HIGH | R-1 (a) | `wads_query_data` LLM 회신에 step/lotcd 분포 + 일평균 추가 | wads_tools.py:128-169 |
| HIGH | R-1 (b) | `wads_query_sql` 결과 ≤20행은 전체 JSON, 초과시 top10 + 통계 메타 | wads_tools.py:391-411 |
| HIGH | R-2 | `[RENDER:]` 태그 파싱 + `wads_agent_node` 분기 추가 | wads_agent.py:302-345, common.py |
| HIGH | R-3 | WADS 시스템 프롬프트 자유도 확대 (표 금지 조건부화, 길이 적응) | prompts.py:594-613 |
| HIGH | R-4 (1) | `wads_lookup_step_context` 신규 도구 | wads_tools.py, wiki_store.py |
| MEDIUM | R-4 (2) | `wads_lookup_related_failures` 신규 도구 | wads_tools.py, fail_history_tools.py |
| MEDIUM | R-4 (3) | `wads_correlate_with_yield` 신규 도구 | wads_tools.py, yield_db.py |
| MEDIUM | S-3 | 의도별 평가 골든셋 추가 | eval/ |
| LOW | 2.5 | 별도 합성 LLM 단계 (선택) | wads_agent.py |
| LOW | D-2 | "본문 표 금지" 조건부화 (artifact 있을 때만) | prompts.py |

---

## 6. 구현 순서 (PR 분할 권장)

### PR1 — 기반: 데이터 가시성 + 모드 시그널 (R-1 + R-2 + R-3)
**한 PR에 묶음 — 셋이 분리되면 사용자 체감 변화 없음.**
1. `extract_render_mode` 헬퍼 추가 (`common.py`).
2. `wads_query_data` / `wads_query_sql` 회신 텍스트 강화 (§2.1).
3. `wads_agent_node` 에 `[RENDER:]` 분기 추가 (§2.2 옵션 A).
4. WADS 시스템 프롬프트 자유도 확대 + `[RENDER:]` 사용 가이드 추가 (§2.3).
5. **검증**: 4가지 의도(통계/리포트/해석/리스트) 케이스 수동 확인.

### PR2 — 외부 데이터: wiki lookup (R-4 (1))
- `wads_lookup_step_context` 단독.
- 시스템 프롬프트에 "해석성 질의에만 1회 호출" 가이드.
- 평가: "step01이 뭐야" 응답에 wiki 정의가 인용되는지.

### PR3 — 외부 데이터: fail_history + yield (R-4 (2),(3))
- 두 도구를 함께 (둘 다 "원인/맥락" 질의에 같이 쓰이는 경우 많음).
- TASK SCOPE 가드: "lookup 도구는 합쳐서 최대 2회 호출".

### PR4 (선택) — 합성 LLM 단계 (§2.5)
- 자유 답변 품질 정량 평가 후 도입 결정.

---

## 7. 검증 시나리오

| # | 사용자 입력 | 기대 동작 |
|---|------------|----------|
| 1 | "4SS 최근 3일치 검출 통계 알려줘" | wads_query_data → 본문: 일평균/step분포/lot분포 자연어 인용. `[RENDER: text]` → **artifact 없음**. |
| 2 | "4SS step01 리포트 보여줘" | wads_get_html_report → 본문 1-2문장. `[RENDER: report]` → HTML artifact 1개. |
| 3 | "왜 5NA step02가 늘었지?" | wads_query_sql + wads_lookup_step_context + wads_lookup_related_failures → 본문 단락형 해석. `[RENDER: text]`. |
| 4 | "지난주 검출된 lot id 알려줘" | wads_query_data → 본문에 LOTID 나열 + 한 줄 코멘트. `[RENDER: text]` (또는 `[RENDER: table]` LLM 판단). |
| 5 | "step별 건수 집계 표로 보여줘" (명시적 표 요청) | wads_query_sql → `[RENDER: table]` 동적 컬럼 카드. 본문은 한 줄 코멘트. |

---

## 8. 기존 계획과의 관계

`peaceful-wobbling-map.md` (text2sql 전환 플랜)은 **이미 적용된 상태**로 가정. 본 플랜은 그 위에 응답 자유도를 얹는 후속 작업이다.
- `wads_query_sql` 자체는 그대로 사용 (이미 ContextVar 패턴 적용됨).
- `_render_wads_sql_html` 도 그대로 — `[RENDER: auto|table]` 일 때만 호출되도록 분기만 추가.
- `_SAFETY_RULES` 는 본 플랜에서도 수정하지 않음 (이전 D-1 유지).
