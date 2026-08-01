# Yield Wiki Obsidian Plugin 설계

- 작성일: 2026-08-01
- 대상 브랜치: `feat/obsidian-wiki-platform`
- 대상 시스템: `08-YieldAgent`의 `fail_history_agent` Wiki
- 대상 Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- Milestone: M4 — Obsidian Plugin

## 1. 목적

M2와 M3에서 생성·갱신되는 Obsidian Markdown Wiki를 사용자가 Obsidian 안에서 검색하고, 질문하고, 근거를 확인하고, 검토할 수 있게 한다.

Plugin은 새로운 Knowledge Base를 만들지 않는다. 기존 Wiki Store, OpenSearch, Agent SSE, Obsidian wikilink graph를 얇은 UI로 연결한다. 기존 frontend는 제거하거나 변경하지 않는다.

## 2. 범위

### 포함

- Obsidian 오른쪽 sidebar의 `Yield Wiki` view
- Chat, Search, Review 탭
- 현재 열린 노트를 선택적 Chat context로 전달
- 기존 OpenSearch를 사용한 검색과 Concept 중심 결과 그룹화
- 현재 노트의 관련 문서와 backlink 표시
- Agent SSE 답변, Citation, interrupt 표시
- Source Markdown과 원본 PPT 링크 열기
- Review 생성, 조회, 승인, 반려, 이력 표시
- Plugin 전용 Bearer 인증 API
- Plugin source와 실제 Vault 설치 artifact 분리
- 실제 Backend, OpenSearch, LLM, Obsidian을 연결한 end-to-end 검증

### 제외

- Plugin 내부 embedding, vector store 또는 `kb.json`
- 검색 시점의 Wiki 합성 또는 Markdown 갱신
- Plugin에서 Concept·Source Markdown 직접 수정
- 기존 `yield_frontend`, `wiki_frontend`, `repl_agent/frontend`, Streamlit UI 수정 또는 제거
- OpenSearch mapping과 기존 embedding 데이터 변경
- Neo4j 또는 별도 Graph DB 도입
- M3의 예약 실행 또는 전체 pending job 처리
- 승인된 Review 내용을 Concept 본문에 자동 병합하는 기능

## 3. 설계 원칙

1. Plugin은 표시와 사용자 입력만 담당하는 thin client다.
2. 검색·Chat·Review의 권한과 데이터 검증은 Backend가 담당한다.
3. Wiki 생성과 갱신은 계속 `bootstrap_wiki`와 `sync_wiki`가 담당한다.
4. 기존 Agent와 SSE event contract를 재사용하며 두 번째 Chat Agent를 만들지 않는다.
5. Concept와 Source는 기존 Wiki의 single-writer 경계를 통하지 않고 수정하지 않는다.
6. Plugin은 Vault 상대 경로만 받고, Backend는 경로가 Vault 내부인지 검증한다.
7. 검색 결과가 Wiki를 암묵적으로 변경하지 않도록 read와 write를 분리한다.
8. Plugin 장애가 기존 API와 frontend의 동작을 바꾸지 않아야 한다.

## 4. 사용자 인터페이스

Plugin은 Obsidian 오른쪽 sidebar에 하나의 `Yield Wiki` view를 등록한다.

### 4.1 공통 영역

- Chat, Search, Review 탭
- Backend 연결 상태
- 설정 화면을 여는 버튼
- 요청 진행, 실패, 재시도 상태

### 4.2 Chat 탭

- 질문 입력과 대화 이력
- 현재 열린 노트를 context에 포함할지 선택
- streaming 답변과 진행 상태
- Citation 목록
- Agent interrupt가 발생했을 때 승인·입력 UI
- Citation 클릭 시 Source Markdown 또는 원본 파일 열기

### 4.3 Search 탭

