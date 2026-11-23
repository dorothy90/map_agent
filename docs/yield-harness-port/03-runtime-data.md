# 스킬·메모리·대화·Oracle 이식

**선택 부록.** 기존 소스만 옮길 때는 이 장을 건너뛴다. 기존 업무 스킬까지 보존하려면 2절을 먼저 읽는다. 개인 이력까지 옮기는 경우에만 해당 백업 절차를 사용한다.

기준 앱은 `b28862324779fc42086b89cfdc23c216f136fbc0`이다. 앱 소스만 반입하면 새 프로필과 빈 채팅 목록으로 시작한다. Luna/Terra에 설치하는 개발 스킬과 앱 사용자가 만드는 Hermes 스킬은 저장 위치가 다르다.

|원하는 결과|진행할 단계|필요한 반입물 또는 사내 자원|
|---|---|---|
|같은 앱으로 사내 DB를 조회하고 수율일보 등 네 스킬을 호출|1, 2, 4, 5|이 브랜치의 업무 스킬, 사내 Oracle/Mongo/모델 및 사용자 인증|
|새 채팅에서 저장한 웨이퍼 그룹을 기억|1, 2 또는 3A, 4, 5|소유자가 승인한 메모. 동일 더미 DB를 재현할 때는 동봉된 예제 메모를 별도 선택|
|과거 대화와 기존 표·맵 링크까지 재개|1, 3A, 3B, 4, 5|같은 정지 시점의 사용자 프로필과 Mongo 앱 DB 백업. 소유자 및 DBA와 진행|

DB 덤프, 원본 채팅, 개인 설정, 서비스 계정과 비밀은 이식용 파일 목록에서 제외했다. 실제 이력은 조직에서 승인한 별도의 **반입 전용 데이터 매체**로 전달한다. 사내 호스트명·DDL·로그·화면·업무 수치·백업을 외부 AI나 저장소에 올리지 않는다. 아래 근거와 실행 결과도 내부에만 보관한다.

## 1. 사용자와 경로 확인

1. 운영과 별도로 쓰는 검증용 Mongo DB 이름, 실제 모델과 Oracle 읽기 계정, 새 프로필용 지속 디스크를 정한다. 재배포 후에도 동일한 사용자 프로필이 마운트되어야 한다.
2. 사내 인증 어댑터의 정확한 `principal_id`를 사용한다. 로컬 단일 사용자 게이트웨이의 대응 값은 `HARNESS_PRINCIPAL`이다. `harness.native_hermes.profile_path`와 `harness.isolation.backend.principal_hash`상 프로필 경로는 실제 체크아웃 루트 기준 `.runtime/hermes/profiles/<SHA-256(UTF-8 principal_id)>`다. OS 사용자 홈이나 `08-YieldAgent/.runtime`에 설치하면 검색되지 않는다.
3. 아래 자리표시자를 내부 실제값으로 설정한다. POSIX shell 및 Python 3.11 기준이다. 앱 서비스 계정으로 작업한다.

```sh
export APP_ROOT="/사내/검증용/yield-agent"
export PORT_DOCS="$KIT"  # 01에서 설정한 GitHub 복제본의 docs/yield-harness-port
export PRINCIPAL_ID="인증에서_받은_검증사용자ID"
PROFILE_HASH=$(python3 -c 'import hashlib,os; print(hashlib.sha256(os.environ["PRINCIPAL_ID"].encode()).hexdigest())')
PROFILE_DIR="$APP_ROOT/.runtime/hermes/profiles/$PROFILE_HASH"
umask 077
```

## 2. 업무 스킬 네 개 설치

|반입 파일 (이 문서 기준 상대경로)|역할 및 판정|
|---|---|
|`profile-seed/skills/yield-daily-report/SKILL.md`|수율일보. 월 3개월·주 4주·일 3일 표, 최근 2주 열화/개선 각 최대 3개 웨이퍼 산점도, 최대 10개 BIN의 직전주·최근주·델타 3행. 표본 부족 시 적격 항목 수와 이유 표시|
|`profile-seed/skills/yield-summary/SKILL.md`|수율 요약. 월·주·일 표 앞에 같은 조회 결과에 근거한 결론 표시|
|`profile-seed/skills/wafer-map-cummap/SKILL.md`|웨이퍼 개별·누적 맵. 원문에 개발 당시 좌표·검사 사례와 직접 SQL 대안이 있다. 사내 좌표 범위나 제품 기본값으로 고정하지 않는다. 저장 그룹 조회 시 Hermes가 메모에서 읽은 LOT·웨이퍼 쌍을 전용 도구의 `wafer_groups` 인자로 전달한다.|
|`profile-seed/skills/wafer-handoff-note/SKILL.md`|현장 메모만으로 3줄 교대 인수인계. 실데이터 없이 스킬 로딩을 확인할 때도 사용 가능|
|`profile-seed/optional-demo/MEMORY.md`|외부 더미 DB의 회오리·환형 예제 메모 두 항목. 기본 설치 제외. 동일 더미 식별자가 있는 검증 DB에서만 설치|
|`install_profile_seed.py`|대상 프로필 경로 계산, 충돌 점검, 누락된 파일 설치|
|`snapshot_profile.py`|기존 사용자 스킬 리소스/메모리/SQLite 이력 중 선택한 자료를 별도 스냅숏으로 생성|
|`check_oracle_contract.sql`|핵심 테이블/뷰 9개의 컬럼과 읽기 권한을 0행 조회로 점검|

