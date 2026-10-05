# Hermes-inspired Yield Harness Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (when delegation is chosen) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 도메인 기능을 빠짐없이 활용하면서, 필요한 조사를 이어 가고 근거 있는 최종 답변을 제공하는 하네스로 업그레이드한다.

**Architecture:** 기존 LangGraph 하네스·FastAPI·React·Mongo/GridFS를 유지한다. 공통 도구 실행 경로, 필요한 순간에 읽는 도메인 스킬, 같은 엔진의 제한된 전문 조사, 전체 결과 참조를 중심으로 개선한다. Hermes 엔진을 설치하거나 기존 supervisor를 새 하네스 안에 중첩하지 않는다.

**Tech Stack:** Python, Pydantic, LangGraph/LangChain, FastAPI/SSE, Oracle, OpenSearch, Mongo/GridFS, Docker Python runtime, React/TypeScript/Vite, pytest, browser E2E.

## Global Constraints

- 기준 설계: [설계 문서](../specs/2026-09-19-hermes-inspired-harness-upgrade-design.md).
- 애플리케이션 수정은 사용자가 구현을 요청한 다음 시작한다. 이 파일 작성 자체는 구현 완료가 아니다.
- `08-YieldAgent/AGENTS.md`와 루트 AGENTS의 의미 해석 하드코딩 금지를 지킨다.
- 현재 모델 OpenRouter `z-ai/glm-5.3-flash`와 공급자를 유지한다. 총 토큰 120,000·모델 40회·도구 24회 제한을 오류 회피 목적으로 상향하지 않는다.
- 자동 조사 범위는 이력·원문·맵까지다. 사용자가 최종 답변 포함 활성 300초를 승인했다. 이 상한으로 구현·검증한다.
- 기존 원본·산출물·계보·소유권·epoch·취소·중복 요청 방지를 유지한다. 원본의 preview를 전체 자료로 계산하지 않는다.
- 실제 DB/LLM/도구/브라우저를 확인하지 않고 전체 완료를 선언하지 않는다. 승인된 실제 자료 전송 범위만 사용하고 테스트 코드에 자격증명을 넣지 않는다.
- 이미 많은 파일이 수정/미추적 상태다. 작업 시작 시 현재 diff를 보존하고 파일/덩어리 단위로 관리한다. 저장소 전체 초기화·전체 일괄 staging·기존 수정 덮어쓰기를 하지 않는다.
- 작업 중 기존 서버·세션을 중지하거나 설정을 바꾸기 전에 실행 상태와 적용 단계를 확인한다. 계획 작성 중에는 서버를 건드리지 않는다.

## 1. 단계와 의존성

| 단계 | 작업 | 배포 가능한 산출물 | 다음 단계 조건 |
|---|---|---|---|
| A. 기준/결과 정확성 | 1–4 | 실제 증거와 연결된 평가, 표/원문 보존, 안전한 오류 | 기존 호환 검사와 새 회귀 통과 |
| B. 도메인 기능 복원 | 5–8 | 웨이퍼·WADS·맵·이력·보고서 도구 | 함수/DB 수치·산출물 대조 |
| C. 조사 엔진 | 9–12 | 스킬·맥락·종료 판단·전문 조사 | 중간 설명 종료/예산/위임 경계 통과 |
| D. 대화/운영 | 13–14 | 정확한 진행 상태·이력·취소·복구 | 브라우저와 재시작 경계 통과 |
| E. 실제 비교/전환 | 15 | 동일 모델/자료 비교와 단계적 적용 | 필수 시나리오·정확성·운영 검사 통과 |

하나의 대규모 교체로 진행하지 않는다. A/B 단계의 호환 개선은 독립 검토할 수 있다. C의 선택적 전문 조사는 직접 도구 경로가 검증된 뒤 붙인다. 병렬 위임으로 느린 기본 조회를 숨기지 않는다.

## 2. 파일과 책임

아래 경로는 저장소 루트 기준이다. `H = 08-YieldAgent/harness`, `F = 08-YieldAgent/yield_frontend` 표기는 표 안에서만 쓴다.

