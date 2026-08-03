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

M1은 cross-process lock이 없는 **단일 자동 writer** 구조입니다. 운영 순서는 반드시
`migrate → bootstrap → validate/lint → server` 입니다. 마이그레이션, v2→v3 보강,
bootstrap 중에는 `agent_server`와 `wiki_queue`가 완전히 중지되어 있어야 합니다.
운영 트래픽과 mutating CLI를 동시에 실행하지 않습니다.

```bash
# 현재 server/queue writer 중지 및 확인
pkill -f "uvicorn agent_server" || true
pgrep -af "uvicorn agent_server" && exit 1 || true
```

---

## 3-1. 기존 Vault를 외부 Vault로 비파괴 마이그레이션

먼저 dry-run으로 대상 파일 수를 확인한 뒤 apply를 실행합니다.

```bash
cd 08-YieldAgent
python migrate_wiki_vault.py \
  --source wiki \
  --target "$WIKI_VAULT_PATH" \
  --dry-run

python migrate_wiki_vault.py \
  --source wiki \
  --target "$WIKI_VAULT_PATH" \
  --apply
```

기존 노트에 v3 frontmatter 보강이 필요하면 server가 중지된 이 단계에서 먼저 dry-run을
검토하고 적용합니다. 이 CLI는 `--apply` 옵션이 없으며, `--dry-run`을 생략하면 적용합니다.

```bash
python migrate_v2_to_v3.py --vault "$WIKI_VAULT_PATH" --dry-run
python migrate_v2_to_v3.py --vault "$WIKI_VAULT_PATH"
```

마이그레이션은 source Vault 파일을 삭제하지 않습니다. 롤백이 필요하면 서버를 중지한
다음, 이전 `WIKI_VAULT_PATH`로 복원하여 재기동합니다.

### 운영 중 재실행과 충돌 조정

이 명령은 **초기 cutover용 복사 도구**입니다. 외부 Vault가 실제로 운영된 뒤에는
`log.md`처럼 서버가 생성·추가하는 파일이 기존 source Vault와 달라질 수 있습니다.
그 상태에서 `--apply`를 다시 실행하면 `different target file: .../log.md`와 함께
종료합니다. 이는 오류를 숨기거나 대상 파일을 덮어쓰지 않고, 기존 source와 운영 중인
Vault 양쪽의 내용을 모두 보존하기 위한 의도된 안전 장치입니다.

충돌이 나면 `--apply`를 반복하거나 source와 target 사이의 직접 `cp`, `rm`, 강제
덮어쓰기로 해결하지 마세요.
먼저 해당 Vault writer를 중지하거나 작업이 끝났음을 확인한 뒤, 두 파일을 별도의
임시 검토 디렉터리에 보존하고 비교합니다. 아래 예시는 읽기·복사만 하며 어느 쪽도
변경하지 않습니다.

```bash
REVIEW_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wiki-migration-conflict.XXXXXX")"
cp -p wiki/log.md "$REVIEW_DIR/log.md.source"
cp -p "$WIKI_VAULT_PATH/log.md" "$REVIEW_DIR/log.md.target"
diff -u "$REVIEW_DIR/log.md.source" "$REVIEW_DIR/log.md.target" || true
```

운영자는 두 사본을 검토하여 source에만 있는 초기 내용과 target에만 있는 운영 기록을
포함하는 해법을 명시적으로 승인해야 합니다. 승인된 병합본은 Vault 백업·변경 관리
절차에 따라 적용하고, 조정 근거와 파일 경로를 운영 기록에 남깁니다. 이미 운영 중인
Vault는 과거 source Vault에서 재마이그레이션하지 않는 것이 기본입니다. 재마이그레이션이
정말 필요하면, 승인된 병합 결과를 기준 source로 준비하고 별도 검증 대상에서 dry-run과
checksum 검증을 다시 수행한 후에만 적용합니다.

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

초기 cutover에서는 `migrate_wiki_vault.py`가 repository Vault의 `foundations.yaml`도 함께
옮기므로 보통 별도 복사가 필요 없습니다. 우선순위는 bootstrap 전에 검토합니다.

