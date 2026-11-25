# 기능을 기준으로 이식 파일 찾기

기준 커밋의 기존 프로그램이 어떤 파일을 통해 동작하는지 정리했다. 아래에서 `H`는 `08-YieldAgent/harness/`, `F`는 `08-YieldAgent/harness_console/`이다. FILES.tsv에는 기준 커밋 당시의 전체 경로가 들어 있다. 현재 브랜치를 이식할 때는 체크아웃에 존재하는 파일을 사용한다. CHECKPOINT-144.tsv는 기존 앱 체크포인트 `58ad8ee7`에서 손댄 파일의 이력이다.

## 1. 어느 쪽 코드인지 구분한다

| 범위 | 파일 또는 폴더 | 역할과 이식 시 판단 |
|---|---|---|
| Hermes 원본 | H/engine/ 및 UPSTREAM.json, LICENSE, PATCHES.diff | 고정 Hermes 배포를 원형으로 가져온 부분. UPSTREAM.json에 실린 2,656개 원본 파일을 바이트 단위로 검사한다. 사내에 다른 버전이 있으면 소스 경로를 분리하고 핀을 선택한다. 엔진 내부에 업무 도구를 직접 끼워 넣을 필요가 없다. |
| 업무 채팅 연결 | H/hermes_chat.py, hermes_chat_worker.py, native_tools.py | worker가 직원 프로필의 state.db에서 Hermes 세션을 재개하고 전용 도구 6개를 등록한다. Hermes가 선택 응답과 도구 이력을 모델에 전달하고 최종 답변을 작성한다. |
| 앱 공통 상태 | H/control.py, executor.py, types.py, store.py | 세션·실행·결과 식별자, 취소와 소유권, 결과 저장, 산출물 URL의 연결. 소유자를 특정하는 사내 계층이 이미 있으면 그 계약과 맞춘다. |
| 웹 API 및 세션 | H/app.py, router.py, gateway/{routes,services,auth,session_rpc,session_store,events,execution}.py, gateway/manifest.json | HTTP·WebSocket 연결, 세션 목록·제목·이력, 사용자 선택에 대한 응답, 진행 상태 및 최종 답변. 사내 인증과 외부 개발용 고정 사용자 설정을 구분한다. |
| 스킬·기억 | H/skill_catalog_worker.py, hermes_chat_worker.py, tools/load_wafer_maps.py | 실제 프로필 스킬을 읽어 목록에 표시한다. 기억한 웨이퍼 그룹은 모델이 LOT·웨이퍼 쌍으로 만들어 맵 도구에 전달한다. H/skills.py와 옛 고정 스킬은 현재 커밋에서 제거됨. 사용자 정의 스킬 내용은 별도 반입 대상으로 03에 수록. |
| 공용 프로필·이력·검증 | H/native_hermes.py, chat_tool_helpers.py, native_verification.py, native_budget.py | 직원 프로필과 저장된 대화 이력을 읽고, 수율 데이터 형식과 모델 사용량을 확인한다. 웹 채팅과 모델 평가는 모두 H/hermes_chat.py를 통해 실행한다. |
| 실행 경계 | H/isolation/, H/runtime/, H/config.py, H/dev/ | 전용 도구가 생성 코드를 실행하는 경계, 기존 프로그램의 환경설정, 검증용 게이트웨이. 우선 소스를 같은 경로에 대응시키고 사내 OS·인증 설정은 사내에서 병합한다. |

## 2. 수율 채팅의 조회·계산 도구

`H/tools/registry.py:native_domain_registry`가 도구를 모은다. `H/native_tools.py:register_yield_tools`가 Hermes에 등록하고 `H/executor.py`가 사용자·세션에 맞춰 실행한다. MCP 서버를 띄워 도구를 노출하는 경로는 이번 일반 채팅의 진입점이 아니다.

### 다른 에이전트에 MCP 도구 노출

