# 08-YieldAgent 백엔드 완전 해부 — 퀴즈 & 답변

---

## Part 1: 서버 부팅 (앱이 켜지는 순간)

### 배경 지식

서버 시작 시 `lifespan` 함수에서 두 가지 MongoDB 연결이 초기화됩니다.

```python
# agent_server.py:71-84
@asynccontextmanager
async def lifespan(app: FastAPI):
    motor_client = AsyncIOMotorClient("mongodb://localhost:27017")
    app.state.motor_db = motor_client["yield_agent"]

    with MongoDBSaver.from_conn_string(...) as checkpointer:
        app.state.graph = workflow.compile(checkpointer=checkpointer)
        yield

    motor_client.close()
```

| 연결 | 라이브러리 | 용도 | 동기/비동기 |
|---|---|---|---|
| `motor_client` | motor | 대화 이력(chat_turns) CRUD | **async** |
| `MongoDBSaver` | langgraph | LangGraph 상태 체크포인트 | **sync** |

CORS 설정:
```python
# agent_server.py:90-96
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    ...
)
```

---

### Q1. 서버 부팅 시 MongoDB 연결이 2개 만들어집니다. 각각 어떤 용도이고, 왜 따로 있을까요?

<details>
<summary>정답 보기</summary>

1. **motor (async)** — `chat_turns` 컬렉션에 대화 이력을 저장/조회하는 용도. FastAPI의 async 핸들러에서 직접 사용.
2. **MongoDBSaver (sync)** — LangGraph의 체크포인터. 그래프 실행 중 각 노드의 state를 자동으로 저장/복원하는 용도.

**따로 있는 이유:** LangGraph 체크포인터는 sync API만 제공하고, FastAPI의 REST API 핸들러는 async로 동작합니다. 용도도 다릅니다 — 하나는 "우리가 직접 만든 대화 이력", 다른 하나는 "LangGraph가 관리하는 그래프 상태"입니다.
</details>

---

### Q2. `app.state.graph`에 저장되는 것은 뭔가요? 이게 없으면 어떤 일이 벌어질까요?

<details>
<summary>정답 보기</summary>

`workflow.compile(checkpointer=checkpointer)`의 결과물 — 즉, **실행 가능한 LangGraph 컴파일드 그래프**입니다.

없으면 `POST /chat/stream`에서 `req.app.state.graph`를 참조할 때 `AttributeError`가 발생하여, 모든 채팅 요청이 실패합니다. 그래프가 없으면 에이전트 실행 자체가 불가능합니다.
</details>

---

### Q3. CORS에 `localhost:3000`과 `localhost:5173` 두 개가 있는 이유는?

<details>
<summary>정답 보기</summary>

- `localhost:3000` — Create React App (CRA)의 기본 포트
- `localhost:5173` — Vite의 기본 dev server 포트

React 프론트엔드를 어떤 도구로 실행하든 CORS 문제가 발생하지 않도록 두 가지 모두 허용한 것입니다.
</details>

---

## Part 2: 그래프 구조 (LangGraph StateGraph)

### 배경 지식

```python
# supervisor.py:549-601
class YieldQueryState(TypedDict):
    messages: Annotated[list, add_messages]        # reducer: ID 기반 추가/교체
    yield_artifacts: Annotated[list, operator.add]  # reducer: 단순 누적
    lotcd: str                                      # reducer 없음: 덮어쓰기
    ...
```

그래프 구조:
```
START → rewrite → supervisor ──→ yield_agent ──┐
                    ↑    │                      │
                    │    ├──→ wads_agent ───────┤
                    │    │                      │
                    │    ├──→ map_agent ────────┤
                    │    │                      │
                    │    └──→ END (FINISH)      │
                    └──────────────────────────┘
                         (루프 복귀)
```

---

### Q4. `yield_artifacts: Annotated[list, operator.add]`와 `lotcd: str`의 업데이트 방식 차이는?

