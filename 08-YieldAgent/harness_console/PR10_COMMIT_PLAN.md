# PR-10 커밋 계획 제안 (실행하지 않음)

기준: 2026-10-25 작업 트리. `/tmp/pr10_git_full.txt` 16,887줄 및 `/tmp/pr10_git_summary.txt`를 입력으로 사용하고 `git status --short -uall`, `git ls-files`, `git check-ignore -v`로 재확인했다. 아래는 리뷰용 제안이다. **실제 커밋·push·git add는 수행하지 않았다.** 현재 단계는 테스트·문서만 추가/수정했다.

최종 재확인 `/tmp/pr10_final_status.txt`: 16,896경로 = M 1,472 / D 75 / ?? 15,349. harness_console은 1,943경로 모두 ??이다. 입력 스냅샷과 시점이 다르며, 증가분 전체를 PR10이 만든 변경으로 간주하지 않는다.

## 현재 상태와 제외

- harness_console 전체는 아직 추적되지 않는다. 저장소 루트에서 `git ls-files 08-YieldAgent/harness_console` 결과 **0건**(프로젝트 cwd에서 `git ls-files harness_console`도 0건). 이력상 과거 커밋 존재 여부는 허용된 git 명령으로 조사하지 않았다. 사용자 인계상 아직 한 번도 커밋되지 않은 상태다.
- `repl_agent/frontend/node_modules/` **11,882개 untracked**는 루트 ignore에 걸리지 않는다. `git check-ignore -v repl_agent/frontend/node_modules/.package-lock.json`은 일치 규칙 없음. 이 항목은 **이번 작업 범위 밖 기존 상태**다. **`git add -A` 및 `git add .` 금지**. 선별 경로만 검토한다.
- `outputs/`, `.superpowers/`, `.codex/`, 캐시·개인 런타임·스크린샷 등 비작업 산출물은 제외 권고. 기존 변경/삭제를 이 작업에서 복구하거나 포함 결정하지 않는다.
- `harness_console/.gitignore`의 1/2/4행은 각각 node_modules/, dist/, screenshots/를 제외한다. 해당 하위 예시 경로를 `git check-ignore -v`로 확인했다. `.gitignore` 파일 자체는 루트 규칙 때문에 별도 추적 여부 확인이 필요하다.

## 권장 단위와 순서

파일 수는 현재 status의 변경 경로 수다(전체 디렉터리 파일 수 아님). M/D/??는 수정/삭제/미추적이며 ignored 엔진 5개는 status에 없으므로 별도다. 행 간 경로를 중복 집계하지 않는다. 경로는 프로젝트 cwd 기준이다. 테스트 명령은 **사용자 실행 계획**이며 Mongo/모델 테스트를 이번 작업에서 실행한 뜻이 아니다.