| 파일 | 처리 | 책임 |
|---|---|---|
| `H/types.py`, `H/tools/registry.py` | 수정 | 결과/표/완료 판단/도구 권한·가용성 계약 |
| `H/store.py`, `H/tools/python_tools.py`, `H/runtime/worker.py` | 수정 | 여러 표 보존·원본 읽기·Python 입출력 |
| `H/tools/document_tools.py` | 추가 | 저장 원문·문서의 페이지 읽기 |
| `H/domain_metrics.py` | 추가 | 출처에 근거한 지표 정의·수치 의미·전체성 검사 |
| `H/tools/yield_tools.py`, `yield_db.py`, `yield_viz.py` | 수정 | 기간/웨이퍼 수율·산점도·이상감지 연결 |
| `H/tools/wads_tools.py`, `wads_tools.py` | 수정 | 탐색·고유 보고서·원문·웨이퍼 연결 |
| `H/tools/analysis_tools.py`, `map_agent.py` | 수정 | 기간/대상 맵·평균·차이·영역 수치, 검색/관계/그룹/서비스 |
| `H/tools/lot_tools.py`, `lot_history_agent.py` | 수정 | 기존 LOT 분류·HTML 재사용 |
| `H/report.py`, `H/tools/artifact_tools.py`, `ppt_builder.py` | 추가/수정 | 여러 출처의 순서 있는 문서 구성, 분석 상태 보존 |
| `H/skills.py`, `H/skills/*/SKILL.md` | 추가 | 짧은 스킬 목록·본문·참고 자료 로딩 |
| `H/instructions.py`, `H/instructions/analysis.md` | 수정 | 짧은 공통 지침, 도메인 지침 이동 |
| `H/context.py`, `H/executor.py`, `H/nodes.py` | 수정 | 맥락 조립·계측·예약·루프·완료 검토 |
| `H/completion.py`, `H/graph.py`, `H/delegation.py` | 수정 | 근거 정합성·다음 행동·같은 엔진의 전문 조사 |
| `H/control.py`, `H/router.py`, `H/mcp_server.py`, `agent_server.py` | 수정 | 상태/이력/복구/기존 API 호환 |
| `user_memory.py`, `fail_history_tools.py`, `wiki_queue.py`, `wiki_summarizer.py` | 필요 부분만 수정 | 기존 기억/검색을 공통 예산·출처 계약에 연결 |
| `F/src/types.ts`, `App.tsx`, `components/AgentPlan.tsx`, `components/Artifacts.tsx`, `lib/stream.ts` | 수정 | 실제 상태·복원·중지·표 구분 표시 |
| `H/tests/*`, `H/evals/*`, `F/tests/*` | 추가/수정 | 수치·원문·사용자 시나리오와 운영 회귀 |

## 3. 공통 인터페이스 설계

다음은 구현 계약이다. 전체 애플리케이션 코드의 복사본이 아니며, 실제 구현은 각 작업의 재현 테스트와 함께 작성한다.

```python
# harness/types.py — 새 계약. 기존 필드는 읽기 호환을 유지한다.
class ResultTable(Contract):
    table_id: str
    title: str
    rows: list[dict]
    units: dict[str, str] = Field(default_factory=dict)
    complete: bool
    missing_reason: str | None = None

class TableRef(Contract):
    table_id: str
    title: str
    columns: list[str]
    total_rows: int
    preview_rows: list[dict]
    units: dict[str, str] = Field(default_factory=dict)
    complete: bool
    missing_reason: str | None = None
    data_ref: str

class CompletionReview(Contract):
    action: Literal['finish', 'continue', 'ask_user']
    complete: bool = False
    issues: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list, max_length=10)

# ToolResult에 tables: list[ResultTable]을 추가한다.
# 기존 rows 결과는 단일 table_id='default'로 정규화한다.
# rows와 tables의 동시 지정은 금지한다. 원본은 한 번 저장하고 preview는 참조용이다.
```

도구 이름을 모델이 선택하는 것은 허용한다. 위 enum과 구조 검사는 API 계약이며 자연어 문구를 enum에 맞춰 if/else로 라우팅하는 규칙을 추가한다는 뜻이 아니다.

원본 API와 Python 계약:

```python
await store.rows(principal_id, session_id, result_id, table_id=None)
await store.read_result(principal_id, session_id, result_id,
                        table_id=None, offset=0, limit=50, columns=None)
# 다중 표에 table_id가 없으면 후보 table_id를 포함한 입력 오류.
# 기존 단일 표는 생략 가능.

# 격리 Python 입력:
tables[result_id][table_id]  # 항상 표별 DataFrame
datasets[result_id]         # 단일 표 결과만 제공하는 기존 호환 변수
emit_table(frame, name='weekly_summary')
# name 생략 시 table_1, table_2…를 부여. 같은 이름 중복은 오류.
```

## Task 1. baseline과 기능별 검증 기준 고정

**Files:** 수정 `08-YieldAgent/harness/evals/cases.json`, `run.py`, `run_live.py`; 추가 `08-YieldAgent/harness/evals/capabilities.json`, `assertions.py`; 테스트 `08-YieldAgent/harness/tests/test_live_eval.py`.

**Interfaces:** `evaluate_case(case: dict, run: dict) -> dict`가 상태·필수 성공 도구·수치·산출물 조건과 실패 이유를 반환한다. `capture_build_identity() -> dict`는 코드/설정/도구/스킬의 비밀값 없는 지문을 반환한다.

- [ ] 기존 audit A1–A6/C1–C8/D1–D4를 case ID와 연결한다. 기능별 기존 함수·등록 도구·가용성·검증 case를 `capabilities.json`에 기록한다. 이 파일은 평가용이며 runtime 라우터가 읽지 않는다.
- [ ] `test_budget_partial_cannot_pass_normal_case`, `test_failed_required_tool_cannot_pass`, `test_unrelated_evidence_cannot_approve_release`를 추가한다. 실패한 도구가 호출 목록에 있어도 정상 case가 통과하면 안 된다.
- [ ] 실제 평가 기록에 run ID, 코드 SHA+dirty hash, 모델 설정, 원본 checksum, 산출물 hash를 저장한다. `.env` 내용·키는 fingerprint 입력에서 제외한다.
- [ ] 수정 전 모델/자료 조건으로 기준 기록을 보관한다. 실패는 baseline 실패로 기록하고 성공으로 보정하지 않는다.
- [ ] 실행: `PYTHONPATH=08-YieldAgent .venv/bin/pytest 08-YieldAgent/harness/tests/test_live_eval.py -q`. 새 부정 검사는 수정 전 FAIL, 수정 후 PASS여야 한다.