<details>
<summary>정답 보기</summary>

- `yield_artifacts` — **누적(append)**. 노드가 `[{새 아티팩트}]`를 반환하면, 기존 리스트에 추가됩니다. `operator.add`는 리스트의 `+` 연산과 같습니다.
- `lotcd` — **덮어쓰기(overwrite)**. reducer가 없으므로 마지막에 반환한 값이 기존 값을 완전히 대체합니다.

| 필드 | reducer | 동작 |
|---|---|---|
| `messages` | `add_messages` | ID 기반 추가/교체 |
| `yield_artifacts` | `operator.add` | 리스트 누적 |
| `lotcd` | 없음 | 마지막 값으로 덮어쓰기 |
</details>

---

### Q5. 사용자가 "4SS 수율 알려줘"라고 하면 노드가 어떤 순서로 실행되나요? (최소 경로)

<details>
<summary>정답 보기</summary>

```
START → rewrite → supervisor → yield_agent → supervisor(FINISH) → END
```

1. **rewrite** — "4SS 수율 알려줘"가 이미 명확하므로 거의 그대로 통과
2. **supervisor** — `next: "yield_agent"`, `lotcd: "4SS"`, `ref_date: 오늘` 결정
3. **yield_agent** — Oracle에서 데이터 조회, HTML 테이블 + 분석 생성
4. **supervisor** (2번째) — 단순 조회이므로 `next: "FINISH"` → END
</details>

---

### Q6. supervisor → agent 라우팅에 `add_conditional_edges`를 안 쓰고 `Command(goto=...)`를 쓴 이유는?

<details>
<summary>정답 보기</summary>

`Command`는 **state 업데이트와 라우팅을 하나의 반환값으로 통합**합니다.

```python
return Command(
    update={"lotcd": "4SS", "messages": [...], ...},  # state 업데이트
    goto="yield_agent",                                # 라우팅
)
```

`add_conditional_edges`를 쓰면:
- 별도의 라우팅 함수를 정의해야 하고
- state 업데이트는 노드 반환값으로, 라우팅은 edge 함수로 **분리**되어 관리가 복잡해집니다

`Command`를 쓰면 하나의 노드 함수 안에서 "어떤 값으로 업데이트하고 어디로 갈지"를 한 번에 결정할 수 있어 코드가 더 직관적입니다.
</details>

---

## Part 3: 요청이 들어오는 순간 (`POST /chat/stream`)

### 배경 지식

```python
# agent_server.py:226-267
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, req: Request):
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    config = {
        "configurable": {
            "thread_id": request.session_id,
            "sse_queue": queue,
            "sse_loop": loop,
        },
        "recursion_limit": 20,
    }

    input_state = {
        "messages": [HumanMessage(content=request.query)],
        "yield_artifacts": Overwrite([]),  # 매 턴마다 리셋
        ...
    }
```

sync 노드 → async SSE 브릿지:
```python
# common.py:123-137
def emit_sse(config, kind, event):
    queue = config["configurable"]["sse_queue"]
    loop = config["configurable"]["sse_loop"]
    loop.call_soon_threadsafe(queue.put_nowait, (kind, event))
```

---

### Q7. `emit_sse`에서 `loop.call_soon_threadsafe`를 쓰는 이유는? 그냥 `queue.put_nowait`만 하면 안 되나요?

<details>
<summary>정답 보기</summary>

`asyncio.Queue`는 **스레드 안전하지 않습니다**. LangGraph 노드들은 sync 함수이므로 별도의 **워커 스레드**에서 실행됩니다. 반면 `asyncio.Queue`는 **메인 asyncio 이벤트 루프 스레드**에서만 안전하게 조작할 수 있습니다.

`loop.call_soon_threadsafe(queue.put_nowait, ...)`는:
1. 메인 이벤트 루프에 "이 함수를 실행해달라"고 **예약**합니다
2. 이벤트 루프가 자기 스레드에서 안전하게 `queue.put_nowait`를 실행합니다

