# Yield Agent 목표 기반 하네스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 독립된 병렬 구현은 사용자 요청이 있는 경우에만 선택한다.

**Goal:** 기존 웹 앱의 도메인 기능을 유지하며, 결과에 따라 조사·계산·검증을 이어가는 운영 가능한 목표 기반 하네스로 전환한다.

**Architecture:** 명시적인 LangGraph 실행 루프에서 모델이 native tool call을 선택하고, 별도 실행 계층이 권한·예산·재시도·저장·취소를 관리한다. 대화·목표·근거·작업 기록을 유지하며 최종 답변의 요청 충족과 근거를 검사한다. 기존 도메인 함수를 공통 도구로 재사용하고 Python 격리 실행과 MCP 어댑터를 연결한다.

**Tech Stack:** Python 3.11+, 현재 lockfile의 LangGraph/LangChain/Pydantic, FastAPI/SSE, MongoDBSaver, MongoDB/GridFS, 기존 React UI, 컨테이너 Python runtime, MCP Python SDK.

**설계 기준:** [최종 설계 문서](../specs/2026-09-12-goal-driven-harness-design.md). 이 계획은 구현을 시작하지 않고 작성했다. 아래 새 경로·API·테스트는 구현 산출물이며 현재 이미 존재하거나 통과했다는 의미가 아니다.

## 구현 진행 기록 — 2026-09-12

사용자의 구현 지시 후 `codex/goal-driven-harness`에서 작업했다. 아래는 실행으로 확인한 현재 상태이며, 본문의 원안 체크리스트에서 실제 평가·커밋까지 묶인 항목을 일괄 완료 처리하지 않는다.

| 단계 | 구현/검증 상태 |
|---|---|
| 0–2 | 설정·저장·도메인 어댑터 구현. 실제 Oracle, Mongo, 전체 결과 재조회 확인 |
| 3–5 | native 행동 루프·완료 검증·맥락 압축·과거 결과 재참조 구현. scripted 반복/30턴 저장·재참조 검증 |
| 6–7 | 예산·재시도·취소·epoch 복구·질문/정정·SSE 구현. 실제 Mongo 재개와 장애 주입 검증 |
| 8 | 격리 Docker Python 및 전체 원본 계산·차트 생성 검증 |
| 9 | 도메인 어댑터·기존 UI 연결. 실제 수율/WADS/LOT/맵/검색/그룹/관련 공정/PPT 검증. 실제 마이닝 API 미제공 |
| 10–11 | 제한된 child 조사·동시성 구현 및 제어된 모델 검증. 실제 MCP 13개 도구 목록·Oracle 조회·원본 재조회 확인 |
| 12 | 평가 실행기·전환 플래그·운영 문서 구현. 전체 실제 반복 행렬·구버전 비교·마이닝·브라우저 전체 시나리오는 미완료 |

설계 확정 후 실제 시험에서 반영한 변경:

- 사용자가 Groq `qwen/qwen3.8-27b`를 지정했고 실제 DB 결과의 Groq 전송을 승인했다. 이전 OpenRouter 전송 보류와 구분한다.
- Groq 무료 계정의 입력·출력 한도에 맞춰 `load_tools`로 상세 스키마를 필요한 만큼 선택하고 출력 길이/추론 옵션을 설정한다. 자연어 키워드 라우팅은 추가하지 않았다.
- 수율표는 기존 렌더러로 산출물을 생성하고 전체 원본과 함께 저장한다.
- 결과 ACK 유실 시 원본 파일을 보존하고, 복구 체크포인트는 epoch별로 분리하며 중간 쓰기 복사를 마친 후 공개한다.
- 현재 운영 설명은 [harness/README.md](../../../08-YieldAgent/harness/README.md), 평가 구분과 명령은 [evals/README.md](../../../08-YieldAgent/harness/evals/README.md)를 따른다.

전체 release gate는 아직 통과하지 않았으며 `HARNESS_ENABLED` 기본값은 `false`다. 검토 가능한 변경은 작업 브랜치에 있으며 아직 커밋하지 않았다.

이전 Qwen 시험: 회귀 테스트 62개와 실제 MCP 원본/표 수치 대조는 통과했다. 실제 수율 조회 후 최종 응답은 출력 한도에서 native tool JSON이 잘려 `partial/tool_use_failed`로 종료됐다. 당시 출력 상한은 1,000토큰이었다. 상세 결과는 [Groq Qwen 검증 보고서](../../../outputs/groq-qwen-validation.md)에 기록했다.

이후 사용자가 크레딧을 추가하고 OpenRouter `z-ai/glm-5.3-flash`로 같은 테스트를 요청했다. 현재 모델·주소·역할별 모델 설정을 전환하고 출력 상한을 8,192토큰으로 늘렸다. 공급자 기본 추론 설정을 사용한다. 실제 DB 결과의 OpenRouter 전송은 자동 승인 검토에서 차단되어 해당 전송의 명시적 승인을 별도로 요청했다.

GLM 후속 시험에서 temperature=1.0을 적용하고, 빈 결과 메타데이터의 근거 검증과 실행권 만료·갱신 실패·종료 저장 경쟁을 수정했다. 최종 회귀 테스트 63개가 통과했다. 가상 데이터의 실제 GLM→Python→답변 실행은 239.37초에 `completed/verified`로 끝났고 Python 수치는 원본과 일치했으나, 최종 설명의 수식 부호 오류를 독립 검토에서 발견했다. 따라서 답변 정확도와 전체 release gate를 통과로 처리하지 않는다. [GLM 전환·검증 보고서](../../../outputs/openrouter-glm53-validation.md)에 실패 기록과 승인 대기를 함께 기록했다.

사용자의 4SS 후속 질문 실패를 추가 조사했다. 단위 범위의 잘못된 동일성 비교, 예산이 부족한 맥락 압축, 빈 답변·근거 배열 누락 처리, 마무리 문맥의 수정 피드백 유실을 보완했다. 회귀 테스트는 69개 통과했다. 가상 2턴 시험의 첫 조회는 완료됐지만 후속 설명은 예산으로 중단됐으며, 마지막 피드백 수정은 해당 시험 시작 후 적용됐다. 성공으로 처리하지 않는다. 상세 재시험 결과와 남은 품질 문제는 [후속 질문 조사 보고서](../../../outputs/harness-followup-validation.md)에 기록한다. 현재 로컬 시험 서버만 `HARNESS_ENABLED=true`로 실행하며 기본값 전환은 하지 않았다.