```yaml
foundations:
  - product: 4SS
    fail_type: EASY
    cause_oper: PRE METAL CLN
    priority: high
    note: 자주 조회되는 핵심 트리플
```

- **priority high**: lint에서 미합성 triple을 `gap`으로 보고합니다.
- **priority medium/low**: bootstrap seed에는 포함되지만 gap 강제 대상은 아닙니다.

외부 Vault에 파일이 정말 없을 때만 create-only 복사를 사용합니다. `cp -n`은 기존 파일을
덮어쓰지 않습니다.

```bash
test ! -e "$WIKI_VAULT_PATH/foundations.yaml" || exit 1
cp -n wiki/foundations.yaml "$WIKI_VAULT_PATH/foundations.yaml"
```

파일이 이미 있으면 위 명령을 사용하지 않습니다. source와 operator-edited target을 각각
보존한 뒤 snapshot/diff/승인 절차로 조정합니다.

```bash
REVIEW_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wiki-foundations-review.XXXXXX")"
cp -p wiki/foundations.yaml "$REVIEW_DIR/foundations.yaml.source"
cp -p "$WIKI_VAULT_PATH/foundations.yaml" "$REVIEW_DIR/foundations.yaml.target"
diff -u "$REVIEW_DIR/foundations.yaml.source" "$REVIEW_DIR/foundations.yaml.target" || true
```

두 사본은 승인 완료까지 유지합니다. 승인된 병합본만 Vault backup/change-management
절차로 반영하며, 기존 target에 직접 `cp`하지 않습니다.

---

## 5. Bootstrap 실행 — server는 계속 중지

### 5-1. Dry-run으로 seed 후보 확인

Dry-run은 foundations와 OpenSearch aggregation 결과만 보여주며 LLM이나 Vault writer를
실행하지 않습니다.

```bash
python bootstrap_wiki_warmup.py --dry-run \
  --top 20 \
  --min-docs 2 \
  --max-docs 15
```

### 5-2. 작은 범위부터 직접 합성

현재 bootstrap은 처리할 triple마다 raw document를 최대 `--max-docs`개 조회하고
`synthesize_concept_from_docs`를 한 번 호출한 뒤 Concept를 직접 upsert합니다.
**트리플당 LLM 호출 1회**이며 bootstrap은 episode를 생성하지 않습니다.

```bash
python bootstrap_wiki_warmup.py --apply \
  --top 5 \
  --min-docs 2 \
  --max-docs 10 \
  --skip-existing \
  --no-lint
```

확인 후 `--top 20 --max-docs 15`처럼 범위를 넓힙니다. `--skip-existing`은 confidence가
0보다 큰 기존 Concept를 건너뜁니다. 이 옵션을 빼면 같은 triple도 다시 합성합니다.
모든 seed가 skip되어 실제 처리 건수가 0이면 현재 CLI는 exit `1`을 반환하므로 출력의
`--skip-existing` 전후 seed 수를 함께 확인합니다.

`--no-lint`는 이 절차에서 lint를 다음 검증 단계로 분리하기 위한 옵션입니다. 이 옵션을
빼면 bootstrap 종료 시 `wiki_lint.scan()`을 실행하지만 persisted lint report는 만들지
않습니다.

---

## 6. Vault validate + persisted lint — server는 계속 중지

초기화는 누락된 디렉터리와 `index.md`/`log.md`만 create-only로 만들고 기존 파일을
덮어쓰지 않습니다. validation은 모든 managed writer 디렉터리의 실제 create/remove와
기존 `log.md` append-open을 검사합니다.

```bash
python -c 'from wiki_config import resolve_wiki_paths, initialize_wiki_vault, validate_wiki_vault; paths = resolve_wiki_paths(); initialize_wiki_vault(paths); validate_wiki_vault(paths); print(paths.root)'
python wiki_lint.py --vault $WIKI_VAULT_PATH --log
```

`wiki_lint.py --log`를 명시한 두 번째 명령만 `lint_logs/YYYY-MM-DD.md` persisted report를
작성합니다. exit `1`이면 report의 `gap`, `low_confidence`, `stale_episode`를 검토하고,
경로 오류인 exit `2`와 구분합니다.

