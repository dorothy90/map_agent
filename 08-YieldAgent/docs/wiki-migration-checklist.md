# Wiki 통합 사내환경 마이그레이션 체크리스트

> baseline: `747f91e` (사내 운영 마이그레이션 계획 보강 commit)
> 적용 범위: 2026-05-10 ~ 2026-05-11 (Day 1~4) 변경분 + B2 함수형 리팩토링
> 작성일: 2026-05-11

`MIGRATION_PROD_FAIL_RENAME.md`(defect→fail 1회성 운영 마이그레이션)와 별개. 이 문서는 wiki 합성·wiki-first·B2 함수형 노드 도입에 따른 코드 배포.

---

## A. 코드 (수정/신규) — 필수

| 파일 | 상태 | 변경 요지 |
|---|---|---|
| `wiki_store.py` | M | vault I/O v3 frontmatter 확장, atomic write(.tmp+rename), `lookup_concept_body` (multi-factor gate), `compute_evidence_diversity`, `update_concept_evidence`, alias fallback |
| `wiki_summarizer.py` | M | `synthesize_concept` (ConceptSynthesis Pydantic), `EpisodeRef` citation 구조, citation enrichment(episode 메타로 doc_id/date/source_file 보강) |
| `wiki_queue.py` | M | 2-stage async queue (summarize→persist), `evidence_diversity` 트리거, `concept_synthesis` task 분기, `_synth_in_flight` 중복 가드 |
| `fail_history_agent.py` | M | **B2 함수형 노드** — ReAct 제거, `do_search` 직접 호출 → wiki-first 즉시반환 / 아니면 LLM 1회 합성 → 조건부 HTML |
| `fail_history_tools.py` | M | `do_search`/`do_render_report` 함수 API, `_supervisor_parsed_var` ContextVar(옵션 C), wiki gate 3-tier 분기 |
| `prompts.py` | M | `FAIL_HISTORY_SYNTH_SYSTEM_PROMPT_TEMPLATE` 신규 (ReAct prompt 교체) |
| `eval/run_wiki_eval.py` | M | 신규 메트릭 함수: `compute_rouge_l`, `compute_citation_match`, `compute_freshness_regression` |
| `migrate_v2_to_v3.py` | 신규 | v2 vault → v3 frontmatter 보강 (1회 실행) |

baseline에 이미 들어가 있고 이번 사이클에 미변경:
`supervisor.py`, `agent_server.py`, `wiki_lint.py`, `wiki_router.py`, `pages/wiki_graph.py`, `templates/fail_history_report.html` → **추가 배포 불요**

---

## B. 1회성 스크립트 (환경에서 1회 실행)

- `migrate_v2_to_v3.py`
  - 사내 vault에 v2 frontmatter가 있으면 `status`, `last_active`, `stale_after_days`, `confidence`(기본 0.5), `citations`(빈 list), `evidence_diversity_score` 보강
  - vault 비어 있으면 no-op
  - `python migrate_v2_to_v3.py --vault $WIKI_VAULT_PATH --dry-run` → 변경 list 확인 후 `--apply`

---

## C. 환경 변수 (사내 운영 `.env` 또는 secrets)

```bash
# 필수
WIKI_FIRST_ENABLED=true                       # 런타임 toggle (장애 시 false)
WIKI_VAULT_PATH=/사내경로/wiki                # 사내 영구 저장 위치
DOWNLOAD_BASE_URL=https://사내-PPT-API/docs   # 실제 사내 PPT 다운로드 베이스

# 선택
WIKI_SUMMARIZE_MODEL=<로컬 LLM 모델명>         # 미설정 시 RETRIEVE_CHAIN_MODEL fallback
# STREAMLIT_BASE_URL=...                       # React 도입 후 REACT_BASE_URL로 대체
```

---

## D. 디렉토리

- `${WIKI_VAULT_PATH}/{episodes,concepts,super_concepts,lint_logs,aliases}/`
- 코드가 lazy `mkdir`로 자동 생성. uvicorn 실행 유저의 read/write 권한 확인.

---

## E. 의존성

- `pyyaml` (vault frontmatter 처리) — 사내 `pyproject.toml`에 없으면 추가
- 기존 `langchain`, `langfuse`, `opensearchpy`, `pydantic` 버전 변경 없음

---

## F. 제외 — 일회성 / 로컬 데이터

- `wiki/{aliases,concepts,episodes}/*.md` — 로컬 실험 vault. 사내는 빈 상태로 시작 (검색 들어오면서 자동 ingest)
- `wiki/log.md` — 로컬 로그
- `eval/datasets/*.json`, `eval/results/*` — sample 데이터 (사내 데이터로 따로 수집)
- 어제/오늘 인라인 patch 스크립트 — 효과가 코드(`wiki_summarizer.py` citation enrichment)에 반영됨

---

## G. 적용 순서 (권장)

1. **사내 baseline 확인**
   - `git log --oneline | head -3` → `747f91e` 또는 동등 commit
2. **변수 셋업**
   - `.env` C 섹션 값 추가, vault 폴더 권한 확인
3. **코드 배포** (A 섹션 8개 파일)
   - `git pull` 또는 cherry-pick으로 본 commit 반영
4. **의존성 설치**
   - `uv pip install -e .` 또는 사내 패키지 매니저로 `pyyaml` 보강
5. **마이그레이션 (1회)**
   - `python migrate_v2_to_v3.py --vault $WIKI_VAULT_PATH --dry-run` → `--apply`
6. **서버 재기동**
   - uvicorn PID kill + 재기동 (사내 운영은 `--reload` 미사용 가정)
7. **검증**
   - `/health` 200
   - 검색 1회: wiki-first 미발동(빈 vault) → baseline 경로 → episode 자동 누적 시작
   - 같은 트리플 5회 반복 → `evidence_diversity_score ≥ 0.6` 도달 후 concept 합성 트리거
   - concept md 파일에 citations doc_id 채워졌는지 확인
8. **롤백**
   - `.env`에서 `WIKI_FIRST_ENABLED=false` → wiki-first 경로 우회 (코드 롤백 없이도 가능)
   - 코드 자체 롤백 시 A 섹션 파일을 `747f91e`로 되돌림

---

## H. 운영 모니터링 (배포 후)

- `wiki_update_status` 로그 분포 — `enqueued|wiki-first|skipped`
- vault 디스크 사용량 `du -sh ${WIKI_VAULT_PATH}` — body_versions 누적 가드 동작 확인
- LLM 비용: wiki-first hit rate × 토큰 절감 (Day 6 eval과 연계)
- `wiki_first_hit_rate` 너무 낮으면 임계 (`WIKI_FIRST_MIN_*`) 조정
- B2 함수형 노드 latency: 기존 ReAct 84s → 22s 수준 유지되는지 확인 (regression guard)