열화리포트 요청을 수율 통계/PPT로 오인한 실행을 추가 조사해 WADS 원본·통계 자료·신규 PPT의 도구 설명을 명확히 했다. 실제 4SS 8월 WADS는 로컬에서 129개 원본 HTML과 동일 보고서 키 건수를 확인했다. 가상 실제 모델 시험에서는 WADS 도구를 올바르게 사용했지만 통계 코드 생성과 최종 근거 인용에 실패해 2턴 모두 partial로 끝났다. [WADS 조사·검증 보고서](../../../outputs/harness-wads-validation.md). 도구 등록이나 직접 호출 성공을 사용자 요청 전체의 성공으로 간주하지 않는다.

## Global Constraints

- 프로젝트 루트는 `/Users/daehwankim/yield-agent`, 애플리케이션 루트는 `/Users/daehwankim/yield-agent/08-YieldAgent`다. 아래 파일 목록은 이 애플리케이션 루트를 기준으로 한다.
- 단일 하네스를 최종 기본값으로 한다. direct/deterministic/exploratory 자연어 분류기를 추가하지 않는다.
- 기존 9개 worker의 기능·기존 웹 UI·날짜/DB 변환·구조화 missing_param 계약을 보존한다.
- 자연어 의미 해석을 keyword/regex/문구 목록/한국어 표현표/새 special-case/few-shot 누적으로 해결하지 않는다.
- Python 분석 실행을 포함한다. 운영 경로에 mock fallback이나 무격리 exec fallback을 넣지 않는다.
- 모델·공급자는 설정으로 선택하되 사용자 동의 없이 유료 공급자로 전환하지 않는다. 모델 품질은 실제 평가 결과로 판단한다.
- 구버전 실행은 전환·롤백용으로 유지한다. 새 runtime은 별도 state/checkpoint namespace를 쓴다.
- 도메인 결과는 preview 생성 전에 전체 원본을 저장한다. 다른 세션 데이터는 참조할 수 없다.
- 단위·모의 테스트와 실제 E2E를 구분한다. 필수 E2E의 skip 또는 mock-only 통과를 완료로 선언하지 않는다.
- 구현 전 현재 사용자 변경을 확인하고 보존한다. 격리가 필요하면 `codex/goal-driven-harness` 작업 브랜치를 사용한다. 이 계획 작성만으로 기존 파일을 이동하거나 실행기를 교체하지 않는다.
- 단계별 변경은 검증 후 해당 파일만 커밋한다. 저장소 전체를 일괄 stage하지 않는다.

## 1. 단계별 산출물과 의존성

| 단계 | 독립적으로 검증할 산출물 | 선행 |
|---|---|---|
| 0 | 실제 의존성·모델 준비 상태와 비교 기준 | 없음 |
| 1 | 상태·결과·호출 기록 계약과 저장소 | 0 |
| 2 | 수율/WADS/LOT의 전체 데이터 도구 | 1 |
| 3 | 매 관측 후 다음 행동을 선택하는 루프 | 2 |
| 4 | 목표 완료·근거 검증 | 3 |
| 5 | 맥락 압축·작업 기록·지침 | 4 |
| 6 | 예산·재시도·동시성·취소·재시작 복구 | 3, 5 |
| 7 | 질문·재개·목표 수정 API와 이벤트 | 6 |
| 8 | 실제 결과 기반의 격리 Python 분석 | 2, 6, 7 |
| 9 | 나머지 도메인 기능과 기존 UI 연결 | 4, 7, 8 |
| 10 | 제한된 독립 조사·병렬 도구 실행 | 6, 9 |
| 11 | 공통 registry의 MCP 노출 | 9 |
| 12 | 실제 E2E·비교 평가·전환·롤백 | 10, 11 |

단계 3은 구조의 첫 검증 지점이다. 단계 9는 전체 도메인 작업을 새 UI 흐름에서 실행하는 지점이다. 단계 12까지 통과해야 최종 범위 완료다.

## 2. 파일 책임

| 새 파일/디렉터리 | 책임 |
|---|---|
| `harness/types.py` | GoalContract, ToolSpec, ToolObservation, FinalCandidate, RunState |
| `harness/model.py`, `harness/config.py` | 검증된 모델 설정·사용량·capability·예산 기본값 |
| `harness/store.py` | Mongo metadata·GridFS·호출 ledger·소유권 |
| `harness/graph.py`, `harness/nodes.py` | 그래프 조립·모델/도구/질문/종료 전이 |
| `harness/tools/registry.py` | 도구 설명·입력 schema·정책·호출 결합 |
| `harness/tools/yield_tools.py`, `wads_tools.py`, `lot_tools.py` | 핵심 데이터 도구 어댑터 |
| `harness/tools/analysis_tools.py`, `artifact_tools.py` | 나머지 조회·분석·보고서 어댑터 |
| `harness/context.py`, `harness/instructions.py`, `harness/instructions/*.md` | 맥락 구성·압축·검토 가능한 도메인 지침 |
| `harness/completion.py` | 근거 검사·의미 검사·완료 상태 |
| `harness/executor.py`, `harness/control.py` | 호출 실행·재시도·budget·lease·취소 |
| `harness/router.py`, `harness/events.py` | 실행 API·HITL/steer·SSE 이력 |
| `harness/runtime/container.py`, `worker.py`, `Dockerfile` | 격리 Python 프로세스와 자료 전달 |
| `harness/delegation.py` | 독립 조사 scope·예산·결과 통합 |
| `harness/mcp_server.py` | stdio MCP 도메인 도구 서버 |
| `harness/testing.py` | 실제 그래프에 scripted model과 테스트 도구를 연결하는 테스트 지원 |
| `harness/tests/` | 서버 의존 autouse fixture와 분리된 하네스 테스트 |
| `harness/evals/` | 실제 시나리오·독립 실행기·결과 manifest |

기존 파일 변경은 `common.py`, `agent_server.py`, `models.py`, domain 함수 파일, 기존 UI 연결부에 한정한다. legacy의 planner·supervisor·replanner는 새 runtime에서 호출하지 않으며, 롤백 기간에는 파일 자체를 유지한다. 불필요한 도메인 SQL/프롬프트/UI 디자인 개편은 범위에서 제외한다.

