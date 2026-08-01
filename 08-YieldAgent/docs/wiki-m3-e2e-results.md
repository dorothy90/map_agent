# M3 Incremental Wiki Sync E2E Results

- 실행일: 2026-08-01 (Asia/Seoul)
- branch: `feat/obsidian-wiki-platform`
- OpenSearch index: `fail-history`
- MongoDB database: `yield_agent`
- 외부 Vault: `/Users/daehwankim/SYLDAIX/YieldWiki`
- 사전 백업: `/Users/daehwankim/SYLDAIX/YieldWiki.backup-20260801-m3-preapply`
- 검증 provider: process-only `kilo-auto/free` (`anonymous` placeholder)

## Live source scan

실제 `OpenSearchWikiScanner`의 composite pagination 결과:

```text
documents=505
canonical_triples=390
single_source=298
max_docs_per_triple=7
```

## Read-only check

명시적으로 외부 Vault를 지정해 실행했다.

```bash
WIKI_VAULT_PATH=/Users/daehwankim/SYLDAIX/YieldWiki \
WIKI_REQUIRE_EXTERNAL_VAULT=true \
uv run --with opensearch-py python sync_wiki.py --check
```

첫 적용 전 결과:

```text
status=checked
new=390
changed=0
source_removed=0
unchanged=0
```

실행 전후 Vault 전체 파일 SHA-256 tree hash는 모두 다음 값으로 같았다.

```text
50f8c916d0faaf6397ad1656d68f23b80842709ce83549878108101f6c44f018
```

MongoDB도 실행 전후 `wiki_sync_jobs=0`, `wiki_sync_locks=0`으로 같았다.

## Top-three actual apply

무료 provider는 코드나 `.env` 기본값을 바꾸지 않고 해당 프로세스의
`wiki_summarizer.get_llm`만 교체했다. 비민감 테스트 데이터에만 사용했다.

```text
status=completed
enqueued=390
succeeded=3
failed=0
materialized=true
```

`--limit 3`은 이번 실행에서 처리할 batch 크기다. 따라서 나머지 387개가 다음 Cron
실행을 위한 pending으로 남는 것이 정상이다.

성공한 실제 Concept:

| Triple | Evidence | Body chars | Fingerprint |
| --- | ---: | ---: | --- |
| `4SS|BG CMP|JUNCTION` | 4 | 1954 | Concept = manifest = MongoDB |
| `4SS|ILD CMP|IDSAT` | 5 | 2267 | Concept = manifest = MongoDB |
| `4SS|PRE METAL CLN|EASY` | 7 | 2728 | Concept = manifest = MongoDB |

적용 후 상태:

```text
manifest entries=3
MongoDB pending=387
MongoDB succeeded=3
sync --check: unchanged=3, new=387, changed=0, source_removed=0
```

세 Concept는 모두 외부 Vault의 `concepts/` 아래에 있고 `status: active`,
`source_fingerprint`, `source_doc_ids`, `evidence_count`, `evidence_scope`,
`sync_job_id`가 기록됐다.

## Obsidian Markdown graph verification

외부 Vault에서 materializer preview를 다시 실행한 결과:

```text
created=0 modified=0 deleted=0 errors=0
```

세 Concept와 Product/Fail/Operation/Source 노트의 `[[wikilink]]`가 생성됐다. 이 E2E에서는
Obsidian GUI를 자동 조작하지 않았으므로 시각적 Graph 화면 검증을 주장하지 않는다.
운영자는 해당 Vault를 Obsidian에서 열고 Graph view에서 새 Concept 연결을 확인한다.

## Crash recovery

운영 Vault 자체를 훼손하지 않고 외부 Vault 복사본과 UUID 접미사의 실제 MongoDB test
collection을 사용했다. 성공한 `4SS|BG CMP|JUNCTION` manifest entry만 복사본에서 제거한
후 동일 job을 resume했다.

```text
status=completed
recovered=1
LLM calls=0
materializer calls=0
job status=succeeded
fingerprint repaired=true
```

Concept 저장 후 manifest 저장 전에 중단되는 구간이 Concept frontmatter fingerprint로
복구됨을 확인했다. test collection은 검증 종료 시 삭제했다.

## Environment findings

전역 Python에는 `langchain 1.3.11`, `langchain-core 0.3.86`,
`langchain-openai 0.3.0`이 혼재해 `langchain.verbose` 오류가 발생했다. 저장소의 선언에
맞는 `uv run` 환경(`langchain 1.2.9`, `core 1.2.9`, `openai 1.1.7`)에서는 문제가
재현되지 않았다.

또한 기존 코드가 사용하는 `opensearch-py`가 `pyproject.toml`과 `requirements.txt`에
선언돼 있지 않다. 사용자의 지시에 따라 M3와 무관한 의존성 선언은 수정하지 않았고,
E2E에서는 `uv run --with opensearch-py`로 프로세스에만 제공했다.

최초 진단 실행에서는 worktree에서 `.env`가 발견되지 않아 repository fallback Vault가
선택됐다. 해당 실행 전에 외부 Vault는 변경되지 않았고, fallback Vault에 생성된 파일은
원복 후 `/tmp/yield-wiki-m3-misroute-recovery`에 보존했다. 실행 전 0건이었던 임시 운영
job collection도 0건으로 복구한 뒤, 최종 E2E는 `WIKI_VAULT_PATH`와
`WIKI_REQUIRE_EXTERNAL_VAULT=true`를 명시해 다시 수행했다.

## Operational state

- Cron은 설치하지 않았다. `sync_wiki.py --apply --limit 10` 명령과 runbook 예시만 제공한다.
- 외부 Vault 백업은 복구를 위해 유지한다.
- 387개 pending job은 후속 Cron batch에서 처리한다.
- 기존 frontend, API response shape, OpenSearch mapping, embedding은 변경하지 않았다.