실제 원본 4개 스킬은 각각 SKILL.md 한 파일이며 하위 스크립트·템플릿·그림은 없었다. 메타데이터와 본문을 원문 그대로 동봉했다. 추후 다른 스킬에 references/, scripts/, assets/ 등이 있으면 3A 방식으로 같은 상대경로에 복원한다.

대상 사용자의 검증 앱을 정지한 상태에서 실행한다. 첫 명령은 미리보기다. 동일한 파일은 건너뛰며 이름이 같고 내용이 다르면 설치 전에 중단한다. 사내에서 편집한 스킬과 충돌하면 새 프로필에서 두 버전을 비교하고 소유자가 선택한다.

```sh
python3 "$PORT_DOCS/install_profile_seed.py" --project-root "$APP_ROOT" --principal "$PRINCIPAL_ID"
python3 "$PORT_DOCS/install_profile_seed.py" --project-root "$APP_ROOT" --principal "$PRINCIPAL_ID" --apply
```

더미 맵 조회를 재현할 때만 위 명령에 `--with-demo-memory`를 붙여 미리보기 후 적용한다. 사내 DB에 등록된 다른 웨이퍼를 쓰는 경우에는 실제 사내 채팅으로 승인된 그룹·LOT·번호를 저장한다. 다른 직원에게 원 소유자의 메모를 일괄 설치하지 않는다.

다음은 UI와 같은 Hermes 파서로 설치를 확인하는 명령이다. 새 스테이징 사용자의 예상 결과는 네 이름과 enabled=True다. 기존 프로필은 네 이름이 있는지 및 해당 사용자의 활성화 설정을 점검한다.

```sh
(cd "$PROFILE_DIR" && HERMES_HOME="$PROFILE_DIR" HOME="$PROFILE_DIR" \
 "$APP_ROOT/.venv/bin/python" -I "$APP_ROOT/08-YieldAgent/harness/skill_catalog_worker.py") \
 | python3 -c 'import json,sys; print([(s["name"],s["enabled"]) for s in json.load(sys.stdin)["skills"]])'
```

## 3A. 기존 프로필의 일부 또는 업무 이력 전체 이동

|원본 프로필 경로|어떻게 다룰지|
|---|---|
|skills/이름/SKILL.md 및 같은 스킬의 리소스|스킬 본문, 실제 상대경로의 참조 문서·도우미까지 보존. 실제 업무 데이터·외부 주소·스크립트를 원본 측에서 검수|
|memories/MEMORY.md, memories/USER.md|각각 지속 사실과 사용자 관련 메모. 구분자·줄바꿈을 보존한다. mem_ 참조는 항목 텍스트에서 계산된다.|
|state.db, sessions/|원본 메시지·도구 인자·검색용 인덱스·사용량 등이 담긴 SQLite와 복구용 세션 기록. sessions/는 비어 있을 수 있다. 실행 중 DB 파일 하나만 복사하면 최근 WAL 내용이 빠질 수 있다.|
|config.yaml, SOUL.md|대상 앱이 환경에 맞는 설정을 만든다. 승인된 사용자 지침과 skills.disabled 또는 플랫폼별 설정만 소유자가 값 단위로 재적용한다. 원본에 있던 인증키·기기 경로·외부 스킬 디렉터리 등은 가져오지 않는다.|
|lock, 캐시, 로그, 크론·hook, 인증·페어링, 과거 백업, curator 기록|기본 업무 데이터 스냅숏에서 제외한다. 이름이 점으로 시작하는 파일을 실제로 사용하는 커스텀 스킬은 해당 리소스를 별도로 선별한다.|
|별도 격리 작업 디렉터리|대상 환경에서 새로 만든다. 공식 표/맵은 결과 ID 및 GridFS에서 제공된다. 이전 코드가 임시 파일만 남긴 경우 소유자가 그 파일을 따로 선별한다.|

