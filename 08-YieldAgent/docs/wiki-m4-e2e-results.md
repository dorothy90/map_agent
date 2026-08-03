# M4 Obsidian Plugin E2E Results

## 실행 정보

- 실행 시각: 2026-08-01 21:00–2026-08-02 01:16 KST
- 검증 기준: `ac2a9fb93ee1514274a3ab64315028cbb986bff8`
- Obsidian Desktop: `1.9.14` (`/Applications/Obsidian.app`)
- Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- Backend: `http://127.0.0.1:8001`
- OpenSearch: `http://127.0.0.1:9200`, index `fail-history`, 505 documents
- 실제 질의: `oxide`, `product=4SS`, `fail_type=EASY`, `cause_oper=PRE METAL CLN`
- 대조 질의: `세정`, 동일 필터
- Chat 세션: `task8-obsidian-e2e-20260801`, `task8-obsidian-free-20260801`, `task8-provider-error-final-20260801`
- Review: `review:operator_feedback:9634d6b6f4424c96a9ccb9adb816a115`

로컬 인증 토큰은 Backend 프로세스 환경에서만 임시로 사용했다. 토큰 값과 기존 `.env` 값은 추적 파일에 기록하지 않았다.

## 자동 회귀

| 검증 | 결과 | 증거 |
| --- | --- | --- |
| Wiki suite | PASS | `177 passed in 7.18s` |
| Confirm edit/user memory | PASS | `18 passed in 1.22s` |
| Plugin tests | PASS | `36 passed` (Vitest) |
| Plugin production build | PASS | TypeScript 검사 및 esbuild, `main.js` 28.8 kB |

Python 테스트는 시스템 Anaconda가 아니라 저장소의 `uv run --frozen --with pytest`로 수행했다. Task 8 review에서 발견된 production dependency 결함을 수정해 `opensearch-py>=2.8.0`을 `pyproject.toml`과 `requirements.txt`에 선언하고 `uv.lock`을 갱신했다. 수정 후 추가 package override 없이 `uv run --frozen`으로 Backend를 기동했으며, `opensearchpy` import, Plugin health, 실제 Search가 모두 성공했다. `pytest`는 production dependency가 아니므로 테스트 실행에서만 `--with pytest`를 사용했다.

## 실제 Backend 및 API

| 시나리오 | 결과 | 실제 관찰 |
| --- | --- | --- |
| Public health | PASS | HTTP 200 |
| 올바른 Plugin 인증 | PASS | Plugin health HTTP 200 |
| 잘못된 Plugin 인증 | PASS | HTTP 401 |
| Plugin dependency health | PASS | `backend=ok`, `opensearch=ok`, `llm=configured` |
| `oxide` Search | PARTIAL | HTTP 200, `bm25_fallback`, 0 results |
| `세정` 대조 Search | PASS | HTTP 200, `bm25_fallback`, materialized Concept 1건 및 Source evidence 3건 |
| Related | PASS | Concept에서 Operation, Source 7건, Super Concept outgoing 및 backlinks 반환 |
| Source | PASS | `sources/FH-000238.md`의 실제 metadata 반환 |
| Review | PASS | pending v1 생성 후 approved v2, reload 및 append-only history 1건 확인 |
| Session 저장/복원 API | PASS | 두 Chat 세션이 MongoDB 목록/이력에서 다시 조회됨 |

`oxide`는 실제 임베딩 호출이 실패하여 BM25로 정직하게 강등되었고, 동일 필터의 7개 실제 OpenSearch 문서에는 영문 `oxide`가 없어 0건이었다. 따라서 요구한 `oxide` 질의로 Concept를 여는 시나리오는 완료하지 못했다. 대조 질의 `세정`은 다음 결과를 반환했다.

- Concept ID: `concept:4SS|PRE METAL CLN|EASY`
- Concept path: `concepts/4SS_PRE_METAL_CLN_EASY.md`
- Source IDs: `FH-9001-EXTRA`, `FH-000243`, `FH-000238`
- retrieval mode: `bm25_fallback`

검색 호출 전후로 Wiki sync/materialize 작업은 실행하지 않았다. 검색 자체가 새 Wiki 파일이나 sync job을 만들지 않았고, 외부 Vault에 추가된 파일은 명시적인 Review E2E가 만든 Review Markdown 한 건뿐이다.

## 실제 LLM 및 Chat SSE

결과: **외부 LLM quota로 답변 생성 FAIL, 오류 전달 PASS**

초기 구현에서는 HTTP 200 SSE 연결에서 `stream_start`, 여러 `token`, 일반 `message`, `stream_end`가 반환됐지만 Backend 로그의 실제 planner 호출은 OpenRouter HTTP 402 `Insufficient credits`로 실패했다. 첫 시도는 기존 모델 설정, 두 번째 시도는 추적 파일이나 `.env`를 변경하지 않고 프로세스 환경에서 `openai/gpt-oss-20b:free`로 override했다. 무료 모델 재시도도 동일한 402였다.

Task 8 review에서 provider invocation 예외가 JSON parse fallback에 함께 잡히던 문제를 수정했다. 특정 상태코드나 오류 문구 조건은 추가하지 않았다. 모든 model invocation 예외는 상위로 전파하고 JSON parsing 실패만 자연어 fallback으로 처리한다. 수정 코드로 실제 무료 모델 402를 다시 호출한 결과는 다음과 같다.

