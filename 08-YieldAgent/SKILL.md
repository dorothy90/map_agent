# 08-YieldAgent Skills

## yield-query
- **설명**: 반도체 수율(yield) 데이터 조회 및 분석
- **트리거**: 수율, pt1h, 주간/월별/일별 파라미터, 이상감지
- **입력**: lotcd, ref_date, unit, periods, filter_params, yield_lot_ids, yield_groupkey
- **출력**: HTML 테이블 + scatter plot + LLM 분석 결과
- **도구**: Oracle SQL 직접 호출 (pt1h/pt1c/gms 3테이블 병렬)

## wads-report
- **설명**: WADS(Weekly Aggregation Data System) 열화 검출 리포트 조회
- **트리거**: 열화, 검출, WADS 리포트
- **입력**: lotcd, wads_end_tm
- **출력**: HTML 리포트 (Layer1 전수 집계 테이블)
- **도구**: wads_query_data, wads_get_html_report (@tool → 서브그래프 LLM)

## wafer-map
- **설명**: 웨이퍼 맵(binmap/cummap) 시각화
- **트리거**: 웨이퍼 맵, binmap, cummap, 누적 패스레이트
- **입력**: map_lot_id, map_lot_ids, map_wf_ids, map_groupkey, map_type, map_bin_type
- **출력**: 웨이퍼 맵 HTML 시각화
- **도구**: Oracle SQL 직접 호출
