# 사내 운영 마이그레이션 — defect-history → fail-history (인덱스 + 필드 일괄 통일)

> **대상**: 사내 운영 OpenSearch (defect-history 인덱스, defect_type 필드 사용 중)
> **목표**: dev 환경에 적용된 fail-history / fail_type 명명을 운영에 적용
> **PoC 결과 보고서 별도 첨부**: `plans/plan-wondrous-toast-results.md` (PoC PASS, 회귀 0)
> **작성일**: 2026-05-10

---

## 0. 배경

dev 환경(github.com/dorothy90/map_agent.git)은 이미 `fail-history` 인덱스 + `fail_type` 필드로 통일됨 (commit `b77f676`). 사내 운영은 아직 `defect-history` + `defect_type`이라 코드 일관성/유지보수성 위해 **운영 데이터까지 마이그레이션** 필요. dev처럼 인덱스 삭제 후 재생성은 운영 데이터 손실 위험이 커서 **무중단 reindex API + alias 전환** 권고.

핵심 결정:
- 운영 데이터(`defect-history` 인덱스의 누적 raw 문서)는 **보존**
- 무중단 전환 위해 alias 사용
- 롤백 가능한 단계로 분리

---

## 0-A. 코드 배포 마이그레이션 (이번 PR이 운영에 미치는 영향)

데이터 마이그레이션과 별개로 **코드 변경 배포** 자체에 신중히 다뤄야 할 사항들:

### (1) 변경된 코드 표면

| 영역 | 변경 | 운영 영향 |
|---|---|---|
| **신규 파일 8개** | `wiki_*.py`, `wiki_router.py`, `wiki_lint.py`, `wiki/` 디렉토리, `pages/wiki_graph.py`, `eval/run_wiki_eval.py`, `eval/datasets/wiki_micro.json` | 신규 모듈, 기존 코드 미영향 |
| **수정 파일 6개** | `agent_server.py`(lifespan 4줄), `fail_history_agent.py`(state 반환 2필드), `fail_history_tools.py`(인덱스/필드 + wiki hook), `prompts.py`(FAIL_HISTORY 프롬프트에 1섹션 추가), `supervisor.py`(YieldQueryState 2필드 추가), `templates/fail_history_report.html`(헤더에 deep link 1줄), `eval/datasets/fail_history_goldset.json`(49건 키 변경) | **prompts.py와 supervisor.py state schema 변경**이 가장 영향 큰 표면 |
| **신규 의존성** | `python-frontmatter>=1.1`, `jsonschema>=4` (둘 다 transitive 없음, 무거운 의존성 없음) | `pip install` 또는 `uv sync` 운영 환경 적용 필요 |
| **신규 환경변수** | `WIKI_SUMMARIZE_MODEL` (default: `RETRIEVE_CHAIN_MODEL` 재사용), `STREAMLIT_BASE_URL` (default: `http://localhost:8501`) | .env 또는 systemd unit에 추가 |
| **신규 디스크 경로** | `08-YieldAgent/wiki/` markdown vault (컨테이너 영속 볼륨 필요) | 운영 컨테이너 spec 변경 |
| **신규 HTTP endpoint** | `GET /api/wiki/graph`, `GET /api/wiki/node/{id:path}` | 사내 방화벽/L7 ingress 라우팅 추가 |

### (2) State schema 마이그레이션 (LangGraph checkpoint)

`YieldQueryState`에 2 필드 추가됨:
```python
wiki_hit_ids: list[str]           # turn별 overwrite (reducer 없음)
wiki_update_status: str           # "queued"|"summarized"|"persisted"|"dropped"|"skipped"
```

**기존 thread_id**(MongoDB checkpoint)에 누적된 state는 두 필드가 없음 → LangGraph가 default(`None`/`""`)로 처리. **schema 충돌 없음**(reducer가 add가 아닌 overwrite). 단:
- 새 코드 첫 실행 시 기존 세션이 resume되면 state에 두 필드가 추가됨 → checkpoint 크기 미미하게 증가
- `wiki_hit_ids`/`wiki_update_status` 값을 의존하는 다운스트림 코드가 새 코드에만 있으니 기존 thread도 안전

### (3) 프롬프트 변경 영향 (LLM 응답 형식)