- HTTP: 200 SSE
- event types: `stream_start`, `error`
- structured `error`: 1건
- `stream_end`: 0건

따라서 Plugin은 일반 성공 응답으로 오인하지 않고 기존 오류/재시도 UI 상태로 전환할 수 있다. 외부 quota가 해결되지 않았으므로 다음 항목 자체는 여전히 성공으로 판정하지 않았다.

- 현재 Concept 내용에 근거한 원인/조치 답변
- 실제 검색 결과에서 만든 structured Citation
- Citation으로 `sources/FH-000238.md` 열기

초기 두 세션은 MongoDB에 저장되었지만 Citation 수는 0이다. 오류로 종료된 최종 provider-error 세션은 성공 턴으로 저장되지 않았다. Plugin health의 `llm=configured`는 키 존재만 뜻하며 실제 호출 가능 여부를 보장하지 않는다는 점도 확인했다.

## 실제 Vault 설치

결과: **PASS**

`npm run install:vault -- --vault /Users/daehwankim/SYLDAIX/YieldWiki`를 실행해 다음 세 파일만 설치했다.

- `.obsidian/plugins/yield-wiki/main.js`
- `.obsidian/plugins/yield-wiki/manifest.json`
- `.obsidian/plugins/yield-wiki/styles.css`

설치 전후 모두 `data.json`은 존재하지 않았다. 따라서 보존할 기존 checksum은 없었고, 설치기가 `data.json`을 새로 만들거나 덮어쓰지 않은 것은 확인했다. 이후 실제 Plugin 설정에서 서버 URL과 로컬 토큰을 저장하자 `data.json`이 생성됐으며, 추적 파일에는 포함되지 않았다.

## Obsidian Desktop UI

결과: **PARTIAL (주요 Desktop UI PASS, 실제 LLM 답변/Citation 미완료)**

초기에는 접근 가능한 창이 없어 `cgWindowNotFound (-10005)`가 발생했지만, 사용자가 `YieldWiki` Vault 창을 연 뒤 Computer Use로 다음 실제 Desktop 흐름을 완료했다.

- Restricted Mode를 해제하고 로컬 `Yield Wiki` Plugin `0.1.0`을 활성화했다.
- Plugin 설정에 `http://localhost:8001`과 로컬 토큰을 저장하고 `연결됨`을 확인했다.
- `세정` + `4SS / EASY / PRE METAL CLN` 검색에서 `bm25_fallback` 안내, Concept 1건, Source 3건을 확인했다.
- Concept 결과를 눌러 `concepts/4SS_PRE_METAL_CLN_EASY.md`를 열고 10 backlinks를 확인했다.
- `FH-000238`의 `Source 노트` 버튼을 눌러 `sources/FH-000238.md`와 `Cited By` backlink를 열었다.
- 앱을 reload한 뒤 토큰, `연결됨` 상태, 이전 Chat 세션이 복원되는 것을 확인했다.
- 현재 Markdown 노트 전송을 끈 비민감 질문 `연결 테스트`로 실제 402를 재현했고, 성공 답변 대신 오류 내용과 `다시 시도` 버튼이 표시됐다.
- 테스트 pending Review `review:operator_feedback:6a926e4c4c41482ab39bea9dfb5e6927`을 UI에서 승인했다. Markdown은 `approved`, version 2, append-only history 1건으로 갱신됐다.

외부 LLM quota가 남아 있지 않아 현재 노트 기반의 실제 답변, structured Citation 생성과 Citation 버튼 클릭은 여전히 미완료다. 회사 문서 내용을 불필요하게 외부 provider로 재전송하지 않도록 402 UI 검증에서는 현재 노트 전송을 껐다. `oxide` 질의의 Concept open도 embedding dependency가 정상화된 뒤 다시 확인해야 한다.

## 장애 경로

| 장애 | 결과 | 실제 관찰 |
| --- | --- | --- |
| 잘못된 토큰 | PASS | HTTP 401 |
| Backend 중지 | PASS (API 수준) | 종료 후 8001 연결 거부 |
| OpenSearch 중지 | PASS (API 수준) | health는 `opensearch=unavailable`, Search HTTP 502 |
| OpenSearch 복구 | PASS | `opensearch-node1` 재시작 후 12초 내 HTTP 200 |
| Embedding 장애 | PASS (API 수준) | Search가 `bm25_fallback`으로 명시적으로 강등 |
| LLM 장애 | PASS (API+UI) | 실제 402가 structured SSE `error`로 반환되고 `stream_end` 없이 Plugin에 오류와 `다시 시도`가 표시됨 |

OpenSearch 컨테이너는 검증 후 다시 시작해 최종 HTTP 200을 확인했다. 임시 Backend 프로세스는 검증 후 정상 종료했다.

## 최종 판정

M4의 자동 회귀, clean frozen Backend의 OpenSearch dependency, 실제 Backend 인증, OpenSearch BM25 검색, Concept/Source 탐색, Related API, Review 저장과 Desktop 승인, MongoDB 세션 및 Desktop 복원, 실제 Vault artifact 설치와 LLM provider 오류 UI는 검증됐다. 전체 E2E 완료 판정은 하지 않는다. 실제 LLM 답변과 structured Citation 생성은 quota로 실패했고, 지정 `oxide` 질의는 embedding 실패 후 BM25에서 0건이었기 때문이다.
