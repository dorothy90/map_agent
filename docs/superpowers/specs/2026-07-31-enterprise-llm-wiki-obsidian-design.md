# Enterprise LLM Wiki + Obsidian 설계

- 작성일: 2026-07-31
- 대상 브랜치: `feat/obsidian-wiki-platform`
- 대상 시스템: `08-YieldAgent`의 `fail_history_agent` Wiki
- Obsidian Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`

## 1. 목적

현재 `fail-history` OpenSearch 인덱스와 `fail_history_agent`가 생성하는 Wiki를 사내 공유 Obsidian Vault에서 탐색하고 검토할 수 있는 운영형 지식 플랫폼으로 정비한다.

기존 PPT 검토·OpenSearch 적재·Fail History 검색·Wiki 합성 흐름은 유지한다. 이번 설계는 별도의 지식 엔진으로 교체하는 작업이 아니라, 기존 Wiki를 단일 외부 Vault에 안정적으로 저장하고 Obsidian Plugin을 새로운 UI로 추가하는 작업이다.

## 2. 확정된 결정

1. `fail-history` OpenSearch 인덱스를 근거 데이터의 원본으로 유지한다.
2. Wiki Concept의 canonical key는 `product + cause_oper + fail_type`이다.
3. `bootstrap_wiki_warmup.py`는 사용자 질문 없이 OpenSearch 문서를 메타데이터별로 직접 합성하는 경로로 유지한다.
4. `fail_history_tools.py`는 사용자 질문 시 하이브리드 검색과 Episode/Concept 근거 누적을 담당한다.
5. Obsidian Vault는 LLM이 컴파일한 Markdown 지식 계층이며 별도 벡터 DB가 아니다.
6. 기존 `yield_frontend`, `wiki_frontend`, `repl_agent/frontend`, Streamlit UI는 수정하지 않는다.
7. 신규 사용자 UI는 Obsidian Plugin으로만 추가한다.
8. Agent 생성 노트는 Agent만 수정하고, 사용자는 `reviews/`에 검토 의견을 기록한다.
9. Vault 위치는 코드에 하드코딩하지 않고 `WIKI_VAULT_PATH`로 주입한다. 현재 장비의 값은 `/Users/daehwankim/SYLDAIX/YieldWiki`다.
10. 세 메타데이터가 한 문서에 함께 존재하지 않는 별도 content-only 인덱스는 이번 범위에서 제외한다.
11. 자연어 해석 실패를 keyword, regex, 문구 목록, 특수 분기로 보정하지 않는다.

## 3. 레퍼런스 적용 범위

### 3.1 `domleca/llm-wiki`

참고 저장소: <https://github.com/domleca/llm-wiki>

MIT 라이선스의 Obsidian Plugin이다. Obsidian 안에서 Chat, 하이브리드 검색, Citation, Wiki 탐색을 제공하는 UX와 Plugin 구조를 참고한다.

다음 기능은 가져오지 않는다.

- Vault 전체 재추출
- 로컬 embedding 재생성
- 별도 `wiki/kb.json` Knowledge Base
- 기존 Wiki Markdown을 다시 읽어 두 번째 Wiki를 생성하는 흐름

YieldAgent용 Plugin은 기존 FastAPI를 호출하는 얇은 클라이언트로 구현한다.

### 3.2 `nashsu/llm_wiki`

참고 저장소: <https://github.com/nashsu/llm_wiki>

다음 운영 패턴을 설계 참고자료로 사용한다.

- Raw Source → Generated Wiki → Schema 분리
- `purpose.md`, `schema.md`, `index.md`, `overview.md`, `log.md`
- content hash 기반 증분 처리
- 재시작 가능한 작업 큐
- 비동기 Review
- Citation 추적
- Wiki lint와 Knowledge Graph insight

이 저장소는 GPLv3이므로 소스 코드를 현재 사내 코드에 직접 복사하거나 결합하지 않는다. Tauri, LanceDB, 자체 문서 파서, 자체 Chat Agent도 도입하지 않는다.

## 4. 목표 아키텍처

```text
검토 완료된 PPT
      │
      ▼
OpenSearch fail-history
content + embedding + metadata
      │
      ├──────────────────────┐
      ▼                      ▼
Bootstrap Wiki Compiler      Fail History Search
질문 없는 직접 합성            질문 기반 검색·근거 누적
      │                      │
      └──────────┬───────────┘
                 ▼
            Wiki Service
     Concept / Episode / Source
                 │
                 ▼
/Users/daehwankim/SYLDAIX/YieldWiki
                 │
        ┌────────┴─────────┐
        ▼                  ▼