`prompts.py FAIL_HISTORY_SYSTEM_PROMPT_TEMPLATE`에 "## Wiki Memory 사용 규칙" 섹션 추가 (5줄). 기존 응답 형식(answer + `[SUGGESTION: ...]`)은 동일하지만:
- 운영 LLM(사내 로컬)이 추가된 섹션을 어떻게 해석하는지 **사전 검증 필수** (Step 3 staging smoke test)
- gpt-oss-120b 또는 dev 모델 = 운영 모델 기준이면 dev PoC 결과가 그대로 적용
- 다른 모델이면 `wiki_memory` 섹션 무시/오용 가능 → 응답 회귀 측정 필요

### (4) 코드 ↔ 데이터 순서 의존성

**중요한 조합**:
| 코드 상태 | 데이터 상태 | 결과 |
|---|---|---|
| 구버전 (defect_type) | defect-history 인덱스 | ✅ 정상 (현재 운영) |
| 신버전 (fail_type) | defect-history 인덱스 | ❌ **검색 결과 0** (필드명 불일치) |
| 구버전 (defect_type) | fail-history 인덱스 | ❌ **검색 결과 0** |
| 신버전 (fail_type) | fail-history 인덱스 | ✅ 정상 (목표) |

→ **반드시 alias 전환(Step 4)이 코드 환경변수 변경(Step 5)보다 먼저**. 또는 코드/환경변수 변경 직전에 alias 전환을 묶어서 atomic하게.

### (5) 코드 배포 절차 (Step 5 상세화)

```bash
# 5-0. 코드 가져오기 (사내 git mirror 가정)
cd /opt/map_agent
git fetch origin
git checkout main
git pull origin main   # b77f676 또는 0e9af9a 머지 확인

# 5-1. 의존성 설치 (uv 또는 pip)
cd /opt/map_agent/08-YieldAgent
uv sync   # 또는 pip install python-frontmatter jsonschema
# ※ Streamlit dev viewer 운영 안 띄우면 streamlit-agraph 불필요

# 5-2. 환경변수 추가 (.env 또는 systemd)
echo "OPENSEARCH_INDEX=fail-history" >> .env
echo "WIKI_SUMMARIZE_MODEL=${RETRIEVE_CHAIN_MODEL:-gpt-oss-120b}" >> .env
# STREAMLIT_BASE_URL은 운영 React URL 결정 후

# 5-3. 디렉토리 권한 (컨테이너면 volume mount)
mkdir -p /opt/map_agent/08-YieldAgent/wiki/{episodes,concepts,aliases,.cache}
chown -R appuser:appuser /opt/map_agent/08-YieldAgent/wiki

# 5-4. agent_server 재기동 (메모 §uvicorn 정책: --reload 없이 PID kill + 재기동)
PID=$(lsof -nP -iTCP:8001 -sTCP:LISTEN | awk 'NR==2{print $2}')
kill -INT $PID
sleep 5
cd /opt/map_agent/08-YieldAgent
nohup python -m uvicorn agent_server:app --port 8001 --log-level info > /var/log/agent_server.log 2>&1 &

# 5-5. health + lifespan 로그 확인
curl -s http://localhost:8001/health
grep "wiki_queue.*started\|MongoDB 체크포인터" /var/log/agent_server.log | tail -2
```

### (6) 신규 endpoint 노출 정책

`/api/wiki/graph`, `/api/wiki/node/{id:path}`는 **인증 없는 read-only**. 운영 정책에 따라:
- 사내망 only면 그대로 노출
- 외부 노출은 nginx/L7 ingress에 path 기반 차단 또는 별도 reverse proxy 추가
- vault 본문에 사내 코드명/제품명 포함 → 외부 접근 시 정보 노출 위험 (PoC 메모: dev=OpenRouter 외부, 운영=사내 로컬 LLM이라 redaction 미적용)

### (7) Vault 디렉토리 운영 정책

| 항목 | 권고 |
|---|---|
| 위치 | `/opt/map_agent/08-YieldAgent/wiki/` (또는 별도 영속 볼륨) |
| 백업 | `wiki/` 전체 + `.cache/` 제외. 일 1회 정도 (PoC dev에선 노드 누적 100~200 스케일) |
| 모니터링 | node 1000 초과 시 `wiki_queue` WARN. wiki_lint는 주 1회 cron |
| 권한 | agent_server 프로세스만 write. read는 모니터링 도구도 OK |
| 컨테이너 환경 | volume mount, `--restart` 정책에서도 vault 영속 보장 |

### (8) 코드 롤백 절차 (Step 5 롤백 보강)