---

## 7. Server/queue writer 시작

마이그레이션·bootstrap·validation/lint가 끝난 뒤에만 production writer를 시작합니다.

```bash
nohup uvicorn agent_server:app --host 0.0.0.0 --port 8001 \
  --log-level info >> /var/log/yield-agent/agent.log 2>&1 &
```

로그에서 `Wiki Vault ready: ...`, wiki queue 시작, `Application startup complete`를 모두
확인합니다. startup validation이 실패하면 workers는 시작되지 않습니다.

---

## 8. 배포 후 검증

### 8-1. Vault와 검색

```bash
find "$WIKI_VAULT_PATH/concepts" -type f -name '*.md' | wc -l
find "$WIKI_VAULT_PATH/episodes" -type f -name '*.md' | wc -l
head -30 "$WIKI_VAULT_PATH/concepts/4SS_PRE_METAL_CLN_EASY.md"
```

Concept frontmatter의 `confidence`와 `citations`, 본문을 확인합니다. Bootstrap 자체는
Episode를 만들지 않으므로 `episodes/` 증가는 질문 기반 검색/queue 경로에서만 기대합니다.

warm-up한 triple의 검색에서는 wiki-first/assisted 여부, citation link, confidence badge를
실제 응답과 server log에서 확인합니다. warm-up하지 않은 triple은 OpenSearch baseline
경로로 검색되고 질문 기반 queue가 Episode를 누적할 수 있어야 합니다. latency는 고정
수치로 가정하지 말고 배포 환경의 기존 SLO와 비교합니다.

### 8-2. API 확인

```bash
curl --fail 'http://127.0.0.1:8001/api/wiki/graph?view=product_tree&product=4SS&limit=100' \
  | jq -e 'any(.nodes[]; .key == "concept:4SS|PRE METAL CLN|EASY" and .attributes.has_wiki == true)'
```

---

## 9. 운영 모니터링과 롤백

Server가 실행 중일 때는 read-only 모니터링만 수행합니다.

```bash
du -sh "$WIKI_VAULT_PATH"
find "$WIKI_VAULT_PATH/concepts" -type f -name '*.md' | wc -l
python wiki_lint.py --vault "$WIKI_VAULT_PATH" --json
grep "WIKI-FIRST" /var/log/yield-agent/agent.log | wc -l
grep "enqueue_status" /var/log/yield-agent/agent.log | tail -50
```

persisted lint가 필요하면 maintenance window에 server/queue를 중지하고
`python wiki_lint.py --vault $WIKI_VAULT_PATH --log`를 실행한 뒤 server를 다시 시작합니다.
별도 CLI와 server가 동시에 Vault에 쓰도록 예약하지 않습니다.

빠른 검색 경로 차단은 `.env`의 `WIKI_FIRST_ENABLED=false`로 수행하고 server를 재시작합니다.
Vault rollback은 server를 중지한 뒤 이전 `WIKI_VAULT_PATH`를 복원하고, 그 경로에 대해
validation을 통과한 다음 재시작합니다. Migration source나 충돌 사본은 삭제하지 않습니다.

---

## 10. 트러블슈팅

| 증상 | 확인 | 대응 |
|---|---|---|
| startup이 `not writable`로 종료 | 오류에 표시된 managed directory 권한/ACL | 해당 mount 권한을 수정하고 validation 재실행 |
| startup이 `log is not appendable`로 종료 | `$WIKI_VAULT_PATH/log.md` ACL/소유권 | 파일을 덮어쓰지 말고 append 권한만 복구 |
| migration이 `symlink`로 종료 | source/target 파일 또는 경로 component | symlink를 따라가지 말고 실제 독립 Vault 경로와 regular file로 재준비 |
| migration이 `different target file`로 종료 | source/target checksum 차이 | 양쪽 snapshot을 유지하고 diff/승인 reconciliation 수행 |
| bootstrap 후 wiki-first 발동 안 함 | Concept 존재, confidence, bootstrap `save_fail` | `wiki_summarizer.synthesize_concept_from_docs`와 Vault log 확인 |
| `참고 자료`가 비어 있음 | 합성 결과 citation의 `doc_id`/source metadata | v2 migration으로 값을 발명하지 말고 raw docs와 합성 결과를 조사 후 해당 triple 재합성 |
| confidence가 0.0 | structured synthesis 결과 | `synthesize_concept_from_docs` prompt/schema와 LLM 응답 확인 |
| OpenSearch aggregation 실패 | 인증, index, keyword mapping | bootstrap 출력과 `fail_history_tools`의 동일 client 설정 확인 |
| bootstrap 응답 지연/중단 | 한 triple의 LLM 응답 지연 | server 중지 상태를 유지하고 `--top`, `--max-docs`를 줄여 `--skip-existing --no-lint`로 재실행 |
| `pyyaml` import 오류 | 의존성 미설치 | 승인된 패키지 절차로 `pyyaml` 설치 |

