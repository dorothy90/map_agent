# Incremental Wiki Sync 설계

- 작성일: 2026-08-01
- 대상 브랜치: `feat/obsidian-wiki-platform`
- 대상 시스템: `08-YieldAgent`의 `fail_history_agent` Wiki
- 대상 Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- Milestone: M3 — OpenSearch 변경 기반 증분 Wiki 갱신

## 1. 목적

M3는 새로운 Wiki 합성기를 만드는 작업이 아니다. 기존 `synthesize_concept_from_docs()`와 `wiki_store.upsert_concept()`를 그대로 사용하면서, OpenSearch에 문서가 추가·수정·삭제됐을 때 영향을 받는 metadata triple만 안전하게 다시 합성한다.

최초 구축과 전체 강제 재생성은 기존 `bootstrap_wiki_warmup.py`가 계속 담당한다. 평상시 증분 운영은 CronJob에서 호출 가능한 `sync_wiki.py`가 담당한다.

```text
최초 구축·전체 복구: bootstrap_wiki_warmup.py
일상적인 변경 반영: sync_wiki.py
Markdown graph 갱신: M2 wiki_materializer.py
```

## 2. 확정된 범위

### 포함

- OpenSearch의 전체 metadata triple을 누락 없이 열거
- 근거 데이터 fingerprint를 이용한 new/changed/unchanged/source_removed 판정
- MongoDB job, lease, retry, resume, 전역 실행 lock
- `.yield-wiki/manifest.json` 결과 mirror
- Concept frontmatter의 source fingerprint와 doc ID 기록
- 신규·변경 triple만 기존 LLM 합성기로 처리
- 근거 삭제 시 Concept stale 처리와 Review 생성
- bootstrap 성공 결과의 fingerprint 기록
- bootstrap의 exact triple 선택 실행
- `--check`, `--apply`, `--resume`, `--limit` CLI
- 무료 LLM API를 이용한 상위 3개 triple 실제 E2E

### 제외

- 새로운 LLM Prompt 또는 새로운 Wiki 합성기
- OpenSearch mapping, embedding, 재임베딩 변경
- 390개 triple 전체의 즉시 LLM 실행
- OS/macOS/Linux CronJob 자동 등록
- FastAPI 상시 scheduler 연결
- Obsidian Plugin
- content-only index의 metadata 추론
- Concept 또는 Source의 자동 삭제
- 기존 frontend 변경

CronJob 등록과 문서 ingestion 자동화는 후속 자동화 milestone에서 수행한다. M3는 CronJob이 안전하게 반복 호출할 수 있는 멱등 명령과 운영 예시를 제공한다.

## 3. 실제 데이터 기준

2026-08-01에 실제 `fail-history`를 조회한 결과는 다음과 같다.

- OpenSearch documents: 505
- raw metadata triples: 390
- canonical triples: 390
- 단일 문서 triple: 298
- triple당 최대 문서 수: 7

단일 문서 triple도 Wiki Concept로 생성한다. 근거가 한 건이라는 사실을 `evidence_count: 1`과 `evidence_scope: single_source`로 숨김없이 표시한다. 기존 LLM의 self-rated confidence 값을 임의로 낮추거나 덮어쓰지는 않는다.

## 4. 구성 요소

```text
OpenSearch fail-history
        │
        ▼
sync_wiki.py
        │
        ▼
wiki_sync.py
변경 triple 탐지·실행 조정
        │
        ├── wiki_job_store.py
        │   MongoDB job/lease/retry/lock
        │
        ├── wiki_manifest.py
        │   성공 fingerprint mirror
        │
        └── 기존 합성 경로
            synthesize_concept_from_docs()
            wiki_store.upsert_concept()
                    │
                    ▼
            M2 Markdown Materializer
```

### 4.1 `sync_wiki.py`

운영자와 CronJob이 실행하는 CLI다.

```bash
python sync_wiki.py --check
python sync_wiki.py --apply --limit 10
python sync_wiki.py --resume --limit 10
```

### 4.2 `wiki_sync.py`

- OpenSearch composite aggregation과 pagination
- canonical triple별 문서 조회
- snapshot과 fingerprint 계산
- manifest 비교와 change set 생성
- MongoDB job 등록
- 기존 합성기 호출과 Concept 저장
- batch 종료 후 M2 materialization 한 번 실행

### 4.3 `wiki_job_store.py`

- `wiki_sync_jobs` collection
- deterministic job ID와 unique index
- job atomic claim
- job lease, retry, terminal failure
- `wiki_sync_locks` collection의 전역 실행 lease

### 4.4 `wiki_manifest.py`

- `.yield-wiki/manifest.json` 읽기
- 전체 manifest의 same-directory temp write와 atomic replace
- 성공한 triple 결과 기록
- Concept fingerprint를 이용한 manifest 복구

