# Wiki 사내 적용 절차

> 대상 commit: `1d41bde` (2026-05-16, Karpathy 회귀 + foundations + warmup)
> 기준 baseline: `b77f676` 이후 — `MIGRATION_PROD_FAIL_RENAME.md`의 defect→fail rename 완료, OpenSearch 인덱스명 `fail-history` (≈160 docs)
> 참고: `wiki-migration-checklist.md` (전체 변경 카탈로그)

운영자가 순서대로 따라 실행하면 됩니다.

---

## 0. 전제 조건

- [ ] `git log --oneline | head -3` 에 `b77f676` 또는 그 이후 commit 존재
- [ ] OpenSearch 인덱스 `fail-history` 동작 확인: `curl -u $OS_USER:$OS_PWD http://$OS_HOST:$OS_PORT/fail-history/_count`
- [ ] 사내 로컬 LLM 엔드포인트(OpenAI 호환) 접근 가능 — `WIKI_SUMMARIZE_MODEL` 또는 `RETRIEVE_CHAIN_MODEL`로 연결
- [ ] 운영 서버에 vault 읽기·쓰기 권한 가진 경로 확보

---

## 1. 코드 pull

```bash
cd <map_agent repo>
git fetch origin
git log --oneline origin/main | head -5
# 1d41bde feat(wiki): Karpathy 정신 회귀 — 임계 완화 + foundations catalog + warmup
# 3d44a38 fix(yield_viz): cummap Δ row 색 희석 해소
# 466ce1c feat(wiki): v3 frontmatter + evidence diversity + citations, B2 함수형

git merge --ff-only origin/main    # 또는 git pull --ff-only
```

이후 `b77f676 → 0e9af9a → 747f91e → 466ce1c → 3d44a38 → 1d41bde` 가 모두 반영됨.

---

## 2. 의존성

`pyproject.toml` (또는 사내 패키지 매니저)에 `pyyaml`이 있는지 확인:

```bash
grep -E "^pyyaml|^PyYAML" pyproject.toml
```

없으면 추가:
```bash
uv add pyyaml      # 또는 pip install pyyaml
```

기존 `langchain` / `langfuse` / `opensearch-py` / `pydantic` / `python-frontmatter` 는 변경 없음.

---

## 3. 환경 변수 (`.env` 또는 운영 secrets)

```bash
# 필수
WIKI_FIRST_ENABLED=true                            # 장애 시 false로 즉시 차단 가능
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki
WIKI_REQUIRE_EXTERNAL_VAULT=true
DOWNLOAD_BASE_URL=https://internal-api/docs        # 사내 PPT 다운로드 베이스
WIKI_SUMMARIZE_MODEL=<사내 로컬 LLM 모델명>          # 미설정 시 RETRIEVE_CHAIN_MODEL fallback

# 기존 (확인만)
OPENSEARCH_HOST=<사내 OpenSearch>
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=fail-history
RETRIEVE_CHAIN_MODEL=<사내 LLM>
```

위 경로는 로컬/enterprise 검증용 절대 경로입니다. 실제 운영 서버에서는 워크스테이션
경로를 사용하지 말고, 서버에 마운트한 전용 절대 경로를 `WIKI_VAULT_PATH`에 설정합니다.
환경 변수 이름은 배포 환경마다 동일하게 유지합니다.

vault 폴더는 코드가 자동 `mkdir` — uvicorn 실행 유저의 권한만 확인:
```bash
mkdir -p $WIKI_VAULT_PATH
ls -ld $WIKI_VAULT_PATH    # 쓰기 권한 확인
```

---

## 3-1. 기존 Vault를 외부 Vault로 비파괴 마이그레이션

먼저 dry-run으로 대상 파일 수를 확인한 뒤 apply를 실행합니다.

```bash
cd 08-YieldAgent
python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --dry-run

python migrate_wiki_vault.py \
  --source wiki \
  --target /Users/daehwankim/SYLDAIX/YieldWiki \
  --apply
```

