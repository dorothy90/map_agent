# Backend Yield-to-Binmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 `/chat/stream` 백엔드가 수율 조회, 열화 대상 선정, 정확한 wafer 순위, binmap 생성을 수행하고 요청한 범위와 수치를 검증할 수 있게 한다.

**Architecture:** LLM이 업무 의미와 도구 조합을 결정한다. 도구는 명시적인 모집단·지표·측정 정책을 받아 전체 데이터에서 계산하고, 저장된 결과 참조로 다음 도구에 정확한 측정 건을 전달한다. 자연어 키워드 라우팅이나 이 질문 전용 파이프라인을 추가하지 않는다.

**Tech Stack:** 기존 Python, Pydantic, Oracle, MongoDB 결과 저장소, 하네스 도구 registry, Matplotlib, SSE API 유지.

## Global Constraints

- 이번 요청은 계획 작성이다. 애플리케이션 구현·DB 수정·모델 변경은 실행 범위가 아니다.
- 제품 4SS, 파라미터명, 문장, 날짜를 코드 조건으로 고정하지 않는다.
- 수율 값·DB 날짜를 더 수정하지 않는다. 현재 자료는 사용자 승인으로 날짜를 이동한 기존 자료다.
- 원본 전체·출처·소유권을 보존한다. LLM preview를 전체 모집단으로 사용하지 않는다.
- 필수 조건만 확인하고 도구로 알 수 있는 정보는 조회한다. 도구 계약의 enum은 구조화된 값 검증용이며 자연어 분기용으로 사용하지 않는다.
- 새 planner나 agent는 만들지 않는다. 기존 도구와 스킬을 확장한다.

## 확인된 실패와 아직 검증할 가설

실행 ID: `5757e07f-da08-438a-ba24-e4c199a1514a`.
원문: `4SS 최근4주 수율 조회하고 열화 파라미터 중에 제일 수율안좋은 wafer 10개 binmap 보여줘`.

- 실제 8001 서버의 새 세션 `/chat/stream`에서 실행했다.
- 97.04초, 모델 8회, 도메인 도구 3회, 89,182토큰. 최종 상태 `partial / incomplete_at_budget`.
- 수율 조회 성공, WADS 보고서 86개 및 보고서-wafer 연결 584개 조회 성공.
- `query_wafer_yield`는 `pass_rate`와 `parameter`를 동시에 받아 validation 실패.
- binmap 도구 호출과 이미지 생성은 0회. 백엔드는 최신 대표 파라미터로 범위를 좁히겠다고 발표했다.
- compaction 18.167초, answer 24.771초, review 35.948초: 합계 78.886초.
- 정확히 어떤 예산 조건이 추가 추론을 차단했는지는 현재 종료 코드만으로 단정하지 않는다. `nodes.py`의 token/context/model/tool/time 분기를 구분해 기록해야 한다.

원본 근거: `outputs/backend-perf-20261004/{request.json,events.json,backend-events.json,run.json,summary.json}`.

## 결과 의미를 먼저 고정

제가 만든 수동 보고서는 정답 데이터셋이 아니다. 다음 가정을 임의로 적용했으므로 이를 그대로 백엔드 기본값으로 이식하지 않는다.

- 10개 전체 대신 항목별 10개, 총 130개를 생성했다.
- 열화를 WADS가 아닌 최신 두 주의 지표 방향 변화로 정의했다.
- 파라미터 bin 미정의 PT1C를 전체 A-bin 수율로 대체했다.

실행 계약에는 제품, 기간과 시간 기준, 열화 근거(기간 비교/선택된 WADS 결과), 순위 모집단, 수율 정의, 전체/그룹별 개수, 재측정 정책, 동률 정책을 명시한다. LLM은 원문·앞선 대화·도구 근거로 계약을 채우고, 서로 다른 결과를 만드는 중요한 의미가 남으면 한 번에 짧게 확인한다. `10개`를 근거 없이 항목별 10개로 확대하거나 전체 모집단을 대표 파라미터 하나로 축소하지 않는다.