그냥 `queue.put_nowait(...)`를 하면 **다른 스레드에서 asyncio 객체를 직접 조작**하는 것이라 race condition이 발생할 수 있습니다.
</details>

---

### Q8. `Overwrite([])`를 안 쓰고 그냥 `[]`로 보내면 어떤 일이 벌어질까요?

<details>
<summary>정답 보기</summary>

`yield_artifacts`의 reducer는 `operator.add`(리스트 합치기)입니다.

- `Overwrite([])` → reducer를 무시하고 **강제로 빈 리스트**로 덮어씁니다
- `[]` → reducer가 적용되어 `기존_리스트 + [] = 기존_리스트` → **이전 턴의 아티팩트가 그대로 남습니다**

결과: 두 번째 질문을 하면 첫 번째 질문의 아티팩트 + 두 번째 질문의 아티팩트가 합쳐져서 프론트에 중복 전송됩니다.
</details>

---

### Q9. `thread_id`에 `session_id`를 넣는 이유는? 이게 다른 사용자의 대화와 어떻게 격리되나요?

<details>
<summary>정답 보기</summary>

LangGraph 체크포인터는 `thread_id`를 키로 사용하여 그래프 state를 저장/복원합니다.

- 사용자 A의 `session_id`가 `"aaa-111"`이면, 체크포인터에 `thread_id="aaa-111"`로 저장
- 사용자 B의 `session_id`가 `"bbb-222"`이면, 체크포인터에 `thread_id="bbb-222"`로 저장

각 `thread_id`마다 **독립된 state 스냅샷**이 MongoDB에 별도 저장됩니다. `graph.astream(input, config={"configurable": {"thread_id": "aaa-111"}})`을 호출하면 "aaa-111"의 이전 state만 복원되므로, 다른 사용자의 대화와 완전히 격리됩니다.
</details>

---

## Part 4: 그래프 실행 & SSE 스트리밍

### 배경 지식

```python
# agent_server.py:269-289
async def _run_graph():
    async for step in graph.astream(input_state, config=config):
        await queue.put(("step", step))
    await queue.put(("done", None))

task = asyncio.create_task(_run_graph())
```

`graph.astream()` yield 예시:
```python
{"rewrite": {"messages": [HumanMessage(...)]}}
{"supervisor": {"step_count": 1, "lotcd": "4SS", "messages": [AIMessage(...)]}}
{"yield_agent": {"yield_artifacts": [...], "messages": [AIMessage(...)]}}
{"supervisor": {"step_count": 2, ...}}
```

토큰 스트리밍 시뮬레이션:
```python
# agent_server.py:350-355
chunks = _chunk_text(content)  # 완성된 텍스트를 청크로 분할
for chunk in chunks:
    yield _sse(TokenEvent(content=chunk))
    await asyncio.sleep(0.02)  # 20ms 간격
```

file:// 아티팩트 처리:
```python
# agent_server.py:380-388
if art_data.startswith("file://"):
    with open(file_path, "r") as f:
        art_data = f.read()
    os.remove(file_path)  # 전송 후 삭제
```

---

### Q10. `graph.astream()`이 yield하는 데이터의 구조는?

<details>
<summary>정답 보기</summary>

각 노드가 완료될 때마다 **`{노드이름: 해당 노드의 state 업데이트 딕셔너리}`** 형태를 yield합니다.

예시 (단순 수율 조회):
```python
# 1) rewrite 노드 완료
{"rewrite": {"messages": [HumanMessage(content="4SS 수율 알려줘", id="...")]}}

# 2) supervisor 노드 완료 (yield_agent로 라우팅 결정)
{"supervisor": {"step_count": 1, "lotcd": "4SS", "ref_date": "20260315", "messages": [AIMessage(...)]}}

# 3) yield_agent 노드 완료
{"yield_agent": {"yield_artifacts": [{"title": "yield", "data": "file://..."}], "messages": [AIMessage("4SS 주간 수율...")], "analysis_result": "## 분석..."}}

# 4) supervisor 노드 완료 (FINISH)
{"supervisor": {"step_count": 2, "messages": [AIMessage("조회를 완료했습니다.")]}}
```

