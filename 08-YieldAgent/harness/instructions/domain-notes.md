# 수율 업무 지식 — 사용자 편집 문서

## 출처와 적용 범위

아래 사내 업무 정의는 사용자가 직접 설명한 내용(2026-11-06)이다. 공개된 반도체 일반 설명과 구분하며, 다른 회사·공정에도 같은 의미라고 일반화하지 않는다. 사용자 최신 정정과 실제 데이터의 명시적 정의를 우선한다. 이 문서는 업무 배경이지 DB 조회 결과나 저수율 판정 정책 자체가 아니다.

## 사용자가 확인한 사내 업무 정의

- 데이터 단위(사용자 확인, 2026-11-06): MAP은 die level data이고, DF_WF_ALL_PRB_V2는 wafer level data다. wafer 수율·파라미터 조회에는 DF_WF_ALL_PRB_V2만 사용하며, DF_DIE_TO_WF_YLD는 더 이상 사용하지 않으므로 조회하지 않는다.
- 시각 대응(사용자 확인, 2026-11-06): 동일한 대응 테스트의 MAP.END_TM과 DF_WF_ALL_PRB_V2.MEASURETIME_START는 같은 값이다. MAP의 날짜를 사용했다는 이유만으로 날짜가 틀렸다고 판단하지 않는다. 두 테이블의 데이터 단위 차이와 시각 값의 동일성은 구분한다. 대응 키나 재검사 간 매칭 규칙은 이 설명만으로 추정하지 않는다.
- 일별 wafer 수율 조회에는 DF_WF_ALL_PRB_V2의 wafer 단위 데이터를 사용한다. die 위치·심볼·map 분석에는 MAP을 사용한다. MAP 행 수를 wafer 수로 세거나 wafer 수율 조회를 die 데이터 조회로 대체하지 않는다.
- 데이터 단위만으로 테이블의 한 행이 고유 wafer 하나 또는 고유 die 하나라고 단정하지 않는다. 상세 컬럼, 식별 키, 집계·재검사 규칙은 실제 schema와 확인된 업무 정의를 따른다.
- 검사 단계가 명시되지 않은 “수율”은 PT1H를 뜻한다. 다른 test가 명시되면 해당 test를 따른다.
- FABOUT 이후 PT1H / PT1C / PT2H 등 여러 test를 거친다. 나열만으로 고정된 검사 순서나 모든 자재에 대한 필수 검사 여부를 확정하지 않는다.
- “오늘 팹아웃 자재”는 통상 오늘 테스트가 찍힌 자재를 언급하는 업무 표현이다. 실제 FABOUT 날짜와 테스트 날짜는 별개다. 요청 맥락에 맞는 테스트 시각 컬럼을 확인하고, 둘을 자동으로 동일시하지 않는다.
- test별 bin rate를 모두 합치면 1이 된다. die 검사 이후 left와 right SDA symbol을 부여하며, 예를 들어 `??`는 pass이고 각 심볼별 fail이 지정된다. 전체 심볼별 매핑은 아직 제공되지 않았다.
- test별 item parameter도 있다. 이는 screen 값이 아니라 품질을 의미할 수도 있으며, die 수율 분석 업무에서 중요하다. item parameter를 bin rate와 동일하게 취급하거나 합이 1이라고 가정하지 않는다.

## 아직 확인되지 않은 사항

- PT1H / PT1C / PT2H 명칭의 풀네임과 H/C 등의 의미를 추정해 확정하지 않는다.
- 재검사 선택 기준, 최초/최종 검사 우선순위, 중복 처리 기준은 미확인이다.
- 수율과 bin rate의 정확한 분모, 제외 die, 결측·미측정 처리 기준은 미확인이다. 아래 cummap 구현의 좌표별 분모와 업무 수율 분모를 혼동하지 않는다.
- `??` 이외의 전체 left/right SDA 심볼과 fail/bin 매핑, 매핑 테이블의 업무상 권위는 미확인이다.
- 저수율 판정 임계값·기준 출처는 미확인이다. 85%를 기본 기준으로 사용하지 않는다.
- 테스트 날짜 컬럼, 시간대, “오늘”의 마감 시각은 실제 데이터 정의를 확인한다. 미래 날짜나 최신 데이터 날짜로 요청 날짜를 임의 대체하지 않는다.