다른 에이전트에 도구를 노출할 때만 프로젝트 루트에서 `PYTHONPATH=08-YieldAgent .venv/bin/python -m harness.mcp_server`를 실행한다. `domain_registry`가 MCP에 여는 도구는 `list_tables`, `query_db`, `read_result`, `render_yield_table`, `load_wafer_maps`의 5개다. 웹 채팅의 `native_domain_registry`는 여기에 `query_lot_history`를 더해 전용 수율 도구 6개를 등록한다. MCP에는 `query_lot_history`와 별도 Hermes 도구인 `execute_code`가 없다. 같은 세션을 유지하려면 `HARNESS_MCP_SESSION_ID`를 쓴다.

### 웹 채팅 도구와 구현 파일

| 공개 이름 | 구현 파일 | 같이 확인할 의존성과 데이터 |
|---|---|---|
| list_tables | H/tools/list_tables.py | Oracle 딕셔너리와 앱 계정의 가시성. 실제로 읽을 수 있는 사내 테이블·뷰 기준과 비교 |
| query_db | H/tools/query_db.py | common.py의 Oracle 연결, SQL 조회 범위, 결과 원본 및 result_id |
| read_result | H/tools/python_tools.py | H/store.py와 결과 페이지. preview와 전체 표 구분 |
| execute_code | H/hermes_chat_worker.py, H/hermes_chat.py, H/runtime/session_kernel.py | Hermes의 Python 셀 실행기를 직원·채팅별로 관리한다. query_db의 전체 CSV 또는 read_result의 python_csv를 읽고 표·차트·다운로드 파일을 반환한다. |
| render_yield_table | H/tools/table_artifact.py | yield_report_data.py, yield_report_scope.py, report_policy.py, wafer_group_report.py 및 폴더 밖 yield_viz.py·yield_table_renderer.py. 사내 파라미터명·단위·분모와 맞춰야 함 |
| query_lot_history | H/tools/lot_history.py | LOT별 FDC·Q-time·트러블·향후 조치·샘플 분할. 요청 종류만 읽고 알람 기간과 후속 맵 범위를 구분 |
| load_wafer_maps | H/tools/load_wafer_maps.py | map_aggregate.py, map_aggregate_plot.py, map_bin_plot.py, bin_mapping.py. 누적 맵과 저장된 LOT별 웨이퍼 개별 맵 각각의 계약을 보존 |

사용자 프로필의 yield-daily-report/SKILL.md가 기존 도구의 보고서 입력을 사용한다. H/tools/report_analysis.py가 동일 제품과 기준일로 월·주·일 표를 만들고 report_scatter.py와 report_bin_maps.py가 산점도와 검사별 BIN 맵을 만든다. 스킬 파일만 옮기거나 표 렌더러만 가져가면 전 기능이 이어지지 않는다.

`query_db`는 limit을 생략하면 마지막 행까지 읽어 CSV와 DB 결과에 저장한다. 표시 미리보기, 명시적 limit, 조회 오류를 구분한다. `render_yield_table`의 결과가 본문과 화면의 공통 근거이며, 기간별 Δ는 같은 단위의 최근 버킷과 직전 버킷 차이다.

`H/tools/map_matrix*.py`, `map_matrix.html`, `map_page_result.py`는 요청한 행·열별 좌표 집계와 보이는 셀의 표시를 담당한다. `H/map_jobs.py`, `map_job_worker.py`, `gateway/map_completion.py`는 집계가 대기 시간을 넘으면 원래 직원·세션으로 결과를 보낸다. 프론트 맵 갤러리, 게이트웨이 완료 이벤트, Mongo 작업 저장소까지 함께 옮긴다.

### 채팅별 Python 상태와 다운로드 첨부

백엔드의 `H/control.py`가 직원과 채팅 ID별로 Python 프로세스를 관리한다. `H/hermes_chat_worker.py`가 Hermes의 `execute_code` 호출을 `H/hermes_chat.py`로 전달하고, `H/runtime/session_kernel.py`가 해당 직원의 OS 격리 환경에서 실행한다. 같은 프로세스가 살아 있는 동안 다음 질문에서도 변수와 import를 재사용한다. 초기화, 유휴 종료, 서버 재시작 후에는 변수를 다시 만들어야 한다. 웹 백엔드를 여러 프로세스로 배포한다면 같은 채팅의 후속 요청을 같은 프로세스로 보내야 이 동작이 이어진다.