## 3. 공통 인터페이스

구현 태스크들이 공유하는 계약이다. 이 인터페이스는 Python 호출 경계이며, 새로운 자연어 intent 분류 체계를 뜻하지 않는다.

```python
# harness/model.py
def build_model(settings, *, purpose: str):
    """tool calling 또는 최종 검증에 쓰는 설정된 모델 반환."""

# harness/store.py
class HarnessStore:
    async def put_result(self, principal_id, session_id, observation, rows, artifacts):
        """전체 자료를 저장한 ToolObservation 반환."""
    async def read_result(self, principal_id, session_id, result_id, *, offset=0, limit=50, columns=None):
        """검증된 페이지와 total_rows/truncated/scope/provenance 반환."""
    async def claim_invocation(self, run_id, invocation_id, tool_name, arguments, epoch):
        """새 호출 소유권 또는 이미 완료된 호출의 result_id 반환."""
    async def finish_invocation(self, run_id, invocation_id, observation, epoch):
        """현재 epoch에서만 종료 확정."""

# harness/tools/registry.py
class ToolRegistry:
    def model_tools(self, principal_id):
        """허용된 도구의 native tool schemas 반환."""
    async def invoke(self, name, arguments, context):
        """검증된 domain callable의 ToolObservation 반환."""

# harness/graph.py
def build_harness(*, model, registry, store, checkpointer, control, instructions):
    """RunState를 사용하는 compiled graph 반환."""

# harness/completion.py
def validate_evidence(candidate, goal, observations):
    """근거 없는 fact·잘못된 범위·미충족 항목의 issue 목록 반환."""

# harness/control.py
class RunController:
    async def start(self, principal_id, session_id, request_id, query):
        """중복 요청을 합치거나 새 run_id 반환. 활성 충돌은 409."""
    async def cancel(self, principal_id, run_id):
        """취소 상태를 기록하고 실행 도구에 전달."""
    async def submit_input(self, principal_id, run_id, payload):
        """input/steer의 revision과 소유권 검증 후 접수."""

# harness/testing.py
async def run_scripted(*, query, script, tool_results, settings=None, messages=None):
    """실제 graph/executor + scripted 모델. dict 보고서 반환."""
```

`run_scripted`의 script 항목은 실제 모델 응답으로 변환되는 `tool`/`arguments` 또는 `finish` payload다. 반환 dict에는 `status`, `tool_calls`, `model_observations`, `answer`, `stop_reason`, `events`가 포함된다. tool_results는 도구별 관측 fixture이며 런타임이 `data_origin=fixture`를 부여한다. 이 도우미가 별도 planner나 종료 규칙을 구현해서는 안 된다. pytest의 asyncio 실행 의존성을 늘리지 않도록 아래 예시는 `asyncio.run`을 사용한다.

## Task 0: 준비 상태·모델 설정·기준 시나리오

**Files**
- Create: `harness/__init__.py`, `harness/config.py`, `harness/model.py`, `harness/evals/preflight.py`
- Create: `harness/tests/conftest.py`, `harness/tests/test_model_config.py`, `harness/evals/__init__.py`, `harness/evals/baseline_cases.json`
- Modify: `common.py`, 프로젝트 루트 `pyproject.toml`, `uv.lock`, `.env.example`

**Interfaces:** `build_model(settings, purpose=...)`, `python -m harness.evals.preflight --require-live`.

- [ ] 기존 사용자 변경과 현재 서버 실행 방식을 기록하고 lockfile 환경을 동기화한다. pytest는 개발 의존성으로 추가한다. 프레임워크 전체 업그레이드를 동시에 하지 않는다.
- [ ] 모델 설정이 전달되지 않거나 지정한 모델명이 무시될 때 실패하는 테스트를 먼저 만든다. `common.get_llm(model=...)`의 인자가 실제 적용되는지 확인한다.
- [ ] provider/base URL/model/key의 구성을 설정 경계에서 검증한다. 현재 무료 모델 설정은 유지하되 capability 검증 실패를 다른 모델로 숨기지 않는다.
- [ ] preflight는 비밀값을 출력하지 않고 모델 인증→작은 실제 tool-call→Oracle 읽기→Mongo 읽기/쓰기→필수 외부 도구 연결 순서로 확인한다. 모델 오류와 파싱 오류를 구분한다. 이 단계의 테스트용 Mongo 기록에는 별도 run ID를 쓴다.
- [ ] 인사, 단순 수율, 수율+원인, 빈 결과, 기간 변경, 다중 입력 질문을 기준 시나리오로 저장한다. 이전 실행의 401·응답 실패도 기준 상태에 그대로 기록한다.
- [ ] `pytest harness/tests/test_model_config.py -q`와 preflight를 실행하고 검증된 파일만 커밋한다.

**Acceptance example**

```python
def test_explicit_model_is_preserved(monkeypatch):
    from common import get_llm
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    model = get_llm(model="fixture/model")
    assert model.model_name == "fixture/model"
```

**통과 기준:** 키가 유효하지 않으면 진단이 실패하며 LLM E2E 완료를 선언하지 않는다. 구조 구현과 로컬 테스트는 계속할 수 있다. 외부 API의 실제 연결 정보가 없는 도구는 live 검증 미완료로 명시한다.

## Task 1: 계약·전체 결과 저장·소유권

**Files**
- Create: `harness/types.py`, `harness/store.py`, `harness/tests/test_store.py`
- Read/reuse: `result_contracts.py`, `agent_server.py`의 Mongo 연결 설정

**Interfaces:** 설계 §5의 계약과 `HarnessStore`. artifact는 bytes/문자열로 GridFS에 저장하고 관측에는 ID만 반환한다.

