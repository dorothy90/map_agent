# 사내 레포에 기존 수율 분석 하네스 파일 옮기기

[GitHub 작업 브랜치](https://github.com/dorothy90/map_agent/tree/codex/goal-driven-harness)에서 **소스와 이 설명서**를 함께 받는다. 복제한 브랜치의 HEAD를 한 번 기록하고 해당 커밋의 설명서·FILES.tsv·소스를 끝까지 함께 사용한다. 현재 화면은 `harness_console`, 웹 채팅과 모델 평가의 실행 경로는 `stock_chat`, Hermes 원본은 `5894e750c3dba980d43b25aa29e3958149ba2c61`이다. 사내 레포 정보와 내부에서 만든 비교표·diff·로그는 사내에만 둔다.

일반 채팅에서 Hermes가 직원 프로필의 대화 이력을 복원하고 도구를 호출하며 최종 답변을 작성한다. 백엔드는 직원별 접근 권한을 확인하고 전용 수율 도구 6개를 등록한다. 일반 웹 채팅에서는 Hermes 원본 자동 검색이 직원에게 허용된 도구를 불러온다. 공용 파일 읽기·검색·이미지 분석, 작업 위임, 할 일 목록도 Hermes로 실행한다. [공용 폴더 설정과 도구별 권한](02-file-map.md#shared-files) · [두 채팅 검증 기록](VERIFICATION.md#shared-files) [자동 검색과 공정명 처리 검증](VERIFICATION.md#native-discovery) · [GLM 후속 질문 화면](VERIFICATION.md#glm-map)

Windows 개발 PC에서 새 채팅으로 맵 조회와 엑셀 다운로드를 검증할 때는 [Windows 실채팅 절차](06-windows-chat.md)를 따른다.
Windows는 로컬 테스트 전용이며 운영 배포는 Ubuntu에서 `local` 백엔드와 `bwrap`을 사용한다.

## 이전 Windows 이식본에서 이어가기

Windows 맵·엑셀 지원 버전(`f7232c7c`)까지 옮겼다면, 다음 기능 커밋(`9ff7d746`)의 차이는 채팅 조건 처리 파일 6개다. 같은 대화에서 사용자가 확정한 조건은 후속 질문에 이어 쓴다. 답변 첫 줄에 `이번 대화 기준: …`으로 이어받은 조건과 출처를 밝힌다. `session_search`로 다른 대화에서 찾은 조건은 확인 카드의 추천 선택지일 뿐이다. 사용자가 고르기 전에는 조회에 쓰지 않는다. 저수율이나 그룹 분할 기준은 현재 대화에서 확인되지 않았으면 항상 `clarify`로 묻는다.

`9ff7d746`까지 옮겼다면 다음 커밋은 `cb58209b`다. 안 쓰는 `run_python`과 Docker 실행 경로를 제거한다. 웹 채팅의 Python은 Hermes 원본 `execute_code`와 `read_result(export_for_python=true)`를 사용한다. 운영 분석 격리는 Ubuntu의 `bwrap`이며, `runtime/container.py`와 `runtime/Dockerfile`은 더 이상 없다. 이 커밋을 포함한 HEAD를 이식할 때는 같은 커밋에서 다시 만든 `FILES.tsv`를 사용한다.

| 사내에서 비교할 경로 (`08-YieldAgent/harness/` 기준) | 확인할 내용 |
|---|---|
| `instructions.py`, `instructions/behavior.md` | 매 턴 새 지침 파일을 읽는다. 사내 패키지에도 두 파일을 같은 상대 경로로 배치한다. |
| `chat_tool_helpers.py` | 이전 대화 검색 결과가 현재 대화의 확정 조건인지 모델이 구분할 수 있게 표시한다. 직원별 검색 범위를 보존한다. |
| `tests/test_instructions.py`, `tests/test_domain_tool_registry.py`, `tests/test_hermes_chat.py` | 지침 로드, 검색 도구의 실제 반환값, 채팅에서 모델에 전달되는 입력을 검사한다. |

어디까지 옮겼는지 불분명하면 사내 레포에서 최신 [첫 지시문](05-luna-terra.md#첫-지시문)을 실행한다. 이전 비교표는 보관하고, GitHub에서 새로 받은 참조본의 `FILES.tsv`로 전체 비교표를 다시 만든다. 사내 경로가 다르거나 자체 수정이 있는 파일은 내용을 대조해 병합한다.

## 무엇을 열어야 하나

[2026-11-25 이식 재검증](VERIFICATION.md#port-2026-11-25)에서는 실제 4SA 조회 1,200행, 수율일보의 표·산점도·BIN 맵과 업무 도구를 확인했다. 2026-11-24 실제 웹 채팅에서 수율 조회, 스킬 호출, 후속 질문을 확인했다. 도구 없는 모델 평가와 가상 자료 평가도 같은 Hermes 진입점에서 완료됐다. Ubuntu 직원별 파일 격리는 배포 전 필수 확인 항목이다. [웹 콘솔 정리 검증](VERIFICATION.md#unused-harness-cleanup) · [Hermes 실행 경로 검증](VERIFICATION.md#stock-only) · [Python 변수와 엑셀 다운로드 검증](VERIFICATION.md#web-python-delivery) · [Ubuntu 통과 기준](04-acceptance.md#ubuntu-배포-전-직원별-파일-격리)

| 질문 | 열 파일 | 들어 있는 내용 |
|---|---|---|
| 지금까지 만든 기능이 어느 파일에 있나? | [기능별 파일 지도](02-file-map.md) | Hermes 원본, 연결 코드, 전용 도구 6개, 공용 모듈, 웹, 테스트, 별도 프로필을 나눠 설명 |
| 기존 앱 체크포인트에서 바뀐 파일은? | [CHECKPOINT-144.tsv](CHECKPOINT-144.tsv) | 기존 앱 체크포인트 `58ad8ee7`과 그 직전 커밋의 차이 144개. 추가 42·수정 96·삭제 6. 각 경로·역할·현재 반입 여부·처리 방법 |
| 실제로 옮길 파일 전체는? | [FILES.tsv](FILES.tsv) | 체크아웃한 Git 커밋에서 선별한 파일. 파일마다 원본 경로·권장 목적지·용도·역할·SHA-256·권한·크기. 경로는 Git 저장소 루트 기준 |
| 사내 파일과 충돌하면? | [01 소스 병합](01-merge.md) | 읽기 전용 참조 폴더 생성 → 사내 대응표 작성 → 새 파일 복사 → 기존 파일 수동 병합 → 검증·복구 |
| 수율일보 등 만든 스킬과 메모도 필요한가? | [03 사용자 데이터](03-runtime-data.md) | 업무 스킬 4개 원본, 기존 개인 메모·대화·산출물까지 이관할 때의 별도 절차 |
| 어디까지 옮겼는지 어떻게 확인하나? | [04 이식 검수](04-acceptance.md) | 파일 단위 완료 기준, 이미 실행 가능한 사내 환경이 있을 때의 업무 검증표 |
| Luna/Terra에게 뭐라고 시키나? | [05 작업 지시문](05-luna-terra.md) | 사내에서 그대로 붙여넣을 첫 지시, 단계별 후속 지시, 진행기록 양식 |

## 사내에서 시작하기

1. Luna 또는 Terra에서 **대상 사내 레포**를 작업 폴더로 열고 [첫 지시문](05-luna-terra.md)을 붙여넣는다.
2. [소스 병합 1~3절](01-merge.md)에 따라 GitHub 브랜치를 사내 레포 밖에 복제한다. 기록한 커밋과 이식 목록의 경로·내용·권한이 맞는지 확인한 다음 사내 레포와 비교한다. 직접 GitHub 접근이 제한된 곳은 같은 브랜치와 커밋을 제공하는 사내 반입 미러 주소를 쓴다.
3. 첫 결과는 새 파일·동일 파일·충돌 파일 수와 주요 충돌 사유다. 복사와 수동 병합은 이 비교표를 검토한 다음 4절부터 진행한다. Git과 Python 3.11 이상이면 첫 비교를 실행할 수 있다.
4. 이식 검수는 [04](04-acceptance.md), 업무 스킬과 선택 데이터는 [03](03-runtime-data.md)을 따른다. 파일 검색은 로컬 체크아웃의 `docs/yield-harness-port/FILES.tsv`에서 경로나 기능명으로 할 수 있다. TSV는 UTF-8 탭 구분이다.

## 수량을 읽는 법

수량은 현재 `FILES.tsv`의 헤더를 뺀 행 수다. `required`에는 고정 Hermes 배포 전체, `optional`에는 업무 스킬과 선택 연동, `test`와 `docs`에는 검수 코드와 운영 참고 파일이 들어간다. 업무 캡처·덤프·더미 데이터·기존 사용자의 프로필은 복사 목록에서 제외한다. Hermes가 배포한 예시 리소스와 회귀 테스트의 입력 예시는 원본 무결성·테스트 재현을 위해 유지한다. 이 자료로 운영 DB를 채우지 않는다. 설명서와 비교 유틸리티는 참조 체크아웃에서 실행한다. 과거 CHECKPOINT-144.tsv는 이식 목록의 대용으로 사용할 수 없다.

현재 목록에는 LOT 이력과 맵의 전체 집계·백그라운드 완료, 조회 원본 전체와 표 요약, 직원별 예약·칸반 첨부·프로젝트·개인 파일, MCP 호출 기록 및 Office 파일 전달이 포함된다. [기능별 이식 검수](04-acceptance.md#port-current)

[반입 전 실제 검증 기록](VERIFICATION.md)에 기준 소스 무결성, 사내 충돌 모의 병합, 원본 스킬, 예시 DB에서의 실제 업무 결과를 적었다. 사내 담당자가 내린 병합 판단과 확인 결과는 외부로 보낼 필요가 없다.
