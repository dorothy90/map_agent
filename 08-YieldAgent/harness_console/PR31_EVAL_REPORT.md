# PR31 평가 보고서 — wafer-set 18툴 신규 구성, 복합 한국어 질문 34건

작성: 2026-10-27 (KST) · 실행: pr9_real_dev_server (PR9_REAL_MODEL=1, HARNESS_WAFER_SET_TOOLS=1), 실제 모델·Oracle(읽기 전용)
검증: 정답은 MAP 캐시/DF_DIE_TO_WF_YLD 캐시(Python)로 독립 계산(/tmp/pr31/truth_out.txt)하여 대조. 코드 수정 없음(아래 "공개 사항" 참조).

## 1. 요약
| 구분 | 건수 | 비율(34건) |
|---|---|---|
| PASS (정답 일치) | 15 | 44% |
| PARTIAL (일부 정확/지표 오선택/가정 후 진행) | 5 | 15% |
| FAIL (답변 없음·검증 실패·오답) | 14 | 41% |

- 엄격 성공률 15/34 = **44%**, PASS+PARTIAL 20/34 = **59%** (P0 프로브 제외 33건 기준 PASS 15/33 = 45%).
- **PASS한 답은 전부 정답과 정확히 일치**했고, 숫자를 지어낸 사례는 없음. 실패의 압도적 원인은 정확성이 아니라 **예산 한계 + 도구 오류 반복**.
- 예산 한계: tool 24회 / model 40회 / 활성 300초(HARNESS_TOOL_LIMIT, HARNESS_MODEL_LIMIT, HARNESS_ACTIVE_SECONDS 기본값)에 24건 중 다수가 도달.

## 2. 질문별 결과
시간=answer 기준(초), 도구=호출수(오류수), 검증=서버 verify_answer verdict.

| ID | 질문 요약 | 판정 | 시간 | 도구(오류) | 검증/종료 | 원인·비고 |
|---|---|---|---|---|---|---|
| A1 | 4SS PT1H 10/12~18 하위 5 wafer | PARTIAL | 85.8 | 8(2) | unknown | 정답 5건(4SS5BK1.01 88.37 …)은 표에 있으나 "확인 불가"로 종료, 내부 메타 24KB 노출 |
| A2 | 4SA PT1H 10/12~18 상위 5 wafer | FAIL | 130.8 | 24(7) | models_limit | 답변 없음. 정답 4SAWJH1.21 96.74 … |
| A3 | 10월 4SS vs 4SA 평균+wafer수 | FAIL | 275.7 | 24(13) | pass(숫자 없음) | 건수(2575/1550)만 맞고 평균 계산 실패. aggregate_metrics "Duplicate measurement has conflicting values". 모델이 'yield-analysis 스킬을 활성 프로필에서 삭제했다'고 주장(미확인, 아래 이슈) |
| A4 | 4SS lot 하위 3 (10/13~19) | PASS | 188.0 | 23(6) | pass | 4SSQZ3G 90.16, 4SS5BK1 90.28, 4SSM6AC 90.77 일치 |
| A5 | 4SA 일별 수율 10/20~26 | PASS | 103.5 | 11(3) | pass | 7일 값 일치. process="4SA PT1H" 0행 후 자체 복구 |
| A6 | 오늘 lot_cd별 wafer | PASS | 140.4 | 24(5) | pass | 4SA 50/91.93, 4SS 50/89.01 일치 |
| A7 | 4SS 90% 미만 wafer 수 | PASS | 214.0 | 24(2) | pass | 354/1550=22.84% 일치 |
| B1 | 4SS 하위4 맵 | PASS | 44.9 | 3 | pass | 4SSPNEB.14 87.13 … 일치+맵 |
| B2 | 4SA 하위6 맵 | PASS | 67.3 | 4 | pass | 일치+맵 |
| B3 | 4SS 상위3 맵 | PASS | 39.7 | 4 | pass | 일치, 동점 명시 |
| B4 | 4SS cummap | FAIL | 204.1 | 24(12) | models_limit | 누적 맵은 render_view 필요(테스트 smoke guard가 차단) |
| C1 | 4SA PT1C vs PT1H 평균 | FAIL | 171.2 | 24(6) | verification_fail | DIE_LIMIT(63.4MB), reduce_maps "Stored map payload table is required". 정답 89.036 / 92.358 |
| C2 | 4SS PT1C 하위3 | PARTIAL | 88.0 | 9(1) | pass | wafer_parameter(CFTA) 값을 "수율"로 사용(정답은 MAP PT1C 87.24/87.52/87.63). 지표는 명시했으나 검증기가 통과시킴 |
| C3 | PT1H−PT1C 차이 | FAIL | 331.6 | 24(4) | models_limit | 답변 없음. 정답 4SS −0.017, 4SA −3.322 |
| D1 | CGS(U)/TWT(T) 평균+상위3 | PASS | 127.2 | 14(7) | pass | 1.078/1.078, 상위3 일치, 동점 명시 |
| D2 | 4SA fail 심볼 top3 | PARTIAL | 291.3 | 24(7) | pass | 1·2위(IGATE 1.122, IDDQ 1.117) 정확, 3위는 값(1.116)만 맞고 이름 미해결 |
| D3 | CGS/VTH cummap | FAIL | 301.9 | 24(14) | unknown | cummap 미생성(render_view 차단) |
| D4 | DIBL/IDSAT/ION cummap | FAIL | 310.5 | 24(13) | verification_fail | 맵 3장 없음. 수치(1.065/1.086/1.064)는 정답과 부합하나 ION "3~4%" 근거 없음 |
| E1 | ION 상위5 wafer | PASS | 83.0 | 9(1) | pass | 4SSWVWF.11 0.268251 … 일치 |
| E2 | VTH 하위10% vs 나머지 | PASS | 175.7 | 19(0) | pass | 92.85 vs 92.47 (35 wafer) 일치 |
| E3 | IOFF 상위3 + 맵 | FAIL | 207.7 | 24(14) | verification_fail | 선택은 정확(4SSPT71.18, 4SSLCGJ.01, 4SSPT71.10)이나 render_maps가 filter_set 표(yield_percent 없음) 거부 → 맵·수율 미제공 |
| E4 | PT1C 제품별 CFTA 평균 | PASS | 109.8 | 20(5) | pass | 0.448573 / 0.449405 일치 |
| F1 | 짝수 vs 홀수 wafer | FAIL | 227.0 | 24(14) | verification_fail | 정답 91.890(744) vs 91.913(806), 결과 없음 |
| F2 | 3의 배수 vs 나머지 | FAIL | 211.9 | 24(12) | models_limit | 정답 91.590(112) vs 91.675(238), 답변 없음 |
| F3 | 7일 구간 비교 | PASS | 125.6 | 24(5) | pass | 92.900/90.639/91.827/91.849 일치 |
| F4 | 상·하위 10% 비교 | PASS | 221.6 | 22(9) | pass | k=155, 88.235 vs 95.667 일치 |
| G1 | "수율 안 좋은 wafer 보여줘" | PARTIAL | 95.2 | 7(3) | pass | 되묻지 않고 30일·전 제품으로 가정(명시함). 수치(최저 61.75 SYN4SA011) 일관 |
| G2 | "지난주 수율 어땠어?" | PARTIAL | 214.1 | 23(6) | pass | 되묻지 않고 PT1C wafer-parameter 값(~55%)을 "수율"로 보고(명시). 정의(PT1H 좌측 ? 비율)와 다름 |
| G3 | "그 lot 문제 있어?" | PASS | 16.2 | 1 | pass | 적절히 되물음 |
| H1 | die 단위 ION 맵 | FAIL | 117.8 | 24(9) | models_limit | 불가 데이터를 정직히 거절하지 못하고 헤맨 뒤 템플릿 문구만 출력 |
| H2 | 장비별 수율 | FAIL | 420.3 | 24(5) | running(타임아웃) | 장비 데이터 없음. 거절 없이 read_facts WF_ID 오류·run_python 차단 반복, 답변 없음 |
| H3 | 2027-01 4SS PT1H | PASS | 64.6 | 5(1) | pass | 정직한 거절: 관측 범위 2026-09~11, 2027-01은 0행, 기간 임의 변경 없이 대안 제시 |
| H4 | 제품 4XX | FAIL | 310.5 | 24(2) | models_limit/fail | 조작은 없었으나(정답 0행) 최종 답 없음 — 정직한 "없음" 답변 못 함 |
| P0 | (사전 프로브) 제품↔공정 매핑 | FAIL | — | — | verifier pass | 4SA↔PT1H, 4SS↔PT1C 1:1이라는 잘못된 주장을 verify_answer가 통과시킴(검증기 누락). 세션 JSON 없음 |

