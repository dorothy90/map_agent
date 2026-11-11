# Harness Console

Hermes는 유일한 웹 UI 및 harness 실행 경로입니다. `src/main.tsx`는 항상 `src/web/entry`를 마운트하고, `src/hermes`의 채팅 컴포넌트를 웹 호스트 어댑터로 연결합니다. URL에 UI 선택 파라미터를 지정할 필요가 없습니다.

## 준비 사항 및 실행

- Node.js 22.12 이상(또는 20.19 이상), npm
- 인증된 Hermes 게이트웨이와 세션·결과·아티팩트 API
- 배포 환경에 맞는 `window.hermesWebOptions.resolver` 연결 설정

```bash
cd 08-YieldAgent/harness_console
npm install
npm run dev
```

브라우저에서 **http://127.0.0.1:5175** 를 엽니다. 포트가 사용 중이면 오류를 냅니다. 인증 resolver가 없으면 연결하지 않고 안내를 표시합니다. 개발 fixture 연결은 [통합 테스트 안내](tests-integration/README.md)를 참고하세요.

Vite는 `/api/ws`, `/gateway-m1`, `/harness/sessions`, `/harness/results`, `/harness/artifacts`를 `HARNESS_GATEWAY_TARGET`(기본 `http://127.0.0.1:8001`)으로 프록시합니다. 기존 API 프록시도 유지합니다. `vite preview`는 빌드 미리보기용입니다.

## 기능과 연결

- Hermes Thread와 ChatBar를 통한 메시지 전송, 스트리밍, 중지 및 후속 요청
- 게이트웨이 기반 세션 목록, 저장 세션 재열기 및 이력 복원
- 도구 실행 상태, 확인·승인 입력과 Yield 결과·아티팩트 표시
- 모델 사용량과 실행 시간 표시

`src/platform`의 웹 호스트와 `src/protocol`의 게이트웨이 계약을 사용합니다. 결과와 아티팩트는 소유권이 확인되는 API로 읽습니다. 실제 제공되는 기능은 연결된 게이트웨이의 지원 범위에 따릅니다.

## 검증

```bash
npx tsc --noEmit
npx vitest run
npm run build
```

`npm run test:integration`과 `npm run test:integration:real`은 별도 환경과 명시적인 실행 설정이 필요합니다. [통합 테스트 안내](tests-integration/README.md)에 fixture와 실제 모델 검증 조건이 정리되어 있습니다. Hermes 브라우저 스크린샷 도구는 `e2e-web/screenshot.mjs`입니다.

`PR*.md`, `PORTING_BASELINE.md` 및 [이전 이식 기록](../harness/INTERNAL_MIGRATION_2026_10_31.md)은 당시의 전환·롤백 결정을 기록한 역사 문서이며 현재 진입 경로 안내가 아닙니다.