1. 소유자 한 명씩 원본 프로필을 확인한다. 전체 이력을 옮길 때는 **새 제출을 막고 활성 작업을 정상 종료**한 뒤 웹·워커·CLI·배치의 쓰기를 중지한다. 대기 중인 사용자 입력·승인도 정리한다. 3B Mongo 덤프와 프로필 스냅숏이 모두 끝날 때까지 신규 쓰기를 재개하지 않는다.
2. 다음 명령은 원본 측에서 실행한다. SOURCE_PROFILE은 해당 소유자의 프로필 절대경로다. NEW_SNAPSHOT은 보안 스테이징 폴더 아래 **새 디렉터리**로 지정한다. 필요한 옵션만 선택한다. SQLite는 온라인 백업 API로 복사 후 integrity_check를 한다. 중단된 스냅숏은 복원하지 않는다.

```sh
python3 "$PORT_DOCS/snapshot_profile.py" --source "$SOURCE_PROFILE" --dest "$NEW_SNAPSHOT" \
    --skills --memory --history
test -f "$NEW_SNAPSHOT/SNAPSHOT_COMPLETE"
```

3. 사내 전송을 승인받은 자료만 반입한다. 매체에 상대경로와 SHA-256 검수 목록을 만들고 조직 승인 채널로 전달한 해시/서명과 내부에서 대조한다. SNAPSHOT_COMPLETE는 작업이 끝났다는 표시이며 매체의 서명은 아니다.
4. 사내의 **별도 검증 체크아웃**에서 앱 쓰기를 정지한다. 빈 신규 프로필에만 다음을 복원한다. INBOUND_PROFILE은 수신한 해당 사용자 스냅숏이다. 대상 디렉터리가 이미 존재하거나 복사 도중 실패하면 여기서 중단하고 기존 자료와 병합하지 않는다. 실패 시 새로운 스테이징 위치에 다시 복원한다.

```sh
(
  set -eu
  test -f "$INBOUND_PROFILE/SNAPSHOT_COMPLETE"
  mkdir -p "$(dirname "$PROFILE_DIR")"
  mkdir -m 700 "$PROFILE_DIR"
  for part in skills memories state.db sessions; do
    if test -e "$INBOUND_PROFILE/$part"; then cp -Rp "$INBOUND_PROFILE/$part" "$PROFILE_DIR/"; fi
  done
)
```

5. 원본 스냅숏 DB와 사내 복원 DB에 각각 다음을 실행해 ok와 세션·메시지 수를 대조한다. 실제 사용자 텍스트나 키를 출력할 필요가 없다. 이력을 새로 시작할 때는 빈 SQLite에 이 명령을 수행하지 않는다.

```sh
python3 - "$PROFILE_DIR/state.db" <<'PY'
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve(strict=True)
with sqlite3.connect(p.as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    print('ok', db.execute('SELECT count(*) FROM sessions').fetchone()[0],
          db.execute('SELECT count(*) FROM messages').fetchone()[0])
PY
```

동일 사용자의 로그인 ID가 사내에서 변경되는 경우 스킬·선별 메모는 새 소유자의 새 프로필로 설치할 수 있다. 과거 대화는 해시 폴더명만 변경해 복구할 수 없다. Mongo에도 권한·세션·결과·승인 소유자가 기록된다. 전체 이력에는 같은 안정된 principal_id를 매핑하고 권한 검증을 해야 한다. 내부에서 매핑을 보증할 수 없으면 새 이력으로 시작하고 원본은 승인된 내부 보관본에 유지한다.

## 3B. 과거 대화와 결과 링크를 위한 Mongo 복원

harness.store.HarnessStore는 harness_runs, harness_invocations, harness_results, harness_sessions를 사용한다. 게이트웨이에는 gateway_sessions, gateway_messages, 메시지 순번, 이벤트, 요청 영수증, pending_prompts, 승인 기록 등이 있다. 원본 행·표·이미지·HTML은 GridFS 버킷 **harness_blobs.files 및 harness_blobs.chunks**와 결과 문서의 ObjectId로 연결된다. 개별 JSON/CSV 추출로는 BSON 타입이나 첨부 링크를 잃을 수 있다.

