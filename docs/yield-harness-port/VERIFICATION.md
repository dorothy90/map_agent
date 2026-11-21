# 반입 전 검증 기록

소스 기준 `58ad8ee71f113edcbab4c471dc0aac25f0330cd2`. 검증 호스트는 macOS Apple Silicon이다. 다음 결과는 외부 개발용 더미 환경에서 얻었다. 사내 레포와의 실제 병합 및 사내 DB 결과는 내부 담당자가 04에 기록한다.

| 항목 | 실제 실행 및 결과 |
|---|---|
| 소스 범위 | Git 객체로 아카이브를 두 번 만들어 바이트 일치. harness 2,890개, harness_console 1,934개, 공용·루트 및 기타 경로 28개. 총 4,852개, 원본 크기 합계 73,797,160바이트. required 4,014, test 811, optional 15, docs 12 |
| 마지막 변경 | 기준 커밋과 직전 커밋의 경로 차이는 144개. 추가 42, 수정 96, 삭제 6. 현재 반입 파일에 포함되는지 CHECKPOINT-144.tsv에 표시 |
| 원본 무결성 | 기준 Git 트리의 선별 경로·바이트·실행 비트를 FILES.tsv와 대조. 엔진 매니페스트 2,656개 해시 확인 |
| 사내 병합 도우미 | 임시 git 레포에 서로 다른 common.py를 둠. 충돌까지 ADD를 지정했을 때 쓰기 전 중단. MERGE로 남기고 나머지 4,851개 파일을 ADD한 뒤 모든 바이트·실행 비트 일치, 기존 common.py 보존. 재실행으로 추가한 파일 0개 |
| 소스만 풀어 기동·빌드 | 새 venv에 uv 잠금 버전 273패키지 설치, bundled Hermes import 성공. 대표 백엔드 테스트 47개·프론트 테스트 39개 통과. npm 잠금 설치 후 TypeScript/Vite production build 성공 |
| 실제 DB 리포트 | 추출본에서 개발 Oracle 연결만 주입하고 ReportAnalysis(months=3, weeks=4, days=3, parameters_per_direction=3, bins_per_test=10) 경로 실행. 4SA에서 success, 월 3/주 4/일 3 버킷, 열화·개선 순위 6개/산점도 원자료 2,400점, PT1H TEST·PT1C TEST 주차별 BIN/델타맵 산출물 각 1개. 표 3개·산점도 1개·검사별 맵 2개 합계 6개. 사내 DB 값 또는 새 반입본의 브라우저 실측으로 간주하지 않음 |
| 같은 커밋의 실제 채팅 | 이전 별도 체크아웃에서 실제 브라우저→모델→네이티브 list_tables/query_db/read_result→새 채팅 완료. 개발 Oracle 독립 집계와 4SA 190 LOT/3,950장, 4SS 187 LOT/3,950장 일치. 기존 체크아웃에서는 별도 선택 백엔드 테스트 49개와 프론트 테스트 39개 통과. 위 추출본 테스트와 중복 집계하지 않음 |
| 프로필·메모 | 원본 업무 SKILL.md 4개와 동봉본 바이트 일치. 빈 프로필에서 Hermes 카탈로그가 4개 활성 상태로 읽음. 기본 설치·더미 메모 선택 설치·재실행·목적지 충돌 검사, 하위 리소스와 SQLite WAL 포함 스냅숏/무결성 검사 통과 |
| 저장소 계약 | 개발 Oracle에 읽기 전용, 9개 테이블/뷰의 0행 조회 통과. 분리한 시험 MongoDB에 데이터를 만들어 mongodump와 mongorestore 실행 후 GridFS 원본 ObjectId·파일 바이트 일치. 실제 과거 사용자 DB 전체를 반입한 시험은 수행하지 않음 |