마이그레이션은 source Vault 파일을 삭제하지 않습니다. 롤백이 필요하면 서버를 중지한
다음, 이전 `WIKI_VAULT_PATH`로 복원하여 재기동합니다.

---

## 3-2. M1 외부 Vault 검증 절차

### 3-2-1. 격리 Wiki 테스트

```bash
cd 08-YieldAgent
python -m pytest -q tests/wiki
```

모든 M1 Wiki 테스트가 실패 없이 통과해야 합니다.

### 3-2-2. 문법 및 공백 검증

```bash
cd 08-YieldAgent
python -m py_compile wiki_config.py wiki_store.py wiki_router.py bootstrap_wiki_warmup.py wiki_lint.py migrate_v2_to_v3.py migrate_wiki_vault.py agent_server.py
git diff --check
```

두 명령은 모두 exit `0`이어야 합니다.

### 3-2-3. 운영 provider 합성 명령 (운영 준비 시)

아래 명령은 `4SS | EASY | PRE METAL CLN` 한 트리플에 대해서만 실제 OpenSearch와
설정된 사내 로컬 LLM을 호출합니다. 운영 endpoint가 사용 가능한 경우에만 실행하는
운영 준비 검증 명령입니다.

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
python -c 'from bootstrap_wiki_warmup import process_triple; status, message = process_triple({"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN", "source": "e2e"}, max_docs=5); print(status, message); raise SystemExit(0 if status == "ok" else 1)'
```

`fail-history`가 해당 트리플 문서를 반환하고, 구조화된 Concept 합성 결과가
`/Users/daehwankim/SYLDAIX/YieldWiki/concepts/`에 생성 또는 갱신되며, 명령은 `ok`와
exit `0`을 출력해야 합니다.

### 3-2-3A. Task 6 승인 비민감 E2E: process-only Kilo free provider

운영 기본값은 계속 사내 로컬 LLM(`WIKI_SUMMARIZE_MODEL` 또는
`RETRIEVE_CHAIN_MODEL`)입니다. Task 6의 원래 configured/OpenRouter provider 호출은
HTTP 402로 사용할 수 없었습니다. 이후 사용자가 이 E2E의 live-verification provider를
승인된 **비민감 테스트 데이터**의 process-only `kilo-auto/free`로 명시적으로 교체했습니다.
따라서 아래 명령이 Task 6에서 승인된 실제 OpenSearch → LLM → 외부 Vault E2E 경로입니다.
이는 configured/OpenRouter 또는 사내 운영 provider가 성공했다는 증거가 아닙니다. 코드나
`.env`의 provider 기본값을 변경하지 않고, 실행 프로세스 안에서만
`wiki_summarizer.get_llm`을 monkeypatch합니다.

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
python -c 'from langchain_openai import ChatOpenAI; import wiki_summarizer; wiki_summarizer.get_llm = lambda model=None, temperature=0: ChatOpenAI(model="kilo-auto/free", base_url="https://api.kilo.ai/api/gateway", api_key="anonymous", temperature=temperature); from bootstrap_wiki_warmup import process_triple; status, message = process_triple({"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN", "source": "e2e"}, max_docs=5); print(status, message); raise SystemExit(0 if status == "ok" else 1)'
```

