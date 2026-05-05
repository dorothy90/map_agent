# Cummap 관련 코드 위치 정리

yield agent 의 cummap grid 와 map_agent 의 일반 cummap 두 흐름이 동시에 존재. 사내 환경에서 cummap 정보가 안 보일 때 어디부터 짚을지 정리.

모든 경로는 `/Users/daehwankim/yield-agent/map_agent/08-YieldAgent/` 기준 상대.

---

## 데이터 소스

`MAP` 테이블 (Oracle).

| 컬럼 | 비고 |
|---|---|
| `LOT_ID` | wafer 의 lot ID |
| `WF_ID` | wafer ID |
| `MAP_VAL_JSON` | die 좌표 + bin 코드 JSON (`{"MAP": ["x,y,left_bin,right_bin", ...]}`) |
| `FAB_ID` | fab 식별자 |
| `LOT_CD` | "4SS" / "4SA" 등 lot category code |
| `START_TM`, `END_TM` | 측정 시작/종료 시각 |
| `OPER_DET_DESC` | "PT1H TEST" / "PT1C TEST" |

---

## A. 데이터 fetch (DB → wafer 데이터)

| 파일:라인 | 함수 | 용도 |
|---|---|---|
| `map_agent.py:459` | `_query_wafer_data_by_date(lotcd, start, end, category)` | **yield grid cummap 의 진입점** — 날짜 범위로 wafer 조회 |
| `map_agent.py:129, 153, 182, 478` | (other SELECT 블록) | lot_id / wf_id 기반 단건 조회 (일반 cummap 용) |

---

## B. Wafer JSON 파싱

| 파일:라인 | 함수 | 출력 |
|---|---|---|
| `map_agent.py:223` | `_parse_map_json(map_val_json)` | `{"x_y": {left_bin, right_bin}}` dict |
| `map_agent.py:237` | `_parse_wafer_for_cummap(args)` | `(rows, cols, passes)` 리스트 — pass-rate 계산용 |
| `map_agent.py:260` | `_parse_wafer_for_binmap(map_val_json)` | binmap 용 dict |
| `map_agent.py:274` | `_get_map_bounds(map_data_list)` | `(min_row, max_row, min_col, max_col)` 격자 경계 |

---

## C. 시각화 함수 (두 종류 cummap)

### C1. Yield agent 의 cummap grid (기간 × 파라미터)

이번 PR 에서 Δ row + zone 분석 추가된 흐름.

| 파일:라인 | 역할 |
|---|---|
| `yield_viz.py:747` | `_build_cummap_grid_html(...)` — 메인 렌더 함수, PNG → base64 HTML |
| `yield_viz.py:33` | `from wafer_zones import WAFER_ZONES, compute_zone_deltas, worst_zone` |
| `yield_viz.py:761-763` | `_query_wafer_data_by_date`, `_parse_wafer_for_cummap`, `_get_map_bounds` import |
| `yield_viz.py:815` | `_query_wafer_data_by_date` 호출 (period × cat 별) |
| `yield_viz.py:850-852` | `_parse_wafer_for_cummap` 병렬 호출 |
| `yield_viz.py:877-906` | `delta_row` 계산 (curr - prev) |
| `yield_viz.py:893-895` | **이번 PR**: `compute_zone_deltas` + `worst_zone` 호출 |

### C2. Map agent 의 일반 cummap (lot / wafer 단위)

| 파일:라인 | 함수 |
|---|---|
| `map_agent.py:370` | `_visualize_cummap` (entry) |
| `map_agent.py:389` | `_visualize_cummap_inner` (실제 렌더) |
| `map_agent.py:534-552` | `map_type="cummap"` / `"all"` 분기 |
| `map_agent.py:598` | 기존 binmap/cummap 생성 entrypoint |

---

## D. Agent flow / 라우팅

| 파일:라인 | 역할 |
|---|---|
| `supervisor.py:96` | `map_type: "binmap" \| "cummap" \| "all"` 파라미터 schema |
| `supervisor.py:57` | task `goal` 예시에 "cummap" 등장 |
| `prompts.py:54, 100-147, 191, 290-297, 338, 420, 481` | LLM 프롬프트 곳곳에 cummap 키워드 / 예시 / 라우팅 룰 |
| `yield_query_agent.py:29, 265-269` | `_build_cummap_grid_html` import + 호출 (cummap grid 트리거) |

