# QuickStart - LangGraph 빠른 시작

## 개요

LangGraph를 처음 접하는 개발자들을 위한 빠른 시작 가이드입니다. 기본 개념부터 핵심 기능까지 단계별로 학습할 수 있도록 구성되어 있습니다.

> 참고 문서: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api.md)

---

## 학습 순서

각 노트북을 순서대로 진행하시면 LangGraph의 기본기를 체계적으로 익힐 수 있습니다.

### 01. LangGraph 기본 튜토리얼

**파일:** `01-QuickStart-LangGraph-Tutorial.ipynb`

LangGraph의 핵심 기능을 단계별로 학습합니다.

| 주제 | 설명 |
|------|------|
| 기본 챗봇 구축 | StateGraph, State, Node, Edge 개념 이해 |
| 도구(Tools) 추가 | Tool Binding, ToolNode, Conditional Edges |
| 메모리 추가 | Checkpointer, Thread ID, 장기 기억 관리 |
| Human-in-the-Loop | interrupt, Command, 인간 승인 워크플로우 |
| 상태 커스터마이징 | Custom State Fields, Command(update=...) |
| 상태 이력 관리 | State History, Checkpoint ID, Rollback/Resume |

---

### 02. Graph API 심화

**파일:** `02-QuickStart-LangGraph-Graph-API.ipynb`

LangGraph Graph API의 심화 기능을 학습합니다.

| 주제 | 설명 |
|------|------|
| State 심화 | Reducers, 메시지 처리, 다중 스키마 |
| Nodes 심화 | Config 스키마, 노드 매개변수, 노드 캐싱 |
| Edges 심화 | 조건부 엣지, 매핑 기반 라우팅, 병렬 실행 |
| Send | 동적 라우팅, Map-Reduce 패턴 |
| Command | 상태 업데이트와 제어 흐름 통합 |

---

### 03. Ollama를 활용한 로컬 LLM 에이전트

**파일:** `03-QuickStart-LangGraph-Ollama.ipynb`

Ollama를 사용하여 로컬 환경에서 LLM 에이전트를 구축합니다.

| 주제 | 설명 |
|------|------|
| 에이전트 생성 | create_agent를 사용한 기본 에이전트 구축 |
| 도구(Tool) | 도구 정의, 컨텍스트(Context) 활용 |
| 응답 형식 | Pydantic 모델을 사용한 구조화된 응답 |
| 단기 메모리 | Checkpointer를 통한 대화 상태 유지 |
| 미들웨어 | Human in the Loop Middleware |

---

**Made by TeddyNote LAB**