## 기존 구현 참고 — 사용자 확인 정의와 구분

다음은 기존 문서에 있던 구현 설명을 보존한 것이다. 사용자 확인 정의나 공개 표준으로 승격하지 않는다. 실제 schema·도구 계약과 대조하고, 심볼 매핑·시간대와 실제 FABOUT 시각의 정의는 미확인이다. 두 테스트 시각 값의 동일성은 위 사용자 확인 정의를 따른다. 아래 설명이 위 미확인 사항을 해소한 것으로 취급하지 않는다.

- 검사 단계가 명시되지 않은 “수율”은 PT1H를 뜻한다.
- 웨이퍼 PT1H 수율은 DF_WF_ALL_PRB_V2의 PARAM_NM='PT1H',
  PROCESS='PT1H TEST' 값이며, 0–1 비율이다.
- 오늘 테스트된 자재의 PT1H 수율 조회는 DF_WF_ALL_PRB_V2에서 PARAM_NM='PT1H', PROCESS='PT1H TEST'와 테스트 측정 시각 MEASURETIME_START의 요청 날짜 범위를 함께 확인한다. 두 테이블의 대응 테스트 시각 값은 같지만, wafer 수율 질문은 wafer level 테이블을 직접 조회하는 것이 적합하다. MAP에서 고른 lot만으로 날짜 조건 없이 수율을 조회하면 요청한 테스트 범위를 보존했다고 보장할 수 없다.
- 일별 조회는 DF_WF_ALL_PRB_V2에서 PROCESS와 `MEASURETIME_START >= :start_dt AND MEASURETIME_START < :end_dt`를 함께 필터링하고 PARAM_NM별로 집계한다. PT1H만 묻는 경우에는 PARAM_NM='PT1H'를 선택한다. MEASURETIME_START가 실제 FABOUT 시각이라는 뜻은 아니다. 업무 시간대와 현재까지/하루 전체의 마감 기준은 별도로 확인한다.
- PT1C도 같은 테이블에서 PARAM_NM='PT1C',
  PROCESS='PT1C TEST'를 사용하며, 0–1 비율이다.
- 수율·불량 수치는
  DF_WF_YLD 계열 웨이퍼 테이블에서 가져오며 맵으로 계산하지 않는다.
- bin은 왼쪽 문자와 오른쪽 문자를 결합한 두 글자다.
  결합한 쌍이 DF_SYMBOL_BIN.REGEX_PATTERN에 매칭될 때만 fail이다.
- cummap은 선택된 wafer·measurement의 공통 좌표별
  `fail_rate = fail_count / observation_count`다. 좌표에 유효 관측이 없으면
  NaN이며 missing die는 pass가 아니다. 단일 wafer도 분모 1의 0/1 rate다.
- 특정 bin cummap은 지정 bin 집합만 분자에 포함하고 좌표별 유효 관측
  분모는 바꾸지 않는다. binmap은 cummap과 다른 지표다.
- 재측정은 measurement_id로 구분하며 어떤 측정을 포함했는지 정책과
  `(lot_id, wf_id, measurement_id)` 구성으로 남긴다.
