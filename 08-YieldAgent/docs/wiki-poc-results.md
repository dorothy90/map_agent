# Wiki PoC v3 — 사내 운영 결과 (템플릿)

> 운영 적용 후 1~2주 데이터 누적된 시점에 `eval/run_wiki_eval.py` 실행 결과를 채워서 PoC 종료 보고로 사용.
> 측정 도구: `python -m eval.run_wiki_eval --bench main --mode both --save`
> 데이터: 사내 `fail-history` 인덱스 + warm-up 후 vault 상태
> 작성자: ____  작성일: ____

---

## 0. 환경 스냅샷

| 항목 | 값 |
|---|---|
| 측정 commit | `_______` (`1d41bde` 이후 통합 commit) |
| 사내 OpenSearch index | `fail-history` |
| docs 수 | `___` (시작 ≈ 160, 측정 시점 ___) |
| vault episodes / concepts / super_concepts | `___` / `___` / `___` |
| warm-up 실행 일자 | `____-__-__` (`bootstrap_wiki_warmup.py --apply --top __`) |
| 측정 기간 | `____-__-__` ~ `____-__-__` (___일) |
| LLM 모델 | `WIKI_SUMMARIZE_MODEL=______` |
| WIKI_FIRST_MIN_CONFIDENCE | `___` (default 0.5) |
| WIKI_SUPER_REFERENCE_ENABLED | `true | false` |

---

## 1. Success Criteria 측정

### 1-1. 적중률 (`wiki_first_hit_rate`)

| 분모 | baseline | wiki-on | wiki-first 비율 | 목표 | 달성 |
|---|---|---|---|---|---|
| 전체 질의 | N/A | `__/__` | `___%` | ≥ 10% | ☐ |
| 풍부-concept 질의 | N/A | `__/__` | `___%` | ≥ 30% | ☐ |
| exact-triple 질의 | N/A | `__/__` | `___%` | (참고) | — |

### 1-2. 토큰·Latency 절감 (wiki-first 경로 한정)

| 지표 | baseline 평균 | wiki-first 평균 | 절감률 | 목표 | 달성 |
|---|---|---|---|---|---|
| 입력 토큰 | `____` | `____` | `___%` | ≥ 40% | ☐ |
| Latency (s) | `__.__` | `__.__` | `___%` | ≥ 60% | ☐ |
| LLM 호출 수 | `_` | **0** | 100% | — | ✓ |

### 1-3. 합성 효과 (`concept_improvement_score` ROUGE-L)

| 비교 | ROUGE-L | 목표 | 달성 |
|---|---|---|---|
| wiki-first body vs raw 결과 | `___` | ≥ +0.05 (vs baseline) | ☐ |
| wiki-assisted (raw+wiki) vs baseline (raw만) | `___` | (참고) | — |

### 1-4. Citation 일관성 (`citation_match_rate`)

| 모드 | citation_match | 목표 | 달성 |
|---|---|---|---|
| wiki-first | `___` | ≥ 0.8 | ☐ |
| wiki-assisted | `___` | (참고) | — |

### 1-5. 회귀 가드

| 지표 | baseline | wiki-on | 손실 | 임계 | 회귀 |
|---|---|---|---|---|---|
| `must_mention_rate` (전체) | `___` | `___` | `__%` | ≤ 3% | ☐ 없음 / ☐ 있음 |
| `recall@5` (전체) | `___` | `___` | `__%` | 0% | ☐ 없음 / ☐ 있음 |
| `freshness_regression` (30d 초과 응답) | N/A | `__건` | — | 0건 | ☐ |

### 1-6. 종합 판정

- ☐ 모든 Success Criteria 달성 — 사내 운영 정착 권고
- ☐ 일부 미달 (사유: ____________) — 임계 조정 또는 추가 데이터 후 재측정
- ☐ 회귀 발견 — 롤백 또는 hotfix 필요

---

## 2. retrieval_mode 분포

| 모드 | 건수 | 비율 |
|---|---|---|
| wiki-first | `__` | `__%` |
| wiki-assisted | `__` | `__%` |
| baseline (wiki on 상태에서 미발동) | `__` | `__%` |

---

## 3. Vault 상태

- `concepts/`: `__`개  
  - confidence ≥ 0.7: `__`개  
  - 0.5 ≤ confidence < 0.7: `__`개 (`참고용` 배지 표시 대상)  
  - confidence < 0.5: `__`개 (lint `low_confidence` 후보)
- `episodes/`: `__`개 (orphan: `__`개, stale 30d+: `__`개)
- `super_concepts/`: `__`개 (생성 시점 ____)
- `aliases/`: `__`개

---

## 4. lint 결과 (마지막 실행: ____-__-__)

| 카테고리 | 건수 | 비고 |
|---|---|---|
| `gap` (foundations priority=high 누락) | `__` | foundations.yaml 보강 필요 |
| `low_confidence` | `__` | 운영자 검수 대상 |
| `orphan` | `__` | concept으로 안 묶인 episode |
| `stale_episode` (30d+) | `__` | archive 또는 재합성 후보 |
| `broken_link` | `__` | 0이어야 정상 |
| `alias_asymmetry` | `__` | 0이어야 정상 |
| `duplicate_concept` | `__` | 0이어야 정상 |
| `invalid_frontmatter` | `__` | 0이어야 정상 |

---

## 5. 사용자 피드백 (정성)

> 사내 운영자/엔지니어 인터뷰 정리. 다음 항목 필수:
> - wiki-first 답변이 raw OpenSearch 결과보다 도움되는지
> - PPT 다운로드 링크 활용도
> - confidence 배지(참고용·근거 약함) 의미 전달 정상인지
> - 신뢰성 우려 (할루시네이션 사례 있는지)

### 긍정
- 
- 

### 개선 요청
- 
- 

---

## 6. 결정 사항

| 항목 | 상태 |
|---|---|
| 사내 정착 진행 | ☐ |
| 임계 재조정 (`WIKI_FIRST_MIN_CONFIDENCE` 등) | ☐ 필요 / ☐ 불요 (값: ___) |
| super_concept UI 노출 활성화 (`WIKI_SUPER_REFERENCE_ENABLED=true`) | ☐ 활성화 / ☐ 보류 |
| lint cron 활성화 (`WIKI_LINT_CRON_HOURS=24`) | ☐ |
| 후속 PoC v4 추진 | ☐ |

---

## 7. 후속 작업 (PoC 외)

- ☐ React+Vite force-graph 컴포넌트 (Streamlit dev viewer 대체)
- ☐ 임계 조정 후 재측정
- ☐ 운영자 검수 워크플로우 (super_concept review UI)
- ☐ 다른 agent 통합 (WADS/lot_history)
- ☐ 사용자 직접 wiki 편집 UI
- ☐ 임베딩 기반 의미 클러스터 합성

---

## 8. 부록

- 측정 데이터 raw: `eval/results/wiki_eval_main_<timestamp>.json`
- 사용된 dataset: `eval/datasets/______.json`
- 관련 commit: `1d41bde` (Karpathy 회귀) + `_______` (옵션 4 + Day 5/6 통합)
- 참고 문서: `wiki-deployment-procedure.md`, `wiki-migration-checklist.md`