평가 핵심 조건:

```python
normal_pass = (
    run['status'] == 'completed'
    and all(required_tool_successes)
    and all(numeric_checks)
    and all(artifact_checks)
)
# 장애 주입 case는 별도 expected_outcome으로 판단한다.
# 모든 partial을 일괄 실패/성공으로 처리하지 않는다.
```

## Task 2. 서로 다른 표와 원본 전체성 보존

**Files:** 수정 `08-YieldAgent/harness/types.py`, `tools/registry.py`, `store.py`, `tools/python_tools.py`, `runtime/worker.py`, `completion.py`, `router.py`; 테스트 `tests/test_store.py`, `test_python_runtime.py`, `test_completion.py`, `test_mcp_contract.py`.

**Interfaces:** 위 `ResultTable`, `TableRef`, `rows(..., table_id=...)`; `ToolObservation.schema_version='harness-observation/v2'`, `tables: list[TableRef]`. 기존 v1은 읽을 때 단일 표로 정규화한다.

- [ ] 서로 다른 열의 두 `emit_table` 결과가 각각 이름·행 수·schema를 유지하는 회귀를 작성한다. 단일 표/과거 v1 JSON 읽기도 함께 검사한다.
- [ ] GridFS publication의 중복·취소 보장을 유지하며 각 표를 별도 원본으로 저장한다. `truncated`는 화면 preview 생략으로 유지하고 `complete`는 조회 모집단 완전성을 표현한다.
- [ ] `read_result`, Python 입력, 결과 HTTP/MCP 응답, fallback 표 표시를 동일 계약으로 바꾼다. 다중 표에서 묵시적으로 첫 표만 선택하거나 합치지 않는다.
- [ ] `run_python` 결과가 불완전한 입력을 사용하면 원본 계보와 제한이 남는지 검사한다. 인쇄한 문자열로 전체성을 덮어쓸 수 없어야 한다.
- [ ] 위 네 테스트 파일을 실행하고 실제 Docker에서 두 표와 그림을 출력해 저장·재조회한다.

```python
assert [t.table_id for t in observation.tables] == ['weekly', 'wafer']
assert observation.tables[0].columns == ['week', 'reports']
assert observation.tables[1].columns == ['lot', 'wafers']
assert await store.rows(owner, session, result_id, table_id='wafer') == [{'lot': 'fixture', 'wafers': 7}]
```

## Task 3. 모델의 원본 문서 접근 경로

**Files:** 추가 `08-YieldAgent/harness/tools/document_tools.py`; 수정 `tools/registry.py`, `tools/artifact_tools.py`, `fail_history_tools.py`; 테스트 추가 `08-YieldAgent/harness/tests/test_document_tools.py`.

**Interfaces:** `read_artifact_text(artifact_id, offset=0, limit=4000)`와 `get_history_document(doc_id)` 도구. 문서 반환은 원본 ID, 전체 길이, 페이지, 다음 offset, scope/provenance를 포함한 `ToolResult`다.

- [ ] WADS HTML 본문과 200자 이후에 정답이 있는 불량 문서를 테스트한다. 다른 세션 artifact, 임의 URL/경로는 거절하는 검사를 추가한다.
- [ ] 저장 artifact만 읽어 text/html을 텍스트로 투영한다. 문단/표 제목을 유지하고 원문은 변경하지 않는다. 바이너리는 처리 가능한 형식/다운로드 참조를 반환한다.
- [ ] `_fetch_results_by_doc_ids`를 재사용해 검색 결과 ID로 전체 본문을 읽는다. 짧은 검색 snippet과 전체 문서가 다른 반환 경로임을 도구 설명에 적는다.
- [ ] 전체 본문을 매번 프롬프트에 넣지 않고 모델이 필요한 페이지를 선택하게 한다.
- [ ] `test_document_tools.py` 실행 후 실제 WADS 하나와 불량 문서 하나를 원문과 대조한다. 같은 자료가 자체 하네스와 MCP에서 동일 출처로 읽혀야 한다.

## Task 4. 오류와 도구 가용성 계약

**Files:** 수정 `08-YieldAgent/harness/runtime/domain_process.py`, `executor.py`, `tools/registry.py`, `tools/analysis_tools.py`, `evals/preflight.py`; 테스트 `test_domain_process.py`, `test_provider_errors.py`, `test_tool_parity.py`.

**Interfaces:** `ToolSpec`에 `effect: Literal['read','artifact','persistent_write']`, `availability: Callable[[], dict] | None` 추가. 가용성 결과는 `{'available': bool, 'reason': str}`. 기존 `read_only` 동작은 마이그레이션 중 호환한다.