## 5. Canonical triple 계약

Concept canonical key는 기존 계약을 유지한다.

```text
product + cause_oper + normalized fail_type
```

기존 bootstrap에 이미 사용 중인 fail type 정규화만 공통 함수로 분리한다.

```text
EASY(W)     → EASY
IDSAT(I)    → IDSAT
GATE_OX(G)  → GATE_OX
JUNCTION(J) → JUNCTION
```

Product와 Cause Operation은 OpenSearch의 exact metadata를 사용한다. canonical job key와 Concept key에는 normalized fail type을 쓰지만 fingerprint에는 OpenSearch 원본 fail type을 포함한다.

이 규칙은 자연어 의미를 추정하지 않는다. 기존 metadata suffix 계약만 한 곳에서 재사용하며 keyword/phrase 분기나 LLM 출력 보정 규칙을 추가하지 않는다.

## 6. OpenSearch scan

기존 중첩 terms aggregation은 각 level의 size 제한 때문에 전체 triple을 누락할 수 있다. M3는 composite aggregation과 `after_key` pagination을 사용한다.

```json
{
  "size": 0,
  "aggs": {
    "triples": {
      "composite": {
        "size": 500,
        "sources": [
          {"product": {"terms": {"field": "product.keyword"}}},
          {"fail_type": {"terms": {"field": "fail_type.keyword"}}},
          {"cause_oper": {"terms": {"field": "cause_oper"}}}
        ]
      }
    }
  }
}
```

문서 fetch는 scoring이 없는 `bool.filter`와 exact fields를 사용한다. `_source`는 fingerprint와 기존 합성기에 필요한 필드로 제한하며 embedding은 요청하지 않는다.

## 7. Fingerprint 계약

각 문서는 다음 필드를 canonical JSON으로 직렬화해 SHA-256을 계산한다.

```text
doc_id
content
cause
action
comment
date
source_file
product
fail_type
cause_oper
```

`embedding`은 제외한다. 같은 문서 내용으로 vector만 재생성된 경우 Wiki 의미가 바뀌지 않았으므로 재합성하지 않는다.

동일 canonical triple의 document snapshot은 `doc_id`, document fingerprint 순으로 정렬한다. 정렬된 snapshot을 다시 canonical JSON으로 직렬화해 triple fingerprint를 계산한다. OpenSearch 결과 순서는 fingerprint에 영향을 주지 않는다.

## 8. Change detection

| 현재 상태 | 이전 manifest | 판정 | 동작 |
| --- | --- | --- | --- |
| 존재 | 없음 | `new` | 합성 job 생성 |
| 존재 | fingerprint 동일 | `unchanged` | skip |
| 존재 | doc IDs 동일, fingerprint 변경 | `changed` | 재합성 job 생성 |
| 존재 | 이전 doc ID 일부 누락 | `source_removed` | stale + Review |
| 없음 | 이전에 존재 | `source_removed` | stale + Review |

문서가 추가되면 기존 문서와 신규 문서를 모두 다시 기존 합성기에 전달한다. 증분 문서만 이어 붙이지 않고 triple 전체 근거를 다시 합성해 Concept의 일관성을 유지한다.

## 9. MongoDB job 계약

Database는 기존 `yield_agent`를 사용하고 collection만 추가한다.

```json
{
  "_id": "sha256(canonical_triple_key + source_fingerprint)",
  "triple_key": "4SS|PRE METAL CLN|EASY",
  "product": "4SS",
  "fail_type": "EASY",
  "cause_oper": "PRE METAL CLN",
  "source_fingerprint": "sha256:...",
  "source_doc_ids": ["FH-000238", "FH-000243"],
  "doc_count": 2,
  "change_type": "new",
  "status": "pending",
  "attempts": 0,
  "lease_owner": null,
  "lease_until": null,
  "next_retry_at": null,
  "last_error": null,
  "created_at": "UTC datetime",
  "updated_at": "UTC datetime"
}
```

상태 전이는 다음과 같다.

```text
pending
  → running
      → succeeded
      → failed(attempts < 3, next_retry_at 설정)
      → terminal_failed(attempts == 3)

running + expired lease
  → 다른 worker가 running으로 재claim
```

job claim은 `find_one_and_update` 한 번으로 수행한다. 동일 triple/fingerprint job은 deterministic `_id`로 중복 등록되지 않는다.

우선순위는 다음과 같다.

1. retry 가능한 failed 또는 lease-expired 작업
2. 기존 Concept의 changed 작업
3. 신규 triple
4. 동일 그룹에서는 doc count 내림차순, canonical key 오름차순

## 10. Global sync lock

