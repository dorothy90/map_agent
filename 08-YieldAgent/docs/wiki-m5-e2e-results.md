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

## 승인 후 live 무료 모델 검증

- 실행 시각: 2026-08-02 21:12–21:22 KST
- 검증 기준: `0aeaf4b` + 모델 선택 환경변수 수정
- 사용자 승인: `4SS / EASY / PRE METAL CLN` 회사 문서를 외부 OpenRouter 무료
  모델로 전송하는 것을 명시적으로 승인
- 합성 모델: `nvidia/nemotron-3-super-120b-a12b:free`
- Planner 모델: `google/gemma-4-26b-a4b-it:free`

승인 직후 live Vault 전체를 다음 경로로 복제했다.

```text
/Users/daehwankim/SYLDAIX/YieldWiki.backup-20260802-m5-pre-free-llm
```

백업 직후 원본과 백업의 상대경로+파일 SHA-256 tree hash는 모두
`ea48b11b574d8c2145621a736cd9145b42abcd3477a76cbbb155cc9ed5cae9ce`로
같았다.

### 무료 모델 선정과 모델 설정 수정

OpenRouter models API에서 무료 모델의 구조화 출력 지원을 확인했다. 비민감 schema
probe는 Gemma에서 성공했지만 실제 `ConceptSynthesis` tool schema는 provider가 numeric
`maximum` 제약을 거부했다. 동일한 실제 schema와 synthetic 문서를 사용한 probe에서
Nemotron 무료 모델이 성공해 Wiki 합성 모델로 선택했다.

이 과정에서 `common.get_llm(model=...)`이 전달된 model과
`RETRIEVE_CHAIN_MODEL`을 무시하고 고정 모델을 사용하던 결함을 RED 테스트로 재현했다.
수정 후 명시적 model, 환경 기본 model, 기존 fallback 순서로 선택하며 관련 테스트
25개가 통과했다.

### live bootstrap 및 graph projection

exact bootstrap 결과:

```text
status=ok
documents=7
confidence=0.88
citations=7
synthesized_entities=8
synthesized_relations=12
```

구조 검증에서 endpoint Entity가 없는 Relation 7개는 warning과 함께 materialization에서
제외됐다. 최종 projection은 active Entity 8개, active Relation 5개이며 exact Concept의
one-hop 확장은 Relation 5개와 Source 5개를 반환했다. 모든 active Relation Source는
Concept의 authoritative Source ID 7개 안에 있다.

materializer `--check`를 연속 두 번 실행한 결과는 모두 다음과 같았다.

```text
created=0 modified=0 deleted=0 errors=0
```

두 실행 모두 동일한 invalid Relation warning 7개만 반환했으므로 파일 projection은
idempotent하다.

### authenticated Plugin Chat 및 session Citation

현재 worktree Backend를 실제 Vault, MongoDB, OpenSearch와 임시 Plugin token으로 18001에서
기동했다. public health와 authenticated Plugin health는 HTTP 200, 잘못된 token은 HTTP
401이었다.

첫 자연어 질문은 Planner가 기존 `relation_tree_agent`로 분류해 Wiki RAG 검증에서
제외했다. 요청을 `Fail History 검색 + Wiki 근거`로 명확히 표현한 두 번째 호출은 실제
`fail_history_agent`로 routing됐다.

```text
retrieval_mode=wiki-first
OpenSearch result rows=7
fail_history synthesis LLM calls=0
final answer chars=6211
SSE citations=7
stream_end=true
```

MongoDB session history에는 user 1개와 assistant 2개 turn이 저장됐고, 최종 assistant
turn의 canonical `sources/...` Citation 7개와 6,211자 답변이 재조회됐다. 임시 Backend는
검증 후 정상 종료했다.

### Obsidian Desktop