---

## 11. 현재 bootstrap/운영 계약 요약

| 파일 | 현재 역할 |
|---|---|
| `bootstrap_wiki_warmup.py` | foundations + OpenSearch aggregation → raw docs → triple당 직접 LLM 합성 1회 → Concept upsert |
| `wiki_lint.py` | read-only scan; `--log`를 명시할 때만 persisted report 작성 |
| `migrate_wiki_vault.py` | checksum 검증, no-clobber initial cutover; source/target symlink 거부 |
| `migrate_v2_to_v3.py` | 누락 frontmatter 필드만 보강; `--dry-run` 생략 시 적용 |
| `agent_server.py` | 모든 writer destination과 operation log 검증 후 queue 시작 |

기존 전체 변경 카탈로그는 `wiki-migration-checklist.md`를 참고합니다.

---

## M3. OpenSearch 증분 Wiki 동기화

최초 프로젝트 구축 또는 Vault 전체 복구에는 기존 bootstrap을 사용합니다. 평상시에는
`sync_wiki.py`가 OpenSearch의 source fingerprint를 manifest와 비교하고, 신규·변경
triple만 기존 LLM 합성기에 전달합니다. `--limit`은 전체 한도가 아니라 이번 실행의
batch 크기이므로 다음 실행은 남아 있는 다음 job을 이어서 처리합니다.

먼저 읽기 전용으로 현재 차이를 확인합니다. 이 모드는 MongoDB job/lock과 Vault를
변경하지 않습니다.

```bash
cd 08-YieldAgent
python sync_wiki.py --check
```

일상 동기화와 중단된 queue 재개 명령은 다음과 같습니다.

```bash
python sync_wiki.py --apply --limit 10
python sync_wiki.py --resume --limit 10
```

`--apply`는 scan 후 신규·변경 job을 추가하고 최대 10개를 처리합니다. `--resume`은
OpenSearch 전체 scan이나 신규 job 등록 없이 retry 가능한 기존 job만 처리합니다.
두 명령은 MongoDB의 전역 lease를 사용하므로 동시에 시작된 두 Cron 실행 중 하나만
Vault writer가 됩니다. bootstrap과 수동 migration에는 이 lease가 적용되지 않으므로
그 작업을 수행할 때는 Cron과 다른 Wiki writer를 먼저 중지해야 합니다.

M3는 Cron을 자동 설치하지 않습니다. 운영 경로와 Python 환경을 확인한 뒤 운영자가
다음 형식으로 등록합니다.

```bash
# crontab 예시 — 실제 절대 경로로 교체
*/10 * * * * cd /path/to/08-YieldAgent && /path/to/python sync_wiki.py --apply --limit 10 >> /path/to/logs/wiki-sync.log 2>&1
```

근거 문서 ID가 사라지면 시스템은 Concept를 삭제하거나 자동 재합성하지 않습니다.
Concept를 `stale`로 표시하고 `reviews/`에 `review_type: source_removal`,
`status: pending`인 Review를 한 번만 생성합니다. 운영자가 Review의 누락 ID와 원본
시스템 상태를 확인한 뒤 현재 근거로 재생성을 승인한 경우에만 exact bootstrap을
실행합니다.

