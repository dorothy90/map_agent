# Langfuse 트레이싱 가이드

Yield Query Agent의 LLM 호출, 노드 실행, API 호출 등을 Langfuse로 추적하고 분석하는 방법을 정리합니다.

---

## 1. 개요

### Langfuse란?
- LLM 애플리케이션을 위한 **오픈소스 Observability 플랫폼**
- LangChain/LangGraph와 네이티브 통합 지원
- 클라우드 / 셀프호스팅 모두 가능

### 추적 가능한 항목
| 항목 | 설명 |
|------|------|
| **Traces** | 사용자 쿼리 → 최종 응답까지의 전체 실행 흐름 |
| **Spans** | 각 노드/함수 단위 실행 구간 (supervisor, yield_agent 등) |
| **Generations** | LLM 호출 상세 (프롬프트, 응답, 토큰 수, 지연시간) |
| **Latency** | 노드별, LLM 호출별 지연시간 |
| **Token Usage** | input/output/total 토큰 수 |
| **Cost** | 모델별 비용 추산 |

---

## 2. 환경 설정

### 2.1 패키지 설치

```bash
pip install langfuse
```

### 2.2 환경변수 (.env)

```env
# Langfuse 설정
LANGFUSE_SECRET_KEY = "sk-lf-..."
LANGFUSE_PUBLIC_KEY = "pk-lf-..."
LANGFUSE_HOST = "https://hipaa.cloud.langfuse.com"
```

> **참고**: 셀프호스팅 시 `LANGFUSE_HOST`를 내부 서버 URL로 변경하면 됩니다.

### 2.3 Langfuse 계정 생성 (클라우드)