데이터 롤백(§3)은 환경변수 원복으로 즉시 가능. 코드 롤백은 추가:

```bash
# R-A. 이전 commit으로 revert
cd /opt/map_agent
git log --oneline -5   # 이전 commit hash 확인 (b77f676 이전)
git checkout <prev_hash>

# R-B. 의존성 원복 불필요 (python-frontmatter/jsonschema는 새 모듈에만 사용,
#       구버전 코드가 이 패키지를 import 안 하므로 설치된 채 둬도 무해)

# R-C. 환경변수 원복
sed -i '/^OPENSEARCH_INDEX=fail-history/d' .env
echo "OPENSEARCH_INDEX=defect-history" >> .env

# R-D. agent_server 재기동
PID=$(lsof -nP -iTCP:8001 -sTCP:LISTEN | awk 'NR==2{print $2}')
kill -INT $PID
# ... 재기동
```

**롤백 시간**: ~10분 (코드 checkout + 재기동). vault 디렉토리는 그대로 두고, 다음 시도 시 재사용.

---

## 1. 사전 점검 (배포 D-3)

| 항목 | 명령 | 합격 기준 |
|---|---|---|
| 운영 인덱스 doc 수 | `GET /defect-history/_count` | 예상치(수십 만~) 일치 |
| 인덱스 mapping | `GET /defect-history/_mapping` | `defect_type` 필드 매핑 (text + .keyword) 확인 |
| disk 여유 공간 | `GET /_cat/allocation` | 인덱스 크기의 **2배 이상 여유** (reindex 임시 복제) |
| 클러스터 health | `GET /_cluster/health` | green (yellow는 단일노드 OK) |
| 기존 alias | `GET /_cat/aliases` | `fail-history` alias 미존재 확인 |
| agent_server 운영 환경변수 | `OPENSEARCH_INDEX` 값 | 빈 값(default `defect-history`) 또는 명시 `defect-history` |
| dev 코드 PR 머지 | `b77f676` | 운영 배포 브랜치에 머지 |
| Langfuse trace 연결 | dev 환경 동작 확인 | 운영 LLM endpoint도 동작 확인 |

---

## 2. 마이그레이션 단계 (배포 당일)

### Step 1 — 새 인덱스 생성 (`fail-history`)

```bash
# 1-1. 기존 mapping 가져와서 fail_type으로 키만 변경한 mapping 생성
curl -s -k -u $OS_USER:$OS_PASS "https://$OS_HOST/defect-history/_mapping" \
  | jq '.["defect-history"].mappings | .properties.fail_type = .properties.defect_type | del(.properties.defect_type)' \
  > /tmp/fail-history-mapping.json

# 1-2. 새 인덱스 생성 (settings는 동일하게 복사)
curl -s -k -u $OS_USER:$OS_PASS "https://$OS_HOST/defect-history/_settings" \
  | jq '.["defect-history"].settings.index | {settings: {index: .}}' \
  > /tmp/fail-history-settings.json

curl -s -k -u $OS_USER:$OS_PASS -X PUT "https://$OS_HOST/fail-history" \
  -H 'Content-Type: application/json' \
  -d "$(jq -s '.[0] * {mappings: .[1]}' /tmp/fail-history-settings.json /tmp/fail-history-mapping.json)"
```

**검증**: `GET /fail-history/_mapping` → `fail_type` 필드 존재, `defect_type` 없음.

### Step 2 — Reindex (필드 변환 script 포함)

```bash
curl -s -k -u $OS_USER:$OS_PASS -X POST "https://$OS_HOST/_reindex?wait_for_completion=false" \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"index": "defect-history"},
    "dest": {"index": "fail-history"},
    "script": {
      "source": "if (ctx._source.defect_type != null) { ctx._source.fail_type = ctx._source.defect_type; ctx._source.remove(\"defect_type\"); }"
    }
  }'
# → task_id 반환

# 진행 상황 모니터링
curl -s -k -u $OS_USER:$OS_PASS "https://$OS_HOST/_tasks/<task_id>" | jq '.task.status'
```

**예상 시간**: 운영 데이터 양 × ~100 docs/sec (네트워크/디스크 의존). 100만 doc ≈ 3시간.

**검증**:
- `GET /fail-history/_count` ≡ `GET /defect-history/_count` (값 동일)
- 샘플 doc 비교: `GET /fail-history/_doc/<sample_id>` → `fail_type` 필드 존재, `defect_type` 없음