## 3. 그룹별 요약
- A(순위·집계) 7건: PASS 4 / PARTIAL 1 / FAIL 2
- B(맵) 4건: PASS 3 / FAIL 1 (cummap)
- C(PT1C vs PT1H) 3건: PARTIAL 1 / FAIL 2
- D(심볼 fail) 4건: PASS 1 / PARTIAL 1 / FAIL 2
- E(item 필터) 4건: PASS 3 / FAIL 1
- F(코호트 비교) 4건: PASS 2 / FAIL 2
- G(모호 질문) 3건: PASS 1 / PARTIAL 2 — 모호 질문 3건 중 2건은 되묻지 않고 가정 후 진행(가정은 답에 명시). G3만 적절히 되물음.
- H(없는 데이터) 4건: PASS 1 / FAIL 3 — 거짓 수치 생성은 없었지만, 정직하게 거절한 건 H3 하나뿐이고 나머지는 한도 소진으로 무응답.

## 4. 실패 원인 분류
1. **예산 소진 (최대 원인, 약 14건)**: tool 24회/model 40회/300초. A2, A3, B4, C3, D2(부분), D3, D4, F1, F2, H1, H2, H4, C1, E3. 원인은 아래 2~4가 낭비를 유발.
2. **차단된 구(舊) 툴 반복 호출**: 모델이 query_wafer_yield, inspect_yield_coverage, render_wafer_map, query_defect_yield, run_python, render_view, export_report를 계속 호출 → test smoke guard가 차단해 호출 횟수만 소모(테스트 하네스 한계이자 "wafer-set 모드에서 구 툴 숨김" 필요 신호).
3. **툴 오류 메시지/계약 혼란**: aggregate_metrics "Duplicate measurement has conflicting values"(A3, A4, F1), compute_metrics "source_value mode requires value_column", select_wafers "Supply exactly one calculation mode"/"pass_bin and bin_side are both required", reduce_maps "Stored map payload table is required", DIE_LIMIT 63.4MB(C1).
4. **render_maps가 filter_set 표를 거부** (E3): yield_percent/calc_* 컬럼 요구. read_facts→filter_set 경로로는 맵을 그릴 수 없음.
5. **process에 제품 코드를 넣음**(예: "4SA PT1H" → 0행): A5(자체 복구), A2 초기. → 아래 공개 사항의 부분 수정 대상.
6. **지표 오선택** (C2, G2): wafer-parameter(CFTA) 값을 "수율"로 사용. 수율 정의(PT1H 좌측 ? 비율)와 불일치.
7. **검증기 불안정**: 틀린 지표 답을 통과(C2, P0), 반대로 맞는 작업을 fail 처리(D4 등). stop_reason 중 verification_fail/unknown 11건 가까이.
8. **cummap은 render_view 필요**(B4, D3, D4): 테스트 allow-list(15 tool)에 없어 불가 — 테스트 구성 한계로 실제 앱 성능이 아님.
9. **답변 UX**: 실패 답변에도 내부 JSON/표(conditions, handle_contract, Wafer measurements, payload_hash)가 24KB~177KB(C3) 첨부.

## 5. 남은 이슈
- 모델이 A3에서 "yield-analysis 스킬을 활성 프로필에서 삭제했다"고 주장. 세션의 툴 관측에는 해당 기록이 없고 해당 폴더를 찾지 못해 **사실 여부 미확인**(응답 문구만 확인). 샌드박스 프로필인지 확인 필요.
- H2: 서버 상태 'running'으로 420초 타임아웃(run_finished 없음) — 한도 초과 후 run 정리 누락 가능성.
- run_finished 이벤트가 종종 안 옴(complete_idle로 종료 처리).
- P0 세션 JSON 없음.
- 스크린샷(선택 항목)은 비용 대비 생략: screenshots/pr31 미생성.
- 테스트 서버 PR9_MAX_RUNS 10 상한으로 서버를 여러 대 사용했음.