- [ ] DB 입력 오류·인증·연결·공급자 429/5xx·마이닝 미설정을 서로 구분하는 회귀를 작성한다.
- [ ] 오류를 예외명 한 단어로 축약하지 않고 code, safe_message, retryable, source를 보존한다. 비밀값과 연결 문자열을 마스킹한다.
- [ ] 가용하지 않은 도구의 이유를 목록에 나타내고 실행에서도 다시 검사한다. 미연결 mining을 빈 성공 자료로 만들지 않는다.
- [ ] `run_python`은 네트워크 없는 격리 artifact 작업으로 분류한다. `persistent_write`와 같은 것으로 취급해 자식 계산을 막지 않는다.
- [ ] 지정 테스트를 실행한다. preflight는 Oracle·Mongo·Docker·LLM·검색·선택 서비스의 준비 상태를 따로 출력해야 한다.

## Task 5. 수율·웨이퍼 조회와 지표 의미 복원

**Files:** 수정 `08-YieldAgent/harness/tools/yield_tools.py`, `08-YieldAgent/yield_db.py`, `yield_viz.py`; 추가 `08-YieldAgent/harness/domain_metrics.py`, `tests/test_wafer_yield.py`, `tests/test_domain_metrics.py`.

**Interfaces:** 도구 `inspect_yield_coverage`, `query_wafer_yield`, `analyze_yield`, `render_yield_scatter`. 기존 `query_yield` 유지. 공통 결과는 `ToolResult`와 표별 metric/unit/complete metadata다.

- [ ] 최신 데이터 조회, 기간별 동률 최저, 빈 기간, 재측정, 10,000행을 넘는 모집단에서 preview 밖 최저값을 찾는 테스트를 만든다.
- [ ] 먼저 실제 컬럼·식별자·기존 metric 계산을 대조한다. `_fetch_wafer_scatter`의 제한 있는 시각화용 결과를 전체 순위 입력으로 재사용하지 않는다.
- [ ] 조회는 bound parameter와 명시된 정렬/기간 경계로 구현한다. 순위는 DB 전체 집계 또는 전체 저장 자료에서 계산한다. 불완전할 경우 전체 최저라고 반환하지 않는다.
- [ ] PT1H 전체 A-bin pass, 파라미터 값, GMS%를 다른 metric으로 설명한다. 실제 DB 재측정 정책을 확인할 수 없으면 도구에 `measurement_policy` 선택을 노출하고 사용자에게 필요한 선택만 묻는다.
- [ ] `_detect_anomalies`와 `_build_scatter_html`의 계산/표시 자산을 사용한다. 방향·분모·표본 수·임계값을 결과에 적고 임계 문구가 실제 계산과 일치하게 한다.
- [ ] 신규 두 테스트와 기존 domain 테스트 실행 후 실제 4SS의 기간 집계와 웨이퍼 집합을 독립 SQL로 대조한다.

```python
# 전체 순위의 검사 대상. natural-language 문구가 아닌 수치 계약이다.
assert ranked.table_id == 'wafer_ranking'
assert {r['wafer_id'] for r in ranked.rows if r['rank'] == 1} == expected_tied_ids
assert ranked.complete is True
assert missing_period['value'] is None
```

## Task 6. WADS 탐색·통계·원문·웨이퍼 연결

**Files:** 수정 `08-YieldAgent/harness/tools/wads_tools.py`, `tools/artifact_tools.py`, `08-YieldAgent/wads_tools.py`; 추가 `08-YieldAgent/harness/tests/test_wads_coverage.py`.

**Interfaces:** 도구 `inspect_wads_coverage`, 기존 `query_wads`, `get_wads_report`. 보고서 키는 기존 `_report_key`와 같은 제품/검사/항목/발행 시각 기준으로 생성하며 model에게 불투명 ID로 제공한다.

- [ ] 제품/기간 미지정 탐색, 같은 날짜 여러 발행 시각, 1보고서 여러 웨이퍼, GROUPKEY 조인 누락을 테스트한다.
- [ ] `_wads_join_coverage`, `_wafer_groups_for_reports`의 기존 로직을 조회 결과에 연결한다. `reports`, `report_wafers`, `join_coverage`를 별도 표로 저장한다.
- [ ] 제품이 없다고 즉시 질문하지 않고 목록/최신 시점을 조회할 수 있게 한다. 광범위 원본 조회는 페이지/기간 제한을 명시한다.
- [ ] 원문 report ID를 통해 정확한 문서를 읽고 해당 LOT/WF와 연결한다. 통계는 보고서 고유 건수와 연결 웨이퍼 수를 분리한다.
- [ ] `test_wads_coverage.py`를 실행하고 실제 2026-08 4SS 자료의 독립 집계와 비교한다. 이전 대화의 129/884 같은 숫자를 예상값으로 고정하지 않는다.

## Task 7. 맵·LOT·불량이력·관련 공정·마이닝

**Files:** 수정 `08-YieldAgent/harness/tools/analysis_tools.py`, `tools/lot_tools.py`, `08-YieldAgent/map_agent.py`, `yield_viz.py`, `lot_history_agent.py`, `fail_history_tools.py`; 추가 `08-YieldAgent/harness/tests/test_domain_coverage.py`.

