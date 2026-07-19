# LOT History 공정별 LLM Insight 설계

## 목표

`lot_history_agent`가 LOT 이력을 테이블별로 단순 나열하는 데서 끝나지 않고, 5개 이력 테이블의 행을 정확히 같은 공정끼리 모아 LLM으로 분석한다. 단일 LOT에는 공정별 insight를 제공하고, 여러 LOT에는 동일 공정에서 반복되는 이상과 차이를 추가로 종합한다.

기존 Oracle 조회와 상세 HTML은 유지한다. LLM은 조회, 공정명 보정 또는 공정 분류를 하지 않고, 이미 묶인 이력의 해석만 담당한다.

## 범위

분석 대상은 다음 5개 데이터 소스다.

- `fdc_alarm`
- `qtime_over`
- `trouble_lot`
- `future_action`
- `sample_split`

이번 변경에서 하지 않는 일:

- 비슷한 공정명, 약어 또는 설명을 합치지 않는다.
- 키워드, 정규식, 표현 목록으로 공정명을 보정하지 않는다.
- LLM에게 공정명을 추론하거나 재분류하게 하지 않는다.
- 기존 Oracle SQL과 상세 테이블의 의미를 변경하지 않는다.

## 공정 식별 계약

공정 키는 Oracle `CHAR` 패딩 제거를 위해 문자열 앞뒤 공백만 제거한다. 대소문자와 내부 공백은 그대로 보존한다. 결과 문자열이 정확히 같은 행만 같은 공정으로 묶는다.

| 데이터 소스 | 공정 키 | 이벤트 역할 |
|---|---|---|
| `fdc_alarm` | `oper_id` | `fdc_alarm` |
| `qtime_over` | `from_oper` | `qtime_outgoing` |
| `qtime_over` | `to_oper` | `qtime_incoming` |
| `trouble_lot` | `step_desc` | `trouble_lot` |
| `future_action` | `action_step` | `future_action` |
| `sample_split` | `step` | `sample_split` |

`sample_split.oper_desc`는 공정 설명으로만 보존한다.

Q-TIME 행은 FROM 공정과 TO 공정 그룹에 각각 한 번씩 포함한다. 두 이벤트는 동일한 `event_id`를 공유하고 역할을 `qtime_outgoing`과 `qtime_incoming`으로 구분한다. 이를 통해 LLM은 출발 공정 이후 지연과 도착 공정 진입 전 지연을 모두 볼 수 있으며, 최종 종합 단계에서는 동일 사건을 두 건으로 세지 않는다.

공정 키가 비어 있는 행은 `unmatched`에 보존한다. 이 행은 기존 상세 HTML에는 계속 표시하지만 공정별 LLM 분석에는 넣지 않는다.

## 구성 요소

### 공통 이벤트 변환

`lot_history_data`의 테이블별 행을 다음 공통 형태로 변환한다.

```json
{
  "event_id": "stable-within-request",
  "lot_id": "LOT-A",
  "process": "PHOTO",
  "source": "qtime_over",
  "role": "qtime_outgoing",
  "event_time": "2026-07-18T10:20:00",
  "details": {
    "from_oper": "PHOTO",
    "to_oper": "ETCH",
    "control_limit": 60,
    "q_time": 85,
    "bal": 25
  }
}
```

`details`에는 원본 행의 분석에 필요한 필드를 보존하되, LLM 입력에는 HTML이나 Python 객체를 넣지 않고 JSON 직렬화 가능한 값만 사용한다.

### 공정 그룹 생성

공통 이벤트를 `process` 값으로 그룹화하고 그룹 내부를 `event_time` 오름차순으로 정렬한다. 시간값이 없는 이벤트는 뒤에 두되 삭제하지 않는다.

LLM 호출은 이벤트가 1건인 그룹도 포함한다. 한 건뿐이라는 사실 자체가 분석 결과에 명시되며, LLM은 반복 또는 인과관계를 주장할 수 없다. 이 기준은 분석 누락보다 근거 제한을 명시하는 편을 우선한다.

### 공정별 LLM 분석

각 공정 그룹을 독립 입력으로 분석한다. 입력에는 공정명, 대상 LOT 목록, 시간순 이벤트와 각 이벤트의 source/role/details를 제공한다.

LLM 출력 계약:

```json
{
  "process": "PHOTO",
  "risk_level": "high",
  "summary": "...",
  "evidence": ["..."],
  "patterns": ["..."],
  "hypotheses": [
    {
      "text": "...",
      "confidence": "medium",
      "basis": "..."
    }
  ],
  "recommended_checks": ["..."],
  "event_ids": ["..."]
}
```

프롬프트는 다음 원칙을 강제한다.

- 입력 이벤트에 있는 사실만 근거로 사용한다.
- 시간적 선후 관계와 인과관계를 구분한다.
- 원인이 확정되지 않았으면 가설로 표시한다.
- 반복을 주장할 때 해당 event ID 또는 LOT을 근거로 남긴다.
- Q-TIME의 같은 `event_id`가 양쪽 역할로 나타나도 한 사건으로 센다.
- 데이터가 부족하면 부족하다고 명시한다.

모델은 기존 `common.get_llm()` 팩토리를 사용한다. 모델명은 기존 환경 설정을 따르며 이 기능만을 위한 하드코딩 모델명은 추가하지 않는다. 프롬프트는 `prompts.py`에 중앙화한다.