- [ ] GoalContract, RunState, ToolObservation, FinalCandidate를 Pydantic/JSON 직렬화 가능한 모델로 정의한다. `schema_version`을 고정하고 불명확한 추가 필드는 거부한다.
- [ ] 120행의 전체 결과를 저장한 뒤 첫 50행과 다음 페이지를 읽는 테스트를 먼저 작성한다. 다른 principal/session 조회와 유효하지 않은 ref의 테스트를 포함한다.
- [ ] Mongo metadata와 GridFS payload를 구현한다. payload를 저장한 후 metadata를 공개하며, 중간 실패한 파일은 orphan으로 추적해 정리한다. checksum과 row count를 기록한다.
- [ ] 호출 ledger에는 `(run_id, invocation_id)` unique index를 둔다. 완료 상태와 result ID를 compare-and-set으로 기록한다. result 원본과 preview의 소유권을 함께 검증한다.
- [ ] 실제 로컬 Mongo/GridFS를 사용한 저장·재읽기·소유권 거부 테스트를 실행한 뒤 커밋한다.

```python
def test_preview_does_not_replace_full_result(stored_120_row_result):
    import asyncio
    store, observation = stored_120_row_result
    page = asyncio.run(store.read_result("user-a", "session-a", observation.result_id, offset=50, limit=50))
    assert page["total_rows"] == 120
    assert len(page["rows"]) == 50
    assert page["rows"][0]["row_id"] == 50
    assert page["truncated"] is True
```

fixture `stored_120_row_result`은 이 테스트 파일에서 `put_result`로 `row_id=0..119`를 실제 저장하고 종료 시 해당 ID만 삭제한다. 운영 데이터나 다른 테스트의 기록은 삭제하지 않는다.

**검증:** `pytest harness/tests/test_store.py -q` → 전체 보존·격리·부분 저장 실패 처리 통과.

## Task 2: 핵심 조회를 도구로 제공

**Files**
- Create: `harness/tools/__init__.py`, `registry.py`, `yield_tools.py`, `wads_tools.py`, `lot_tools.py`
- Create: `harness/tests/test_domain_tools.py`
- Modify only where extraction is needed: `yield_query_agent.py`, `yield_db.py`, `wads_tools.py`, `lot_history_tools.py`

**Interfaces:** `query_yield`, `query_wads`, `get_wads_report`, `query_lot_history`, `read_result` → ToolObservation. 입력에는 기간·제품·검사 단계·result 참조를 명시한다.

- [ ] 현재 도메인 schema를 기준으로 입력 모델을 정의한다. 사용자 principal/session/run ID는 모델 인자가 아니라 실행 context에서 주입한다.
- [ ] 날짜 변환, empty와 error 구분, 결과 전체 보존, WADS 보고서와 LOT 연결이 깨질 때 실패하는 계약 테스트를 만든다.
- [ ] 기존 DB/도구 함수를 호출하는 어댑터를 작성한다. worker 안의 후속 선택·별도 계획·확인 질문을 실행하지 않는다. 숨은 전역 슬롯 대신 검증된 입력을 전달한다.
- [ ] 조회 직후 전체 데이터를 `put_result`로 보관하고 preview/summary/artifact ref를 반환한다. 기존 `ResultEnvelopeV1`은 legacy 호환 결과로만 유지한다.
- [ ] 실제 Oracle에서 읽은 동일 조건의 결과를 legacy와 비교한다. 수치·기간·LOT 집합을 비교하고 LLM 답변 문구는 비교 기준으로 쓰지 않는다.
- [ ] `pytest harness/tests/test_domain_tools.py -q` 실행과 live 비교 후 커밋한다.

**구체 테스트:** `test_week_range_preserved`, `test_empty_is_not_error`, `test_120_rows_remain_readable`, `test_report_lot_links_preserved`, `test_no_nested_planning_or_interrupt`를 작성한다. 마지막 테스트는 조회 어댑터 실행 시 기존 planner/model과 interrupt 호출을 금지하는 spy로 검증한다.

## Task 3: 관측 기반 모델–도구 루프

**Files**
- Create: `harness/graph.py`, `harness/nodes.py`, `harness/executor.py`, `harness/testing.py`, `harness/tests/test_loop.py`
- Consume: Task 1 store, Task 2 registry, Task 0 model

**Interfaces:** `build_harness(...)`, `run_scripted(...)`. 목표/관측/제어 명령 외 별도 canonical intent parser를 만들지 않는다.

- [ ] 모델이 수율 결과를 받은 후 처음 계획에 없던 LOT 조회를 선택하는 실패 테스트를 작성한다.
- [ ] `context → model → policy → execute → observe → context` 그래프를 만든다. tool call과 ToolMessage를 call ID로 연결하고 에러에도 응답 메시지를 붙인다.
- [ ] 모델에 GoalContract와 최신 관측을 제공한다. 초기 작업 기록은 수정 가능한 안내이며 허용된 도구 목록을 제한하는 작업 큐가 아니다.
- [ ] `update_worklog`, `ask_user`, `finish` 제어 도구를 등록한다. `finish`는 후보를 기록할 뿐 이 단계에서 검증을 우회하는 종료 권한을 갖지 않는다.
- [ ] native tool-call schema 오류를 한 번 교정할 수 있게 전달한다. 일반 텍스트 답변도 후보로 다루며 파싱 실패를 인사 응답 경로로 보내지 않는다.
- [ ] 실제 모델로 짧은 단순 조회와 결과 기반 추가 조회를 확인하고 `pytest harness/tests/test_loop.py -q` 후 커밋한다.

```python
def test_model_can_add_a_tool_after_observation():
    import asyncio
    from harness.testing import run_scripted
    report = asyncio.run(run_scripted(
        query="수율과 관련 LOT 이력을 확인해줘",
        script=[
            {"tool": "query_yield", "arguments": {"lotcd": "4SS", "unit": "weekly", "periods": 4}},
            {"tool": "query_lot_history", "arguments": {"lot_ids": ["TEST001"]}},
            {"finish": {"answer": "fixture 결과 확인", "claims": [], "coverage": [], "limitations": []}},
        ],
        tool_results={
            "query_yield": {"status": "success", "rows": [{"lot_id": "TEST001", "yield": 90}]},
            "query_lot_history": {"status": "success", "rows": [{"lot_id": "TEST001", "process": "P1"}]},
        },
    ))
    assert [c["name"] for c in report["tool_calls"]] == ["query_yield", "query_lot_history"]
    assert report["model_observations"][1]["tool_name"] == "query_yield"
```

이 테스트는 실행·관측 순서를 검증한다. 실제 모델이 올바른 후속 도구를 선택하는 능력은 Task 12의 live 평가로 따로 검증한다. 빈 coverage 후보를 completed로 인정하는 테스트가 아니다.

