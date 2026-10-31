# PR-10 알려진 제한·미결정

2026-10-25 정적 확인 및 허용된 오프라인 검증 기준. PB는 [PORTING_BASELINE.md](PORTING_BASELINE.md), PR8/PR9는 [PR8_PORTING.md](PR8_PORTING.md)/[PR9_PORTING.md](PR9_PORTING.md)다. 표의 결정 담당은 제안 역할이며 실제 사내 담당 배정은 **미확정**이다. 과거 문서의 테스트 통과 수는 해당 시점 기록이고 이번 실행 결과가 아니다.

| 분류 | 제한·근거 | 누가 결정 / 무엇이 필요한가 |
|---|---|---|
| 보안/인증 | Q1의 principal 전달·위조 방지·WS origin/ticket·재접속 책임 미확정. 개발 fake identity는 운영 인증이 아님. PB §7.2 Q1(725~727행), §7.3; PR9 §위험·미결정(2292~2294행), §9c(2430~2433행) | 플랫폼/백엔드 담당과 대환 님이 인증 규격·동일 principal의 REST/WS/result 적용을 결정하고 실환경 격리를 검증 |
| 보안/인증 | 사내 플랫폼 보안은 사용자 전제이며 OS sandbox·일반 HTTP/MCP egress 보장으로 검증되지 않음. PB §7.1(695행), §6.1 | 플랫폼 보안 담당의 실제 배포 경계 확인 필요; 사내 사실을 추정하지 않음 |
| 배포 | agent_server의 ON 마운트는 기본 거부 resolver, `/gateway-m1` prefix. 운영 services 주입·프록시 공개 경로·릴리스/제한 배포 대상·관찰 기간·중단 기준 미확정. PB §7.1 PR-10(667행), §7.2 Q1/Q7, PR9 2379~2380행 | 배포/백엔드 담당과 대환 님이 운영 조립과 단계별 배포 게이트를 결정. 실제 E2E 후 전환 일정 합의 |
| 배포 | 기본 5175/8001은 다른 vite/uvicorn과 충돌 가능. PR9B_RUNBOOK의 포트 충돌 주의 참조. `PR9_VITE_PORT`는 코드에 없으며 `--port`, `PR9_VITE_ORIGIN`, `PR9_PORT` 사용 | 실행자가 lsof로 확인하고 origin/target 일치 확인. 기존 일부 proxy는 8001 고정이므로 전체 이동 여부 별도 검토 |
| 데이터/보존 | 승인 규칙/감사 분리 방향은 합의됐지만 사내 보존 기간은 미확정. PB §7.2 Q5(731행), §7.3(744,754행). 컬렉션/인덱스는 PR10_ROLLBACK 표 참조 | 데이터/보안 담당이 감사·receipt·tombstone·blob·checkpoint의 기간과 참조 정리 정책을 결정. 임의 TTL/drop 금지 권고 |
| 기능 비활성 | PromptOverlays 통째 비활성. native window·terminal·preview·첨부·모델 설정·프로젝트·온보딩·pet·음성·다중 pane은 렌더 및 effect/polling/구독도 차단. PB §7.1 프론트 이식(674~680행), §7.2 | 제품/보안 담당과 대환 님이 후속 단계 범위를 결정하고 capability 및 부작용 회귀를 함께 검증 |
| 8c 미구현 | defer_history/source는 구현됨. eager_build, lazy, workspace, editor, api_key, close_on_disconnect, display_projection, title/latest 활성 옵션은 미연결. PR8 §PR-8c 지원 및 거절 값(1059~1082행) | 백엔드 담당이 각 lifecycle/권한/조회 의미를 먼저 정의. -32005를 성공으로 숨기지 않음 |
| 8c 성능 | 빈 WS transcript여도 정확한 message_count/publication 때문에 전체 history projection을 읽음. bounded ACK·background restore·tip-only 메모리 최적화·4130 동등성 미구현. PR8 §hydrating·상태·순서 및 한계(1084~1103행) | 백엔드 담당이 실제 대형 이력으로 성능·메모리 예산과 안전한 resume 정책 결정 |
| PR-7 이월 | active_list/stats, authority live inventory, approval.received의 표시 시점 timeout, 여러 서버의 legacy observer 소유권, mixed-history 순서, pending outbox 원자성 미완/미확정. PR8 §8c(1071,1075행) 및 PR9 §서버 요청(2294행); 상세 `harness/PR7B_WS_RPC.md` §모호/미구현/결정 필요(124~145행) | 백엔드 담당이 durable observer lease와 timeout/메시지 identity 정책 결정. 7b의 deferred 미지원 설명은 최신 8c로 대체되었으므로 미해결로 재기록하지 않음 |
| PR-7 epoch | session 단조 generation과 run-local epoch 차이, 이미 frozen된 과거 frame 필드 소급 없음. 재획득 뒤 ingest된 과거 일반 이벤트의 정확한 epoch 복구 한계. PR9 §9c fix6(2466행 부근) | 백엔드 담당이 저장 계약 변경 필요성을 판단하고 다중 run/recovery 시험. 현재 fence는 재시도 힌트이며 성공 허가가 아님 |
| 법무/폰트/라이선스 | fonts.css/woff2 제외·시스템/사내 승인 폰트 방향 합의와 법무 승인 완료는 다름. PB §7.2 Q6(732행), §7.3; PR9 §최종 빌드·번들 검사(2306~2318행) | 법무/배포 담당이 정확한 패키지 버전·컴포넌트·peer/전이 라이선스와 고지 검토. 사내 승인 폰트 목록 미확정 |
| 테스트 제외 | PR9a 원본 739개 검토에서 미복사 128 + 제외 suite 43 = 171개 파일. case 규칙 97개는 별도 단위. PR9 §원본 테스트 재검토·제외(48~56행). 9b에서 ConfirmHost 6개와 completed-reply-refresh 17개 복구(2369행 부근) | 프론트 담당이 현재 review JSON/설정과 기능별 제외를 다시 대조. 역사적 171을 현재 잔여 개수로 단정하지 않음 |
| 테스트 제외 | Radix Dialog 내부 Select overlay가 부모 Dialog도 닫는 재현 1건(1실패/1통과), 정규 suite 제외. 실제 9b 조합에 중첩 Select 없음. PR9 §테스트 재검토·자동 호출 감사(2286행), §최종 검증(2370행 부근) | 프론트 담당이 중첩 Select 도입 전 실브라우저 재현/수정. 현재 오프라인 성공이 이 버그 해소를 뜻하지 않음 |
| lockfile | lockfile 5곳 차이는 이번 사용자 인계 정보. PB §7.2 Q6, PR9 §기존 설정 SHA(2158행), §최종 검증(2356행)은 의존성 검토 근거이나 5곳의 경로·내용·원인은 명시하지 않음. 정확한 5곳은 **미확정** | 패키지 관리 담당/대환 님이 비교 기준과 다섯 diff를 확인. 이번 작업은 package*.json 수정/설치 없이 진행 |
| 9b 결과 표시 | 최근 50 run, 표 첫 100행까지만 표시. 다음 페이지/이전 이력 탐색 미구현. 미리보기를 열지 않은 artifact는 비교하지 않으며 병합 셀/중복 헤더 동일성 추정 없음. PR9 §위험·미결정(2295행) | 제품/프론트 담당이 페이지 탐색 범위 결정, 실제 넓은 표/많은 run 검증 |
| 9c fix5 | 웹 UI의 session.create에 service_tier를 보내 authority가 -32005로 거부. 웹 gateway-compat에서 기본 tier 및 무효 기본 필드를 생략하도록 보정됨. 모든 tier를 무조건 제거하는 것은 아님. PR9 §9c 원본 이탈/차이(2386~2410행, fix5에 해당) | 프론트/백엔드 담당이 실제 서버 재검증. 명시적 비기본 tier/fast:true는 서버 fail-closed 정책 유지 |
| 승인 화면 | approval 카드는 시드된 승인으로만 확인 가능. 승인 가능 실제 도구가 연결되지 않아 실제 도구 승인/재개 성공 경로는 확인되지 않음. PR9 §9c 개발 fixture·한계(2412~2433행) | 백엔드/검증 담당이 승인 대상 도구와 안전한 실행 시나리오를 연결하고 once/session/always/deny·재개 검증 |
| 실제 모델 화면 | 9c는 read_result 외 도구 차단, 외부 DB 조회/보고서 생성 성공을 검증하지 못함. 실행 예산은 run 수이지 모델 호출 수가 아님. PR9 §9c 한계(2430~2433행), fix6 마지막 인계 | 대환 님/검증 담당이 실제 스트리밍·중지·승인·신호 종료·DB/worker 정리 및 허용 데이터→모델→도구 전체 E2E 수행 |
| 롤백 검증 범위 | 이번 Python은 py_compile만 수행. Mongo pytest 및 실제 재시작/프록시/UI 확인은 사용자 실행 대기. PB §7.1 PR-10/§7.2 Q7, PR9 §인계(2374~2380행) | 사용자 환경에서 PR10_ROLLBACK 체크리스트 완료 후 제한 배포 판단 |