- MAP.END_TM은 대응 테스트의 DF_WF_ALL_PRB_V2.MEASURETIME_START와 같은 값이라는 사용자 정의를 따른다. 이 동일성이 실제 FABOUT 날짜와 테스트 날짜까지 같다는 뜻은 아니다. 시간대와 대응 키·재검사 규칙은 별도 확인한다.
- lot은 LOT_ID를 뜻하며, LOT_CD는 제품 코드다. lot 수는 distinct LOT_ID로 세고(스플릿 lot도 별도 lot), wafer 수는 distinct GORUPKEY로 센다.
- DF_WF_ALL_PRB_V2에서 한 웨이퍼(GORUPKEY)·PROCESS의 bin PARAM_NM(X(Y) 형태)과 pass PARAM_NM(PT1H/PT1C, '??' 비율)의 합은 1이다. CFTA는 bin(B)이지만 이 합에 포함하지 않는다. item PARAM(VTH, FMAX 등)은 테스트 측정값이며 bin이 아니므로 합이 1일 필요가 없다.

## 테이블 설명 (사용자 확인, 2026-11-10)

### DF_ALL_PARA_PRB (제품·공정별 파라미터 목록)

- 제품(LOT_CD)과 검사공정(PROC_GBN_CD: PT1H, PT1C)마다 어떤 파라미터(PARAM_NM)가 있는지 담은 목록이다. PARAM_NM은 DF_WF_ALL_PRB_V2의 PARAM_NM과 같은 이름이다.
- PARAM_KIND_CD: I는 item(VTH, FMAX 같은 측정값), B는 bin(X(Y) 모양, pass bin PT1H·PT1C, CFTA)이다.
- PARAM_SEQNO: 공정 안에서의 표시 순서다.
- YIELD_TABLE: 렌더러가 DF_ALL_PARA_PRB에서 해당 LOT_CD의 TRIM(YIELD_TABLE)='o' 파라미터를 직접 조회한다. PT1H 다음 PT1C, 각각 PARAM_SEQNO 순서로 정확한 PARAM_NM을 표시하며 x는 제외한다. 주차·그룹 보고서 파라미터는 x100 소수 1자리, % 없이 표시한다. GMS는 원값 소수 1자리, lot/wafer 수는 정수다. 답변에는 전체 파라미터가 아니라 YIELD_TABLE='o' 파라미터라고 설명한다.
- PROC_GBN_CD는 'PT1H'처럼 TEST 없이 적는다. DF_WF_ALL_PRB_V2와 연결할 때는 PROC_GBN_CD 뒤에 ' TEST'를 붙여 PROCESS('PT1H TEST')와 맞춘다.

### DF_WF_ALL_PRB_V2 (wafer별 수율·파라미터 값)