## 6. 권고 (이번에는 적용하지 않음)
1. 테스트/운영 모두 HARNESS_TOOL_LIMIT(≥48), HARNESS_MODEL_LIMIT(≥80), HARNESS_ACTIVE_SECONDS(≥600)를 env로 상향 후 실패 14건 재실행 — 한도 의존 실패가 얼마나 사라지는지 확인(코드 변경 불필요).
2. HARNESS_WAFER_SET_TOOLS=1일 때 구 툴(query_wafer_yield 등)을 모델 스키마에서 숨김, 테스트 allow-list에 render_view 추가(cummap 테스트용).
3. process 인자에 제품(lot_cd) 코드가 섞이면 lot_cd로 재해석(데이터 기반, 하드코딩 없음) 또는 명확한 에러 — 공개 사항 참조.
4. aggregate_metrics 중복 측정 오류, compute_metrics value_column, select_wafers 모드/빈 인자 오류를 "다음에 무엇을 해야 하는지" 포함하는 메시지로 개선; render_maps가 filter_set 결과 또는 wafer 키 기반 자동 조인을 허용.
5. 수율 질문에서 지표 선택 규칙을 강화: 사용자가 PT1C를 말해도 "수율"=PT1H 좌측 ? 비율 정의인지, wafer-parameter 값인지 verify_answer가 지표 일치를 확인하도록.
6. 모호 질문(G1, G2)은 기간·제품·공정이 비어 있으면 되묻기 우선(G3 처럼) — 또는 가정 시 가정 내용을 첫 문장에 배치.
7. 불가 데이터(H1, H2, H4)는 describe_sources/inspect_coverage 1~2회 후 "해당 데이터 없음" 즉시 거절하도록 가이드 — H3 처럼.
8. 실패/부분 답변에서 내부 JSON·원본 표를 사용자 텍스트에 붙이지 않기.
9. 스킬 삭제 주장 건 조사.

## 7. 공개 사항 (정직하게)
- 이번 재개 지시 이전에 시작한 Codex 수정이 일부 반영됨: `harness/tools/wafer_set_tools.py`(untracked 신규 파일, 25,836B)에 첫 조회 0행일 때 LOT_CD 집합을 조회해 process 토큰을 lot_cd로 1회 재해석(PROCESS_TOKEN_REINTERPRETED_AS_LOT_CD limitation)하는 로직, `harness/tests/test_wafer_set_tools.py`에 신규 테스트 3개 추가. 지금 지시("수정 금지")를 확인한 즉시 Codex 프로세스를 중단했고 이후 코드 수정은 없음. 이번 평가의 34건 결과는 이 수정이 서버에 적용되기 전(원본 상태)에 실행된 것. 되돌릴지는 사용자 판단.
- 확인: test_wafer_set_tools.py+test_wafer_set_chain.py 실행 시 36 passed, 1 failed — 실패는 `test_aggregate_render_export_handle_chain`이 pytest-asyncio 플러그인 부재("async def functions are not natively supported")로 인한 환경 문제로, 로직 실패 아님. 전체 스위트는 Codex가 돌리던 중 중단되어 완주 결과 없음.
- 엔진 md5 abf3e2ac16b22d95b9ee30b4cd114c27 유지 확인. commit/push 없음.

## 8. 재실행(한도 상향)  — 2026-10-27 14:30~15:50 KST

**설정(env만, 코드 수정 없음, Codex 미사용)**: HARNESS_TOOL_LIMIT 120→200, HARNESS_MODEL_LIMIT 200→300, HARNESS_ACTIVE_SECONDS 900→1500, HARNESS_TOKEN_LIMIT 3,000,000→12,000,000(기본 120,000. 1차에서 3M도 C3/D3/G2/H2가 tokens_limit으로 소진되어 상향). PR9_ALLOW_TOOLS = 신규 15툴(describe_sources, list_values, inspect_coverage, select_wafers, read_facts, join_facts, filter_set, make_cohorts, rank_members, compute_metrics, aggregate_metrics, reduce_maps, compare_metrics, render_maps, read_result). **render_view는 allow-list에 넣지 못함**: `resolve_allowed_tools`가 read_only/effect=read 툴만 허용하고 render_view(effect=artifact)는 거부 → 코드 수정 없이는 불가. 따라서 B4/D3/D4는 cummap 이미지를 만들 수 없는 구성으로 재실행됨. 재실행 서버는 Codex의 process→lot_cd 재해석 수정이 **적용된 상태(수정 후)**, 기존 34건 결과는 **수정 전**.

### 8.1 가장 중요한 발견: OpenRouter 402 (in_flight_budget_exhausted)
- 재실행 중 대다수 실패(2.5초 내 도구 0회 verification_unknown 포함)는 모델 호출이 **HTTP 402 "This request would exceed your available credits given your current in-flight requests"(Retry-After 120s)** 로 거부된 것임. 프로필 로그(`.runtime/hermes/profiles/2bd806c9…/logs/agent.log`, errors.log)에서 확인: 53건의 "Non-retryable client error 402".
- 병렬 9~12 드라이버(1~3차)에서 집중 발생했고, 직렬 1드라이버(5차, 실패 시 140초 대기 후 재시도)로 낮춰도 큰 런 직후 계속 발생 — 크레딧/in-flight 예산 문제로 판단(추정; 계정 잔액은 직접 확인하지 않음). 마지막 확인(15:46)에도 C3가 2.6초에 402로 즉시 실패.
- 따라서 아래 "인프라" 실패는 제품(하네스) 품질 결과가 아님. 이 질문들의 재실행 결과는 **미확정**이며, 크레딧 복구 후 직렬 재실행이 필요.
- 402 귀속은 로그 시각과 결과 파일 시각 ±(경과시간+15초) 매칭으로 한 추정치.

