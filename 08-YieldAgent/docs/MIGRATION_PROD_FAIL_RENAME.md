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
