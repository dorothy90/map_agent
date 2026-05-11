# 불량이력 Agent — 요구기술수준 (Technical Requirements Specification)

> 대상: 반도체 수율/품질 도메인 불량이력 조회·합성 에이전트
> 범위: `fail_history_agent` 노드 + Wiki 메모리 백본 + 멀티에이전트 오케스트레이션
> 작성 기준: plan v3, 사내 LLM/OpenSearch 인프라 가정

---

## 1. 업무 구조화 및 AI 해결전략 설계

### 1.1 업무 정의

| 항목 | 내용 |
|------|------|
| **업무 목적** | 과거 불량이력(원인/조치/관찰) PPT·리포트 자산에서 동일/유사 사건을 즉시 검색·합성하여 현장 엔지니어의 의사결정 시간을 단축 |
| **핵심 사용자** | 수율 엔지니어, 공정 PE, 신뢰성/품질 담당 |
| **입력** | `(product, fail_type, cause_oper)` 트리플 + 자연어 쿼리(`dh_query`) |
| **출력** | 한국어 합성 답변(markdown) + 원본 PPT 인용/다운로드 링크 + HTML 카드 아티팩트 |
| **운영 SLA** | wiki-first 경로 < 2s, baseline RAG 경로 < 8s, 합성 실패율 < 5% |

### 1.2 As-Is / To-Be 비교

| 구분 | As-Is (수작업) | To-Be (Agent) |
|------|----------------|----------------|
| 검색 | 사내 파일서버·SharePoint 키워드 검색 | 하이브리드(BM25+kNN) + 약어 확장 + 메타필터 |
| 합성 | 엔지니어가 PPT 5~10건을 직접 비교 | LLM이 N개 episode 통합 → concept rollup |
| 재사용 | 결과는 휘발성, 같은 질문 반복 답습 | Wiki vault에 누적, 임계 만족 시 0-LLM 재현 |
| 신뢰성 | 답변 출처 추적 불가 | citation 의무화(`[ep:xxx]`, `[FH-XXXXXX]`) |

### 1.3 AI 해결전략 (전략 4축)

1. **검색 정확도 우선 (Recall-first)**: 약어 사전(`ACRONYM_MAP`) 확장 + BM25 0.4 / kNN 0.6 가중 정규화로 표기 변형·동의어 견딤.
2. **합성은 인용 강제 (Citation-mandatory)**: LLM 출력 스키마(`ConceptSynthesis`)에 inline `[ep:xxx]` 인용을 필수 필드로 강제, 인용 없는 주장 차단.
3. **누적 학습은 메모리 백본으로 (Memory-as-a-System)**: 단일 검색은 휘발적이지만 episode→concept rollup이 지속 자산화. wiki-first 게이트(confidence ≥ 0.7, unique_doc_ids ≥ 4) 통과 시 LLM 호출 0회.
4. **블랙박스 금지 (Auditable-by-default)**: 모든 노드 `@observe` 트레이싱(Langfuse), wiki `log.md` append-only, 합성 미발동 사유(`fail_reasons`)도 저장.

---

## 2. RAG 이중구조 (Two-Tier RAG) 요구기술

본 에이전트는 **단일 검색용 baseline RAG**와 **누적 학습용 메모리 RAG**의 이중 구조로 설계한다.

### 2.1 Tier 1 — Baseline Hybrid RAG

| 기술 요소 | 요구 수준 |
|-----------|-----------|
| **검색 백엔드** | OpenSearch 2.x, neural search plugin (`search_pipeline`/`normalization-processor`) |
| **벡터 인덱스** | `qwen3-embedding-8b` (4096 dim) — 사내 endpoint 호환 |
| **하이브리드 결합** | BM25(0.4) + kNN(0.6) min-max 정규화 → arithmetic_mean |
| **쿼리 전처리** | ACRONYM_MAP 기반 확장 (WTM→Wafer Test Module 등 9종+) |
| **메타필터** | `product.keyword`(term), `fail_type.keyword`(prefix — alias 포용), `cause_oper`(term) |
| **임베딩 캐시** | `functools.lru_cache(maxsize=128)` + 429/5xx 지수백오프 재시도 (2^n초) |

