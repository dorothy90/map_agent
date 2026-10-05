# 사내 백엔드로 이식하기

Python 3.11을 유지하며 Hermes를 별도로 설치하지 않는다. `harness/engine/`에 원본 엔진과 내부 의존 모듈을 포함했다. 기존 백엔드가 같은 Python 실행 파일로 내부 작업 프로세스를 시작하므로 서버를 하나 더 운영할 필요가 없다.

## 옮길 구성

하네스가 없던 사내 버전에는 최신 이식 커밋 하나만 cherry-pick하지 않는다. `codex/goal-driven-harness` 브랜치의 최신 전체 파일을 기준으로 아래 구성들을 병합한다. 사내 저장소에서는 먼저 현재 작업을 커밋하고 별도 이식 브랜치를 만든다. 원본 브랜치에 포함된 기존 하네스 구현에도 의존하기 때문이다.

1. `harness/` 전체: 엔진 본체, 실행 어댑터, 스킬, 도구 등록, 저장소, 이벤트 및 설정. `__pycache__`와 테스트 산출물은 제외한다.
2. 백엔드 연결부: `agent_server.py`의 하네스 초기화·종료, 세션 선택, 실행/중단/재개, SSE 및 결과·산출물 경로를 사내 서버에 병합한다. 기존 파일 전체를 덮어쓰지 않는다.
3. 업무 도구가 참조하는 모듈: `harness/tools/`와 `harness/runtime/`에서 사용하는 기존 수율·맵·Oracle 연결 모듈을 함께 제공하거나 사내 구현에 연결한다. 엔진만 복사해도 사내 에이전트가 자동 등록되지는 않는다. 사내 도구는 `harness/tools/registry.py`의 `domain_registry()`에서 `ToolRegistry`에 스키마·실행 함수·효과를 등록한다. 사내 에이전트가 독립 실행 함수라면 그 함수를 호출하는 도구 어댑터를 추가한다.
4. 일반 Python 의존성: 루트 `requirements.txt`/`pyproject.toml`의 추가 항목을 승인 절차에 따라 병합한다. `engine/pyproject.toml`은 원본 보존 자료로, 설치 대상으로 사용하지 않는다. 기존 OpenAI SDK는 검증한 2.24.0 버전으로 맞춘다.
5. 환경 설정: `.env.example`의 `HARNESS_*`, 모델 엔드포인트, DB 설정 및 기존 격리 Python 실행 환경을 연결한다. 실제 비밀값은 복사 문서에 넣지 않는다.

`.runtime/hermes/src`, `.venv-hermes`, 원본 Hermes Git 저장소는 필요 없다. `.runtime/hermes/profiles/`는 설치 파일이 아니라 사용자별 스킬·메모리 실행 데이터이며 쓰기 가능한 저장 공간이 필요하다. 기존 메모리를 보존하려면 이 데이터는 별도 이관한다.

## 포함된 본체 위치

| 역할 | 내부 소스 |
|---|---|
| 진입점·대화 실행 | `engine/run_agent.py`, `engine/agent/` |
| 도구 검색·설명·호출 | `engine/model_tools.py`, `engine/tools/registry.py`, `engine/tools/tool_search.py` |
| 스킬·메모리 | `engine/tools/skills_tool.py`, `engine/tools/memory_tool.py` 및 관련 모듈 |
| 백엔드 도구 연결 | `native_worker.py`, `native_hermes.py`, `tools/registry.py`, `executor.py` |
| 원본 추적 | `engine/UPSTREAM.json`, `engine/PATCHES.diff`, `engine/LICENSE` |

엔진의 내부 import 의존성 때문에 CLI·플러그인 관련 소스도 일부 포함되어 있지만 해당 제품 기능을 백엔드에 노출하지는 않는다. 런타임에 패키지를 설치하거나 엔진을 업데이트하지 않는다. 필요한 선택 패키지가 없으면 사내 패키지 절차로 준비해야 한다.

## 사내 검증

승인된 패키지를 준비한 후 `BACKEND_PYTHON=/path/to/python bash scripts/setup_hermes.sh`로 import를 확인한다. `harness/tests/test_native_transport.py`는 다른 경로로 복사한 실제 엔진을 로컬 가상 모델과 연결해 스킬·도구 호출·취소를 검사한다.

이 검사는 실제 업무 정답을 보장하지 않는다. 사내 DB와 실제 LLM을 연결한 사용자 시나리오에서 조회 기간, 열화 파라미터 전체 집합, 수율 정의, 모집단 및 하위 10개 선정, 맵 10개를 각각 확인한 후 운영 전환한다. 기존 실제 평가에서 기간·지표 해석 오류와 토큰 예산 중단이 있었으므로, 이식 성공과 답변 품질 통과를 구분한다.
