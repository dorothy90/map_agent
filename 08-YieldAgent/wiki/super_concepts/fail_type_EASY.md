---
axis: fail_type
axis_value: EASY
confidence: 0.45
created: '2026-05-16T22:39:44'
id: super:fail_type=EASY
last_active: '2026-05-16T22:39:44'
source_concept_ids:
- concept:4SS|PRE METAL CLN|EASY
- concept:4SS|STI CMP|EASY(W)
stale_after_days: 30
status: reference_only
type: super_concept
updated: '2026-05-16T22:39:44'
version: 1
---

## 공통 패턴 (2 concepts 기반)
- **제품**: 4SS 제품군에서 `EASY`(또는 `EASY(W)`) 유형의 불량이 보고됨.
- **불량 유형**: 전기적 성능 저하 혹은 기능 저하를 의미하는 `EASY` 계열 불량.
- **공정 단계와 연관성**: `PRE METAL CLN` 단계와 `STI CMP` 단계 모두 `EASY` 불량과 연관된 사례가 보고됨. 이는 **공정 단계별 변동·오염**이 `EASY` 불량을 유발할 가능성을 시사함.
- **원인·증상**: 구체적인 원인 서술은 첫 번째 개념에서만 제공되었으며, 두 번째 개념은 원인 서술이 누락돼 있어 공통적으로 **공정 변동·오염**이 핵심 요인일 가능성을 추정함.

## 변별 요소
- **concept:4SS|PRE METAL CLN|EASY** : 세정 불완전(용액 농도·시간, 린스 부족, 노즐 오정렬, 챔버 압력 변동)으로 인한 금속 이물질 잔류가 `EASY` 불량을 초래함. 구체적인 원인·조치가 상세히 기술됨.
- **concept:4SS|STI CMP|EASY(W)** : 원인 서술이 전무하고, `주 원인`이라는 텍스트만 반복됨. 실제 원인·메커니즘은 불명확함.

## 참고 권고
- **패턴 가이드**: `EASY` 불량은 다양한 공정 단계에서 발생할 수 있으므로, **공정 파라미터 관리·모니터링**을 전 단계에 걸쳐 적용하는 것이 예방에 유효함.
- **데이터 보강**: 두 번째 개념처럼 원인 서술이 부족한 경우, 해당 공정 단계(`STI CMP`)에 대한 **실험적 검증** 및 **원인 분석**을 추가로 수행할 필요가 있음.
- **조치 우선순위**: 첫 번째 개념에서 검증된 세정·노즐·압력 관리 조치는 `EASY` 불량 감소에 효과적이므로, 다른 공정 단계에서도 유사한 **청정도·압력 관리**를 검토할 것을 권고함.