## 이번 검증

- `npx tsc --noEmit -p .`: 통과.
- `npx vitest run src/web`: 6파일, 33테스트 통과(새 main opt-in 4케이스 포함).
- `npm test`: 10테스트 통과.
- `npm run build`: 통과. 기존 동적/정적 import 혼용·500 kB 초과 청크 경고 유지.
- Vitest의 localStorage 경고 유지. 설치·서버·브라우저·Mongo·네트워크 실행 없음.
- Python 신규 회귀 파일 `harness/tests/test_pr10_rollback.py`와 PR9 개발 fixture 두 파일 `pr9_dev_server.py`, `pr9_real_dev_server.py`의 py_compile 통과. pytest/E2E 통과 주장은 하지 않는다.

## e473f5a9 잔존 표기

아래 표는 수정 후 검색 결과로 채운다. 역사 기록과 산출물은 변경하지 않는다. `harness_console/README.md`는 보호 파일이며 MD5 `6f4f4ebd46d05cf939026e7a6bd16195`를 유지한다. 역사 계획 두 문서에 `/tmp/pr10_historical_pin.patch`의 주석이 적용됨. e473f5a9는 작성 당시 pin이며 현재 번들은 5894e750임을 병기했고 기존 본문은 보존했다.

검색: `grep -rIn --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git --exclude-dir=scripts --exclude-dir=__pycache__ --exclude='.env*' e473f5a9 harness harness_console ../docs ../outputs` (원문 결과 `/tmp/pr10_pin_remaining.txt`). 금지된 .env/scripts 및 바이너리 캐시 내용은 읽지 않는다.