**Interfaces:** 확장 `render_wafer_map`, 신규 `compare_wafer_maps`; 기존 `query_lot_history`, `search_fail_history`, `query_relation`, `query_good_bad_groups`, `analyze_mining` 유지. 입력에 임의 자연어 SQL 분기를 추가하지 않는다.

- [ ] 제품/기간 맵, 특정 bin, 평균 pass, 두 기간 차이/영역 열화, 3개 이상 기간 보존을 테스트한다. 이전의 최근 2기간 제한을 결과 범위에 숨기지 않는다.
- [ ] 원본 자료 계산과 렌더링을 분리해 수치 표+이미지를 반환한다. 필요한 기존 순수 계산만 추출하고 map agent 전체 계획 흐름은 감싸지 않는다.
- [ ] LOT 5개 테이블과 기존 `_risk_level`, `_render_lot_history_html`을 연결한다. 서로 다른 테이블을 한 통계 모집단으로 합치지 않는다.
- [ ] BM25와 기존 벡터/위키 읽기를 선택 가능한 검색 기능으로 제공한다. 임베딩 공급자·차원·인덱스 호환성을 preflight에서 확인하고 미설정일 때 검색 모드를 명시한다. 본문 읽기는 Task 3을 사용한다.
- [ ] 관련 공정/양불 그룹은 기존 결과와 집합 일치를 검사하고 상관계수·인과관계로 확대 해석하지 않는다.
- [ ] 마이닝 API가 제공되면 실제 schema·인증·응답·오류·GINI 렌더 계약을 확인한다. 없으면 `unavailable` 결과와 별도 미완료 항목을 남긴다. 새로운 마이닝 알고리즘/더미 수치로 대체하지 않는다.
- [ ] `test_domain_coverage.py`, 기존 domain/tool parity 테스트 후 각 실제 연결 가능한 도메인에서 1개 이상 정상·빈 결과·오류 시나리오를 검증한다.

## Task 8. 선택한 결과가 빠지지 않는 보고서

**Files:** 추가 `08-YieldAgent/harness/report.py`; 수정 `tools/artifact_tools.py`, `08-YieldAgent/ppt_builder.py`; 추가 `08-YieldAgent/harness/tests/test_report_coverage.py`.

**Interfaces:** `build_report_sections(observations: list[ToolObservation], tables: dict, artifacts: dict) -> list[dict]`; section은 `kind`, `title`, `result_ids`, `table_ids`, `content`, `analysis_status`를 가진다. `analysis_status`는 `not_run|no_findings|findings|partial`이다.

- [ ] 제품 A 수율+계산표+제품 B 수율+WADS/이력/맵을 넣어 순서·내용·출처가 보존되는 테스트를 작성한다.
- [ ] `state.update` 누적 대신 ordered sections를 렌더러에 전달한다. 지원하지 못한 자료를 출처만 적고 처리 완료로 표시하지 않는다.
- [ ] 분석 미실행과 이상 없음이 다른 슬라이드 내용이 되게 한다. 수치·단위·기간·가설·한계가 원본과 일치해야 한다.
- [ ] PPTX에서 텍스트·표 값을 다시 읽어 기대 결과와 비교한다. 실제 슬라이드를 이미지로 렌더링해 잘림·누락도 확인한다.
- [ ] `test_report_coverage.py`와 실제 복합 보고서 검증을 수행한다. 산출물 파일 존재만으로 합격시키지 않는다.

```python
assert unanalyzed_section['analysis_status'] == 'not_run'
assert ordered_source_ids == requested_source_ids
assert rendered_source_ids == actual_used_source_ids
assert first_product_table in extracted_tables and second_product_table in extracted_tables
```

## Task 9. 필요한 때만 읽는 도메인 스킬

**Files:** 추가 `08-YieldAgent/harness/skills.py`, `skills/yield-analysis/SKILL.md`, `skills/wads-investigation/SKILL.md`, `skills/wafer-map-analysis/SKILL.md`, `skills/failure-investigation/SKILL.md`, `skills/report-writing/SKILL.md`; 수정 `instructions.py`, `instructions/analysis.md`, `nodes.py`, `types.py`; 추가 `tests/test_skills.py`.

**Interfaces:** `SkillCatalog.list() -> list[dict]`, `SkillCatalog.read(name: str, resource: str | None = None) -> dict`. 반환은 name/description/version/hash/content. 모델 제어 도구는 `list_skills`, `read_skill`이다.

- [ ] catalog에는 본문이 없고, 읽은 스킬만 맥락에 포함되며, 참고 자료 경로가 해당 스킬 밖을 읽지 않는 테스트를 만든다.
- [ ] 기존 프롬프트·업무 상수에서 도메인 의미와 절차를 옮긴다. 실패 문장별 예시를 추가하지 않는다.
- [ ] 스킬 선택은 모델이 한다. 모든 질문에서 스킬 5개나 child를 강제 로딩하지 않는다.
- [ ] 실행 중 스킬 버전을 고정하고 다음 run부터 변경을 적용한다. 상위 공통 지침과 사용자 정정을 스킬이 덮어쓰지 못하게 한다.
- [ ] `test_skills.py` 실행 후 단순 수율과 복합 조사에서 실제 로딩 목록·입력 크기를 비교한다.