### Step 3 — 새 인덱스 검증 (Read-only smoke test)

```bash
# Step 3-1. 운영 환경변수 임시 override (테스트 인스턴스만)
export OPENSEARCH_INDEX=fail-history

# Step 3-2. 별도 staging agent_server 인스턴스 띄우기 (port 8002)
cd /opt/map_agent/08-YieldAgent
python -m uvicorn agent_server:app --port 8002 &
STAGING_PID=$!

# Step 3-3. 검증 쿼리 (대표 트리플 5개)
for q in "4SS STI CMP EASY" "6E2 ILD CMP FMAX" "5QQ METAL1 DEP VMIN"; do
  curl -s -X POST http://localhost:8002/chat/stream \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"smoke-$RANDOM\",\"query\":\"$q 불량이력\"}" \
    --max-time 60 | grep -c "fail_history_agent.*node_complete"
done

# Step 3-4. eval 회귀 검사
cd /opt/map_agent/08-YieldAgent
python -m eval.run_wiki_eval --bench main --limit 10 --save

# Step 3-5. staging 인스턴스 종료
kill -INT $STAGING_PID
```

**합격 기준**:
- 검색 latency 회귀 +30% 이내 (PoC dev 기준 +18%)
- recall@5 / MRR / must_mention_rate 회귀 0
- 응답 텍스트에 fail_type 값(예: `EASY(W)`) 정상 등장

### Step 4 — Alias 전환 (atomic)

```bash
# 운영 인덱스 이름을 가리키는 alias 추가 + 기존 인덱스에 alias도 추가 (롤백용)
curl -s -k -u $OS_USER:$OS_PASS -X POST "https://$OS_HOST/_aliases" \
  -H 'Content-Type: application/json' \
  -d '{
    "actions": [
      {"add": {"index": "fail-history", "alias": "fail-history-current"}},
      {"add": {"index": "defect-history", "alias": "defect-history-legacy"}}
    ]
  }'
```

이 단계까지는 **코드 변경 X**. 운영 agent_server는 여전히 `defect-history` 직접 조회.

### Step 5 — agent_server 환경변수 변경 + 재기동 (점진적)

```bash
# 5-1. 환경변수 업데이트 (.env 또는 systemd unit)
export OPENSEARCH_INDEX=fail-history

# 5-2. 운영 agent_server 재기동 (메모 §uvicorn 정책: --reload 없이 PID kill + 재기동)
PID=$(lsof -nP -iTCP:8001 -sTCP:LISTEN | awk 'NR==2{print $2}')
kill -INT $PID
sleep 5
cd /opt/map_agent/08-YieldAgent
python -m uvicorn agent_server:app --port 8001 --log-level info > /var/log/agent_server.log 2>&1 &

# 5-3. health check
curl -s http://localhost:8001/health
```

**검증**: 사용자 검색 시 응답 정상 + `[wiki_queue] started` 로그 + `MongoDB 체크포인터` 로그.

### Step 6 — 운영 모니터링 (D+1, D+7)

| 메트릭 | 측정 | 임계 |
|---|---|---|
| 검색 latency p50/p95 | 운영 모니터링 대시보드 | dev baseline +30% 이내 |
| 사용자 응답 에러율 | agent_server 로그 | 변화 없음 |
| wiki_queue drops | `/api/wiki/stats` (PoC 외 endpoint, 추가 시) 또는 로그 | ≤2% |
| Langfuse trace | wiki_summarize span 1쿼리당 1개 | 정상 |
| OpenSearch 클러스터 health | `GET /_cluster/health` | green/yellow 유지 |

### Step 7 — 기존 인덱스 정리 (D+30, 안정화 후)

```bash
# 안정화 확인 후 기존 defect-history 인덱스 삭제
curl -s -k -u $OS_USER:$OS_PASS -X DELETE "https://$OS_HOST/defect-history"

# defect-history-legacy alias도 자동 제거됨
```

**디스크 회수**: 인덱스 크기만큼 free.

---

## 3. 롤백 절차 (Step 5 이후 문제 발생 시)

```bash
# R-1. 환경변수 원복
export OPENSEARCH_INDEX=defect-history

# R-2. agent_server 재기동
PID=$(lsof -nP -iTCP:8001 -sTCP:LISTEN | awk 'NR==2{print $2}')
kill -INT $PID
sleep 5
cd /opt/map_agent/08-YieldAgent
python -m uvicorn agent_server:app --port 8001 ...

# R-3. fail-history 인덱스는 그대로 유지 (다음 시도 위해)
```