`anonymous`는 client가 요구하는 비밀값이 아닌 placeholder이며, 영구 환경 변수나
secrets에 저장하지 않습니다. `kilo-auto/free`는 prompt와 output을 provider 서비스 개선에
사용할 수 있으므로 개인·기밀·운영 데이터를 전송해서는 안 됩니다. Kilo의 anonymous free
access는 IP당 시간당 200회로 제한되며, free model routing은 변경될 수 있습니다.
자세한 현재 제한과 데이터 처리 주의사항은 Kilo의 [rate-limit 문서](https://kilo.ai/docs/getting-started/rate-limits-and-costs)와 [free usage 문서](https://kilo.ai/docs/getting-started/using-kilo-for-free)를 확인합니다.

### 3-2-4. production Wiki router로 같은 외부 Vault 검증

현재 전역 환경에서는 기록된 `motor`/`pymongo` 호환성 문제로 전체 `agent_server`를
import할 수 없습니다. M1에서는 의존성 버전을 바꾸지 않고, production `wiki_router`를
변경 없이 mount한 최소 FastAPI 프로세스를 사용합니다. 첫 터미널에서 실행합니다.

```bash
cd 08-YieldAgent
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
python -c 'from fastapi import FastAPI; import uvicorn; from wiki_router import router; app=FastAPI(); app.include_router(router, prefix="/api/wiki"); uvicorn.run(app, host="127.0.0.1", port=8001)'
```

두 번째 터미널에서 합성한 Concept가 포함되는 product-filtered graph를 요청하고, 정확한
Concept identity와 `has_wiki`를 확인합니다. 전역 graph에 작은 limit를 적용하면 다른
product의 product/fail-type node가 먼저 소비되어 이 검증 대상이 제외될 수 있으므로,
unfiltered `limit=20`은 이 acceptance check에 사용하지 않습니다.

```bash
curl --fail 'http://127.0.0.1:8001/api/wiki/graph?view=product_tree&product=4SS&limit=100' \
  | jq -e 'any(.nodes[]; .key == "concept:4SS|PRE METAL CLN|EASY" and .attributes.has_wiki == true)'
```

명령은 exit `0`이어야 하며, HTTP `200` JSON에는
`concept:4SS|PRE METAL CLN|EASY` node 하나가 `has_wiki: true`로 포함되어야 합니다.

### 3-2-5. M1 범위 확인

```bash
git diff main...HEAD --name-only | rg '^(08-YieldAgent/(yield_frontend|wiki_frontend|repl_agent/frontend|pages)/|08-YieldAgent/app.py$)'
```

출력이 없어야 합니다. 이 검증은 M1에서 기존 frontend source와 `app.py`를 변경하지
않았음을 확인합니다.

---

## 4. `foundations.yaml` 사내 도메인 조정

`08-YieldAgent/wiki/foundations.yaml` 은 로컬 PoC seed로 작성돼 있음. 사내 운영 우선순위에 맞게 수정:

```yaml
foundations:
  - product: 4SS
    fail_type: EASY
    cause_oper: PRE METAL CLN
    priority: high
    note: 자주 조회되는 핵심 트리플
  # … 사내 도메인 지식으로 추가/수정
```

- **priority high**: 반드시 사전 warm-up 대상. lint가 vault 미존재 시 `gap` 알림.
- **priority medium/low**: warm-up은 들어가지만 미충족 허용.

vault 경로가 `WIKI_VAULT_PATH`와 다르면 그 경로에 `foundations.yaml` 을 직접 복사:
```bash
cp 08-YieldAgent/wiki/foundations.yaml $WIKI_VAULT_PATH/foundations.yaml
```

---

## 5. 서버 재기동

`migrate_v2_to_v3.py`는 빈 vault라 실행 불요.

```bash
# uvicorn 현재 PID 확인 후 종료
pkill -f "uvicorn agent_server"

# 재기동 (사내 운영은 --reload 미사용)
nohup uvicorn agent_server:app --host 0.0.0.0 --port 8001 \
  --log-level info >> /var/log/yield-agent/agent.log 2>&1 &
```

로그에 다음 표시 확인:
```
INFO:     Application startup complete
... [wiki_queue] started ...
```

---

## 6. Warm-up 실행

### 6-1. Dry-run으로 seed 후보 확인

```bash
python 08-YieldAgent/bootstrap_wiki_warmup.py --dry-run
```

출력 예시:
```
1) foundations.yaml seed: 8
  - 4SS|EASY|PRE METAL CLN (priority=high)
  ...

2) OpenSearch aggregation top-20 (min_docs=2): 18
  - 4SS|EASY(W)|PRE METAL CLN (doc=7)
  - 4SS|IDSAT(I)|ILD CMP (doc=5)
  ...

3) merged seeds: 26
```

### 6-2. 실 적용

```bash
python 08-YieldAgent/bootstrap_wiki_warmup.py --apply \
  --top 20 \
  --queries-per-triple 3 \
  --drain-timeout 600
```

- 트리플당 LLM 호출 ≈ 3회(summarize) + 1회(synthesize) = 4회
- seed 26개면 ≈ 100회 LLM 호출. 사내 LLM 처리 속도 기준 5~15분 예상
- 끝에 vault 요약 + lint 자동 실행 → `lint_logs/<오늘>.md` 생성

### 6-3. 부분 실행

처음엔 작게 시작 권장:
```bash
python bootstrap_wiki_warmup.py --apply --top 5 --queries-per-triple 2
```

성공 확인 후 더 큰 N으로 재실행 (이미 합성된 트리플은 evidence_diversity 가드로 중복 합성 안 함).

---

## 7. 검증

### 7-1. vault 상태
```bash
ls $WIKI_VAULT_PATH/concepts/ | wc -l    # 합성된 concept 수
ls $WIKI_VAULT_PATH/episodes/ | wc -l    # 누적 episode 수
cat $WIKI_VAULT_PATH/concepts/4SS_PRE_METAL_CLN_EASY.md | head -30
```

frontmatter에 `confidence`, `citations`, `body_versions` 채워졌는지 확인.

### 7-2. 검색 응답 확인

사내 Streamlit/agent에서 warm-up 한 트리플로 검색 → 응답에 다음 모두 표시되어야:

- ✅ `## 누적 패턴 (N건 분석)` 형태 본문
- ✅ `**참고 자료 (N건)**:` + 클릭 가능 `[원본 PPT 다운로드](url)` 마크다운
- ✅ `_wiki-first 응답 · confidence=N · OpenSearch 호출 0회_` 마커
- ✅ `confidence < 0.7` 인 경우 머리에 배지:
  - `> ⚠️ **근거 약함** — evidence N건, confidence=0.4X. 운영자 검수 권장.`
  - `> ℹ️ **참고용** — evidence N건, confidence=0.6X.`

### 7-3. Latency 확인

`fail_history_agent` 노드 latency:
- wiki-first 경로: **< 10s** (LLM 호출 0회)
- baseline 경로 (warm-up 안 한 트리플): 20~30s

### 7-4. 회귀 — warm-up 안 한 트리플
- 새 트리플로 검색 → `baseline` 모드 정상 응답 + episode 자동 누적

### 7-5. Lint 한 번 더
```bash
python 08-YieldAgent/wiki_lint.py --vault $WIKI_VAULT_PATH --log
cat $WIKI_VAULT_PATH/lint_logs/<오늘>.md
```

- `gap (priority=high 미존재)` ≤ 3건 — 초과 시 foundations.yaml 보강 또는 warm-up 재실행
- `low_confidence` 항목 검토 — confidence < 0.5 concept은 운영자 수동 검수 또는 archive
- `stale_episode` — 30일 경과 orphan, 필요 시 archive

---

## 8. 운영 가드 + 롤백

### 8-1. 모니터링

매주 확인:
```bash
# vault 디스크 사용량
du -sh $WIKI_VAULT_PATH

# concept 수 / confidence 분포
python -c "
import sys; sys.path.insert(0, '08-YieldAgent')
from bootstrap_wiki_warmup import summarize_vault
summarize_vault()
"

# uvicorn 로그에서 wiki-first hit 통계
grep "WIKI-FIRST" /var/log/yield-agent/agent.log | wc -l
grep "enqueue_status" /var/log/yield-agent/agent.log | tail -50
```

지표:
- **wiki_first_hit_rate**: WIKI-FIRST 로그 수 / 전체 fail_history_agent 호출 수
- **합성 큐 drain 시간**: 운영 트래픽에서 wiki_queue.stats() 또는 로그
- **lint gap 추세**: 매주 lint_logs 비교

### 8-2. 임계 재조정 (데이터 증가 시)

vault 데이터가 늘면 (예: docs > 1,000) `wiki_store.lookup_concept_body` 의 기본 임계를 다시 올릴 수 있음:
- 코드 수정 또는 env 노출 추가
- 예: `WIKI_FIRST_MIN_CONFIDENCE=0.7` 도입

### 8-3. 롤백

#### 빠른 차단 (코드 롤백 없이)
```bash
# .env
WIKI_FIRST_ENABLED=false
```
→ wiki gate 우회, 모든 검색이 baseline 경로. 단 wiki_queue ingest는 계속 동작.

#### 완전 롤백
```bash
git revert 1d41bde       # 임계 완화만 되돌림
git revert 466ce1c       # wiki 도입 자체를 되돌림 (정말 위험할 때)
git push origin main
# 사내 서버에서 git pull + uvicorn 재기동
```

---

## 9. 트러블슈팅

| 증상 | 원인 추정 | 대응 |
|---|---|---|
| warm-up 후에도 wiki-first 발동 X | concept 합성 실패 — `wiki/log.md` 또는 uvicorn stderr 확인 | LLM 응답 형식 오류 가능. `wiki_summarizer` 로그 확인 |
| `enqueue_status=skipped` 만 나옴 | `WIKI_FIRST_ENABLED=false` 또는 wiki_queue 미시작 | env 확인 + uvicorn 재기동 |
| `참고 자료` 링크가 비어 있음 | citations frontmatter doc_id 누락 | `migrate_v2_to_v3.py` 또는 episode 재 ingest |
| confidence 모두 0.0 | LLM 합성 응답에서 confidence 미반환 | `wiki_summarizer.synthesize_concept` 프롬프트/스키마 확인 |
| OpenSearch aggregation 실패 | 권한 또는 keyword 매핑 부재 | `fail_history_tools._search_opensearch` 와 동일 인증 확인 |
| `pyyaml` import 오류 | 의존성 미설치 | `uv add pyyaml` |
| `bootstrap_wiki_warmup.py` 타임아웃 | LLM 응답 지연 | `--drain-timeout 1200` 늘리거나 `--top` 줄여 재실행 |

---

## 10. 사후 작업 (선택)

- **lint 정기 실행**: cron 또는 sysd timer로 일/주 단위 `wiki_lint.py --log`
- **foundations.yaml 갱신**: lint `gap` 결과를 보고 운영자가 catalog 보강
- **Day 5 super_concept**: 충분한 concept 누적 후 PoC v3 plan Day 5로 진행
- **Day 6 eval 4-way**: baseline / wiki-on / wiki-first / wiki-assisted 정량 비교

---

## 11. 핵심 변경 카탈로그 요약 (`1d41bde`)

| 파일 | 종류 | 변경 |
|---|---|---|
| `wiki_store.py` | M | 임계 완화: unique_doc_ids·evidence_diversity 게이트 제거 |
| `wiki_queue.py` | M | 합성 트리거 완화: episode≥2면 시도, diversity<0.3만 차단 |
| `wiki_lint.py` | M | gap / low_confidence / stale_episode 룰 + `--log` 옵션 |
| `fail_history_tools.py` | M | wiki-first 응답에 confidence 배지 (≥0.7 / 0.5~0.7 / <0.5) |
| `wiki/foundations.yaml` | 신규 | 운영자 작성 prefill catalog |
| `bootstrap_wiki_warmup.py` | 신규 1회성 | foundations + OpenSearch agg → query 변형 → drain → vault summary + lint |

전체 변경 카탈로그(이전 `466ce1c` 포함)는 `wiki-migration-checklist.md` 참고.