## Task 4: 목표 완료와 근거 검증

**Files**
- Create: `harness/completion.py`, `harness/tests/test_completion.py`
- Modify: `harness/types.py`, `harness/nodes.py`, `harness/graph.py`

**Interfaces:** `validate_evidence(candidate, goal, observations) → list[issue]`. issue에는 `code`, `acceptance_item_id`, `claim_id`, `result_id`, `message`를 포함한다.

- [ ] 수율과 원인 설명을 요청했는데 수율 결과만 있는 후보, 다른 기간 결과를 인용한 후보, 없는 result ID를 인용한 후보를 먼저 실패시킨다.
- [ ] 코드 검증으로 출처·권한·범위·수치·표본·coverage를 검사한다. 숫자 비교 대상은 저장된 result/집계 자료로 제한한다.
- [ ] 별도 최종 의미 검사를 붙여 관측에서 결론으로의 과도한 추론, 빠진 요청, 가설과 사실 혼동을 확인한다. 매 도구 호출마다 별도 비평 모델을 실행하지 않는다.
- [ ] 검증 오류를 다음 모델 입력에 반영하고 교정 2회 후 partial을 반환한다. partial·blocked·cancelled의 설명에도 확인한 사실만 포함한다.
- [ ] 인사와 일반 설명은 데이터 인용 없이 끝나며, 예산 종료는 completed가 될 수 없도록 한다.
- [ ] `pytest harness/tests/test_completion.py -q`와 실제 복합 요청 검증 후 커밋한다.

```python
def test_missing_source_rejects_data_claim():
    from harness.completion import validate_evidence
    issues = validate_evidence(
        candidate={"claims": [{"id": "c1", "kind": "fact", "text": "수율 90%", "evidence": [{"result_id": "missing"}]}], "coverage": []},
        goal={"acceptance_items": [{"id": "g1", "description": "수율을 제시"}]},
        observations={},
    )
    assert any(issue["code"] == "missing_evidence" for issue in issues)
```

**통과 기준:** pending 없음, 도구 성공, 호출 한도 초과 어느 것도 단독으로 완료 신호가 되지 않는다.

## Task 5: 긴 맥락·작업 기록·지침

**Files**
- Create: `harness/context.py`, `harness/instructions.py`, `harness/instructions/analysis.md`, `harness/instructions/evidence.md`
- Create: `harness/tests/test_context.py`
- Modify: `harness/nodes.py`, `harness/types.py`

**Interfaces:** `build_context(state, observations, instructions, token_budget)`, `compact_context(state, *, target_tokens)`를 이 단계에서 정의한다. 출력은 model messages와 사용한 result ID/압축 메타다.

- [ ] 30턴 후 이전 LOT 재참조, 제품 수정, 상충하는 결과, tool-call/result 쌍 보존 테스트를 만든다.
- [ ] 원래 목표와 최신 정정을 우선하고 오래된 조사 요약은 하위 맥락으로 넣는다. 최근 assistant 최종 답변과 질문 계약을 유지한다.
- [ ] 70%/50% 압축 기준을 적용하고 압축 전후 token 수를 기록한다. 요약된 사실의 result ID가 실제 저장소에 존재하는지 검증한다.
- [ ] `update_worklog`로 사실·가설·미해결을 나누고 삭제/수정 이력을 남긴다. user revision이 바뀌면 무관한 과거 슬롯을 자동 상속하지 않는다.
- [ ] 지침은 검토된 파일에서만 로드한다. 검색 문서에 “이전 지침을 무시하라”가 있어도 지침 우선순위를 바꾸지 않는 테스트를 넣는다.
- [ ] `pytest harness/tests/test_context.py -q`와 실제 긴 대화 재참조를 확인하고 커밋한다.

**통과 기준:** 압축해도 전체 결과 재조회가 가능하고, 새 목표가 이전 제품/기간으로 오염되지 않는다. 압축 중 LLM 오류가 발생하면 원래 참조를 보존한 제한된 맥락으로 실행하거나 명시적 오류로 종료하며 목표를 버리지 않는다.

## Task 6: 실행 제어·예산·오류 복구

**Files**
- Create: `harness/control.py`, `harness/tests/test_control.py`, `harness/tests/test_recovery.py`
- Modify: `harness/executor.py`, `harness/config.py`, `harness/store.py`, `harness/graph.py`

**Interfaces:** `RunController`, ledger epoch, cancellable execution context. 로컬 asyncio task registry는 편의 캐시이며 실행 소유권의 권위는 Mongo다.

- [ ] 인증 실패 1회 중단, transient 최대 3회, 중복 request ID, 중복 invocation, lease 만료 후 이전 worker의 늦은 결과 거부 테스트를 먼저 만든다.
- [ ] 설계 §8의 도구 24/모델 40/600초/120,000 token 한도를 구현한다. 모델·child·최종 검증의 모든 호출을 합산한다. 최종 응답을 위한 예산을 예약한다.
- [ ] 실행 중 취소를 외부 I/O deadline과 Python runtime에 전파한다. 취소 요청과 완료가 경합하면 compare-and-set으로 terminal 상태 하나를 결정한다.
- [ ] `requested → running → succeeded/failed/cancelled/unknown` 호출 상태를 기록한다. 완료된 도구는 replay 시 재실행하지 않는다. unknown 쓰기 작업은 자동 재전송하지 않는다.
- [ ] 동일 도구+정규화 인자+입력 checksum을 이용해 반복을 감지한다. 데이터 갱신을 요청한 경우에는 새로운 snapshot임을 기록하고 재조회한다. 문장 유사도 규칙으로 사용자의 의도를 추측하지 않는다.
- [ ] 재시작 때 lease·체크포인트·호출 ledger를 조정한다. 재개 가능한 읽기는 이어서 수행하고 복구 불가능한 작업은 blocked/partial로 전환한다.
- [ ] `pytest harness/tests/test_control.py harness/tests/test_recovery.py -q`를 실행한다. 실제 Mongo와 두 worker 프로세스로 경합/재시작 테스트 후 커밋한다.

**통과 기준:** active run 하나, terminal event 하나, 완료된 동일 invocation의 외부 호출 하나. side effect 결과가 불명확한 상황에서는 exactly-once 보장 대신 unknown 상태를 노출한다.

## Task 7: API·질문·재개·실행 중 정정

