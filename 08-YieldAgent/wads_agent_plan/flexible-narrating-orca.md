# WADS Agent: 응답 자유도(Flexibility) 개선 플랜

## Context

**문제 (사용자 보고)**:
> "최근 3일치 검출 통계 알려줘" 처럼 자연어로 통계/요약을 물어도 항상 HTML 테이블 카드만 떨어진다. LLM이 데이터를 읽고 자연어로 답하지 못한다.

**증상**:
1. 통계성 질문("3일치 건수", "step별 비율", "어떤 lot이 많아?")에도 무조건 표가 렌더링됨.
2. LLM 응답은 "조회 완료. 총 N건. [SUGGESTION: ...]" 수준의 한 줄짜리 요약뿐 — 정작 숫자/패턴을 말로 풀어주지 않음.
3. WADS 외부 지식(wiki 개념 설명, 과거 fail history, yield 추세)과의 연계가 전혀 없어 "왜 늘었는지", "step01이 뭔지" 같은 후속 맥락 제공 불가.

## 0. 실제 스키마 (선행 정정 — 본 플랜의 모든 도구/프롬프트 기준)

WADS 관련 Oracle 테이블은 **2개**다. 현재 코드(`wads_tools.py:25`)는 단일 `WADS_TABLE` env var 한 개만 가정하고, 컬럼명도 `LOTID`/`CTN_DESC`/`HTML` 5종으로 가정하지만 **실제와 다르다**.

### DF_WADS_REPORT (현 `_query_wads_data`가 대상으로 삼는 테이블)
| 컬럼 | 의미 | 비고 |
|------|------|------|
| LOTCD | 제품 로트코드 | "4SS", "5NA" 등 |
| CATEGORY | 테스트 카테고리 | `PT1H_TEST` 또는 `PT1C_TEST` (2종) |
| PARAMETER | fail_type | 예: "EASY", "TWT" 등 (현 코드의 "step01..09" 가정은 잘못됨) |
| END_TM | 종료 시각 | "YY/MM/DD" 포맷 (예: "26/02/13") — 현 코드의 "YYYY-MM-DD HH:MM:SS" 가정과 다름 |
| HTML | 레포트 본문 | CLOB |

**LOTID 컬럼 없음** — 현 `wads_query_data` 가 `LOTID` 를 SELECT 하는 구문(`wads_tools.py:142`)은 실행 실패하거나, 운영 환경에 view 가 래핑돼 있어 우연히 통과 중일 가능성 있음. 확인 필요.

### DF_WADS_WF_LIST (신규 — 본 플랜에서 추가로 활용)
| 컬럼 | 의미 | 비고 |
|------|------|------|
| OPER_PARA | `{CATEGORY}_{PARAMETER}` 결합 | 예: `PT1H_TEST_EASY(W)` |
| GROUPKEY | 웨이퍼 식별 키 | 검출된 개별 wafer |
| END_TM | 종료 시각 | DF_WADS_REPORT 와 조인 키 |
| LOT_CD | 로트코드 | DF_WADS_REPORT.LOTCD 와 동일 의미(컬럼명만 다름 — 언더스코어) |

→ DF_WADS_REPORT 가 "(lotcd, category, parameter, end_tm) 단위 리포트 1행"이라면 DF_WADS_WF_LIST 는 그 리포트에 포함된 **wafer N개의 explode**.