## Task 10. 맥락 조립·토큰 항목 계측·기억 연결

**Files:** 수정 `08-YieldAgent/harness/context.py`, `executor.py`, `nodes.py`, `control.py`, `08-YieldAgent/user_memory.py`, `wiki_queue.py`, `wiki_summarizer.py`; 테스트 `test_context.py`, `test_context_budget.py`, `test_token_accounting.py`; 추가 `tests/test_memory_bridge.py`.

**Interfaces:** `InputSection(name: str, estimated_tokens: int)`을 계측 결과로만 사용한다. 기존 `model_usage`에 `input_sections`, provider usage 상세를 추가한다. 기억 갱신의 모델 호출도 `ExecutionContext.model_call(..., purpose='memory')`를 거친다.

- [ ] 동일 result가 과거 tool message·선택 근거·worklog에서 본문 3번으로 중복 삽입되지 않는지 검사한다.
- [ ] 공통 지침/스킬/schema/대화/프로필/근거/기타를 조립 지점에서 계측한다. 항목 추정 합계와 실제 공급자 전체 입력량을 따로 남긴다.
- [ ] 압축은 tool call/result 묶음, 최신 사용자 정정, 결정된 metric, 원본 ID를 보존한다. 압축이 실패하거나 더 커지면 기존 맥락을 유지한다.
- [ ] 기존 프로필은 principal에 맞춰 읽고 현재 지시보다 낮은 우선순위로 전달한다. 구체적 제품/날짜를 영구 선호로 저장하지 않는다.
- [ ] 기존 피드백/위키 갱신은 한 run에 중복 수행하지 않게 키를 부여하고 공통 호출 예산·취소 정책에 연결한다. 임의 사실이나 실패 답변을 학습 자료로 승격하지 않는다. 기본 설정의 기존 갱신 정책을 존중하며 스킬 자동 수정은 추가하지 않는다.
- [ ] 네 테스트 파일과 고정 30턴 대화 평가를 실행한다. 비용 보고에서 reasoning이 completion에 포함되면 한 번만 합산한다.

## Task 11. 조기 종료와 복합 scope 검증 수정

**Files:** 수정 `08-YieldAgent/harness/types.py`, `nodes.py`, `completion.py`, `graph.py`, `instructions/analysis.md`; 테스트 `test_loop.py`, `test_completion.py`, `test_review_regressions.py`, `test_simplification.py`.

**Interfaces:** `CompletionReview`를 기존 `Verification` 대신 새 run에서 사용한다. 기존 결과 JSON의 accepted/complete는 과거 기록 읽기 용도로 유지한다. `continue`는 검토 피드백을 넣어 model 노드로, `ask_user`는 model이 질문을 구성하도록, `finish`는 terminal로 보낸다.

- [ ] ‘도구를 확인하겠습니다’ 역할의 여러 표현/언어를 가상 후보로 넣고 continue verdict 뒤 실제 도구 실행까지 이어지는 테스트를 작성한다. 인사·일반 설명·정당한 부분 답변도 검사한다.
- [ ] 수율 weekly+WADS start/end, 서로 다른 제품/기간 비교가 정상적으로 검증되며 실제 범위 불일치·다른 소유자 result는 거절되는지 검사한다.
- [ ] flat scope의 모든 키를 모든 source에 요구하는 검사를 제거한다. 결과별 입력·기간 메타데이터·계보를 보존하고 내용 검토에 출처별로 제공한다.
- [ ] 후보/검토 피드백/근거를 중복 넣지 않는다. continue 가능한 자원이 없으면 최종 정리 경로로 전환한다. 의미 해석용 문자열 감지기는 만들지 않는다.
- [ ] 구조화 표와 자연어 결론이 함께 전달되며, 미검증 수치를 정답으로 보이지 않게 한다.
- [ ] 지정 네 테스트 후 실제 수율+WADS 복합 요청을 실행한다. 예정 행동만 남기고 종료하는지와 실제 답변의 수치·요청 충족을 대조한다.

## Task 12. 마무리 자원 예약과 같은 엔진의 전문 조사

**Files:** 수정 `08-YieldAgent/harness/config.py`, `executor.py`, `store.py`, `nodes.py`, `delegation.py`, `types.py`; 테스트 `test_delegation.py`, `test_token_accounting.py`, `test_recovery.py`, `test_provider_errors.py`.

**Interfaces:** 기존 `DelegateInput`에 `skill_names: list[str]` 추가. 반환은 `summary`, `status`, `result_ids`, `limitations`, `question`을 포함한 결과. 도구 호환 이름 `delegate_readonly`는 유지하되 외부 데이터 읽기+격리 계산만 허용한다.

