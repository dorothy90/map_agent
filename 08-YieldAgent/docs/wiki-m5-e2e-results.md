# M5 Wiki Graph-Assisted RAG E2E Results

## 실행 정보

- 실행 시각: 2026-08-02 18:02–18:14 KST
- 검증 기준: `e56ba69bfb74` 및 Task 7에서 발견한 global-lock 수정
- OpenSearch: `http://127.0.0.1:9200`, index `fail-history`
- MongoDB: `mongodb://localhost:27017`, database `yield_agent`
- live Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- 외부 LLM destination: `openrouter.ai` (model은 저장소 밖 기본 설정)

토큰 값, 원문 문서, Vault 본문, OpenSearch 원문은 출력하거나 이 문서에 기록하지
않았다. 회사 데이터의 외부 전송 승인이 없으므로 live LLM 합성과 회사 문서 기반
Chat은 실행하지 않았다.

## 의존성 및 실제 서비스

| 검증 | 결과 | 실제 관찰 |
| --- | --- | --- |
| frozen dependency | PASS | `uv lock --check`: 268 packages resolved, exit 0 |
| OpenSearch health | PASS | HTTP 200, ping true, cluster status `yellow` |
| OpenSearch index | PASS | `fail-history` 존재, 505 documents |
| MongoDB | PASS | ping true, `wiki_sync_jobs=390`, `wiki_sync_locks=0` |
| 현재 실행 중인 8001 Backend | FAIL | public health 200이지만 다른 workspace의 구버전 프로세스여서 Plugin route HTTP 404 |
| 현재 worktree Backend | PASS | 격리 temp Vault와 임시 토큰으로 18001에서 실제 기동; public 200, no/wrong token 401, authenticated Plugin health 200 |
| Plugin dependency health | PASS | 현재 worktree 서버에서 `backend=ok`, `opensearch=ok`, `llm=configured` |

`llm=configured`는 키 존재만 확인하며 실제 provider 호출 성공을 뜻하지 않는다. 검증용
18001 Backend는 종료했고 생성한 temp Vault와 응답 파일은 휴지통으로 이동했다.

## live Vault 상태 및 projection preview

live Vault는 변경하지 않았다. M5 적용 전 상태는 다음과 같다.

| 검증 | 결과 | 실제 관찰 |
| --- | --- | --- |
| Concept / Source | PASS | Concepts 3, Sources 16 |
| target Concept | PASS | `4SS_PRE_METAL_CLN_EASY.md` 존재, Source IDs 7, fingerprint 존재 |
| structured graph | FAIL | target Concept의 `entities=0`, `relations=0` |
| managed graph paths | FAIL | `entities/`, `relations/` 디렉터리가 없음 |
| Vault validation | FAIL | `entities` managed directory가 없어 `WikiConfigurationError` |
| materializer check 1 | PARTIAL | errors 0, warnings 0, `index.md`/`overview.md` 2개 수정 필요 |
| materializer check 2 | PARTIAL | 동일하게 modified 2; 적용하지 않았으므로 zero-change가 아님 |

active Relation은 0건이다. 따라서 live Relation endpoint와 Source referential check는
검증할 대상이 없었다. 승인 없이 live Vault를 초기화하거나 변경하지 않았다.

## live LLM synthesis, Agent Chat, Citation

결과: **BLOCKED**

요구된 bootstrap 명령은 실제 OpenSearch 회사 문서를 `openrouter.ai`로 전송한다. 회사
데이터와 목적지에 대한 명시적 승인이 없으므로 실행하지 않았다. 이에 따라 다음 항목은
완료로 판정하지 않는다.

- exact `4SS + EASY + PRE METAL CLN` Concept의 real LLM 재합성
- live Entity/Relation 생성과 Concept fingerprint 갱신
- active Relation을 사용한 authenticated Plugin Chat
- Agent 응답의 real Source citation 및 MongoDB session persistence
- Obsidian Desktop에서 Relation/Source/Citation 열기와 reload 복원

비민감 Plugin 인증과 Backend 경계는 위의 temp Vault health E2E로 별도 확인했다. 그러나
이는 real LLM 답변이나 Citation 성공을 대신하지 않는다.

## 격리 incremental 및 fallback E2E

회사 데이터가 외부로 나가지 않는 로컬 격리 시나리오로 다음을 실제 실행했다.

- real OpenSearch exact triple 7 documents를 읽고 live Source note 7개와 target Concept만
  temp Vault로 복사했다.
- UUID가 붙은 실제 MongoDB jobs/locks collection과 production `WikiSyncService`,
  `WikiJobStore`, `wiki_store`, `materialize_wiki`를 사용했다.
- local-only deterministic synthesis로 첫 sync에서 active Relations 2개를 생성했다.
- 동일 Source ID 집합의 controlled content fingerprint를 변경하고 다시 sync하여 active
  Relation 1개, stale Relation 1개를 확인했다.
- 같은 snapshot을 세 번째로 실행해 `unchanged=1`, 총 synthesis calls 2, Relation file
  bytes unchanged를 확인했다. 즉 세 번째 실행의 synthesis call은 0회였다.
- Graph projection load를 의도적으로 실패시키고 외부 embedding 호출 없이 실제 local
  OpenSearch BM25를 실행했다. 결과는 `retrieval_mode=baseline`, 3 results였고
  `graph_context`는 반환되지 않았다.
- 격리 MongoDB collections는 종료 시 삭제했고 temp Vault는 휴지통으로 이동했다.

## E2E에서 발견하고 수정한 결함

첫 격리 sync는 `Wiki sync global lock was lost`로 실패했다. 원인은 lock acquire와 renew가
같은 millisecond에 실행될 때 MongoDB update가 owner를 정상 매치해도 값이 같아
`modified_count=0`을 반환하는데, 코드가 이를 소유권 상실로 해석한 것이었다.

실제 격리 MongoDB 회귀 테스트를 먼저 추가해 실패를 확인한 뒤
`renew_global_lock()`만 `matched_count == 1`로 판정하도록 수정했다. 같은 owner의 no-op
renewal은 성공하고 다른 owner는 계속 실패한다. 수정 후 job-store suite는 `8 passed`,
위 incremental E2E도 완료됐다.

최종 검증에서 `uv lock --check`와 전체 Wiki suite `250 passed`를 다시 확인했다. 기존
LangGraph deprecated API 경고 2건은 남아 있지만 실패는 없었다.

## 최종 판정

M5의 local 서비스, 인증, incremental stale/idempotency, actual MongoDB lock, Graph 장애 시
actual OpenSearch fallback은 검증됐다. 전체 real M5 E2E는 완료하지 않는다. live Vault가
아직 pre-M5 구조이고, 외부 OpenRouter로 회사 데이터를 보내는 live synthesis/Chat이
명시적 승인 전 **BLOCKED**이기 때문이다. Obsidian Desktop GUI도 이번 Task에서는
조작하지 않았으며 live Relation과 Citation이 생성된 뒤 수동 검증이 필요하다.