Obsidian Plugin        기존 YieldAgent API/UI
Chat/Search/Review     변경 없이 계속 사용
```

## 5. 저장소별 책임

### 5.1 OpenSearch

근거 문서와 검색 인덱스를 보관한다.

- `content`
- `embedding`
- `product`
- `fail_type`
- `cause_oper`
- `cause`
- `action`
- `comment`
- `date`
- `doc_id`
- `source_file`

현재 검증된 `fail-history` 인덱스만 사용한다. Wiki 합성은 embedding에서 메타데이터를 복원하지 않고 별도 metadata 필드로 문서를 집계한다.

### 5.2 Obsidian Vault

LLM이 합성한 사람이 읽을 수 있는 지식을 Markdown으로 보관한다.

```text
YieldWiki/
├── purpose.md
├── schema.md
├── index.md
├── overview.md
├── log.md
├── concepts/
├── episodes/
├── super_concepts/
├── aliases/
├── sources/
├── reviews/
├── attachments/
├── lint_logs/
├── .obsidian/
└── .yield-wiki/
    └── manifest.json
```

### 5.3 MongoDB

기존 MongoDB를 작업 실행 상태에 재사용한다. 별도 운영 DB는 추가하지 않는다.

- 증분 합성 job 상태
- 재시도 횟수
- 처리 시작·종료 시각
- Review 상태 변경 이력
- Plugin 대화 세션 metadata

Vault의 `.yield-wiki/manifest.json`은 문서 fingerprint와 마지막 성공 결과를 담는 이식 가능한 manifest다. 실행 중인 queue 상태의 원본은 MongoDB다.

## 6. Vault 문서 계약

### 6.1 Concept

파일명은 기존 stable machine key 규칙을 유지한다.

```text
concepts/4SS_PRE_METAL_CLN_EASY.md
```

frontmatter 예시:

```yaml
id: concept:4SS|PRE METAL CLN|EASY
type: concept
title: 4SS EASY @ PRE METAL CLN
product: 4SS
fail_type: EASY
cause_oper: PRE METAL CLN
created: 2026-07-31T10:00:00
updated: 2026-07-31T11:00:00
version: 3
status: active
confidence: 0.82
source_episode_ids:
  - episode:a123456789ab
sources:
  - FH-000238
aliases:
  - 4SS PRE METAL CLN EASY
tags:
  - fail-history
  - product/4SS
  - fail/EASY
  - operation/PRE-METAL-CLN
```

본문은 현재 FMMEA/8D 기반 합성 구조와 inline Citation을 유지한다. 새 Entity나 관계를 근거 없이 추가하지 않는다.

### 6.2 Episode

검색 결과의 불변 스냅샷이다.

- 동일 query, filter, doc set은 중복 생성하지 않는다.
- 원본 `doc_id`와 `source_file`을 보존한다.
- Concept 합성 근거로만 사용하며 사람이 직접 편집하지 않는다.

### 6.3 Source

OpenSearch 문서와 원본 PPT를 연결하는 얇은 노트다.

- `doc_id`
- `source_file`
- `date`
- `page_num`
- 다운로드 URL
- 해당 Source를 인용한 Concept backlink

원본 PPT 바이너리는 초기 단계에서 Vault에 복사하지 않는다. 기존 다운로드 endpoint를 링크한다.

### 6.4 Review

사용자가 작성할 수 있는 유일한 운영 영역이다.

```yaml
type: review
target: "[[4SS_PRE_METAL_CLN_EASY]]"
status: pending
reviewer: user-id
created: 2026-07-31T12:00:00
```

상태는 `pending`, `approved`, `rejected`, `resolved`로 제한한다. Review 승인만으로 Agent 생성 본문을 직접 덮어쓰지 않는다. 승인 결과는 다음 합성의 구조화된 검토 context로 전달하고 변경 이력에 기록한다.

## 7. 주요 데이터 흐름

### 7.1 초기 직접 합성

```text
bootstrap_wiki_warmup.py --apply
  → metadata triple 집계
  → triple별 raw docs 조회
  → LLM Concept 합성
  → Citation 보강
  → atomic Markdown write
  → index/overview/log 갱신
  → lint
```

사용자 질문 embedding은 사용하지 않는다.

### 7.2 질문 기반 증분 합성

```text
질문
  → wiki-first gate
  → gate miss/assisted면 OpenSearch hybrid search
  → wiki_queue enqueue
  → Episode 생성
  → Concept 근거 누적
  → evidence diversity 충족 시 Concept 재합성
  → Vault 반영
```

기존 동작을 유지하되 queue 영속화는 후속 milestone에서 추가한다.

### 7.3 OpenSearch 변경 기반 증분 합성

```text
triple별 doc_id + source fingerprint 계산
  → manifest와 비교
  ├── 동일: skip
  ├── 신규/수정: 해당 triple만 재합성
  └── 삭제: stale 표시 후 Review 생성