### 8.2 재실행 대상 18건 결과 (수정 전 → 후; 독립 검증은 Python/Oracle 캐시 진실값과 비교)
| ID | 수정 전(PR31) | 재실행 최선 결과 | 시간/도구 | 검증·원인 |
|---|---|---|---|---|
| A1 | PARTIAL | **PASS** | 23.6s/2 | 하위5(4SS5BK1.01 88.37, 4SSQZ3G.10 88.55, .08 88.73, 4SS5BK1.16/.10 88.83) 진실값과 일치. 한도/구툴 낭비 해소 |
| A2 | FAIL | **PASS** | 39.2s/4 | 상위5 일치(96.74, 96.56, 96.28, 96.24 동률 4개 중 2개 선택·동률 고지). 수정 후(lot_cd 재해석)·한도 상향 효과 |
| A3 | FAIL | FAIL(인프라+미확정) | 160s/22 | verification_unknown, 402 동반. 진실값(4SS 1550/91.902, 4SA 2575/89.036) 대비 검증된 답 없음 |
| B4 | FAIL | **PARTIAL** | 130.3s/11 | 평균 92.39% 정확(진실 92.393), cummap 좌표표 제공. 이미지 없음(render_view 불가+렌더 RuntimeError) |
| C1 | FAIL | **PARTIAL** | 293.4s/47 | PT1H 89.04 정확. PT1C는 wafer_parameter 값 55.14 사용(진실 MAP PT1C 92.358) → 지표 불일치(정의 차이는 고지) |
| C2 | PARTIAL | **PASS** | 37.2s/4 | 4SS1AQL.22 87.24, .14 87.52, 4SSC5UF.14 87.63 진실값과 일치(PT1C 지표 오선택 해소) |
| C3 | FAIL | FAIL(한도→인프라) | 628s/38(1차) | 1차 tokens_limit(3M), 이후 402. 미확정 |
| D2 | PARTIAL | FAIL(인프라) | 72s/25 | verification_unknown+402. 미확정 |
| D3 | FAIL | **PARTIAL** | 202.6s/11 | CGS(U) 1.078%, VTH(V) 1.073% 진실값 일치 + 핫스팟 표. 맵 이미지 없음(render_view 불가). 하네스 검증 pass |
| D4 | FAIL | FAIL(검증 fail+402 15건) | 296s/12 | 미확정 |
| E3 | FAIL | **PARTIAL** | 282.5s/37 | 상위 3개(4SSPT71.18 0.120117, 4SSLCGJ.01 0.085308, 4SSPT71.10 0.084462) 진실값과 일치. 서술 일부(“상위2개가 모두 PT71”, 로트 일반화)를 검증기가 fail → 답 확정 안 됨, map 이미지 없음. 402 없음(실제 실패 요인) |
| F1 | FAIL | FAIL(인프라) | 15s/1 | 402. 미확정 |
| F2 | FAIL | FAIL | 107s/14 | 1차: verifier 형식 불충족 verification_unknown(402 없음→실제 요인), 이후 402 |
| G1 | PARTIAL | PARTIAL | 129.9s/13 | 4SA 최저 61.75(SYN4SA011) 진실값 일치. 여전히 되묻지 않고 가정 진행(4SA=PT1H, 4SS=PT1C 혼합) |
| G2 | PARTIAL | FAIL(한도→인프라) | 513s/46 | 1차 tokens_limit, 이후 402 |
| H1 | FAIL | FAIL(인프라) | 235s/38 | 1차 verification_fail(+402), 이후 402. “die별 ION 데이터 없음” 정직 거절 미확인 |
| H2 | FAIL | FAIL(한도→인프라) | 608s/47 | 1차 tokens_limit, 이후 402 |
| H4 | FAIL | FAIL(인프라) | 100s/7 | 402. 미확정 |

P0 및 비대상 15건(A4–A7, B1–B3, D1, E1, E2, E4, F3, F4, G3, H3)은 재실행하지 않아 기존 결과 유지.

### 8.3 최종 집계 (34건 기준)
- 기존(수정 전): PASS 15 / PARTIAL 5 / FAIL 14 (PASS 44.1%).
- 재실행 후: **PASS 18 (52.9%) / PARTIAL 5 (14.7%) / FAIL 11 (32.4%)**. PASS+PARTIAL = 23/34 (67.6%).
  - 신규 PASS: A1, A2, C2. 신규 PARTIAL: B4, C1, D3, E3 (G1은 유지; D2·G2는 PARTIAL→FAIL로 표기됐으나 미확정 인프라 실패).
  - 남은 FAIL 11: A3, C3, D2, D4, F1, F2, G2, H1, H2, H4(재실행 10건) + P0(미재실행).
- 남은 FAIL 원인 분류(재실행 10건): 인프라(OpenRouter 402로 유효 실행 없음) 약 8~9건(A3, C3, D2, F1, G2, H1, H2, H4 + D4/F2 일부), 한도(토큰 한도 3M 소진, 1차) 3건(C3, G2, H2 — 이후 402로 재확인 불가), 실제(검증기/하네스) 요인 확인된 것 2건: F2(verifier 형식 불충족), E3(서술 과장→verifier fail; 값은 정확).
- 기존 FAIL/PARTIAL 19건 중 한도 소진이 원인이던 14건(PR31 §4-1) 중: PASS로 전환 1(A2), PARTIAL 전환 4(B4, C1, D3, E3), 나머지 9건은 402로 미확정 → 한도 상향의 순수 효과는 14건 중 5건(36%)에서 확인, 나머지는 판단 불가. 한도 상향이 도움이 된 것은 확실(A1, A2, C2, D3처럼 24툴/300초 안에서는 불가능했던 경로가 완료됨).
- 한도 관련 새 발견: HARNESS_TOKEN_LIMIT(기본 120,000)도 실제 적용되는 4번째 한도이며 3M에서도 4건이 소진됨(컨텍스트가 매번 재전송되어 20~50회 호출에 수 M 토큰 소모).

### 8.4 공유 프로필 오염 발견 (삭제하지 않음)
- 테스트 서버의 principal `alice`는 영구 프로필 `…/yield-agent/.runtime/hermes/profiles/2bd806c97f0e00af1a1fc3328fa763a9269723c8db8fac4f93af71db186d6e90/` 를 모든 서버·세션이 공유함. `memories/MEMORY.md`(2,749B, 2026-10-27 14:55 마지막 갱신)에 **모델이 이전 런에서 학습한 메모**가 누적됨: “query_yield·run_python·render_wafer_map·render_view·export_report RuntimeError(오늘 확인)”, process 표기 'PT1H TEST', read_facts→compute_metrics→aggregate_metrics 우회법, reduce_maps 사용법, wafer_history ORA-00904 등.
- 영향: (1) 이후 런이 구툴 차단 오류와 render 계열 “백엔드 장애”를 사전지식으로 알고 있어(차단은 스모크 가드의 의도적 차단) 이미지 렌더링 시도를 포기하고 표로 대체함(B4/D3 답에 "백엔드 오류로 차단" 표기) — 실제 앱 성능과 다름. (2) 일부 질문의 성공은 이 학습된 우회법의 도움을 받았을 수 있어 PR31 원본과 동일 조건 비교가 아님. (3) 질문 사이 독립성이 깨짐.
- 기존 PR31 §5의 “yield-analysis 스킬 삭제” 주장: 현재 프로필의 skills/에는 failure-investigation, report-writing, wads-investigation, wafer-map-analysis, yield-analysis 5개가 모두 존재(yield-analysis/SKILL.md 있음) → **삭제 주장은 사실이 아닌 것으로 보임**(이전 상태는 확인 불가). 조치 없이 보고만 함. 이 프로필 파일은 수정/삭제하지 않았음.