서로 다른 단위를 가진 파라미터 원본 VALUE들을 하나의 수율 순위로 섞지 않는다. bin 미정의 항목을 전체 pass 수율로 자동 대체하지 않는다. WADS 표기 속 문자를 맵 bin으로 추정하지 않는다. 실제 `BIN_CATEGORY_MAP`과 WADS 항목명은 서로 다르므로 검사 단계·출처를 포함한 검증된 매핑이 필요하다. 매핑 근거가 없으면 그 제한을 반환한다.

## 선택한 접근

1. 프롬프트만 보강: 빠르지만 없는 계산 기능과 측정 건 연결 문제를 해결하지 못한다.
2. **기존 도구 계약·결과 참조·스킬을 보강: 권장.** 다양한 제품·기간·개수로 재사용하며 LLM의 도구 선택을 유지한다.
3. 이 요청 전용 복합 도구/새 에이전트: 호출은 줄지만 자연어 해석을 고정하기 쉽고 기존 기능을 중복하므로 제외한다.

## Task 1: 실패 재현과 계약 평가

**Files:** 새 `08-YieldAgent/harness/evals/yield_binmap.py`, 새 `08-YieldAgent/harness/tests/test_yield_binmap_contract.py`.

- [ ] 위 원문을 수정 없이 새 `/session` → `/chat/stream`으로 전송하는 평가 클라이언트를 만든다. 모델·도구 답변을 mock하거나 중간 입력을 대신 작성하지 않는다.
- [ ] 원문, 세션/run ID, 실제 모델 설정 식별자, 기간, 최초 진행 응답/첫 결과/전체 소요 시간, 도구 입력·상태, token 항목, 질문, 최종 답변·artifact ID를 저장한다. 비밀키는 기록하지 않는다.
- [ ] 평가 결과를 `completed_correct`, `clarification_required`, `partial`, `failed`, `completed_incorrect`로 구분한다. 정상적인 의미 확인은 이미지 성공과 별개로 보고한다.
- [ ] 수동 보고서 이미지를 기준 정답으로 사용하지 않는다. 백엔드가 확정한 계약에 따라 Oracle 원본을 독립 계산한 결과를 검증기로 사용한다.

## Task 2: 세 가지 지표와 전체 순위 지원

**Files:** `harness/tools/yield_tools.py`, `harness/domain_metrics.py`, `harness/tests/test_domain_metrics.py`, `harness/tests/test_domain_tools.py`.

**Contract:** `query_wafer_yield`에서 전체 A-bin pass%, 특정 bin 제외 pass%, 파라미터 원본값을 서로 배타적인 입력 구조로 표현한다. 기존 `metric=pass_rate` 호출은 유지하고 잘못된 `parameter` 조합은 계속 거부한다. 신규 특정-bin 지표에는 `target_bin`, `bin_type`, 출처가 명시된 매핑을 요구한다.

**Output:** `wafer_ranking` 표에 `measurement_id`, wafer/검사/측정 시각, 분자·분모·값·단위·순위·그룹을 저장한다. `selection` 표에는 선택된 정확한 측정 ID, cutoff와 동률 수를 저장한다. 모집단 수, 조회 완전성, 재측정 및 동률 정책을 scope에 둔다.

- [ ] 전체 pass%와 특정-bin 수율이 다른 원본으로 계산 검증을 먼저 추가한다.

```python
def test_bin_yield_is_distinct_from_overall_pass():
    from harness.domain_metrics import wafer_pass_metric
    sample = {"MAP": ["0,0,A,A", "0,1,B,B", "1,0,C,C", "1,1,A,A"]}
    assert wafer_pass_metric(sample)["value"] == 50
    assert wafer_pass_metric(sample, target_bin="B")["value"] == 75
```