### 본 스키마가 플랜에 미치는 영향
1. **CATEGORY 가 새 필터 차원**: 현 도구/프롬프트에는 PT1H_TEST/PT1C_TEST 개념이 없음. 통계 질문 ("PT1H 검출만 알려줘") 처리 불가 → 필터 + 분포 요약에 반드시 포함.
2. **PARAMETER 는 fail_type**: 시스템 프롬프트의 "step01..09" 예시 (현 `prompts.py:545-547, 583-591`) 전면 교체. 동시에 §2.4 R-4(2) `wads_lookup_related_failures` 와 의미적으로 정합 — 같은 fail_type 축으로 wiki/fail_history 연결 가능.
3. **wafer 단위 차원 추가**: "몇 개 wafer가 검출됐어?" / "어떤 GROUPKEY?" 류 질의는 DF_WADS_REPORT 만으로는 답 불가. WF_LIST 도구가 필요.
4. **END_TM 포맷 정정**: `_query_wads_data` (`wads_tools.py:65-74`) 의 `TO_DATE(..., 'YYYY-MM-DD')` 가정은 실제 "YY/MM/DD" 와 충돌. 별도 P0 fix 필요 (본 플랜의 사전 작업으로 §6 PR1 에 포함).
5. **테이블 이름 env var 분리**: 단일 `WADS_TABLE` → `WADS_REPORT_TABLE` + `WADS_WF_LIST_TABLE` 2개. SQL 검증 allowlist 도 2개로 확장 (`wads_tools.py:278-282`).

---

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

#### (a) `wads_query_data` (현 `wads_tools.py:128-169`) — DF_WADS_REPORT 대상
- 현재: 상위 50건 LOT 라인 나열만 — 통계 정보 0.
- 변경: 데이터 후처리하여 LLM 반환 텍스트에 아래 요약 블록 추가 (스키마 §0 반영)
  ```
  WADS 리포트 조회 완료: 총 N건 (DF_WADS_REPORT)
  - 기간: {start_tm} ~ {end_tm}, 일평균 {avg_per_day:.1f}건
  - CATEGORY 분포: PT1H_TEST=18건, PT1C_TEST=9건
  - PARAMETER(fail_type) 분포: EASY=12건, TWT=8건, ...   (상위 5개)
  - LOTCD 분포: 4SS=20건, 5NA=7건, ...                   (상위 5개)
  - 최신 3건: LOTCD/CATEGORY/PARAMETER/END_TM ...
  ```
- 이러면 "최근 3일 검출 통계 알려줘"에 LLM이 직접 숫자 인용 가능.
- **필터 시그니처 확장**: `category: Optional[str]` 추가 (PT1H_TEST | PT1C_TEST, 부분일치).

#### (b) `wads_query_sql` (현 `wads_tools.py:391-411`)
- 현재: `"WADS SQL 쿼리 실행 완료: 총 47건. 컬럼: ctn_desc, cnt"` 한 줄.
- 변경: **결과 행 수가 작으면 (≤ 20행) 전체 JSON을 그대로 LLM에 노출**. 그 이상이면 상위 10행 + 통계 메타(`n_rows`, `n_cols`, 수치 컬럼의 sum/min/max/avg)를 직렬화.
- ContextVar 저장(=artifact용)은 그대로 유지 — 호환성 깨지지 않음.
- **SQL 생성 프롬프트 정정** (`wads_tools.py:295-312` `_SQL_GEN_PROMPT`):
  - 테이블 2개 (`DF_WADS_REPORT`, `DF_WADS_WF_LIST`) + 컬럼 정정.
  - END_TM 포맷 `'YY/MM/DD'` 기반 `TO_DATE(SUBSTR(END_TM,1,8),'YY/MM/DD')`.
  - JOIN 가이드: `r.LOTCD = w.LOT_CD AND r.END_TM = w.END_TM` (필요 시).
  - allowlist 도 두 테이블로 확장 (`wads_tools.py:278-282`).

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

### 2.4 [HIGH] R-4 해소: 외부 데이터 연계 — 신규 도구 (내부 WF_LIST + 외부 지식)

#### 2.4.0 (선행, 신규) `wads_query_wf_list` — DF_WADS_WF_LIST 전용 도구
스키마 §0 의 **wafer 단위 차원**을 노출하는 도구. 본 플랜의 가장 큰 신규 정보원.

```python
@tool
def wads_query_wf_list(
    lotcd: Optional[str] = None,
    category: Optional[str] = None,       # PT1H_TEST | PT1C_TEST
    parameter: Optional[str] = None,      # fail_type (예: EASY, TWT)
    start_tm: Optional[str] = None,       # YY/MM/DD
    end_tm: Optional[str] = None,
    group_by: Optional[str] = None,       # "lotcd" | "category" | "parameter" | "end_tm" — None이면 raw
) -> str:
    """WADS 웨이퍼 단위 검출 목록(DF_WADS_WF_LIST) 조회/집계."""
```

