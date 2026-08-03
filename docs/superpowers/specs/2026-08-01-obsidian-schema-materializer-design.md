# Obsidian Schema Materializer 설계

- 작성일: 2026-08-01
- 대상 브랜치: `feat/obsidian-wiki-platform`
- 대상 시스템: `08-YieldAgent`의 `fail_history_agent` Wiki
- 대상 Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- Milestone: M2 — Obsidian schema와 실제 Wiki graph

## 1. 목적

현재 Wiki frontend는 API가 metadata를 읽어 동적으로 graph edge를 만들지만, Obsidian의 기본 Graph View는 Markdown의 `[[wikilink]]`만 연결로 인식한다. 현재 Vault 문서는 실제 wikilink가 없어 노드가 서로 떨어져 보인다.

M2는 기존 Concept 합성과 frontend를 유지하면서, Concept metadata와 citation을 결정론적으로 Markdown 문서와 wikilink로 materialize한다. 최종 Graph는 다음 4단계 계층과 Source 연결을 표현한다.

```text
Product → Product-specific Fail Type → Cause Operation → Concept → Source
```

## 2. 범위

### 포함

- Product, product-specific Fail Type, Cause Operation MOC 문서
- Citation 기반 Source 문서
- Concept의 agent-managed Knowledge Links
- 실제 문서 수와 링크를 반영하는 `index.md`와 `overview.md`
- `purpose.md`와 `schema.md`의 초기 템플릿
- 기존 Vault를 변환하는 backfill 명령
- bootstrap과 질문 기반 Concept 저장 경로의 materializer 호출
- 자동화 테스트와 실제 Obsidian Graph View 검증

### 제외

- OpenSearch mapping, embedding, 기존 문서 재임베딩
- LLM을 이용한 링크 대상 또는 파일명 선택
- 기존 `yield_frontend`, `wiki_frontend`, `repl_agent/frontend`, Streamlit UI 변경
- 기존 `/api/wiki/graph` 응답 계약 변경
- Obsidian Plugin 구현
- content-only index에서 metadata를 추론하는 기능
- Neo4j 또는 별도 Graph DB 도입

## 3. 설계 원칙

1. 링크는 `product`, `fail_type`, `cause_oper`, citation `doc_id`만으로 결정한다.
2. LLM이 작성한 FMMEA/8D 본문과 inline citation은 보존한다.
3. 자동 생성 영역과 사용자가 관리하는 영역을 명확히 구분한다.
4. 같은 Vault 상태에서 반복 실행한 결과는 동일해야 한다.
5. 자동 삭제는 materializer가 생성했다고 표시된 파생 문서로 제한한다.
6. 각 파일은 임시 파일을 거쳐 원자적으로 교체한다.
7. 기존 Wiki 저장과 API의 책임을 확장하되 frontend 계약은 바꾸지 않는다.

## 4. Vault 문서 구조

```text
YieldWiki/
├── purpose.md
├── schema.md
├── overview.md
├── index.md
├── products/
│   └── 4SS.md
├── product_fails/
│   └── 4SS_EASY.md
├── operations/
│   └── PRE_METAL_CLN.md
├── concepts/
│   └── 4SS_PRE_METAL_CLN_EASY.md
├── sources/
│   ├── FH-000238.md
│   └── FH-000243.md
└── super_concepts/
    └── fail_type_EASY.md
```

`episodes/`, `reviews/`, `aliases/`, `attachments/`, `lint_logs/`, `.yield-wiki/` 등 기존 디렉터리는 그대로 유지한다.

## 5. Link topology

예시 연결은 다음과 같다.

```text
[[products/4SS]]
  → [[product_fails/4SS_EASY|EASY]]
    → [[operations/PRE_METAL_CLN|PRE METAL CLN]]
      → [[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]
        → [[sources/FH-000238|FH-000238]]
```

각 문서의 링크 책임은 다음과 같다.

| 문서 | 상위 링크 | 하위 링크 |
| --- | --- | --- |
| Product | 없음 | 해당 Product Fail |
| Product Fail | Product | 해당 Cause Operation |
| Cause Operation | 관련 Product Fail | 해당 Concept |
| Concept | 해당 Cause Operation | 실제 인용 Source, 유효한 Super Concept |
| Source | 없음 | 자신을 인용한 Concept backlink |
| Super Concept | 없음 | 실제 존재하는 Concept |

Product와 Fail Type을 Concept에 직접 연결하는 shortcut은 만들지 않는다. 이 둘은 Concept frontmatter property와 tag로 유지하여 기본 Graph의 4단계 구조를 흐리지 않는다.