1. [https://cloud.langfuse.com](https://cloud.langfuse.com) 접속
2. 회원가입 후 프로젝트 생성
3. Settings > API Keys에서 Secret Key / Public Key 복사
4. `.env` 파일에 붙여넣기

---

## 3. 코드 적용 방법

### 3.1 콜백 핸들러 (LangGraph 통합)

`yield_query_agent.py`에서 `LangfuseCallbackHandler`를 생성하고, 그래프 실행 시 `config`로 전달합니다.

```python
from langfuse.callback import CallbackHandler as LangfuseCallbackHandler

# 환경변수에서 키 자동 로드
langfuse_handler = LangfuseCallbackHandler()

# 그래프 실행 시 콜백 전달
final_state = yield_supervisor.invoke(
    initial_state,
    config={"callbacks": [langfuse_handler]},
)

# 또는 stream() 모드에서
for step in yield_supervisor.stream(
    initial_state,
    config={"callbacks": [langfuse_handler]},
):
    ...
```

이것만으로 LangGraph의 **모든 노드 실행과 LLM 호출**이 자동으로 추적됩니다.

### 3.2 @observe 데코레이터 (커스텀 스팬)

LLM이 아닌 함수(API 호출, 데이터 처리 등)도 추적하려면 `@observe` 데코레이터를 사용합니다.

```python
from langfuse.decorators import observe

@observe(name="fetch_weekly_data")
def _fetch_weekly_data(lotcd: str, date_str: str) -> dict | None:
    """FastAPI에서 1주치 데이터 조회"""
    ...

@observe(name="analyze_with_llm")
def _analyze_with_llm(weeks_data, table_str, lotcd, llm) -> str:
    """LLM에게 열화/개선 분석 요청"""
    ...

@observe(name="supervisor_node")
def supervisor_node(state):
    """Supervisor 노드"""
    ...
```

### 3.3 flush() 호출

Langfuse는 트레이스를 **비동기**로 전송합니다. 프로세스 종료 전에 반드시 `flush()`를 호출하세요.

```python
# 스크립트 종료 시
langfuse_handler.flush()

# Streamlit에서는 각 요청 처리 후
langfuse_handler.flush()
```

---

## 4. 현재 적용 구조

### yield_query_agent.py

```
사용자 쿼리 입력
    │
    ▼
┌──────────────────────┐
│  supervisor_node     │ ← @observe + @timed
│  (LLM: 쿼리 파싱)    │    → Langfuse: Generation (토큰, 지연시간)
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌──────────┐ ┌──────────────┐
│yield_agent│ │  wads_agent  │ ← @observe + @timed
│   node   │ │    node      │
└─────┬────┘ └──────────────┘
      │
      ├─ _fetch_4_weeks()      ← @observe (API 호출 추적)
      │   └─ _fetch_weekly_data() × 4  ← @observe (개별 API 추적)
      │
      └─ _analyze_with_llm()   ← @observe (LLM 분석 추적)
           └─ model.invoke()        → Langfuse: Generation
```

### app.py (Streamlit)

```python
# stream() 모드로 노드별 실시간 진행 표시
with st.status("에이전트 처리 중...", expanded=True) as status:
    for step_output in yield_supervisor.stream(
        initial_state,
        config={"callbacks": [langfuse_handler]},
    ):
        for node_name, node_state in step_output.items():
            st.write(f"[{elapsed:.1f}s] {node_name} 노드 완료")

    status.update(label=f"완료! (총 {total_time:.1f}초)", state="complete")

langfuse_handler.flush()
```

---

## 5. Langfuse 대시보드 사용법

### 5.1 Traces 탭

대시보드 접속: [https://cloud.langfuse.com](https://cloud.langfuse.com)

- **Traces 목록**: 각 사용자 쿼리가 하나의 Trace로 표시
- **Trace 상세**: 클릭하면 노드별 실행 흐름을 **워터폴 차트**로 확인

```
예시 Trace:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
│ yield_supervisor (총 18.3s)
│ ├── supervisor_node (3.2s)
│ │   └── ChatOpenAI Generation (3.1s)
│ │       input: 142 tokens
│ │       output: 89 tokens
│ ├── yield_agent_node (15.1s)
│ │   ├── fetch_4_weeks (0.8s)
│ │   │   ├── fetch_weekly_data W03 (0.2s)
│ │   │   ├── fetch_weekly_data W04 (0.2s)
│ │   │   ├── fetch_weekly_data W05 (0.2s)
│ │   │   └── fetch_weekly_data W06 (0.2s)
│ │   └── analyze_with_llm (14.2s)    ← 병목!
│ │       └── ChatOpenAI Generation (14.1s)
│ │           input: 856 tokens
│ │           output: 423 tokens
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 5.2 주요 확인 포인트

| 확인 항목 | 위치 | 활용 |
|-----------|------|------|
| LLM 응답 지연 | Trace > Generation | 모델 변경 검토 (120B → 더 작은 모델) |
| 프롬프트 내용 | Generation > Input | 프롬프트 최적화 |
| 토큰 사용량 | Generation > Usage | 비용 절감 |
| API 호출 시간 | Trace > Span | 병렬화 검토 |
| 에러 발생 | Trace > Status | 실패 원인 분석 |

### 5.3 Generations 탭

LLM 호출만 필터링하여 확인:
- **모델별** 평균 지연시간
- **프롬프트 / 응답** 전문 확인
- **토큰 사용량** 추이

### 5.4 Dashboard 탭

전체 통계 요약:
- 일별 Trace 수
- 평균 지연시간 추이
- 토큰 사용량 추이
- 에러율

---

## 6. 성능 분석 및 최적화 팁

### 6.1 병목 파악

Langfuse 대시보드에서 워터폴 차트를 보면 어디가 느린지 바로 확인할 수 있습니다.

**일반적인 병목 구간:**

| 구간 | 예상 소요 | 최적화 방법 |
|------|-----------|-------------|
| `supervisor_node` LLM 호출 | 2~5초 | 프롬프트 축소, 더 빠른 모델 사용 |
| `_fetch_4_weeks` API 순차 호출 | 0.5~2초 | asyncio로 병렬 호출 |
| `_analyze_with_llm` LLM 호출 | 5~15초 | 프롬프트 축소, streaming 적용 |
| `wads_agent_node` 서브에이전트 | 5~20초 | 쿼리 최적화, 캐싱 |

### 6.2 터미널 로그 (timed 데코레이터)

Langfuse와 별개로 터미널에서도 실시간 타이밍 확인 가능:

```
23:25:01 [yield_agent] INFO ▶ supervisor_node 시작
23:25:04 [yield_agent] INFO ◀ supervisor_node 완료 (3.21s)
23:25:04 [yield_agent] INFO ▶ yield_agent_node 시작
23:25:04 [yield_agent] INFO ▶ _fetch_4_weeks 시작
23:25:04 [yield_agent] INFO ▶ _fetch_weekly_data 시작
23:25:04 [yield_agent] INFO ◀ _fetch_weekly_data 완료 (0.12s)
...
23:25:05 [yield_agent] INFO ▶ _analyze_with_llm 시작
23:25:15 [yield_agent] INFO ◀ _analyze_with_llm 완료 (10.34s)
23:25:15 [yield_agent] INFO ◀ yield_agent_node 완료 (11.28s)
```

### 6.3 Streamlit UI 진행 표시

`stream()` 모드 적용으로 사용자에게 실시간 진행 상황 표시:

```
에이전트 처리 중...
  [3.2s] supervisor 노드 완료
  [15.1s] yield_agent 노드 완료
완료! (총 15.1초)
```

---

## 7. 고급 활용

### 7.1 세션 추적

같은 사용자의 대화 흐름을 하나의 세션으로 묶으려면:

```python
langfuse_handler = LangfuseCallbackHandler(
    session_id="user_session_123",
    user_id="daehwan",
)
```

### 7.2 메타데이터 추가

특정 트레이스에 메타데이터를 붙이려면:

```python
langfuse_handler = LangfuseCallbackHandler(
    metadata={"lotcd": "4SS", "query_type": "yield"},
    tags=["production", "yield_agent"],
)
```

### 7.3 사용자 피드백 연동

Langfuse의 Score API를 통해 사용자 피드백을 트레이스에 연결:

```python
from langfuse import Langfuse

langfuse = Langfuse()

# 특정 트레이스에 점수 부여
langfuse.score(
    trace_id="trace-xxx",
    name="user_feedback",
    value=1,  # 1 = 좋음, 0 = 나쁨
    comment="정확한 분석이었습니다",
)
```

### 7.4 셀프호스팅

데이터를 외부로 보내고 싶지 않다면 Docker로 셀프호스팅:

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

`.env`에서 호스트만 변경:
```env
LANGFUSE_HOST = "http://localhost:3000"
```

---

## 8. 트러블슈팅

### 트레이스가 보이지 않는 경우

1. **환경변수 확인**: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` 세 가지 모두 설정되었는지 확인
2. **flush() 호출**: 프로세스 종료 전에 `langfuse_handler.flush()` 호출 확인
3. **네트워크 확인**: Langfuse 서버에 접근 가능한지 확인
4. **디버그 모드**: `LANGFUSE_DEBUG=true` 환경변수를 설정하면 전송 과정 로그 출력

### @observe 데코레이터가 작동하지 않는 경우

- `@observe`는 **활성 트레이스 컨텍스트** 안에서만 동작합니다
- `LangfuseCallbackHandler`를 통해 invoke/stream 호출 시 자동으로 컨텍스트가 생성됩니다
- 단독 함수 테스트 시에는 `@observe`가 최상위 트레이스를 자동 생성합니다

### 토큰 사용량이 표시되지 않는 경우

- OpenRouter 경유 시 일부 모델은 `usage` 정보를 반환하지 않을 수 있습니다
- `ChatOpenAI`의 `model_kwargs`에서 `stream_options` 등을 확인하세요