- 자연어 검색어
- 선택적 `product`, `fail_type`, `cause_oper` filter
- Concept 중심으로 묶인 검색 결과
- 근거 문장, Source, retrieval mode 표시
- Concept가 아직 없는 결과는 `원본 문서만 존재` 상태로 표시
- 결과 클릭 시 해당 Vault 노트 열기

### 4.4 Review 탭

- `pending`, `approved`, `rejected`, `resolved` 상태별 목록
- 검토 대상 Concept와 근거 Source 열기
- 검토자와 의견 입력
- 승인·반려 동작
- 상태 변경 이력과 동시 수정 충돌 표시

## 5. Source와 설치 구조

Plugin source는 repository에서 관리한다.

```text
obsidian/plugin/
├── manifest.json
├── package.json
├── tsconfig.json
├── esbuild.config.mjs
├── src/
│   ├── main.ts
│   ├── api.ts
│   ├── settings.ts
│   └── views/
└── styles.css
```

빌드 결과만 실제 Vault에 설치한다.

```text
/Users/daehwankim/SYLDAIX/YieldWiki/.obsidian/plugins/yield-wiki/
├── main.js
├── manifest.json
├── styles.css
└── data.json
```

`data.json`은 Obsidian이 관리하는 로컬 설정이며 repository에 복사하거나 커밋하지 않는다. 설치 작업은 `main.js`, `manifest.json`, `styles.css`만 갱신하고 기존 `data.json`을 보존한다.

## 6. Backend 구성

M4는 `08-YieldAgent`에 Plugin 전용 adapter를 추가한다.

```text
08-YieldAgent/
├── wiki_plugin_router.py
├── wiki_plugin_auth.py
└── wiki_review_store.py
```

- `wiki_plugin_router.py`: Plugin request/response와 기존 서비스를 연결
- `wiki_plugin_auth.py`: Bearer token 검증
- `wiki_review_store.py`: Review의 상태, version, append-only history 관리

공통 로직은 기존 함수 또는 작게 추출한 shared service를 사용한다. 기존 route가 Plugin route를 HTTP로 다시 호출하거나 동일 기능을 복제하지 않는다.

## 7. API 계약

모든 Plugin API는 `/api/wiki/plugin` namespace를 사용하고 Bearer token을 요구한다.

### 7.1 Health

```http
GET /api/wiki/plugin/health
Authorization: Bearer <token>
```

인증과 Backend 연결 상태를 확인한다. OpenSearch와 LLM 상태는 별도 dependency 상태로 반환하되, 하나가 실패했다고 HTTP server 자체를 offline으로 표시하지 않는다.

### 7.2 Search

```http
GET /api/wiki/plugin/search?q=...&product=...&fail_type=...&cause_oper=...&limit=20
```

응답의 각 결과는 다음 핵심 필드를 가진다.

```json
{
  "concept_id": "4SS_PRE_METAL_CLN_EASY",
  "concept_path": "concepts/4SS_PRE_METAL_CLN_EASY.md",
  "concept_status": "materialized",
  "product": "4SS",
  "fail_type": "EASY",
  "cause_oper": "PRE_METAL_CLN",
  "retrieval_mode": "hybrid",
  "score": 0.91,
  "evidence": [],
  "sources": []
}
```

Concept가 없으면 `concept_id`와 `concept_path`는 `null`, `concept_status`는 `source_only`다. 이 응답을 만드는 과정에서 Wiki job을 생성하거나 Markdown을 수정하지 않는다.

### 7.3 Related

```http
GET /api/wiki/plugin/related/{node_id}
```

현재 materialized Markdown의 wikilink와 backlink를 기준으로 related node를 반환한다. LLM 추론이나 OpenSearch 유사도만으로 graph edge를 만들지 않는다.

### 7.4 Source

```http
GET /api/wiki/plugin/sources/{doc_id}
```

Source 노트의 Vault 상대 경로, 존재하는 citation metadata, 원본 download URL을 반환한다. 사용할 수 없는 값을 추측하거나 placeholder URL을 만들지 않는다.