동일 Cause Operation이 여러 Product Fail에 사용되면 하나의 Operation 문서가 여러 Product Fail의 하위 노드가 된다. Operation 문서에는 연결된 Product Fail과 Concept가 정렬된 목록으로 기록된다.

## 6. 문서 소유권과 갱신 정책

### 6.1 Agent 본문 보존 문서

다음 문서는 materializer가 전체를 덮어쓰거나 삭제하지 않는다.

- `concepts/`
- `super_concepts/`
- `episodes/`
- `reviews/`
- 사용자가 직접 작성한 기타 Markdown

Concept에서는 아래 marker 사이만 materializer가 교체한다.

```markdown
<!-- yield-wiki:knowledge-links:start -->
## Knowledge Links

- Operation: [[operations/PRE_METAL_CLN|PRE METAL CLN]]
- Sources: [[sources/FH-000238|FH-000238]]
<!-- yield-wiki:knowledge-links:end -->
```

marker가 없으면 본문 끝에 추가한다. marker 밖의 본문은 byte 단위로 보존한다.

Super Concept도 동일한 managed block 방식으로 실제 존재하는 Concept 링크만 기록한다. 참조 대상이 하나도 없거나 일부가 없으면 파일은 유지하고 frontmatter의 `status`를 `stale`로 바꾸며 managed block에 누락 참조를 표시한다.

### 6.2 전체 자동 생성 문서

다음 문서는 materializer가 현재 Concept 집합에서 전체를 재생성한다.

- `products/`
- `product_fails/`
- `operations/`
- `sources/`
- `index.md`
- `overview.md`

파생 문서에는 다음 frontmatter를 기록한다.

```yaml
generated_by: yield-wiki-materializer
```

현재 상태에서 더 이상 필요하지 않은 파생 파일은 이 marker가 있을 때만 정리한다. marker가 없는 같은 디렉터리의 파일은 사용자 소유로 간주하여 보존한다.

### 6.3 생성 시에만 쓰는 문서

`purpose.md`와 `schema.md`는 없을 때만 기본 템플릿을 생성한다. 생성 후에는 운영자 소유 문서이며 materializer가 덮어쓰지 않는다.

`.obsidian/graph.json`도 없을 때만 권장 필터를 가진 초기 설정을 생성한다. 기존 설정은 절대 덮어쓰지 않는다. 권장 제외 대상은 `index.md`, `log.md`, `lint_logs/`와 운영용 hidden path다.

## 7. Source 생성 규칙

Source는 Concept의 실제 citation에서 확인된 `doc_id`에 대해서만 생성한다. Source frontmatter와 본문에는 현재 Wiki 데이터에서 사용할 수 있는 범위의 다음 값을 기록한다.

- `doc_id`
- `source_file`
- `date`
- `page_num`
- `download_url`
- 자신을 인용한 Concept backlink

원본 PPT/PDF 바이너리는 Vault에 복사하지 않는다. 기존 다운로드 URL을 만들 수 있는 정보가 있을 때만 링크를 기록한다. 값이 없는 필드는 추측하거나 placeholder를 만들지 않는다.

## 8. Materialization 흐름

```text
Concept 저장 완료
  → Vault의 Concept/Super Concept frontmatter와 citation 스캔
  → metadata와 citation validation
  → 목표 graph model 계산
  → MOC/Source/index/overview Markdown render
  → Concept/Super Concept managed block render
  → 같은 디렉터리의 temp file로 write
  → atomic replace
  → materializer 소유의 stale 파생 파일만 정리
```

Materializer는 OpenSearch나 LLM을 직접 호출하지 않는다. Wiki 저장 결과만 입력으로 사용하므로 M2에서는 embedding과 index schema가 바뀌지 않는다.

## 9. 기존 저장 흐름과의 결합

### 9.1 Bootstrap

대량 Concept 생성 중 매번 Vault 전체를 다시 스캔하면 불필요한 O(n²) 작업이 된다. Concept upsert에는 내부용 materialization 제어 인자를 두고 bootstrap은 개별 저장 시 갱신을 미룬 뒤 전체 batch 종료 시 한 번 실행한다.

```text
metadata triple별 Concept upsert(materialize=false)
  → batch 완료
  → materialize once
```

### 9.2 질문 기반 갱신

질문 처리로 Concept 하나가 저장되거나 갱신되면 성공한 upsert 직후 materializer를 한 번 실행한다. Wiki 저장이 실패하면 materializer는 실행하지 않는다.