### 여러 LOT 종합

LOT이 두 개 이상이면 공정별 구조화 insight를 두 번째 LLM 호출에 전달한다. 원본 전체 행을 다시 보내지 않고, 공정별 결과와 근거 event ID를 전달하여 입력 크기를 제한한다.

종합 결과는 다음을 포함한다.

- 여러 LOT에서 반복된 동일 공정 이상
- 동일 장비, FDC 항목, Trouble 또는 Q-TIME 패턴
- 특정 LOT에만 나타난 차이
- 가장 먼저 확인할 공정과 근거
- 공통 원인으로 확정할 수 없는 부분

단일 LOT이면 이 종합 호출은 생략한다.

## 실행 흐름

1. `query_lot_history`로 기존 5개 테이블을 조회한다.
2. 조회 결과를 공통 이벤트로 변환한다.
3. 정확히 같은 공정 키로 이벤트를 그룹화한다.
4. 공정 그룹별 LLM 분석을 실행한다.
5. 여러 LOT이면 공정별 insight를 LLM으로 종합한다.
6. 기존 상세 HTML에 전체 요약과 공정별 insight 섹션을 추가한다.
7. 기존 `ResultEnvelope.rows`의 LOT별 건수 행은 그대로 유지한다. 공정 insight 전문은 `lot_history_sql_result.additional_kwargs["lot_history_result"]["process_insights"]`에 넣고, `ResultEnvelope.metadata`에는 분석 성공/실패 공정 수와 전체 종합 생성 여부만 넣는다.

기존 상세 데이터는 insight 생성 실패와 무관하게 항상 렌더링한다.

## 오류 및 입력 크기 처리

- Oracle 조회 실패: 기존 오류 경로를 유지하고 LLM을 호출하지 않는다.
- 특정 공정 LLM 실패: 해당 공정에 분석 실패 상태를 기록하고 나머지 공정 분석은 유지한다.
- 전체 종합 LLM 실패: 공정별 insight는 그대로 표시하고 전체 종합만 생략한다.
- 구조화 출력 검증 실패: 임의 문장을 정상 insight로 사용하지 않고 해당 분석을 실패로 처리한다.
- 이벤트가 많은 공정: 초기 구현은 공정당 한 번 호출하며 행을 조용히 자르지 않는다. 모델 입력 한도 때문에 호출이 실패하면 해당 공정을 분석 실패로 표시하고 원본 상세 이력은 유지한다. 청크 분석은 실제 운영 데이터에서 입력 한도 실패가 확인될 때 별도 변경으로 다룬다.
- 공정 키 없음: `unmatched`로 보존하고 상세 HTML에 표시한다.

## UI 결과

기존 LOT별 상세 섹션 위에 다음을 추가한다.

1. 여러 LOT일 때 전체 공정 종합 insight
2. 공정별 risk, 요약, 근거, 가설, 확인 권고
3. 기존 FDC/Q-TIME/Trouble/Action/Sample 상세 테이블

Insight와 원본 상세 이력을 함께 제공해 사용자가 LLM 설명의 근거를 확인할 수 있게 한다.

## 검증 기준

### 단위 검증

- 각 테이블 행이 올바른 공정 키와 역할로 변환된다.
- 앞뒤 공백만 제거되고 대소문자와 내부 공백은 유지된다.
- Q-TIME 한 행이 같은 `event_id`로 FROM/TO 그룹에 각각 포함된다.
- 빈 공정 키가 `unmatched`로 이동한다.
- 시간순 정렬과 시간 누락 보존이 동작한다.
- 단일 LOT에서는 최종 종합 호출이 발생하지 않는다.
- 여러 LOT에서는 공정별 결과를 이용한 종합 호출이 발생한다.
- 한 공정의 LLM 실패가 다른 공정과 기존 HTML을 제거하지 않는다.

### 실제 end-to-end 검증

완료 선언 전 다음 사용자 시나리오를 실제 환경에서 재현한다.

1. 실제 Oracle에서 단일 LOT의 5개 이력을 조회한다.
2. 공정 키가 정확히 같은 원본 행들이 한 그룹에 들어갔는지 조회 결과와 대조한다.
3. 실제 LLM을 호출해 공정별 구조화 insight를 받는다.
4. insight의 근거 event ID가 실제 입력 행에 존재하는지 확인한다.
5. 여러 LOT을 실제 조회하고 동일 공정에 대한 LOT 간 종합 insight를 생성한다.
6. 실제 서버/API 도구 실행으로 기존 상세 HTML과 새 insight가 함께 전달되는지 확인한다.

실제 Oracle 또는 LLM 접근이 불가능하면 단위 테스트 통과만으로 완료를 선언하지 않고, 접근 불가 항목과 미검증 범위를 명확히 보고한다.

## 성공 조건

- 5개 테이블의 이력이 정확히 같은 공정별로 묶인다.
- Q-TIME FROM/TO 관점이 모두 유지되고 중복 사건임을 식별할 수 있다.
- LLM은 공정별 상세 이력에서 근거 기반 insight를 생성한다.
- 여러 LOT에서는 동일 공정의 반복 패턴과 차이를 종합한다.
- LLM 실패 시에도 원본 이력과 기존 HTML 결과는 손실되지 않는다.
- 실제 Oracle 조회, 실제 LLM 호출, 실제 API 흐름으로 최종 동작이 검증된다.