### 2.2 Tier 2 — Memory-based Advanced RAG (Wiki Vault)

핵심 차별점: **검색 결과를 그대로 답변하는 것이 아니라, episode(immutable snapshot) → concept(mutable rollup) → alias(symmetry) 3-노드 그래프로 영속화**한다.

#### 2.2.1 게이트 정책 (3-mode)

| Mode | 발동 조건 | LLM 호출 | 응답 시간 |
|------|-----------|----------|-----------|
| **wiki-first** | concept body ≥ 400자 ∧ source_episodes ≥ 3 ∧ unique_doc_ids ≥ 4 ∧ confidence ≥ 0.7 ∧ citation_coverage ≥ 0.8 ∧ last_active ≤ 30d ∧ status=active | **0회** | < 2s |
| **wiki-assisted** | body ≥ 200자 ∧ confidence ≥ 0.4 (일부 임계 미달) | 1회 (raw + wiki body 동시 context) | < 8s |
| **baseline** | 위 조건 미달 | 1회 (raw only) | < 8s |

#### 2.2.2 합성 트리거 — Evidence Diversity Score

같은 raw set만 반복 누적되는 것을 차단하기 위해 `compute_evidence_diversity`로 0~1 점수 산출:

```
score = 0.6·min(1, unique_doc/N/1.5)
      + 0.3·min(1, unique_dates/N)
      + 0.1·min(1, N/5)
```

`score`가 threshold 이상일 때만 concept body 재합성 → 동어반복 방지.

#### 2.2.3 멀티턴 컨텍스트 처리

- **세션 단위 메모리**: LangGraph `Checkpointer` (`InMemorySaver` PoC → `PostgresSaver` 운영) + `thread_id`로 사용자 세션 격리.
- **요청 단위 격리**: `_tool_payload_var`, `_wiki_payload_var`, `_supervisor_parsed_var` 세 개의 `ContextVar`로 reports / wiki hit_ids / supervisor 추출 필터를 분리. 동시 요청 시 cross-talk 방지.
- **Supervisor Fallback**: ReAct LLM이 `search_fail_history` 호출 시 filter를 빠뜨리면 `_get_supervisor_parsed()`로 자동 보강(plan v3 옵션 C).
- **턴간 인용 연결**: 같은 thread 내 후속 질문이 같은 concept을 가리키면 wiki citation이 자동으로 재사용되어 답변 일관성 유지.

#### 2.2.4 기타 고급 RAG 기술

| 기법 | 적용 위치 | 효과 |
|------|-----------|------|
| **Query Rewriting** | `supervisor.py` 상단 `rewrite` 노드 | 모호한 표현→명확 쿼리 (예: "그거 어떻게 됐어" → "4SS STI CMP EASY 불량 조치 결과") |
| **Acronym Expansion** | `_expand_acronyms()` | BM25 recall ↑, 표기 누락 보완 |
| **Alias Symmetry Graph** | `upsert_alias()` 양방향 노드 | "EASY" ↔ "EASY(W)" 자동 매칭 (lookup_concept_body 폴백 경로) |
| **Citation-aware Synthesis** | `ConceptSynthesis` schema | LLM이 인용 없는 주장 출력 자체 차단 (pydantic 강제) |
| **Body Versioning** | `body_versions` (cap 5) | 시간 경과에 따른 합성 본문 진화 추적 + 롤백 가능 |
| **TTL/Lifecycle** | `stale_after_days=30`, `status=active|stale|archived` | 오래된 concept 자동 강등 → wiki-first에서 제외 |
| **Structured Output** | `with_structured_output(method="function_calling")` | 모델 독립적인 스키마 강제 (OpenAI/Anthropic/사내 LLM 호환) |

---

## 3. Metadata 설계

### 3.1 OpenSearch 인덱스 스키마 (`fail-history`)

