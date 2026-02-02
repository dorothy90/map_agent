# Basic - LangGraph 기초 개념

## 개요

LangGraph의 핵심 구성 요소를 학습하는 기초 튜토리얼입니다. 모델(LLM), 메시지, 그래프의 기본 개념을 이해하고 실제로 구현하는 방법을 다룹니다.

> 참고 문서: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api.md)

---

## 학습 순서

각 노트북을 순서대로 진행하시면 LangGraph의 기초 개념을 체계적으로 익힐 수 있습니다.

### 01. 모델 (LLM)

**파일:** `01-LangGraph-Models.ipynb`

LangGraph 에이전트의 핵심인 LLM 모델의 다양한 사용법을 학습합니다.

| 주제 | 설명 |
|------|------|
| 모델 초기화 | init_chat_model, ChatOpenAI 클래스 사용법 |
| 답변 출력 | invoke(), stream() 메서드 |
| 비동기 처리 | ainvoke(), astream() 메서드 |
| 배치 요청 | batch()를 통한 병렬 처리 |
| Tool Calling | bind_tools()를 사용한 도구 바인딩 |
| Structured Output | with_structured_output()으로 정형화된 응답 |
| 토큰 사용량 | UsageMetadataCallbackHandler 활용 |
| 멀티모달 | 이미지 입력 처리 |

---

### 02. 메시지 (Messages)

**파일:** `02-LangGraph-Messages.ipynb`

LangChain/LangGraph에서 모델과의 대화를 나타내는 메시지 시스템을 학습합니다.

| 주제 | 설명 |
|------|------|
| 기본 사용법 | 메시지 객체 생성 및 모델 호출 |
| 텍스트/메시지 프롬프트 | 문자열 vs 메시지 리스트 |
| SystemMessage | 모델 동작 지침 설정 |
| HumanMessage | 사용자 입력 및 메타데이터 |
| AIMessage | 모델 응답 및 도구 호출 정보 |
| ToolMessage | 도구 실행 결과 전달 |
| 메시지 콘텐츠 | 텍스트, 멀티모달 콘텐츠 처리 |

---

### 03. 그래프 생성 (Building Graphs)

**파일:** `03-LangGraph-Building-Graphs.ipynb`

LangGraph의 핵심인 StateGraph를 사용하여 워크플로우를 구축하는 방법을 학습합니다.

| 주제 | 설명 |
|------|------|
| StateGraph | 그래프 생성, 컴파일, 실행 |
| State | 스키마 정의, TypedDict 활용 |
| Reducers | 덮어쓰기, 리스트 추가, add_messages |
| MessagesState | 사전 정의된 메시지 상태 클래스 |
| Nodes | 노드 함수 정의, config 활용 |
| Edges | 일반 엣지, 조건부 엣지 |
| Send | 동적 병렬 실행, Map-Reduce 패턴 |
| Command | 상태 업데이트와 제어 흐름 통합 |
| Recursion Limit | 재귀 제한 설정 |

---

**Made by TeddyNote LAB**