**주의:** 이것은 전체 state가 아니라, 해당 노드가 **변경한 부분만** 포함합니다.
</details>

---

### Q11. 토큰 스트리밍이 "시뮬레이션"인 이유는? 진짜 스트리밍이 되는 부분은 어디?

<details>
<summary>정답 보기</summary>

**시뮬레이션인 이유:**
LangGraph 노드는 실행이 완전히 끝난 후에 결과를 반환합니다. `yield_agent`가 "4SS 주간 수율 데이터입니다..."라는 전체 메시지를 한 번에 반환하면, `agent_server`가 이 텍스트를 `_chunk_text()`로 3글자 단위로 잘라서 20ms 간격으로 보냅니다. **LLM이 생성하는 것처럼 보이지만 실제로는 이미 완성된 텍스트를 잘라서 보내는 것**입니다.

**진짜 스트리밍이 되는 부분:**
`supervisor_node`의 `<think>...</think>` 구간입니다. supervisor는 `_model.stream()`으로 LLM을 호출하면서, 토큰이 생성될 때마다 `emit_sse(config, "thinking", ...)`로 실시간 전송합니다. 이 부분만 진짜 LLM 토큰 스트리밍입니다.
</details>

---

### Q12. 아티팩트를 `file://` 경로로 state에 넣는 이유는? 직접 HTML 문자열을 넣으면 안 되나?

<details>
<summary>정답 보기</summary>

**넣을 수는 있지만 성능 문제가 발생합니다.**

1. LangGraph 체크포인터는 **매 노드 실행 후 전체 state를 MongoDB에 저장**합니다
2. HTML 테이블이 수십~수백KB인데, 이걸 state에 직접 넣으면 **매번 체크포인트 저장 시 거대한 데이터가 MongoDB에 쓰여집니다**
3. supervisor가 에이전트 결과를 볼 때도 state에 있는 거대한 HTML을 로드하게 되어 불필요한 메모리 사용

해결: `yield_query_agent`가 HTML을 `generated/` 폴더에 파일로 저장하고, state에는 `"file://경로"`라는 가벼운 문자열만 넣습니다. `agent_server`의 SSE 생성 단계에서 파일을 읽어 프론트에 보내고, 파일을 삭제합니다.
</details>

---

## Part 5: Rewrite Node — 질의 보정

### 배경 지식

```python
# supervisor.py:131-179
def rewrite_node(state, config):
    messages = state["messages"]

    # 오래된 메시지 pruning (30개 초과 시)
    if len(messages) > 30:
        prune_ops = [RemoveMessage(id=m.id) for m in excess]

    last_human = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
    recent = _get_recent_turns(messages, max_turns=5, exclude_last=last_human)

    response = _model.invoke([system_prompt, *recent, user_message])

    return {"messages": prune_ops + [HumanMessage(content=rewritten, id=last_human.id)]}
```

---

### Q13. rewrite_node에서 `id=last_human.id`를 빼면 어떻게 될까요?

<details>
<summary>정답 보기</summary>

`add_messages` reducer는 메시지의 `id`가 같으면 **교체(replace)**, 다르면 **추가(append)**합니다.

- `id=last_human.id` 있을 때: 원래 "응"이라는 메시지가 "4SS WADS 열화 리포트 보여줘"로 **변경**됩니다. 메시지 수가 늘어나지 않습니다.
- `id`를 빼면: 새로운 ID로 HumanMessage가 생성되어, 원래 "응" 메시지 **뒤에** rewrite된 메시지가 **추가**됩니다. 결과적으로 supervisor가 보는 messages에 "응"과 "4SS WADS 열화 리포트 보여줘"가 모두 있어서, LLM이 혼동할 수 있습니다.
</details>