### 7.5 Chat

```http
POST /api/wiki/plugin/chat
Content-Type: application/json
```

```json
{
  "query": "현재 이슈의 주요 원인은?",
  "session_id": "...",
  "user_id": "...",
  "current_note_id": "concepts/4SS_PRE_METAL_CLN_EASY",
  "resume_value": null
}
```

응답은 기존 `/chat/stream`의 SSE event schema를 재사용한다. `stream_start`, `status`, `thinking`, `token`, `message`, `artifact`, `suggestion`, `interrupt`, `stream_end`, `error`를 Plugin이 필요한 형태로 표시한다.

`current_note_id`가 있으면 Backend가 canonical Vault path로 해석하고 존재와 범위를 검증한 뒤, 노트 metadata와 본문을 structured context로 Agent 실행에 전달한다. Plugin이 읽은 본문을 임의 문자열 prompt로 합쳐 보내지 않는다. 기존 `/chat/stream` request와 동작은 유지한다.

### 7.6 Review

```http
GET /api/wiki/plugin/reviews?status=pending
POST /api/wiki/plugin/reviews
PATCH /api/wiki/plugin/reviews/{review_id}
```

Review 상태는 `pending`, `approved`, `rejected`, `resolved`다. 변경 요청은 `expected_version`을 포함한다.

```json
{
  "status": "approved",
  "reviewer": "operator-id",
  "comment": "근거 확인 완료",
  "expected_version": 2
}
```

현재 version이 다르면 `409 Conflict`를 반환한다. 각 변경은 기존 기록을 덮어쓰지 않고 history에 추가한다. M4의 승인은 Concept 본문을 자동으로 바꾸지 않는다.

### 7.7 Session

```http
GET /api/wiki/plugin/sessions
GET /api/wiki/plugin/sessions/{session_id}
```

기존 Agent session 저장소를 읽는 인증 adapter다. Plugin은 최근 session 목록과 선택한 session의 표시 가능한 메시지만 받는다. 기존 session route의 저장 형식과 수명 정책은 바꾸지 않는다.

## 8. 검색 의미와 fallback

검색은 기존 server-side OpenSearch BM25와 vector retrieval을 재사용한다. Plugin은 embedding API를 호출하지 않는다.

```text
query + filters
  → 기존 hybrid retrieval
  → raw hit의 canonical metadata triple 검증
  → 동일 triple별 결과 grouping
  → materialized Concept join
  → Concept + evidence + Source response
```

- hybrid 검색 성공: `retrieval_mode=hybrid`
- embedding dependency만 실패하고 BM25 성공: `retrieval_mode=bm25_fallback`
- OpenSearch 자체 실패: fallback으로 성공 처리하지 않고 `502`
- metadata triple을 검증할 수 없는 hit: Concept를 추측하지 않고 Source evidence로만 반환

UI는 `bm25_fallback`을 Semantic Search라고 표시하지 않고 `키워드 검색으로 대체됨`이라고 알린다.

## 9. Chat context와 session

Plugin Chat은 기존 Agent와 session 저장소를 사용한다. 새로운 Agent graph나 별도 대화 DB를 만들지 않는다.

현재 노트 context는 다음 순서로 처리한다.

1. Plugin이 활성 Markdown file의 Vault-relative node ID를 전송한다.
2. Backend가 traversal과 symlink escape를 막고 Vault 내부 파일인지 검증한다.
3. Backend가 frontmatter, 본문, wikilink, citation을 읽는다.
4. 검증된 값을 typed context envelope에 담아 기존 Agent 실행에 전달한다.
5. Agent 응답의 citation은 다시 canonical Source ID와 Vault path로 변환한다.

Plugin 재시작 후에도 마지막 session ID와 표시 가능한 이력은 Backend session API에서 복구한다. token이나 전체 Chat 원문을 Vault 문서에 기록하지 않는다.