실제 `YieldWiki` Vault의 Graph view에서 새 `entities/`와 `relations/` 노드 및 연결이
표시되는 것을 확인했다. 생성 Entity 노트를 열어 `canonical_name`, `entity_type`,
`source_concept_ids`, `status: active`와 active Relation wikilink가 표시되는 것도 확인했다.
Plugin sidebar는 검증 시점에 임시 Backend가 종료된 상태여서 connection refused였으며,
이번 실행에서 Desktop Citation 버튼 클릭은 다시 수행하지 않았다. Citation의 API/SSE 및
MongoDB persistence는 위 authenticated E2E로 검증했다.

### 승인 후 최종 판정

외부 LLM 승인으로 기존 BLOCKED였던 live exact synthesis, Entity/Relation materialization,
authenticated Wiki RAG 답변 및 canonical Citation persistence는 **PASS**로 전환됐다.
Desktop Graph와 Entity 노트도 실제 확인했다. 남은 UI 항목은 운영 Backend를 실행한
상태에서 Plugin Citation 버튼을 클릭하는 수동 확인뿐이다.

승인 후 코드 변경까지 포함한 최종 회귀는 Wiki+memory `355 passed`, confirm-edit
`9 passed`, Obsidian Plugin `36 passed`와 production build 성공이다. `uv lock --check`,
`git diff --check`, 임시 Backend 종료도 확인했다.

## Readable label live migration 및 Desktop Citation 검증

- 실행 시각: 2026-08-02 23:13–23:18 KST
- 검증 기준: `628bb47`
- live Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- 보존 백업: `/Users/daehwankim/SYLDAIX/YieldWiki.backup-20260802-readable-labels`

live Vault write 전에 원본과 백업 각각의 정렬된 상대경로+파일 SHA-256 manifest를
생성했다. 두 manifest는 각각 61개 파일이며 `cmp`가 exit 0으로 일치했다. 두 manifest
파일 자체의 SHA-256도 모두
`a67a2be2390cd53540e8c5707b680a70e94a4369fefd1872634857b13284fadb`였다.
백업은 삭제하지 않고 보존했다.

실제 migration apply 결과는 `created=13 modified=2 deleted=13 warnings=7 errors=0`이었다.
이후 연속 두 번의 check는 모두
`created=0 modified=0 deleted=0 warnings=7 errors=0`으로 idempotent했다. 7개 warning은
기존 `4SS_PRE_METAL_CLN_EASY.md`의 누락 Relation endpoint warning과 동일하며 새로운
warning class는 없었다. 대표 readable 경로는 다음과 같다.

```text
entities/wafer queue time 초과--af3a0906.md
relations/wafer queue time 초과 causes EASY(W)--22c969f1.md
```

Obsidian Desktop Graph view에서 Entity/Relation 노드가 자연어 label과 8자리 hash로
표시되고 64자리 hash-only node가 active projection에서 사라진 것을 확인했다. 위 Entity
노트를 열어 full `entity:sha256:...` ID, `concept:4SS|PRE METAL CLN|EASY` 연결과 active
Relation link를 확인했다. 위 Relation 노트에서는 full `relation:sha256:...` ID,
subject/object full Entity ID, Concept link, `FH-9003-EXTRA` Source link가 유지됐다.

현재 worktree Backend를 `127.0.0.1:18001`에서 승인된 free model override와 임시 Plugin
token으로 실행했다. public health는 HTTP 200, 잘못된 token은 HTTP 401, 유효 token의
Plugin health는 HTTP 200이었다. Plugin 설정의 연결 테스트도 Desktop에서 `연결됨`을
표시했다.

승인된 exact `4SS / EASY / PRE METAL CLN` 질문을 Obsidian Plugin Chat에서 전송했다.
Planner는 `fail_history_agent`로 routing했고 `retrieval_mode=wiki-first`, 결과 7건,
fail-history synthesis LLM call 0회를 기록했다. Desktop은 정상 completion UI와
`1 steps · 19.1초`를 표시하고 Citation 버튼 7개를 렌더링했다. `FH-9001-EXTRA`
Citation을 클릭하자 canonical `sources/FH-9001-EXTRA.md`가 열렸으며, 표시된 frontmatter
`doc_id`가 `FH-9001-EXTRA`와 일치했다. 기존 Citation 구현이 그대로 통과해 Plugin 코드나
테스트는 변경하지 않았다.

