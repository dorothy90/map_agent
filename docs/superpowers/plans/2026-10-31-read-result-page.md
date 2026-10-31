# read_result 페이지 전달 수정 계획

사용자가 요청한 페이지가 모델에 전달되고, 안내 문구가 실제 전달 행 수와 일치해야 한다.

## 원인과 설계

저장소의 50행·32KB 미리보기 제한, 모델 전달의 10행 제한, 대화 맥락의 토큰 예산이 각각 행을 줄인다. 두 read_result 구현은 그 이전의 조회 행 수로 안내 문구를 만든다.

선택한 설계는 ResultPage라는 선택적 구조화 정보다. 대상 table_id, 원본 행 수, offset, 요청 limit을 도구 결과에서 저장된 observation까지 보존한다. 페이지 대상은 최대 1,000행과 바이트 상한 안에서 보존한다. 일반 도구의 기존 미리보기 제한은 유지한다. 최종 모델 입력을 만드는 공통 메서드가 실제 표시 행 수로 문구와 표시 메타데이터를 만든다. 대화 맥락의 예산 적용 후에도 같은 메서드를 호출한다.

도구 이름으로 저장소와 출력 계층에 예외를 각각 추가하는 대안은 변경 줄이 적지만 페이지 의미와 요약 규칙이 다시 분산되므로 채택하지 않는다. 범용 preview_limit 필드만 추가하는 대안은 원본 수와 offset을 표현하지 못한다. 서로 독립된 두 설계 검토가 페이지 객체와 공통 요약 생성에 동의했다. Codex 세션 모델을 사용했으며 다른 모델 계열의 심사는 수행하지 않았다.

자연어 키워드나 기존 안내 문구 파싱을 추가하지 않는다. 원본 GridFS 데이터와 handle page_meta의 저장된 값·버전은 보존한다. 표시 행 수는 읽은 행 수와 구분한다. 기존 작업 중 변경은 보존한다.

## 실행 체크리스트

- [x] 현재 model_view에 158행을 넣어 10행 전달과 158행 안내가 공존함을 재현.
- [x] store, executor, model_view, native bridge, context 경로 조사.
- [x] 두 설계 대안 검토 후 선택.
- [x] 회귀 테스트를 먼저 작성하고 기존 코드의 실패 확인.
- [x] types, store, executor, context, 두 reader, ToolResult에 필요한 변경 적용.
- [x] 실제 로컬 MongoDB에서 저장, 페이지 조회, 도구 실행, 모델 입력, reload 검증.
- [x] 158행, 1,000행, offset, 빈 페이지, 바이트 상한, 예산 축소, 일반 도구 제한 검증.
- [x] 구성된 LLM에 실제 도구 결과를 전달하여 마지막 행을 읽을 수 있는지 확인.
- [x] 이번 변경만 독립 검토하고 관련 기존 테스트 실행.

현재 저장소에는 작업 이전부터 많은 미커밋 변경이 있다. 이번 요청은 로컬 수정과 테스트이므로 전체 변경을 커밋하거나 PR로 게시하는 단계는 생략한다. 기존 코드 스냅샷은 /tmp/read-result-before에 보관해 이번 변경만 비교한다.

## 검증 방식

새 테스트 파일은 UUID로 이름을 지정한 로컬 테스트 데이터베이스를 사용하고 자신이 만든 데이터베이스만 정리한다. 실제 MongoDB와 실제 도구 실행을 사용하되 입력 행은 합성 데이터로 표시한다. LLM 검증은 구성된 provider를 사용하며 자격증명이나 사용자 데이터는 보고서에 기록하지 않는다. 테스트 결과와 실제 모델 호출 결과를 구분해 보고한다.

## 검증 결과

- 수정 전 실제 MongoDB → NativeBridge 재현은 요청 158행, 모델 전달 10행, 안내 158행으로 실패했다. `outputs/read-result-page/before.json`에 기록했다.
- 수정 후 같은 경로에서 전달 158행, 안내 158행, 재조회 158행을 확인했다. `outputs/read-result-page/after.json`에 기록했다.
- 실제 구성 모델 `z-ai/glm-5.3-flash`가 read_result 도구를 호출하고 마지막 행의 무작위 값을 정확히 반환했다. `outputs/read-result-page/live-model.json`에 기록했다. 전체 native worker와 브라우저 UI 검증은 아니다.
- 새 회귀 사례는 수정 전 16개 실패를 확인했다. 이후 불완전한 원본 보존 사례 2개를 추가했다.
- 기존 비동기 reader 테스트 4개는 pytest-asyncio 부재로 pytest가 실행하지 못했다. 테스트 본문을 asyncio.run으로 각각 실행하여 모두 통과했고 `outputs/read-result-page/async-tests.txt`에 기록했다. 초기 pytest 실행 오류는 `regression-environment.txt`에 보존했다.
- 독립 코드 검토에서 차단 결함은 없었다. 신규 주석 관련 삭제 권고도 없었다.
- 기존 page 정보가 없는 저장 결과는 자동 변환하지 않는다. 새 read_result 호출부터 수정된 페이지 정보를 저장한다. 서버 재시작과 배포는 수행하지 않았다.

최종 관련 pytest 실행은 155 passed였다. 기존 비동기 reader 4개는 별도로 모두 통과했다. 재실행 명령은 다음과 같다.

```sh
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest -q 08-YieldAgent/harness/tests/test_read_result_projection.py 08-YieldAgent/harness/tests/test_store.py 08-YieldAgent/harness/tests/test_result_tables.py 08-YieldAgent/harness/tests/test_context.py 08-YieldAgent/harness/tests/test_context_budget.py 08-YieldAgent/harness/tests/test_simplification.py 08-YieldAgent/harness/tests/test_publication.py 08-YieldAgent/harness/tests/test_empty_result_signal.py
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.read_result_page --live-model --output outputs/read-result-page/live-model.json
```
