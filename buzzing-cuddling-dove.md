# Plan: Supervisor가 이전 WADS 결과에서 lot ID를 추출하여 map_agent에 전달하도록 수정

## Context
WADS agent가 step08 검출 결과(lot ID 포함)를 반환한 후, 사용자가 "3월 22일에 검출된 lot들 map 보여줘"라고 요청하면 supervisor가 map_lot_ids를 비워둔 채 map_agent로 라우팅 → INTERRUPT 발생하여 사용자에게 lot ID를 다시 물어보는 버그.

**근본 원인**: `SUPERVISOR_SYSTEM_PROMPT`에 이전 agent 결과에서 lot ID를 추출하라는 지시가 없음. 대화 이력에 lot ID가 포함된 wads_agent 메시지가 FULL로 유지되고 있지만, LLM이 이를 활용하라는 instruction이 없어서 map_lot_ids를 빈 문자열로 출력.

## 수정 사항

### 파일: `/Users/daehwankim/yield-agent/map_agent/08-YieldAgent/prompts.py`

**변경 위치**: line 193 (`- 단순 조회는 1스텝으로 끝낼 것`) 뒤, line 194 (`=== CRITICAL: OUTPUT FORMAT`) 앞에 새 섹션 삽입.

**추가할 프롬프트 섹션**:
```
=== CROSS-AGENT CONTEXT: LOT ID EXTRACTION ===
이전 에이전트 결과에서 lot ID를 참조하는 경우:
- 사용자가 "검출된 lot", "그 lot들", "위 lot", "해당 lot들" 등으로 이전 결과의 lot을 지칭하면,
  대화 이력의 가장 최근 wads_agent/yield_agent 메시지에서 lot ID(7자 이상 영숫자, 예: 4SSYKXR)를 추출
- map 요청 시 → map_lot_ids에 쉼표 구분으로 설정
- yield 요청 시 → yield_lot_ids에 설정
- lotcd는 추출된 lot ID 앞 3자에서 자동 추론

예시:
- wads_agent 결과: "4SSYKXR – step08, 4SS58YI – step08"
  사용자: "검출된 lot들 map 보여줘"
  → map_lot_ids="4SSYKXR,4SS58YI", lotcd="4SS", next="map_agent"
- 날짜 필터가 있으면 해당 날짜 결과의 lot만 추출
```

## 수정하지 않는 파일
- `supervisor.py` - 이미 최근 2턴 agent 메시지를 FULL로 유지하고 있어 변경 불필요
- `wads_agent.py` - AIMessage에 lot ID가 이미 포함되어 있어 변경 불필요

## 검증 방법
1. 서버 재시작 후 동일 시나리오 재현:
   - "4SS step08 검출 리포트 보여줘" → wads_agent가 lot ID 포함 결과 반환
   - "검출된 lot들 map 보여줘" → supervisor 로그에서 map_lot_ids가 비어있지 않은지 확인
2. 로그에서 `map_lot_ids=` 값이 실제 lot ID로 채워지는지 확인