```bash
python bootstrap_wiki_warmup.py --apply \
  --product 4SS \
  --fail-type EASY \
  --cause-oper "PRE METAL CLN" \
  --no-lint
```

`--product`, `--fail-type`, `--cause-oper`는 세 옵션을 모두 함께 제공해야 합니다.
bootstrap 성공 결과에도 동일한 source fingerprint가 기록되므로 이후 sync에서 같은
근거를 다시 합성하지 않습니다. Concept 저장 후 manifest 저장 전에 중단된 경우에는
다음 sync가 Concept frontmatter의 fingerprint를 확인하여 LLM을 다시 호출하지 않고
manifest와 MongoDB job 상태만 복구합니다.

---

## M7. 메타데이터 없는 보조 인덱스로 기존 Wiki 보강

`enrich_wiki.py`는 `syld_gpt_2067627`의 `page_content`와 4096차원
`qwen/qwen3-embedding-8b` 벡터를 사용해 이미 존재하는 Triple Concept를 보강합니다.
새 Triple을 추론하거나 Concept 본문과 `citations`를 다시 합성하지 않습니다.

각 Concept별로 벡터 검색 상위 5개를 가져오고, 미처리 후보 전체를 구조화 LLM 호출
한 번으로 판정합니다. 관련성이 검증된 후보만 `related_evidence`로 연결됩니다. 원본
OpenSearch 인덱스는 읽기 전용이며 판정 상태는
`$WIKI_VAULT_PATH/.yield-wiki/evidence-manifest.json`에 저장됩니다.

먼저 외부 호출과 Vault 쓰기가 없는 preview를 실행합니다.

```bash
uv run --frozen python enrich_wiki.py --check \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

전체 기존 Concept를 보강하는 한 줄 명령은 다음과 같습니다.

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm --vault /Users/daehwankim/SYLDAIX/YieldWiki
```

`--limit`을 생략하면 현재 존재하는 Triple Concept 전체를 처리합니다. 한 번의 실행 범위를
제한하려면 `--limit N`을 추가합니다.

`--allow-external-llm`은 bounded Concept 문맥과 검색된 후보 본문이 현재 설정된 embedding 및
chat provider로 전송됨을 명시적으로 승인하는 옵션입니다. 사내 승인 범위를 제한하려면
정확한 Triple 세 옵션을 함께 사용합니다.

```bash
uv run --frozen python enrich_wiki.py --apply --allow-external-llm \
  --vault /Users/daehwankim/SYLDAIX/YieldWiki \
  --product 4SS --fail-type EASY --cause-oper 'PRE METAL CLN'
```

두 번째 실행에서 Concept semantic hash, 후보 content hash, embedding model, judgment model이
모두 같으면 해당 후보의 LLM 판정은 생략되고 `skipped`로 집계됩니다. 후보 검색을 위한
Concept embedding은 현재 실행마다 한 번 호출됩니다. manifest 내용도 동일하면 안전
writer를 실행하지 않으므로 attempt/tombstone 파일을 추가하지 않습니다.

관련 후보가 승인되면 `sources/EVD-<hash>.md`가 생성되고 Concept의 Knowledge Links에
`Related Evidence`가 별도 표시됩니다. 이 Source는
`generated_by: yield-wiki-evidence-enricher` 소유이므로 기존 materializer가 덮어쓰거나
삭제하지 않습니다. 후보가 모두 무관하면 Source Markdown이나 Concept 링크를 억지로
생성하지 않습니다.

Cron은 다른 Vault writer와 겹치지 않는 maintenance window에 등록합니다.

```bash
# 운영 절대 경로와 승인된 환경 파일로 교체
15 * * * * cd /path/to/08-YieldAgent && /path/to/uv run --frozen python enrich_wiki.py --apply --allow-external-llm --vault /path/to/YieldWiki >> /path/to/logs/wiki-enrich.log 2>&1
```

안전 writer는 manifest가 실제로 바뀔 때 `.yield-wiki` 아래 attempt/tombstone 파일을 남길
수 있습니다. 이는 실패 복구 기록이며 임의 삭제하지 않습니다. 운영 보존 기간과 정리
정책은 별도로 정한 뒤 적용합니다.
