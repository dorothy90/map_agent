# 수율 도메인 의미

- 검사 단계가 명시되지 않은 “수율”은 PT1H를 뜻한다.
- 웨이퍼 PT1H 수율은 DF_DIE_TO_WF_YLD의 PARAM='PT1H',
  PROCESS='PT1H TEST' 값이며, 0–1 비율이다.
- PT1C도 같은 테이블에서 PARAM='PT1C',
  PROCESS='PT1C TEST'를 사용하며, 0–1 비율이다.
- bin 값은 맵 질문에만 사용한다. 수율·불량 수치는
  DF_WF_YLD 계열 웨이퍼 테이블에서 가져오며 맵으로 계산하지 않는다.
- bin은 왼쪽 문자와 오른쪽 문자를 결합한 두 글자다.
  결합한 쌍이 DF_SYMBOL_BIN.REGEX_PATTERN에 매칭될 때만 fail이다.
- 팹아웃 시각은 MAP.END_TM이며 KST 기준이다.
- lot은 LOTID를 뜻하며, LOT_CD는 제품 코드다.
- DF_DIE_TO_WF_YLD에서 한 웨이퍼·PROCESS의 bin PARAM(X(Y) 형태)과 pass PARAM(PT1H/PT1C, '??' 비율)의 합은 1이다. item PARAM(VTH, FMAX 등)은 테스트 측정값이며 bin이 아니므로 합이 1일 필요가 없다.
