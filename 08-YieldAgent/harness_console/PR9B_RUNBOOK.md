# PR-9b 화면 확인 (사용자 실행용)

Codex는 서버·Mongo·브라우저를 실행하지 않았습니다. 기존 Python 환경과 설치된 Node 의존성을 사용합니다. 새 패키지를 설치하지 않습니다.

1. 터미널 A: 작업 디렉터리에서 `HARNESS_MONGO_URI`를 기존 안전한 방법으로 환경에 설정합니다(값을 로그에 출력하지 마세요). `python tests-integration/pr9_dev_server.py`를 실행합니다. 기본 포트 8001, `PR9_PORT=0`이면 빈 포트를 골라 stdout JSON `{port,db}`로 알립니다. DB는 `zz_pr9_<uuid>`이며 정상 종료 시 삭제됩니다.
2. 터미널 B: `HARNESS_GATEWAY_TARGET=http://127.0.0.1:8001 npm run dev`를 실행합니다. 다른 포트를 사용했다면 위 JSON의 port를 대입합니다. Vite는 127.0.0.1:5175를 사용합니다. origin을 바꾸면 서버의 `PR9_VITE_ORIGIN`도 같은 값으로 지정합니다.
3. 브라우저에서 `http://127.0.0.1:5175/?ui=hermes&user=alice`를 엽니다. 일반 대화, 표와 검증 결과, 검증과 질문 대기 세션을 선택합니다. `user=bob`은 별도 소유자의 데이터입니다. 이 인증 형식은 개발 fixture에만 있습니다.
4. 터미널 C: `node e2e-web/screenshot.mjs`. `/tmp/pr9b_shots/01-sessions.png`, `02-results.png`, `03-clarify.png`를 확인합니다. 승인 시드는 없음; 질문 시드를 촬영합니다. 화면 오류·요소 대기는 실패로 종료합니다.
5. 질문 선택, 표 가로 스크롤, artifact 미리보기, 새 채팅·중지·새로고침을 직접 확인합니다. OfflineController에는 엔진/모델이 없어 새 질문에 답변이 생성되지는 않습니다.
6. `http://127.0.0.1:5175/`에서 기존 App도 확인합니다. UI rollback은 `ui=hermes`를 제거하고 새로고침합니다. 서버/API rollback은 별도 PR-10 절차입니다.
7. 터미널 A/B에서 Ctrl-C로 종료합니다. 서버 정상 종료 로그를 확인합니다. 강제 종료(SIGKILL) 시 lifespan 정리가 불가능하므로 stdout의 정확한 `zz_pr9_` DB 이름을 확인하고 수동 정리해야 합니다.

운영에서는 `window.hermesWebOptions = {resolver, fetcher}`를 새 진입점 실행 전에 주입합니다. resolver는 매 연결마다 같은 origin의 `/api/ws`용 인증 URL/protocol을 반환하고, fetcher는 같은 서비스의 REST 인증을 담당합니다. 운영 인증 규격 Q1은 미결정이며 미주입 상태는 연결 없이 오류 화면을 표시합니다. 개발 resolver를 운영에 복사하지 마세요.

## 포트 충돌 주의

기본 5175(Vite)/8001(서버 예시)은 같은 PC의 다른 vite/uvicorn 개발 프로세스가 사용 중일 수 있습니다. 실행 전에 `lsof -i :5175`와 `lsof -i :8001`로 확인합니다. 다른 작업의 프로세스를 임의로 종료하지 않습니다.

현재 `vite.config.ts`는 5175와 `strictPort: true`를 지정하며 `PR9_VITE_PORT`를 읽지 않습니다. Vite 포트는 예를 들어 `HARNESS_GATEWAY_TARGET=http://127.0.0.1:8002 npm run dev -- --port 5176`으로 바꿉니다. 서버는 `PR9_PORT=8002 PR9_VITE_ORIGIN=http://127.0.0.1:5176 python tests-integration/pr9_dev_server.py`처럼 설정합니다. 서버 origin 정책의 `PR9_VITE_ORIGIN`은 실제 Vite origin과 정확히 일치해야 합니다. 서버 포트 0을 선택했다면 출력된 port를 target에 반영합니다.

근거: `vite.config.ts`의 server/proxy, `tests-integration/pr9_dev_server.py:36,152`, `tests-integration/pr9_real_dev_server.py:129`. scripts/는 읽지 않았습니다. 기존 `/runs` 등 일부 Vite proxy target은 8001 고정이므로 이 예시만으로 모든 기존 API가 8002로 이동하지는 않습니다. 전체 기존 UI 회귀는 기존 target을 확인한 별도 환경에서 수행합니다. 위 명령은 사용자 실행 예시이며 PR-10 작성 중 실행하지 않았습니다.
