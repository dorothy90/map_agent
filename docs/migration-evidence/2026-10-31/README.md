# 보안망 이식 참조 검증 기록

시작 문서는 [현재 상태 이식 안내](../../../08-YieldAgent/harness/INTERNAL_MIGRATION_2026_10_31.md)다. 이 디렉터리의 결과는 로컬 개발 환경에서 얻은 근거다. 사내 환경의 검증 결과가 아니다.

| 확인 항목 | 결과 | 근거 |
|---|---|---|
| 수정 전 실제 MongoDB와 NativeBridge 페이지 조회 | 158행 요청, 모델 전달 10행, 안내 158행으로 실패 | [before.json](before.json) |
| 수정 후 같은 경로와 저장 결과 재조회 | 전달과 안내 모두 158행 | [after.json](after.json) |
| 실제 모델의 도구 호출과 마지막 행 확인 | 구성된 GLM 모델이 158번째 행의 무작위 값을 정확히 반환 | [live-model.json](live-model.json) |
| 관련 Python 테스트 | 155 passed | [pytest-final.txt](pytest-final.txt) |
| 기존 비동기 reader 테스트 | pytest-asyncio 부재로 본문을 직접 실행, 4개 통과 | [async-tests.txt](async-tests.txt) |
| 내장 엔진 import | Python 3.11.14에서 설치·다운로드 없이 성공 | [engine-import.txt](engine-import.txt) |
| Git 인덱스에서 별도로 꺼낸 소스 | 페이지 테스트 18개와 내장 엔진 import 성공 | [exported-source-tests.txt](exported-source-tests.txt). 의존성은 기존 가상환경을 사용했으며 새 오프라인 설치 검증은 아님 |
| Python 의존성 잠금 검사 | `uv lock --check --offline` 성공 | 선언과 잠금 파일 282개 패키지 해결 |
| 현재 Console 빌드 | `npm run build` 성공 | Node.js 25.1.0, npm 11.6.2. 큰 chunk와 동적·정적 import 중복 경고 존재 |

페이지 검증은 합성 데이터를 사용했다. 실제 업무 정답, 전체 native worker, 브라우저 UI, 사내 LLM과 DB, Linux와 Kubernetes 배포는 이 결과로 완료 처리하지 않는다. 실행·재검증 명령은 시작 문서에 있다.

[requirements.lock.txt](requirements.lock.txt)는 같은 커밋의 uv.lock에서 `uv export --frozen --no-dev --no-emit-project`로 생성했다. 사내 의존성 검토와 wheelhouse 준비를 위한 참조 파일이며 개발 테스트 패키지는 제외한다.

전달 준비에서는 staged 파일을 로컬 설정의 키·토큰·비밀번호 값 및 알려진 토큰·개인키 형태와 대조했다. 일치가 발견되지 않았다. 이는 전문 보안 감사나 모든 과거 커밋의 검사 결과가 아니다.

소스 스냅샷에는 기존 개발 변경도 포함한다. vendored patch와 일부 기존 파일에는 공백 경고가 남아 있다. 원본 추적과 패치 의미를 보존하기 위해 일괄 포맷하지 않았다.
