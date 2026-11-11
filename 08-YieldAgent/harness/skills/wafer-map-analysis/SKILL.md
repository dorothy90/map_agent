---
name: wafer-map-analysis
description: 웨이퍼 binmap·누적맵·기간별 차이와 공간적 열화 분포를 비교할 때 사용한다.
---

# 웨이퍼 맵 분석

`load_wafer_maps`의 schema에서 LOT/WF 또는 제품·기간 범위, 검사 단계, bin 선택을 확인한다. 보고서 후속 요청이면 보고서에 연결된 실제 식별자를 재사용한다. 선택 필터와 사용자가 명시한 집계 그룹을 구분한다.

전체 pass의 ?-bin 정의와 특정 불량 bin의 분포를 구분한다. 평균 pass 값의 분모가 웨이퍼별 평균인지 위치별 평균인지 결과의 metric 정의를 읽는다. 빈 좌표와 측정되지 않은 셀을 0%로 처리하지 않는다.

cummap은 공통 좌표별 `fail_count / observation_count`이며 단일 wafer도 같은 정의를 쓴다. 여러 lot의 lot별/전체 합산이 모호하고 결과를 바꾸면 질문한다. `wafer_map_manifest`·`wafer_map_members`와 cummap의 `wafer_map_cells`를 저장하고, 맵만 요청한 답변에는 근거 표를 노출하지 않는다.

`compare_wafer_maps`는 입력한 기간들을 보존하는 비교 도구다. 비교 기간의 웨이퍼 수·측정 정책·좌표 정합성을 확인한다. 원본 조회가 제한됐으면 일부 표본의 비교로 설명한다.

이미지의 인상만으로 수치를 만들지 않는다. 반환된 평균·차이·영역별 수치와 이미지가 같은 입력에서 생성됐는지 확인한다. 공간 패턴은 관련 공정/이력 조사의 단서이며 단독 인과 증거가 아니다.

불량 수율 하위 wafer 맵은 `query_defect_yield(include_binmap=true)`로 순위와 같은 측정 건에서 생성할 수 있다. 선정 개수와 rendered_wafer_ids를 확인한다. 이 도구의 팹아웃 시각은 맵 테이블 end_tm이며 파라미터 측정 시각과는 다른 시간 기준이다. 여러 불량 bin의 합집합 수율과 각 항목별 수율을 구분해 설명한다.

근거 표를 저장할 때 `emit_table(manifest_df, name="wafer_map_manifest")`, `emit_table(members_df, name="wafer_map_members")`, `emit_table(cells_df, name="wafer_map_cells")`로 이름을 지정한다. 각 맵 패널의 y축은 같은 패널의 x축에 `scaleanchor`로 연결하고 `scaleratio=1`로 설정한다. 여러 패널의 y축을 모두 첫 x축에 연결하지 않는다.