검증 후 exact Backend PID를 종료했다. `127.0.0.1:18001`에 listener가 없음을 확인했고
임시 Plugin token/PID 파일을 제거했다. 토큰 값, 회사 문서 본문, Vault 본문은 이 문서에
기록하지 않았다.

최종 회귀는 Wiki+memory `365 passed`(기존 LangGraph deprecated API warning 2건),
confirm-edit `9 passed`, Obsidian Plugin `36 passed`였고 production build도 성공했다.
`uv lock --check`는 268 packages를 정상 확인했으며 `git diff --check`도 통과했다.

## Final review 수정 후 live 재검증

- 실행 시각: 2026-08-03 06:16–08:08 KST
- 검증 기준: `7943a62`
- live Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`

최종 privacy, safe mutation, legacy Concept fallback, Plugin refresh 수정 이후 전체 자동 회귀를
다시 실행했다. Wiki+memory `409 passed`(기존 LangGraph deprecated API warning 2건),
confirm-edit `9 passed`, Obsidian Plugin `45 passed`였고 production build도 성공했다.
샌드박스 내부 실행은 기존 `uv` cache와 `127.0.0.1` listen 권한 때문에 실패했으며,
동일 명령을 허용된 실제 환경에서 재실행한 위 결과를 기준으로 판정했다.

live Vault materializer check를 연속 두 번 실행한 결과는 모두 다음과 같았다.

```text
created=0 modified=0 deleted=0 warnings=7 errors=0
```

7개 warning은 기존 누락 Relation endpoint warning과 동일하다. active hash-only filename은
없었고 readable Entity 8개와 Relation 5개 모두 frontmatter에 full SHA-256 ID를 유지했다.

현재 worktree Backend를 실제 Vault, MongoDB, OpenSearch와 기존 Plugin 인증 설정으로
`127.0.0.1:18001`에서 실행했다. public health HTTP 200, authenticated Plugin health HTTP
200, 잘못된 token HTTP 401을 확인했다. Planner를
`google/gemma-4-26b-a4b-it:free`로 둔 첫 요청은 OpenRouter provider가 HTTP 500을 반환해
실패했다. 동일 코드와 입력에서 Planner만 구조화 출력이 검증된
`nvidia/nemotron-3-super-120b-a12b:free`로 바꾼 fallback 재실행은 정상 완료됐다. 이 결과는
무료 모델 기반 Backend/API fallback 검증이며, 요구된 Gemma Planner 구성의 PASS로
간주하지 않는다. Brief 7항의 exact query 문자열을 그대로 사용했으며 그 SHA-256은
`1f47ffb3d933fe0dd751b312857d0dec41571265f94df76ba3095ff5149173d3`이다.

```text
route=fail_history_agent
retrieval_mode=wiki-first
OpenSearch result rows=7
fail_history synthesis LLM calls=0
SSE citations=7
stream_end=true
```

7개 Citation은 모두 canonical `sources/...` 경로였고, 대표 Citation
`sources/FH-000238.md`를 Plugin Source API로 재조회해 HTTP 200, `doc_id=FH-000238`,
`type=source`가 일치하는 것을 확인했다. 임시 Backend는 정상 종료했으며 port 18001에
listener가 없음을 확인했다. 토큰 값과 회사 문서·답변 본문은 기록하지 않았다.

SSE에는 canonical Citation 7개가 포함됐지만, Citation **버튼 렌더링**은 Desktop에서만
확인할 수 있으므로 이번 API 결과로 검증됐다고 기록하지 않는다. 이번 재검증에서 Mac
Computer Use 계층은 Obsidian의 최초 app-state 조회에서 응답하지
않았다. 두 UI 전용 검증 에이전트와 controller 직접 호출이 같은 위치에서 멈춰 종료됐으므로,
**최종 수정 이후 Desktop Citation 버튼 클릭 재확인은 BLOCKED**로 기록한다. 이전
`628bb47` 기준 Desktop Citation 클릭 PASS 증거는 유지되지만, 현재 `7943a62`에서의 API,
SSE, canonical Source 해석 성공을 새로운 Desktop 클릭 성공으로 대체하지 않는다. 따라서
현재 HEAD의 요구된 Gemma 구성과 Desktop Citation-click을 포함한 post-fix live E2E 전체
판정은 **BLOCKED**다.

## M7 content-only index Wiki enrichment E2E

- 실행일: 2026-08-03 KST
- source index: `syld_gpt_2067627`
- live Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- 보존 백업: `/Users/daehwankim/SYLDAIX/YieldWiki.backup-20260803-content-enrichment`
- 승인된 외부 범위: `4SS / EASY / PRE METAL CLN` 한 Concept
- embedding model: `qwen/qwen3-embedding-8b`, 4096 dimensions
- judgment model: `nvidia/nemotron-3-super-120b-a12b:free`

백업 직후 `diff -qr`로 live Vault와 백업이 동일함을 확인했다. `--check`는 실제
OpenSearch mapping과 live Vault의 Concept 3개를 읽고 다음 결과를 반환했으며, 실행 후에도
백업과 차이가 없었다.

```text
status=checked
concepts=3
external model calls=0
Vault changes=0
```

exact selector check는 대상 Concept가 1개임을 확인했다. 승인된 범위의 최초 apply에서는
벡터 검색 후보 5개를 구조화 LLM 호출 한 번으로 판정했다.

```text
status=completed
concepts=1
candidates=5
evaluated=5
accepted=0
rejected=5
attached=0
materialized=false
errors=0
```

실제 보조 인덱스 후보들은 해당 Triple과 근거 관계가 성립하지 않아 모두 거절됐다.
따라서 Source Markdown과 Concept `Related Evidence`를 만들지 않았으며, 임계값을 낮추거나
관계를 발명하지 않았다. 같은 명령의 두 번째 실행은 다음과 같이 judgment LLM을 호출하지
않았다. 멱등성 보강 후 재실행 전후 `.yield-wiki` 전체 파일의 SHA-256 목록도 동일했다.

```text
status=completed
candidates=5
evaluated=0
skipped=5
attached=0
errors=0
```

원본 인덱스는 실행 후에도 12건이며 mapping SHA-256은
`cdb7d5b0cfaa6f4ac584d58c1df1bd96959bef42b6cf30c31dfeef72f8d271af`였다.
live Vault와 백업의 차이는 `.yield-wiki/evidence-manifest.json` 및 safe mutation의
attempt/tombstone 기록뿐이며 Markdown 차이는 없다.

materializer check는 `created=0 modified=0 deleted=0 errors=0`이고 기존 누락 Relation
endpoint warning 7개만 유지됐다. Wiki lint는 기존 high-priority foundation gap 2개
(`4SS|STI CMP|EASY`, `4SS|M0C ETCH|TWT`)만 보고했다. 이번 기능으로 추가된 lint나
materializer 오류는 없다. Obsidian Plugin은 `45 passed` 및 production build 성공이다.
Wiki 전체 자동화 테스트는 `424 passed, 10 failed`였고, 10건은 변경 전에도 동일하게
발생한 system Python 3.13과 기존 statsmodels/pandas 조합의 호환성 실패다. 이번 기능의
집중 테스트는 `26 passed`였다.

실제 관련 Source가 없어 이번 E2E에서 새 Graph edge는 생성되지 않았다. 자동화 테스트에서
승인된 related evidence의 Concept→Source link, enrichment owner 보존, owner collision 거부,
본문/citation 보존을 검증했다. 실제 Graph edge의 Desktop 확인은 관련 문서가 인덱스에
추가된 뒤 동일 명령에서 `accepted > 0`이 발생할 때 수행한다.