1. 3A와 같은 정지 구간에서 MongoDB Database Tools의 mongodump/mongorestore를 사용한다. 연결 정보는 해당 위치 전용의 권한 0600인 `--config` 파일(예: 해당 환경에서 관리하는 uri 키) 또는 사내 DBA 백업 체계로 제공한다. 명령행·문서·모델 요청에 URI 비밀번호를 적지 않는다. SOURCE_DB는 실제 HARNESS_MONGO_DB이고 ARCHIVE는 새 파일이다. Database Tools와 DB 버전 호환성을 내부에서 확인한다.
2. 아래 방식은 **승인된 소유자들만 들어 있는 전용 애플리케이션 DB 전체**에 한한다. 여러 시스템/사용자의 공유 DB에서 일부 사용자만 승인된 경우 전 DB를 반입하지 않는다. 사내 DBA가 소유권과 결과·원자료·파일·청크 참조를 함께 보존하는 별도 범위 복원을 준비하거나 신규 시작을 사용한다.

```sh
(
  set -eu
  test ! -e "$ARCHIVE"
  test -n "$SOURCE_DB"
  mongodump --config="$SOURCE_DUMP_CONFIG" --db="$SOURCE_DB" --gzip --archive="$ARCHIVE"
)
```

3. 매체 검수 후 사내 **새로 할당한 빈 DB**에서 복원한다. 담당자가 실제 접속 서버와 STAGE_DB에 기존 컬렉션이 0개인지 확인한다. 운영 DB에 병합하거나 --drop을 사용하지 않는다. 사내 계정·역할·비밀번호는 대상 시스템에서 설정한다.

```sh
(
  set -eu
  test -n "$SOURCE_DB" && test -n "$STAGE_DB" && test "$SOURCE_DB" != "$STAGE_DB"
  mongorestore --config="$DEST_RESTORE_CONFIG" --gzip --archive="$ARCHIVE" \
    --nsInclude="$SOURCE_DB.*" --nsFrom="$SOURCE_DB.*" --nsTo="$STAGE_DB.*" --stopOnError
)
```

4. 서비스 재개 전 양쪽의 실제 컬렉션 이름·문서 수·인덱스 수·GridFS 파일 수와 청크 수를 대조한다. 샘플 harness_results가 가리키는 data_ref 및 artifact_id 파일이 동일 ObjectId로 열리고 크기와 바이트 SHA-256이 일치하는지 내부에서 검사한다. 활성 run은 0개여야 한다. 기존 개발 승인 규칙이 조직 운영 권한으로 작동하지 않도록 내부 담당자가 scope를 검토한다.
5. 스테이징 HARNESS_MONGO_DB를 복원 DB로 선택하고 3A의 같은 principal 프로필과 함께 시작한다. 그 계정으로 예전 대화를 목록에서 열고 기존 표·맵 링크가 실제로 표시되는지 본다. 다른 계정으로 해당 세션·결과를 조회할 때 접근이 거부되어야 한다. 감사 근거는 내부 검수 기록에만 남긴다.
6. 성공하면 절체한다. 실패하면 새 스테이징 쪽 쓰기를 중지하고 이전 라우팅·이전 DB·이전 프로필 경로를 한 세트로 되돌린다. 원본은 리허설 중 유지한다. 전환 후 새 데이터가 있으면 따로 보관하고 차분 처리 후 롤백한다.

## 4. 사내 Oracle에서 수율 기능을 재현하기 위한 입력 계약

실제 DB 계정이 아래 이름을 조회하도록 권한·스키마·뷰 또는 synonym을 구성한다. 동일해 보이는 사내 컬럼의 단위와 조인 키는 내부에서 확인한다. 실제 데이터 행·DDL을 밖으로 보내지 않는다.

|기능|테이블/뷰|판정할 의미|
|---|---|---|
|제품 목록·기간표·웨이퍼 수율|DF_WF_ALL_PRB_V2|LOT_CD 제품 / LOT_ID lot / WFID 번호 / **GORUPKEY** 실제 철자. PROCESS·PARAM_NM·VALUE 및 측정 시각, 제품+LOT+WFID+검사별 최신 재측정 선택. YEAR, MONTH, DAY, WEEK_HYNIX와 사내 달력 대조|
|기간표 파라미터|DF_ALL_PARA_PRB|제품·검사별 YIELD_TABLE='o', PARAM_SEQNO, PARAM_KIND_CD. 메타 PROC_GBN_CD와 측정 PROCESS의 ' TEST' 포함 표기 연결|
|열화·개선 산점도|DF_PARAM_META|SOURCE_ID='wafer_parameter', 제품·검사·파라미터별 UNIT, BETTER_WHEN의 higher/lower. 직전 주와 최근 주에 모두 측정된 적격 항목 수|
|웨이퍼 맵 및 BIN 맵|MAP, DF_SYMBOL_BIN|MAP.WF_ID, OPER_DET_DESC, START_TM, END_TM 및 MAP_VAL_JSON. 원본 예제 다이 문자열의 첫 필드=row(y), 둘째=col(x), 다음 좌·우 심볼. 양쪽 '?'가 모두 통과 심볼. 제품·검사별 REGEX_PATTERN. 사내 포맷이 다르면 어댑터에서 변환하고 실제 원본 좌표와 대조|
|공정별 RCP·장비 비교|DF_WF_LEGEND|제품·LOT·웨이퍼+OPER_ID/OPER_DET_DESC와 RECIPE_ID, EQP_ID, EQP_CH, STEPPER_RECIPE_ID. 공정 이력과 맵 측정 검사는 별도 조건|
|GMS|DF_GMS_YIELD_MONTHLY, DF_GMS_YIELD_WEEKLY, DF_GMS_YIELD_DAILY|LOTCODE, PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD. 각 기간 키·범위·단위 확인|