**Files**
- Create: `harness/router.py`, `harness/events.py`, `harness/tests/test_api.py`, `harness/tests/test_interrupts.py`
- Modify: `agent_server.py`, `models.py`, `harness/nodes.py`, `harness/control.py`

**Interfaces:** 기존 `/chat/stream` façade, 새 `/runs/{run_id}` 조회/events/cancel/input API. session은 server가 runtime version을 지정한다.

- [ ] 모든 missing fields가 한 번에 전달되는지, resume가 질문 이전 모델/도구 호출을 반복하지 않는지 테스트한다.
- [ ] 질문을 실행 노드와 분리한 interrupt 노드로 구현한다. `interrupt_id`, `goal_revision`, `action_id`를 저장하고 stale input을 409로 거부한다.
- [ ] `/input` payload는 `kind=input|steer`, `request_id`, `goal_revision`, `interrupt_id`, `value`를 받는다. steer에는 query 원문을 value로 전달한다. 서버는 scope·소유권을 검증하고 모델이 의미를 해석하게 한다.
- [ ] 사용자 수정 시 빈 resume로 기존 gate를 소진하지 않는다. 활성 invocation 종료/취소를 조정하고 새 goal revision에서 다시 판단한다.
- [ ] 오래 걸리는 실행을 HTTP 응답 생명주기와 분리한다. 사건을 저장한 다음 SSE로 발행하고 after sequence로 재접속한다. 일반 진행 로그가 클라이언트마다 새로운 실행을 만들지 않게 한다.
- [ ] 인증된 principal 공급자를 연결하고 body의 `user_id`를 접근 권한으로 신뢰하지 않는다. 인증이 없는 기존 로컬 운영은 서버에서 단일 principal을 고정하고 외부 multi-user 모드로 표시하지 않는다.
- [ ] `pytest harness/tests/test_api.py harness/tests/test_interrupts.py -q`와 실제 서버 kill/restart/resume를 확인한 뒤 커밋한다.

**통과 기준:** 사용자 질문 1회에 모든 슬롯 수집, 승인·정정 중복 제출 안전, SSE 재접속 artifact 중복 없음, 다른 run 취소/읽기 불가.

## Task 8: 실제 조회 결과 기반 Python 샌드박스

**Files**
- Create: `harness/runtime/__init__.py`, `container.py`, `worker.py`, `Dockerfile`, `harness/tools/python_tools.py`
- Create: `harness/tests/test_python_runtime.py`, `harness/tests/test_python_isolation.py`
- Reuse: `repl_agent/runtime/base.py`의 ExecutionResult/PlotArtifact 계약

**Interfaces:** `run_python(code, input_result_ids) → ToolObservation`; runtime `execute`, `cancel`, `close_session`. 실행 결과에는 code artifact ref와 source_result_ids를 기록한다.

- [ ] 실제 저장소의 전체 결과로 통계값을 계산하고 fixture preview 50행으로 계산한 값과 구분하는 테스트를 먼저 작성한다.
- [ ] 컨테이너 이미지를 고정하고 라이브러리를 사전 설치한다. 격리 옵션을 코드에서 강제하고 승인된 workspace/data만 mount한다.
- [ ] 명시한 result ID를 DataFrame 목록으로 로드한다. 실제 DB 조회는 서버의 도메인 도구만 수행하고 컨테이너에 자격증명을 전달하지 않는다.
- [ ] stdout/stderr 제한, 예외 줄 번호, timeout/cancel/runtime_lost, Plotly artifact를 같은 관측 계약으로 반환한다. 예외 후 모델이 코드를 수정할 수 있게 한다.
- [ ] 성공한 코드·입력 checksum·표·차트를 저장한다. runtime이 사라져도 이 자료를 다시 읽고 이어갈 수 있도록 한다. 메모리 변수 직렬화를 재개 계약으로 사용하지 않는다.
- [ ] 호스트 파일·다른 세션 workspace·네트워크 접근 거부, 메모리/PID/시간 제한, 컨테이너 없는 환경의 unavailable 상태를 실제 컨테이너로 검사한다.
- [ ] `pytest harness/tests/test_python_runtime.py harness/tests/test_python_isolation.py -q`와 실제 Oracle 결과→Python→artifact E2E 후 커밋한다.

**통과 기준:** 모델이 실행한 코드의 숫자를 원본 데이터로 재계산해 일치 확인한다. 실제 데이터 분석 실패를 mock으로 대체하지 않는다. 단순 별도 프로세스 실행을 격리 통과로 인정하지 않는다.

## Task 9: 모든 도메인 기능과 기존 UI 전환

**Files**
- Create: `harness/tools/analysis_tools.py`, `harness/tools/artifact_tools.py`, `harness/tests/test_tool_parity.py`
- Modify where needed: `map_agent.py`, `fail_history_tools.py`, `relation_tree_agent.py`, `wt_resp_agent.py`, `mining_agent.py`, `ppt_export_agent.py`
- Modify: `yield_frontend/src/types.ts`, `yield_frontend/src/lib/stream.ts`, `yield_frontend/src/App.tsx`, `yield_frontend/src/components/AgentPlan.tsx`, `yield_frontend/src/components/Hitl.tsx`, `yield_frontend/src/components/Artifacts.tsx`
- Create: `yield_frontend/e2e/harness.spec.ts`, `yield_frontend/playwright.config.ts`
- Modify: `yield_frontend/package.json`, `package-lock.json`（브라우저 검증 실행 명령 추가）

**Interfaces:** 설계 §6의 나머지 도구와 §10 이벤트. 기존 UI에 최종 계획·진행·근거·중지·재개를 표시한다.

- [ ] 9개 worker의 기능별 입력/출력/필수 외부 서비스 목록을 표로 만들고 각 도구의 동등성 사례를 테스트에 추가한다.
- [ ] 순수 조회·계산·렌더러를 어댑터에 연결한다. 모듈 전역 state를 여러 세션이 공유하지 않는지 확인한다. 전문 LLM 호출은 사용량과 parent invocation을 추적한다.
- [ ] artifact 생성 시 source result ID를 남기고 export를 idempotency key와 고유 경로로 처리한다. 기존 HTML·이미지·PPTX와 Python 차트 모두 접근 가능하게 한다.
- [ ] UI에서 작업 기록은 수정 가능한 계획으로 표시한다. 현재 행동·완료 항목·미해결·최종 근거를 표시하고 내부 reasoning 원문은 노출하지 않는다.
- [ ] SSE parser에 event ID/sequence를 지원하고 종료 상태를 구분한다. 사용자에게 읽기 조사마다 plan_review 승인을 요구하는 동작은 새 경로에서 제거한다.
- [ ] API 이벤트 테스트, 프론트 build, 실제 브라우저의 조회→조사→chart→중지→재개를 확인하고 커밋한다.