---

## Part 6: Supervisor Node — 두뇌

### 배경 지식

```python
# supervisor.py:328-545
def supervisor_node(state, config):
    step_count = state.get("step_count", 0) + 1

    if step_count > 4:  # 최대 스텝 강제 종료
        return Command(update=..., goto=END)

    # LLM stream() 호출 → <think> 실시간 전송 + JSON 파싱
    for chunk in _model.stream(...):
        # <think> 태그 내용 → emit_sse(thinking)
        ...

    # JSON 추출 & Pydantic 검증
    decision = RouteResponse(**json.loads(json_match.group(1)))

    # 동일 에이전트 재호출 방지
    if last_agent == decision.next:
        decision.next = "FINISH"

    return Command(update={...}, goto=decision.next or END)
```

---

### Q14. supervisor가 "4SS 수율 알려줘"를 받으면 반환하는 JSON의 `next` 필드는?

<details>
<summary>정답 보기</summary>

**`"yield_agent"`**

"수율"이라는 키워드가 있으므로 시스템 프롬프트의 라우팅 규칙에 따라 yield_agent로 라우팅합니다.

전체 JSON 예시:
```json
{
  "next": "yield_agent",
  "lotcd": "4SS",
  "ref_date": "20260315",
  "unit": "weekly",
  "periods": 0,
  "filter_params": [],
  "message": "4SS 제품의 오늘 기준 주간 수율을 조회하겠습니다."
}
```
</details>

---

### Q15. supervisor가 에이전트 결과를 200자로 잘라서 보는 이유는?

<details>
<summary>정답 보기</summary>

```python
# supervisor.py:374-384
if isinstance(m, AIMessage) and agent_name in _AGENT_NAMES and len(m.content) > 200:
    summary = m.content[:200] + "...(결과 생략)"
```

두 가지 이유:

1. **LLM 토큰 절약** — 에이전트 결과가 수천 자인데 전부 시스템 프롬프트에 넣으면 토큰을 낭비하고 비용이 증가합니다
2. **이어쓰기 방지** — LLM이 에이전트의 분석 텍스트를 보고 그걸 이어서 쓰는(hallucination) 경향이 있습니다. 결과를 `[AGENT_RESULT:yield_agent] ...생략`으로 축약하면, supervisor가 "결과가 나왔구나" 정도만 인지하고 본연의 라우팅 역할에 집중합니다
</details>

---

### Q16. step_count가 5가 되면 어떤 일이 벌어지나요?

<details>
<summary>정답 보기</summary>

```python
# supervisor.py:340-348
if step_count > 4:
    return Command(
        update={
            "step_count": step_count,
            "messages": [AIMessage(content="분석을 완료했습니다.", name="supervisor")],
        },
        goto=END,
    )
```

**무한루프 방지를 위한 강제 종료**입니다. step_count가 5가 되면:
1. "분석을 완료했습니다."라는 메시지를 state에 추가
2. `goto=END`로 그래프를 종료
3. LLM 호출 없이 즉시 반환 (LLM 판단을 하기 전에 early return)

이렇게 하면 supervisor ↔ agent 루프가 무한히 돌아가는 것을 방지합니다.
</details>

---

## Part 7: Agent Nodes

### 배경 지식

yield_agent_node 흐름:
```
1. state에서 파라미터 읽기 (lotcd, ref_date, unit, periods)
2. Oracle DB에서 pt1h 파라미터 데이터 조회
3. 주간/월간/일간 수율 테이블 생성 (HTML)
4. LLM으로 분석 요약 생성
5. 이상감지 (anomaly detection)
6. state 반환: messages, yield_artifacts, analysis_result, anomaly_params, agent_suggestion
```

에이전트 → supervisor 복귀:
```python
workflow.add_edge("yield_agent", "supervisor")
workflow.add_edge("wads_agent",  "supervisor")
workflow.add_edge("map_agent",   "supervisor")
```