## 10. Citation과 노트 열기

- Vault 내부 문서: Obsidian `workspace.openLinkText`로 연다.
- 원본 PPT/PDF URL: Obsidian의 외부 링크 API를 통해 시스템 browser에서 연다.
- Citation은 최소한 `doc_id`, 표시 label, Source path 또는 download URL 중 실제 존재하는 값을 가진다.
- Source path와 URL이 모두 없으면 클릭 가능한 링크로 위장하지 않고 metadata만 표시한다.

## 11. 인증과 설정

Backend는 `OBSIDIAN_PLUGIN_API_TOKEN` 환경변수를 사용한다. 값이 없으면 Plugin namespace를 fail-closed 상태로 두며 요청을 허용하지 않는다.

Plugin 설정은 다음 두 값만 저장한다.

```json
{
  "serverUrl": "http://localhost:8001",
  "apiToken": "local-secret"
}
```

- 모든 Plugin endpoint는 Authorization header를 요구한다.
- token 비교에는 timing-safe comparison을 사용한다.
- token을 request/exception log에 기록하지 않는다.
- token을 Markdown, shared setting, build artifact에 포함하지 않는다.
- Obsidian desktop의 `requestUrl`을 사용하여 browser CORS 우회 설정을 Backend에 추가하지 않는다.
- server URL은 기본적으로 localhost지만 사용자가 설정 화면에서 변경할 수 있다.

## 12. 오류 처리

| 상태 | 의미 | Plugin 동작 |
| --- | --- | --- |
| `401` | token 누락 또는 불일치 | 설정 확인 안내 |
| `404` | node, Source, Review 없음 | stale 결과 알림과 새로고침 |
| `409` | Review version 충돌 | 최신 Review를 다시 불러옴 |
| `502` | OpenSearch 또는 LLM 장애 | retry 가능한 dependency 오류 표시 |

- Chat stream이 끊기면 부분 답변을 유지하고 수동 재시도 버튼을 표시한다.
- interrupt resume와 Review 변경처럼 상태를 바꾸는 요청은 자동 재전송하지 않는다.
- 동일 session으로 재질문할지 새 session을 만들지는 사용자가 선택한다.
- 연결 실패와 인증 실패를 같은 메시지로 숨기지 않는다.

## 13. Write ownership

Plugin이 직접 쓸 수 있는 것은 자신의 local `data.json`뿐이다.

- Concept, Source, MOC: 기존 Wiki Store/materializer만 작성
- Wiki 합성·갱신: `bootstrap_wiki`, `sync_wiki`
- Review: Backend의 `wiki_review_store`만 작성
- Plugin setting: Obsidian Plugin storage만 작성

이 경계는 여러 Obsidian 사용자가 같은 공유 Vault를 열어도 Markdown 경쟁 쓰기를 만들지 않기 위한 것이다.

### 13.1 Review 저장 형식

새 데이터베이스를 추가하지 않고 기존 `reviews/*.md`를 Review의 canonical record로 사용한다. M3가 생성한 source-removal Review도 같은 API로 조회할 수 있어야 한다.

- frontmatter: `id`, `review_type`, `status`, `target_concept_id`, `version`, `created`, `updated`
- 기존 Review metadata: 누락 없이 보존
- 본문: 운영 목적과 사람이 읽을 수 있는 설명
- managed history block: 변경 시각, 이전·새 상태, reviewer, comment를 append-only로 기록

기존 Review에 `version`이 없으면 읽을 때 version `1`로 해석한다. 최초 상태 변경 시 명시적인 `version`을 기록한다. Update는 기존 Wiki lock과 atomic replace를 사용하며, 요청의 `expected_version`을 lock 내부에서 다시 비교한다. Plugin이 전송한 reviewer와 comment는 YAML과 Markdown을 깨뜨리지 않도록 기존 frontmatter writer로 직렬화한다.