**롤백 시간**: <5분. defect-history 데이터 그대로라 무손실.

---

## 4. 영향도 매트릭스

| 컴포넌트 | 영향 | 대응 |
|---|---|---|
| OpenSearch defect-history 인덱스 | reindex 중 read-only 권장 (Step 2 동안 새 write 없으면 OK) | reindex 전 사용자 공지 또는 maintenance window |
| agent_server fail_history_agent | 환경변수만 변경, 코드 무수정 (이미 `OPENSEARCH_INDEX` env 지원) | Step 5 재기동 |
| LangGraph checkpoint (MongoDB) | 무영향 | — |
| 기존 wiki vault | 운영 환경엔 vault 0 (PoC 산출물은 dev 한정) | 운영에서 새로 누적 시작 |
| Streamlit/React 프론트 | 무영향 (deep link URL 동일) | — |
| ppt_export_agent / 다른 agent | 무영향 | — |

---

## 5. 위험 + 완화

| 위험 | 가능성 | 영향 | 완화 |
|---|---|---|---|
| reindex 중 새 write 누락 | 중 | 중 | maintenance window 또는 `wait_for_completion=false` + 마지막 delta sync |
| 클러스터 disk 부족 | 저 | 고 | Step 0 사전 점검 (인덱스 크기 2배 여유) |
| 새 인덱스 mapping 오류 (analyzer 누락 등) | 중 | 고 | Step 3 staging smoke test로 사전 차단 |
| agent_server 재기동 시 in-flight 요청 drop | 중 | 저 | lifespan finally drain (현재 코드 보장) + 트래픽 적은 시간대 |
| 사용자 PR 빈도 변화로 wiki_queue 부하 변화 | 저 | 저 | Step 6 모니터링으로 감지 |
| dev에서 발생한 NoneType 회귀 (wiki_summarizer) | 저 | 저 | 이미 fix 적용 (PoC Day 7), 추가 발생 시 monitoring |

---

## 6. 일정 (총 1주 권고)

| 일자 | 작업 | 담당 |
|---|---|---|
| D-3 | 사전 점검 + dev 코드 PR 머지 + 운영 disk/health 확인 | 담당자 |
| D-1 | 사용자 maintenance 공지 (예정 다운타임 30분~3시간, reindex 시간) | 운영 |
| D | Step 1~4 (인덱스 생성 + reindex + alias) | 운영 |
| D | Step 5 (환경변수 + 재기동) | 담당자 |
| D+1 | Step 6 1일차 모니터링 | 담당자 |
| D+7 | Step 6 1주 안정화 확인 | 담당자 |
| D+30 | Step 7 (기존 인덱스 정리) | 담당자 |

---

## 7. PoC dev에서 검증된 것 (참고)

| 항목 | dev 결과 |
|---|---|
| 코드 변경 잔재 | 우리 영역 `defect` 0건 |
| eval recall@5 회귀 | 0 (1.000 → 1.000) |
| eval MRR 회귀 | 0 |
| latency 회귀 | +18% (가드 30% 이내) |
| wiki_queue durable ack | 100% (11/11 persisted) |
| wiki_lint 위반 | 0 (PoC 데이터 reset 후) |
| Streamlit Playwright 회귀 | 통과 |

자세한 데이터: `plans/plan-wondrous-toast-results.md` (별도 PR 또는 별첨).

---

## 8. 후속 작업 (이 마이그레이션 외)

- React+Vite repo의 force-graph-2d 컴포넌트 구현 (deep link `/wiki_graph?focus=...` 받는 페이지)
- 운영 환경 wiki_queue stats endpoint 추가 (모니터링 대시보드 연동)
- 운영 30+쌍 어려운 케이스 goldset 라벨링 (현재 5쌍은 dev/PoC용)

---

## 9. 결정 필요 항목 (배포 전 확인)

- [ ] reindex를 maintenance window로 갈지 vs 무중단(write 일시 중단 전략)
- [ ] disk 여유 부족 시 sharding/relocate 계획
- [ ] D+30 기존 인덱스 정리 시점에 백업본을 별도 저장할지
- [ ] 운영 사용자에게 deep link URL이 `STREAMLIT_BASE_URL` (현재 dev) → 운영 React URL로 바뀌는 시점 공지