- 필터 조합 (1):
  - `category`, `parameter` 따로 주면 → SQL에서 `OPER_PARA LIKE '{category}_{parameter}%'` 로 합성.
  - `category` 만 → `OPER_PARA LIKE '{category}_%'`.
  - `parameter` 만 → `OPER_PARA LIKE '%_{parameter}%'`.
- LLM 회신 텍스트 (§2.1 (a) 와 동일 원칙 — **수치 인용 가능하게**):
  ```
  WF 검출 wafer: 총 N개 (DF_WADS_WF_LIST)
  - LOTCD 분포: 4SS=18, 5NA=4, ...
  - CATEGORY 분포: PT1H_TEST=14, PT1C_TEST=8
  - PARAMETER 분포: EASY=10, TWT=8, ...
  - 대표 GROUPKEY 5개: ...
  ```
- artifact: `group_by` 주어진 집계 결과만 표 카드로 렌더링(§2.2 의 `[RENDER: table]` 시), raw 리스트는 본문 인용으로 충분.
- **현재 `wads_query_data` 가 LOTID 를 회신**(`wads_tools.py:161-169`) 하던 동작의 진짜 자리는 사실상 이 도구임. supervisor 의 chained input 패턴 (`wads_agent.py:354-376`) 도 LOT_CD/GROUPKEY 회수 시 본 도구로 옮길지 검토 필요.

#### 2.4.1 외부 지식 lookup (이전 플랜의 R-4 (1)(2)(3) 그대로, fail_type 정합성으로 더 강해짐)

WADS 외 같은 레포의 자산 (모두 read-only adapter):

| 데이터원 | 모듈 | 무엇을 줄 수 있나 |
|---------|------|--------------------|
| Wiki / Super-concept | `wiki_store.lookup`, `wiki_store.lookup_super_concept` | **PARAMETER(=fail_type) / CATEGORY** 의 의미·정의·과거 누적 본문 |
| Fail History (OpenSearch) | `fail_history_tools._search_opensearch`, `do_search` | 같은 product + fail_type 의 과거 cause/action 사례 (PARAMETER 가 fail_type 이므로 직결) |
| Yield DB | `yield_db` / `yield_query_agent` 보조 함수 | 같은 lotcd의 같은 주의 yield 수치 — 상관 분석용 |
| Lot history (Inline-WT) | `lot_history_tools` | 검출된 lot의 inline-WT 이력 |

**제안: WADS에 추가할 lookup 도구 (모두 read-only, 결과는 LLM 회신 텍스트로만 노출 — artifact 발사 안 함)**

1. **`wads_lookup_param_context(parameter: str, category: Optional[str]=None)`**
   - `wiki_store.lookup_super_concept(axis="fail_type", axis_value=parameter)` 또는 fallback `lookup(query=f"{category} {parameter}")` 호출.
   - LLM에 PARAMETER(=fail_type) 의 의미 / 측정 위치 / 관찰 포인트를 텍스트로 회신.
   - 사용 시점: "EASY가 뭐야?" / "PT1H_TEST와 PT1C_TEST 차이?" / "왜 늘었지?" 류 질의.

2. **`wads_lookup_related_failures(lotcd: str, parameter: str, top_k: int=3)`**
   - `fail_history_tools._search_opensearch` 를 product=lotcd, fail_type=parameter 로 좁혀 호출.
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

### 2.5 [HIGH] R-2': 동일 HTML 카드 한 종류만 떨어지는 문제 해소