job별 lease만 사용하면 두 CronJob이 서로 다른 Concept를 동시에 저장해 Vault single-writer 계약을 위반할 수 있다. `wiki_sync_locks` collection에 단일 전역 lease를 둔다.

```text
lock ID: incremental-wiki-sync
owner: 실행별 UUID
lease_until: UTC datetime
```

`--apply`와 `--resume`은 먼저 global lease를 atomic claim한다. 이미 유효한 lock이 있으면 새 실행은 작업을 변경하지 않고 `already_running`으로 종료한다. 작업 처리 중 lease를 갱신하고 종료 시 자신이 소유한 lock만 해제한다. 프로세스가 종료되면 만료 후 다음 실행이 회수한다.

## 11. Concept crash-recovery metadata

성공한 Concept에는 다음 metadata를 원자적으로 함께 저장한다.

```yaml
source_fingerprint: sha256:...
source_doc_ids:
  - FH-000238
  - FH-000243
evidence_count: 2
evidence_scope: multiple_sources
sync_job_id: sha256:...
```

처리 순서는 다음과 같다.

```text
LLM 합성
→ Concept + source_fingerprint atomic 저장
→ manifest atomic 갱신
→ MongoDB job succeeded
```

Concept 저장 후 manifest 갱신 전에 프로세스가 종료될 수 있다. 다음 실행은 Concept의 fingerprint가 목표 fingerprint와 같으면 LLM을 호출하지 않고 manifest와 MongoDB 상태만 복구한다.

## 12. Manifest 계약

```json
{
  "schema_version": 1,
  "index": "fail-history",
  "updated_at": "UTC datetime",
  "triples": {
    "4SS|PRE METAL CLN|EASY": {
      "source_fingerprint": "sha256:...",
      "source_doc_ids": ["FH-000238", "FH-000243"],
      "evidence_count": 2,
      "concept_id": "concept:4SS|PRE METAL CLN|EASY",
      "concept_version": 2,
      "last_success_at": "UTC datetime"
    }
  }
}
```

Manifest는 실행 queue의 원본이 아니다. 실행 상태의 원본은 MongoDB이며 manifest는 Vault와 함께 이동 가능한 성공 결과 mirror다.

## 13. CLI 동작

### `--check`

- OpenSearch와 manifest만 읽는다.
- MongoDB job, lock, Vault를 변경하지 않는다.
- new/changed/source_removed/unchanged 수와 대상 triple을 출력한다.

### `--apply`

- global sync lock을 획득한다.
- OpenSearch를 scan한다.
- new/changed job을 idempotent upsert한다.
- source_removed를 stale + Review로 반영한다.
- 기존 retry/lease-expired job부터 `--limit`만큼 처리한다.
- batch에 성공한 Concept가 있으면 M2 materializer를 한 번 실행한다.
- global lock을 해제한다.

### `--resume`

- global sync lock을 획득한다.
- OpenSearch를 다시 scan하거나 새 job을 등록하지 않는다.
- MongoDB의 retry 가능한 job만 `--limit`만큼 이어서 처리한다.
- global lock을 해제한다.

`--check`, `--apply`, `--resume`은 mutually exclusive다. `--limit`은 `--apply`와 `--resume`에서만 허용하고 양의 정수여야 한다.

## 14. Bootstrap 연계

`bootstrap_wiki_warmup.py`는 삭제하지 않고 다음 용도로 유지한다.

- 새 프로젝트/Vault 최초 구축
- Vault 전체 복구
- Prompt 또는 Wiki 구조 변경 후 전체 강제 재생성
- 운영자가 지정한 전체/부분 강제 재생성

bootstrap과 sync는 같은 canonical triple 함수, snapshot/fingerprint 함수, 기존 합성 함수를 공유한다. bootstrap의 Concept 저장도 source fingerprint metadata와 manifest 성공 항목을 기록한다. 따라서 정상 bootstrap 직후 sync는 같은 triple을 재합성하지 않는다.

M3는 운영자가 삭제 Review를 확인한 뒤 한 Concept만 명시적으로 재생성할 수 있도록 bootstrap에 세 exact filter를 함께 받는 선택 실행을 추가한다.

```bash
python bootstrap_wiki_warmup.py --apply \
  --product 4SS \
  --fail-type EASY \
  --cause-oper "PRE METAL CLN"
```

세 filter는 모두 함께 제공해야 하며 일부만 제공하면 실행하지 않고 CLI 오류를 반환한다. 이 경로도 기존 합성 함수를 그대로 사용한다.

현재처럼 manifest가 없는 기존 Vault는 첫 sync에서 해당 triple을 new로 본다. Concept에 source fingerprint가 없기 때문에 실제 근거를 다시 합성하여 M3 계약으로 승격한다.

## 15. Source removal 안전성