- [ ] 기존 per-period 순위를 유지하되 full-range 순위를 명시적으로 선택할 수 있게 한다. `selection`에서 전체 N개와 그룹별 N개를 구분한다.
- [ ] WADS 모집단을 선택하면 소유권 확인된 `source_result_id/table_id`의 wafer 집합과 기간·검사 조건을 교차 적용한다. 미리보기 목록을 입력으로 복사하지 않는다.
- [ ] 동률·재측정·빈 맵·누락 맵·불완전 조회·순위 후보가 N개 미만인 경우를 시험한다. 같은 시간의 상충 맵은 임의 선택하지 않는다.
- [ ] 여러 파라미터를 분석해도 동일 제품/기간/검사의 맵은 한 번 조회·파싱해 재사용한다. 맵 JSON은 전체 저장하되 LLM 요약에는 넣지 않는다.

검증 명령: `PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests/test_domain_metrics.py 08-YieldAgent/harness/tests/test_domain_tools.py -q`.

## Task 3: 순위 결과에서 정확한 binmap 생성

**Files:** `harness/tools/map_tools.py`, `harness/tools/registry.py`, `map_agent.py`, `harness/tests/test_map_coverage.py`, 새 `harness/tests/test_ranked_map_lineage.py`.

**Interface:** 새 `render_ranked_wafer_maps(source_result_id, selection_table_id="selection")` 도구를 `map_tools.register()`에 등록한다. 검사·wafer·측정 건·개수는 선택 결과에서 읽고 LLM이 다시 입력하지 않는다. 결과 접근 권한과 선택 표의 출처를 검증한다.

- [ ] 순위에 사용한 보관 맵과 측정 ID만 렌더링한다. LOT 전체 재조회로 다른 날짜·재측정 건이 섞이지 않게 한다.
- [ ] 이미지와 함께 `rendered_measurements` 표를 반환한다. 순위 selection과 렌더링 measurement ID 집합·개수가 정확히 일치해야 한다.
- [ ] 고정된 bin 색/범례, wafer ID, 순위, 수율 정의·값을 표시한다. 파일명은 실행/선택별로 고유하게 만들어 동시 생성 충돌을 방지한다.
- [ ] 10개 요청에 10개만 나타나는 실제 이미지 검증, 빈 맵/일부 누락/다른 세션 참조/재측정 선택 변경 테스트를 추가한다.

## Task 4: 업무 스킬과 오류 후 재계획

**Files:** `harness/skills/yield-analysis/SKILL.md`, `harness/skills/wafer-map-analysis/SKILL.md`, `harness/skills/wads-investigation/SKILL.md`, `harness/instructions/analysis.md`, `harness/executor.py`, `harness/types.py`, `harness/tests/test_loop.py`.

- [ ] 기간 비교 열화와 WADS 검출을 서로 다른 근거로 설명한다. LLM이 원문 맥락에 맞는 근거를 선택하고 선택 이유를 scope에 남기도록 한다.
- [ ] 일반적인 절차를 안내한다: 기간/지표 확인 → 수율·열화 근거 → 전체 모집단 순위 → 저장 selection의 맵 → 완료 검증. 특정 문장에 매칭되는 예시나 분기를 추가하지 않는다.
- [ ] 팹아웃은 이 맵 도구의 `end_tm`이라는 정의를 도구 설명에 넣는다. 이를 파라미터 테이블의 `MEASURETIME_START`와 무조건 동일시하지 않는다.
- [ ] validation 오류에 실패 필드, 허용 조합, 안전한 입력 요약을 구조화해 반환한다. 의미를 바꾸는 자동 치환은 하지 않고 LLM이 수정하도록 한다.
- [ ] 잘못된 `pass_rate + parameter` 입력 뒤 올바른 명시 입력으로 회복하는 테스트를 넣는다. 같은 오류 반복은 제한하고 미완료 범위를 보고한다.

## Task 5: 결과 크기와 종료 예산 개선