- [ ] 자식이 격리 Python을 사용하고 persistent_write는 사용할 수 없으며, 부모 대화 전체가 복제되지 않는지 검사한다.
- [ ] `question`/선택한 스킬/제약/result 참조만 새로운 child 대화에 넣는다. 엔진·모델 factory·executor·저장소 계약은 부모와 같다.
- [ ] 초기 제한은 깊이 1·자식 최대 2개·도메인 동시 실행 2개를 유지한다. 복구 시 완료한 도구 결과를 재사용하고 예산을 이중 청구하지 않는다.
- [ ] 조사 호출 승인 전에 현재 근거 크기의 답변+검토 입력/출력/호출 수를 원자적으로 예약한다. 시간도 답변·검토 단계가 쓸 수 있도록 남긴다. 공급자 오류 재시도가 이 예약을 가져가지 못하게 한다.
- [ ] 초기 시간 예약은 각 마무리 호출의 유효 timeout 합을 사용하고, 전체 활성 시간보다 길면 호출 timeout을 남은 시간 안으로 줄인다. 모델 시작 전에 해당 deadline을 적용한다. 예산 부족은 조사를 중단시키며 총 시간을 늘리지 않는다.
- [ ] root 취소·조건 변경·lease 상실이 child/도메인 프로세스/Python 컨테이너까지 전달되는지 확인한다. child의 partial/질문을 부모가 합쳐 다음 행동을 결정한다.
- [ ] 네 테스트 후 실제 동일 복합 과제를 직접 도구 실행과 전문 위임으로 비교한다. 위임은 독립 조사에서만 사용하고 비용 감소를 사전에 보장하지 않는다.

## Task 13. 진행 상태·질문 이력·취소·새로고침

**Files:** 수정 `08-YieldAgent/harness/executor.py`, `router.py`, `control.py`, `08-YieldAgent/agent_server.py`, `models.py`, `yield_frontend/src/types.ts`, `App.tsx`, `components/AgentPlan.tsx`, `components/Artifacts.tsx`, `lib/stream.ts`; 테스트 `harness/tests/test_api.py`, `test_progress.py`, `yield_frontend/tests/stream.test.mjs`; 추가 `yield_frontend/tests/harness.e2e.spec.ts`.

**Interfaces:** status 이벤트에 invocation_id, parent_invocation_id, state, elapsed를 보존한다. `state`는 running/success/partial/empty/error/cancelled. history snapshot에 `through_sequence`와 최신 run 정보를 반환한다. run 목록은 cursor 페이지+최신 우선 조회를 지원한다.

- [ ] 시작/오류를 초록 완료로 표시하지 않는 검사, 보충 질문/답 이력, 대기 중 취소, 101번째 run, 완료 직전 새로고침을 작성한다.
- [ ] 복원 snapshot 이후 `sequence > through_sequence` 이벤트만 이어받고 UI가 event_id/run_id로 합친다. message·artifact 중복과 누락을 함께 검사한다.
- [ ] 산출물은 생성 순서로 안정적으로 복원한다. 표별 제목·열을 구분해 보여준다. UI 전체 디자인 변경은 하지 않는다.
- [ ] 중지는 텍스트 의미 판정이 아닌 기존 cancel API를 사용한다. 대기 상태에서도 버튼을 표시한다.
- [ ] Playwright 개발 의존성과 `test:e2e` 스크립트를 추가하고 가상 스트림 서버로 자동 회귀를 실행한다. 이후 실제 backend/LLM/DB 브라우저 시험도 별도로 한다.
- [ ] 실행: frontend 디렉터리에서 `npm test`, `npm run build`, `npm run test:e2e`; backend의 API/progress 테스트도 통과해야 한다.

## Task 14. 실행 버전·MCP 복구·호환 배포 준비

**Files:** 수정 `08-YieldAgent/harness/control.py`, `router.py`, `mcp_server.py`, `checkpoints.py`, `store.py`, `README.md`, `08-YieldAgent/AGENTS.md`, `.env.example`; 테스트 `test_recovery.py`, `test_checkpoint_ownership.py`, `test_mcp_contract.py`, `test_api.py`.

**Interfaces:** 신규 run의 `runtime_version`, `contract_version`, `skill_versions`를 저장한다. legacy session pinning은 유지한다. v1 완료 기록을 읽는 adapter는 Task 2를 사용한다.

- [ ] v1 완료 이력/v2 신규 run, legacy session, 진행 중 v1 run, 질문 대기 v1 run을 별도 fixture로 검증한다.
- [ ] 배포 전 기존 실행을 종료 상태까지 drain한다. 대기 작업은 기존 버전에서 재개/취소해야 한다. v1의 활성 checkpoint를 v2 graph에 읽히지 않는다.
- [ ] 완료된 harness 세션은 다음 run에서 검증된 history/result adapter를 통해 v2로 전환한다. 기존 checkpoint는 재사용하지 않는다. legacy session은 기존 동작을 유지한다.
- [ ] 고정 MCP 세션에서는 소유 lease가 살아 있으면 충돌을 반환하고, 죽은 실행은 fenced recovery/종료 처리한다. 서로 다른 프로세스가 같은 활성 세션을 소유하지 못하게 한다.
- [ ] 롤백 기준은 v2 자료 읽기가 가능한 A단계 호환 릴리스로 잡는다. 새 실행을 중단하고 활성 실행을 정리한 뒤 전환한다. v2 자료를 읽지 못하는 더 오래된 빌드로 무조건 되돌리지 않는다.
- [ ] 위 네 테스트와 실제 서버 재시작/고정 MCP 재접속 시험을 수행한다. 문서에서 현재/legacy 구조와 미연결 서비스를 구분한다.

