# 08-YieldAgent Skills

## Architecture

```
START → rewrite → planner → supervisor ⟷ [yield_agent, wads_agent, map_agent, fail_history_agent, lot_history_agent, ppt_export] → END
```

- **rewrite**: 사용자 메시지 리라이팅 + tool-calling (compute_wafer_ids)
- **planner**: 복합 질문 → TaskItem 리스트 분해 (PlanResponse)
- **supervisor**: ReAct 스타일 멀티스텝 라우터 (RouteResponse structured output)
- 라우팅: `Command(goto=...)` 기반, supervisor ← agent 루프

---

## yield-query
- **설명**: 반도체 수율(yield) 데이터 조회 및 분석
- **트리거**: 수율, pt1h, pt1c, 주간/월간/일별 파라미터, 이상감지
- **입력**: lotcd, ref_date, unit, periods, filter_params, yield_lot_ids, yield_groupkey
- **출력**: HTML 테이블 + scatter plot + cummap grid + LLM 분석 결과
- **모드**:
  - **period 모드**: lotcd + ref_date 기준 기간별 집계 (pt1h/pt1c/gms 3테이블 병렬)
  - **lot 비교 모드**: yield_lot_ids 또는 yield_groupkey로 개별 lot/wafer 비교
- **도구**: Oracle SQL 직접 호출 (`_fetch_periods`, `_fetch_wafer_scatter`, `_fetch_lot_sql`)
- **아티팩트**: yield_table, yield_scatter, yield_cummap

## wads-report
- **설명**: WADS 열화 검출 리포트 조회
- **트리거**: 열화, 검출, WADS 리포트
- **입력**: lotcd, wads_start_tm, wads_end_tm (기간 범위 또는 단일 날짜)
- **출력**: HTML 리포트 (렌더링 우선순위: reports > sql_result > query)
- **구현**: create_react_agent 기반 ReAct 서브그래프 (WADS_TOOLS)
- **모델**: RETRIEVE_CHAIN_MODEL
- **아티팩트**: wads_report, wads_sql_result, wads_query

## wafer-map
- **설명**: 웨이퍼 맵(binmap/cummap) 시각화
- **트리거**: 웨이퍼 맵, binmap, cummap, 누적 패스레이트
- **입력**: map_lot_id, map_lot_ids, map_wf_ids, map_groupkey, map_type(binmap|cummap|all), map_oper(PT1H|PT1C)
- **출력**: matplotlib PNG → base64 인코딩 HTML
- **도구**: Oracle SQL 직접 호출 (`_query_wafer_data`)
- **아티팩트**: map_artifacts

## fail-history
- **설명**: 불량이력 RAG 검색 + HTML 리포트
- **트리거**: 불량이력, 불량 검색, TWT, IOFF 등 불량유형
- **입력**: dh_query(자유 텍스트), dh_fail_type(불량유형), dh_cause_oper(원인공정), lotcd
- **출력**: HTML 리포트 (Jinja2 템플릿 기반)
- **구현**: create_react_agent 기반 ReAct 서브그래프 (FAIL_HISTORY_TOOLS)
- **검색**: OpenSearch 하이브리드 (BM25 + kNN)
- **모델**: RETRIEVE_CHAIN_MODEL
- **아티팩트**: fail_history_report

## lot-history
- **설명**: LOT 종합 이력 조회 (5개 테이블)
- **트리거**: LOT 이력, FDC알람, Q-TIME초과, Trouble, Future Action, Sample Split
- **입력**: lh_lot_ids (복수 LOT_ID, 쉼표 구분)
- **출력**: 5개 섹션 HTML 리포트 (위험도 레벨: red/yellow/green)
- **Oracle 테이블**: DF_FDC_ALARM, DF_QTIME_OVER, DF_TROUBLE_LOT, DF_FUTURE_ACTION, DF_SAMPLE_SPLIT
- **구현**: create_react_agent 기반 ReAct 서브그래프 (LOT_HISTORY_TOOLS)
- **모델**: RETRIEVE_CHAIN_MODEL
- **아티팩트**: lot_history_report

## ppt-export
- **설명**: 누적 아티팩트를 PPTX 리포트로 변환
- **트리거**: PPT, 리포트 내보내기, PPT로 만들어줘
- **입력**: state에 누적된 yield/wads/map/fail_history artifacts
- **출력**: PPTX 파일 (바이트 + 파일 경로)
- **구현**: YieldReportPPTBuilder.build_compact() — file:// 참조 자동 해소
- **아티팩트**: pptx (다운로드 링크로 변환)