| 필드 | 타입 | 인덱싱 | 용도 |
|------|------|--------|------|
| `product` | text + .keyword | both | BM25 매칭 + term 필터 |
| `fail_type` | text + .keyword | both | prefix 필터(alias 포용) |
| `cause_oper` | keyword | exact | term 필터 (정규 공정명) |
| `cause` | text | BM25 | 원인 본문 |
| `action` | text | BM25 | 조치 본문 |
| `comment` | text | BM25 | 코멘트 |
| `content` | text | BM25 | 전문 검색 |
| `embedding` | knn_vector(4096, l2/cosine) | kNN | 의미 검색 |
| `doc_id` | keyword | exact | 인용 키 (`FH-XXXXXX`) |
| `source_file` | keyword | exact | PPT 파일명 |
| `date` | date | range | 시점 필터 |
| `page_num` | integer | - | 페이지 추적 |
| `filenm` | keyword | exact | 다운로드 라벨 |

### 3.2 Wiki Vault Frontmatter 스키마 (3 노드 타입)

#### Episode (immutable snapshot)
```yaml
id: episode:<sha1[:12]>
type: episode
created: <iso>
updated: <iso>
version: 1
query: <원문>
query_normalized: <소문자+공백+ACRONYM 확장>
filters: {product, fail_type, cause_oper}
doc_ids: [FH-000429, ...]
summary: <1-line>
links: []
# lifecycle
status: active
last_active: <iso>
stale_after_days: 30
# 신뢰성
confidence: 0.5  # episode 단일이라 중립
citations: []
```

#### Concept (mutable rollup)
```yaml
id: concept:<product>|<cause_oper>|<fail_type>
type: concept
version: <증가>
seen_count: <누적 검색 횟수>
source_episode_ids: [episode:xxx, ...]
# 합성 결과
confidence: 0.0~1.0
citations: [{episode_id, doc_id, date, source_file, natural_label, download_url}, ...]
evidence_diversity_score: 0.0~1.0
unique_doc_ids: <int>
body_versions:  # cap 5
  - {version, created, expires_at, source_episode_ids, body_markdown, confidence}
# lifecycle
status: active | stale | archived
last_active: <iso>
last_evidence_check: <iso>  # 합성 미발동도 갱신 (진단 가시성)
```

#### Alias (symmetry node)
```yaml
id: alias:<canonical>|<variant>
type: alias
canonical: EASY
variant: EASY(W)
confidence: 1.0  # LLM 확정 발견
```
*양방향 생성 의무: `(c,v)`와 `(v,c)` 두 파일 모두 존재해야 lint 통과.*

### 3.3 Metadata 설계 원칙

1. **Immutable vs Mutable 분리**: episode는 절대 수정 불가(스냅샷 무결성), concept만 누적 갱신.
2. **Atomic Write**: `tmp → os.rename`으로 동시 write 충돌 가드 (`wiki_store._write`).
3. **단일 저자 (Single-writer)**: wiki 워커 1개가 모든 write 담당 → 파일락 불요.
4. **진단 가시성**: 합성 발동 여부와 무관하게 `last_evidence_check`, `evidence_diversity_score`를 매회 기록 → "왜 합성 안 됐는지"를 파일만 보고 추적 가능.
5. **Append-only Audit Log**: `wiki/log.md`에 모든 episode_create / concept_update / alias_create 이벤트 timestamp 기록.
6. **Citation 이중 트랙**: 내부 audit용(`episode_id`)과 사용자 가시용(`natural_label`, `download_url`) 분리.

---

## 4. Multi-Agent Orchestration & 도메인별 전문 에이전트 클러스터

### 4.1 계층 구조 (Hierarchical Supervisor-Worker)

```
                ┌──────────────────────────────────┐
                │   Top Supervisor (rewrite → route) │
                │   ReAct, max 4 steps              │
                └────────────┬─────────────────────┘
                             │ Command(goto=...)
       ┌─────────────────────┼──────────────────────┬──────────────┐
       ▼                     ▼                      ▼              ▼
 ┌───────────┐         ┌───────────┐          ┌──────────┐    ┌──────────┐
 │ Yield     │         │ Fail      │          │ WADS     │    │ Map      │
 │ Cluster   │         │ History   │          │ Cluster  │    │ Cluster  │
 │           │         │ Cluster   │          │          │    │          │
 ├───────────┤         ├───────────┤          ├──────────┤    ├──────────┤
 │ yield_q   │         │ FH search │          │ wads_q   │    │ binmap   │
 │ anomaly   │         │ FH synth  │          │ wads_viz │    │ cummap   │
 │ viz       │         │ wiki_sum  │          │          │    │ zones    │
 │           │         │ wiki_worker│         │          │    │          │
 └───────────┘         └─────┬─────┘          └──────────┘    └──────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Wiki Vault     │ ← 모든 클러스터 공통 메모리 백본
                    │  (episode/      │
                    │   concept/      │
                    │   alias)        │
                    └─────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ PPT Export Agent│ ← 결과 자동 문서화
                    └─────────────────┘
```