**Files:** `harness/context.py`, `harness/nodes.py`, `harness/executor.py`, `harness/tests/test_context.py`, `harness/tests/test_context_budget.py`, `harness/tests/test_time_budget.py`, `harness/tests/test_token_accounting.py`.

- [ ] 종료 분기를 `token_reserve`, `context_limit`, `time_reserve`, `model_limit`, `tool_limit`, `forced`로 구분해 실제 수치와 함께 기록한다. 이번 실행이 조기에 종료된 정확한 조건을 재현한다.
- [ ] 모델에는 결과 ID, 범위, 지표, 행 수, 짧은 요약/preview, 선택 결과만 전달한다. 584개 연결행과 원본 맵은 저장소에 두고 필요한 계산 도구가 읽는다.
- [ ] 검증에 필요한 전체 데이터는 그대로 보존한다. evidence 원문·conversation·result index에 같은 정보가 중복되는지 측정하고 중복만 줄인다.
- [ ] 짧은 대화에서 큰 evidence 때문에 발생한 요약은 evidence 축소로 해결할 수 있는지 평가한다. 요약 후 실제 크기가 줄었는지 측정한다.
- [ ] 답변·검토 출력 예약량을 실제 필요한 결과 크기로 산정한다. 추가 도구 실행을 위한 여유와 완료 검증 예산을 함께 보존한다. 전체 token/time 상한을 넘기거나 리뷰를 제거하지 않는다.
- [ ] 모델이나 예산 상향은 이 단계의 기본 해결책으로 사용하지 않는다. 구조 개선 전후를 동일 설정으로 비교한다.

## Task 6: 실제 백엔드 인수 평가

**Files:** Task 1 평가 클라이언트, `harness/evals/README.md`, `harness/tests/test_completion.py`, `harness/tests/test_report_coverage.py`.

- [ ] 원문을 새 세션에서 3회 실행한다. 매회 계약·모집단·이미지 ID와 독립 DB 검증기를 대조한다. LLM 대신 수동 계산을 주입하지 않는다.
- [ ] 변형 평가: 다른 제품, 다른 기간, N=3/10, 전체/항목별 요청, WADS 연결 집합 요청, 데이터 없음, 재측정·동률, bin 매핑 미확정, binmap→cummap 후속 요청.
- [ ] 원문 해석이 불명확해 실제 확인 질문이 나오면 그 질문을 별도 결과로 보고한다. 승인되지 않은 테스트 답을 만들어 넣지 않는다.
- [ ] 성공 조건: 확정된 계약의 N개 wafer, 동일 측정 건 N개 이미지, 독립 계산과 수치 일치, 범위/단위/동률/누락 표시, 최종 `completed`. 이미지 없이 완료 또는 부분 조회로 전체 최저 주장하면 실패다.
- [ ] 수율표, 열화 근거, 선정 wafer 표, binmap, 한계를 최종 응답에 보존한다. 대형 원본 표 덤프가 결과 설명을 대신하지 않게 한다.
- [ ] 목표 성능: 예산 부족 없는 완료, 오류 회복 성공, 기준 89,182토큰 대비 감소, 현재 300초 상한 안에 완료. 이후 10회에서 중앙값/P95를 측정한다. 성공했던 비교 기준이 없으므로 97초보다 빠른 정상 완료를 사전 보장하지 않는다.

## 실행 순서와 완료 보고

1 → 2 → 3 → 4 → 5 → 6 순으로 진행한다. 각 단계는 실패 테스트 → 최소 수정 → 관련 테스트 → diff 검토로 완료한다. 기존 사용자 변경을 건드리지 않는다.

최종 보고에는 실제 백엔드 원문 응답, 성공/확인필요/실패 횟수, elapsed·model/tool/token 수, 선택 wafer와 이미지 일치 검사, 남은 제한을 포함한다. 애플리케이션 변경 완료와 실제 성능 검증 완료를 구분한다.
