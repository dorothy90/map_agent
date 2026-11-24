# 기능을 기준으로 이식 파일 찾기

기준 커밋의 기존 프로그램이 어떤 파일을 통해 동작하는지 정리했다. 아래에서 `H`는 `08-YieldAgent/harness/`, `F`는 `08-YieldAgent/harness_console/`이다. **실제 복사 대상은 FILES.tsv의 원래 전체 경로를 따른다.** CHECKPOINT-144.tsv는 기존 앱 체크포인트 `58ad8ee7`에서 손댄 파일의 이력이다.

## 1. 어느 쪽 코드인지 구분한다

| 범위 | 파일 또는 폴더 | 역할과 이식 시 판단 |
|---|---|---|
| Hermes 원본 | H/engine/ 및 UPSTREAM.json, LICENSE, PATCHES.diff | 고정 Hermes 배포를 원형으로 가져온 부분. UPSTREAM.json에 실린 2,656개 원본 파일을 바이트 단위로 검사한다. 사내에 다른 버전이 있으면 소스 경로를 분리하고 핀을 선택한다. 엔진 내부에 업무 도구를 직접 끼워 넣을 필요가 없다. |
| 업무 채팅 연결 | H/hermes_chat.py, hermes_chat_worker.py, native_tools.py | worker가 직원 프로필의 state.db에서 Hermes 세션을 재개하고 전용 도구 6개를 등록한다. Hermes가 선택 응답과 도구 이력을 모델에 전달하고 최종 답변을 작성한다. |
| 앱 공통 상태 | H/control.py, executor.py, types.py, store.py | 세션·실행·결과 식별자, 취소와 소유권, 결과 저장, 산출물 URL의 연결. 소유자를 특정하는 사내 계층이 이미 있으면 그 계약과 맞춘다. |
| 웹 API 및 세션 | H/app.py, router.py, gateway/{routes,services,auth,session_rpc,session_store,events,execution}.py, gateway/manifest.json | HTTP·WebSocket 연결, 세션 목록·제목·이력, 사용자 선택에 대한 응답, 진행 상태 및 최종 답변. 사내 인증과 외부 개발용 고정 사용자 설정을 구분한다. |
| 스킬·기억 | H/skill_catalog_worker.py, hermes_chat_worker.py, tools/load_wafer_maps.py | 실제 프로필 스킬을 읽어 목록에 표시한다. 기억한 웨이퍼 그룹은 모델이 LOT·웨이퍼 쌍으로 만들어 맵 도구에 전달한다. H/skills.py와 옛 고정 스킬은 현재 커밋에서 제거됨. 사용자 정의 스킬 내용은 별도 반입 대상으로 03에 수록. |
| 프로필·저장 이력·공용 검증 | H/native_hermes.py, native_verification.py, native_budget.py | 프로필과 저장된 결과에 쓰이는 공용 코드다. 웹 채팅과 모델 평가는 H/hermes_chat.py를 통해 Hermes 원본으로 실행한다. |
| 실행 경계 | H/isolation/, H/runtime/, H/config.py, H/dev/ | 전용 도구가 생성 코드를 실행하는 경계, 기존 프로그램의 환경설정, 검증용 게이트웨이. 우선 소스를 같은 경로에 대응시키고 사내 OS·인증 설정은 사내에서 병합한다. |

## 2. 전용 도구 여섯 개

`H/tools/registry.py:domain_registry`가 도구를 모은다. `H/native_tools.py:register_yield_tools`가 Hermes에 등록하고 `H/executor.py`가 사용자·세션에 맞춰 실행한다. MCP 서버를 띄워 도구를 노출하는 경로는 이번 일반 채팅의 진입점이 아니다.

| 공개 이름 | 구현 파일 | 같이 확인할 의존성과 데이터 |
|---|---|---|
| list_tables | H/tools/list_tables.py | Oracle 딕셔너리와 앱 계정의 가시성. 실제로 읽을 수 있는 사내 테이블·뷰 기준과 비교 |
| query_db | H/tools/query_db.py | common.py의 Oracle 연결, SQL 조회 범위, 결과 원본 및 result_id |
| read_result | H/tools/python_tools.py | H/store.py와 결과 페이지. preview와 전체 표 구분 |
| 웹 채팅 `execute_code` / 기존 호출 `run_python` | H/hermes_chat_worker.py, H/runtime/session_kernel.py, H/tools/python_tools.py | 일반 채팅에서는 직원·세션별 Python 실행 환경을 사용한다. 기존 도구를 직접 호출하는 경로는 호출별로 실행한다. |
| render_yield_table | H/tools/table_artifact.py | yield_report_data.py, yield_report_scope.py, report_policy.py, wafer_group_report.py 및 폴더 밖 yield_viz.py·yield_table_renderer.py. 사내 파라미터명·단위·분모와 맞춰야 함 |
| load_wafer_maps | H/tools/load_wafer_maps.py | map_aggregate.py, map_aggregate_plot.py, map_bin_plot.py, bin_mapping.py. 누적 맵과 저장된 LOT별 웨이퍼 개별 맵 각각의 계약을 보존 |