**검증 명령:** `pytest harness/tests/test_tool_parity.py -q`; 프론트 디렉터리에서 `npm run build`, `npx playwright test e2e/harness.spec.ts`.

**통과 기준:** artifact DOM 존재만 검사하지 않는다. 표시된 수치/제품/기간/result ID를 실제 API 결과와 비교하고 버튼 동작 후 서버 상태를 확인한다. 프론트 기존 디자인의 전면 개편은 하지 않는다.

## Task 10: 독립 조사와 제한된 병렬 실행

**Files**
- Create: `harness/delegation.py`, `harness/tests/test_delegation.py`
- Modify: `harness/tools/registry.py`, `harness/executor.py`, `harness/control.py`, `harness/nodes.py`

**Interfaces:** `delegate_readonly(question, result_ids, allowed_tools, budget)`; child 결과는 observations/claims/limitations. 내부 principal/run lineage는 서버 주입.

- [ ] 독립된 WADS와 LOT 이력 조사에 각각 scope를 부여하는 테스트를 작성한다. child가 공유 목표를 수정하거나 parent 예산을 넘어설 때 거부하는 사례를 포함한다.
- [ ] parent와 child의 맥락·workspace·invocation ID를 분리하고 결과 참조만 공유한다. 재위임 깊이는 1, 동시에 2개까지만 허용한다.
- [ ] 예산 예약을 원자적으로 처리하고 모든 child 사용량을 root에 합산한다. 전체 취소는 child와 해당 외부 호출까지 전달한다.
- [ ] 같은 결과에 의존하는 호출은 순서대로 실행한다. 동시에 가능한 호출은 read-only이고 서로 결과 의존성이 없을 때만 실행한다.
- [ ] child 질문·실패·상충하는 결론을 root 관측으로 반환한다. root가 검증 후 최종 답변을 결정한다.
- [ ] `pytest harness/tests/test_delegation.py -q`와 실제 독립 조사 비교 실행 후 커밋한다.

**통과 기준:** 병렬 실행으로 데이터 scope나 취소가 섞이지 않으며 순차 결과와 같은 근거를 보존한다. 이 단계는 항상 여러 agent를 호출하는 정책을 추가하지 않는다.

## Task 11: MCP 도메인 서버

**Files**
- Create: `harness/mcp_server.py`, `harness/tests/test_mcp_contract.py`, `harness/evals/mcp_smoke.py`
- Read/reuse: `harness/tools/registry.py`, `harness/store.py`, 현재 MCP SDK

**Interfaces:** 로컬 stdio tools/list와 tools/call. domain 도구와 read_result만 노출하며 ask_user/finish/delegate_readonly는 제외한다.

- [ ] registry와 MCP 도구 schema가 같고 오류/empty/partial이 같은 의미로 전달되는 계약 테스트를 먼저 작성한다.
- [ ] MCP SDK 어댑터를 만들고 host principal을 서버 실행 설정에서 바인딩한다. 도구 인자로 principal/다른 세션에 대한 권한을 받지 않는다. registry를 공통 executor를 통해 호출하며 host session별 한도·동시성·도구 timeout·ledger를 적용한다.
- [ ] 구조화된 결과와 artifact ref를 반환한다. 로컬 artifact는 접근 가능한 resource로 제공하고 저장소 임의 경로 읽기는 막는다.
- [ ] 실제 stdio MCP 클라이언트에서 목록→수율 조회→result 읽기→후속 조회를 수행한다. 기존 하네스를 다시 호출하는 재귀 도구를 노출하지 않는다.
- [ ] 동일 질문을 자체 하네스와 Codex+MCP에서 실행해 tool contract·자료 접근이 일치하는지 확인한다. 두 호스트의 답변 문구/호출 순서 일치를 요구하지 않는다.
- [ ] `pytest harness/tests/test_mcp_contract.py -q`, `python -m harness.evals.mcp_smoke --require-live` 실행 후 커밋한다.

**통과 기준:** MCP 연결 여부와 자체 하네스의 판단 품질을 분리해 보고한다. 외부 호스트 연결이 아직 없으면 MCP 계약 테스트만 통과한 것으로 기록한다.

## Task 12: 실제 E2E·전환·롤백

**Files**
- Create: `harness/evals/run.py`, `cases.json`, `report.py`, `harness/evals/README.md`
- Create: `harness/tests/test_rollout.py`
- Modify: `agent_server.py`, 프로젝트 루트 `.env.example`, 애플리케이션 루트 `AGENTS.md`
- Reference: 기존 `tests/e2e_client.py`, `tests/test_e2e_regression.py`, 본 설계

**Interfaces:** `python -m harness.evals.run --require-live --repetitions 3 --output PATH`, 평가 manifest와 결과 보고서. 아직 없는 명령이며 이 단계의 구현 산출물이다.