### 4.2 도메인별 전문 에이전트 클러스터

| 클러스터 | 전문 영역 | 구성 노드 | 책임 분리 |
|----------|-----------|-----------|-----------|
| **Yield Cluster** | 수율 시계열 조회·이상감지 | `yield_query_agent`, anomaly detector, `yield_viz` | DB → 시계열 → HTML |
| **Fail History Cluster** | 불량이력 검색·합성 | `fail_history_agent`, `wiki_summarizer`, `wiki_queue` worker | 검색 → 합성 → vault 영속화 |
| **WADS Cluster** | 열화 리포트 (서브그래프) | `wads_agent`, `wads_tools` | Oracle CLOB → 정제 → 리포트 |
| **Map Cluster** | 웨이퍼 맵 시각화 | `map_agent`, `wafer_zones`, `cummap` | 좌표 → bin → PNG/HTML |
| **Relation Cluster** | LOT 계보·관계 추적 | `relation_tree_agent`, `lot_history_agent` | 계보 그래프 |
| **REPL Cluster** | Ad-hoc 분석 | `repl_agent` | 동적 코드 실행 (샌드박스) |
| **Export Cluster** | 문서 자동화 | `ppt_export_agent`, `ppt_llm_designer`, `ppt_builder`, `ppt_renderer` | LLM 설계 → 템플릿 렌더 |

### 4.3 오케스트레이션 패턴 요구 수준

| 패턴 | 적용 | 요구 기술 |
|------|------|-----------|
| **Supervisor + Subagents (Tool Calling)** | Top supervisor가 도메인 클러스터를 도구처럼 호출 | LangGraph `Command(goto, update)` 기반 라우팅 |
| **Handoffs (Agent Switching)** | 클러스터 내부에서 search→synth→render 순차 전환 | StateGraph edge + conditional routing |
| **Subgraph Composition** | WADS·Fail History는 서브그래프로 캡슐화 | `StateGraph.compile()` 결과를 노드로 임베드 |
| **Step Budget** | supervisor 무한루프 방지 | `step_count` 카운터, max 4 |
| **Shared Memory** | 클러스터 간 wiki vault 공유 | 동일 `_VAULT` 경로, 같은 frontmatter 스키마 |
| **Streaming Updates** | 장기 작업 중 진행률 보고 | `get_stream_writer()` custom mode |
| **Human-in-the-Loop** | 신뢰도 < 임계 시 confirm | `interrupt()` + `Command(resume=...)` |
| **Guardrails** | PII / 사내 비공개 정보 마스킹 | before_model 미들웨어 |

### 4.4 업무 자동화 — Wiki 형식 문서 자동 구현

#### 4.4.1 자동화 파이프라인

```
[검색 발생]
    ↓
[do_search → results]
    ↓
[wiki_queue.summarize_enqueue] ── 비동기 ──▶ [wiki worker thread]
                                                  ↓
                                          [wiki_summarizer.summarize]
                                          (SummarizeOut: episode_summary,
                                           episode_body_md, alias_pairs)
                                                  ↓
                                          [upsert_episode]  ←─ immutable
                                                  ↓
                                          [evidence_diversity 계산]
                                                  ↓
                                          [score ≥ threshold?]
                                          ├─ No  → episode만 저장
                                          └─ Yes → [synthesize_concept]
                                                      (ConceptSynthesis:
                                                       body_markdown,
                                                       confidence, citations)
                                                      ↓
                                                   [upsert_concept]
                                                      ↓
                                                   [upsert_alias (있으면)]
                                                      ↓
                                                   [log.md append]
```