- 한 행은 wafer 하나·검사공정(PROCESS) 하나·파라미터(PARAM_NM) 하나의 값(VALUE)이다. wafer 수율과 파라미터 조회는 이 테이블을 사용한다.
- LOT_CD(PRODUCT와 같은 값)는 제품 코드다.
- ALIAS_LOT_ID는 원래 lot이다. LOT_ID는 ALIAS_LOT_ID와 같거나, 스플릿된 lot이면 ALIAS_LOT_ID 뒤에 문자 하나가 붙는다(예: 4SS7T9A → 4SS7T9AA). lot 수는 LOT_ID 기준으로 센다.
- WFID는 wafer 번호이며 한 자리 번호는 앞에 0 없이 적힌다(예: '7'). ALIAS_LOT_ID_N은 ALIAS_LOT_ID.두 자리 wafer 번호(예: 4SSTQXD.05)이고, GORUPKEY는 LOT_ID.두 자리 wafer 번호(예: 4SS7T9AA.10)다. wafer 수는 distinct GORUPKEY로 센다. 키를 직접 만들 때 WFID를 그대로 붙이지 말고 GORUPKEY·ALIAS_LOT_ID_N 컬럼을 사용한다.
- PROCESS는 'PT1H TEST' 또는 'PT1C TEST'다.
- PARAM_NM과 PARAM_SEQNO는 DF_ALL_PARA_PRB의 PARAM_NM·PARAM_SEQNO와 같다. BINITEM은 I=item, B=bin이며 DF_ALL_PARA_PRB.PARAM_KIND_CD와 같은 구분이다.
- WEEK_HYNIX는 ISO 주차 'YYYY-WW'(월요일 시작, Oracle IYYY-IW)로 측정 날짜의 ISO 주차와 같다. YEAR·MONTH·DAY는 측정 날짜이며 TRUNC(MEASURETIME_START)와 일치한다. 최근 N주/월/일은 LOT_CD의 PROCESS='PT1H TEST'에서 MAX(TO_DATE(YEAR||LPAD(MONTH,2,'0')||LPAD(DAY,2,'0'),'YYYYMMDD'))를 기준으로 하며 LAST_UPDATE나 호스트 시계를 사용하지 않는다. 해당 행이 없으면 기간을 만들지 않는다.
- MEASURETIME_START·MEASURETIME_END는 테스트 측정 시작·종료 시각이다.
- GMS 테이블은 DF_GMS_YIELD_WEEKLY / DF_GMS_YIELD_MONTHLY / DF_GMS_YIELD_DAILY이며 PERIOD_DATE는 각각 YYYYWW / YYYYMM / YYYYMMDD다(버킷의 대시 제거). 모두 LOTCODE, FAB_YIELD, PRB_YIELD, CUM0_YIELD를 제공하며 CUM2/PNT 원천 컬럼은 없다.
- 최근 N은 기준 측정일의 버킷까지 연속 ISO 주/달력 월/날짜를 포함한다. 최근 N개월의 주별 결합은 첫 달 1일~기준 측정일에 겹치는 모든 ISO 주를 포함하며, 집계 날짜는 그 닫힌 구간으로 제한한다.
- 그 밖의 컬럼(T_ET~E1_E2 영역 값, AREA_STATUS, REPAIR_VAL, REJ_YN, TESTER_MODEL 등)의 업무 의미는 아직 확인되지 않았으므로 추정해서 쓰지 않는다.

### DF_GMS_YIELD_WEEKLY (제품별 주차 GMS)

- 한 행은 제품(LOTCODE) 하나·주차(PERIOD_DATE) 하나의 FAB_YIELD, PRB_YIELD, CUM0_YIELD다.
- PERIOD_DATE는 대시 없는 'YYYYWW' 문자열(예: '202646')이다. 문자열이라 '>= 2026-46' 같은 대시 형식 비교는 모든 주차에 맞아 버리므로, 필요한 주차를 'YYYYWW'로 바꿔 IN으로 조회한다.

## 계속 지식을 추가하는 방법과 사내 이식

이 파일 `08-YieldAgent/harness/instructions/domain-notes.md`를 UTF-8 Markdown으로 편집한다. 새 지식에는 정의, 적용 제품/검사 범위, 출처·확인일을 함께 적고 미확인은 그대로 표시한다. 공개 일반 설명을 추가할 경우 별도 절에 출처와 적용 한계를 기록하며 사내 정의를 덮어쓰지 않는다. 현재 공개 일반 설명을 근거로 추가한 정의는 없다.

기존 `load_native_instructions()`가 `native.md` 뒤에 이 파일 전체를 붙이고, Hermes의 새 실행 시작 시 worker 지침으로 전달한다. 스킬을 별도로 호출할 필요가 없다. 저장 후 다음 실행부터 반영되며 이미 실행 중인 요청에는 소급 반영되지 않는다. 이 MD만 수정할 때 서버 재시작은 필요 없다. 이 경로는 native Hermes용이며 legacy 분석 경로 전체에 적용된다고 가정하지 않는다.

사내 이식 시 app 아래 배포 레포의 같은 상대 경로에 이 파일을 함께 배포한다. 별도 NAS/MongoDB 지식 저장소나 profile 스킬 복사본을 만들 필요가 없다. 읽기 전용 이미지로 배포한다면 파일 수정 후 이미지를 재배포하거나 해당 파일을 배포 설정에서 마운트한다. 이 문서는 격리·인증·도구 권한을 변경하지 않는다.
