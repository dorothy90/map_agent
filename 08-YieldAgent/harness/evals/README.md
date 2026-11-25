# 하네스 평가

프로젝트 루트에서 실행한다. `harness/tests`는 Hermes/native 실행 계약을 검증한다.

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests -q
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.preflight --require-live
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.mcp_smoke --require-live
```

테스트 모음은 실제 Mongo와 Python worker를 사용하지만 모델 응답과 장애는 제어된 대체 공급자로 검증한다. `mcp_smoke`는 다섯 도구의 노출, 실제 Oracle 테이블 목록과 저장 결과의 재열람·소유권을 확인하며 외부 모델 호출을 하지 않는다. `run_synthetic`는 실제 모델을 호출하지만 독립된 평가 DB에 저장한 가상 자료를 원본 채팅의 `read_result`와 `execute_code`로 읽고 검산한다. 실데이터 평가는 `run_live`를 사용한다.

실제 데이터의 외부 모델 전송을 승인한 운영자가 사용하는 명령:

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.run_live \
  --query '최근 4주 4SS 수율을 표로 보여줘' \
  --allow-external-data --output outputs/harness-live.json
```

`run_live`는 실제 모델·Mongo·도구를 사용한다. `--no-domain-tools`는 인사 등 모델만 확인할 때 사용한다. 모든 시나리오가 completed일 때만 종료 코드 0을 반환하며, partial·failed·입력 대기는 통과로 처리하지 않는다. 응답 상태·사용량·원본 ID·호출 인자를 JSON에 기록하며 평가별 Mongo DB에 원본과 체크포인트가 남는다. 원본에는 내부 자료가 포함될 수 있으므로 보고서를 공개 저장소에 올리지 않는다.

실행 태스크가 종료됐는데 상태가 계속 created/running/cancelling이면 정리를 먼저 마치고 최종 상태와 `evaluation_error=nonterminal_worker_exit`를 보고서에 남긴다. 다음 동작이 `input`이면 원본 Hermes가 띄운 질문을 기다렸다가 해당 실행에 응답한다. 모델 출력 길이·temperature·추론 옵션도 보고서에 기록한다.

현재 기본 평가 공급자는 OpenRouter다. 실제 자료는 `HARNESS_BASE_URL`로 지정한 목적지에 대한 전송 승인 범위에서 사용한다. `run_synthetic`는 실제 생산 자료를 전달하지 않는 대안이다.

2026-09-12의 단순화 전후 비교, 제품·날짜 변형, 실제 DB 연결·모델·브라우저 확인은 [검증 보고서](../../../outputs/harness-simplification-validation.md)에 기록했다. 실제 DB 연결 시험에서 발견한 테스트 보고서의 HTML 본문 날짜와 메타데이터 불일치도 함께 기록했다.

전체 전환 평가는 다음 명령을 사용한다.

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.run \
  --require-live --allow-external-data --repetitions 3 \
  --review-manifest outputs/harness-review.json \
  --output outputs/harness-release
```

독립 검토 파일에는 `numeric_match`, `fault_suite`, `mining_live`, `browser_live`, `memory_30_turns`, `legacy_comparison`, `independent_domain_review` 각각의 `passed`와 구조화된 `evidence`가 필요하다. 각 증거는 `path`, 파일 `sha256`, 현재 `code_hash`와 `model_hash`, 해당 `run_ids`, 그리고 `numeric_hashes` 또는 `artifact_hashes`를 포함한다. 단순 파일 존재나 관계없는 README는 승인 증거가 될 수 없다. 정상 시나리오는 `completed` 상태와 필수 도구의 성공을 모두 요구하며 토큰·시간 제한의 `partial`은 실패다. 장애 주입 사례만 명시한 실패 상태를 정상 결과로 판정한다.

`cases.json`의 `turns`는 `query`, `input`, `steer`, `cancel` 동작을 순서대로 실행할 수 있다. `input`은 바로 앞 실행이 `waiting_user`일 때만 허용된다. `capabilities.json`은 감사 항목, 기능, 도구, 서비스, 검증 사례의 대응표이며 런타임 라우팅에는 사용하지 않는다.

무료 계정의 분당·일일 한도 때문에 반복 평가를 마치지 못할 수 있다. 실행을 완료하지 못한 기록을 성공률에서 지우거나 다른 유료 모델로 대체하지 않는다. 실패 기록과 원인을 남기고 동일 조건으로 재평가한다.