```

fingerprint가 같으면 LLM을 호출하지 않는다. 실패한 triple은 다른 triple의 성공을 롤백하지 않고 독립적으로 재시도한다.

## 8. 공통 Vault 경로

현재 `wiki_store.py`는 `WIKI_VAULT_PATH`를 사용하지만 `wiki_router.py`는 코드 내부의 `wiki/`를 직접 가리킨다. 모든 모듈은 하나의 공통 설정 모듈에서 Vault 경로를 가져와야 한다.

적용 대상:

- `wiki_store.py`
- `wiki_router.py`
- `bootstrap_wiki_warmup.py`
- `wiki_lint.py`
- `make_super_concept.py`
- `migrate_v2_to_v3.py`
- `agent_server.py`의 lint scheduler

운영 모드에서는 환경변수가 없거나 Vault가 쓰기 불가능하면 시작을 실패시킨다. 레포 내부 `wiki/`로 조용히 fallback하지 않는다. 테스트에서는 임시 Vault 경로를 명시적으로 주입한다.

## 9. Obsidian Plugin

신규 Plugin은 `.obsidian/plugins/yield-wiki/`에 설치한다.

### 9.1 기능

- YieldAgent 서버 URL 설정
- 인증 토큰 설정
- 현재 노트 기반 질문
- Fail History Chat
- Semantic Search
- Product/Fail Type/Cause Oper 필터
- Related Notes
- Citation 클릭 시 Source 노트 또는 원본 PPT 열기
- Review 작성, 승인, 반려
- 대화 세션 목록과 재개
- 서버 연결 상태와 오류 표시

### 9.2 제외 기능

- Vault 전체 LLM 재추출
- Plugin 내부 embedding 생성
- 별도 vector store
- 별도 `kb.json`
- Plugin이 `concepts/`를 직접 수정하는 동작

### 9.3 Backend API

기존 API 계약을 변경하지 않고 Plugin 전용 endpoint를 추가한다.

```text
GET  /api/wiki/health
GET  /api/wiki/search
GET  /api/wiki/related/{node_id}
GET  /api/wiki/sources/{doc_id}
POST /api/wiki/chat
GET  /api/wiki/reviews
POST /api/wiki/reviews
PATCH /api/wiki/reviews/{review_id}
```

Chat streaming은 기존 서버의 SSE event contract를 재사용한다. Plugin 전용 API는 bearer token을 요구하고, 토큰을 Vault Markdown이나 Git에 저장하지 않는다.

## 10. 기존 Frontend 비변경 계약

다음 디렉터리는 수정하지 않는다.

- `08-YieldAgent/yield_frontend/`
- `08-YieldAgent/wiki_frontend/`
- `08-YieldAgent/repl_agent/frontend/`
- `08-YieldAgent/pages/`
- `08-YieldAgent/app.py`의 UI 코드

Backend가 외부 Vault를 읽게 되면서 기존 화면에 표시되는 데이터 위치는 바뀌지만, 화면 코드와 기존 API 응답 형식은 바뀌지 않는다. 기존 Frontend는 회귀 테스트 대상으로만 사용한다.

## 11. Knowledge Graph와 Agentic RAG

초기 Graph 관계는 검증 가능한 기존 정보로 제한한다.

- Concept → Episode
- Episode → Source
- Product → Fail Type → Cause Oper → Concept
- Concept 간 공통 Source
- Concept 간 공통 metadata

일반 Entity/Relation 추출은 ingestion 확장 milestone로 미룬다.

Agentic RAG는 기존 Planner/Router를 유지한다.

```text
Question
  → Wiki-first exact triple
  → OpenSearch hybrid retrieval
  → Wiki graph traversal
  → evidence merge
  → Citation 검증
  → Answer