---

### Q17. yield_agent가 이상(anomaly)을 감지하면, 그 정보가 supervisor에게 어떻게 전달되나요?

<details>
<summary>정답 보기</summary>

yield_agent가 state에 `anomaly_params`를 반환합니다:
```python
{"anomaly_params": [{"param": "IOFF", "direction": "up", "magnitude": 15.2}]}
```

supervisor_node가 다시 실행될 때 `state.get("anomaly_params", [])`로 읽고, 시스템 프롬프트에 주입합니다:
```python
# supervisor.py:363-368
if anomaly_params:
    param_names = ", ".join(a["param"] for a in anomaly_params)
    prompt += f"\n\n[이전 분석 결과] 이상 감지된 파라미터: {param_names}"
```

이 정보를 바탕으로 supervisor가 `wads_agent`나 `map_agent`를 추가 호출할지 결정합니다.
</details>

---

### Q18. 멀티스텝 시나리오에서 "4SS IOFF 수율 이상한데 원인 분석해줘"의 실행 순서는?

<details>
<summary>정답 보기</summary>

```
Step 1: START → rewrite → supervisor(next="yield_agent") → yield_agent
        → IOFF 이상 감지, anomaly_params에 기록

Step 2: yield_agent → supervisor(anomaly 확인, next="wads_agent") → wads_agent
        → 열화 검출 리포트 조회

Step 3: wads_agent → supervisor(next="map_agent") → map_agent
        → cummap으로 웨이퍼 맵 시각화

Step 4: map_agent → supervisor(next="FINISH") → END
```

각 에이전트 완료 후 supervisor로 복귀하여 다음 행동을 결정하는 **ReAct 스타일 루프**입니다.
</details>

---

## Part 8: 대화 이력 저장 & REST API

### 배경 지식

```python
# agent_server.py:441-454 — SSE 스트리밍 끝난 후
turn_doc = {
    "session_id": request.session_id,
    "query": request.query,
    "messages": turn_messages,
    "artifacts": turn_artifacts,  # data는 빈 문자열로 저장
    "suggestion": turn_suggestion,
    "timestamp": datetime.now(UTC),
}
await db.chat_turns.insert_one(turn_doc)
```

REST API:

| 메서드 | 경로 | 용도 |
|---|---|---|
| `GET` | `/health` | 헬스체크 |
| `POST` | `/session` | 새 session_id 발급 (uuid4) |
| `GET` | `/sessions` | 세션 목록 (최근 50개) |
| `GET` | `/session/{id}/history` | 특정 세션 대화 이력 |
| `DELETE` | `/session/{id}` | 세션 삭제 |
| `POST` | `/chat/stream` | SSE 스트리밍 채팅 |

---

### Q19. 프론트에서 새 대화를 시작할 때 호출해야 하는 API 순서는?

<details>
<summary>정답 보기</summary>

```
1. POST /session → { "session_id": "새로운-uuid" } 받기
   (또는 클라이언트에서 직접 uuid 생성 — 둘 다 가능)

2. POST /chat/stream → { "query": "...", "session_id": "새로운-uuid" }
   → SSE 이벤트 수신 시작
```

최소 2단계입니다. 사실 `POST /session`은 서버에서 uuid만 생성해주므로, 프론트에서 직접 `uuid.v4()`를 쓰면 1단계만으로도 가능합니다.
</details>

---

### Q20. 세션 전환 시 (사이드바에서 이전 대화 클릭) 호출해야 하는 API는?

<details>
<summary>정답 보기</summary>

```
GET /session/{이전_session_id}/history
```

이 API가 해당 session_id의 모든 대화 턴(user + assistant 메시지)을 시간순으로 반환합니다. 프론트는 이 데이터로 채팅 UI를 복원합니다.

이후 대화를 이어가려면 같은 session_id로 `POST /chat/stream`을 보내면 됩니다. LangGraph 체크포인터가 해당 thread_id의 state를 자동 복원하므로 대화 컨텍스트가 유지됩니다.
</details>