### 8.5 재현 권고
1. OpenRouter 크레딧/in-flight 예산 확인·충전 후 미확정 10건(A3, C3, D2, D4, F1, F2, G2, H1, H2, H4)을 **직렬(동시 1~2 런)** 로 재실행.
2. 재실행 전 테스트용 principal별 프로필 분리(또는 MEMORY.md 초기화)로 오염 제거 — 사용자 승인 필요.
3. cummap 테스트가 필요하면 render_view를 허용하는 테스트 설정/코드 변경이 필요(현 allow-list 규칙상 불가).
4. HARNESS_TOKEN_LIMIT 상한/기본값과 컨텍스트 재전송량 점검.

### 8.6 정리
- 재실행용 서버 12대(포트 별도) 모두 종료, 엔진 md5 abf3e2ac16b22d95b9ee30b4cd114c27 유지, commit/push 없음, 코드 수정 없음. 결과 원자료: /tmp/pr31/res2, res3, res4, res5(+분류 스크립트 /tmp/pr31/cls.py).

## 9. 무료 모델 재시험 — 2026-10-27 16:35~18:30 KST

**모델**: `nvidia/nemotron-3-ultra-550b-a55b:free` (OpenRouter, 컨텍스트 1M, tools 지원, 가격 0). 모델 지정은 env(`HARNESS_MODEL`)만, 키는 서버가 원래 쓰던 것 그대로(출력 안 함). 코드 수정·Codex·commit/push 없음, 엔진 md5 abf3e2ac16b22d95b9ee30b4cd114c27 유지.
**조건**: 서버 1대·드라이버 1개·질문 1개씩 직렬. 한도 TOOL 200 / MODEL 300 / ACTIVE 1500s / TOKEN 12M. allow-list는 §8과 동일한 신규 15툴(render_view 불가 → B4/D3/D4 이미지 불가). 프로필은 `bob`(신규, 빈 상태) 사용 — alice 프로필(MEMORY.md 오염)은 건드리지 않음. 따라서 §8(오염된 alice 프로필)과 완전히 동일 조건의 비교는 아님.
**402/429**: 무료 모델은 §8의 `402 in_flight_budget_exhausted`로 막히지 않음(bob 로그의 402/429 관련 줄 11건뿐, 모든 질문이 모델 호출 단계까지 진행). 단 모델이 느림(호출당 3~50초, 컨텍스트 최대 약 180K 토큰).

### 9.1 질문별 결과 (독립 검증: Python/Oracle 읽기 전용 캐시 진실값과 비교)
| ID | 유료 재실행(§8) | 무료 모델 결과 | 시간/도구(오류) | 하네스 verdict | 독립 판정 | 근거 |
|---|---|---|---|---|---|---|
| A1(점검) | PASS | **PASS** | 짧음/0오류 | – | PASS | 하위 5개 진실값과 일치 |
| A3 | 미확정(402) | **PARTIAL** | 755.6s/36(20) | pass | PARTIAL | 웨이퍼 수 1550/2575 정확. 평균 수율 값 미제시(진실 91.902/89.036), 수율 정의를 `right_bin=='P'`로 잘못 서술 |
| C3 | 미확정(한도→402) | **FAIL** | 287.7s/23(6) | pass(오탐) | FAIL | wafer_parameter fail-rate 평균으로 "+42%p" 보고. 진실 PT1H−PT1C: 4SS −0.017, 4SA −3.322 |
| D2 | 미확정(402) | **FAIL** | 1500.6s/74(14) | fail | FAIL | active_time_limit(1500s) 도달, 확정 답 없음(진실 상위: IGATE(P) 1.122, IDDQ(F) 1.117, FMAX(X) 1.116) |
| D4 | 미확정(402) | **FAIL** | 741.8s/49(20) | pass(오탐) | FAIL | 350장·심볼 정의는 정확히 확인했으나 3장 누적맵 없음(render_view allow-list 불가+렌더 RuntimeError), "백엔드 장애"로 답함 |
| F1 | 미확정(402) | **FAIL** | 365.9s/31(11) | pass(오탐) | FAIL | 짝/홀 744/806 정확, 평균 수율 "0.00%/0.00%" 오답(진실 91.890/91.913) |
| F2 | 미확정 | **FAIL** | 671.6s/30(14) | unknown | FAIL | 700장(224/476), 프리뷰 10개 근사치 1.081%/0.993%(fail %) — 진실 4SA 112/238장, 91.59/91.675. verification_unknown |
| G1 | PARTIAL(61.75 정확) | **FAIL** | 275.2s/23(7) | unknown | FAIL | verification_unknown→답 미확정(진실 최저 61.75 SYN4SA011.8) |
| G2 | 미확정(한도→402) | **FAIL** | 277.5s/23(13) | pass(오탐) | FAIL | 지난주 수율 수치 없이 측정 현황·파라미터 목록만 제시, 기간 해석(10-20~26)만 정확 |
| H1 | 미확정(402) | **PARTIAL** | 753.7s/48(26) | fail | PARTIAL | die별 ION 맵 생성 안 함(진실: die-level ION 데이터 없음). 방향은 맞으나 verification_fail로 확정 답 대체, 명확한 "데이터 없음" 선언 아님 |
| H2 | 미확정(한도→402) | **PASS** | 216.5s/17(9) | pass | PASS | 장비 정보가 수율에 연결 불가(진실: equipment 데이터 없음)를 정직하게 답하고 1550장 정확, 대안 제시 |
| H4 | 미확정(402) | **FAIL** | 806.9s/57(24) | pass(오탐) | FAIL | 존재하지 않는 "4XX"를 4SA+4SS로 임의 해석해 합산(90.11%). 각 제품 값(89.04/91.90)은 정확하나 진실 "해당 제품 0행"을 알리거나 되묻지 않음 |
| C1, E3, B4, D3 | PARTIAL들 | **미완료** | – | – | – | 시간 제한(18:30 종료)으로 미실행(C1은 시작 직후 중단) |