수율일보는 별도의 일곱 번째 도구를 추가한 기능이 아니다. 사용자 프로필의 yield-daily-report/SKILL.md가 기존 도구의 보고서 입력을 사용한다. H/tools/report_analysis.py가 동일 제품과 기준일로 월·주·일 표를 만들고 report_scatter.py와 report_bin_maps.py가 산점도와 검사별 BIN 맵을 만든다. 스킬 파일만 옮기거나 표 렌더러만 가져가면 전 기능이 이어지지 않는다.

### 채팅별 Python 상태와 다운로드 첨부

백엔드의 `H/control.py`가 직원과 채팅 ID별로 Python 프로세스를 관리한다. `H/hermes_chat_worker.py`가 Hermes의 `execute_code` 호출을 `H/hermes_chat.py`로 전달하고, `H/runtime/session_kernel.py`가 해당 직원의 OS 격리 환경에서 실행한다. 같은 프로세스가 살아 있는 동안 다음 질문에서도 변수와 import를 재사용한다. 초기화, 유휴 종료, 서버 재시작 후에는 변수를 다시 만들어야 한다. 웹 백엔드를 여러 프로세스로 배포한다면 같은 채팅의 후속 요청을 같은 프로세스로 보내야 이 동작이 이어진다.

웨이퍼 맵을 엑셀로 만들 때는 `read_result`가 저장된 좌표 표 전체를 분석용 CSV로 전달할 수 있다. 분석 가상환경에 `openpyxl`과 `Pillow`를 설치한다. Python이 `publish_directory`에 저장한 파일은 `H/runtime/output_files.py`에서 수집하고 `H/store.py`에서 직원·채팅에 귀속시킨다. `H/gateway/generated_files.py`와 웹의 `F/src/platform/web-host.ts`를 함께 이식해야 답변의 첨부 버튼으로 파일을 받을 수 있다. [이식 후 검수](04-acceptance.md#web-python-delivery)

<a id="shared-files"></a>

### 공용 수율 폴더와 Hermes 도구 5개

Ubuntu 백엔드에서 `HARNESS_SHARED_YIELD_DIR`에 공용 수율 자료 디렉터리의 절대 경로를 지정하고 서비스를 다시 시작한다. 예를 들어 `/srv/yield/shared-inputs`를 미리 만들고 서비스 계정에 디렉터리 탐색 및 파일 읽기 권한을 준다. 설정을 비워두면 서버가 체크아웃 루트 아래 `.runtime/shared-yield/`를 만든다. 채팅에는 그 안의 상대 경로를 전달한다.

| 도구 | 웹 채팅에서의 동작 |
|---|---|
| `read_file` | 게시한 공용 파일을 읽는다. |
| `search_files` | 공용 폴더 안에서 파일명과 내용을 검색한다. |
| `vision_analyze` | 공용 폴더의 PNG·JPEG 등 래스터 이미지를 연다. 모델 공급자가 이미지 처리를 지원할 때 제공된다. |
| `delegate_task` | 같은 프로필과 모델에서 작업을 위임한다. 자식은 허용된 수율·공용 파일 도구로 작업하고 결과는 부모 대화에 귀속된다. |
| `todo_list` | Hermes 자동 검색으로 불러와 현재 대화의 할 일을 관리한다. |

`H/hermes_chat_worker.py`가 원본 검색 방식을 적용한다. 파일 범위 검사는 `H/shared_file_tools.py`, 위임 권한은 `H/web_delegation.py`, 공용 경로 설정은 `H/config.py`와 `H/hermes_chat.py`에 있다. 파일 도구는 심볼릭 링크를 따라가지 않는다. 임시 복사량은 호출당 64 MiB, 디렉터리 검색은 하위 항목 10,000개까지다. 생성된 표·맵은 로그인 사용자와 세션에 귀속되는 기존 저장소에 보관한다.

### 일반 채팅에서 도구를 찾는 방식

직원에게 허용된 수율 도구를 먼저 등록하면 Hermes가 `tool_search`, `tool_describe`, `tool_call`을 제공한다. 도구 이름을 이미 아는 모델은 `tool_describe`로 인자를 확인하고 `tool_call`로 실행할 수 있다. 이 세 도구를 사내 수율 레지스트리에 추가할 필요는 없다.

기존 직원 프로필도 함께 이식할 때는 `H/native_hermes.py`의 프로필 준비 코드와 `H/hermes_chat_worker.py`의 세션 재개 코드를 함께 병합한다. 프로필 준비 코드는 예전 하네스가 설정한 `enabled: off`와 `defer: []` 조합을 한 번 해제한다. 세션 재개 코드는 예전 대화에 저장된 도구 목록을 현재 직원 권한에 맞춘다.

누적 맵 설명은 `H/tools/load_wafer_maps.py`의 도구 소개와 `H/tools/map_aggregate.py`의 `group_oper` 입력 설명에 있다. 모델은 사용자가 쓴 제조 공정명·ID로 먼저 조회하고, 공정 하나를 식별할 수 없을 때 해당 제품의 공정 목록을 확인하도록 안내받는다. 맵 도구는 공정명은 대소문자를 무시하고 ID는 정확히 대조한다. 맵 검사명은 별도 인자 `oper`로 전달한다.

스킬 저장에는 Hermes 원본 `skill_manage`를 사용한다. 저장 시점은 모델이 판단하고 새 스킬 설명의 길이는 원본 검증기가 검사한다. 이번 이식에서 추가하는 스킬 자동 생성 규칙이나 길이 보정 코드는 없다.

## 3. 기존 프론트와 테스트

| 경로 | 보존할 기능 |
|---|---|
| F/src/main.tsx → F/src/web/entry.tsx → HermesWebApp.tsx | 실제 웹 진입점, 세션 목록, 새 대화, 기존 채팅 화면 구성 |
| F/src/web/WebSessionRow.tsx, use-web-session-list.ts, session-labels.ts, sidebar.css | 사이드바의 세션 목록·제목·선택·로딩/실패 |
| F/src/web/execution-trace.tsx, F/src/hermes/components/assistant-ui/thread/ 및 tool/ | 진행 중 설명, 완료 기록 접기, 보낸 메시지의 시간·복사 동작, 모델 응답 표시 순서 |
| F/src/web/ProductPicker.tsx, SkillsCatalog.tsx, SkillsCatalog.css | DB 제품 선택, 프로필 스킬 목록과 새 스킬 진입 |
| F/src/web/wafer-map-gallery.tsx, wafer-map-data.ts, web.css | 여러 맵의 표시와 확대, 응답 폭, 실제 산출물 연결 |
| F/src/platform/, src/protocol/, src/hermes/ | 기존 Hermes UI 이식 코드와 수율 웹 호스트 어댑터. 이 폴더 전체를 미수정 원본으로 간주하지 않는다. |
| F/package.json, package-lock.json, tsconfig*, vite.config.ts, index.html | 현재 프론트의 빌드·경로 계약을 설명하는 기존 입력 파일. 사내 앱의 기존 루트 설정과 충돌 시 프론트 하위 프로젝트 단위로 유지 |
| H/tests/, F/src/**/*.test.*, F/tests-integration/ | 역할별로 FILES.tsv에서 test 필터. 사내 기존 모듈과 병합 후 어떤 기능이 달라졌는지 확인하는 근거 |

## 4. 폴더 밖 파일과 개별 처리

- 필수 공용 모듈은 08-YieldAgent/common.py(Oracle 연결), bin_mapping.py(BIN·PASS), yield_viz.py, yield_table_renderer.py(표·표현), wafer_zones.py(좌표 구역)다. 앱과 같은 상대경로의 Python import가 해소되는지 확인한다.
- 루트의 pyproject.toml·uv.lock·.python-version, 기존 scripts/setup_hermes.sh와 .env.example은 기존 작업의 일부로 목록에 포함했다. 지금 단계에서 사내 서비스 전체의 의존성 잠금 파일이나 .env를 덮어쓸 필요는 없다.
- 외부 작업 폴더에만 있던 seed_wf_all_prb_v2.py, seed_wt_activeparam_v2.py, sync_param_kind_lists.py 및 sql/DF_WF_ALL_PRB_V2.sql, DF_WF_LEGEND.sql, DF_WF_LEGEND_WT_ACTIVEPARAM_V2.sql은 개발 더미 DB 적재/정리 작업이다. 별도 표시한 참고 항목이며 기준 커밋 앱의 파일 목록에는 없다. 일부 적재 스크립트는 기존 테이블을 비운다. 사내 운영 DB에는 실행하지 않는다. 실제 앱이 읽는 테이블·열 계약은 03과 check_oracle_contract.sql로 대조한다.
- .runtime 프로필, MongoDB, Oracle 데이터는 Git 파일과 별도로 처리한다. 기존 인증 키·로컬 .env, node_modules, .venv, 캡처·로그·개발 메모·OS별 패키지는 소스 이식 대상 밖이다.
- F의 구형 계획 문서나 옛 yield_frontend에 적힌 구조가 실제 소스와 다르면 `F/src/main.tsx`와 현재 도구 레지스트리, import 경로를 우선한다. 커스텀 사내 에이전트는 기존 위치를 보존하고 대응표에 기록한다.