---

## E. 결과 처리 (artifact → 사용자)

| 파일:라인 | 역할 |
|---|---|
| `yield_query_agent.py:287-292` | `cummap_html` → `_save_html_to_file` → `"yield_cummap"` artifact 로 state 에 push |
| `ppt_builder.py:277-279` | `title == "yield_cummap"` 인 artifact 를 PPT 슬라이드에 embed |

---

## F. 신규 / 지원 모듈

| 파일 | 역할 |
|---|---|
| `wafer_zones.py` | 9-zone dict + `compute_zone_deltas` / `worst_zone` (이번 PR) |

---

## G. 테스트 / eval

| 파일:라인 | 역할 |
|---|---|
| `eval/test_json_parsing.py:97, 102, 193, 196` | LLM JSON 응답 파싱 테스트 — `map_type="cummap"` 케이스 |

---

## 사내 환경에서 cummap 안 보일 때 의심 지점 (데이터 흐름 순)

1. **DB 단계** — `MAP` 테이블에 `LOT_CD = '<사내 lotcd>'` 인 `MAP_VAL_JSON` row 가 실제로 들어가 있나?
   - `OPER_DET_DESC` 가 `"PT1H TEST"` / `"PT1C TEST"` 정확히 일치하나?
   - 사내에선 `"PT1H"`, `"PT1H_TEST"`, `"pt1h test"` 등 다를 가능성 높음

2. **fetch SQL 단계** — `_query_wafer_data_by_date` SQL 이 사내 스키마와 맞나?
   - 컬럼명 (`MAP_VAL_JSON`, `START_TM`, `LOT_CD`, `OPER_DET_DESC`) 그대로인지
   - 권한 / 테이블명 prefix (스키마.테이블) 차이 점검

3. **파싱 단계** — 사내 `MAP_VAL_JSON` 포맷이 `{"MAP": ["x,y,left_bin,right_bin", ...]}` 와 같나?
   - 다른 컬럼 / 구분자 / 키 (`"DIES"`, `"WAFER_MAP"` 등) 일 가능성
   - 빈 결과면 `_parse_wafer_for_cummap` 출력이 빈 리스트

4. **Early-return 단계** — `yield_viz.py:794-795` 의 `if len(columns) < 3: return ""`
   - `anomaly_params` 가 비어있고 `PT1H` / `PT1C` 두 column 만 있으면 발동 → cummap 자체 안 만들어짐
   - anomaly 검출 (`_detect_anomalies` line 505) 결과를 함께 봐야

5. **Artifact 단계** — `yield_query_agent.py:287` 의 `if cummap_html:` 가 빈 string 이면 artifact 가 state 에 안 실림
   - 응답 streaming 에 `"artifact_type": "html"`, `"title": "yield_cummap"` 이벤트가 나오는지 로그 확인

6. **로깅 확인 우선** — `_build_cummap_grid_html` 내부 `logger.info("[CummapGrid] ...")` (line 814, 817, 821 등)
   - "DB fetch 완료" 로그 + 직전 "period=... key=... → N wafers" 의 N 값 확인
   - N=0 이면 fetch 단계 문제, N>0 인데 cummap 비어있으면 파싱/렌더 단계 문제

---

## 빠른 진단 SQL (사내 DB 에서 직접)

```sql
-- 1. lotcd 별 row / process 분포
SELECT LOT_CD, OPER_DET_DESC, COUNT(*) AS N
FROM MAP
WHERE START_TM >= <조회 시작>
  AND START_TM <  <조회 종료>
GROUP BY LOT_CD, OPER_DET_DESC
ORDER BY 1, 2;

-- 2. MAP_VAL_JSON 샘플 (포맷 확인)
SELECT LOT_ID, WF_ID, OPER_DET_DESC,
       SUBSTR(MAP_VAL_JSON, 1, 200) AS HEAD
FROM MAP
WHERE LOT_CD = '<사내 lotcd>'
  AND ROWNUM <= 3;
```
