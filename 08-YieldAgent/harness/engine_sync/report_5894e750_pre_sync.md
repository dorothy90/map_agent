# 엔진 동기화 비교 보고서

## 요약

- Hermes 커밋: `5894e750c3dba980d43b25aa29e3958149ba2c61`; clean: `True`
- 기존 UPSTREAM pin: `e473f5a9c976a0b5bc292aa415dae28c638a47c3`

| 분류 | 개수 |
|---|---:|
| 동일 | 601 |
| 변경 | 1446 |
| 추가 in-scope | 609 |
| 추가 out-of-scope | 18245 |
| 삭제 | 74 |

추가는 Hermes에만 있는 파일, 삭제는 엔진에만 있는 파일입니다.
범위: 엔진이 포함한 최상위 폴더 아래 전체 또는 엔진 전체 파일에 존재하는 확장자의 최상위 파일.
확장자 없는 새 최상위 파일은 out-of-scope입니다.
out-of-scope는 전체 경로를 나열하지 않고 최상위 폴더별 개수만 기록합니다.

| out-of-scope 폴더 (`.` = 최상위 파일) | 개수 |
|---|---:|
| `.` | 25 |
| `.github` | 80 |
| `apps` | 4403 |
| `assets` | 10 |
| `contributors` | 2137 |
| `docker` | 21 |
| `evals` | 236 |
| `locales` | 35 |
| `native` | 5 |
| `nix` | 24 |
| `optional-mcps` | 66 |
| `optional-skills` | 854 |
| `plugin-catalog` | 802 |
| `scripts` | 232 |
| `skills` | 331 |
| `tests` | 7220 |
| `tests-js` | 44 |
| `ui-tui` | 632 |
| `web` | 250 |
| `website` | 838 |

## 패치 호환성

| 파일 | 전체 적용 가능 | Hermes가 pin 원본과 동일 | 충돌 hunk |
|---|---|---|---|
| `hermes_bootstrap.py` | False | False | `@@ -489,33 +489,8 @@` |
| `model_tools.py` | True | False | 없음 |
| `pm/extras.py` | True | False | 없음 |
| `run_agent.py` | True | False | 없음 |

파일 전체 및 개별 hunk는 각각 변경 전 원본 복사본에서 `git apply --check -p1`로 검사했습니다.
실제 적용은 수행하지 않았습니다. 개별 통과가 조합 적용 또는 런타임 호환성을 보장하지 않습니다.

## 로컬 패치 파일

| 파일 | 패치 후 기록 일치 | Hermes 대비 상태 |
|---|---|---|
| `hermes_bootstrap.py` | True | 변경 |
| `model_tools.py` | True | 변경 |
| `pm/extras.py` | True | 변경 |
| `run_agent.py` | True | 변경 |

## 변경 파일 상위 20개

줄 수 방향: 현재 엔진 → Hermes. UTF-8 텍스트만 계산하며 1 MiB 초과/바이너리는 null.

| 파일 | 추가 줄 | 제거 줄 |
|---|---:|---:|
| `gateway/run_turn.py` | 136 | 2105 |
| `package-lock.json` | 928 | 1129 |
| `tui_gateway/server.py` | 82 | 1582 |
| `gateway/run_turn_runner.py` | 476 | 1059 |
| `hermes_cli/update_lock.py` | 1336 | 100 |
| `tools/browser_tool.py` | 5 | 1361 |
| `gateway/run.py` | 168 | 1084 |
| `gateway/platforms/api_server.py` | 330 | 852 |
| `gateway/run_notifications.py` | 351 | 596 |
| `hermes_cli/_early_recovery.py` | 858 | 74 |
| `agent/auxiliary_client.py` | 440 | 455 |
| `hermes_cli/cli_commands_mixin.py` | 136 | 703 |
| `plugins/platforms/discord/adapter.py` | 436 | 392 |
| `agent/conversation_compression.py` | 311 | 482 |
| `agent/context_compressor.py` | 283 | 509 |
| `hermes_state_messages.py` | 375 | 397 |
| `hermes_cli/update_cmd.py` | 560 | 209 |
| `hermes_cli/backup.py` | 326 | 435 |
| `gateway/run_inbound.py` | 215 | 529 |
| `hermes_cli/config.py` | 255 | 456 |

## 무결성

- 일치 2121, 불일치 0, 목록외 0, 기록 파일 누락 0
- local_files 패치 후 해시를 우선하고, 나머지는 files 원본 해시와 비교합니다.
- engine_matches_pin은 local_files가 아닌 files 원본 해시와 비교한 별도 값입니다.
- 불일치 경로: 없음 (생략 0개)
- 누락 경로: 없음 (생략 0개)
- 엔진 해시 2121개 전체: [JSON](report_5894e750.json)의 engine_hashes.
- 제외: __pycache__, *.pyc, .git, .env/.env.*. UPSTREAM.json·PATCHES.diff는 engine_meta로 별도 표시.

## 재현 명령

저장소 08-YieldAgent 디렉터리에서:

```sh
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync --check-only
```

## 한계·미확인 사항

- e473f5a9 객체가 없는 얕은 클론이므로 두 커밋의 직접 diff가 아닙니다. 현재 엔진 바이트와 Hermes 작업트리를 비교합니다.
- pin 원본에 대한 판단은 UPSTREAM.json 해시에 의존합니다. 원본 패치 재구성이나 의미적 검증은 하지 않았습니다.
- Git 입력은 추적 파일만 읽습니다. clean=false이면 보고서는 커밋 자체가 아닌 수정된 작업트리 상태를 반영합니다.
- 일반 디렉터리 입력은 commit/clean=null이며 커밋 검증을 할 수 없습니다.
- 제외한 환경 파일은 열람하지 않았습니다. 바이너리 판정은 NUL/UTF-8 해독 가능 여부를 사용합니다.
- 소스·패치 내용은 출력하지 않습니다. DB·LLM·엔진 실행, 의존성 설치 및 실제 패치 적용은 범위 밖입니다.