| 잔존 위치 | 사유 / 처리 |
|---|---|
| harness/README.md:108 | 2026-10-05 역사 기준임을 명시하고 현재 5894e750 병기 |
| harness_console/PORTING_BASELINE.md:48,60,537,572 | 이전 pin/회귀 비교점 및 D01 결정의 역사 근거 |
| harness/engine_sync/report_5894e750_pre_sync.md:6,113 | 동기화 이전 pin과 비교 한계의 역사 증거, 수정 금지 |
| harness/engine_sync/report_5894e750_pre_sync.json:20971 | 동기화 이전 조사 산출물, 수정 금지 |
| harness/engine_sync/upstream_pre_sync.json:3 | 이전 원본 해시 manifest, 수정 금지 |
| ../docs/superpowers/plans/2026-10-05-hermes-core-alignment.md:5 | 역사 계획. 작성 당시 pin 및 현재 번들 5894e750 주석 적용됨 |
| ../docs/superpowers/plans/2026-10-05-hermes-native-engine.md:5 | 역사 계획. 동일한 주석 패치 적용됨 |
| ../outputs/backend-hermes-native-20261005-{1,2,3}/evaluation.json:15 | 당시 실제 평가의 엔진 커밋, 산출물 수정 금지 |
| ../outputs/backend-hermes-embedded-20261006-{1,2,3}/evaluation.json:15 | 당시 실제 평가의 엔진 커밋, 산출물 수정 금지 |
| ../outputs/delegation-enabled-live-20261020-trace.json:6 | 과거 실행 trace의 pin, 산출물 수정 금지 |
| ../outputs/hermes-port-audit-20261021/report.md:10 | 과거 포팅 감사 기준, 산출물 수정 금지 |
| ../outputs/hermes-port-fixes-20261021/report.md:3 | 과거 수정 감사 기준, 산출물 수정 금지 |
| 본 파일의 절 제목/검색 명령 | 잔존 표기 감사용 자기 참조 |

보호 대상 harness_console/README.md에는 검색 시 해당 pin 표기가 없었다. 역사 pin을 현행으로 오해할 표현 정정과 역사 증거 삭제는 구별한다.

프로젝트 전체 `.`도 같은 제외 조건에 `--exclude-dir=dist`를 추가해 재검색했다(`/tmp/pr10_pin_workspace.txt`). 표 밖의 추가 소스 일치는 없었다. 위 첫 검색에는 harness/engine의 dist도 포함된다.