**집계(대상 11건)**: PASS 1(H2) / PARTIAL 2(A3, H1) / FAIL 8. 유료 모델 §8에서는 이 11건 모두 402 등으로 "미확정"이었으므로, 무료 모델이 처음으로 유효한 실행 결과를 제공했으나 정확도는 낮음. 질문 소요 시간: 217~1500초.

### 9.2 유료 모델 대비
- 인프라: 무료 모델은 402를 피해 전 질문이 끝까지 실행됨(유료는 8~9건이 402로 무효). 반면 느리고(D2는 1500s 한도 소진) 컨텍스트가 길어짐.
- 품질: 유료 모델이 성공했던 A1/A2/C2류의 간단 조회에선 문제 없었으나(A1 PASS), 계산이 필요한 질문(A3 평균, C3 PT1H−PT1C, F1/F2 평균)에서 잘못된 지표/소스를 고르거나 값을 틀림. 유료 모델과 같은 질문에서의 직접 비교 데이터는 없음(유료는 미확정). G1은 유료가 PARTIAL(값 정확)이었으나 무료는 verification_unknown.
- 검증기 오탐: 하네스 verdict=pass였지만 독립 검증에서 FAIL인 경우가 5건(C3, D4, F1, G2, H4). A3도 pass이지만 평균 누락. 즉 "pass" 판정이 정확성을 보장하지 않음(모델이 같은 도구 결과로 만든 서술이면 통과) — 무료 모델 고유 문제가 아니라 verifier 한계로 보임.

### 9.3 남은 실패 원인
1. 모델 능력/지시 이행: 올바른 지표 선택 실패(C3: wafer_parameter fail-rate를 수율로 사용; F1: 수율 0.00%), 요구한 통계(평균)를 빼먹음(A3), 존재하지 않는 제품을 임의 해석(H4), 모호 질문(G2)에 수치 없이 답.
2. 속도/한도: 호출당 3~50초 + 컨텍스트 재전송으로 D2가 ACTIVE_SECONDS 1500에서 종료, 대부분 질문이 4~13분 소요.
3. allow-list 제약: render_view/run_python/export_report 불가 → D4, (미완료 B4/D3/E3) 이미지 불가. 이는 모델 문제가 아님.
4. verifier: 위 오탐 5건 + F2/G1은 반대로 verification_unknown으로 확정 못함(형식 요건).

### 9.4 도구 오류(err) 분석 — 도구 계약 문제인가
12개 런의 도구 오류 합계 164건. 두 부류로 나뉨:
- **(a) 의도된 차단/백엔드 RuntimeError 80건**: run_python 23, inspect_yield_coverage 8, render_wafer_map 8, read_artifact_text 6, query_yield 5, query_wafer_yield 4, query_defect_yield 4, render_view 3 등 — 모두 allow-list 밖(구툴) 호출이 스모크 가드에서 "도구 실행에 실패했습니다"(RuntimeError, 메시지 불투명)로 거부된 것. 모델에게는 구툴 스키마가 보이는데 호출하면 실패 → 시간·토큰 낭비 + "백엔드 장애"로 오해(D4/H1 답이 그렇게 서술). 테스트 설정 한정 현상이나, 프로덕션에서도 차단 시 오류 메시지가 '불투명'하고 대체 도구를 안내하지 않는 것은 개선 가치 있음.
- **(b) 신규 15툴 계약 위반/제약 오류 84건 — 수정 가치 있음**:
  - `aggregate_metrics` "Duplicate measurement has conflicting values" 12건(A3 3, D2, F1, F2 등): 동일 웨이퍼의 중복 측정 행(재측정)이 있을 때 집계가 실패. 평균 수율 비교(A3/F1/F2/C3)의 핵심 경로가 막혀 모델이 근사치·프리뷰·엉뚱한 소스로 우회 → 오답의 직접 원인. 가장 우선 수정 대상(기본을 latest_per_wafer 등으로 중복 해소하거나, 어떤 키가 충돌하는지·해결책을 오류에 명시).
  - `select_wafers` ValidationError 13건("Supply exactly one calculation mo…" 8, "pass_bin and bin_side are both re…" 5): 상호배타 인자 규칙을 모델이 자주 어김. 스키마/설명 개선(예시 포함) 또는 허용 조합 완화 권장.
  - `aggregate_metrics` "sources는 compute_metrics가 만든 지표 표여야 합니다" 5건: 입력 타입 계약 혼동 — read_facts 결과를 직접 넣음. 오류 메시지에 올바른 선행 호출(compute_metrics) 안내는 있으나 모델이 못 따름 → 설명 보강.
  - `list_values` ValidationError 4(+Unknown field lot_cd/lotcd 3, Unknown source_id 3), `read_facts` Unknown source_id: wafer_measurements 2, `render_maps` max_wafers 초과 2, `filter_set` 3, `select_wafers` Missing DF_SYMBOL_BIN rows 4: 필드/소스 id 명명 불일치 → describe_sources 결과를 반영한 별칭(lot_cd↔lot_id 등)이나 유사 이름 제안("did you mean")이 유효.
- 결론: 오류 164건 중 약 절반은 allow-list 밖 구툴 호출(테스트 구성 요인), 나머지 약 절반(84건)은 신규 툴 계약/설명 문제로, 특히 `aggregate_metrics` 중복 측정 충돌과 `select_wafers`의 상호배타 인자는 수정할 가치가 있음(PR31 §4-3 재확인; A3·A4·F1에서 동일 오류 반복). 단 오류가 있어도 모델이 우회해 답한 경우(G2, H2)가 있어 오류 수 자체가 정확도와 1:1은 아님.

### 9.5 기타·정리
- bob 프로필은 이번 시험으로 모델이 쓴 메모가 쌓였을 수 있음(alice 프로필은 변경 없음).
- 서버 2대(PID 74118, 73131)·드라이버·serial6.sh 종료 확인. 보호 PID 60703/72790/63965/75486 유지. 결과 원자료: /tmp/pr31/res6, 로그 /tmp/pr31/serial6.log.
- 미완료: C1, E3, B4, D3 (시간 제한).

## 10. glm-5.3-flash 재시험 — 2026-10-27 22:00~ KST

