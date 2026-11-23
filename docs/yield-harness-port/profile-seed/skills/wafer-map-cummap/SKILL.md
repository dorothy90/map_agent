---
name: wafer-map-cummap
description: Use when drawing wafer die maps or cummaps from MAP data.
metadata:
  yield_ui:
    title: 웨이퍼 누적 맵
    description: 선택한 웨이퍼의 다이 좌표별 불량 분포를 맵으로 비교합니다.
    category: 웨이퍼 맵
    artwork: wafer
---

# Wafer map / cummap 절차

## 맵 조회
- 선택한 제품은 `lot_cd`, 측정 검사는 MAP의 정확한 `OPER_DET_DESC` 값을 `oper`로 전달한다. 이름이 붙은 그룹별 개별 맵은 `wafer_groups`에 각 그룹명(label)·LOT(lot_id)·웨이퍼 번호 목록(wf_ids)을 함께 전달한다.
- load_wafer_maps가 막히면 MAP 테이블 직접 조회로 대체 가능하다: MAP은 LOT_ID, WF_ID(NUMBER), MAP_VAL_JSON, START_TM/END_TM, OPER_DET_DESC(예: 'PT1H TEST') 컬럼이다. MAP_VAL_JSON = {"MAP":["row,col,좌측심볼,우측심볼", ...]} — 첫 필드=행(y), 둘째 필드=열(x), 0~60 정수 좌표(사용자 확인 2026-11-21, x/y를 바꿔 그리면 회전된 맵이 됨). '?'는 pass, 좌·우 심볼 하나라도 '?'가 아니면 fail이다.
- 수율일보의 주차별 BIN 비교는 수율일보 스킬의 render_yield_table.analysis가 같은 제품·검사·기간으로 생성한다.
- 개별 누적맵은 load_wafer_maps에 aggregate와 확정된 제품·검사·기간을 전달한다. 최근 N주는 `recent_weeks`, 명시한 날짜는 `start_time`·`end_time`, 전체 기간은 `all_time=true`로 전달한다. 집계는 전체 선택 범위를 읽는다. 원본 페이지 크기는 개별 raw 조회에서만 지정한다.
- 특정 웨이퍼의 원본 다이가 필요하면 lot_ids 및 선택한 wf_ids를 전달한다. 범위가 빠졌다는 응답을 받으면 실제로 비어 있는 조건을 확인한다.

## cummap 계산
- 좌표별 `fail_rate = fail_count / observation_count`, 분모는 그 좌표 유효 관측 수. 미관측 좌표는 NaN(0 아님). 특정 bin 선택 시 분자만 바꾼다.
- 근거 표 필수 저장: `wafer_map_manifest`(map_type cummap|binmap, grouping_mode selection_set|explicit_groups, numerator_mode defined_fail_bins|selected_bins, bins/result_ids는 JSON 배열 문자열), `wafer_map_members`, cummap이면 `wafer_map_cells` — 모두 `contract_version='wafer-map-evidence/v1'`. 사용자가 '맵만' 요청하면 이 표를 답변에 노출하지 않는다.
- 플롯: `autosize=True`(고정 width 금지), 단일 맵은 `update_yaxes(scaleanchor='x', scaleratio=1)`, y축 `autorange='reversed'`, 여러 패널은 각 y축을 자기 패널 x축에 연결(`for_each_yaxis`에 axis.anchor 사용).

## 기준일/기간
- lot/wafer의 PT1H 측정일은 MAP.END_TM = DF_WF_ALL_PRB_V2.MEASURETIME_START (사용자 확인 정의). '최근 N주' 맵 창은 지정한 제품·검사의 MAP.END_TM 최신 측정일을 포함하는 ISO 주를 기준으로 계산한다.