#### 4.4.2 Wiki 형식 요구사항

| 요건 | 구현 |
|------|------|
| **마크다운 표준** | python-frontmatter (YAML front-matter + markdown body) |
| **고정 섹션 구조** | `## 누적 패턴` / `## 검증된 조치` / `## 미해결 / 이상 케이스` |
| **인용 표준** | `[ep:abc123]` (episode), `[FH-000429]` (raw doc), `[concept:...]` (cross-link) |
| **링크 그래프** | frontmatter `links: [...]` 필드로 노드 간 양방향 참조 |
| **뷰어** | Streamlit `pages/wiki_graph.py` (PoC) → React 운영 |
| **딥링크** | `?focus=concept:<key>` URL 파라미터로 특정 노드 즉시 펼치기 |
| **Lint** | `wiki_lint.py`: alias symmetry, dangling link, missing frontmatter 검사 (CI 게이트) |
| **마이그레이션** | `migrate_v2_to_v3.py`: 스키마 진화 시 in-place 변환 |

#### 4.4.3 자동화 거버넌스

1. **단일 워커 보장**: `wiki_queue` 내 단일 thread → race condition 차단.
2. **재시도 정책**: LLM 호출 실패 시 episode는 raw로 fallback 저장(정보 손실 0), concept 합성만 skip.
3. **토큰 가드**: prompt 입력 시 raw 상위 5건, episode 상위 10건, body 500자 cap.
4. **롤백 가능성**: `body_versions` cap 5 → 직전 4개 버전 복원 가능.
5. **CI 통합**: PR 시 `wiki_lint`로 alias symmetry / orphan node 검증, 실패 시 머지 차단.

---

## 5. 비기능 요구사항 (NFR)

| 항목 | 요구 수준 |
|------|-----------|
| **응답 시간** | wiki-first p95 < 2s, baseline p95 < 8s |
| **동시성** | 단일 인스턴스 ≥ 10 concurrent users (ContextVar 격리 검증) |
| **관측성** | Langfuse 트레이스 100% 커버 (`@observe` 데코레이터), Streamlit dashboard |
| **재현성** | 같은 (query, filter, doc_ids) → 같은 `episode_id`(sha1) |
| **보안** | 사내 LLM 우선, OpenRouter는 dev only. citation URL은 사내 도메인 화이트리스트 |
| **테스트** | unit (검색/합성/upsert), integration (e2e), eval set (precision/recall/citation_coverage) |
| **운영 모니터링** | wiki-first hit rate, evidence_diversity 분포, fail_reasons top-N, LLM 실패율 |

---

## 6. 인프라 요구사항

| 영역 | 요구 |
|------|------|
| **LLM** | 사내 GPT-OSS 120B (운영), Claude/GPT (백업), function_calling 지원 |
| **Embeddings** | qwen3-embedding-8b (4096-dim), OpenRouter/사내 endpoint 호환 |
| **Vector Store** | OpenSearch 2.x + neural-search plugin |
| **Checkpointer** | PostgresSaver (운영), InMemorySaver (dev) |
| **Wiki Vault** | 로컬 FS (PoC) → NAS/S3 mount (운영), git-versioned 권장 |
| **Worker** | wiki_queue thread (PoC) → Celery/RQ (운영) |
| **Tracing** | Langfuse self-hosted |
| **UI** | Streamlit (PoC) → React (운영) |

---

## 7. 단계별 도입 로드맵 (요약)

| Phase | 범위 | 산출물 |
|-------|------|--------|
| Day 1~3 | Baseline RAG + episode immutable + wiki_queue | OpenSearch 검색, episode 누적 |
| Day 3~5 | Concept rollup + evidence diversity + wiki-assisted | 같은 트리플 N개 합성 |
| Day 5~7 | wiki-first 게이트 + citation 강제 + alias graph | 0-LLM 재현, 표기 변형 견딤 |
| Phase 2 | 멀티 클러스터 통합(Yield/WADS/Map과 vault 공유) | 크로스 도메인 인용 |
| Phase 3 | PPT 자동 export + React 운영 뷰어 + CI 거버넌스 | 사용자 가시 자동 문서화 |