## Task 15. 실제 시나리오 비교와 최종 전환

**Files:** 수정 `08-YieldAgent/harness/evals/cases.json`, `run_live.py`, `run.py`, `README.md`; 추가 `outputs/harness-upgrade-validation-2026-09-19.md` 및 실행별 JSON/산출물 증거.

**Interfaces:** 대화 case에 `turns`, 입력 대기 응답, steer/cancel/reconnect 동작, 기대 수치/범위/문서 조건을 명시한다. LLM의 문장 완전 일치를 정답으로 쓰지 않는다.

- [ ] 실제 모델/DB 실행 전에 새 evaluator의 false-positive 방지 검사를 통과시킨다.
- [ ] 독립 SQL/계산으로 기준값을 확보하고 데이터 시간 범위·모집단을 기록한다. 실제 자료의 외부 전송은 이미 승인된 범위와 대조한다.
- [ ] 핵심 네 대화(수율+원인, 최저 웨이퍼+맵, WADS 통계+원문, 비교+PPT)를 각각 3회 실행한다. 추가로 전체 도메인·빈 자료·공급자 오류·시간/토큰 제한·30턴·취소/복구를 검증한다.
- [ ] 동일 모델/생성 설정과 가능한 동일 데이터로 baseline과 비교한다. 성공률, 수치 오류, 미완료 이유, 입력/출력 토큰, 전체 시간, 반복 호출, 사용자 개입 횟수를 공개한다.
- [ ] 정상 핵심 반복 시험은 모두 요청 충족해야 한다. 수치·원문·출처 오류, 미분석을 정상으로 표시, 소유권/취소/복구 실패는 출시를 막는다. 정상 시나리오의 예산 종료를 ‘부분 성공’으로 통과시키지 않는다.
- [ ] 실제 브라우저에서 진행 안내, 보충 입력, 조건 변경, 중지, 새로고침, 링크/PPT 다운로드를 확인한다. 테스트 실패 시 해당 단계로 돌아가고 미검증 기능은 완료 표에서 제외한다.
- [ ] 검증된 새 세션부터 기본 적용한다. 모델/예산 설정을 몰래 변경해 baseline 비교 조건을 바꾸지 않는다. 마이닝이 없으면 미연결을 별도 항목으로 남긴다.

최종 보고에 포함할 표:

| 시나리오 | 모델/DB | 성공/시도 | 정확성 | 시간 | 입력/출력 토큰 | 실제 산출물 | 남은 제한 |
|---|---|---|---|---|---|---|---|
| 각 case ID | live/fixture 명시 | 측정값 | 독립 대조 결과 | 측정값 | provider 실제값/추정 분리 | run과 연결한 hash | 미완료 이유 |

## 4. 감사 항목 추적

| 감사 ID | 해결 작업 |
|---|---|
| A1 중간 설명 종료 | 11, 12, 15 |
| A2 복합 범위 오거절 | 5, 6, 11 |
| A3 미분석 PPT 정상 판정 | 8 |
| A4 PPT 결과 덮어쓰기/누락 | 2, 8 |
| A5 다중 표 병합 | 2, 13 |
| A6 원문 접근 부족 | 3, 6, 7 |
| 도메인·기억 이전 누락 | 5–10 |
| C1 질문/답 이력 | 13 |
| C2 대기 중 취소 | 12, 13 |
| C3 긴 세션 최신 run | 13 |
| C4 새로고침 완료 경계 | 13 |
| C5 고정 MCP crash | 14 |
| C6 잘못된 진행 상태 | 13 |
| C7 산출물 순서 | 8, 13 |
| C8 원인 정보 소실 | 4 |
| D1 기능 이름만 검사 | 1, 5–8, 15 |
| D2 partial/실패 도구 통과 | 1, 15 |
| D3 관계없는 검증 증거 | 1, 15 |
| D4 대화/브라우저/도메인 평가 부족 | 13–15 |

## 5. 적용하지 않을 임시 처방

- 자연어 표현·제품·날짜·로그별 조건문을 추가하지 않는다.
- 모든 전문 지침·도구 schema·과거 결과를 매번 넣지 않는다.
- 모델의 문장을 더 길게 만들거나 토큰 제한만 올려 완료 문제를 숨기지 않는다.
- 모든 기존 agent를 무조건 하위 LLM으로 실행하지 않는다.
- 정상 답변 검증을 삭제하거나 partial을 모두 성공으로 세지 않는다.
- 구조가 Hermes와 비슷해지면 답변 품질도 자동으로 같아진다고 약속하지 않는다.

## 6. 계획 검토 체크

- [ ] 조사 시간 선택과 마이닝 API 유무를 사용자 답변으로 확정한다.
- [x] 기존 코드·감사 보고서·Hermes 공식 문서와 구조를 대조했다.
- [x] 도메인 9개와 기억/화면/운영 누락을 작업에 연결했다.
- [x] 기존 자산 유지·수정·범위 밖 항목을 구분했다.
- [x] 실제 데이터·모델·브라우저 완료 기준과 롤백 경계를 포함했다.
- [x] 구현 요청과 5분 상한 승인을 받고 단계 A부터 실행 중이다.