---

### Q21. 아티팩트 data를 MongoDB에 빈 문자열로 저장하면, 이전 대화를 불러올 때 아티팩트를 어떻게 보여줄까요?

<details>
<summary>정답 보기</summary>

**현재로서는 보여줄 수 없습니다. 이것은 알려진 한계점입니다.**

- 실시간 스트리밍 중에는 SSE로 전체 HTML 데이터가 전송되어 정상 표시됩니다
- 하지만 `chat_turns`에는 `data: ""`로 저장되므로, `GET /session/{id}/history`로 이전 대화를 불러오면 아티팩트의 실제 데이터가 없습니다
- 아티팩트의 메타정보(title, type, agent)만 남아있습니다

해결하려면: 아티팩트를 별도 컬렉션/스토리지에 저장하고 ID로 참조하거나, 필요한 만큼만 저장하는 전략이 필요합니다.
</details>

---

## Part 9: 전체 흐름 종합

### 시퀀스 다이어그램

```
React                    FastAPI                  LangGraph Graph
  │                         │                          │
  │── POST /chat/stream ──→ │                          │
  │   {query, session_id}   │                          │
  │                         │── asyncio.Queue 생성     │
  │                         │── create_task(_run_graph) │
  │                         │                          │── rewrite_node()
  │                         │    ← queue.put(step) ────│
  │  ← SSE: stream_start   │                          │
  │  ← SSE: node_complete   │                          │── supervisor_node()
  │  ← SSE: thinking       │    ← emit_sse(thinking) ─│
  │  ← SSE: node_complete   │    ← queue.put(step) ────│
  │  ← SSE: token (×N)     │                          │── yield_agent_node()
  │  ← SSE: message         │                          │
  │  ← SSE: artifact        │    ← queue.put(step) ────│
  │  ← SSE: suggestion      │                          │
  │  ← SSE: node_complete   │                          │── supervisor_node()
  │  ← SSE: message         │    ← queue.put(step) ────│   (FINISH)
  │  ← SSE: stream_end     │    ← queue.put(done) ────│
  │                         │── MongoDB insert_one     │
```

---

### Q22. 사용자가 "오늘 4SS 수율 알려줘"라고 입력했을 때, 프론트가 받는 SSE 이벤트를 순서대로 나열하세요. (최소 6개)

<details>
<summary>정답 보기</summary>

```
1. stream_start    → { type: "stream_start", session_id: "...", query: "오늘 4SS 수율 알려줘" }
2. node_complete   → { type: "node_complete", node: "rewrite", step: 0, elapsed: 0.5 }
3. thinking        → { type: "thinking", content: "수율 요청이므로 yield_agent로..." }
4. node_complete   → { type: "node_complete", node: "supervisor", step: 1, elapsed: 1.2 }
5. token (×N)     → { type: "token", content: "4SS", agent: "supervisor" }
6. message         → { type: "message", agent: "supervisor", content: "4SS 수율을 조회합니다." }
7. node_complete   → { type: "node_complete", node: "yield_agent", step: 1, elapsed: 4.0 }
8. token (×N)     → { type: "token", content: "조회 결과...", agent: "yield_agent" }
9. message         → { type: "message", agent: "yield_agent", content: "4SS 주간 수율 데이터..." }
10. artifact       → { type: "artifact", artifact_type: "html", data: "<table>...", title: "yield" }
11. artifact       → { type: "artifact", artifact_type: "markdown", data: "## 분석...", title: "analysis" }
12. suggestion     → { type: "suggestion", content: "WADS 열화 리포트도 확인해보세요" }
13. node_complete  → { type: "node_complete", node: "supervisor", step: 2, elapsed: 5.0 }
14. token/message  → supervisor의 FINISH 메시지
15. stream_end     → { type: "stream_end", total_steps: 2, elapsed: 5.5 }
```
</details>

---

