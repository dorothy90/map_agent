# LangGraph V1 튜토리얼

LangGraph V1의 핵심 개념과 실전 활용 방법을 다루는 한국어 Jupyter Notebook 튜토리얼 모음입니다. 초보자부터 중급 개발자까지 LangGraph를 활용한 AI 에이전트 개발 방법을 단계별로 학습할 수 있습니다.

## 목차

1. [환경 설정](#환경-설정)
2. [튜토리얼 목록](#튜토리얼-목록)
3. [시작하기](#시작하기)
4. [참고 자료](#참고-자료)

## 환경 설정

### 1. UV 설치

UV는 빠르고 효율적인 Python 패키지 관리자입니다.

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 가상 환경 생성 및 활성화

```bash
# 가상 환경 생성
uv venv

# 가상 환경 활성화 (macOS/Linux)
source .venv/bin/activate

# 가상 환경 활성화 (Windows)
.venv\Scripts\activate
```

### 3. 의존성 설치

```bash
# pyproject.toml 기반 설치
uv sync

# 또는 직접 패키지 설치
uv add install langchain langchain-openai langchain-anthropic langchain-community langgraph python-dotenv
```

### 4. 환경 변수 설정

`.env.example` 파일을 `.env`로 복사하고 API 키를 설정합니다:

```bash
cp .env.example .env
```

`.env` 파일 내용:
```
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
```

## 튜토리얼 목록

### 기초 개념

1. **[langgraph-messages.ipynb](langgraph-messages.ipynb)**
   - 메시지 구조와 타입 (HumanMessage, AIMessage, SystemMessage)
   - 메시지 히스토리 관리
   - 대화 컨텍스트 구성

2. **[langgraph-tools.ipynb](langgraph-tools.ipynb)**
   - Tool 정의와 등록
   - 함수 기반 Tool 생성
   - Tool 실행 및 에러 처리
   - ToolRuntime 활용

3. **[langgraph-structured-output.ipynb](langgraph-structured-output.ipynb)**
   - Pydantic을 활용한 구조화된 출력
   - 응답 형식 검증
   - JSON 스키마 정의

### 상태 관리

4. **[langgraph-short-term-memory.ipynb](langgraph-short-term-memory.ipynb)**
   - Checkpointer를 활용한 세션 상태 저장
   - thread_id 기반 대화 관리
   - InMemorySaver와 PostgresSaver
   - 메모리 관리 패턴 (trim, delete, summarize)

5. **[langgraph-long-term-memory.ipynb](langgraph-long-term-memory.ipynb)**
   - Store를 활용한 영구 데이터 저장
   - Namespace와 Key 구조
   - 사용자 선호도 및 학습 데이터 관리
   - 세션 간 정보 공유

### 미들웨어와 컨텍스트

6. **[langgraph-middleware.ipynb](langgraph-middleware.ipynb)**
   - before_model, after_model 미들웨어
   - wrap_model_call을 활용한 요청/응답 가로채기
   - dynamic_prompt로 동적 시스템 프롬프트 생성
   - 미들웨어 체이닝

7. **[langgraph-runtime.ipynb](langgraph-runtime.ipynb)**
   - Runtime 객체 구조 (Context, Store, Stream writer)
   - Tool과 미들웨어에서 Runtime 접근
   - 사용자 컨텍스트 관리
   - 정적 설정과 동적 컨텍스트

8. **[langgraph-context-engineering.ipynb](langgraph-context-engineering.ipynb)**
   - Model Context 엔지니어링
   - Tool Context 최적화
   - Life-cycle Context 관리
   - 상태 기반 동적 프롬프트와 Tool 선택

### 스트리밍과 안전성

9. **[langgraph-streaming.ipynb](langgraph-streaming.ipynb)**
   - Stream 모드: updates, messages, custom
   - LLM 토큰 스트리밍
   - get_stream_writer()를 활용한 커스텀 업데이트
   - 실시간 진행 상황 보고

10. **[langgraph-guardrails.ipynb](langgraph-guardrails.ipynb)**
    - PII 탐지 및 보호 (이메일, 전화번호, 신용카드)
    - Redact, Mask, Hash, Block 전략
    - Human-in-the-Loop 미들웨어
    - 커스텀 Guardrail 구현

11. **[langgraph-human-in-the-loop.ipynb](langgraph-human-in-the-loop.ipynb)**
    - 민감한 작업에 대한 사람의 승인
    - Interrupt 설정 및 의사결정 (approve, edit, reject)
    - Command 객체를 통한 재개
    - 금융 및 고객 지원 시스템 예제

### 고급 패턴

12. **[langgraph-multi-agent.ipynb](langgraph-multi-agent.ipynb)**
    - Tool Calling 패턴 (Supervisor + Subagents)
    - Handoffs 패턴 (에이전트 전환)
    - 계층적 에이전트 시스템
    - 고객 지원 멀티에이전트 시스템

13. **[langgraph-retrieval.ipynb](langgraph-retrieval.ipynb)**
    - RAG (Retrieval-Augmented Generation) 패턴
    - 2-Step RAG, Agentic RAG, Hybrid RAG
    - Vector Store와 Retriever
    - 지식 베이스 구축 및 Q&A 시스템

14. **[langgraph-mcp.ipynb](langgraph-mcp.ipynb)**
    - MCP (Model Context Protocol) 개요
    - Transport 타입: stdio, HTTP, SSE
    - FastMCP를 활용한 커스텀 서버 구현
    - Stateful vs Stateless 세션

## 시작하기

### 1. 저장소 클론

```bash
git clone <repository-url>
cd langgraph-v1-tutorial
```

### 2. 환경 설정

위의 [환경 설정](#환경-설정) 섹션을 따라 UV를 설치하고 의존성을 설치합니다.

### 3. Jupyter Notebook 실행

```bash
# Jupyter Lab 설치 (선택사항)
uv pip install jupyterlab

# Jupyter Lab 실행
jupyter lab
```

또는 VS Code의 Jupyter 확장을 사용할 수 있습니다.

### 4. 튜토리얼 순서

다음 순서로 학습하는 것을 권장합니다:

**입문:**
1. langgraph-messages.ipynb
2. langgraph-tools.ipynb
3. langgraph-structured-output.ipynb

**메모리 관리:**
4. langgraph-short-term-memory.ipynb
5. langgraph-long-term-memory.ipynb

**미들웨어:**
6. langgraph-middleware.ipynb
7. langgraph-runtime.ipynb
8. langgraph-context-engineering.ipynb

**고급 기능:**
9. langgraph-streaming.ipynb
10. langgraph-guardrails.ipynb
11. langgraph-human-in-the-loop.ipynb

**실전 패턴:**
12. langgraph-multi-agent.ipynb
13. langgraph-retrieval.ipynb
14. langgraph-mcp.ipynb

## 필수 요구사항

- **Python**: 3.9 이상
- **API Keys**:
  - OpenAI API Key (GPT-4 모델 사용)
  - Anthropic API Key (Claude 모델 사용, 선택사항)
- **기본 지식**:
  - Python 프로그래밍 기초
  - 비동기 프로그래밍 개념 (async/await)
  - LLM 및 프롬프트 엔지니어링 기초

## 프로젝트 구조

```
langgraph-v1-tutorial/
├── README.md                              # 프로젝트 문서
├── .env.example                           # 환경 변수 템플릿
├── .env                                   # 환경 변수 (gitignore)
├── pyproject.toml                         # 프로젝트 설정 및 의존성
├── uv.lock                                # 의존성 잠금 파일
│
├── langgraph-messages.ipynb               # 메시지 구조
├── langgraph-tools.ipynb                  # Tool 정의와 사용
├── langgraph-structured-output.ipynb      # 구조화된 출력
├── langgraph-short-term-memory.ipynb      # 단기 메모리
├── langgraph-long-term-memory.ipynb       # 장기 메모리
├── langgraph-middleware.ipynb             # 미들웨어
├── langgraph-runtime.ipynb                # Runtime 객체
├── langgraph-context-engineering.ipynb    # 컨텍스트 엔지니어링
├── langgraph-streaming.ipynb              # 스트리밍
├── langgraph-guardrails.ipynb             # 보안 및 안전장치
├── langgraph-human-in-the-loop.ipynb      # 사람의 개입
├── langgraph-multi-agent.ipynb            # 멀티에이전트 시스템
├── langgraph-retrieval.ipynb              # RAG 패턴
└── langgraph-mcp.ipynb                    # Model Context Protocol
```

## 참고 자료

### 공식 문서
- [LangChain Documentation](https://docs.langchain.com/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Python API Reference](https://api.python.langchain.com/)

### 관련 리소스
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Anthropic Claude Documentation](https://docs.anthropic.com/)
- [UV Documentation](https://docs.astral.sh/uv/)

### 커뮤니티
- [LangChain GitHub](https://github.com/langchain-ai/langchain)
- [LangGraph GitHub](https://github.com/langchain-ai/langgraph)
- [LangChain Discord](https://discord.gg/langchain)

## 라이선스

이 튜토리얼은 교육 목적으로 제공됩니다.

## 기여하기

튜토리얼 개선 사항이나 오류를 발견하신 경우 Issue를 생성하거나 Pull Request를 제출해 주세요.

---

**Happy Learning with LangGraph V1!**