**문제 명확화**: §2.2 의 `[RENDER: text|report|table|auto]` 태그는 **artifact 채널의 on/off + 종류**만 결정한다. 그러나 `table` 또는 `auto` 가 선택돼 카드가 발사되는 경우, 카드의 시각적 포맷은 여전히 `_render_wads_sql_html` (`wads_agent.py:116-169`) 한 종류 — 제목 / 건수 배지 / 컬럼 동적 표 — 로 고정. 단일 COUNT, 분포, 피벗, 시계열 모두 같은 표 박스로 표시된다. 또한 SQL 도구로 가는 입력(`query_description` 자연어)도 자유롭지만, 진입 도구(`wads_query_data`)의 **조건 시그너처가 lotcd/start_tm/end_tm/parameter 4개로 고정**돼 있어 "PT1H 만", "EASY 와 TWT 둘 다", "lotcd LIKE '4S%'" 같은 조건은 모두 `wads_query_sql` 로 강제 우회 → 결과적으로 항상 SQL 카드 한 종류만 나옴.

#### 2.5.A 1순위: artifact 기본형을 **markdown** 으로 전환

- `ArtifactType.markdown` 이 이미 존재 (`models.py:29`). 프런트엔드는 이미 다룰 줄 안다.
- 통계 / 분포 / 비교 / 추세 / lot 리스트 → LLM 본문 자체가 곧 artifact. 별도 카드 chrome / 표 박스 / "총 N건 조회됨" 배지 모두 사라진다.
- 시그널: `[RENDER: md]` 추가 (§2.2 의 태그 집합 확장 → `text | md | report | table | auto`).
  - `text` = artifact 0개, 메시지만.
  - `md` = LLM 본문을 `ArtifactType.markdown` 으로 1개 발사 (오른쪽 패널에 본문 그대로).
  - `report` = HTML report (DF_WADS_REPORT.HTML).
  - `table` = 정형 표 카드 (의도된 표 출력).
  - `auto` = 이전 로직 fallback.
- `wads_agent_node:314-345` 분기에 `md` 케이스 추가:
  ```python
  if render_mode == "md":
      artifacts = [{
          "type": "markdown", "mime": "text/markdown",
          "data": answer, "title": "wads_summary",
      }]
  ```

#### 2.5.B 2순위: HTML 카드가 필요한 경우 **레이아웃 레지스트리** 도입

`table` / `auto` 로 HTML 카드가 발사되는 경우, 단일 렌더러를 5종 레이아웃으로 분기:

| 레이아웃 | 자동 감지 규칙 | 시각화 |
|---------|----------------|--------|
| `kpi` | 행 1개, 컬럼 1-3개 (대부분 숫자) | 큰 숫자 + 캡션 카드 (1-3개) |
| `distribution` | 컬럼 2개 (범주 + 숫자), 행 2-20개 | 가로 막대 (텍스트 또는 inline SVG) + 비율% |
| `pivot` | 컬럼 ≥3개, 첫 2개가 범주축 | 피벗 표 (헤더 강조) |
| `trend` | END_TM 컬럼 + 숫자 컬럼 | 시계열 라인 (matplotlib PNG, 또는 sparkline ascii) |
| `table` | 위 어디에도 안 맞음 (fallback) | 현재 동적 컬럼 표 |

- 감지는 **도구 측**에서 (LLM 부담 ↓). `wads_query_sql` 결과 dict 리스트의 shape 보고 `storage["render_layout"]` 에 힌트 저장.
- LLM 이 시그널을 덮어쓸 수 있음: `[RENDER: table:distribution]` 처럼 콜론으로 layout 지정 (옵션).
- 구현 위치: `wads_agent.py` 의 `_render_wads_sql_html` 을 `_render_kpi` / `_render_distribution` / `_render_pivot` / `_render_trend` / `_render_table` 5개 함수 + dispatcher 로 분리.

#### 2.5.C 3순위: chart artifact (선택)

- `distribution` / `trend` 는 matplotlib PNG → `ArtifactType.image` 로 발사하면 시각적 차별성 최대.
- `yield_viz.py` 패턴(이미 PNG base64 다루는 코드 있음) 재활용.
- 1차 PR 범위 밖. PR4 이후로.

#### 2.5.D 진입 도구 시그너처 확장 — 조건 표현 자유도

`wads_query_data` (`wads_tools.py:110-127`) 의 시그너처 한정이 사용자 조건을 SQL 도구로 강제 우회시키는 근원:

