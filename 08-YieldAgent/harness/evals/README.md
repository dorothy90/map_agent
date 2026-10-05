# 하네스 평가

프로젝트 루트에서 실행한다. `harness/tests`는 기존 서버가 없으면 자동 skip하는 테스트 모음과 분리되어 있다.

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m pytest 08-YieldAgent/harness/tests -q
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.preflight --require-live
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.local_tools
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.mcp_smoke --require-live
```

테스트 모음은 실제 Mongo와 Docker를 사용하지만 모델 응답과 장애는 제어된 대체 공급자로 검증한다. `local_tools`와 `mcp_smoke`는 실제 Oracle·검색·Python·산출물을 확인하며 외부 모델 호출을 하지 않는다. `run_synthetic`는 실제 모델을 호출하지만 모든 생산 데이터 도구를 가상 자료로 교체하므로 실제 DB 통합 평가가 아니다.

실제 데이터의 외부 모델 전송을 승인한 운영자가 사용하는 명령:

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.run_live \
  --query '최근 4주 4SS 수율을 표로 보여줘' \
  --allow-external-data --output outputs/harness-live.json
```

`run_live`는 실제 모델·Mongo·도구를 사용한다. `--no-domain-tools`는 인사 등 모델만 확인할 때 사용한다. 모든 시나리오가 completed일 때만 종료 코드 0을 반환하며, partial·failed·입력 대기는 통과로 처리하지 않는다. 응답 상태·사용량·원본 ID·호출 인자를 JSON에 기록하며 평가별 Mongo DB에 원본과 체크포인트가 남는다. 원본에는 내부 자료가 포함될 수 있으므로 보고서를 공개 저장소에 올리지 않는다.

실행 태스크가 종료됐는데 상태가 계속 created/running/cancelling이면 정리를 먼저 마치고 최종 상태와 `evaluation_error=nonterminal_worker_exit`를 보고서에 남긴다. 입력 대기 상태는 정상적인 중단 지점으로 구분한다. 모델 출력 길이·temperature·추론 옵션도 보고서에 기록한다.

현재 기본 평가 공급자는 OpenRouter다. 실제 자료는 `HARNESS_BASE_URL`로 지정한 목적지에 대한 전송 승인 범위에서 사용한다. `run_synthetic`는 실제 생산 자료를 전달하지 않는 대안이다.

WADS 요청 의미 해석과 기존 원본 보고서 열람을 분리해 확인하는 가상 평가:

```bash
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.wads_intent --output outputs/harness-wads-intent.json
PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.evals.wads_synthetic
```

`wads_intent`는 가상 이전 대화 이후의 첫 도메인 행동에서 멈춘다. 실제 DB 도구를 실행하지 않으며 WADS 도구와 DEMO 제품을 선택해야 통과한다. 도구 선택만 확인하므로 통계·답변 완성 여부는 검증하지 않는다. `wads_synthetic`는 가상 보고서 4건/웨이퍼 조인 7행으로 실제 모델·격리 Python·원본 HTML 반환의 2턴 흐름을 실행한다. 8월 통계 다음의 원본 열람에서 `get_wads_report`가 호출돼야 하며 수율 조회나 신규 PPT 생성으로 대체하면 실패한다. 기록의 completed 상태 외에도 원본과 최종 수치 및 기간을 독립적으로 검토한다.

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
