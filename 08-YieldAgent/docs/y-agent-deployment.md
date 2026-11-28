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
npm run build:win -- --publish never
```

배포 빌드에는 사내 코드 서명 설정을 적용한다. `dist/`에서 `Y-Agent Setup <버전>.exe`를 내려받을 수 있게 제공한다. 사용자가 이 파일을 더블클릭하면 설치가 진행되고 완료 후 Y-Agent가 자동으로 열린다. Windows 테스트 PC에서 설치, 접속, 로그인, 창 최소화, 결과 파일 열기를 확인한다.

패키징만 확인할 때는 `YORI_APP_URL`에 `https://y-agent.example.invalid/`를 사용할 수 있다. 이 주소는 접속이 불가능한 테스트용 예시다. 사내 접속 검증은 실제 HTTPS 주소를 넣은 빌드에서 진행한다.

## Windows 앱 업데이트

웹 UI 변경은 사내 웹을 배포하면 데스크톱 앱에서 다음 페이지 로드에 반영된다. Electron 버전, 네이티브 창·마스코트 제어, 설치 설정이나 웹 주소를 변경할 때는 Windows 앱을 새 버전으로 빌드한다.

1. 데스크톱 `package.json`의 버전을 올리고, 사내 코드 서명 설정을 적용해 `npm run build:win -- --publish never`로 빌드한다. 설치 파일 서명은 `latest.yml`이 생성되기 전에 빌드 과정에서 마쳐야 한다.
2. 같은 빌드의 설치 `.exe`와 `.exe.blockmap`을 사내 웹 주소의 `/desktop-updates/` 경로에 올린다. 마지막으로 `latest.yml`을 올려 새 버전을 공개한다. 해당 경로에서 Windows 앱이 파일을 받아야 하므로 로그인 HTML이나 SPA fallback을 반환하지 않도록 구성한다.
3. 직원은 마스코트나 앱 화면을 우클릭해 **앱 업데이트 확인**을 누른 다음 **다운로드**를 선택한다. 다운로드가 끝나면 **지금 설치**로 앱을 재시작하거나, **나중에**를 선택하고 우클릭 메뉴에서 설치를 이어갈 수 있다.

이 기능을 포함하기 전에 설치한 Windows 버전은 최초 한 번 새 설치 파일로 교체한다. 사내 Windows PC에서 이전 서명 버전부터 새 서명 버전까지 다운로드와 재시작을 확인하고 배포한다.