- `category: Optional[str | list[str]]` 추가 (§2.1 (a) 에 이미 단일 str 추가 — multi-value 로 확장).
- `parameter: Optional[str | list[str]]` (multi-value: SQL 내부에서 `OR` 결합).
- `lotcd: Optional[str | list[str]]` (multi-lot 비교 시).
- `exclude_parameter: Optional[list[str]]` (NOT LIKE).
- 동일 변경을 `wads_query_wf_list` (§2.4.0) 에도 적용 — 처음부터 다중값 받게 설계.

→ 결과: 단순 조건(다중값 / NOT)도 `wads_query_sql` 거치지 않고 처리. `wads_query_sql` 의 사용 빈도는 줄고, SQL 카드의 단조로움도 자연히 줄어듦.

#### 2.5.E LLM 작성형 artifact (가장 자유로운 옵션 — 보류)

- 신규 `@tool wads_render_custom(body_md: str, kind: Literal["md","html","mermaid"])` — LLM 이 도구 결과를 보고 직접 artifact 본문 작성.
- 위험: 환각 / ContextVar 와 cross-check 못 함 / 토큰 비용.
- 권장: 1차 도입 안 함. 자유도가 여전히 부족하면 PR5 (§2.6) 합성 LLM 단계와 함께 도입.

---

### 2.6 [MEDIUM] 응답 합성 단계 (선택적 강화)

**현재**: ReAct 한 사이클에서 LLM이 도구 호출 → 곧장 사용자 답변 생성.

**옵션**: yield_agent 패턴(`yield_query_agent._analyze_with_llm`)처럼, 도구 결과가 모이면 **별도 합성 LLM 호출**로 자연어 답을 만든다.
- 장점: 도구 호출용 모델(빠른 모델)과 분석용 모델(강한 모델)을 분리 가능 (`WADS_SYNTH_MODEL` 환경변수).
- 단점: 지연 +1-2초, 호출 수 +1.
- **권장**: 우선 §2.1–2.5 만으로 검증 후, 자유 답변 품질이 더 필요할 때 도입. 1차 PR에는 포함하지 않음.

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
| **P0** | §0-1 | END_TM 포맷 'YY/MM/DD' 로 정정 (TO_DATE 인자) | wads_tools.py:65-74 |
| **P0** | §0-2 | 컬럼명 정정: LOTID 제거, CTN_DESC → PARAMETER, CATEGORY 추가 | wads_tools.py:80, 142, 206, 295-312 |
| **P0** | §0-3 | env var 분리: `WADS_REPORT_TABLE`, `WADS_WF_LIST_TABLE` + SQL allowlist 2개 | wads_tools.py:25, 278-282 |
| HIGH | R-1 (a) | `wads_query_data` LLM 회신에 CATEGORY/PARAMETER/LOTCD 분포 + 일평균 추가 + `category` 필터 | wads_tools.py:128-169 |
| HIGH | R-1 (b) | `wads_query_sql` 결과 ≤20행은 전체 JSON, 초과시 top10 + 통계 메타 | wads_tools.py:391-411 |
| HIGH | R-2 | `[RENDER:]` 태그 파싱 (`text/md/report/table/auto`) + `wads_agent_node` 분기 | wads_agent.py:302-345, common.py |
| HIGH | R-2' (A) | **artifact 기본형 markdown 전환** — `[RENDER: md]` 케이스에서 본문을 markdown artifact 로 발사 | wads_agent.py, models.py:29 |
| HIGH | R-2' (B) | **레이아웃 레지스트리** — `_render_wads_sql_html` 을 kpi/distribution/pivot/trend/table 5종으로 분기 + 도구 측 자동 감지 | wads_agent.py:116-169, wads_tools.py |
| HIGH | R-2' (D) | `wads_query_data` / `wads_query_wf_list` 시그너처 multi-value 확장 (`list[str]` + exclude) | wads_tools.py:110-127 |
| HIGH | R-3 | WADS 시스템 프롬프트 자유도 확대 + step→PARAMETER(fail_type) 예시 교체 + `[RENDER: md]` 가이드 | prompts.py:526-616 |
| HIGH | R-4 (0) | **`wads_query_wf_list` 신규 도구 (DF_WADS_WF_LIST)** — wafer/GROUPKEY 차원 | wads_tools.py |
| HIGH | R-4 (1) | `wads_lookup_param_context` 신규 도구 | wads_tools.py, wiki_store.py |
| MEDIUM | R-4 (2) | `wads_lookup_related_failures` (parameter=fail_type 직결) | wads_tools.py, fail_history_tools.py |
| MEDIUM | R-4 (3) | `wads_correlate_with_yield` 신규 도구 | wads_tools.py, yield_db.py |
| MEDIUM | R-2' (C) | chart artifact (matplotlib PNG → `ArtifactType.image`) | wads_agent.py, yield_viz.py |
| MEDIUM | S-3 | 의도별 평가 골든셋 추가 (CATEGORY/WF_LIST/layout 케이스 포함) | eval/ |
| LOW | 2.6 | 별도 합성 LLM 단계 (선택) | wads_agent.py |
| LOW | 2.5.E | `wads_render_custom` LLM 작성형 artifact (보류) | wads_tools.py |
| LOW | D-2 | "본문 표 금지" 조건부화 (artifact 있을 때만) | prompts.py |