- [ ] eval runner는 preflight 실패·필수 case skip·fixture/mock 출처를 만나면 비영(非零) exit로 종료한다. 불완전한 실험도 manifest에 남긴다.
- [ ] DB의 실제 커버리지를 읽어 기준 날짜·제품·LOT·검사 단계를 고정한다. 현재 날짜의 “최근 4주”에 데이터가 없다면 그 사실을 평가하고, 복합 조사 검증은 실제 데이터가 있는 별도의 명시 기간으로 실행한다. 두 경우를 혼동하지 않는다.
- [ ] 아래 평가 행렬을 3회씩 반복하고 도구 호출·원본 결과·SSE·최종 근거를 함께 저장한다. 실제 LLM의 경로 선택은 특정 문장/도구 순서와 일치하는지 대신 목표 충족과 근거로 평가한다.
- [ ] 복구/격리/취소/동시성은 fault injection으로 별도 검증한다. 비용을 줄이기 위한 scripted tests 결과와 실제 LLM 결과를 분리한다.
- [ ] legacy와 새 runtime을 동일 조건으로 비교한다. 핵심 수치/LOT/기간은 같아야 하며, 원인 분석은 근거 충족도·미실행 항목·불필요한 승인 수·호출 수·지연·사용량으로 비교한다.
- [ ] 새 세션만 feature flag로 전환한다. 기존 active legacy 세션은 기존 엔진에서 끝내거나 명시적 새 run으로 옮긴다. 체크포인트를 억지로 변환하지 않는다.
- [ ] 이전 완료 대화는 원문·최종 답변을 불러오고 호환 result만 참조한다. 원본이 없고 표본만 남은 결과는 degraded로 표시하고 필요하면 다시 조회한다.
- [ ] 새 하네스 활성 run은 flag rollback만으로 legacy로 보내지 않는다. 새 run 접수만 멈추고 실행 중인 run은 같은 runtime에서 종료/취소·복구한다.
- [ ] 아래 release gate를 모두 통과한 결과를 문서화한 후 기본값 전환을 확정하고 운영 문서를 갱신한다.

### 실제 평가 행렬

| 시나리오 | 확인할 결과 |
|---|---|
| 인사/기능 설명 | DB·Python 실행 없이 답변 |
| 최근 4주 수율 | 제품·주차·계산·표가 실제 DB와 일치 |
| 수율+열화 원인 | 관측 결과에 맞는 후속 조사, 근거/가설 구분 |
| 수율은 정상 | 근거 없이 열화 원인을 만들어내지 않음 |
| WADS 0행 또는 일부 자료 누락 | empty/partial 관측을 반영, 성공 위장 없음 |
| 이전 보고서/LOT 재참조 | 정확한 result ID와 범위 유지 |
| 30턴 뒤 이전 자료 비교 | 압축 후 재조회 가능, tool-call 쌍 정상 |
| 제품·기간을 실행 중 수정 | goal revision 갱신, 이전 결과 무단 재사용 없음 |
| 여러 필수 입력 누락 | 한 번의 구조화 질문, 답변 후 동일 실행 재개 |
| LLM 401 / 429 / 네트워크 timeout | 오류 분류, 올바른 재시도·중단 |
| Python 계산·오류 교정·차트 | 실제 원본 사용, 수치 재계산 일치 |
| Python 무한 루프·메모리 초과 | 제한 시간/자원 안에서 종료, 앱 응답 유지 |
| SSE 단절·재접속·서버 재시작 | 실행/이벤트/결과 복원, 성공 호출 중복 없음 |
| 중복 POST·동시 요청·취소 경쟁 | run/terminal 상태 하나, 늦은 응답 무시 |
| 맵·검색·연관분석·마이닝·PPT | 기존 도메인 기능과 결과 동등성 |
| 독립 child 실패·취소 | root 예산·상태 일관성, 부분 근거 보존 |
| 다른 세션 result/workspace 접근 | 일관된 접근 거부 |
| MCP 호출과 자체 도구 호출 | 같은 schema·데이터·권한 결과 |

### Release gate

- 필수 결정형 테스트 100% 통과, mandatory live case skip 0개.
- 실제 읽기 결과의 제품·기간·행 수·수치 비교 100% 일치. 허용 오차는 도구별 수치 연산 특성으로 명시한다.
- 동적 조사 케이스는 case별 3회 중 최소 2회, 전체 반복의 90% 이상에서 목표/근거 rubric 통과. 이는 초기 합격선이며 테스트 실행 결과와 함께 공개한다.
- 접근권한 위반·완료된 invocation 중복 부작용·예산 종료의 성공 위장·존재하지 않는 근거 인용은 0건.
- 고위험 인과 단정이나 핵심 요청 누락이 평가에 남으면 평균 점수와 무관하게 전환 보류.
- 실제 서비스/LLM 호출 증거가 없으면 해당 항목은 미검증. 모의 데이터·문장 유사도·LLM judge 단독 평가로 대체하지 않음.
- 지연/호출/비용은 baseline 대비 실측 보고. 단순 조회에 복합 조사 비용을 상시 부과하지 않는지 확인하고 성능 회귀 이유를 기록한다.
- 기본값 전환과 롤백 모두 재현 완료.

## 4. 구현 시 검증 명령

개발 의존성을 추가한 이후 프로젝트 루트에서 실행한다. `harness/evals` 명령은 해당 태스크에서 구현한다.

```bash
uv sync --group dev
uv run pytest 08-YieldAgent/harness/tests -q
```

애플리케이션 루트에서 실제 서버·평가를 실행한다.

```bash
../.venv/bin/python -m harness.evals.preflight --require-live
../.venv/bin/python -m uvicorn agent_server:app --port 8001
```

다른 터미널의 같은 디렉터리에서:

```bash
../.venv/bin/python -m harness.evals.run --require-live --repetitions 3 --output ../outputs/harness-eval
../.venv/bin/python -m harness.evals.mcp_smoke --require-live
```

단계별 테스트는 `PYTHONPATH`에 애플리케이션 루트를 포함하는 `harness/tests/conftest.py`를 Task 0에서 추가한다. 기존 `08-YieldAgent/tests/conftest.py`의 서버 자동 skip은 새 테스트 디렉터리에 적용하지 않는다. 프론트 검증은 해당 프론트 디렉터리에서 수행한다.

## 5. 최종 인수 결과물

1. 자체 웹 앱에서 실행되는 목표 기반 하네스와 기존 9개 도메인 도구.
2. 실제 자료를 대상으로 실행하는 Python workspace와 저장된 코드·차트·보고서.
3. 목표·근거·호출·비용·오류·종료 사유를 재현 가능한 실행 기록.
4. 질문·취소·정정·재접속·서버 재시작·독립 조사 지원.
5. Codex에서도 같은 도구를 호출하는 로컬 MCP 서버.
6. 실제 DB·LLM·외부 도구 실행 증거가 담긴 E2E 보고서와 전환/롤백 절차.

구현 순서는 0→1→2→3→4→5→6→7→8→9→10→11→12다. 먼저 단계 3에서 실제 결과 기반 후속 행동을 입증하고, 전체 범위를 단계 12까지 완성한다. 런타임 전환 전까지 구버전은 비교·롤백 대상으로 유지한다.