웨이퍼 맵을 엑셀로 만들 때는 `read_result`가 저장된 좌표 표 전체를 분석용 CSV로 전달할 수 있다. 분석 가상환경에는 엑셀·이미지용 `openpyxl`·`Pillow`와 Word·PowerPoint용 `python-docx`·`python-pptx`를 설치한다. Python이 `publish_directory`에 저장한 파일은 `H/runtime/output_files.py`에서 수집하고 `H/store.py`에서 직원·채팅에 귀속시킨다. `H/gateway/generated_files.py`와 웹의 `F/src/platform/web-host.ts`를 함께 이식해야 답변의 첨부 버튼으로 파일을 받을 수 있다. [이식 후 검수](04-acceptance.md#web-python-delivery)

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

직원은 개인 작업 폴더 `/workspace/`에서 `write_file`로 텍스트 파일을 만들고 `patch`로 수정한다. `read_file`과 `search_files`도 이 경로를 지원한다. 상대 경로는 기존 공용 폴더를 읽는다. `H/personal_file_tools.py`를 포함하고 같은 직원의 프로필 아래 `web-files`를 유지하면 다른 채팅에서 파일을 다시 열 수 있다. 공용 폴더는 읽기 전용이다.

### 사이드바에서 채팅을 프로젝트별로 모으기

`desktop_project`의 `create`, `switch`, `list`로 현재 채팅을 프로젝트에 넣거나 소속과 목록을 확인한다. `H/web_projects.py`는 직원 프로필의 `web-projects`에 프로젝트를 저장하며, 채팅의 소속은 하네스 DB의 해당 직원 세션에 저장한다. 이식할 때 두 저장소와 웹 사이드바를 함께 옮긴다. 프로젝트에는 서버 폴더 경로를 지정하지 않는다.

### 채팅의 일회성·반복 예약 관리

직원은 `cronjob_manage`로 현재 채팅에 일회성 작업, 일정 간격의 반복 작업, 특정 요일·시각의 작업을 예약한다. 같은 직원의 같은 채팅에서 `list`로 예약 ID를 찾고 `update`로 이름·실행할 요청·일정·반복 횟수를 수정한다. `pause`로 정지한 예약은 `resume`으로 재개할 수 있다. 세 동작 모두 기존 예약 ID를 유지한다. `remove`로 취소하면 이후 회차는 실행되지 않으며 이미 접수된 회차는 마칠 수 있다. 원본 Hermes의 시간 해석과 작업 저장 형식을 사용하며 직원 프로필의 `web-schedules`에 보관한다. 이식할 파일은 `H/web_cron_tool.py`, `H/web_cron_dispatch.py`, `H/web_cron_worker.py`다. 달력 예약에는 `croniter`가 필요하다.

`skills`에는 직원 프로필에 설치된 스킬 이름을 순서대로 저장하고, `prompt`에는 제품·검사 등 해당 예약에 필요한 조건을 저장한다. 예를 들어 수율일보를 이식할 때는 이 설명서의 `profile-seed/skills/yield-daily-report`를 사용할 직원의 프로필에 설치한다. `run`은 기존 예약 ID로 즉시 실행을 접수한다. 백엔드는 예약한 직원과 채팅을 확인한 뒤 별도의 Hermes 실행 세션에서 현재 스킬 본문과 예약 조건을 원본 방식으로 조합한다. 직접 실행과 정시 실행 모두 결과를 예약한 웹 채팅에 표시한다.

같은 회차는 한 번만 접수한다. 직접 실행도 완료 횟수에 포함되며 반복 상한에 도달하면 예약이 종료된다. 매일 오전 8시인 무제한 예약을 오전 8시 이후에 한 번 실행하면 다음 예약은 다음 날 오전 8시이고 잔여 횟수에는 상한이 없다. 브라우저 탭을 닫아도 결과를 같은 채팅에서 다시 열 수 있다. 서버가 꺼져 여러 회차를 놓치면 기한이 지난 작업을 한 번 실행하고 다음 예약 시각으로 넘어간다. 사내 검증 시 두 직원 계정으로 결과 채팅과 표의 접근 권한을 각각 확인한다.

사내 공유 서버에서는 `app.state.gateway`에 `GatewayServices`를 초기화하고 인증을 담당하는 authority 라우터 하나에 `schedule_lifespan=gateway_schedule_lifespan`을 전달한다. `H/web_cron_worker.py`도 함께 배포한다. 로컬 개발 서버의 `H/dev/local_gateway_app.py`는 같은 연결 방식을 사용하며 고정된 개발 직원 계정만 처리한다.

웹 예약은 정지 상태 생성과 사유, 모델 고정, 원래 채팅으로의 전달, 이전 회차 또는 같은 채팅의 다른 예약 결과 참조, 실행에 사용할 도구 묶음 제한, 후속 대화 이력에 결과 포함을 지원한다. 서버 스크립트 실행 관련 옵션 `script`, `monitor`, `no_agent`, 서버 작업 경로 `workdir`는 제공하지 않는다.

### 채팅 간에 이어지는 개인 불량 아이템

웹 채팅의 `kanban_create`, `kanban_list`, `kanban_show`, `kanban_comment`는 직원 프로필과 하네스 DB별로 분리한 원본 Hermes 칸반 SQLite 저장소를 사용한다. `H/web_kanban.py`가 LOT·웨이퍼 쌍을 구조화해 카드 기록에 보관한다. 등록·추가 시 Oracle MAP에서 해당 LOT_ID와 WF_ID 쌍과 지정된 제품·검사 범위를 확인한다. 한 쌍이라도 일치하지 않으면 이번 요청 전체를 저장하지 않는다. 동일 아이템에 같은 쌍을 다시 추가해도 장수가 늘지 않는다. 카드는 담당 에이전트 없이 보관되며 조사 작업을 예약하지 않는다.

카드의 전체 맵은 `H/tools/load_wafer_maps.py`에 카드 ID(`wafer_item_id`)를 전달해 조회한다. 저장 시 확인된 제품과 검사 범위를 재사용하며, 별도 요청이 있으면 해당 조회 조건을 따른다. `H/tools/map_bin_plot.py`가 LOT·웨이퍼별 맵을 그린다. `신규`는 해당 아이템에 마지막으로 추가된 웨이퍼를 뜻한다. 직원 채팅의 카드 표시와 다시 열기에는 채팅 워커·IPC·게이트웨이 이력 처리 및 웹 실행 기록 컴포넌트가 함께 필요하다.

`kanban_attach`는 현재 채팅에서 생성한 파일의 `artifact_id` 또는 `output_path`를 받아 카드에 복사한다. 파일당 상한은 Hermes 원본과 같은 25 MiB다. 같은 직원이 다른 채팅에서 `kanban_attachments`를 호출하면 해당 카드의 첨부 목록과 다운로드 버튼을 받는다. 서버를 이식할 때 `H/web_kanban_attachments.py`와 `H/gateway/generated_files.py`를 포함하고, 직원 프로필의 `web-kanban` 아래에 있는 `kanban.db`와 `attachments` 폴더를 함께 보관한다.

`kanban_complete`는 직원이 지정한 카드의 조사 결론을 기록하고 원본 칸반 상태를 `done`으로 바꾼다. 같은 직원은 다른 채팅에서도 `kanban_show`로 상태, 완료 결론과 등록 웨이퍼를 확인할 수 있다.

`kanban_block`과 `kanban_unblock`은 보류와 해제, `kanban_schedule`은 카드를 나중에 처리할 상태로 돌리는 용도다. 시각을 정해 실행하려면 웹 예약을 사용한다. `kanban_link`는 카드 간 선후 관계를 설정하고 `kanban_request_review`는 검토를 요청한다. `kanban_attach_url`은 현재 채팅에 속한 생성 파일 링크를 받아 첨부한다. 직원 채팅의 `kanban_request_changes`는 `review` 상태인 카드를 수정 사유와 함께 다시 작업할 수 있는 상태로 되돌린다. 부모 카드가 모두 끝났으면 `ready`, 아니면 `todo`다. `kanban_heartbeat`는 담당 워커 선점 없이 진행 메모와 시각만 남기고 카드 상태는 바꾸지 않는다.

### 일반 채팅에서 도구를 찾는 방식

직원에게 허용된 수율 도구를 먼저 등록하면 Hermes가 `tool_search`, `tool_describe`, `tool_call`을 제공한다. 도구 이름을 이미 아는 모델은 `tool_describe`로 인자를 확인하고 `tool_call`로 실행할 수 있다. 이 세 도구를 사내 수율 레지스트리에 추가할 필요는 없다.

기존 직원 프로필도 함께 이식할 때는 `H/native_hermes.py`의 프로필 준비 코드와 `H/hermes_chat_worker.py`의 세션 재개 코드를 함께 병합한다. 프로필 준비 코드는 예전 하네스가 설정한 `enabled: off`와 `defer: []` 조합을 한 번 해제한다. 세션 재개 코드는 예전 대화에 저장된 도구 목록을 현재 직원 권한에 맞춘다. 이전에 읽은 수율·예약 도구의 설명이나 인자가 달라진 경우, 모델은 Hermes의 `tool_describe`로 최신 명세를 다시 읽고 해당 도구를 실행한다.

누적 맵 설명은 `H/tools/load_wafer_maps.py`의 도구 소개와 `H/tools/map_aggregate.py`의 `group_oper` 입력 설명에 있다. 모델은 사용자가 쓴 제조 공정명·ID로 먼저 조회하고, 공정 하나를 식별할 수 없을 때 해당 제품의 공정 목록을 확인하도록 안내받는다. 맵 도구는 공정명은 대소문자를 무시하고 ID는 정확히 대조한다. 맵 검사명은 별도 인자 `oper`로 전달한다.

스킬 저장에는 Hermes 원본 `skill_manage`를 사용한다. 저장 시점은 모델이 판단하고 새 스킬 설명의 길이는 원본 검증기가 검사한다. 이번 이식에서 추가하는 스킬 자동 생성 규칙이나 길이 보정 코드는 없다.

## 3. 기존 프론트와 테스트

| 경로 | 보존할 기능 |
|---|---|
| F/src/main.tsx → F/src/web/entry.tsx → HermesWebApp.tsx | 실제 웹 진입점, 스트리밍, 세션 목록·재개, 새 대화, 기존 채팅 화면 구성 |
| F/src/web/HermesWebApp.tsx, F/src/web/web.css | 여러 질문을 모은 clarify 카드, 실행 중지 |
| F/src/hermes/components/assistant-ui/clarify/, F/src/hermes/components/assistant-ui/tool/approval.tsx | 확인 카드와 승인 카드. 확인 카드 UI는 조건 상속 커밋(`9ff7d746`)과 함께 옮긴다. |
| F/src/web/McpServers.tsx | 직원이 연결하는 MCP 서버 페이지 |
| F/src/web/entry.tsx, F/src/hermes/components/assistant-ui/embeds/frame-embed.tsx | 본문 수율 HTML 표 |
| F/src/web/WebSessionRow.tsx, use-web-session-list.ts, session-labels.ts, sidebar.css | 사이드바의 세션 목록·제목·선택·로딩/실패 |
| F/src/web/execution-trace.tsx, F/src/protocol/usage-tick.ts, F/src/hermes/components/assistant-ui/thread/ 및 tool/ | 도구 진행과 도구 카드, 토큰·시간 사용량, 완료 기록 접기, 보낸 메시지의 시간·복사 동작, 모델 응답 표시 순서 |
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