### Q23. 같은 session_id로 두 번째 메시지를 보내면, 첫 번째 대화의 컨텍스트가 유지되는 이유는?

<details>
<summary>정답 보기</summary>

**2가지 메커니즘:**

1. **LangGraph 체크포인터 (MongoDBSaver)**
   - 각 노드 실행 후 `thread_id`(=session_id) 기준으로 전체 state를 MongoDB에 자동 저장합니다
   - 두 번째 요청 시 `graph.astream(input, config={"configurable": {"thread_id": 같은_세션}})` 호출하면, 체크포인터가 이전 state를 자동 복원합니다
   - `messages`에 이전 대화가 모두 들어있고, `lotcd` 등 파라미터도 유지됩니다

2. **`add_messages` reducer**
   - 새 `HumanMessage`가 `input_state["messages"]`로 들어오면, reducer가 기존 메시지 리스트에 **추가**합니다
   - 결과적으로 `messages = [이전 Human, 이전 AI, ..., 새 Human]`이 되어 전체 대화 히스토리가 유지됩니다

이 두 가지가 합쳐져서, 두 번째 요청의 rewrite_node와 supervisor_node가 이전 대화 맥락을 볼 수 있게 됩니다.
</details>

---

### Q24. 서버를 재시작해도 대화가 유지되나요? 유지된다면 어떤 구성요소 덕분?

<details>
<summary>정답 보기</summary>

**네, 유지됩니다.** 두 가지 영속성 레이어 덕분입니다:

1. **MongoDBSaver (LangGraph 체크포인터)**
   - 그래프 state가 MongoDB에 저장되어 있으므로, 서버 재시작 후에도 같은 session_id로 요청하면 이전 state가 복원됩니다
   - `messages`, `lotcd`, `ref_date` 등 모든 state 필드가 보존됩니다

2. **motor + chat_turns 컬렉션**
   - 대화 이력이 MongoDB `chat_turns` 컬렉션에 별도 저장되어 있습니다
   - `GET /session/{id}/history`로 이전 대화를 불러올 수 있습니다
   - `GET /sessions`로 세션 목록도 조회 가능합니다

만약 MongoDB가 아닌 인메모리 체크포인터(MemorySaver)를 썼다면, 서버 재시작 시 모든 대화가 사라졌을 것입니다.
</details>

---

## 보너스: 핵심 개념 요약표

| 개념 | 위치 | 설명 |
|---|---|---|
| `lifespan` | agent_server.py:71 | 서버 시작/종료 시 리소스 초기화/정리 |
| `YieldQueryState` | supervisor.py:549 | 모든 노드가 공유하는 state 스키마 |
| `add_messages` reducer | supervisor.py:556 | 메시지 ID 기반 추가/교체 |
| `operator.add` reducer | supervisor.py:571 | 아티팩트 리스트 누적 |
| `Overwrite([])` | agent_server.py:237 | reducer 무시하고 강제 초기화 |
| `Command(update, goto)` | supervisor.py:543 | state 업데이트 + 라우팅 통합 |
| `asyncio.Queue` | agent_server.py:230 | sync 노드 ↔ async SSE 브릿지 |
| `emit_sse` | common.py:123 | sync → async 큐 전송 헬퍼 |
| `call_soon_threadsafe` | common.py:135 | 크로스 스레드 안전 호출 |
| `file://` 아티팩트 | agent_server.py:380 | 큰 HTML을 state에서 분리 |
| `_chunk_text` | agent_server.py:129 | 토큰 스트리밍 시뮬레이션 |
| `RetryPolicy` | supervisor.py:609 | 일시적 오류 자동 재시도 (3회) |
| `MongoDBSaver` | agent_server.py:78 | LangGraph state 영속성 |
| `chat_turns` | agent_server.py:452 | 대화 이력 영속성 |
| `RemoveMessage` | supervisor.py:140 | 체크포인트 크기 제한을 위한 메시지 pruning |