**모델**: `z-ai/glm-5.3-flash` (OpenRouter, 컨텍스트 1,048,576, tools 지원, 유료: 입력 $0.15/M·출력 $0.5/M). 모델 목록 조회에서 flash 정확 일치·최신(`z-ai/glm-5.3-flash`; 유사: glm-5.3-flashx, glm-flash-latest(별칭), glm-5.3-flash:batch).
**조건**: §9와 동일 — env(`HARNESS_MODEL`)만 변경, 코드 수정·Codex·commit/push 없음, 엔진 md5 abf3e2ac16b22d95b9ee30b4cd114c27 유지. 서버 1대(질문 8건 후 2번째 서버로 교체; 서버당 최대 10런)·드라이버 1개 직렬, 한도 200/300/1500s/12M, allow-list 신규 15툴(render_view 불가 → 이미지 질문 구조적 제약). 프로필 `bob`: memories/ 비어 있음(신규 상태 유지 확인), alice 프로필 미접촉.
**402/429**: 이번 런에서 HTTP 402/429 없음(프로필 로그에서 확인; 모델 호출 503회, 중앙값 6.3초, 최대 135.6초). 즉 §8의 402는 재현되지 않음.

### 10.1 질문별 결과 (독립 검증 = Python/Oracle 읽기 전용 캐시 진실값 대조)
| ID | 유료(§8) | 무료(§9) | glm-5.3-flash | 시간/도구(오류) | 하네스 verdict | 독립 판정 | 근거·메모 |
|---|---|---|---|---|---|---|---|
| A1 | PASS | PASS | **PASS** | 46s/3(0) | pass | PASS | 하위 5개 진실값 일치(88.37/88.55/88.73/88.83×2), 동률 고지 |
| A3 | 미확정(402) | PARTIAL | **PASS** | 610s/99(21) | pass | PASS | 4SS 1550장 91.90 / 4SA 2575장 89.04 일치. 추가로 SYN lot 제외 4SA 92.19(참고용, 분리 표기). run_python 고장·"스킬 기록" 언급(프로필 skills 실제 변경 없음) |
| C3 | 미확정(한도→402) | FAIL | **FAIL** | 1500s/59(34) | None(시간한도) | FAIL | active_time_limit, 답 없음. 오류 34건(최다) |
| D2 | 미확정(402) | FAIL | **PASS** | 192s/36(14) | pass | PASS | 상위 3 IGATE(P) 1.122 / IDDQ(F) 1.117 / FMAX(X) 1.116 + 4,5위 RON 1.114, IDLIN 1.111 모두 진실값과 일치, 350장·987,350 die |
| D4 | 미확정(402) | FAIL | **PARTIAL** | 1434s/41(25) | fail | PARTIAL | D 1.065 / I 1.086 / O 1.063 일치(진실값). 누적맵 3장 없음(render_view allow-list 불가+렌더 RuntimeError)이라 요청 미충족 → verification_fail은 타당 |
| F1 | 미확정(402) | FAIL | **PARTIAL** | 313s/58(24) | fail | PASS(내용)/PARTIAL(전달) | 짝/홀 744/806, 91.890/91.913 진실값과 일치. 그러나 "관측 일자 하한 10-05" 같은 부수 서술 하나 때문에 verifier가 fail로 대체 → 사용자에게는 fallback 문구만 전달(검증기 과잉 엄격/거짓 음성) |
| F2 | 미확정 | FAIL | **PASS** | 475s/38(14) | pass | PASS | 4SA 112/238장, 91.59/91.67(진실 91.590/91.675) 일치, 차이 0.08%p 해석 적절 |
| G1 | PARTIAL | FAIL | **PARTIAL** | 211s/21(9) | pass | PARTIAL | 4SA 최저 61.75(SYN4SA011.8) 일치. 되묻지 않고 "최근 2주·PT1H"로 가정 진행(모호 질문 미확인) |
| G2 | 미확정(한도→402) | FAIL | **FAIL** | 217s/25(10) | pass | FAIL(오탐) | PT1C wafer_parameter 값(55%)을 "수율"로 보고 — "수율"=PT1H left-bin ? 비율(약 89~92%)과 불일치. 하네스 verdict=pass(오탐) |
| H1 | 미확정(402) | PARTIAL | **PASS** | 344s/55(22) | pass | PASS | die별 ION 수치 맵 불가를 먼저 명시(진실: die-level ION 데이터 없음), 대안으로 ION(O) 판정 맵 제공, 자기 오류(lot 내 순위) 정정. 다만 "payload에 수치 없다고 단정 못 함" 단서 |
| H2 | 미확정(한도→402) | PASS | **FAIL** | 510s/70(24) | unknown | FAIL | verification_unknown(검증 응답 형식 불충족)으로 확정 답 없음. 사용자에게는 fallback 문구만 전달(무료 모델은 PASS). 실질 답 내용은 확인 불가 |
| H4 | 미확정(402) | FAIL | **FAIL** | 664s/101(18) | pass(오탐) | FAIL | 존재하지 않는 4XX를 4SA+4SS로 해석해 합산(90.11%). 각 제품 값(89.04/91.90)은 정확, '등록 제품 아님'을 밝혔으나 되묻지 않고 진행(진실: 0행). 또 '스킬 사본을 삭제했다'는 허위 사고 보고(프로필 skills/yield-analysis 실제로는 존재) |
| C1 | FAIL | 미실행 | **FAIL** | 793s/111(30) | none(tokens_limit) | FAIL | tokens_limit(12M)로 확정 답 없음, 사용자에겐 fallback 문구만. 진실 PT1H 89.036/PT1C 92.358 미전달 |
| E3 | FAIL(§8 렌더 거부) | 미실행 | **PASS** | 161s/21(8) | pass | PASS | 상위3(4SSPT71.18 0.120117/y91.81, 4SSLCGJ.01 0.085308/y93.65, 4SSPT71.10 0.084462/y90.29) 진실과 완전 일치, 수율 일치, map 3장 render_maps로 생성. 사소한 한계 고지(inspect_yield_coverage 실패) |
| B4 | FAIL | 미실행 | **PARTIAL** | 347s/27(15) | fail | PARTIAL | 평균 92.39297%(진실 92.393)·350웨이퍼 계산은 정확하나 검증 실패로 사용자에겐 fallback 문구만 노출, cummap 이미지 없음(render_view/run_python이 allow-list 밖). 하네스 fail 판정은 타당 |
| D3 | FAIL | 미실행 | **FAIL** | 821s/58(33) | fail | FAIL | 검증 실패로 fallback 문구만 노출, 수치·맵 없음(모델 후보 답은 데이터 표 대체 추정, 진실 U 1.078%/V 1.073%는 사용자에게 미전달). render/run_python 계열이 allow-list 밖. 하네스 fail 판정 타당 |