### 9.3 Backfill

별도 CLI는 현재 Vault를 대상으로 preview와 apply를 지원한다.

```text
materialize_obsidian_wiki.py --check
materialize_obsidian_wiki.py --apply
```

`--check`는 파일을 바꾸지 않고 생성·수정·정리 대상과 validation 오류를 보고한다. `--apply`는 같은 계산 결과를 실제 반영한다. 실제 외부 Vault에 최초 적용하기 전에는 전체 Vault를 복구 가능한 별도 경로로 백업한다.

## 10. Validation과 오류 처리

- Concept에 canonical `product`, `fail_type`, `cause_oper`가 없으면 해당 Concept를 건너뛰고 lint 오류로 보고한다.
- 안전한 stable filename으로 변환할 수 없는 metadata는 임의로 보정하지 않고 오류로 보고한다.
- Citation에 `doc_id`가 없으면 Source를 생성하지 않고 lint 오류로 보고한다.
- 하나의 잘못된 Concept 때문에 검증 가능한 다른 Concept의 preview가 불가능해지지는 않는다.
- `--apply`는 validation 오류가 하나라도 있으면 아무 파일도 변경하지 않고 실패한다.
- 렌더링 또는 쓰기 실패 시 기존 파일은 유지한다.
- 여러 프로세스가 동시에 materialize하지 않도록 기존 Wiki의 단일 writer 경계를 재사용한다.

## 11. 결정론과 정렬

- 모든 문서 목록과 링크 목록은 canonical key 기준으로 정렬한다.
- YAML과 Markdown 출력 순서를 고정한다.
- timestamp 때문에 매 실행마다 diff가 생기지 않도록 내용 변경이 있을 때만 파생 문서를 교체한다.
- 동일 입력으로 연속 두 번 실행했을 때 두 번째 실행의 변경 파일 수는 0이어야 한다.

## 12. 테스트 전략

구현은 TDD로 진행한다.

### 12.1 단위 테스트

- metadata triple에서 MOC 경로와 wikilink 생성
- Citation에서 Source model 생성
- 정렬된 4단계 topology 생성
- managed block 삽입과 교체
- Concept 본문 보존
- Super Concept의 유효 링크와 stale 판정

### 12.2 임시 Vault 통합 테스트

- Product → Product Fail → Operation → Concept → Source 전체 생성
- `index.md`의 실제 count와 링크 확인
- `purpose.md`, `schema.md` create-only 확인
- marker 없는 사용자 파일 보존
- materializer 소유의 stale 파생 파일만 정리
- 연속 실행 idempotency 확인
- validation 오류 시 apply가 Vault를 변경하지 않는지 확인
- bootstrap batch에서 materializer가 한 번만 호출되는지 확인
- 질문 기반 upsert 후 materializer가 호출되는지 확인

### 12.3 회귀 테스트

- 기존 Wiki test suite 통과
- `/api/wiki/graph` 응답 shape 유지
- 기존 frontend 경로에 변경이 없는지 diff 확인
- OpenSearch mapping과 embedding 코드에 변경이 없는지 diff 확인

### 12.4 실제 End-to-End 검증

대상은 `/Users/daehwankim/SYLDAIX/YieldWiki`다.

1. 기존 Vault를 백업한다.
2. 실제 생성된 `4SS_PRE_METAL_CLN_EASY` Concept를 대상으로 `--check` 결과를 확인한다.
3. `--apply`로 backfill한다.
4. 파일을 다시 파싱하여 broken wikilink가 0개인지 확인한다.
5. Obsidian에서 Vault를 열고 Graph View의 연결선을 직접 확인한다.
6. Product에서 Concept와 Source까지 노드를 따라 이동한다.
7. 노드를 클릭했을 때 해당 Markdown이 열리는지 확인한다.

실제 UI 확인까지 성공하기 전에는 M2 완료로 선언하지 않는다.

## 13. 완료 조건

- Graph View에 4단계 계층과 Source 연결선이 표시된다.
- Product → Product Fail → Operation → Concept 경로를 따라 탐색할 수 있다.
- Concept → Source citation 경로를 따라 탐색할 수 있다.
- 기존 LLM Wiki 본문이 보존된다.
- 잘못되거나 끊어진 wikilink가 없다.
- 두 번째 materialization 실행은 변경을 만들지 않는다.
- 기존 Wiki API와 frontend 동작이 유지된다.
- OpenSearch 재임베딩 없이 동작한다.