```

Search/Reflection/Citation Agent를 처음부터 별도 프로세스로 늘리지 않는다. 측정된 실패가 있을 때만 기존 graph에 최소 node를 추가한다.

## 12. 오류 처리와 동시성

- Agent가 Vault의 유일한 자동 writer다.
- Markdown은 임시 파일 작성 후 `os.replace`로 원자적 교체한다.
- Concept별 중복 합성은 job key로 차단한다.
- 부분 실패는 해당 Concept에만 기록하고 전체 batch를 롤백하지 않는다.
- 공유 폴더 연결이 끊기면 작업을 재시도 상태로 남기고 내부 Vault에 대신 쓰지 않는다.
- 사용자가 Agent 관리 노트를 직접 수정한 경우 fingerprint 충돌로 감지하고 Review를 만든다.
- Plugin은 Agent 관리 노트에 쓰지 않고 Review API만 사용한다.

## 13. 보안과 운영

- Vault와 API는 사내 접근 제어 범위에 둔다.
- OpenSearch, LLM, API 인증정보를 Vault나 `.obsidian` 공유 설정에 커밋하지 않는다.
- Plugin token은 Obsidian의 local plugin data에만 저장한다.
- Source 다운로드는 기존 권한 검사를 통과해야 한다.
- 모든 합성, 갱신, 승인, 반려를 `log.md`와 서버 audit log에 기록한다.
- Vault backup과 복구는 코드 배포와 분리한다.

## 14. 마이그레이션

1. 새 Vault 경로와 쓰기 권한을 검사한다.
2. 빈 Vault 구조를 생성한다.
3. 기존 `08-YieldAgent/wiki` 파일을 복사한다.
4. 파일 수, ID, frontmatter, body checksum을 비교한다.
5. 새 schema의 누락 필드만 보강한다.
6. `index.md`, `overview.md`, Source/MOC를 재생성한다.
7. lint를 실행한다.
8. Backend를 새 Vault 경로로 시작한다.
9. 기존 API와 React Wiki 조회를 검증한다.
10. Obsidian에서 동일 노트를 연다.

기존 Vault는 검증 완료 전 삭제하지 않는다.

## 15. 구현 Milestone

### M1. 외부 Vault 기반

- 공통 Vault 설정
- 운영 fail-fast
- Vault 초기화
- 마이그레이션
- 기존 API 호환
- Wiki 전용 단위·통합 테스트
- 실제 OpenSearch/LLM/Vault E2E

### M2. Obsidian 스키마

- `purpose.md`, `schema.md`, `overview.md`
- Concept properties
- Source 노트
- 근거 기반 wikilink
- Product/Fail/Operation MOC
- Obsidian 기본 설정

### M3. 증분 Wiki Compiler

- triple fingerprint
- 변경 없는 triple skip
- 변경 triple만 재합성
- MongoDB job persistence
- retry/resume
- Review 생성

### M4. Obsidian Plugin

- 설정과 인증
- Search/Chat
- Related Notes
- Citation
- Review
- Plugin 빌드·설치·E2E

### M5. Graph와 Agentic RAG

- Source/metadata graph
- graph-assisted retrieval
- Citation 검증
- 고립·broken link·knowledge gap 분석

### M6. 문서 수집 확장

- Outlook
- Markdown/PDF/DOCX
- SharePoint
- Images/OCR
- 주간 자동화

### M7. Content-only 인덱스 연구

한 문서에 canonical triple이 함께 존재하지 않는 자료를 대상으로 multi-document metadata assembly를 별도 설계한다. 이 milestone 전에는 해당 인덱스를 현재 Wiki에 혼합하지 않는다.

## 16. 검증 기준

완료 선언에는 실제 실행 검증이 필요하다.

### M1 완료 기준

- 실제 `fail-history` OpenSearch 조회 성공
- 실제 LLM Concept 합성 성공
- 외부 Vault에 Markdown 생성 성공
- 생성된 frontmatter/body 파싱 성공
- Wiki API가 외부 Vault의 동일 내용을 반환
- 기존 React Wiki 화면이 동일 API를 정상 표시
- Obsidian에서 노트와 link를 열 수 있음
- Vault 미연결 시 내부 경로에 파일이 생성되지 않음

### M3 완료 기준

- 변경 없는 재실행에서 LLM 호출 0회
- 신규 문서 추가 시 영향받은 triple만 갱신
- 실패 후 재시작해 job 재개
- 저신뢰/충돌 결과가 Review로 이동

### M4 완료 기준

- Obsidian에서 실제 Chat 요청과 SSE 수신
- Search 결과에서 실제 Concept 열기
- Citation에서 Source/PPT 열기
- Review 생성·상태 변경
- Plugin이 별도 embedding 또는 `kb.json`을 만들지 않음

## 17. 기준 테스트 상태

새 worktree 생성 직후 전체 Python 테스트 수집은 Wiki 변경 전부터 다음 환경 비호환으로 실패한다.

- `motor 3.3.2`와 `pymongo 4.15.5`
- `statsmodels 0.14.4`와 현재 pandas 환경

이 문제는 이번 Wiki 범위에 포함하지 않는다. Wiki 관련 테스트는 외부 시스템을 격리한 전용 test suite로 추가하고, milestone 완료 시 실제 OpenSearch, 실제 LLM, 실제 Vault까지 별도 E2E로 검증한다.

## 18. 비범위

- 기존 Frontend UI 변경
- 기존 OpenSearch 문서 재임베딩
- `fail-history` 인덱스 schema 변경
- content-only 인덱스의 metadata 복원
- Tauri 데스크톱 앱 도입
- LanceDB 도입
- 일반 목적 Entity Graph 전면 구축
- Outlook/SharePoint ingestion의 즉시 구현
- 기존 Wiki 또는 사용자 자료 삭제
