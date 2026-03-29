# wads_agent → map_agent 멀티스텝 실패 수정

## Context
"3월 20일에 검출된 lot들 map 보여줘" → supervisor가 wads_agent 결과에서 lot ID를 추출해 map_agent로 라우팅해야 하지만, 200자 hard truncation 때문에 lot ID를 볼 수 없어 실패.

## 근본 원인
`supervisor.py:251-263` — 모든 agent 메시지를 무조건 200자로 자름.
방금 반환된 wads_agent 결과도 잘려서 supervisor LLM이 lot ID를 읽을 수 없음.

## 수정: 턴 기반 요약 (하드 truncation 제거)

**파일**: `map_agent/08-YieldAgent/supervisor.py:249-263`

현재 (하드 truncation):
```python
_MAX_AGENT_MSG_LEN = 200
for m in messages:
    if isinstance(m, AIMessage) and agent_name in _AGENT_NAMES and len(m.content) > _MAX_AGENT_MSG_LEN:
        summary = m.content[:200]...
```

수정 (턴 기반):
- **최근 N턴의 agent 메시지**: full content 유지 (supervisor가 결과를 보고 판단할 수 있도록)
- **그 이전 agent 메시지**: 요약 (LLM summarization 또는 앞부분 truncation)

```python
# 최근 agent 메시지는 full 유지, 오래된 것만 요약
_RECENT_FULL_TURNS = 2  # 최근 2턴은 full
_MAX_OLD_MSG_LEN = 300  # 오래된 메시지만 truncation

agent_msg_count = sum(1 for m in reversed(messages)
                      if isinstance(m, AIMessage) and getattr(m, "name", "") in _AGENT_NAMES)

condensed = []
recent_agent_seen = 0
for m in reversed(messages):
    agent_name = getattr(m, "name", "")
    if isinstance(m, AIMessage) and agent_name in _AGENT_NAMES:
        recent_agent_seen += 1
        if recent_agent_seen <= _RECENT_FULL_TURNS:
            condensed.append(m)  # full content
        elif len(m.content) > _MAX_OLD_MSG_LEN:
            summary = m.content[:_MAX_OLD_MSG_LEN].rsplit("\n", 1)[0]
            condensed.append(AIMessage(
                content=f"[AGENT_RESULT:{agent_name}] {summary}...(결과 생략)",
                name=agent_name,
            ))
        else:
            condensed.append(m)
    else:
        condensed.append(m)
condensed.reverse()
```

이렇게 하면:
- wads_agent가 방금 반환 → full 결과 유지 → supervisor가 lot ID 읽을 수 있음
- 3턴 이상 전 agent 결과 → 요약 (토큰 절약, "이어쓰기" 방지)

## 수정 파일
- `map_agent/08-YieldAgent/supervisor.py:249-263` — 턴 기반 요약 로직

## 검증
- "3월 20일에 검출된 lot들 map 보여줘" → wads_agent → supervisor(full 결과에서 lot ID 추출) → map_agent(map_lot_ids) → FINISH
- 오래된 agent 결과는 여전히 요약되어 토큰/이어쓰기 문제 없음