| 순서 / 제목 제안 | 포함 경로·패턴 | M | D | ?? | 선행 의존 / 해당 단위 검증 |
|---|---|---:|---:|---:|---|
| 1 `Sync bundled Hermes to 5894e750` | harness/engine/**, harness/engine_sync/** | 1448 | 74 | 621 | pin/patch 검토; engine_sync hash 검증 및 test_engine_sync.py, 아래 MD5 비교. ignored 5개 별도 |
| 2 `Connect authenticated gateway services` | harness/gateway/**, harness/*.py, agent_server.py | 6 | 0 | 25 | 1; py_compile, 사용자 환경 gateway/harness pytest. 운영 인증은 아직 미확정 |
| 3 `Cover gateway contracts and rollback` | harness/tests/** | 6 | 0 | 33 | 1~2; 사용자 환경 전체 harness pytest, 신규 PR10 Mongo opt-in/cleanup 포함 |
| 4 `Add harness console build infrastructure` | package*.json, tsconfig.json, vite/vitest*.config.ts, index.html | 0 | 0 | 8 | lockfile 차이 검토; tsc, npm test, build (src 반입 후 전체 검증) |
| 5 `Port protocol and web host adapters` | src/protocol/**, src/platform/** | 0 | 0 | 54 | 4; 관련 Vitest, tsc |
| 6 `Port Hermes UI sources and tests` | src/hermes/** | 0 | 0 | 1813 | 4~5; 전체 Vitest, 원본 hash/patch·제외 감사 대조 |
| 7 `Compose opt-in web UI and Yield results` | src/web/**, src/yield/** | 0 | 0 | 26 | 5~6 및 기존 console 진입점 단위; 관련 Vitest, npm test, build |
| 8 `Add integration and browser fixtures` | tests-integration/**, e2e-web/**, e2e/** | 0 | 0 | 14 | 2~7; Python py_compile, 사용자가 통합/실제 모델·브라우저·DB 정리 검증 |
| 9 `Document porting and rollback gates` | harness/*.md, harness_console/*.md | 3 | 0 | 14 | 전 단위; PB/PR8/9/10 문서와 실제 결과·pin 대조. 루트 밖 계획 2개는 패치 적용 대기이며 이 수에 미포함 |
| 별도 `Add baseline console and audit records` | console의 위 행 외 src/*, audit/review JSON 등 | 0 | 0 | 19 | 4~7과 함께 리뷰; 기존 App 회귀 npm test, main-optin, build. 모두 untracked라 기존 파일 수정처럼 누락하지 않음 |

4~7은 상호 import가 있으므로 독립 checkout의 build가 꼭 필요하면 4~7과 baseline console 단위를 한 커밋으로 묶는다. 제목/분리 방식은 대환 님 결정이다. 프론트가 모두 untracked이므로 PR9a “원본 이식”과 PR9b/9c “수정”은 Git 상태만으로 구분할 수 없고 감사 JSON·PR 문서의 원본 출처를 함께 검토해야 한다. 이 표가 파일별 변경의 정확성이나 기존 작업의 승인까지 뜻하지 않는다.

### 기존 M/D는 별도 검토 단위

다음은 선행 작업의 tracked 변경이다. 위 엔진/서버/테스트/문서 행에 포함된 M을 다시 더하지 않는다. `common.py`, `map_agent.py`, `yield_viz.py`, harness/tools 3개, harness/skills 2개는 위 단위 밖 기존 변경이다. 작성자가 내용을 임의 수정하지 않았다.

- 서버 M 6: agent_server.py, harness/config.py, harness/domain_metrics.py, harness/native_hermes.py, harness/native_worker.py, harness/store.py.
- 서버 테스트 M 6: test_defect_yield.py, test_domain_metrics.py, test_map_coverage.py, test_native_hermes.py, test_native_transport.py, test_review_core_boundaries.py.
- 문서 M 3: harness/MIGRATION.md(기존), harness/README.md, harness/INTERNAL_PORTING_LUNA.md(PR10 문구 정정 포함).
- 별도 기존 기능 변경 M 8: common.py, map_agent.py, yield_viz.py, harness/tools/{defect_tools,map_tools,yield_tools}.py, harness/skills/{wafer-map-analysis,yield-analysis}/SKILL.md. 관련 기능의 원래 테스트·실데이터 검증은 담당자가 확인 후 별도 커밋한다.
- 범위 밖 M 1: 저장소 루트 `.env.example`(status 이름만 확인, 읽기/수정 안 함). 범위 밖 D 1: `.superpowers/brainstorm/50632-1783829435/state/server-info`. 제외 권고.

## ignored 엔진 파일 5개

재확인 결과 루트 `.gitignore:6:dist/`가 dashboard 4개를, `.gitignore:127:.gitignore`가 sidecar 파일을 제외한다. 원본 번들 보존을 위해 해당 5개는 선별 강제 추가가 필요하다. 아래 명령은 **프로젝트 cwd에서 대환 님이 검토 후 실행할 정확한 명령**이며 이번에는 실행하지 않았다.

```sh
git add -f -- \
  harness/engine/plugins/kanban/dashboard/dist/index.js \
  harness/engine/plugins/kanban/dashboard/dist/style.css \
  harness/engine/plugins/hermes-achievements/dashboard/dist/index.js \
  harness/engine/plugins/hermes-achievements/dashboard/dist/style.css \
  harness/engine/plugins/platforms/photon/sidecar/.gitignore
```

## 루트 .gitignore 충돌 — 대환 님 결정

18행 `<<<<<<< HEAD`, 32행 `=======`, 36행 `>>>>>>> 947e80f76d1f2718060427952a62538bb42b60ce`가 남아 있다. 파일은 수정하지 않았다. 아래는 각각의 규칙을 그대로 인용한다.

HEAD 쪽(19~31행): 특정 wafer 이미지 12개와 SQL-Agent notebook 1개를 개별 제외한다.

```gitignore
07-Supervisor/usecase/binmap_4SS4UR9_20260202_225447.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_225839.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_225917.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_230649.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_230809.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_231033.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_231352.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_231418.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_232222.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_232249.png
07-Supervisor/usecase/binmap_4SS4UR9_20260202_232409.png
07-Supervisor/usecase/cummap_4SS4UR9_20260202_231442.png
10-Use-Cases/04-LangGraph-SQL-Agent.ipynb
```

상대 쪽(33~35행, 첫 빈 줄 포함): 모든 `.mmd` 파일을 제외한다.

```gitignore

# mermaid
*.mmd
```

의견: 개별 산출물 제외와 Mermaid 제외는 서로 충돌하는 규칙이 아니므로 두 쪽을 모두 보존할 수 있다. 다만 `.mmd`가 관리할 문서 소스라면 전체 제외는 너무 넓을 수 있고 notebook도 추적 의도를 확인해야 한다. 두 쪽의 필요성과 node_modules 제외 추가는 대환 님이 별도로 결정하고 충돌 표식을 제거해야 한다. PR10에서 자동 해결하지 않는다.

## 커밋 전 체크리스트

- [ ] 루트 .gitignore 충돌과 범위 밖 node_modules 처리 결정. 선별 경로 목록 재검토.
- [ ] 보호 파일·서버/엔진의 이번 작업 변경 없음 확인, 기록한 pin과 원본 hash/patch 대조.
- [ ] 비밀 파일/키 포함 여부를 **사용자가** 확인. 아래는 향후 명령이며 이번에 .env를 읽지 않았다. 파일명만 출력하는 검색으로 비밀 값을 로그에 남기지 않는다. grep의 미검출만으로 비밀이 없다고 보장하지 않는다.

```sh
# 저장소 루트에서 추적 파일명 점검 (staging 전후 사용자 실행)
git ls-files | grep -Ei '(^|/)(\.env($|\.)|id_rsa|id_ed25519)|\.(pem|key)$'
# 검토할 소스만, .env/scripts/벤더/산출물 제외, 내용 대신 파일명 출력
grep -rIlE 'BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9]{20,}|(api_key|password|secret)[[:space:]]*=[[:space:]]*["\x27][^"\x27]+' \
  --exclude='.env*' --exclude-dir=scripts --exclude-dir=node_modules \
  --exclude-dir=engine --exclude-dir=dist --exclude-dir=__pycache__ \
  08-YieldAgent/harness 08-YieldAgent/harness_console/src
```

- [ ] 엔진 MD5: `/tmp/pr6_before_engine.md5` 형식은 `hash harness/engine/상대경로`다. 프로젝트 cwd에서 같은 정렬·파일 집합으로 다음과 같이 생성해 비교한다. 저장 시점 이후 의도한 엔진 동기화 차이인지 분리한다. PR10 전후 무수정 보장은 동일한 시점 baseline끼리 비교해야 하며, 과거 PR6 baseline과의 불일치가 곧 PR10 수정은 아니다. 파일 목록 변화/ignored 5개/삭제 74개도 확인한다.

```sh
find harness/engine -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print | LC_ALL=C sort |
while IFS= read -r path; do printf '%s %s\n' "$(md5 -q "$path")" "$path"; done > /tmp/pr10_engine.md5
diff -u /tmp/pr6_before_engine.md5 /tmp/pr10_engine.md5
```

- [ ] 큰 파일: `find harness harness_console -type f -size +10M ! -path '*/node_modules/*' ! -path '*/dist/*' ! -path '*/screenshots/*' -print` 결과를 검토한다. 번들에 필요한 dist 4개는 별도 확인한다.
- [ ] 전체 검증(사용자 환경): 프로젝트 루트에서 `../.venv/bin/python -m pytest harness/tests -q`, console에서 `npx tsc --noEmit -p .`, `npx vitest run`, `npm test`, `npm run build`. 신규 Mongo 테스트는 HARNESS_MONGO_URI가 필요하며 없거나 연결 불가하면 skip. 기존 Mongo 테스트에는 skip이 없는 파일도 있다.
- [ ] integration/실제 모델/화면은 tests-integration/README.md·PR9B_RUNBOOK.md에 따라 사용자가 수행. yield_frontend의 기존 `npm test`도 별도 수행.
- [ ] PR10 작성자 검증은 console tsc·src/web Vitest·npm test·build, Python py_compile까지만이다. 실제 배포 승인과 동일시하지 않는다.
- [ ] `/tmp/pr10_historical_pin.patch`의 루트 밖 역사 계획 두 줄 주석을 검토·적용한다. 샌드박스 쓰기 제한으로 미적용이다.
