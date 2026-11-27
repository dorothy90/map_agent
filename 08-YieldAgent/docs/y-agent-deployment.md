# Y-Agent 사내 배포

브라우저와 Windows 앱은 같은 사내 HTTPS 웹 주소에 접속한다. 웹은 정적 프론트엔드와 인증이 적용된 Harness 게이트웨이로 구성한다. Windows 설치 파일에는 접속 주소와 Electron 앱을 담는다. DB 접속 정보와 모델 API 키는 서버에서 관리한다.

## 웹

`08-YieldAgent/harness_console`에서 `npm ci`와 `npm run build`를 실행한다. `dist/`를 사내 웹 사이트 루트에 배포한다. 브라우저에서 대화 주소를 바로 열 수 있도록 SPA 라우트를 `index.html`로 연결한다.

같은 HTTPS 주소에서 `/harness/`, `/api/`, `/auth/`, `/gateway-m1/`을 사내 인증이 적용된 게이트웨이로 연결한다. WebSocket 업그레이드를 전달하고 SSE 응답은 버퍼링하지 않는다. 개발용 `VITE_HARNESS_LOCAL_GATEWAY` 플래그 없이 빌드한다. 배포 주소에서 로그인, 대화 스트리밍, 결과 파일 열기를 확인한다.

## Windows x64

Windows 빌드 머신에서 `08-YieldAgent/desktop`으로 이동한 뒤 PowerShell에서 실행한다.

```powershell
npm ci
$env:YORI_APP_URL = "https://<사내 Y-Agent 주소>/"
npm run build:win
```

`dist/`의 NSIS 설치 파일을 사내 코드 서명 절차에 따라 서명한 뒤 배포한다. Windows 테스트 PC에서 설치, 접속, 로그인, 창 최소화, 결과 파일 열기를 확인한다.