이전 doc ID 중 하나라도 현재 snapshot에서 사라지면 자동 재합성하거나 삭제하지 않는다.

```text
source_removed
→ Concept status: stale
→ deterministic Review 생성
→ 누락 doc IDs와 감지 시각 기록
→ manifest의 마지막 성공 결과 유지
```

Review 파일명은 removal event의 canonical triple과 이전/현재 doc ID 집합으로 만든 deterministic hash를 사용한다. 동일 삭제 event를 반복 scan해도 Review는 하나만 존재한다.

이 파일은 시스템이 생성하는 Review 요청 껍데기다. 시스템은 생성 시 target, missing doc IDs, detected_at, `status: pending`만 기록한다. 이후 status, reviewer, 검토 의견은 운영자 소유이며 sync는 기존 Review 파일을 덮어쓰지 않는다.

운영자 확인 후 기존 bootstrap의 특정 triple 강제 재생성 경로로 현재 근거를 반영한다. M3는 Review 승인 UI나 자동 삭제를 구현하지 않는다.

## 16. Error handling

- OpenSearch scan 실패 시 job과 Vault를 변경하지 않는다.
- MongoDB 연결 실패 시 `--apply`/`--resume`은 합성을 시작하지 않는다.
- Manifest JSON이 손상됐으면 빈 manifest로 간주하지 않고 명시적으로 실패한다.
- LLM 합성 실패는 job에 기록하고 retry 가능 상태로 둔다.
- Concept 저장 실패 시 manifest와 succeeded 상태를 기록하지 않는다.
- Manifest 저장 실패 시 Concept fingerprint를 다음 실행의 복구 근거로 사용한다.
- Materializer 실패 시 run을 실패로 보고하고 기존 Markdown 파일의 원자적 저장 계약을 유지한다.
- 오류 문자열에는 자격 증명, 전체 LLM 응답, 전체 source content를 기록하지 않는다.

## 17. 테스트 전략

구현은 TDD로 진행한다.

### 17.1 Pure unit tests

- canonical fail type과 triple key
- 문서 순서 독립적인 fingerprint
- embedding 변경 무시
- content/cause/action/comment 변경 감지
- doc ID 추가와 삭제 판정
- manifest serialize/atomic replace/idempotency
- corrupted manifest fail-closed

### 17.2 MongoDB integration tests

- deterministic job upsert
- 같은 job 중복 등록 방지
- atomic claim
- lease 만료 전 중복 claim 방지
- lease 만료 후 reclaim
- retry와 terminal failure
- global lock의 획득·갱신·소유자 해제

### 17.3 Temporary Vault integration tests

- new/changed Concept 저장
- source fingerprint metadata
- manifest 성공 기록
- Concept fingerprint 기반 manifest 복구와 LLM 미호출
- source_removed stale + deterministic Review
- batch materializer 1회 호출
- bootstrap과 sync의 canonical/fingerprint 공유

### 17.4 Actual end-to-end

1. 실제 OpenSearch composite scan에서 505 documents와 390 canonical triples를 확인한다.
2. `--check` 전후 MongoDB와 Vault snapshot이 동일한지 확인한다.
3. 실제 MongoDB에서 job upsert, atomic claim, lease, succeeded를 확인한다.
4. 사용자 승인된 무료 LLM API로 doc count 상위 3개 triple을 실제 합성한다.
5. Concept, manifest, MongoDB, M2 Markdown graph가 모두 갱신됐는지 확인한다.
6. 같은 명령 재실행에서 이미 완료한 fingerprint가 다시 LLM 호출되지 않고,
   `--limit`에 남은 queue가 있으면 다음 batch만 처리되는지 확인한다. 전체 queue가
   소진된 뒤 재실행에서는 LLM 호출과 Markdown 변경이 모두 0인지 확인한다.
7. Concept 저장 후 manifest 미기록 상태를 재현해 LLM 호출 없이 복구되는지 확인한다.
8. 실제 Obsidian Graph에서 새 Product/Fail/Operation/Concept/Source 연결을 확인한다.

## 18. 완료 조건

- 최초 구축은 기존 bootstrap이 계속 담당한다.
- sync는 OpenSearch의 신규·수정 triple만 기존 합성기에 전달한다.
- embedding-only 변경은 LLM 호출을 만들지 않는다.
- source removal은 자동 삭제·자동 재합성을 만들지 않는다.
- 중단된 job은 lease 만료 후 resume된다.
- 동시 CronJob은 하나만 Vault를 쓴다.
- Concept fingerprint로 manifest 장애 구간을 복구한다.
- 실제 상위 3개 triple E2E가 OpenSearch, MongoDB, 무료 LLM, Vault, Obsidian까지 통과한다.
- 기존 frontend, API response shape, OpenSearch mapping과 embedding은 변경되지 않는다.