---

## 6. 구현 순서 (PR 분할 권장)

### PR0 — 스키마 정정 (§0)
**선행 필수.** 본 PR이 머지되지 않으면 PR1 이후 변경은 의미 없음.
1. `_ORACLE_TABLE` 단일 변수 → `_REPORT_TABLE` + `_WF_LIST_TABLE` 두 변수.
2. `_query_wads_data` SQL: `SELECT LOTCD, CATEGORY, PARAMETER, END_TM` 로 컬럼 정정, LOTID 제거.
3. END_TM TO_DATE 포맷 `'YY/MM/DD'` 로 변경 + SUBSTR 길이 8.
4. SQL 검증 allowlist 2개 테이블로 확장.
5. `_SQL_GEN_PROMPT` 스키마 블록 전면 교체.
6. 운영에서 LOTID/CTN_DESC view alias 가 있는지 확인 — 있다면 호환성 alias 한 줄 추가.

### PR1 — 기반: 데이터 가시성 + 모드 시그널 + markdown artifact (R-1 + R-2 + R-2'.A + R-2'.D + R-3)
**한 PR에 묶음 — 셋이 분리되면 사용자 체감 변화 없음.**
1. `extract_render_mode` 헬퍼 추가 (`common.py`). 태그 집합: `text | md | report | table | auto`.
2. `wads_query_data` / `wads_query_sql` 회신 텍스트 강화 (§2.1) + multi-value 시그너처 확장 (§2.5.D).
3. `wads_agent_node` 에 `[RENDER:]` 분기 추가 (§2.2) + **`md` 케이스에서 본문을 `ArtifactType.markdown` 로 발사** (§2.5.A).
4. WADS 시스템 프롬프트 자유도 확대 + `[RENDER:]` 사용 가이드 (특히 통계/분포는 기본 `md` 권장) + step → PARAMETER 예시 교체 (§2.3).
5. **검증**: 7가지 의도(통계/리포트/해석/리스트/카테고리 비교/wafer/명시적 표) 케이스 수동 확인. 같은 질의 5종 입력 시 **artifact 종류가 의도별로 달라지는지** 명시적으로 체크.

### PR1.5 — 레이아웃 레지스트리 (R-2'.B)
PR1 만으로도 `[RENDER: md]` 경로는 다양해지지만, `table`/`auto` 경로가 여전히 단조롭다면 곧장 이 PR.
1. `_render_wads_sql_html` 을 `_render_kpi` / `_render_distribution` / `_render_pivot` / `_render_trend` / `_render_table` + dispatcher 로 분리.
2. `wads_query_sql` 결과 dict 리스트 shape 자동 감지 → `storage["render_layout"]` 힌트.
3. `[RENDER: table:distribution]` 같은 콜론 override 파싱.
4. 검증: 동일 데이터의 다른 shape 입력으로 카드가 시각적으로 달라지는지.

