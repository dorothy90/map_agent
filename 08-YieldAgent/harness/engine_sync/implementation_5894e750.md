# PR-4b 1단계 실행 결과

Hermes `5894e750c3dba980d43b25aa29e3958149ba2c61`을 원본 바이트와 모드 그대로 이식한 뒤 기존 목적의 패치 4개만 적용했다.

## 갱신 및 검증

- dry-run 및 적용: 추가 609, 변경 1,446, 삭제 74. 최종 엔진 파일 2,656개(메타 제외).
- 원본과 바이트가 다른 파일은 패치 대상 4개뿐이며, 2,656개 전체의 모드 비트가 원본과 일치한다.
- 실행권한 관련 변경: `tools/bot_desktop/launcher.sh`를 원본 모드로 반영.
- `.env.example`은 이름만 기록하고 열거나 복사하지 않았다. 범위 밖 18,245개도 가져오지 않았다.
- 재비교: 동일 2,652, patched_local 4, changed 0, added_in_scope 0, deleted 0, added_out_of_scope 18,245.
- 무결성: 일치 2,656, 불일치 0, 누락 0, 목록외 0.
- 임시 원본 복사본에서 결합 패치 `git apply --check -p1` 성공. patch_check의 파일 전체 및 각 hunk도 모두 성공.
- 지정 pytest 명령: 기존 13개 + 신규 13개 = 26 passed. DB 연결 없음.
- import 스모크: hermes_bootstrap / model_tools / run_agent 모두 성공. native_worker와 동일하게 engine을 sys.path 맨 앞에, harness를 뒤에 추가했다. 임시 HOME/HERMES_HOME, 비밀 없는 최소 환경, .env 접근·네트워크·자식 프로세스·임시 HOME 밖 쓰기 차단 상태에서 실행했다. 오류 없음.

## 패치 내용과 결정

1. `hermes_bootstrap.py`: `_legacy_post_swap_invocation` 및 handoff 호출/SystemExit 블록, 시작 시 패키지 활성화·prepare_launch·복구·재실행 블록 제거. 기존 주석과 `harden_import_path(str(_root))` 유지. 새 `_settle_interrupted_update`는 정의를 원본 그대로 보존하고 모듈 로드 시 자동 호출만 제거했다. 중단된 자체 업데이트 복구/재실행을 막는 기존 run_agent 패치와 동일한 목적이며 최소 편집이다. `_pin_launcher_home`, `RelaunchExit`, 기타 부트스트랩 함수는 보존했다. 엔진 내부 import 검색에서 사용하는 `_happy_eyeballs_create_connection`도 보존했다.
2. `model_tools.py`: `discover_plugins()` 자동 호출을 기존 주석과 `pass`로 대체.
3. `pm/extras.py`: 선택 의존성이 없을 때 질문·설치·환경 전환 대신 기존과 같은 `InstallError` 발생.
4. `run_agent.py`: `_early_recovery` import와 복구/재실행 부트스트랩 제거, 기존 주석 유지.

새 플러그인의 확장자가 기존 동적 범위를 넓히는 현상을 발견했다. PR-4a의 범위를 `scope_5894e750.json`으로 보존하여 기존 compare의 scope_rules/in_scope/classify를 재사용한다. 이 결정으로 `eslint.config.shared.mjs`, `flake.lock`, `uv.lock`은 원래대로 범위 밖에 남는다. 기존 엔진에 있던 확장자 없는 LICENSE는 원본과 동일하게 보존한다.

UPSTREAM.json의 바깥 키 순서, 2칸 들여쓰기, 끝 개행, adaptations를 유지했다. 기존 files가 완전한 정렬 상태는 아니므로 살아남은 키의 상대 순서를 유지하고 신규 키만 경로순으로 덧붙였다. files는 모두 원본 해시, local_files는 패치 후 4개 해시다. native_hermes.py는 UPSTREAM_COMMIT 값 한 줄만 변경했다.

기존 테스트의 임시 git init/add/commit을 모의 읽기 응답으로 바꿔 Git 쓰기 없이 기존 커밋 검증·추적 파일 선택 시나리오를 유지했다. 패치 분류 관련 기존 기대값도 patched_local로 갱신했다.

## gitignore 목록

`git check-ignore -v`에서 실제로 무시되는 새 파일(엔진 기준):

- `plugins/hermes-achievements/dashboard/dist/index.js` — `.gitignore:6`, `dist/`
- `plugins/hermes-achievements/dashboard/dist/style.css` — 같은 규칙
- `plugins/kanban/dashboard/dist/index.js` — 같은 규칙
- `plugins/kanban/dashboard/dist/style.css` — 같은 규칙
- `plugins/platforms/photon/sidecar/.gitignore` — `.gitignore:127`, `.gitignore`

추가로 `--no-index` 규칙 검사에서는 `pm/pyproject.toml`, `pyproject.toml`도 `.gitignore:86`의 `pyproject.toml`에 해당한다. 이 두 파일은 이미 추적 중이므로 실제 무시 대상은 아니다. 파일과 ignore 규칙은 그대로 두었고 git add를 하지 않았다.

## 재현 및 자료

08-YieldAgent에서:

```sh
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync sync
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync --dry-run
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync --apply
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -p no:cacheprovider harness/tests/test_engine_sync.py -q
```

sync의 기본은 dry-run이다. 인자 없는 기존 명령은 비교 보고서를 갱신한다. `--apply`는 원본 복사 단계이므로 재실행하면 로컬 패치도 원본으로 덮어쓴다. 이후 패치 재생성과 메타데이터 갱신이 필요하다.

- 적용 전 자료: report_5894e750_pre_sync.json/.md, upstream_pre_sync.json, patches_pre_sync.diff
- 실행 목록: sync_5894e750_dry_run.json, sync_5894e750_applied.json
- 적용 후 자료: report_5894e750.json/.md, gitignore_5894e750.json, import_smoke_5894e750.json

## 미확인 사항

Mongo·실제 모델 호출·도구 실행을 포함한 전체 harness 통합은 실행하지 않았다. import 단계에서 발견한 호환 오류는 없으나 native_worker/native_budget/native_stop_gate/executor의 전체 런타임 호환성을 보장하지 않는다. 설치·네트워크·서버 실행·Git 쓰기는 하지 않았다. 기존 사용자 변경 및 허용 범위 밖 구현 파일은 수정하지 않았다.
