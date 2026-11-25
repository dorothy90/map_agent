---
name: yield-summary
description: Use when summarizing wafer yield by month/week/day.
metadata:
  yield_ui:
    title: 수율 요약
    description: 월·주·일 수율 추이를 표로 모으고 핵심 변화를 요약합니다.
    category: 리포트
    artwork: trend
---

# 수율 요약 (yield-summary)

월별·주별·일별 수율이나 추이를 정리할 때 쓰는 스킬이다.

## 규칙

1. **사용자 조건 존중**: 사용자가 정한 제품(LOT_CD), 공정/검사(PROCESS), 기간을 그대로 따른다. 조건이 누락되면 조회 전에 `clarify`로 확인하고, 데이터에서 실제 제품 코드를 조회해 선택지를 제시한다. 임의 제품·기간·임계값을 만들지 않는다.
2. **결론 먼저**: 조회 결과에 근거한 핵심 결론을 한국어 한두 문장으로 먼저 쓴다. 결론의 수치는 실제 조회 결과에서 온 것이어야 한다.
3. **표는 요청 순서대로**: 결론 아래에 같은 조회 결과의 월별·주별·일별 표를 사용자가 요청한 순서 그대로 배치한다. 기간 리포트는 `render_yield_table`의 artifact_refs.markdown을 그대로 사용하고 본문에 표를 다시 만들지 않는다.
4. **버킷별 수치의 출처 구분**: 기간 합계는 report_totals, 버킷별 lot/wafer 수는 monthly_counts/weekly_counts/daily_counts에서만 인용한다. 버킷 수를 더해 기간 합계로 쓰지 않는다.
5. **고정값 금지**: 이 스킬에 특정 제품, 날짜, 수율 수치, 임계값을 기록하지 않는다. 예시 값은 자리표시자로만 남기고 실제 값은 매번 조회한다.
6. **미확인 사항 보존**: 재검사 처리, 수율 분모, 시간대 등 미확인 정의는 임의로 확정하지 않고 도구 계약·도메인 노트의 조회 규칙을 따른다.

## 절차

1. 제품·공정·기간 확정 (누락 시 한 번의 `clarify`).
2. 기간 조회: `render_yield_table`에 lot_cd+period(기존 report_scope_id가 있으면 그것) 전달. period는 recent(weeks/months/days 중 정확히 하나) 또는 명시적 버킷 목록. grains는 표시 집계 단위(week/month/day).
3. 그룹이 필요하면 저장된 lot_population을 read_result로 읽어 멤버십을 만들고 같은 report_scope_id로 그룹/버킷 렌더링.
4. 답변: 한국어 결론 1–2문장 → artifact 표(요청 순서) → 필요한 상세 근거.