### PR2 — wafer 차원: DF_WADS_WF_LIST 도구 (R-4 (0))
- `wads_query_wf_list` 단독.
- `OPER_PARA LIKE '{category}_{parameter}%'` 합성 로직 + group_by 옵션.
- 시스템 프롬프트에 "wafer 수 / GROUPKEY 질의는 wf_list 도구 사용" 가이드.

### PR3 — 외부 데이터: wiki lookup (R-4 (1))
- `wads_lookup_param_context` 단독.
- 시스템 프롬프트에 "해석성 질의에만 1회 호출" 가이드.
- 평가: "EASY가 뭐야" / "PT1H vs PT1C 차이" 응답에 wiki 정의가 인용되는지.

### PR4 — 외부 데이터: fail_history + yield (R-4 (2),(3))
- 두 도구를 함께 (둘 다 "원인/맥락" 질의에 같이 쓰이는 경우 많음).
- TASK SCOPE 가드: "lookup 도구는 합쳐서 최대 2회 호출".

### PR5 (선택) — 합성 LLM 단계 (§2.6) + chart artifact (§2.5.C)
- 자유 답변 품질 정량 평가 후 도입 결정.
- distribution/trend layout 에 한해 matplotlib PNG → `ArtifactType.image` 발사 (`yield_viz.py` 패턴 재활용).

---

## 7. 검증 시나리오

| # | 사용자 입력 | 기대 동작 |
|---|------------|----------|
| 1 | "4SS 최근 3일치 검출 통계 알려줘" | wads_query_data → 본문: 일평균 / CATEGORY / PARAMETER / LOTCD 분포 자연어 인용. `[RENDER: md]` → markdown artifact (오른쪽 패널에 본문). |
| 2 | "4SS EASY 리포트 보여줘" | wads_get_html_report(parameter="EASY") → 본문 1-2문장. `[RENDER: report]` → HTML artifact 1개. |
| 3 | "왜 5NA TWT가 늘었지?" | wads_query_sql + wads_lookup_param_context + wads_lookup_related_failures → 본문 단락형 해석. `[RENDER: md]`. |
| 4 | "지난주 검출된 wafer 몇 개?" | wads_query_wf_list → 본문에 총 N개 + LOTCD/CATEGORY 분포. `[RENDER: md]`. 검출 0건이면 `[RENDER: text]` (artifact 0개). |
| 5 | "PARAMETER별 건수 집계 표로 보여줘" | wads_query_sql(group by parameter) → `[RENDER: table:distribution]` → **가로 막대 레이아웃** (현재의 일반 표 ❌). |
| 6 | "PT1H와 PT1C 어느 쪽이 더 많아?" (CATEGORY 비교) | wads_query_data(category=["PT1H_TEST","PT1C_TEST"]) → 본문에 비율/건수 인용. `[RENDER: md]`. |
| 7 | "5NA 4SS 어떤 GROUPKEY가 검출됐어?" | wads_query_wf_list → GROUPKEY 리스트 + LOTCD 분포. `[RENDER: md]`. |
| 8 | "4SS 일자별 검출 추이" | wads_query_sql(group by end_tm) → `[RENDER: table:trend]` → **시계열 layout** (PR5 에선 image artifact). |
| 9 | "현재 5NA EASY 건수만 딱 알려줘" | wads_query_sql(COUNT) → 단일 행 1숫자 → `[RENDER: table:kpi]` → **큰 숫자 카드 1개**. |

---

## 8. 기존 계획과의 관계

`peaceful-wobbling-map.md` (text2sql 전환 플랜)은 **이미 적용된 상태**로 가정. 본 플랜은 그 위에 응답 자유도를 얹는 후속 작업이다.
- `wads_query_sql` 자체는 그대로 사용 (이미 ContextVar 패턴 적용됨).
- `_render_wads_sql_html` 도 그대로 — `[RENDER: auto|table]` 일 때만 호출되도록 분기만 추가.
- `_SAFETY_RULES` 는 본 플랜에서도 수정하지 않음 (이전 D-1 유지).