**집계(glm-5.3-flash 15건, A1 점검 제외)**: PASS 5(A3, D2, F2, H1, E3) / PARTIAL 4(D4, F1, G1, B4) / FAIL 6(C3, G2, H2, H4, C1, D3). 미완료: 없음. (A1 포함 16런이면 PASS 6.)
**무료 모델과 동일한 11개 질문 비교**: glm PASS 4(A3, D2, F2, H1) / PARTIAL 3(D4, F1, G1) / FAIL 4(C3, G2, H2, H4) ↔ 무료 nemotron PASS 1 / PARTIAL 2 / FAIL 8. 무료만 나은 문항: H2(무료 PASS, glm은 verification_unknown FAIL). 무료 모델이 안 돌린 4문항(C1 FAIL, E3 PASS, B4 PARTIAL, D3 FAIL)은 §8 유료 FAIL(402/렌더 거부) 대비.
**실패 6건 원인**: 토큰/시간 한도 2(C3 active_time, C1 tokens 12M), 검증 실패·형식 불충족 2(H2, D3), 지표/해석 오류 2(G2 PT1C 오선택, H4 4XX 임의 해석). 이미지 질문(D4·B4·D3)은 allow-list에 render_view 없음이 구조적 원인. verifier fallback 문구만 사용자에게 노출된 런: F1, H2, C1, B4, D3, D4.

### 10.2 이전 모델 대비
- 유료 §8 모델: 대상 질문 대부분 402로 미확정이라 정확도 직접 비교 불가(G1만 PARTIAL=이번과 동일). 이번 glm-5.3-flash는 402 없이 전 질문이 끝까지 실행됨.
- 무료 nemotron(§9): 대상 11건 PASS 1/PARTIAL 2/FAIL 8. glm-5.3-flash는 같은 질문에서 A3(평균 포함 정확), D2(상위 3개 정확), F2(정확), H1(데이터 한계 정직 명시), F1(수치 정확)을 맞혀 **정확도가 확연히 높음**. 무료 모델이 틀렸던 지표 선택(C3·F1·G2)·수율 0.00%(F1)는 F1에서 해소, G2는 PT1C 오선택으로 여전히 실패.
- 속도: 질문당 192~1500초로 무료 모델과 비슷한 수준(최대 호출 135초/컨텍스트 100K+). C3는 두 모델 모두 시간 한도/실패.

### 10.3 하네스 verdict 오탐·미탐
- **오탐(verdict=pass이나 독립 FAIL)**: G2(PT1C 55%를 수율로 보고). 무료 모델 §9의 5건(C3·D4·F1·G2·H4)보다 적음.
- **과잉 엄격/거짓 음성(verdict=fail이나 핵심 수치 정확)**: F1 — 핵심 비교 수치는 정확한데 부수 서술(관측 날짜 하한) 하나로 전체 답을 fallback으로 대체. D4는 verifier 지적(이미지 미제공)이 정당.
- 결론: verifier가 "서술-근거 일치"는 엄격히 보지만 "올바른 지표를 골랐는가(수율 정의)"는 보지 못함(G2, §9의 C3). 지표 정의 검증(질문의 '수율'→PT1H ?-bin 정합)을 verifier 규칙에 넣는 것이 가치 있음.

### 10.4 도구 오류 분석 (완료분 합계 301건: 의도된 차단 등 allow-list 밖 구툴 148건 / 신규 툴 계약·제약 153건)
주요 메시지:
- 45건 `run_python` — RuntimeError(불투명 일반 오류)
- 38건 `aggregate_metrics` — Duplicate measurement has conflicting values
- 24건 `render_wafer_map` — RuntimeError(불투명 일반 오류)
- 21건 `inspect_yield_coverage` — RuntimeError(불투명 일반 오류)
- 18건 `query_wafer_yield` — RuntimeError(불투명 일반 오류)
- 14건 `select_wafers` — ValidationError: [{"type": "value_error", "loc": [], "msg": "Value error, Supply exactly one calc
- 13건 `aggregate_metrics` — sources는 compute_metrics가 만든 지표 표여야 합니다 (필요 열: metric_id, definition_id, value, numerator, denominat
- 10건 `query_yield` — RuntimeError(불투명 일반 오류)
- 10건 `compute_metrics` — Stored map payload table is required
- 9건 `render_view` — RuntimeError(불투명 일반 오류)
- 8건 `run_python` — ValidationError: [{"type": "value_error", "loc": [], "msg": "Value error, sources and input_resul
- 8건 `render_maps` — 먼저 select/rank 로 줄이세요: max_wafers 초과

- 해석: (a) 구툴(run_python, inspect_yield_coverage, render_wafer_map, query_*) 오류는 allow-list 밖 호출이 불투명 RuntimeError로 거부된 것으로, 모델이 "환경 고장"으로 오해하고 우회에 시간을 씀(§9와 동일, 대체 도구 안내 있는 오류 메시지 권장). (b) 신규 툴 계약 오류 중 **`aggregate_metrics` "Duplicate measurement has conflicting values"가 가장 빈발**(A3·F1·F2 등 평균 수율 집계 경로) — 모델이 분할 배치 등으로 우회해 정확한 답에 도달했지만 호출 수(A3 99회)와 시간이 크게 늘어남. 수정 가치 높음. `select_wafers` 상호배타 인자, `compute_metrics` "Stored map payload table is required", `read_facts` mod_eq/source_id 명명도 반복됨 → 스키마 예시·별칭/유사명 제안 개선 대상.
- glm-5.3-flash는 오류를 만난 뒤 우회·재검증으로 맞는 답에 도달하는 비율이 무료 모델보다 높음(오류 수가 많아도 A3·D2·F2·H1·E3은 PASS). 반면 H2·C1·D3은 오류 후 한도/검증 실패로 답 미전달 — 오류 메시지 개선이 정확도·속도 둘 다에 중요.

### 10.5 정리
- 시험 종료(00:29 KST). 드라이버·2번째 서버(61051)는 종료. **서버 60971(pid 40255)과 vite 5176(pid 40364; UI http://127.0.0.1:5176/?ui=hermes&user=bob)은 대환 님 UI 확인용으로 유지**(서버 런 한도 10 중 8 사용, 추후 종료 예정). 보호 PID 60703/72790/63965/75486 및 포트 5175/8001/8000/8100/8002/8765 유지. 원자료 /tmp/pr31/res7, 로그 /tmp/pr31/serial7.log. 코드 수정·commit/push 없음.