`approved`와 `rejected`는 운영자의 판단, `resolved`는 판단에 따른 후속 Wiki 작업까지 완료된 상태를 뜻한다. M4 API는 approved/rejected 기록까지만 수행하며 Concept를 자동 변경하거나 임의로 resolved 처리하지 않는다.

## 14. 테스트 전략

구현은 TDD로 진행한다.

### 14.1 Backend 단위 테스트

- token 없음, 오류, 성공 인증
- token 미설정 시 fail-closed
- search result의 triple grouping과 Concept join
- Concept가 없는 `source_only` 결과
- embedding 실패 시 BM25 fallback과 mode 표시
- OpenSearch 실패 시 `502`
- node path traversal과 Vault 외부 path 차단
- current note structured context 변환
- Review version 증가, history append, `409` 충돌
- 기존 route contract 회귀 방지

### 14.2 Plugin 단위 테스트

- 설정 저장과 Authorization header
- Chat SSE event parsing
- partial stream과 reconnect 상태
- Concept, Source, external citation 열기
- Review conflict 표시
- `bm25_fallback` label 표시

### 14.3 임시 Vault 통합 테스트

- source build와 install artifact 생성
- 기존 `data.json`을 보존하는 설치
- 현재 note ID 추출과 related note 열기
- Plugin unload/reload 시 view와 session 복구
- Plugin이 Concept·Source 파일을 수정하지 않는지 확인

### 14.4 실제 end-to-end 완료 기준

다음 시나리오를 실제 Backend, OpenSearch, LLM, Obsidian으로 실행한다.

1. Plugin 설치 및 활성화
2. `http://localhost:8001` 연결과 Bearer token 인증
3. `4SS / EASY / PRE_METAL_CLN` 검색
4. 검색 결과에서 `concepts/4SS_PRE_METAL_CLN_EASY.md` 열기
5. Related Notes와 backlink 확인
6. 현재 Concept를 context로 Chat 질문
7. 실제 Agent SSE 답변과 Citation 수신
8. Citation을 클릭하여 Source Markdown 열기
9. Review 승인·반려와 이력 보존 확인
10. Obsidian 재시작 후 설정과 session 복구
11. 잘못된 token, embedding 실패, OpenSearch 장애, LLM 장애 동작 확인

lint, syntax check, 단위 테스트만으로 M4 완료를 선언하지 않는다. 실제 LLM credential이 unavailable한 경우 Chat E2E는 완료되지 않은 것으로 명시하며 mock 성공으로 대체하지 않는다.

## 15. 구현 순서

1. Backend Plugin 인증과 health endpoint
2. Search adapter와 Concept grouping
3. Related, Source adapter
4. 기존 Agent streaming service 재사용과 Plugin Chat route
5. Review store와 optimistic concurrency API
6. Obsidian Plugin scaffold와 설정
7. Chat, Search, Review sidebar
8. build/install script와 local Vault 설치
9. 실제 end-to-end 검증과 운영 문서

각 단계는 해당 자동화 테스트가 먼저 실패하는 것을 확인하고 최소 구현으로 통과시킨다. 기존 frontend와 M2/M3 경로에 대한 회귀 테스트를 함께 실행한다.

## 16. 운영 결과

M4 이후 역할은 다음처럼 분리된다.

```text
bootstrap_wiki (초기 구축·복구)
          │
sync_wiki (증분 Wiki 갱신)
          │
Obsidian Markdown + wikilink graph
          │
Yield Wiki Plugin
  ├── Search: 기존 OpenSearch
  ├── Chat: 기존 Agent/SSE
  ├── Related: materialized graph
  └── Review: Backend review store
```

Obsidian은 기존 frontend를 대체하도록 강제되는 것이 아니라, 사내 운영자가 Wiki를 직접 탐색하고 검토하는 주 UI가 된다. 기존 frontend는 변경 없이 남아 독립적으로 계속 사용할 수 있다.