사내 SQLcl/SQL*Plus에서 실제 앱 계정으로 접속해 `@/반입문서경로/check_oracle_contract.sql`을 실행한다. 9개 쿼리는 값이 노출되지 않는 **0행 읽기**다. PASS는 열 이름과 읽기 권한이 맞다는 의미다. 단위·재측정 중복·데이터 존재 여부까지 검증하지 않는다. 오류가 발생하면 DBA가 사내에서 이름/권한/뷰를 확인한다. 목록 탐색은 별도 사용자 데이터 딕셔너리(user_tables 등)를 쓰므로 list_tables와 SELECT 양쪽을 확인한다. synonym으로 읽히지만 목록이 비면 harness/tools/list_tables.py가 보는 객체와 사내 노출 방식을 대조한다.

제품 하나를 골라 동일 계정에서 독립적으로 재집계한다. 기간 경계, 최근 측정 선택, PT1H/PT1C 포함 여부, wafer/lot 고유 수, 각 파라미터 분모와 표시 단위를 대조한다. 사내 검사가 다르면 담당자가 이름과 데이터 의미를 확인하고 사내 호환 뷰/어댑터에 반영한다. 더미 제품명·수치·LOT를 사내 기대 결과로 고정하지 않는다.

기준 커밋이 추적하는 08-YieldAgent/sql/에는 DF_ALL_PARA_PRB.sql, DF_BMW_PNG.sql, DF_SYMBOL_BIN.sql 세 파일이 있다. 예를 들어 첫 파일에는 더미 제품 두 개를 고정하는 제약이 있다. 외부 작업폴더에만 있던 DF_WF_ALL_PRB_V2.sql, DF_WF_LEGEND.sql, DF_WF_LEGEND_WT_ACTIVEPARAM_V2.sql 및 더미 적재 스크립트는 기준 소스의 완전한 사내 DDL이 아니다. Oracle 테이블 전체를 만드는 설치 SQL로 일괄 실행하지 않는다. 실제 생산 Oracle 데이터는 앱 소스에 포함하지 않는다.

## 5. 기능별 사내 합격 기준

- 새 대화에 “수율일보 작성해줘”만 입력하고 실제 제품 하나를 고른다. yield-daily-report가 로드되는지 보고 제품 범위, 검사 둘 다, 기간별 실제 표, 적격 산점도와 3행 BIN 맵을 확인한다. 표본/메타 부족이면 실제 개수와 미생성 이유를 DB로 확인한다.
- 같은 사용자의 다른 대화에서 그룹명과 검사만 입력한다. 전용 `load_wafer_maps`에 전달된 `wafer_groups`의 (LOT_ID, WF_ID) 쌍을 메모 원문 및 DB와 대조한다. 다른 LOT의 같은 웨이퍼 번호가 섞이면 실패다. 사내 데이터에는 사내 사용자 지정 그룹으로 시험한다.
- 권한이 다른 두 계정으로 스킬·메모리·과거 링크 분리를 검증한다. 모두 같은 로컬 개발 principal을 쓰는 환경으로는 이 검증을 대신할 수 없다.
- 이 반입 자료의 검증 범위는 원본 스킬 4개 바이트 일치 및 실제 카탈로그 읽기, 선택 메모·충돌·재실행, 하위 스킬 리소스·SQLite WAL 백업, 임시 Mongo의 GridFS 주소/원본 바이트 복원, 개발 Oracle 9개 계약 0행 조회다. 사내 계정·프록시·업무 정의와 기존 실제 사용자 전체 자료는 사내에서 별도로 검증한다.
