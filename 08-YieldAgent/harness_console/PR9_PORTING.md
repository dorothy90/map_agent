# PR-9a 포팅 기록 — 4차 이어하기

갱신: 2026-10-25T09:19:27 · H `5894e750c3dba980d43b25aa29e3958149ba2c61`

상태: PR-9a 완료. 아래 진입점 미완 없음. 9b 앱 조합/실제 렌더링은 수행하지 않음.

## 최신 체크리스트

- [x] 3차 중단 뒤 tsc 오류/테스트 hang 원인 복구. Radix focus-scope 단일화 + inline dependency로 중복 focus trap 해소.
- [x] vitest 최상위 resolve 복구. integration/real-model은 동일 additive protocol alias 사용; list에서는 globalSetup을 실행하지 않음.
- [x] profile·pane·projects·hermes 8b 부족 export 0. 비활성 기능은 원본 시그니처 확장 shim.
- [x] Thread/composer/ChatView/sidebar/session hooks 전체 경로와 직접 의존 반입. 필수 채팅은 원본 기반 구현.
- [x] 원본 대응 테스트 재검토. 테스트 원본 바이트 수정 0. 제외 사유 파일·case별 기록.
- [x] 마운트/연결/타이머 자동 호출 가드와 offline DOM 검사, SHA/잔여 검색 재생성.
- [x] 네 검증이 실제 끝까지 종료(exit 0).
- [x] PR-9a 전체 완료.

## 파일 수 (PR-9a 누적)
| 분류 | 신규 반입 | 8b 갱신 | 합계 |
|---|---|---|---|
| verbatim | 1463 | 11 | 1474 |
| patch | 87 | 1 | 88 |
| shim | 43 | 24 | 67 |
| new | 23 | 0 | 23 |
| 전체 | 1616 | 36 | 1652 |

별도 기존 설정 4개: tsconfig, vitest, integration, real-model. 문서/감사 JSON은 자체 SHA 순환을 피하려고 자기 SHA를 비워 둠. 테스트 fixture는 원본 SHA까지 기록.

## 진입점별 완료/미완
| 진입점 | 상태 | 근거 |
|---|---|---|
| profile/projects/hermes/pane | 완료 | export 이름 누락 0, 원본 선언/매개변수 보존 |
| approval/rich-editor/MarkdownText/clarify/ToolFallback | 완료 | 이전 반입 유지 + 전체 원본/DOM 회귀 실행 |
| Thread 전체 | 완료 | 원본 message/list/timeline/status/stream 렌더링·편집 suite |
| composer 전체 + chat-bar | 완료 | 원본 제출·중지·draft/queue/focus; voice/screenshot/미지원 completion만 경계 |
| ChatView + 직접 의존 | 완료 | history-window/swap/drop/scroll 및 연결된 mount DOM 검사 |
| sidebar 전체 | 완료 | 세션 목록/검색/재개/삭제/보관/pin 원본 경로. profile/project/cron/messaging 부가 UI는 비활성 |
| session hooks 전체 | 완료 | create/resume/stop/prompt/stream/gateway-event/server-request 원본 suite |
| 최종 검증 | 완료 | 아래 exit/로그 확인 |

## 자동 호출 감사

- `src/platform/pr9-entrypoints.test.tsx`: ChatView/Thread/composer/sidebar 실체를 jsdom에서 mount. 연결 open, 실제 store session 행, timer 11초 진행. manifest의 implemented RPC/REST만 허용. model.options RPC/REST는 implemented라 허용하며 실제 네트워크는 mock.
- complete.path/slash/catalog, subagent/process/session-control, pull-request recovery 등은 지원 상태를 effect/타이머 시작 전에 확인. emoji 정적 데이터 fetch, 테마 font link/customCSS 삽입, connector 설치/account 조회를 비활성 경계에서 차단.
- fetch/WebSocket/EventSource는 node/jsdom 모두 throw하는 offline 기본값. upstream unstubAllGlobals 뒤에도 복원되는 기본값 자체가 차단 함수. 원본 가짜 socket 주입은 허용하되 실제 socket 서버 테스트는 제외.
- 기존 pr9-leaf/boundaries DOM 검사가 approval/clarify/rich-editor/Markdown/tool leaf를 포함. 신규 raw native 호출 없음. 남은 hermesDesktop는 타입·주석·원본 테스트 fixture, dead-session-prune의 존재 검사(호출 아님)이다.
- 자동 호출 후보 전체(파일/줄/코드)는 pr9-audit.json resume4.automatic_call_scan. 실제 DB/LLM/backend/browser 연결 시험은 하지 않았다.

## 원본 테스트 재검토·제외

재검토 739개: {'copied': 568, 'excluded-not-copied': 128, 'excluded': 43}. 실행 제외 suite 43개, case 이름/접두부 규칙 97개. 원본 파일 수정 없이 Vitest 설정에서 분리했다.
- 원본 모노레포 바깥 fixture 경로 두 개는 테스트 설정에서 같은 SHA의 내부 JSON으로 연결. 테스트/기대값/fixture 내용은 무수정.
- 파일별 원본 SHA/배정/사유: [pr9-test-review.json](pr9-test-review.json)
- 반입했지만 제외한 suite: [pr9-excluded-tests.json](pr9-excluded-tests.json)
- 혼합 suite에서만 제외한 비활성 사례: [pr9-excluded-cases.json](pr9-excluded-cases.json)
- 복구한 검사: API config route·transcript paging, 단일 세션 생성/재개, replay/steer/queue, approval/clarify, watchdog, 핀 서버 복원. native/프로필 부가기능 때문에 전체 핵심 suite를 제외하지 않았다.

### 제외 파일별 사유
| 원본 H 파일 | 사유 |
|---|---|
| api/local-models-owner.test.ts | 원본 window.hermesDesktop.api mock 및 비활성 local-runtime-jobs 타입 의존. 8b 명시 webApi host 주입과 불일치. |
| api/plugins.test.ts | 비활성 native backend discovery 및 plugin REST/WebSocket 재연결 성공 기대. |
| app/capabilities/catalog/catalog-browser.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/connectors/accounts-section.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/connectors/data/deep-link.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/connectors/data/prefetch.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/connectors/data/queries-rules.test.tsx | PB connector setup UI disabled; identity-triggered connector refresh not part of web chat |
| app/capabilities/index.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/plugins/plugin-folder-access.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/plugins/plugin-update.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/capabilities/plugins/plugins-tab.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/chat/composer/attachments.test.tsx | 7개 중 4개 실패: native 경로 기반 이미지 읽기 및 preview rail 열기 성공 기대. 3개 thumbnail/remove 검사는 첫 실행 통과했지만 원본 suite 무수정 원칙상 전체 suite 제외. |
| app/chat/composer/directive-actions.test.tsx | 10개 중 2개 실패: in-app/native 브라우저 열기 성공 기대. 비활성 창 경계; 세션 chip 경로는 원본 보존. |
| app/chat/composer/discard-lost-prompt.test.ts | Creates real ws WebSocketServer on 127.0.0.1; socket integration forbidden and ws uninstalled. Queue ownership covered by dispatcher/state mock suites |
| app/chat/composer/hooks/use-at-completions-contrib.test.tsx | complete.path unsupported: mount/query guard must prevent the original RPC expectations |
| app/chat/composer/hooks/use-auto-speak-replies.cross-window.test.tsx | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| app/chat/composer/hooks/use-completions-owner-route.test.tsx | complete.slash/catalog/path 미지원: 원본 원격 completion 성공 기대와 manifest guard가 의도적으로 다름. 기본 텍스트 제출/편집 테스트 유지. |
| app/chat/composer/hooks/use-composer-screenshot.test.tsx | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| app/chat/composer/hooks/use-composer-voice-shortcuts.test.tsx | PB voice/dictation hook is an explicit no-op shim |
| app/chat/composer/hooks/use-composer-voice.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/composer/hooks/use-composer-voice.ts |
| app/chat/composer/hooks/use-slash-completions.test.tsx | complete.slash/commands.catalog unsupported: mount/query guard prevents original RPC expectations |
| app/chat/composer/status-stack/coding-row.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/composer/status-stack/coding-row.tsx |
| app/chat/composer/status-stack/preview-row.test.tsx | 3개 실패: local/remote preview rail 및 native browser 파일 열기 성공 기대; PB preview 비활성 경계. |
| app/chat/hooks/use-composer-actions.test.ts | 29개 중 18개 실패: native 이미지/clipboard/pasted-text 저장 성공 기대; 비활성 native 저장 경계. 일반 텍스트 rich-editor suite는 유지. |
| app/chat/new-session-drag.test.ts | PB disabled native/pane/onboarding behavior; upstream bytes retained, not executed |
| app/chat/right-rail/preview-act.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| app/chat/right-rail/preview-popout-bridge.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/right-rail/preview-popout-bridge.ts |
| app/chat/right-rail/preview-reader.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| app/chat/session-drag.test.ts | PB pane drag-session registry is a no-op; pin/unpin via session menu and pin store remain tested |
| app/chat/sidebar/connection-switcher.test.tsx | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| app/chat/sidebar/cron-jobs-section.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/sidebar/cron-jobs-section.tsx |
| app/chat/sidebar/fleet-rail.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| app/chat/sidebar/gateway-groups-drag.test.tsx | PB multi-gateway/pane drag disabled; primary session sidebar unchanged |
| app/chat/sidebar/gateway-groups.test.tsx | PB multiple gateway/profile management disabled; primary session list suites retained |
| app/chat/sidebar/profile-launch-menu.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/sidebar/profile-launch-menu.tsx |
| app/chat/sidebar/profile-remote-override-dialog.test.tsx | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| app/chat/sidebar/project-dialog.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: app/chat/sidebar/project-dialog.tsx |
| app/chat/sidebar/projects/base-branch-picker.test.tsx | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| app/chat/sidebar/projects/workspace-groups.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| app/chat/sidebar/use-fleet-roster.test.tsx | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| app/context-menu/app-context-menu.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| app/context-menu/pane-context-menu.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| app/contrib/hooks/completed-reply-refresh.test.tsx | app contrib session-tile delegate/background-sync 조합은 9b. use-message-stream completion/duplicate-background 원본 suite는 실행. |
| app/contrib/hooks/use-desktop-integrations.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| app/contrib/hooks/use-session-tile-delegate.test.ts | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| app/cron/index.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/hooks/use-config-record.test.tsx | PB settings config record 자동 read/write UI 비활성. |
| app/hud/composer-drag.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: app/hud/composer-drag.ts |
| app/right-sidebar/terminal/agent-terminal-stream.bound.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: app/right-sidebar/terminal/agent-terminal-stream.ts |
| app/routes.workspace-reveal.test.ts | 비활성 SDK host/pane reveal 조합에 의존. app/routes 기본 선택 및 titlebar 분류 테스트는 원문 반입. |
| app/session/hooks/profile-rail-fresh-chat-owner.test.tsx | PB profile rail/registry source 전환 비활성. 원본 switchProfile/ensureGatewayAgent 성공을 기다리는 suite라 제외; 기본 source 제출·재개는 별도 원본 suite 유지. |
| app/session/hooks/use-message-stream/gateway-event/lifecycle.test.ts | 전체 case가 setup.ready의 free_tier.status/setup.runtime_check/setup.status 자동 호출 기대. PB onboarding/status snapshot 조합 비활성. |
| app/session/hooks/use-prompt-actions/prepared-submissions-spent.test.ts | Native private-file journal cross-window tombstones disabled; browser journal identity tests retained |
| app/settings/about-settings.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/delegation/routing-data.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/delegation/routing-section.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/local-models-settings.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/plugin-install-modal.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/plugin-settings-ownership.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/providers-settings.terminal-disconnect.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/providers-settings.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/settings-search.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/subagents-destination.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/vault-settings.owner.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/settings/vault-settings.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| app/shell/context-usage-panel.test.tsx | useContextBreakdown의 자동 RPC 성공/재시도를 기대. 현재 manifest stub을 가드하므로 원본 기대와 불일치. 표시와 무호출은 신규 경계 테스트로 검증. |
| app/shell/hooks/use-context-breakdown.test.ts | manifest stub session.context_breakdown 자동 성공/재시도 기대. 무호출 DOM 검사와 usage 표시 원본 suite 유지. |
| app/shell/titlebar-controls.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| components/assistant-ui/catalog-install-tool.test.tsx | PB plugin/capability install API disabled; tool progress rendering retained |
| components/assistant-ui/connector-directory-card.test.tsx | PB connector account/setup UI is a null shim; no query on rendering a disabled connector tool |
| components/assistant-ui/connector-tool.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/assistant-ui/connector-tool.tsx |
| components/assistant-ui/embeds/youtube-embed.test.tsx | 원본 native getEmbedHostOrigin mock 계약; 웹 renderer에는 loopback host 없음 (TS2353). |
| components/assistant-ui/markdown-text.filelinks.test.tsx | 6개 중 1개 실패: local media URL 재생 경로 기대; native media transport 비활성. |
| components/assistant-ui/markdown-text.media.test.tsx | 5개 중 4개 실패: native/remote media 파일 resolve 및 local file audio/video player 성공 기대. 미지원 transport 차단. |
| components/assistant-ui/mcp-setup-tool.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/assistant-ui/mcp-setup-tool.tsx |
| components/assistant-ui/theme-picker/theme-tool.test.tsx | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| components/assistant-ui/thread/message-parts-connectors.test.tsx | PB connector/setup cards are null shims; thread text/tool progress suites retained |
| components/assistant-ui/thread/user-edit-composer-owner-route.test.tsx | Two cases require blocked complete.slash catalog or native image upload; text editing tests retained |
| components/assistant-ui/tool/fallback-vision-image.test.tsx | 3개 중 2개 실패: native/remote 이미지 경로 해석 기대; 미지원 파일 transport 비활성. |
| components/assistant-ui/transcript-directive.test.tsx | SDK composerHost + contrib 조합은 9b/비활성 plugin 경계. directive leaf 및 formatter 개별 원본 suite와 DOM 경계는 반입. |
| components/chat/preview-attachment.test.tsx | 5개 중 4개 실패: native 폴더 열기/파일 reveal 및 filesystem 분류 기대; 비활성. |
| components/confirm-host.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| components/find-bar.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| components/model-visibility-dialog.test.tsx | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| components/onboarding/skip-setup.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/onboarding/skip-setup.tsx |
| components/pane-shell/pane-visibility.test.ts | 원본 테스트가 cwd/src/styles.css를 직접 읽음. 보호된 기존 앱 CSS를 검사하여 실패; Hermes CSS는 별도 경로이고 pane 조합은 비활성. |
| components/pane-shell/workspace-scope.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| components/prompt-overlays.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/prompt-overlays.tsx |
| components/prompt-overlays.vault-code.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/prompt-overlays.tsx |
| components/prompt-overlays.vault-save-login.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/prompt-overlays.tsx |
| components/prompt-overlays.vault-unlock.test.tsx | PB 비활성 기능 shim 검증용 원본 테스트: components/prompt-overlays.tsx |
| components/tips/local-runtime-update-offer.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| components/ui/connector-card.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| components/ui/dialog-dismiss-repro.test.tsx | 원본 dialog.tsx/select.tsx/test SHA 동일. 설치된 Radix/jsdom에서 overlay로 Select를 닫을 때 Dialog onOpenChange(false)도 호출(56행). 단독 재현 1 실패/1 통과. native 경계 문제 아님; 원본 UI 무수정 유지, 9b 회귀 확인 필요. |
| hooks/use-media-image.test.tsx | 3개 실패: https 실패 후 gateway media proxy 자동 재시도 성공 기대. 미지원 media REST 차단과 불일치. |
| lib/completion-sound.test.ts | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| lib/desktop-fs.test.ts | 17개 중 13개 실패: native/remote 파일시스템 read/write 성공을 기대; PB 비활성 경계. |
| lib/desktop-git.test.ts | PB native git/free-tier browser challenge disabled; original successful native transport contract cannot run |
| lib/external-link.test.tsx | 원본 native fetchLinkTitle/openExternal mock 계약; 비활성 제목/외부 창 호출 경계와 불일치 (TS2353/TS2339). |
| lib/file-preview-math.render.test.tsx | PB 파일 preview 렌더러 조합 비활성; 채팅 Markdown 수식 원본 검사는 실행. |
| lib/guarded-model-switch.test.tsx | ConfirmHost shell 조합은 9b. 순수 guarded-model-switch 계약과 ConfirmDialog 개별 검사는 별도 반입. |
| lib/local-preview.local-target.test.ts | 4개 중 1개 실패: remote directory를 native preview 대상으로 여는 fallback 기대. 비활성 preview 경계. |
| lib/local-preview.test.ts | 29개 중 8개 실패: 인증 파일 읽기 및 native preview/browser 열기 성공 기대. |
| lib/media-dimensions.test.ts | 순수 dimensions 검사와 native/remote 이미지 resolve 성공 계약이 한 suite에 혼재. native transport 비활성. |
| lib/media-file-download.test.ts | native saveGatewayFile 및 owner-scoped 다운로드 성공 기대; 비활성. |
| lib/media.remote.test.ts | native/remote media 읽기·stream URL 및 gateway transport 성공 기대; 비활성. |
| lib/preview-annotate/handoff.test.ts | 비활성 workspace/windowRelay/BroadcastChannel handoff 성공을 기대; 기존 workspace shim에 $workspaceOwnerKey 없음 (TS2724). |
| lib/session-link-title.test.ts | manifest stub GET /api/sessions/{session_id}의 자동 조회/캐시 성공을 기대. 신규 DOM 테스트는 RPC/REST 0을 검증. |
| lib/session-owner-routed-rpcs.test.tsx | model-visibility-dialog/MCP reload 설정·named profile 원격 source 조합 비활성. |
| lib/voice-playback.routing.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: lib/voice-playback.ts |
| lib/voice-playback.same-turn.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: lib/voice-playback.ts |
| lib/voice-playback.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: lib/voice-playback.ts |
| plugins/hermes-bots/canonical-group-presentation.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| plugins/kanban/api.connection-scope.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| sdk/settings.test.ts | 9b shell/SDK/contrib host 조합 또는 PB pane/native 경계의 테스트. PR9a 필수 채팅 leaf 테스트와 분리. |
| store/appearance-apply.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/billing-block.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/coding-status.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/connections-new-chat-route.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/connections.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/default-profile-startup.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/default-profile.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/desktop-metrics.test.ts | 비활성 telemetry 집계/전송을 기대. |
| store/display-toggles.test.ts | 미지원 config.set 자동 동기화를 기대. 비활성 경계로 제외. |
| store/fleet-roster.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/free-tier-challenge.test.ts | PB native git/free-tier browser challenge disabled; original successful native transport contract cannot run |
| store/free-tier-offer.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/free-tier-sign-in.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: store/free-tier-sign-in.ts |
| store/hud.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: store/hud.ts |
| store/kanban-connection-scope.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| store/layout-connection-scope.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/local-models-surfaces.test.tsx | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| store/local-setup-offer.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/native-notifications.test.ts | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| store/onboarding-decide.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/onboarding-owner.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/onboarding-run.test.ts | PB disabled native/pane/onboarding behavior; upstream bytes retained, not executed |
| store/onboarding.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/pending-submissions-windows.test.ts | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| store/pet-gallery.test.ts | PB 비활성 기능 shim 검증용 원본 테스트: store/pet-gallery.ts |
| store/pet.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/preview-open-browser.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/preview-persistence.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/preview-tabs-scope.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/preview-toggle-browser.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/preview.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/profile-agent-activation.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile-cache.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile-dot-state.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile-remote-override.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile-select-source.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile-share.test.ts | PB profile settings/skin export disabled |
| store/profile-switch-failure.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/profile.test.ts | PB profile 전환/원격 backend/IPC 비활성. 이번에 원본 export 이름·시그니처 전체는 완성. |
| store/projects-connection-tag.test.ts | 프로젝트 RPC fetch 및 다중 연결 관리 성공을 기대; 비활성 shim 대상. |
| store/projects.test.ts | 프로젝트 RPC/native workspace 관리 성공을 기대; 전체 비활성 shim 대상. |
| store/review.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/send-diagnostics.test.ts | 6개 실패: native 로그 수집 및 diagnostics.share_nous 업로드 성공 기대; 비활성. |
| store/session-pane-focus.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/session-sidebar-focus.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/session-states.test.ts | pane-mirror/workspace tree/tile 통합 suite; PB pane 조합 비활성. session-state 순수·reconnect·runtime map·watchdog companion suite는 반입/실행. |
| store/session-tiles-connection-scope.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/session-unread-tile.test.ts | PB profile/다중 connection/pane/workspace 조합 비활성. 원본 signature shim으로 경계; 단일 세션 목록·pin·resume 검사는 실행. |
| store/skill-deeplink-install.test.ts | PB settings/capability 설치/plugin/cron/local runtime 비활성; 자동 RPC·REST·native 실행 없음. |
| store/translucency.test.ts | PB disabled native/pane/onboarding behavior; upstream bytes retained, not executed |
| store/translucency.win10.test.ts | PB disabled native/pane/onboarding behavior; upstream bytes retained, not executed |
| store/updates-backend-check.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/updates-channel.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/updates.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |
| store/windows.test.ts | PB native/voice/screenshot/다중 창 기능 비활성. 웹 제출/중지/상태 보존 검사는 별도 실행. |
| themes/backend-sync.test.ts | PB preview/theme 설정/project/onboarding/billing/native 부가기능 비활성. 해당 source는 null/no-op 또는 로컬 표시 경계. |

## 추가 설치 후보·9b 인계
| 패키지 (H lock) | 파일/용도 | 현재 경계 |
|---|---|---|
| @codemirror/view 6.43.6 | composer/focus.ts의 CodeMirror focus | 채팅 rich editor 유지, 비활성 코드 편집기 경계 |
| emojibase-data 16.0.3 | composer/hooks/use-emoji-completions.ts | 정적 JSON fetch 없음; 기본 텍스트 입력 유지 |
| @xterm/xterm 6.0.0 | terminal | PB 비활성 |
| d3-force 3.0.0 | graph/pane | PB 비활성 |
| react-arborist 3.13.2 / react-dnd 14.0.5 | 프로젝트/파일 tree | PB 비활성 |
| qrcode 1.5.4 / driver.js 1.8.0 | onboarding/tour | PB 비활성 |

설치 0. 9b: app/index.tsx·shell 조합, main.tsx, Vite alias/opt-in/실제 렌더링. Tailwind 자동 source 탐색으로 기존 CSS 산출물 크기가 달라질 수 있으므로 9b에서 스타일 격리 검토. 실제 integration/model 실행은 사용자가 별도 수행.

## 네 검증 및 설정 수집
| 검사 | exit | 결과 로그 |
|---|---|---|
| tsc | 0 | /tmp/pr9-r4-tsc-final2.log |
| vitest | 0 | /tmp/pr9-r4-final-all.log |
| npm | 0 | /tmp/pr9-r4-npm-final.log |
| build | 0 | /tmp/pr9-r4-build-final.log |
| integration_list | 0 | /tmp/pr9-r4-integration-list-final.log |
| real_model_list | 0 | /tmp/pr9-r4-model-list-final.log |

### tsc
```text

PROCESS_GROUP_CLEAN
```

### vitest
```text
(node:47330) Warning: `--localstorage-file` was provided without a valid path
(Use `node --trace-warnings ...` to show where the warning was created)
(node:47334) Warning: `--localstorage-file` was provided without a valid path
(Use `node --trace-warnings ...` to show where the warning was created)
(node:47343) Warning: `--localstorage-file` was provided without a valid path
(Use `node --trace-warnings ...` to show where the warning was created)
(node:47344) Warning: `--localstorage-file` was provided without a valid path
(Use `node --trace-warnings ...` to show where the warning was created)

 Test Files  579 passed | 6 skipped (585)
      Tests  5756 passed | 99 skipped (5855)
   Start at  09:14:33
   Duration  255.59s (transform 27.23s, setup 49.40s, import 546.41s, tests 82.94s, environment 271.39s)


PROCESS_GROUP_CLEAN
```

### npm
```text
✔ out-of-order events sort and sequence replay is idempotent (0.088958ms)
✔ missing parents and cyclic tool metadata do not hide tools or recurse forever (0.111375ms)
✔ invalid event JSON fails visibly instead of silently dropping events (1.0575ms)
✔ artifact MIME detection accepts parameters and response-only metadata (0.117ms)
✔ iframe heights port table row sizing, chart sizes, summary and default cases (0.178459ms)
✔ replayed artifact events deduplicate without losing observation metadata (0.133834ms)
✔ artifact response MIME overrides generic metadata while HTML from either source wins (0.071416ms)
✔ table display rounds floats without changing numeric strings or integers (0.241875ms)
ℹ tests 10
ℹ suites 0
ℹ pass 10
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 178.1975
```

### build
```text

> harness-console@0.1.0 build
> tsc --noEmit && vite build

vite v7.3.7 building client environment for production...
transforming...
✓ 2050 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                     0.48 kB │ gzip:   0.31 kB
dist/assets/index-DkfSlJed.css    143.29 kB │ gzip:  24.65 kB
dist/assets/index-BP2YWnVG.js     245.99 kB │ gzip:  78.20 kB
dist/assets/markdown-CHv1HR4z.js  345.84 kB │ gzip: 105.27 kB
✓ built in 2.58s

PROCESS_GROUP_CLEAN
```

### integration_list
```text
tests-integration/gateway.itest.ts > a: matching modes work; mismatch fails without a mount or mode fallback
tests-integration/gateway.itest.ts > b: real session and admission push a verified message into the installed store
tests-integration/gateway.itest.ts > c: an actually dropped submit ACK reuses identity and restores a durable terminal receipt
tests-integration/gateway.itest.ts > d: disconnected events replay once into the session store
tests-integration/gateway.itest.ts > e: truncated gap resumes the store before REST history
tests-integration/gateway.itest.ts > e: epoch gap resumes the store before REST history
tests-integration/gateway.itest.ts > f: local unsupported calls emit no wire traffic; raw server retains distinct errors
tests-integration/gateway.itest.ts > g: users cannot read each other's replay, receipt, or REST messages
tests-integration/gateway.itest.ts > h: legacy prompt submission stays unsupported and creates no execution
tests-integration/gateway.itest.ts > i: implemented REST sessions, profiles, health and status use actual gateway handlers
tests-integration/gateway.itest.ts > j: absent or invalid test identity fails closed for HTTP and WebSocket
tests-integration/gateway.itest.ts > k: legacy deferred resume hydrates through REST after its snapshot
tests-integration/gateway.itest.ts > k: authority deferred resume hydrates through REST after its snapshot
```

### real_model_list
```text
tests-integration/real-model.itest.ts > one real execution survives lost ACK and reaches the installed store
```

## 다음 이어하기: 파일별 남은 진입점 의존

구현 진입점 누락 없음. 이번 PR-9a 범위의 미완 없음.
| 원본 directory | 비테스트 원본 파일 수 | 누락 |
|---|---|---|
| components/assistant-ui/thread | 34 | 0 |
| app/chat/composer | 91 | 0 |
| app/chat/sidebar | 49 | 0 |
| app/session/hooks/use-prompt-actions | 16 | 0 |
| app/session/hooks/use-session-actions | 20 | 0 |
| app/session/hooks/use-message-stream | 21 | 0 |

## 잔여 검색·보호 SHA

@nous-research/fonts.css/woff 신규·갱신 파일 검색 0건. hermesDesktop 173건(대부분 원본 테스트/타입/주석). 보호 baseline 242개 SHA 동일; 허용되지 않은 수정 0. 새 any cast/ts-ignore 추가 0. 프로세스는 부모 supervisor가 최대 600초 제한으로 실행·회수; 타임아웃 진단은 해당 process group kill. 마지막 전체 실행은 정상 종료 확인.

## 8b shim 갱신 목록
| 파일 | 분류/이유 | 이전 SHA256 | 현재 SHA256 |
|---|---|---|---|
| src/hermes/hermes.ts | verbatim; H 원본 export 전체 복원 | 0200d11daeeb8ca05d7b88259c0b69eecdd30612fae115fc1450e14ac9ddc3ff | 87b6ae0fa05ea9d28b7c1556d7e9f438dd777d3c52594d0a49329d5204d29fad |
| src/hermes/global.d.ts | shim; 8b WebDesktopBridge + 원본 immutable 테스트의 선택 api/preparedSubmissions 타입 경계. 런타임 native host 설치 없음. | 6e1187e2b2b0cc6dbd8341111d3b478166ca882ead323e31352e832c2a2e761e | 531c6f31dbe3f20073a9d5e4a6fc30ead1f93a59f8b17b1901dcab849306f966 |
| src/hermes/app/routes.ts | verbatim; H 원본 export 전체 복원 | dc593fa830b9b7799e5079529140c484b13d9f6ed3a9e646181dfb4268bc4d87 | 037f10823e2567ec9c5bb84ca395607a14b7b9dd515e09d7df9ac421b57afc28 |
| src/hermes/contrib/types.ts | verbatim; H 원본 export 전체 복원 | 8b55881023e0c58b30812b0a663aafa573290c6795ce5d1d4b5c20733a570d21 | 0bc9809f982a18dd38e7362988c5bc1f956236c1ea8cfc23fbb1cf44e94a2145 |
| src/hermes/lib/model-options.ts | verbatim; H 원본 export 전체 복원 | 9f3bfa1674ed86722a27cde33d99a22ee5b84d280908517c5dcb8a9a5a06675f | 473794e08e15b96b48e12409f7869dbf08e09cdafb6cb3b56fd765cb93199f89 |
| src/hermes/lib/connector-tools.ts | verbatim; H 원본 export 전체 복원 | 3b6ceadbaea30fd8fb95f577c3c481be02c77d8732d6de2f6549b254a1514568 | 0ddfd86d9aaa3c817274dbe053ccb8ca6d130a9f1299ea912de15c13e791bf15 |
| src/hermes/lib/chat-runtime.ts | verbatim; H 원본 export 전체 복원 | 51c1f89fcc0e2855182801b1767864cf00ac129b7cd2d056d1ede4c45e80a71b | cdaf6e0dfa3d3532235f3c926a85b2bdcc888f3f088a06266db87d8637e51c79 |
| src/hermes/lib/message-types.ts | shim; H 원본 export 전체 복원 | 529833c21d85616dd893fcff8417e23151f68bc3df5b1ab15f020a6370441259 | a541034bed4c791fa9aa858968148dbcf34a6ea921249ed880a45fc45a6f6199 |
| src/hermes/lib/media.ts | shim; 원본 순수 분류/캐시/타입 유지. native 파일·proxy·download 비활성; proxy는 원본 실패 계약인 빈 문자열 반환. | 9ddefa221ada2466a8e1e284043e2a97133cfb608ee560367103812ee849e4c5 | 76f66c41e2fc3aa797e165d5c59dd621ccdb6179fe46296ea5550600d374c7d1 |
| src/hermes/i18n/index.ts | verbatim; H 원본 export 전체 복원 | 5690f761ba211b55c770985061cdfff31fe23d9d11aeed44b8a2bd5f969180a1 | 0b4f38d631ec0289a7e9540b4cd5d8f39ad04d6d0d9f4e1047d52c9a9da4cb8c |
| src/hermes/themes/backend-sync.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 6e0b88aa95eb1e3c54d0b373ebbe33b64ce4ed9b7bc99745c224516c77fd2c8b | da8e476bee161024f3130a76c19b439aa6d5234dcaf77ba07a46fee4c786e023 |
| src/hermes/store/desktop-metrics.ts | shim; 이전 PR-9a 경계 유지; 최초/1차 기록 참조. | be110f7b2d81ac9f6f6e1c883845ba41c921dc9891f37c944e5d9b7f82f94093 | 4b4975bddacbe1e30772f2e82d32cf99a000911d63724b3247ef6263b05f6fe7 |
| src/hermes/store/projects.ts | shim; PB:674–680 프로젝트/native workspace 비활성. 원본 공개 export/함수 시그니처 전체; 빈 상태와 no-op. 새 채팅 cwd는 기존 workspaceCwdForNewSession 유지. | 79a0665e5c83d7ef1e91540b9f93eb8dda13a7fb9393875691379331de643b69 | 984ca79c6efe682db5a1a9d5b3c7aba9605111d5e2d26821714a1bd918434440 |
| src/hermes/store/onboarding-session.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 08e2ce247afc326778ec70d4725770a574166b180fa2089133ce7a4234ff62d8 | 050a049862e7f7db34598b261e40c866006998379e83fc7b3f2817962d962d4e |
| src/hermes/store/windows.ts | shim; 이전 PR-9a 경계 유지; 최초/1차 기록 참조. | 4ef3df6402b81bb47f418d3a108b3f50dd04ed9d2b7129053196881af61d9c60 | 23b360ae6d30b520cebed7b777c2e550da7c2e5181c68e3d840ed2ed4e654648 |
| src/hermes/store/preview-ownership.ts | verbatim; H 원본 export 전체 복원 | 6c7247f8f35685a0e71b1fb544da6f11f6466330c8c4228881d4bb3c35b45840 | fa97126d0f88029b22e86270daa619c97145879bbec71c03bf2eb164e85e83e9 |
| src/hermes/store/free-tier-sign-in.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | fc824289ca5fe46eae6ad38bf182b724fb62a8a8558fc2e1f18e70c8daea2be0 | 9af8473283360a7dadbf7e9b6db81bd6c45e5d39e6d927ad56ce4b0b1ebf619f |
| src/hermes/store/interface-mode.ts | shim; 원본 resolver 유지, 비활성 layout SidebarRowMeta 타입 발췌. | 4a4cb155765407aff846ea1e4ca5e08f34682211c9b25efb4c67c28b5e177f64 | 40f506e20dda1ca3c2dd09db5f2feff0e11344979ee599665f6f0e56005d2280 |
| src/hermes/store/transcript-sync.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | ee0b0dde944c18c971b3df8149e19fa6d2d44bb1899108436baac92c8f5cfb50 | 2263ca1a58e601a200495226f60faf17a9ab1be2fe4eeb62798ef4a1bcf7ea81 |
| src/hermes/store/profile.ts | shim; PB profile/backend 선택 비활성. H 공개 이름·매개변수·반환 타입 전체 확장, 로컬 기본 profile 유지. | a3e435566f7434774adc2586019f8f57846c885587a70750fd30debbe7d76122 | 5933d166f4a265a7adab1524437fbde83554ef4d8a594400209e34d416056bd8 |
| src/hermes/store/onboarding.ts | shim; PB onboarding 비활성. H public 타입과 비활성 $desktopOnboarding 상태 확장. | d44ca9c0bf1c99304b537ccaea877f76cb0217da90d41cbc8a851873cbdaa871 | a85ced82e76bc3e019c804211f6ae388c9c7089378326843af6156204f471271 |
| src/hermes/store/billing-block.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | c0117affc9504c6f6fb49a75bea728e5dcaa38d885dc83e208895810559ba09f | 7d3e46ee9ec912226f515d46115fa1beb99b1152cd7d478049f35f11cc5817d9 |
| src/hermes/store/native-notifications.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 218ae581e47cd6072b470b63946918085ac9009093e8eb881030c908cd6fb122 | 70f8b1500b31ead834e39984fb1146e75495e136503df1b8c0953145281822e2 |
| src/hermes/store/pet.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 42cc5da3768eb77b9d52c8309371c203c2de8d9d92afca0db6569eab1d7a85d8 | 33dc0209edf5960049fa89cc511c84d7dd371818871811c0947ce06e27fb3f4c |
| src/hermes/store/preview.ts | shim; 이전 PR-9a 경계 유지; 최초/1차 기록 참조. | dc90b45e22949417a15f12908d55b5a8b20c1d9e876a4aa29025d49d5d253f9e | b8e4a54797094ede91afc7bac49bb01e0a0df415cdece64b6e9a826b44f0004c |
| src/hermes/store/preview-status.ts | verbatim; H 원본 export 전체 복원 | 01d3181fce0d541e765f02ed90a62727cf430b8b26e9ed2436662f2bb80f9e1f | 0b7d83caac260e625ca8f98548888e807a6b19fe86f5e26d04dae7bfeb36caae |
| src/hermes/lib/chat-messages/types.ts | verbatim; H 원본 export 전체 복원 | 3fbbe202737bb19e957691f55119ade96967d3865b7e07623a4827e5b03ff6b7 | 2e484105b842c9e247e15b1afa453f6888713717425a9fcd60d5402cfca6ddac |
| src/hermes/components/pane-shell/workspace-scope.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 3a94eb1d1b20ba9575d55eec2ac8f707953786c6b63a2c7e52a8af47402ad30d | caa19dd9a23ae00521c0486345a19ae175f8a6a0cdcd516c23ebee1fcc4bfe7e |
| src/hermes/components/assistant-ui/directive-text.ts | shim; 8b formatter 전용 .ts를 원본 .tsx 재수출로 확장; 기존 경로 shadowing 해소. | 1a4d0d026b713da0d834a9066d991815a2c2c33fff9e1de5806bd18bb9e6f9bf | c3634a642864913329c4aee9a1d9ff165d846a52f2a67f3bacd18eab1adc09a4 |
| src/hermes/components/chat/vibe-hearts.ts | shim; H 원본 export 전체 복원 | fde0fa0bec9bd67de52b8952aaf27049879e09e79029ba35fc6f58767c943fbd | 0b60953240533e54f923be60658103ece82f917571b06e926e66135f5b23aa2f |
| src/hermes/components/pane-shell/tree/store.ts | shim; PB pane/preview 배치 비활성. H 공개 export 전체 확장, no-op/null/빈 store. | 644e797565ed78fb5921df468a2f8a4b5e60d73d58d5c3a8a73c04bf348fb69f | 340f8ed1d5a0207e3c52cffefe8db5acbf265afcdf3e34c644887bc37b51e85e |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/lifecycle.ts | verbatim; H 원본 export 전체 복원 | c24678de9f5997f75c71018e2bb207af5a751a113f14477426700de0f608c16c | 372ffc3a7332b4a62f36f2fdaca0e060b95bbd8524a3efc1a75297aff171d811 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/types.ts | patch; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 0b5fad0a457da1afca9f96afd7ac2f1362e838c61773de4db4f8dd8a5f9eacb1 | dbc24e3bf713ae426cc8411df9dce494e8ae411c41b5c1f118d7b3dc9dfe2e11 |
| src/hermes/app/shell/hooks/use-context-breakdown.ts | shim; 원본 hook + 미지원 session.context_breakdown effect 선차단. | 4e61cab012bc2e7e61c835fb3ed795db161fd8a50b735fc89ba22b5556566fa0 | 048196b5f6eff870835fffd9d05f0da5d184baadf23e9c578fc3d4bf609eadbc |
| src/hermes/app/chat/composer/types.ts | shim; 원본 전체 타입 복원. 아직 미반입 prompt utils의 타입만 submission-types.ts에 원문 발췌. | f0b386baaf6e6769a873054d3748af13a8d2184f92ecf7397f33e1b39f8bd422 | 53a241f867b8273e861d407c47bbfd5036a832f5ac26fc5f1303cb16a973da31 |
| src/hermes/app/chat/sidebar/projects/workspace-groups.ts | shim; PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | 77c450219daf49f0ea264aba39b9efaea363d797478248074fc8a2b7578580ca | 55f812323e5edc8a8bd0e5eceb2fad57cbc708ed0c4280f715c814bd291b8c9c |

## 신규 shim 목록
| 파일 | 근거 |
|---|---|
| src/hermes/api/plugins.ts | PB:674–680 plugin REST/socket/backend discovery 비활성; 함수 시그니처 보존. |
| src/hermes/app/chat/built-in-tour.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/composer/hooks/use-composer-screenshot.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/composer/hooks/use-composer-voice.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/composer/status-stack/coding-row.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/right-rail/preview-act.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/right-rail/preview-nav.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/right-rail/preview-popout-bridge.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/cron-jobs-section.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/local-device-switch.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/profile-launch-menu.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/profile-remote-override-dialog.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/profile-switcher.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/project-dialog.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/chat/sidebar/projects/worktree-dialog.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/hud/composer-drag.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/app/right-sidebar/terminal/agent-terminal-stream.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/catalog-install-tool.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/connector-directory-card.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/connector-tool.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/mcp-setup-tool.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/theme-picker/accent-picker-card.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/theme-picker/layout-picker-card.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/assistant-ui/theme-picker/theme-tool.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/free-tier/notice-strip.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/onboarding/first-run/assembly.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/onboarding/first-run/intro-copy.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/onboarding/first-run/intro.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/onboarding/skip-setup.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/pane-shell/index.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/pane-shell/tree/renderer/drag-session.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/components/prompt-overlays.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/lib/desktop-fs.ts | PB:680 native/remote 파일시스템 비활성; 공개 함수·타입 시그니처 유지. 쓰기/읽기는 명시 거절. |
| src/hermes/lib/desktop-git.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/lib/preview-annotate/handoff.ts | PB:674–680 preview 창 간 handoff 비활성; native relay/BroadcastChannel/저장/listener 없음. |
| src/hermes/lib/tour/run-tour.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/lib/voice-playback.ts | voice playback 비활성. 필수 prompt cancel이 소비하는 순수 interrupt latch는 H 구현 복원. |
| src/hermes/store/display-toggles.ts | 이전 PR-9a 경계 유지; 최초/1차 기록 참조. |
| src/hermes/store/hud.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/store/pet-gallery.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/store/pet-generate.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/store/pet-overlay.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. |
| src/hermes/store/send-diagnostics.ts | PB:680 진단 업로드/native 로그 수집 비활성; 공개 타입/함수 보존. |

## patch 근거 (원본 파일·변경 줄)
| 파일 | 이유 | H 변경 범위 |
|---|---|---|
| src/hermes/api/config.ts | native api → 기존 명시 webApi bridge. 모듈 자동 호출 없음. | @@ -0,0 +1 @@ , @@ -148 +149 @@ , @@ -185 +186 @@ , @@ -197 +198 @@ , @@ -220 +221 @@ , @@ -229 +230 @@ , @@ -238 +239 @@ , @@ -252 +253 @@ , @@ -317 +318 @@ , @@ -335 +336 @@ , @@ -349 +350 @@ , @@ -372 +373 @@ , @@ -384 +385 @@ , @@ -392 +393 @@  |
| src/hermes/api/delegation.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -61 +62 @@ , @@ -68 +69 @@ , @@ -80 +81 @@ , @@ -92 +93 @@  |
| src/hermes/api/mcp.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -28 +29 @@ , @@ -43 +44 @@ , @@ -87 +88 @@ , @@ -98 +99 @@  |
| src/hermes/api/models.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -29 +30 @@ , @@ -57 +58 @@ , @@ -75 +76 @@ , @@ -127 +128 @@  |
| src/hermes/api/skills.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -15 +16 @@ , @@ -27 +28 @@ , @@ -38 +39 @@ , @@ -63 +64 @@ , @@ -70 +71 @@ , @@ -83 +84 @@ , @@ -103 +104 @@ , @@ -133 +134 @@ , @@ -149 +150 @@ , @@ -158 +159 @@ , @@ -167 +168 @@  |
| src/hermes/api/system.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -175 +176 @@  |
| src/hermes/api/toolsets.ts | window.hermesDesktop.api → 기존 명시 webApi host. 자동 호출 없음; WebHost의 capability gate 재사용. | @@ -0,0 +1 @@ , @@ -18 +19 @@ , @@ -29 +30 @@ , @@ -38 +39 @@ , @@ -51 +52 @@ , @@ -63 +64 @@ , @@ -91 +92 @@ , @@ -104 +105 @@ , @@ -113 +114 @@ , @@ -123 +124 @@  |
| src/hermes/app/capabilities/connectors/data/hosted-actions.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -72 +73 @@  |
| src/hermes/app/chat/composer/hooks/use-at-completions.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -133 +134 @@  |
| src/hermes/app/chat/composer/hooks/use-emoji-completions.ts | 미설치 emojibase-data 두 정적 fetch 제거; 빈 completion 결과. 필수 텍스트 입력 로직은 원본. | @@ -32,25 +32 @@ , @@ -58,2 +34 @@  |
| src/hermes/app/chat/composer/hooks/use-mic-recorder.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -274 +275 @@  |
| src/hermes/app/chat/composer/hooks/use-slash-completions.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -79 +80 @@ , @@ -99 +100 @@ , @@ -118 +119 @@  |
| src/hermes/app/chat/composer/index.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -150 +150 @@  |
| src/hermes/app/chat/composer/status-stack/index.tsx | process.list/session.control.read 미지원 시 mount·타이머 refresh 선차단. | @@ -0,0 +1 @@ , @@ -175,2 +176,2 @@ , @@ -192 +193 @@ , @@ -198 +199 @@  |
| src/hermes/app/chat/composer/status-stack/status-row.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -3 +3 @@  |
| src/hermes/app/chat/composer/status-stack/use-subagent-snapshot.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -20 +21 @@  |
| src/hermes/app/chat/hooks/use-composer-actions.ts | native 경로/임시 첨부 저장만 composer-native 경계로 전환. 원본 텍스트/상태/submit callback 유지. | @@ -0,0 +1 @@ , @@ -129 +130 @@ , @@ -601 +602 @@ , @@ -646 +647 @@ , @@ -684 +685 @@ , @@ -789 +790 @@  |
| src/hermes/app/chat/pr-tag.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -3 +4 @@ , @@ -19 +20 @@  |
| src/hermes/app/chat/sidebar/connection-switcher.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -17,0 +18 @@  |
| src/hermes/app/chat/sidebar/fleet-rail.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -1 +1,2 @@  |
| src/hermes/app/chat/sidebar/gateway-group-model.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -4,0 +5 @@  |
| src/hermes/app/chat/sidebar/gateway-groups.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -22 +22 @@  |
| src/hermes/app/chat/sidebar/projects/base-branch-picker.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -16 +16 @@  |
| src/hermes/app/chat/sidebar/projects/entered-content.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -8 +8 @@  |
| src/hermes/app/chat/sidebar/projects/model.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -4 +4 @@  |
| src/hermes/app/chat/sidebar/session-row-slots.tsx | 전체 앱 contrib barrel 대신 동일 원본 use-contributions leaf import. | @@ -4 +4 @@  |
| src/hermes/app/chat/sidebar/sessions-section.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -10 +10 @@  |
| src/hermes/app/chat/sidebar/use-fleet-roster.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -37 +38 @@  |
| src/hermes/app/chat/sidebar/use-server-session-search.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -25 +26 @@  |
| src/hermes/app/chat/use-chat-bar-state.ts | model.options RPC/REST가 implemented일 때만 자동 옵션 조회 허용. | @@ -0,0 +1 @@ , @@ -56 +57 @@  |
| src/hermes/app/command-center/maintenance.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -243 +244 @@  |
| src/hermes/app/gateway/hooks/use-gateway-request.ts | native global 접근을 명시 getWebDesktopBridge host로 전환; session-owned RPC 분기 유지. | @@ -0,0 +1 @@ , @@ -31 +32 @@ , @@ -80 +81 @@  |
| src/hermes/app/session/hooks/use-context-suggestions.ts | complete.path 미지원 시 effect가 호출하는 refresh 시작 전에 legacy/authority gate 확인. | @@ -0,0 +1 @@ , @@ -23 +24 @@  |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -2 +2 @@  |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/server-requests.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -14 +15 @@ , @@ -624 +625 @@  |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/status.ts | process.list 미지원 시 refresh 선차단; 원본 status 이벤트 처리 유지. | @@ -0,0 +1 @@ , @@ -119 +120 @@  |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/tools.ts | process.list 미지원 시 background process refresh 선차단; 원본 tool 이벤트 갱신 유지. | @@ -0,0 +1 @@ , @@ -196 +197 @@  |
| src/hermes/app/session/hooks/use-prompt-actions/prepared-submissions.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -74 +75 @@ , @@ -106 +107 @@ , @@ -230 +231 @@  |
| src/hermes/app/session/hooks/use-prompt-actions/slash-desktop-commands.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -441 +442 @@  |
| src/hermes/app/session/hooks/use-prompt-actions/upload-server-image.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -28 +29 @@  |
| src/hermes/app/session/hooks/use-prompt-actions/utils.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -508 +509 @@ , @@ -524 +525 @@  |
| src/hermes/app/session/hooks/use-session-actions/index.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -2377 +2376,0 @@  |
| src/hermes/app/settings/constants.ts | 비활성 theme provider 대신 원본 ThemeMode 타입 발췌. | @@ -17 +17,2 @@  |
| src/hermes/app/shell/titlebar.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -1,0 +2 @@  |
| src/hermes/components/assistant-ui/chat-empty-slot.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -4 +4 @@  |
| src/hermes/components/assistant-ui/directive-text.tsx | H:432 native image 파일 자동 읽기 제거; 원본 표시/편집 formatter 유지. | @@ -431,2 +431 @@  |
| src/hermes/components/assistant-ui/embeds/url-embed.tsx | 저장된 always consent와 무관하게 외부 iframe 자동 mount 금지; 원본 PrettyLink 표시. | @@ -48 +48,2 @@  |
| src/hermes/components/assistant-ui/embeds/youtube-embed.tsx | native loopback getEmbedHostOrigin 제거. 외부 embed 진입은 url-embed에서 차단. | @@ -40,23 +40 @@  |
| src/hermes/components/assistant-ui/markdown-text.tsx | native window 존재 검사 제거; 이식된 미디어/링크 경계 사용. | @@ -118 +118 @@ , @@ -357 +357 @@  |
| src/hermes/components/assistant-ui/thread/assistant-message.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -999 +1000 @@ , @@ -1007 +1008 @@ , @@ -1050 +1051 @@  |
| src/hermes/components/assistant-ui/transcript-directive.tsx | 전체 앱 contrib barrel 대신 동일 원본 types/use-contributions leaf import. | @@ -6 +6,2 @@  |
| src/hermes/components/chat/generated-image-result.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -60 +61 @@  |
| src/hermes/components/chat/preview-attachment.tsx | native filesystem classify/openDir/revealPath 비활성; 카드 원본 유지. | @@ -19,29 +19 @@ , @@ -111 +83 @@ , @@ -117 +89 @@  |
| src/hermes/components/error-boundary.tsx | native reportRendererError/revealLogs 비활성; React 오류 경계 유지. | @@ -69,10 +69,2 @@ , @@ -188 +180 @@  |
| src/hermes/components/ui/copy-button.tsx | native clipboard 분기 제거; 원본 navigator.clipboard 경로 유지. | @@ -19,6 +18,0 @@  |
| src/hermes/components/ui/favicon.tsx | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -40 +41 @@  |
| src/hermes/contrib/index.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -1,3 +1,2 @@ , @@ -5,2 +3,0 @@  |
| src/hermes/hooks/use-image-download.ts | H:96 native save 분기 제거; 명시 사용자 클릭의 browser download는 원본 유지. | @@ -88,8 +87,0 @@  |
| src/hermes/i18n/context.tsx | 기본 configClient=null; native locale probe 대신 navigator.language. | @@ -5 +5 @@ , @@ -25,21 +25,2 @@ , @@ -198 +179 @@  |
| src/hermes/lib/external-link.tsx | H:146,219 native 제목 조회/외부 창 열기 제거; 원본 링크 텍스트/컴포넌트 유지. | @@ -146,42 +146 @@ , @@ -219,5 +178 @@  |
| src/hermes/lib/local-preview.ts | native preview 정규화/브라우저 열기 비활성. | @@ -192,33 +192 @@ , @@ -308,34 +276 @@  |
| src/hermes/lib/preview-reach.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -21 +22 @@  |
| src/hermes/lib/renderer-loop-pause.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -28 +29 @@  |
| src/hermes/lib/session-link-title.ts | manifest stub GET /api/sessions/{session_id} 자동 호출을 기존 restCapabilityError로 선차단. | @@ -0,0 +1 @@ , @@ -51,0 +53 @@  |
| src/hermes/lib/sibling-ws-url.ts | native global 접근을 명시 getWebDesktopBridge host로 전환. | @@ -0,0 +1 @@ , @@ -50 +51 @@ , @@ -57 +58 @@  |
| src/hermes/lib/wake-indicator.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -15 +16 @@  |
| src/hermes/store/ambient.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -7 +8 @@  |
| src/hermes/store/coding-status.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -3 +3 @@  |
| src/hermes/store/connections.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -4,0 +6 @@ , @@ -158 +160 @@ , @@ -176 +178 @@  |
| src/hermes/store/default-profile.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -3 +4 @@ , @@ -20 +21 @@ , @@ -37 +38 @@ , @@ -54 +55 @@  |
| src/hermes/store/find-in-page.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -155 +156 @@ , @@ -208 +209 @@  |
| src/hermes/store/fleet-roster.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -3 +4 @@ , @@ -22 +23 @@  |
| src/hermes/store/free-tier-challenge.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -64 +65 @@  |
| src/hermes/store/local-models-flag.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -12 +13 @@  |
| src/hermes/store/onboarding-run.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -101 +102 @@  |
| src/hermes/store/power.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -25,6 +24,0 @@  |
| src/hermes/store/profile-share.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -161 +162 @@ , @@ -192 +193 @@  |
| src/hermes/store/pull-requests.ts | 미지원 pull-request REST 자동 복구를 manifest status로 선차단. | @@ -0,0 +1 @@ , @@ -3 +4 @@ , @@ -79,0 +81 @@  |
| src/hermes/store/review.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -6 +7 @@ , @@ -164 +165 @@ , @@ -665 +666 @@ , @@ -674 +675 @@  |
| src/hermes/store/session-pin-sync.ts | native presence 대신 명시 web host 사용; pin/unpin 서버 상태 반영 유지. | @@ -0,0 +1 @@ , @@ -320 +321 @@  |
| src/hermes/store/shared-metrics.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -109 +110 @@  |
| src/hermes/store/translucency.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -0,0 +1 @@ , @@ -72,2 +73,2 @@ , @@ -82,2 +83,2 @@ , @@ -366 +367 @@  |
| src/hermes/styles.css | fonts/codicons/font-face 제외; 시스템 font 변수. 기존 앱 CSS는 불변. | @@ -5 +4,0 @@ , @@ -15,0 +15,4 @@ , @@ -94,32 +96,0 @@ , @@ -476,3 +447 @@ , @@ -480,11 +449,2 @@ , @@ -814 +774 @@ , @@ -1816 +1776 @@ , @@ -1820 +1780 @@ , @@ -1824 +1784 @@ , @@ -1953 +1913 @@ , @@ -2142 +2102 @@  |
| src/hermes/themes/context.tsx | PB 원격 font link·customCSS 자동 삽입 제거. 로컬 색상/theme 계산 유지. | @@ -0,0 +1 @@ , @@ -55 +56 @@ , @@ -396 +397 @@ , @@ -412,30 +413,2 @@ , @@ -449 +422 @@  |
| src/hermes/vitest.setup.ts | 원본 helper 상대경로 및 jsdom localStorage fallback. | @@ -3 +3 @@ , @@ -17 +17 @@  |
| src/protocol/pr9-extra/billing-types.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -23,2 +23,2 @@  |
| src/protocol/pr9-extra/plan-usage.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -11 +11 @@  |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/types.ts | PB:674–680 비활성 native/optional leaf 경계 또는 지원 계약 보존을 위한 최소 이식 patch; 아래 원본 변경 줄 확인. | @@ -43 +43 @@  |

## SHA-256 — PR-9a 신규 파일 전수
| 파일 | 분류 | H 원본 | H SHA256 | 현재 SHA256 |
|---|---|---|---|---|
| src/hermes/api/canonical-clarify-lifecycle.test.ts | verbatim | apps/desktop/src/api/canonical-clarify-lifecycle.test.ts | 4a87b3a9e77cccb758a655e84b1870fbfd15a145d00656296a464cd25bd0e837 | 4a87b3a9e77cccb758a655e84b1870fbfd15a145d00656296a464cd25bd0e837 |
| src/hermes/api/canonical-protocol-wire.test.ts | verbatim | apps/desktop/src/api/canonical-protocol-wire.test.ts | a996e25a18161270d1fc687f4300a641f5e20f2ba9ebec34902ca58af135f000 | a996e25a18161270d1fc687f4300a641f5e20f2ba9ebec34902ca58af135f000 |
| src/hermes/api/config-route-binding.test.ts | verbatim | apps/desktop/src/api/config-route-binding.test.ts | 38b57bc8592bf67347c2cf3a4e946dc15672c3eeeeec152d5ec8ae26eefcfb5e | 38b57bc8592bf67347c2cf3a4e946dc15672c3eeeeec152d5ec8ae26eefcfb5e |
| src/hermes/api/config.ts | patch | apps/desktop/src/api/config.ts | 3f0cdc7f585b4f5eed15935935b5adcca9f8d60d4fed860712479891d9bc7b93 | 1da9968a403d31fdbe7fbd7e25535d47e1c2b09ff4c89afea5fbea4aea9fee9b |
| src/hermes/api/cron.ts | verbatim | apps/desktop/src/api/cron.ts | fdc941d6419b31a525a79cf138856c80e6f79daabad2d9ae02b77e066858ee32 | fdc941d6419b31a525a79cf138856c80e6f79daabad2d9ae02b77e066858ee32 |
| src/hermes/api/delegation.ts | patch | apps/desktop/src/api/delegation.ts | 1e53a2bea8f13e5bea18f3e71e8235af9e8196fc8a565fe7ce2fc89ee88c5307 | f2603d940aa0136f70b08752dbb59e662209c02aff0a7a15a1d8a889db2a7a8c |
| src/hermes/api/local-models.ts | verbatim | apps/desktop/src/api/local-models.ts | 2c270fe483af68bf6828a386c0db76a02d10847a68a4b69064c1f60d7ca03c6d | 2c270fe483af68bf6828a386c0db76a02d10847a68a4b69064c1f60d7ca03c6d |
| src/hermes/api/mcp.ts | patch | apps/desktop/src/api/mcp.ts | c4816685fd302b0c6236115e489d836cb2701ac7a66115a740d93d2edfd97a36 | b41f4c012301a86045a8bfedbcb999572623fe1be0424294ef727eb0e21649e9 |
| src/hermes/api/messaging.ts | verbatim | apps/desktop/src/api/messaging.ts | addff673dfcb0a457100ba3c8c5e84b9fd49f2a50f1676d6187d9d92b3b77c9f | addff673dfcb0a457100ba3c8c5e84b9fd49f2a50f1676d6187d9d92b3b77c9f |
| src/hermes/api/models.ts | patch | apps/desktop/src/api/models.ts | 873569405cf17c31c6ff3637918bbf7fdabf3261e9eefe04b399998c1c061df9 | af4cdd1621dfdd8f1bee7896c3a861e69c47d0d13237706f26d776621409f1f5 |
| src/hermes/api/plugins.ts | shim | apps/desktop/src/api/plugins.ts | f6af6f4d9c259fb345ba988e22afbbeb728fe7beb5f4e9fca1ee8297f44328ba | 50aa1b91aa27c0e62bab8d87c61c356399c5d777edb58319a14b503859073d40 |
| src/hermes/api/profiles.ts | verbatim | apps/desktop/src/api/profiles.ts | eb4baff9d577af1e20ac36ad180df8eb5ca75266624cffb18e2758b814db6c1d | eb4baff9d577af1e20ac36ad180df8eb5ca75266624cffb18e2758b814db6c1d |
| src/hermes/api/session-transcript-paging.test.ts | verbatim | apps/desktop/src/api/session-transcript-paging.test.ts | ae47aa394c37df8af17fbf2a091b4c4ff81c88741cbfd89d7772271945c603cf | ae47aa394c37df8af17fbf2a091b4c4ff81c88741cbfd89d7772271945c603cf |
| src/hermes/api/sessions-mutations.test.ts | verbatim | apps/desktop/src/api/sessions-mutations.test.ts | 5ae8f13ef80e2e5f9cf2d77dc1c8027d288280d3699799e11df63734a443d071 | 5ae8f13ef80e2e5f9cf2d77dc1c8027d288280d3699799e11df63734a443d071 |
| src/hermes/api/skills.ts | patch | apps/desktop/src/api/skills.ts | 2ba717c9a0bf642aa62005b6212663763e09b3bd9fd38f1883a01f4bb571be49 | 1c60629b2014efd76fe25ac474887164d04378c0f25eedce8186643cb6397de6 |
| src/hermes/api/system.ts | patch | apps/desktop/src/api/system.ts | 3867a4fad8cbe78cd98820532dda2fb9c5dc84e05f3f70520e5a43b07ec5716a | 92802a534f0038cf6e87e96eed987fb04cb357e15be6bfac0bd812503f44c1c5 |
| src/hermes/api/toolsets.ts | patch | apps/desktop/src/api/toolsets.ts | 74983b1f674ae1bf0638f1f73e426a29e995ce1d24e0711a2cfcc53983397646 | e4454db904781d8caf24866d2578e3ea9998b5875ea94a7e9dba99e861037f05 |
| src/hermes/app/agents/index.tsx | verbatim | apps/desktop/src/app/agents/index.tsx | 1be2ba051e6559cc497d8ce41923cbb7fe9b230cffcb39f45db2be3a3e5b36e5 | 1be2ba051e6559cc497d8ce41923cbb7fe9b230cffcb39f45db2be3a3e5b36e5 |
| src/hermes/app/capabilities/connectors/data/account-operations.test.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/account-operations.test.ts | bbe7e30140bd250c17d20bc855a4fcc1b6b0bd6beae9d420e5324b6fa493eda9 | bbe7e30140bd250c17d20bc855a4fcc1b6b0bd6beae9d420e5324b6fa493eda9 |
| src/hermes/app/capabilities/connectors/data/account-operations.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/account-operations.ts | 35e44a52310f38402294a65249f4c6924572afe665b988c4a6a9f6ecd47505af | 35e44a52310f38402294a65249f4c6924572afe665b988c4a6a9f6ecd47505af |
| src/hermes/app/capabilities/connectors/data/hosted-actions.ts | patch | apps/desktop/src/app/capabilities/connectors/data/hosted-actions.ts | c8318267fdc419da6767520c81e4efadf3a66a32aa4339db1a73ce6bd47be952 | c2c150db40509b870c2d2b79f768a30933ce6e723f652a782785f2115a3054f0 |
| src/hermes/app/capabilities/connectors/data/join.test.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/join.test.ts | 7e76e6cc8ebd26e6851f0805e7a82abdf5a6b003fc63133bd53f607ba709bf77 | 7e76e6cc8ebd26e6851f0805e7a82abdf5a6b003fc63133bd53f607ba709bf77 |
| src/hermes/app/capabilities/connectors/data/join.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/join.ts | 8e5af5134f9ba69a6a271da2b0cf440324bcedf98712e2242d33dfe0f047fe8a | 8e5af5134f9ba69a6a271da2b0cf440324bcedf98712e2242d33dfe0f047fe8a |
| src/hermes/app/capabilities/connectors/data/keys.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/keys.ts | 9cb5e57ce18704e44156ca6b52f7cb915a27c6089b33296dfbd391a63f6f0f10 | 9cb5e57ce18704e44156ca6b52f7cb915a27c6089b33296dfbd391a63f6f0f10 |
| src/hermes/app/capabilities/connectors/data/mutations.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/mutations.ts | 6c1c495960878439e8553d447a295ba2cc28ad28fed10c8bb3c19a4fbaa00dea | 6c1c495960878439e8553d447a295ba2cc28ad28fed10c8bb3c19a4fbaa00dea |
| src/hermes/app/capabilities/connectors/data/persist.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/persist.ts | 4f070c64f45328a730961d4d2b09ebcdf9829ee790188d3dcd48c5b679087e36 | 4f070c64f45328a730961d4d2b09ebcdf9829ee790188d3dcd48c5b679087e36 |
| src/hermes/app/capabilities/connectors/data/queries-rules.test.tsx | verbatim | apps/desktop/src/app/capabilities/connectors/data/queries-rules.test.tsx | 67788f783558acd454c68d8b0f841450cc15e0adffa8370c8d5ba6318bb44825 | 67788f783558acd454c68d8b0f841450cc15e0adffa8370c8d5ba6318bb44825 |
| src/hermes/app/capabilities/connectors/data/queries.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/queries.ts | 6e198b2ae0d5b90a664dbb015b7f3321ac099cd5b4ac104b61cf3692105862d1 | 6e198b2ae0d5b90a664dbb015b7f3321ac099cd5b4ac104b61cf3692105862d1 |
| src/hermes/app/capabilities/connectors/data/rpc.ts | verbatim | apps/desktop/src/app/capabilities/connectors/data/rpc.ts | 2c084510d39de6de841115dac9c159930c5aba7b6fe522ddc4926d19254102e1 | 2c084510d39de6de841115dac9c159930c5aba7b6fe522ddc4926d19254102e1 |
| src/hermes/app/capabilities/connectors/derive-tools.ts | verbatim | apps/desktop/src/app/capabilities/connectors/derive-tools.ts | 248a11b4b6761540ccbdfec0b91d8951047d7abe23b008dac44b21a01a017f5f | 248a11b4b6761540ccbdfec0b91d8951047d7abe23b008dac44b21a01a017f5f |
| src/hermes/app/capabilities/connectors/derive.ts | verbatim | apps/desktop/src/app/capabilities/connectors/derive.ts | 1c9891116f1f76b23c2560a4bdc407fcb9eeef52ed195d229ad38358b7c7b414 | 1c9891116f1f76b23c2560a4bdc407fcb9eeef52ed195d229ad38358b7c7b414 |
| src/hermes/app/capabilities/connectors/hint-vocabulary.ts | verbatim | apps/desktop/src/app/capabilities/connectors/hint-vocabulary.ts | 635f9efa3113a4547c86166ee9bb59716c5fcbf8b93e839baa5bb8e672d521c0 | 635f9efa3113a4547c86166ee9bb59716c5fcbf8b93e839baa5bb8e672d521c0 |
| src/hermes/app/capabilities/connectors/hosted-notice.tsx | verbatim | apps/desktop/src/app/capabilities/connectors/hosted-notice.tsx | 1232420c43d2b75e385d45339683781b7206c964fb3ddc50c4908379d897cf72 | 1232420c43d2b75e385d45339683781b7206c964fb3ddc50c4908379d897cf72 |
| src/hermes/app/capabilities/connectors/types.ts | verbatim | apps/desktop/src/app/capabilities/connectors/types.ts | f39e0f1ec81fac9883a8bae791bad555bfcc701faa37674bac22652bb79bdb9a | f39e0f1ec81fac9883a8bae791bad555bfcc701faa37674bac22652bb79bdb9a |
| src/hermes/app/capabilities/connectors/use-tools-editor.ts | verbatim | apps/desktop/src/app/capabilities/connectors/use-tools-editor.ts | 1b3737040d5930c29bd1d20aedab29e0e21a196ca6edb11562b5300e365e311d | 1b3737040d5930c29bd1d20aedab29e0e21a196ca6edb11562b5300e365e311d |
| src/hermes/app/capabilities/mcp/mcp-status.ts | verbatim | apps/desktop/src/app/capabilities/mcp/mcp-status.ts | fcda9ddfe52e0e896d46452dea912c51f78ca531d602eb57539b45c6a1bf86f9 | fcda9ddfe52e0e896d46452dea912c51f78ca531d602eb57539b45c6a1bf86f9 |
| src/hermes/app/chat/built-in-tour.ts | shim | apps/desktop/src/app/chat/built-in-tour.ts | f2073bacb5d84306acf3439cde4c2634f3cad0f43a6033c315125517f6aac4f6 | 8203c8d0d62d3734b9b1eba7a493a10cee8ae67d45b5cf12bd0aae12b2f39f61 |
| src/hermes/app/chat/chat-drop-overlay.tsx | verbatim | apps/desktop/src/app/chat/chat-drop-overlay.tsx | 44d0de0c211143485440857d8728d46dd0b06e67f63a6e4978494ad6062fa6c9 | 44d0de0c211143485440857d8728d46dd0b06e67f63a6e4978494ad6062fa6c9 |
| src/hermes/app/chat/chat-surface-state.ts | verbatim | apps/desktop/src/app/chat/chat-surface-state.ts | eabb52dcce4e1179b2e0b9b408ed23579ccf0d1ada8f720d1a72d0156619d0ae | eabb52dcce4e1179b2e0b9b408ed23579ccf0d1ada8f720d1a72d0156619d0ae |
| src/hermes/app/chat/chat-swap-overlay.test.tsx | verbatim | apps/desktop/src/app/chat/chat-swap-overlay.test.tsx | 11d9b602171ed85a7e85e4f183f93dab299b7d494f28018fac338fe2900c65a7 | 11d9b602171ed85a7e85e4f183f93dab299b7d494f28018fac338fe2900c65a7 |
| src/hermes/app/chat/chat-swap-overlay.tsx | verbatim | apps/desktop/src/app/chat/chat-swap-overlay.tsx | ffea2df699124ea0b941d94d0d66cee4235201772c7f78942aa747525a86fc9f | ffea2df699124ea0b941d94d0d66cee4235201772c7f78942aa747525a86fc9f |
| src/hermes/app/chat/composer/attachments.tsx | verbatim | apps/desktop/src/app/chat/composer/attachments.tsx | 91ba43b2cfac942b9b5c6edd80f26e24cd8c015dbb30898d7009402786196534 | 91ba43b2cfac942b9b5c6edd80f26e24cd8c015dbb30898d7009402786196534 |
| src/hermes/app/chat/composer/busy-submit-state.ts | verbatim | apps/desktop/src/app/chat/composer/busy-submit-state.ts | 85f8081998517a2ce25ebfbef57db4d50c0af8e54df66add6fc24d113e84f762 | 85f8081998517a2ce25ebfbef57db4d50c0af8e54df66add6fc24d113e84f762 |
| src/hermes/app/chat/composer/chat-bar-parts.tsx | verbatim | apps/desktop/src/app/chat/composer/chat-bar-parts.tsx | df04a7b824f136ee834c3de15de9eab41601939100457667d7fa2e2e98d5ebaa | df04a7b824f136ee834c3de15de9eab41601939100457667d7fa2e2e98d5ebaa |
| src/hermes/app/chat/composer/completion-drawer.tsx | verbatim | apps/desktop/src/app/chat/composer/completion-drawer.tsx | d2cf44d12e46441138fa91fd734a7e660c4bd05590c5c4f296e704d7536d91be | d2cf44d12e46441138fa91fd734a7e660c4bd05590c5c4f296e704d7536d91be |
| src/hermes/app/chat/composer/composer-utils-width.test.ts | verbatim | apps/desktop/src/app/chat/composer/composer-utils-width.test.ts | ade7589497a05f288ec77cf02b178943a70c59bdf68b3a6496bd6be4f97c2b9d | ade7589497a05f288ec77cf02b178943a70c59bdf68b3a6496bd6be4f97c2b9d |
| src/hermes/app/chat/composer/composer-utils.test.ts | verbatim | apps/desktop/src/app/chat/composer/composer-utils.test.ts | 83a5f4c701122864833cda06df4a7e8c804885df4765e4394d88f6b73b66ef37 | 83a5f4c701122864833cda06df4a7e8c804885df4765e4394d88f6b73b66ef37 |
| src/hermes/app/chat/composer/composer-utils.ts | verbatim | apps/desktop/src/app/chat/composer/composer-utils.ts | 93bb882cab0c13fc2be20d19376ffea4b316f96346c24f4bc52f9f17dae918bd | 93bb882cab0c13fc2be20d19376ffea4b316f96346c24f4bc52f9f17dae918bd |
| src/hermes/app/chat/composer/context-menu.tsx | verbatim | apps/desktop/src/app/chat/composer/context-menu.tsx | 40b48dccb1bc2d9580cfaa949fdf65790b01ee8f05c7f7b1ab349c05b2efd4b5 | 40b48dccb1bc2d9580cfaa949fdf65790b01ee8f05c7f7b1ab349c05b2efd4b5 |
| src/hermes/app/chat/composer/contrib.test.ts | verbatim | apps/desktop/src/app/chat/composer/contrib.test.ts | 88268034298de3e6b72ca93bab7b5acad7b9afa89e88cb76fa4e66dada16c3a6 | 88268034298de3e6b72ca93bab7b5acad7b9afa89e88cb76fa4e66dada16c3a6 |
| src/hermes/app/chat/composer/contrib.ts | verbatim | apps/desktop/src/app/chat/composer/contrib.ts | b4bcaaea914e36d5cbac7efb6c3174da4e96d7e94fe2642e23f0ac66b1d08f1a | b4bcaaea914e36d5cbac7efb6c3174da4e96d7e94fe2642e23f0ac66b1d08f1a |
| src/hermes/app/chat/composer/control-classes.ts | verbatim | apps/desktop/src/app/chat/composer/control-classes.ts | 685c8c82e7656ee0a82f6c9274e7d2fa9d23d9efa6f06e65ddea63b71f8ee615 | 685c8c82e7656ee0a82f6c9274e7d2fa9d23d9efa6f06e65ddea63b71f8ee615 |
| src/hermes/app/chat/composer/controls.test.tsx | verbatim | apps/desktop/src/app/chat/composer/controls.test.tsx | 76bfb3c761b314b66f481458e2354d490c6abd2987fc3a937fe20f64b0608b8a | 76bfb3c761b314b66f481458e2354d490c6abd2987fc3a937fe20f64b0608b8a |
| src/hermes/app/chat/composer/controls.tsx | verbatim | apps/desktop/src/app/chat/composer/controls.tsx | ecdc84e1f7859c3a319825a7ae1a7d28fe3bac2b2da863373e21879e9395b4b4 | ecdc84e1f7859c3a319825a7ae1a7d28fe3bac2b2da863373e21879e9395b4b4 |
| src/hermes/app/chat/composer/directive-actions.tsx | verbatim | apps/desktop/src/app/chat/composer/directive-actions.tsx | dfa792d939b725b20010ce99cd5919faa86af8efc08611e22483323b665dfeba | dfa792d939b725b20010ce99cd5919faa86af8efc08611e22483323b665dfeba |
| src/hermes/app/chat/composer/discard-lost-prompt.test.ts | verbatim | apps/desktop/src/app/chat/composer/discard-lost-prompt.test.ts | 24639dba734a23967d492f8a809cc25be6d4d39cb7ff0fadc8c4095fc26df365 | 24639dba734a23967d492f8a809cc25be6d4d39cb7ff0fadc8c4095fc26df365 |
| src/hermes/app/chat/composer/discard-lost-prompt.ts | verbatim | apps/desktop/src/app/chat/composer/discard-lost-prompt.ts | 84959418773a825cb2184a36a0b96e06a9259eb12f383a678f26c5726e17cc15 | 84959418773a825cb2184a36a0b96e06a9259eb12f383a678f26c5726e17cc15 |
| src/hermes/app/chat/composer/drop-affordance.ts | verbatim | apps/desktop/src/app/chat/composer/drop-affordance.ts | 4101f90f9a266acc82a3a4d826967a8c1c8b34ced8c1a3cbbd5dbc875e45c931 | 4101f90f9a266acc82a3a4d826967a8c1c8b34ced8c1a3cbbd5dbc875e45c931 |
| src/hermes/app/chat/composer/floating-state.ts | verbatim | apps/desktop/src/app/chat/composer/floating-state.ts | 421c109c403c66e2fe772502efc8e0e5ad95ff6b284131a33fb1481afd3b19da | 421c109c403c66e2fe772502efc8e0e5ad95ff6b284131a33fb1481afd3b19da |
| src/hermes/app/chat/composer/floating-surface.test.tsx | verbatim | apps/desktop/src/app/chat/composer/floating-surface.test.tsx | 911657197af7a3b768780c01beb6556c3459011fac5aabbb7cb5c92b8aae51a0 | 911657197af7a3b768780c01beb6556c3459011fac5aabbb7cb5c92b8aae51a0 |
| src/hermes/app/chat/composer/floating-surface.tsx | verbatim | apps/desktop/src/app/chat/composer/floating-surface.tsx | fa3781eb9735a19c5223f14b0d562955bac97acd27795037c3a6c0a764b0f8ea | fa3781eb9735a19c5223f14b0d562955bac97acd27795037c3a6c0a764b0f8ea |
| src/hermes/app/chat/composer/floating-target-editable-guard.test.ts | verbatim | apps/desktop/src/app/chat/composer/floating-target-editable-guard.test.ts | 4966bd1982f95d2f94c1c4b9d7799dfc0c5fbb7f9319f15ddbc6255e583597f7 | 4966bd1982f95d2f94c1c4b9d7799dfc0c5fbb7f9319f15ddbc6255e583597f7 |
| src/hermes/app/chat/composer/floating-target-floating-layer.test.ts | verbatim | apps/desktop/src/app/chat/composer/floating-target-floating-layer.test.ts | 9037f17204e730de3adfa7794e4f8612ee76f8a518a23a8604fc4e5777f3b95f | 9037f17204e730de3adfa7794e4f8612ee76f8a518a23a8604fc4e5777f3b95f |
| src/hermes/app/chat/composer/floating-target-media-controls.test.ts | verbatim | apps/desktop/src/app/chat/composer/floating-target-media-controls.test.ts | ced77e11373947e18cbd97ac65f958510229d29eb9f411ce7964fb079b77b4ca | ced77e11373947e18cbd97ac65f958510229d29eb9f411ce7964fb079b77b4ca |
| src/hermes/app/chat/composer/floating-target-selection-guard.test.ts | verbatim | apps/desktop/src/app/chat/composer/floating-target-selection-guard.test.ts | 1be801aa71096ee69fa4ac40db6c74f602c5378ff6ee51b66237a086c541b739 | 1be801aa71096ee69fa4ac40db6c74f602c5378ff6ee51b66237a086c541b739 |
| src/hermes/app/chat/composer/floating-target.ts | verbatim | apps/desktop/src/app/chat/composer/floating-target.ts | 7e53a3f6f3851f309093f1d838b97301413ee54b93463f7e9ec7d031978cb646 | 7e53a3f6f3851f309093f1d838b97301413ee54b93463f7e9ec7d031978cb646 |
| src/hermes/app/chat/composer/focus-chord.test.ts | verbatim | apps/desktop/src/app/chat/composer/focus-chord.test.ts | c063b8459517beb86a19c58abbffd3435423ebd5385a39dfa8b4a8afeaceaa26 | c063b8459517beb86a19c58abbffd3435423ebd5385a39dfa8b4a8afeaceaa26 |
| src/hermes/app/chat/composer/focus-chord.ts | verbatim | apps/desktop/src/app/chat/composer/focus-chord.ts | c1a4beae1776dec54fb286956304030fe8790977a2b09a9f82d3a90c80c4f19d | c1a4beae1776dec54fb286956304030fe8790977a2b09a9f82d3a90c80c4f19d |
| src/hermes/app/chat/composer/focus.test.ts | verbatim | apps/desktop/src/app/chat/composer/focus.test.ts | 803c4b52fd106d077bffac9d94d984e584e6c9f54537bfef797c278c1304cfa4 | 803c4b52fd106d077bffac9d94d984e584e6c9f54537bfef797c278c1304cfa4 |
| src/hermes/app/chat/composer/focus.ts | verbatim | apps/desktop/src/app/chat/composer/focus.ts | 1126f2c4aa1645a962dfe7f67389ce2523408e69bdfe091ff7ecf6cbe19e33c1 | 1126f2c4aa1645a962dfe7f67389ce2523408e69bdfe091ff7ecf6cbe19e33c1 |
| src/hermes/app/chat/composer/help-hint.tsx | verbatim | apps/desktop/src/app/chat/composer/help-hint.tsx | 9ef57a760fae861433bf2f2bc4899df68aaeb6842971ed516b35c6607262d9df | 9ef57a760fae861433bf2f2bc4899df68aaeb6842971ed516b35c6607262d9df |
| src/hermes/app/chat/composer/hooks/completion-route.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/completion-route.ts | a774fdedafb3b242e0e1f7f326dc087b5125315e35ef1e03d39aa5b928c248fb | a774fdedafb3b242e0e1f7f326dc087b5125315e35ef1e03d39aa5b928c248fb |
| src/hermes/app/chat/composer/hooks/slash-localization.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/slash-localization.test.tsx | c14cb92335b252f687e103611dd0236251e0812893104fb53473063bb4a5c4b3 | c14cb92335b252f687e103611dd0236251e0812893104fb53473063bb4a5c4b3 |
| src/hermes/app/chat/composer/hooks/use-at-completions-contrib.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-at-completions-contrib.test.tsx | 0aa5a72db14a6b86fecc8ed4842f9b7398fc0074c4f0fd126891ff75d0885750 | 0aa5a72db14a6b86fecc8ed4842f9b7398fc0074c4f0fd126891ff75d0885750 |
| src/hermes/app/chat/composer/hooks/use-at-completions.test.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-at-completions.test.ts | 791855a5e39ef46a89655d0b2903951b22f277ac3d4b8c3ecb46c1462be9e263 | 791855a5e39ef46a89655d0b2903951b22f277ac3d4b8c3ecb46c1462be9e263 |
| src/hermes/app/chat/composer/hooks/use-at-completions.ts | patch | apps/desktop/src/app/chat/composer/hooks/use-at-completions.ts | f58d067c5c70d6cd6240f8202f1d77b30b64f224e4f1ccd1bd6f002d75ccf26c | ea09a241b8ecbb79ebe773f0fa9eb86ca173da8efe3b90ca2c530739e533da73 |
| src/hermes/app/chat/composer/hooks/use-auto-speak-replies.owner.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-auto-speak-replies.owner.test.tsx | 2c899380b5fd3ccfd9036bdb5b84898e35357460e343b0705771f92fcb2eeddb | 2c899380b5fd3ccfd9036bdb5b84898e35357460e343b0705771f92fcb2eeddb |
| src/hermes/app/chat/composer/hooks/use-auto-speak-replies.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-auto-speak-replies.test.tsx | 5799e48ca5f5cd909e7e3080b4816c307ea410aa5584f2a971ff0fe85d9065b6 | 5799e48ca5f5cd909e7e3080b4816c307ea410aa5584f2a971ff0fe85d9065b6 |
| src/hermes/app/chat/composer/hooks/use-auto-speak-replies.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-auto-speak-replies.ts | 96abedd00d6c93ea7922750efe14737dfd35b10f16426945aa43faa6a6691e17 | 96abedd00d6c93ea7922750efe14737dfd35b10f16426945aa43faa6a6691e17 |
| src/hermes/app/chat/composer/hooks/use-card-waiting-submit.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-card-waiting-submit.ts | b5413d885eb12ea85649e89d278ac736a4690c25cd34627f3331d2cee1644a55 | b5413d885eb12ea85649e89d278ac736a4690c25cd34627f3331d2cee1644a55 |
| src/hermes/app/chat/composer/hooks/use-completions-owner-route.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-completions-owner-route.test.tsx | 13cc69b7e4fd11fc0f7cf0b2b294897148320c2e1d5dc1b0849dcb5e79bae5e2 | 13cc69b7e4fd11fc0f7cf0b2b294897148320c2e1d5dc1b0849dcb5e79bae5e2 |
| src/hermes/app/chat/composer/hooks/use-composer-branch.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-branch.ts | fd871210bffd5b12cf92ccaf5ef69ff326a12dedce739cac058488416f8130ee | fd871210bffd5b12cf92ccaf5ef69ff326a12dedce739cac058488416f8130ee |
| src/hermes/app/chat/composer/hooks/use-composer-draft.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-draft.test.tsx | e063d04665bfead609acb100f4a9990039bdb1b3b55a48b9e89a64ba3586136c | e063d04665bfead609acb100f4a9990039bdb1b3b55a48b9e89a64ba3586136c |
| src/hermes/app/chat/composer/hooks/use-composer-draft.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-draft.ts | 24872e7ee8d07f6e9aa4d414e8898e817a172f23b1f7031a1ce1c24aa46bb423 | 24872e7ee8d07f6e9aa4d414e8898e817a172f23b1f7031a1ce1c24aa46bb423 |
| src/hermes/app/chat/composer/hooks/use-composer-drop.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-drop.ts | 235dc0ffe18485e0dbe04847ae986ec62bf949e948b79e12e8a606b0be57702f | 235dc0ffe18485e0dbe04847ae986ec62bf949e948b79e12e8a606b0be57702f |
| src/hermes/app/chat/composer/hooks/use-composer-esc-cancel.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-esc-cancel.test.tsx | 50bb73d2b6e36668b5d095239104af32895a07e47fba61f8632f696401ae74ce | 50bb73d2b6e36668b5d095239104af32895a07e47fba61f8632f696401ae74ce |
| src/hermes/app/chat/composer/hooks/use-composer-esc-cancel.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-esc-cancel.ts | 35673366d5a8f86145815cf56c0189d26d61184183c6422f156570c419d710ae | 35673366d5a8f86145815cf56c0189d26d61184183c6422f156570c419d710ae |
| src/hermes/app/chat/composer/hooks/use-composer-metrics.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-metrics.test.tsx | 6143bf1a6e26a898027b7c6db9eeaab3f7f9eeb8943b9e970d24643b8c3ee893 | 6143bf1a6e26a898027b7c6db9eeaab3f7f9eeb8943b9e970d24643b8c3ee893 |
| src/hermes/app/chat/composer/hooks/use-composer-metrics.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-metrics.ts | 846175b51b1c41c2705b51a12365bd3a1135c168bfb03f36dcba72f109a3c033 | 846175b51b1c41c2705b51a12365bd3a1135c168bfb03f36dcba72f109a3c033 |
| src/hermes/app/chat/composer/hooks/use-composer-placeholder.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-placeholder.ts | 8595d632af51b64693e31ae6349c6a7c80961fde8de011e22177a1831ce08a25 | 8595d632af51b64693e31ae6349c6a7c80961fde8de011e22177a1831ce08a25 |
| src/hermes/app/chat/composer/hooks/use-composer-popout.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-popout.test.tsx | a27a525ad3b54be249553484c918991146030c9b2fec15262e2045980b853b3b | a27a525ad3b54be249553484c918991146030c9b2fec15262e2045980b853b3b |
| src/hermes/app/chat/composer/hooks/use-composer-popout.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-popout.ts | f9eca26ec0c1dd5754117dad97bc5fb8503f5287f0483333f109eb0935f32986 | f9eca26ec0c1dd5754117dad97bc5fb8503f5287f0483333f109eb0935f32986 |
| src/hermes/app/chat/composer/hooks/use-composer-queue.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-queue.test.tsx | dbe2746a116f4c9f0566bc9767e830f64ddbcb230f4fbade7d9d7eea475af4c1 | dbe2746a116f4c9f0566bc9767e830f64ddbcb230f4fbade7d9d7eea475af4c1 |
| src/hermes/app/chat/composer/hooks/use-composer-queue.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-queue.ts | 225690b67209c93dc3327df9fa1659af08b39773f94a480ed4da20fb35e31684 | 225690b67209c93dc3327df9fa1659af08b39773f94a480ed4da20fb35e31684 |
| src/hermes/app/chat/composer/hooks/use-composer-screenshot.ts | shim | apps/desktop/src/app/chat/composer/hooks/use-composer-screenshot.ts | 1cd15b69f935fb50c23c45bf29b3e81a769031ae0c8af44301ba0e6a2a2f75a5 | 698470f33cdc3265e0039607ee3f75e9cb865b88962a03197c06e0d23a7ec0dd |
| src/hermes/app/chat/composer/hooks/use-composer-submit.restore-scoping.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-submit.restore-scoping.test.tsx | 9160df16e6c7311c0fccbcdb9f1aa71713dcd56e42a5842dab135759f53b25fb | 9160df16e6c7311c0fccbcdb9f1aa71713dcd56e42a5842dab135759f53b25fb |
| src/hermes/app/chat/composer/hooks/use-composer-submit.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-submit.test.tsx | 14e24c66f971013d3e8220b348b6039c98d0cfc8513a74b15979e767bb82570b | 14e24c66f971013d3e8220b348b6039c98d0cfc8513a74b15979e767bb82570b |
| src/hermes/app/chat/composer/hooks/use-composer-submit.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-submit.ts | a7b5091e42f231f831dd4aae3ee4efd91117fd0744fd53b1d6d392c4ffb3a01e | a7b5091e42f231f831dd4aae3ee4efd91117fd0744fd53b1d6d392c4ffb3a01e |
| src/hermes/app/chat/composer/hooks/use-composer-trigger.test.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-trigger.test.ts | 8b869883d815725b2ae8011fe13ee58723ed80efcaae1fd95df965caabf9f39b | 8b869883d815725b2ae8011fe13ee58723ed80efcaae1fd95df965caabf9f39b |
| src/hermes/app/chat/composer/hooks/use-composer-trigger.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-trigger.ts | a6b816a4b2e081b795240dc51c6bb42acdec3b598930d5d61c4a62a00c4dde20 | a6b816a4b2e081b795240dc51c6bb42acdec3b598930d5d61c4a62a00c4dde20 |
| src/hermes/app/chat/composer/hooks/use-composer-undo.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-undo.test.tsx | 3c9aaa1dde0aaa1ff6d28aadf6f81f8fa8f51a86cd9ab7de408e92069c5a90ef | 3c9aaa1dde0aaa1ff6d28aadf6f81f8fa8f51a86cd9ab7de408e92069c5a90ef |
| src/hermes/app/chat/composer/hooks/use-composer-undo.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-undo.ts | eb8aa78923801bef2be2ac7bd2db87ea131cd48ad10a340773dcce8988e4e8b5 | eb8aa78923801bef2be2ac7bd2db87ea131cd48ad10a340773dcce8988e4e8b5 |
| src/hermes/app/chat/composer/hooks/use-composer-url-dialog.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-url-dialog.test.tsx | 364488964d099307054595732d697f9fa7766e3f5f8a341a7ff546e6917871fd | 364488964d099307054595732d697f9fa7766e3f5f8a341a7ff546e6917871fd |
| src/hermes/app/chat/composer/hooks/use-composer-url-dialog.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-url-dialog.ts | 7719160567b044da8680228e7a437c55bd8996d86c945e75a74462c8722b7a8a | 7719160567b044da8680228e7a437c55bd8996d86c945e75a74462c8722b7a8a |
| src/hermes/app/chat/composer/hooks/use-composer-voice-shortcuts.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-voice-shortcuts.test.tsx | 4ce032961d372226a726711da2b516cc59623745d0f7379e8de78a22dc7f6070 | 4ce032961d372226a726711da2b516cc59623745d0f7379e8de78a22dc7f6070 |
| src/hermes/app/chat/composer/hooks/use-composer-voice.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-composer-voice.test.tsx | 8334cb66f1793d043f44bbba1eefa357d33dd7ace83d3c65fb3bba8bdf36d51b | 8334cb66f1793d043f44bbba1eefa357d33dd7ace83d3c65fb3bba8bdf36d51b |
| src/hermes/app/chat/composer/hooks/use-composer-voice.ts | shim | apps/desktop/src/app/chat/composer/hooks/use-composer-voice.ts | b45727db67819a91f2a92cec55edf1a06166fc807a36a78e1104b6e2938980e8 | 8cf0ae38ef5efe16c41bd5c53a6449a88e9fdcd70fd00bfa9bc7cdbde2c82717 |
| src/hermes/app/chat/composer/hooks/use-emoji-completions.ts | patch | apps/desktop/src/app/chat/composer/hooks/use-emoji-completions.ts | ab3a598fb4463bdb5d3d7f2275a1883e68b828e648791648d9df491cb1a0c4aa | fcd3ed1809a5681608e3c08e80ab8e5d6c33b89569268f8a56edf3f53541288d |
| src/hermes/app/chat/composer/hooks/use-live-completion-adapter.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-live-completion-adapter.ts | d4413dc1093abd018dee48e80437752d9882279d515d530518c16b5a1baa3fb3 | d4413dc1093abd018dee48e80437752d9882279d515d530518c16b5a1baa3fb3 |
| src/hermes/app/chat/composer/hooks/use-mic-recorder.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-mic-recorder.test.tsx | 715191408701202a4d1b8cb54d6b0098170a14ceb61dd5b417f123c12ce6131a | 715191408701202a4d1b8cb54d6b0098170a14ceb61dd5b417f123c12ce6131a |
| src/hermes/app/chat/composer/hooks/use-mic-recorder.ts | patch | apps/desktop/src/app/chat/composer/hooks/use-mic-recorder.ts | e47f3c24dc3793df4579f1c65863062fe56dc02c86f8231eaff57eba37ecd08d | 1c908a8983c489de73ffd3d1dddc8ecb90c66a1c3c141bfacb8a7bb40fa3132e |
| src/hermes/app/chat/composer/hooks/use-micro-actions.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-micro-actions.ts | e701d9b04438a4799fb4ee89920aeb0c28cf2d41143e92a7fdace58232025ea9 | e701d9b04438a4799fb4ee89920aeb0c28cf2d41143e92a7fdace58232025ea9 |
| src/hermes/app/chat/composer/hooks/use-middleware-submit.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-middleware-submit.ts | bc37746f17b1d48774133b50835c975606df14715f8dcc7f4c3b9e2929c7b15d | bc37746f17b1d48774133b50835c975606df14715f8dcc7f4c3b9e2929c7b15d |
| src/hermes/app/chat/composer/hooks/use-popout-drag.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-popout-drag.test.tsx | 75a0b2c432cdc4328fc60eff0bbf82cefebd7f0a84543db7b3935b5b9e31f2fe | 75a0b2c432cdc4328fc60eff0bbf82cefebd7f0a84543db7b3935b5b9e31f2fe |
| src/hermes/app/chat/composer/hooks/use-popout-drag.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-popout-drag.ts | 5815823af8a913b69d743a7cf7ef657847cebf3c00a06fe2143e9f597630f861 | 5815823af8a913b69d743a7cf7ef657847cebf3c00a06fe2143e9f597630f861 |
| src/hermes/app/chat/composer/hooks/use-slash-completions.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-slash-completions.test.tsx | e9ebe73218d1881e1ba3c62defff0fbd147ae0f24650ad68a5af1ff2da764bec | e9ebe73218d1881e1ba3c62defff0fbd147ae0f24650ad68a5af1ff2da764bec |
| src/hermes/app/chat/composer/hooks/use-slash-completions.ts | patch | apps/desktop/src/app/chat/composer/hooks/use-slash-completions.ts | 5f3de85ce99ec76fec6d0d9dd7a100b0e59eb2e155d9ad929da8e5bfaf548cdf | d9527fb452710cf47917294b845d79b9f689762a5e51c24e91c3a7a4a42c07eb |
| src/hermes/app/chat/composer/hooks/use-status-drawer.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-status-drawer.test.tsx | 23c463d1531068286822f91fd80331a4f487b3f17559750a49db02d9b2b7ab99 | 23c463d1531068286822f91fd80331a4f487b3f17559750a49db02d9b2b7ab99 |
| src/hermes/app/chat/composer/hooks/use-status-drawer.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-status-drawer.ts | 2c026d58a9516af63f0526477ac26852581e05858c296acf1c331f73e3dc5047 | 2c026d58a9516af63f0526477ac26852581e05858c296acf1c331f73e3dc5047 |
| src/hermes/app/chat/composer/hooks/use-status-presence.test.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-status-presence.test.ts | 1f4ea40d1d7fd81825a7acbed6426f9f28e559b063f6374ef685c7efd35e4369 | 1f4ea40d1d7fd81825a7acbed6426f9f28e559b063f6374ef685c7efd35e4369 |
| src/hermes/app/chat/composer/hooks/use-status-presence.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-status-presence.ts | 00eae005ee122a7bbabcb67a2b5ba23e5e0bce44d572b1b4cb78642ddd41ba3f | 00eae005ee122a7bbabcb67a2b5ba23e5e0bce44d572b1b4cb78642ddd41ba3f |
| src/hermes/app/chat/composer/hooks/use-voice-conversation-boundary.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation-boundary.test.tsx | fccd354a69c2644f41c8c3bae1a3d16ca16a290b21b4cb705a2a5f75cc5640fe | fccd354a69c2644f41c8c3bae1a3d16ca16a290b21b4cb705a2a5f75cc5640fe |
| src/hermes/app/chat/composer/hooks/use-voice-conversation-meter.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation-meter.test.tsx | a4922e486a2cf7e46779ad82947f68dce96cc11d6bfe91cbeaf93bd8735e5cac | a4922e486a2cf7e46779ad82947f68dce96cc11d6bfe91cbeaf93bd8735e5cac |
| src/hermes/app/chat/composer/hooks/use-voice-conversation-rearm.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation-rearm.test.tsx | 50110c63c7e8154f4869af0c8dc00553f8f2a8f81ff43b3ed78892b179311b54 | 50110c63c7e8154f4869af0c8dc00553f8f2a8f81ff43b3ed78892b179311b54 |
| src/hermes/app/chat/composer/hooks/use-voice-conversation-stale-reply.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation-stale-reply.test.tsx | c238140457b1584304af1040095c181f300701bfab2b4e79e275077b49af82ac | c238140457b1584304af1040095c181f300701bfab2b4e79e275077b49af82ac |
| src/hermes/app/chat/composer/hooks/use-voice-conversation-warmup.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation-warmup.test.tsx | 119ccf7c91a84ef1f7ab73db4a3fb84c805e704e6f56715582f8d898ec9d438c | 119ccf7c91a84ef1f7ab73db4a3fb84c805e704e6f56715582f8d898ec9d438c |
| src/hermes/app/chat/composer/hooks/use-voice-conversation.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation.test.tsx | 3e4f3f741c876f955a99d628d12a812dd4dbd43a51c1dae8e27a1df9eb9529d6 | 3e4f3f741c876f955a99d628d12a812dd4dbd43a51c1dae8e27a1df9eb9529d6 |
| src/hermes/app/chat/composer/hooks/use-voice-conversation.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-conversation.ts | a11dfeecea94d12b32697cec4e6743af07369b25a0c4abe792b536828daea295 | a11dfeecea94d12b32697cec4e6743af07369b25a0c4abe792b536828daea295 |
| src/hermes/app/chat/composer/hooks/use-voice-live-conversation.owner.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-live-conversation.owner.test.tsx | 8668744647ce99e360efad2896d67f03ee2bbbca0f65cd4a78ce3fa7c7c440e7 | 8668744647ce99e360efad2896d67f03ee2bbbca0f65cd4a78ce3fa7c7c440e7 |
| src/hermes/app/chat/composer/hooks/use-voice-live-conversation.test.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-live-conversation.test.ts | 9659c47a54e40983bdda7929f5410c0d8e6915859594994ca294c04a1a36f5ab | 9659c47a54e40983bdda7929f5410c0d8e6915859594994ca294c04a1a36f5ab |
| src/hermes/app/chat/composer/hooks/use-voice-live-conversation.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-live-conversation.ts | 362a0c1ae740ca644ed410f5b2e75f59c8ec09535c97ff5bd478fd2377ea32af | 362a0c1ae740ca644ed410f5b2e75f59c8ec09535c97ff5bd478fd2377ea32af |
| src/hermes/app/chat/composer/hooks/use-voice-recorder-warmup.test.tsx | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-recorder-warmup.test.tsx | e9c577dac0eceabae3304ae58321aab510c89ee2400900bb9d72bd69e0dc55f9 | e9c577dac0eceabae3304ae58321aab510c89ee2400900bb9d72bd69e0dc55f9 |
| src/hermes/app/chat/composer/hooks/use-voice-recorder.ts | verbatim | apps/desktop/src/app/chat/composer/hooks/use-voice-recorder.ts | 00eba22c001284ec5ea76376868faf70b8ab9e930d2a7afd8479d0dc60c660ac | 00eba22c001284ec5ea76376868faf70b8ab9e930d2a7afd8479d0dc60c660ac |
| src/hermes/app/chat/composer/index.tsx | patch | apps/desktop/src/app/chat/composer/index.tsx | 592d9e15ca9e16c5e61bebf5b236245a33ff61b43653976a7e0e6bec8234bf7a | 9d8a2bef73c5e93b472c504055113ede1b8756851f154b54fcb2e9143ffdb5bc |
| src/hermes/app/chat/composer/inline-refs.ts | verbatim | apps/desktop/src/app/chat/composer/inline-refs.ts | 12a9415ec20c686586b2a14b870c53ca63dc2c081c8ed9eb3931b458059b6a59 | 12a9415ec20c686586b2a14b870c53ca63dc2c081c8ed9eb3931b458059b6a59 |
| src/hermes/app/chat/composer/large-paste.test.ts | verbatim | apps/desktop/src/app/chat/composer/large-paste.test.ts | e79f1b5428c2f8b0e4048b44fca56f769b27935af0f73091b645ba2e49b26249 | e79f1b5428c2f8b0e4048b44fca56f769b27935af0f73091b645ba2e49b26249 |
| src/hermes/app/chat/composer/large-paste.ts | verbatim | apps/desktop/src/app/chat/composer/large-paste.ts | 6de08ab1b71d59688911f536bffa2205884c2783393bbf76654ab7f29adde32a | 6de08ab1b71d59688911f536bffa2205884c2783393bbf76654ab7f29adde32a |
| src/hermes/app/chat/composer/micro-actions.tsx | verbatim | apps/desktop/src/app/chat/composer/micro-actions.tsx | 521c452fb41110d010fec1452d066588f437e3f3240b8c0ffd8230c56cef0c9e | 521c452fb41110d010fec1452d066588f437e3f3240b8c0ffd8230c56cef0c9e |
| src/hermes/app/chat/composer/model-pill.test.tsx | verbatim | apps/desktop/src/app/chat/composer/model-pill.test.tsx | 2d872ee49cdf7a92e2b1dd6afe42983a63136f0e58167c04ce3ccc6eeded4587 | 2d872ee49cdf7a92e2b1dd6afe42983a63136f0e58167c04ce3ccc6eeded4587 |
| src/hermes/app/chat/composer/model-pill.tsx | verbatim | apps/desktop/src/app/chat/composer/model-pill.tsx | a74193a0801c098705285d1bd125e6e324cec393bf6a5d5b27c533561aaf7d71 | a74193a0801c098705285d1bd125e6e324cec393bf6a5d5b27c533561aaf7d71 |
| src/hermes/app/chat/composer/paste-to-focus.test.ts | verbatim | apps/desktop/src/app/chat/composer/paste-to-focus.test.ts | d5b55a25c70006816406a9c89a8107f66f552632e0976d85bcd5eb77c57fae51 | d5b55a25c70006816406a9c89a8107f66f552632e0976d85bcd5eb77c57fae51 |
| src/hermes/app/chat/composer/paste-to-focus.ts | verbatim | apps/desktop/src/app/chat/composer/paste-to-focus.ts | b59e1fe98502c681ae410338045bf205648ebf86d1902e5703402ab90eef4045 | b59e1fe98502c681ae410338045bf205648ebf86d1902e5703402ab90eef4045 |
| src/hermes/app/chat/composer/path-refs.test.ts | verbatim | apps/desktop/src/app/chat/composer/path-refs.test.ts | 87bc09a047fb298eba7eba43bb2488f910ccc898ef3c72f15616da3f599e6ca3 | 87bc09a047fb298eba7eba43bb2488f910ccc898ef3c72f15616da3f599e6ca3 |
| src/hermes/app/chat/composer/path-refs.ts | verbatim | apps/desktop/src/app/chat/composer/path-refs.ts | 980ab8af1e242a67e6c4eccfbf6ce973c9b1b2a5727dad4d55de4971938cad76 | 980ab8af1e242a67e6c4eccfbf6ce973c9b1b2a5727dad4d55de4971938cad76 |
| src/hermes/app/chat/composer/prepared-image-recovery.test.tsx | verbatim | apps/desktop/src/app/chat/composer/prepared-image-recovery.test.tsx | 42c227cb9781055c1cf48c3c5799702b88fa55d9900365e2c1c591094be304a9 | 42c227cb9781055c1cf48c3c5799702b88fa55d9900365e2c1c591094be304a9 |
| src/hermes/app/chat/composer/prepared-image-recovery.tsx | verbatim | apps/desktop/src/app/chat/composer/prepared-image-recovery.tsx | 388d58b5c2dc8a58831c0579dbbeea42ca5fafafc20d20b7f9b8d7cbdd19c3ba | 388d58b5c2dc8a58831c0579dbbeea42ca5fafafc20d20b7f9b8d7cbdd19c3ba |
| src/hermes/app/chat/composer/queue-panel.test.tsx | verbatim | apps/desktop/src/app/chat/composer/queue-panel.test.tsx | 03f0918b41bc9bd4bc8d7b449c9c70b590c50d094bd642c726e1d282c166b1a8 | 03f0918b41bc9bd4bc8d7b449c9c70b590c50d094bd642c726e1d282c166b1a8 |
| src/hermes/app/chat/composer/queue-panel.tsx | verbatim | apps/desktop/src/app/chat/composer/queue-panel.tsx | be3e1a9b90d9da013ba9a0f379e6e4687d29a955dec5880dcb6a181ba196179b | be3e1a9b90d9da013ba9a0f379e6e4687d29a955dec5880dcb6a181ba196179b |
| src/hermes/app/chat/composer/queue-slot.test.tsx | verbatim | apps/desktop/src/app/chat/composer/queue-slot.test.tsx | 127969955084389f3a242fe956174b34f85b9e5365cb9b1765a9bbe076e37a9b | 127969955084389f3a242fe956174b34f85b9e5365cb9b1765a9bbe076e37a9b |
| src/hermes/app/chat/composer/queue-slot.tsx | verbatim | apps/desktop/src/app/chat/composer/queue-slot.tsx | 6deb8444cee32d945da22a397056b01d3ad04386bb0e81c562d843c478a88e93 | 6deb8444cee32d945da22a397056b01d3ad04386bb0e81c562d843c478a88e93 |
| src/hermes/app/chat/composer/reasoning-pill.test.tsx | verbatim | apps/desktop/src/app/chat/composer/reasoning-pill.test.tsx | 0c194588e2364d573e1d955b91ca9a67f5381277a6496615d9ef5e58b03ac376 | 0c194588e2364d573e1d955b91ca9a67f5381277a6496615d9ef5e58b03ac376 |
| src/hermes/app/chat/composer/reasoning-pill.tsx | verbatim | apps/desktop/src/app/chat/composer/reasoning-pill.tsx | 1f74358c6a5a7209cc7a66031d1d1b8aa1a07c7d4ac24613bfdd7e6dcc6885d1 | 1f74358c6a5a7209cc7a66031d1d1b8aa1a07c7d4ac24613bfdd7e6dcc6885d1 |
| src/hermes/app/chat/composer/restored-draft-notice.tsx | verbatim | apps/desktop/src/app/chat/composer/restored-draft-notice.tsx | 7bb6c8ee63e5506ca93549741e5528ec14072fb631684c37965ee6fe4d0ccacd | 7bb6c8ee63e5506ca93549741e5528ec14072fb631684c37965ee6fe4d0ccacd |
| src/hermes/app/chat/composer/rich-editor.test.ts | verbatim | apps/desktop/src/app/chat/composer/rich-editor.test.ts | 5175167a2502fda26e455a47fd584ff7345fa97da9b0aa3efb098f2ca1b54f54 | 5175167a2502fda26e455a47fd584ff7345fa97da9b0aa3efb098f2ca1b54f54 |
| src/hermes/app/chat/composer/rich-editor.ts | verbatim | apps/desktop/src/app/chat/composer/rich-editor.ts | ed3122724e5a928754e3b7e43efe0cb49b664854732998229728bfd74df3fd9b | ed3122724e5a928754e3b7e43efe0cb49b664854732998229728bfd74df3fd9b |
| src/hermes/app/chat/composer/scope.tsx | verbatim | apps/desktop/src/app/chat/composer/scope.tsx | ab57efde8cb9c424a9e7ea6be86362c70975757d4e915ae071e05bd4dedab954 | ab57efde8cb9c424a9e7ea6be86362c70975757d4e915ae071e05bd4dedab954 |
| src/hermes/app/chat/composer/slash-refs.test.ts | verbatim | apps/desktop/src/app/chat/composer/slash-refs.test.ts | 35ad538e3eb82f903d47d2747680b87ba78cea00bbc5fbead9d235be4a09684b | 35ad538e3eb82f903d47d2747680b87ba78cea00bbc5fbead9d235be4a09684b |
| src/hermes/app/chat/composer/slash-refs.ts | verbatim | apps/desktop/src/app/chat/composer/slash-refs.ts | 4ec6bc1669c935ed06776c802006f0764582e55371199cd0fc96be3d674b077d | 4ec6bc1669c935ed06776c802006f0764582e55371199cd0fc96be3d674b077d |
| src/hermes/app/chat/composer/start-voice-button.tsx | verbatim | apps/desktop/src/app/chat/composer/start-voice-button.tsx | a62c7679d145aa51c6ddd06f606d9a08b821659226e38243a5680dcb1fb71a9f | a62c7679d145aa51c6ddd06f606d9a08b821659226e38243a5680dcb1fb71a9f |
| src/hermes/app/chat/composer/status-stack/coding-row.test.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/coding-row.test.tsx | 9be1e72d3e6b12e698e2b5a1d053a785f7ab359d799dcee0f1099bd98715920f | 9be1e72d3e6b12e698e2b5a1d053a785f7ab359d799dcee0f1099bd98715920f |
| src/hermes/app/chat/composer/status-stack/coding-row.tsx | shim | apps/desktop/src/app/chat/composer/status-stack/coding-row.tsx | 3c94bb8ba4c359a0dc249ec4b13169b34b535503f15a1b27d71d5e1a0b74100a | 8407bc085ad27e64a45f88ac5758eb450323003b705116a7c9804ec2381dd647 |
| src/hermes/app/chat/composer/status-stack/drawer.css | verbatim | apps/desktop/src/app/chat/composer/status-stack/drawer.css | 274aaa15ced708e4c13ba3a3abb90594f2b477c0c76473b9716444f6699437d5 | 274aaa15ced708e4c13ba3a3abb90594f2b477c0c76473b9716444f6699437d5 |
| src/hermes/app/chat/composer/status-stack/drawer.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/drawer.tsx | a6601f5a70da6e40daed00ae217d980af506ebeb8dee23295ccc490eefaabc09 | a6601f5a70da6e40daed00ae217d980af506ebeb8dee23295ccc490eefaabc09 |
| src/hermes/app/chat/composer/status-stack/index.tsx | patch | apps/desktop/src/app/chat/composer/status-stack/index.tsx | 48017cbcbdffea652bb490da975155818d9d91f775cd9e389717db7c88a8a87e | c8a821396cfe087b5196b5dd4a7a979d2f40b8fd373cc86eda59d11560d1c3a2 |
| src/hermes/app/chat/composer/status-stack/preview-row.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/preview-row.tsx | e940c42cd88076f63995d90e87757ba9d4c9d14cb2b9a907a2e019d4be4be65e | e940c42cd88076f63995d90e87757ba9d4c9d14cb2b9a907a2e019d4be4be65e |
| src/hermes/app/chat/composer/status-stack/session-control-goal.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control-goal.tsx | c39f33a1b82a8f4324cc9a280772a933fab67024c40da6521dfe36c1537aa4a0 | c39f33a1b82a8f4324cc9a280772a933fab67024c40da6521dfe36c1537aa4a0 |
| src/hermes/app/chat/composer/status-stack/session-control-heartbeat.test.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control-heartbeat.test.tsx | 08ef6e81457abfef15c42d22dce8eac67ed5fbcebd3d7e548227ab1a86866aa6 | 08ef6e81457abfef15c42d22dce8eac67ed5fbcebd3d7e548227ab1a86866aa6 |
| src/hermes/app/chat/composer/status-stack/session-control-heartbeat.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control-heartbeat.tsx | ba947f371dd6394cf1d97f0276f3dd4c87404f63a1aafa3c060635308b6fa808 | ba947f371dd6394cf1d97f0276f3dd4c87404f63a1aafa3c060635308b6fa808 |
| src/hermes/app/chat/composer/status-stack/session-control-loop.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control-loop.tsx | f7a346480e3c30748612a593bd8d725d86895ed676f38587729d0ee699784de8 | f7a346480e3c30748612a593bd8d725d86895ed676f38587729d0ee699784de8 |
| src/hermes/app/chat/composer/status-stack/session-control-utils.ts | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control-utils.ts | 21b05f76bdfb685ff40f2782ddc5b18eff60fc7f7beb9945df0d69d1d23ba8a2 | 21b05f76bdfb685ff40f2782ddc5b18eff60fc7f7beb9945df0d69d1d23ba8a2 |
| src/hermes/app/chat/composer/status-stack/session-control.test.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control.test.tsx | 0e7af026a73bac5dafd6f8a0b5fd7994b2d0f7be17cedc23057c82cd9ceeb29c | 0e7af026a73bac5dafd6f8a0b5fd7994b2d0f7be17cedc23057c82cd9ceeb29c |
| src/hermes/app/chat/composer/status-stack/session-control.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/session-control.tsx | cab6e27af5c45c7b2c810a20aad565ad506b5b37384c992543448cf3275dcbf7 | cab6e27af5c45c7b2c810a20aad565ad506b5b37384c992543448cf3275dcbf7 |
| src/hermes/app/chat/composer/status-stack/status-row.tsx | patch | apps/desktop/src/app/chat/composer/status-stack/status-row.tsx | 958b68e63c61071a3c207a9a08e8d029ba6e2a32d2e194c886849133d94877e0 | 840bc48d6d5d1a3a400d1447d893e545074bebd174b12ee34c977bf36eb7979d |
| src/hermes/app/chat/composer/status-stack/subagent-controls.test.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/subagent-controls.test.tsx | b962f99dc0af2a9ea3c17c3dacfb2d9317330964a09c999432135b917283c8b6 | b962f99dc0af2a9ea3c17c3dacfb2d9317330964a09c999432135b917283c8b6 |
| src/hermes/app/chat/composer/status-stack/subagent-controls.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/subagent-controls.tsx | 047bbb8930c3d577ec012d1f232740730885111f2feb2f4524d8dd5288b3f757 | 047bbb8930c3d577ec012d1f232740730885111f2feb2f4524d8dd5288b3f757 |
| src/hermes/app/chat/composer/status-stack/subagent-section.test.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/subagent-section.test.tsx | 2841062f127170e34dacfa01ced756c18eacc5a3cd83b1f4c8d769d5345e7183 | 2841062f127170e34dacfa01ced756c18eacc5a3cd83b1f4c8d769d5345e7183 |
| src/hermes/app/chat/composer/status-stack/subagent-section.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/subagent-section.tsx | ddd7839cb7e9fc88d54c1acc9b803af73ce5079bd75f7db83f7f646cb9842c1b | ddd7839cb7e9fc88d54c1acc9b803af73ce5079bd75f7db83f7f646cb9842c1b |
| src/hermes/app/chat/composer/status-stack/subagent-transcript.tsx | verbatim | apps/desktop/src/app/chat/composer/status-stack/subagent-transcript.tsx | d86db7cc18606018e9495a094f62ce03f9306728ab59ac23e24b39deb9a33cd9 | d86db7cc18606018e9495a094f62ce03f9306728ab59ac23e24b39deb9a33cd9 |
| src/hermes/app/chat/composer/status-stack/use-subagent-snapshot.ts | patch | apps/desktop/src/app/chat/composer/status-stack/use-subagent-snapshot.ts | bf796edf7007a22e1b61901e5e98303034523bc7e02580638c6fdc763870bea5 | 3bb037394a1d88b9a8ad0e73af32c3abc15e5c2a3ff72653464f7509acb90054 |
| src/hermes/app/chat/composer/submission-types.ts | new | — | — | 5d1d3460e6e9ae71b1710b0fcf1f810e663ce35e10f033cee6de3ecb44ad4a5d |
| src/hermes/app/chat/composer/test-utils.ts | verbatim | apps/desktop/src/app/chat/composer/test-utils.ts | 5f648c0fc3da53b951d5e940b62e022dea7d647b14b676a1aef2f18946fd23d6 | 5f648c0fc3da53b951d5e940b62e022dea7d647b14b676a1aef2f18946fd23d6 |
| src/hermes/app/chat/composer/text-utils.test.ts | verbatim | apps/desktop/src/app/chat/composer/text-utils.test.ts | fc6d5b91502e2718e40dbadb21b2a71fe05f43f9f135473d8a42374c6044612a | fc6d5b91502e2718e40dbadb21b2a71fe05f43f9f135473d8a42374c6044612a |
| src/hermes/app/chat/composer/text-utils.ts | verbatim | apps/desktop/src/app/chat/composer/text-utils.ts | c2aae70695092d18eba913d2e611c79dd6b13fe8b5a37a0db04bddcbeec0ab02 | c2aae70695092d18eba913d2e611c79dd6b13fe8b5a37a0db04bddcbeec0ab02 |
| src/hermes/app/chat/composer/trigger-popover-description.test.tsx | verbatim | apps/desktop/src/app/chat/composer/trigger-popover-description.test.tsx | 8d21dd0fe8d4ce156626db5070290ff25482638546d8a40ad19f8ccfde3b3513 | 8d21dd0fe8d4ce156626db5070290ff25482638546d8a40ad19f8ccfde3b3513 |
| src/hermes/app/chat/composer/trigger-popover.test.tsx | verbatim | apps/desktop/src/app/chat/composer/trigger-popover.test.tsx | 6aed2b84fc5040ad73a0e69272b7be6a92241813b1f3e8e782467c6c9e1073ab | 6aed2b84fc5040ad73a0e69272b7be6a92241813b1f3e8e782467c6c9e1073ab |
| src/hermes/app/chat/composer/trigger-popover.tsx | verbatim | apps/desktop/src/app/chat/composer/trigger-popover.tsx | cab8b91fa24359c22fcb704aeff7ab993d1928f777af667f6a070c99e35a51ab | cab8b91fa24359c22fcb704aeff7ab993d1928f777af667f6a070c99e35a51ab |
| src/hermes/app/chat/composer/undo-history.test.ts | verbatim | apps/desktop/src/app/chat/composer/undo-history.test.ts | 52b8bfda6ace9a60f5f3e50fb752dc2d001c1e0a3616d58c3e6d91773562af1c | 52b8bfda6ace9a60f5f3e50fb752dc2d001c1e0a3616d58c3e6d91773562af1c |
| src/hermes/app/chat/composer/undo-history.ts | verbatim | apps/desktop/src/app/chat/composer/undo-history.ts | 0d7d7ae615ba5ab297d3b46aca185fb52eb1bd04fe10fbb4a272aaf3d59c0e80 | 0d7d7ae615ba5ab297d3b46aca185fb52eb1bd04fe10fbb4a272aaf3d59c0e80 |
| src/hermes/app/chat/composer/url-dialog.tsx | verbatim | apps/desktop/src/app/chat/composer/url-dialog.tsx | 1ba7ba70ce02d37f44e862386cbde18b8c50caee48224203c8d8fd62a19b0000 | 1ba7ba70ce02d37f44e862386cbde18b8c50caee48224203c8d8fd62a19b0000 |
| src/hermes/app/chat/composer/url-refs.test.ts | verbatim | apps/desktop/src/app/chat/composer/url-refs.test.ts | 5687718b407c3c4c15ec32d4631a26a0dc911a48d88d4f05db3faece95218858 | 5687718b407c3c4c15ec32d4631a26a0dc911a48d88d4f05db3faece95218858 |
| src/hermes/app/chat/composer/url-refs.ts | verbatim | apps/desktop/src/app/chat/composer/url-refs.ts | 9a1fb69770e8b8d90b614a0f3f9cc3a746266db32bf92e6bc1b645db5f9ec9d1 | 9a1fb69770e8b8d90b614a0f3f9cc3a746266db32bf92e6bc1b645db5f9ec9d1 |
| src/hermes/app/chat/composer/visibility.ts | verbatim | apps/desktop/src/app/chat/composer/visibility.ts | f5da1a184e8b34eaa32b829004174c8d0903ed2b3b1a0b7f094cdcca2700d085 | f5da1a184e8b34eaa32b829004174c8d0903ed2b3b1a0b7f094cdcca2700d085 |
| src/hermes/app/chat/composer/voice-activity.test.tsx | verbatim | apps/desktop/src/app/chat/composer/voice-activity.test.tsx | 8eff0dbb31fa9afa10cfd6dd9b89338322d98a9f9fe73e927d1c534db4c240c8 | 8eff0dbb31fa9afa10cfd6dd9b89338322d98a9f9fe73e927d1c534db4c240c8 |
| src/hermes/app/chat/composer/voice-activity.tsx | verbatim | apps/desktop/src/app/chat/composer/voice-activity.tsx | 8331151d9c64352e12cb274ae92c3a408fd2ff8f570a8b48ac4ca8e9f9ead729 | 8331151d9c64352e12cb274ae92c3a408fd2ff8f570a8b48ac4ca8e9f9ead729 |
| src/hermes/app/chat/composer/voice-engine-rows.tsx | verbatim | apps/desktop/src/app/chat/composer/voice-engine-rows.tsx | b4a95ae0ae27a96bb39c75617e142d5c04882a18ab2a9fe7e8d4c0b8e348b3d4 | b4a95ae0ae27a96bb39c75617e142d5c04882a18ab2a9fe7e8d4c0b8e348b3d4 |
| src/hermes/app/chat/composer/voice-fan.tsx | verbatim | apps/desktop/src/app/chat/composer/voice-fan.tsx | 6d84693c920e2058bebdc53160d8f4f64ec5206c8ab3f14085e6a46bb4cdea9d | 6d84693c920e2058bebdc53160d8f4f64ec5206c8ab3f14085e6a46bb4cdea9d |
| src/hermes/app/chat/composer/voice-menu.tsx | verbatim | apps/desktop/src/app/chat/composer/voice-menu.tsx | 392cff54e8ea488091c1ec952742b1119e8bcafbd6276756682085312853ef45 | 392cff54e8ea488091c1ec952742b1119e8bcafbd6276756682085312853ef45 |
| src/hermes/app/chat/history-window.test.tsx | verbatim | apps/desktop/src/app/chat/history-window.test.tsx | 6594bc479ff3c4e6198d68b6113a4d58da205ebf6b63debcabb25b9297bd77f3 | 6594bc479ff3c4e6198d68b6113a4d58da205ebf6b63debcabb25b9297bd77f3 |
| src/hermes/app/chat/history-window.ts | verbatim | apps/desktop/src/app/chat/history-window.ts | 631bbfe299fdf8656c47fcf4f37356d7f1a7e9bb0da870fd8c384330e62efecd | 631bbfe299fdf8656c47fcf4f37356d7f1a7e9bb0da870fd8c384330e62efecd |
| src/hermes/app/chat/hooks/use-composer-actions.ts | patch | apps/desktop/src/app/chat/hooks/use-composer-actions.ts | 4185899d2566d896b18a5b31fc8a4442c2093c27833d659cab79d7d535e46d88 | e31f81a2cfe3d4ccbef385451cc6e1015c4160079bc332b8684c07db8c6143c7 |
| src/hermes/app/chat/hooks/use-file-drop-zone.ts | verbatim | apps/desktop/src/app/chat/hooks/use-file-drop-zone.ts | d3c1d7e030d8701648c82bfaafb1395387b0a89d4d80e1c3134427f0a4adaf48 | d3c1d7e030d8701648c82bfaafb1395387b0a89d4d80e1c3134427f0a4adaf48 |
| src/hermes/app/chat/index.test.tsx | verbatim | apps/desktop/src/app/chat/index.test.tsx | 394116d36bc0342fa54698eec4f1b34cb352a6b9b57c40a879588034fe37546e | 394116d36bc0342fa54698eec4f1b34cb352a6b9b57c40a879588034fe37546e |
| src/hermes/app/chat/index.tsx | verbatim | apps/desktop/src/app/chat/index.tsx | 9c2217624f94f123b14e1f186ea0b38a03cacc20187c10a907e24e37797029d2 | 9c2217624f94f123b14e1f186ea0b38a03cacc20187c10a907e24e37797029d2 |
| src/hermes/app/chat/intro-visibility.test.ts | verbatim | apps/desktop/src/app/chat/intro-visibility.test.ts | 95c54e10d87b178fb02f810d9fa2f03dbe76b76a0adeef8667fc3ac76bfa60a3 | 95c54e10d87b178fb02f810d9fa2f03dbe76b76a0adeef8667fc3ac76bfa60a3 |
| src/hermes/app/chat/intro-visibility.ts | verbatim | apps/desktop/src/app/chat/intro-visibility.ts | 1f28e3a6cea60aa455ec74d0291f5a42480d82d8c05ed4c7faf08e262d4d4745 | 1f28e3a6cea60aa455ec74d0291f5a42480d82d8c05ed4c7faf08e262d4d4745 |
| src/hermes/app/chat/new-session-drag.test.ts | verbatim | apps/desktop/src/app/chat/new-session-drag.test.ts | e399662cf06b1e71ac76fe0c6a64b480846c43e20968bd9904f07e643748bfd2 | e399662cf06b1e71ac76fe0c6a64b480846c43e20968bd9904f07e643748bfd2 |
| src/hermes/app/chat/new-session-drag.ts | verbatim | apps/desktop/src/app/chat/new-session-drag.ts | ff312195c3da0b402b10bcddb1be20b92375582193b3bb77bdb9403bb0471de8 | ff312195c3da0b402b10bcddb1be20b92375582193b3bb77bdb9403bb0471de8 |
| src/hermes/app/chat/pr-tag.tsx | patch | apps/desktop/src/app/chat/pr-tag.tsx | bec07cebd552f336e150698b336217d82012c823aa5bb0bc6c86b06195835c0b | e51ba2ff27c654637ae045835f12e0e1f2e22f14086e219b2985ca4ec0b96a88 |
| src/hermes/app/chat/profile-tag.tsx | verbatim | apps/desktop/src/app/chat/profile-tag.tsx | d5e34076c9d39eef716ce8362d276097fc7e6f6418cdf64bb376cc90fb492588 | d5e34076c9d39eef716ce8362d276097fc7e6f6418cdf64bb376cc90fb492588 |
| src/hermes/app/chat/resume-exhausted-overlay.test.tsx | verbatim | apps/desktop/src/app/chat/resume-exhausted-overlay.test.tsx | ceec817a435c0ca662a912f8d036ae2d570ee2f9b2437d22484112f7f3a28ef0 | ceec817a435c0ca662a912f8d036ae2d570ee2f9b2437d22484112f7f3a28ef0 |
| src/hermes/app/chat/resume-exhausted-overlay.tsx | verbatim | apps/desktop/src/app/chat/resume-exhausted-overlay.tsx | 0fd9634bf6ffd6d589ac56d12d0e8951320482d7c3022647de630f6db8036126 | 0fd9634bf6ffd6d589ac56d12d0e8951320482d7c3022647de630f6db8036126 |
| src/hermes/app/chat/right-rail/preview-act.ts | shim | apps/desktop/src/app/chat/right-rail/preview-act.ts | 3d32e4e75834d3281b0abaabc8f4a38f26eddae1dfe3c649791f69919d116dde | 71923266a30b4356c0b9d1914a1653c43de2a856ec807d98ba211ae6b3586e16 |
| src/hermes/app/chat/right-rail/preview-active-tab.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-active-tab.ts | a324ce4c8367e36d6b8887050fbba5f899d2f87d07c7103b4e028891bf228b22 | a324ce4c8367e36d6b8887050fbba5f899d2f87d07c7103b4e028891bf228b22 |
| src/hermes/app/chat/right-rail/preview-nav.ts | shim | apps/desktop/src/app/chat/right-rail/preview-nav.ts | 61597f0d71dae284ad4f5d6d5944f9774c6c3cbb39481832d1c2733cd3f38aa7 | 0ff6fdbcce94aab367a3085e2b6bb22964bbc5d8e107697b7d80a54f1bc2e365 |
| src/hermes/app/chat/right-rail/preview-nudge.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-nudge.ts | 0967b8e6bd824695efe2ea44fed251ba6a7c59c18d7bb4cc1e40c110a7fcd0f7 | 0967b8e6bd824695efe2ea44fed251ba6a7c59c18d7bb4cc1e40c110a7fcd0f7 |
| src/hermes/app/chat/right-rail/preview-popout-bridge.test.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-popout-bridge.test.ts | 53c5939c66217b47c8fc84e1b8ff4361cdc41c59dd710eb761d66bf61a357aa7 | 53c5939c66217b47c8fc84e1b8ff4361cdc41c59dd710eb761d66bf61a357aa7 |
| src/hermes/app/chat/right-rail/preview-popout-bridge.ts | shim | apps/desktop/src/app/chat/right-rail/preview-popout-bridge.ts | f0aefecd7cab63bf676ffcfbdca585de1d3e0c1e8e175d38445e7dfc92c949c5 | 21d84b99762f69ed1df50d7053afe173b9c2b0fd0e008f46fd1ee887d7bc18a2 |
| src/hermes/app/chat/right-rail/preview-reader.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-reader.ts | ff214e08ac985be6cab0dc7f06c31e27b2470da2b6fb5ce2544c4d45b7625b1d | ff214e08ac985be6cab0dc7f06c31e27b2470da2b6fb5ce2544c4d45b7625b1d |
| src/hermes/app/chat/right-rail/preview-script-runner.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-script-runner.ts | 8cf397f6f8e4ba5a52edd4c86d93f545a04230b879ffe2cc2a888d3c30fa5d32 | 8cf397f6f8e4ba5a52edd4c86d93f545a04230b879ffe2cc2a888d3c30fa5d32 |
| src/hermes/app/chat/right-rail/preview-typing-abort.ts | verbatim | apps/desktop/src/app/chat/right-rail/preview-typing-abort.ts | 14d77bee38581456aaf597e66895dcc226b2ae40c445a38a903566c5375a90b4 | 14d77bee38581456aaf597e66895dcc226b2ae40c445a38a903566c5375a90b4 |
| src/hermes/app/chat/route-session-state.test.ts | verbatim | apps/desktop/src/app/chat/route-session-state.test.ts | 83038ce8e886ca12a81892720a1f54d9051fa20d923a98dc60df356627019ab9 | 83038ce8e886ca12a81892720a1f54d9051fa20d923a98dc60df356627019ab9 |
| src/hermes/app/chat/route-session-state.ts | verbatim | apps/desktop/src/app/chat/route-session-state.ts | 07de164054ba741c48603a2290ac484a579d55438319ac72a0afed731385e402 | 07de164054ba741c48603a2290ac484a579d55438319ac72a0afed731385e402 |
| src/hermes/app/chat/runtime-backfill.test.tsx | verbatim | apps/desktop/src/app/chat/runtime-backfill.test.tsx | 834569decba6e39d28224cc85f822e8ff860eb8223c3ab630756d4d82d95e2fb | 834569decba6e39d28224cc85f822e8ff860eb8223c3ab630756d4d82d95e2fb |
| src/hermes/app/chat/runtime-repository.test.ts | verbatim | apps/desktop/src/app/chat/runtime-repository.test.ts | 93a2dcc2063f9cc3a822051e984c021e04c3c05a6f9635601c2f6a9828ff6d91 | 93a2dcc2063f9cc3a822051e984c021e04c3c05a6f9635601c2f6a9828ff6d91 |
| src/hermes/app/chat/runtime-repository.ts | verbatim | apps/desktop/src/app/chat/runtime-repository.ts | 6557b3a5f599a3e09c79a78cc29da5ff9a2671dc078c91bfe8594e14127a85eb | 6557b3a5f599a3e09c79a78cc29da5ff9a2671dc078c91bfe8594e14127a85eb |
| src/hermes/app/chat/scroll-to-bottom-button.test.tsx | verbatim | apps/desktop/src/app/chat/scroll-to-bottom-button.test.tsx | 0a8acbcfc56e98d43306a1beecab120bd87905cc32363a5c1f308e4f6327adc0 | 0a8acbcfc56e98d43306a1beecab120bd87905cc32363a5c1f308e4f6327adc0 |
| src/hermes/app/chat/scroll-to-bottom-button.tsx | verbatim | apps/desktop/src/app/chat/scroll-to-bottom-button.tsx | 675edf90d9f3fd4f53d3aff6d2e4e025bbd1c1c1a3b7ab95e188785d5b19320c | 675edf90d9f3fd4f53d3aff6d2e4e025bbd1c1c1a3b7ab95e188785d5b19320c |
| src/hermes/app/chat/session-drag.test.ts | verbatim | apps/desktop/src/app/chat/session-drag.test.ts | 031860c007fb928dfd197c4010ffe4fe8be9e2e975c7f875b7520852e6bf8109 | 031860c007fb928dfd197c4010ffe4fe8be9e2e975c7f875b7520852e6bf8109 |
| src/hermes/app/chat/session-drag.ts | verbatim | apps/desktop/src/app/chat/session-drag.ts | 97cd0dee2117abb3c60e8d9fb80364f10a5998f2b25b91d5ed9eb01342faf153 | 97cd0dee2117abb3c60e8d9fb80364f10a5998f2b25b91d5ed9eb01342faf153 |
| src/hermes/app/chat/session-status-dot.tsx | verbatim | apps/desktop/src/app/chat/session-status-dot.tsx | 3e0c2f0661ea92bf1ac1b798ae0353e86c0ac9af91473a29ff45818729b5d7b8 | 3e0c2f0661ea92bf1ac1b798ae0353e86c0ac9af91473a29ff45818729b5d7b8 |
| src/hermes/app/chat/session-tile-actions.test.ts | verbatim | apps/desktop/src/app/chat/session-tile-actions.test.ts | a6dbbb9ae7052035db7ef99af8d118e118a675ce3a1b9579fec2b67000a0ff91 | a6dbbb9ae7052035db7ef99af8d118e118a675ce3a1b9579fec2b67000a0ff91 |
| src/hermes/app/chat/session-tile-actions.ts | verbatim | apps/desktop/src/app/chat/session-tile-actions.ts | fe1bb0fd26d795809699ad1901b8f50518c64459c40ae6d4d0286f06e83d957f | fe1bb0fd26d795809699ad1901b8f50518c64459c40ae6d4d0286f06e83d957f |
| src/hermes/app/chat/session-view.test.ts | verbatim | apps/desktop/src/app/chat/session-view.test.ts | 7723e9d560ce26fdc06f85bff208e923a3e37bc1999e2050dce40d8e6ee5f441 | 7723e9d560ce26fdc06f85bff208e923a3e37bc1999e2050dce40d8e6ee5f441 |
| src/hermes/app/chat/session-view.tsx | verbatim | apps/desktop/src/app/chat/session-view.tsx | c2c0e3ab515b190fbb61e28687f2dcac6d1ed807ac3750966fa88cd283284f54 | c2c0e3ab515b190fbb61e28687f2dcac6d1ed807ac3750966fa88cd283284f54 |
| src/hermes/app/chat/sidebar/archive-copy.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/archive-copy.test.ts | 209ff3e71b89f74cc8093e04abcbe1765b16adfdd560ff09cbee58efbd718207 | 209ff3e71b89f74cc8093e04abcbe1765b16adfdd560ff09cbee58efbd718207 |
| src/hermes/app/chat/sidebar/chat-sidebar.integration.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/chat-sidebar.integration.test.tsx | 91eeee7b0bd6f17edad6d2b3deebea51474afe53dd5c7abc316209164568203a | 91eeee7b0bd6f17edad6d2b3deebea51474afe53dd5c7abc316209164568203a |
| src/hermes/app/chat/sidebar/chrome-section-add-button.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/chrome-section-add-button.test.tsx | a6a068bf9a4223e5368b2fe3a91f86a27062faa483d037bbb8170a030a80fa76 | a6a068bf9a4223e5368b2fe3a91f86a27062faa483d037bbb8170a030a80fa76 |
| src/hermes/app/chat/sidebar/chrome.tsx | verbatim | apps/desktop/src/app/chat/sidebar/chrome.tsx | 630dad228becd7288fa06773fd44faab759f40e04bf86950dfbdf69bc4f0ea21 | 630dad228becd7288fa06773fd44faab759f40e04bf86950dfbdf69bc4f0ea21 |
| src/hermes/app/chat/sidebar/connection-glyph.tsx | verbatim | apps/desktop/src/app/chat/sidebar/connection-glyph.tsx | 2672befe6fa9db222cce15bc164bbdc6f09b6216572c3e7d944ec87699312866 | 2672befe6fa9db222cce15bc164bbdc6f09b6216572c3e7d944ec87699312866 |
| src/hermes/app/chat/sidebar/connection-switcher.tsx | patch | apps/desktop/src/app/chat/sidebar/connection-switcher.tsx | c5c65813742613676b128c1c652c4df94ad03839b8b3e811fd76bd31d514e571 | 8ac6e84b529531538e27a2e88bf430c233cf16f284244251557485dfefa5aad8 |
| src/hermes/app/chat/sidebar/cron-jobs-section.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/cron-jobs-section.test.tsx | da456b6570558e1e81670a480e651cdb47b7ecee1f1c440cb8a07f5528e98f69 | da456b6570558e1e81670a480e651cdb47b7ecee1f1c440cb8a07f5528e98f69 |
| src/hermes/app/chat/sidebar/cron-jobs-section.tsx | shim | apps/desktop/src/app/chat/sidebar/cron-jobs-section.tsx | 821785056a2a31bbae394cf642c212e1621be9e7c99dabfe9cb624be9923a308 | 28435af43d882326e37a4b7aa9253e48f49b9929339a209db7a26bc46b899a92 |
| src/hermes/app/chat/sidebar/filter-menu.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/filter-menu.test.tsx | b2303e623fad44386f5f356b27a72535bb3ad3bc633d707eeb7d32e66cc5420c | b2303e623fad44386f5f356b27a72535bb3ad3bc633d707eeb7d32e66cc5420c |
| src/hermes/app/chat/sidebar/filter-menu.tsx | verbatim | apps/desktop/src/app/chat/sidebar/filter-menu.tsx | d482e4eaf3d54d1b975e0d5d1537bd51e703f6a0d2ff3dd2f566a8ffb744e39b | d482e4eaf3d54d1b975e0d5d1537bd51e703f6a0d2ff3dd2f566a8ffb744e39b |
| src/hermes/app/chat/sidebar/fleet-gateway-menu-group.tsx | verbatim | apps/desktop/src/app/chat/sidebar/fleet-gateway-menu-group.tsx | b31c9b04798b1e4c3b417b4108228d2323d24700125df8676a323ed63c292a9f | b31c9b04798b1e4c3b417b4108228d2323d24700125df8676a323ed63c292a9f |
| src/hermes/app/chat/sidebar/fleet-rail.ts | patch | apps/desktop/src/app/chat/sidebar/fleet-rail.ts | 699acbfb0e5233b61e3527f00bbec137839cc1e70baaef59542653bdf7cccb7f | cf216f3be32e50acdf6e80cd619dc80c6734ae14fe70ed5d91efe370c6000988 |
| src/hermes/app/chat/sidebar/gateway-group-model.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/gateway-group-model.test.ts | cf2c94976a38acf293ab046eb87067f68a09078103e0d737d83ab4a825380c40 | cf2c94976a38acf293ab046eb87067f68a09078103e0d737d83ab4a825380c40 |
| src/hermes/app/chat/sidebar/gateway-group-model.ts | patch | apps/desktop/src/app/chat/sidebar/gateway-group-model.ts | 78250efbe3f11fb0ebd332cbc527d66b73cc4692e6f107bffcbb46414cc6a5aa | 22e2926d5e240071897ec167d4096d0618941e1db613467681a5abe8eb0ac522 |
| src/hermes/app/chat/sidebar/gateway-group-preferences.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/gateway-group-preferences.test.ts | 9ad3f7d1e5259a113ab0d30d2b0e1db84fda73e2a965eeb95897506b713df7c4 | 9ad3f7d1e5259a113ab0d30d2b0e1db84fda73e2a965eeb95897506b713df7c4 |
| src/hermes/app/chat/sidebar/gateway-group-preferences.ts | verbatim | apps/desktop/src/app/chat/sidebar/gateway-group-preferences.ts | 78d86426b0f0138efad1e7acfc530fd01372b8a53d591639e90c64191282f664 | 78d86426b0f0138efad1e7acfc530fd01372b8a53d591639e90c64191282f664 |
| src/hermes/app/chat/sidebar/gateway-groups-drag.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/gateway-groups-drag.test.tsx | cf9e0d80c00897726c6fe9f21c378980267a006e436c77384213de3bb37f6bbf | cf9e0d80c00897726c6fe9f21c378980267a006e436c77384213de3bb37f6bbf |
| src/hermes/app/chat/sidebar/gateway-groups.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/gateway-groups.test.tsx | 4697637dc46bd828738c96265e0c470f6bb7601e60370d2d820d6da2773c77cd | 4697637dc46bd828738c96265e0c470f6bb7601e60370d2d820d6da2773c77cd |
| src/hermes/app/chat/sidebar/gateway-groups.tsx | patch | apps/desktop/src/app/chat/sidebar/gateway-groups.tsx | 6a6e88e4b8483bf0ff54a17142c9b006097e8addacd6e659b3dcdf278739cd9e | 3e997469ece1f9afdd29b515358c2a6cc328b1a80003637dfa8c2066a369208b |
| src/hermes/app/chat/sidebar/index.tsx | verbatim | apps/desktop/src/app/chat/sidebar/index.tsx | 194742afb6c422679a0ad0507e1f29e66c4188345e47f7ee47f79dfc8f1fe2e6 | 194742afb6c422679a0ad0507e1f29e66c4188345e47f7ee47f79dfc8f1fe2e6 |
| src/hermes/app/chat/sidebar/load-more-row.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/load-more-row.test.tsx | db02bea79445088e17eb26c232b65b29dbf8588412b08834c90d432986c0b603 | db02bea79445088e17eb26c232b65b29dbf8588412b08834c90d432986c0b603 |
| src/hermes/app/chat/sidebar/load-more-row.tsx | verbatim | apps/desktop/src/app/chat/sidebar/load-more-row.tsx | 1a27e5e5f5c3df967b91696b0c117581b819c431dda078ed2220e1284289e546 | 1a27e5e5f5c3df967b91696b0c117581b819c431dda078ed2220e1284289e546 |
| src/hermes/app/chat/sidebar/local-device-switch.tsx | shim | apps/desktop/src/app/chat/sidebar/local-device-switch.tsx | c4233d45b31c377dc5409dd56737634d963e9abfc37a351cd6141e7bb263f704 | 76756ca87ae017404d66d74c43335b0b058bc3a35c5c51e92bc9eef2464fd1d2 |
| src/hermes/app/chat/sidebar/order.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/order.test.ts | 80ff2066da8651d60066edce8f5eefe7102c750e97b17ae6eb7224677e0d1f65 | 80ff2066da8651d60066edce8f5eefe7102c750e97b17ae6eb7224677e0d1f65 |
| src/hermes/app/chat/sidebar/order.ts | verbatim | apps/desktop/src/app/chat/sidebar/order.ts | b98442abb9b57c31d1e2abc765f806392ee785a4c7790e405ddd4ea42f9f0315 | b98442abb9b57c31d1e2abc765f806392ee785a4c7790e405ddd4ea42f9f0315 |
| src/hermes/app/chat/sidebar/profile-dropdown-switcher.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/profile-dropdown-switcher.test.tsx | b818df2433e5f69cbe11a8aec8ede58e425bd87efa11265c67b9bc4003024fed | b818df2433e5f69cbe11a8aec8ede58e425bd87efa11265c67b9bc4003024fed |
| src/hermes/app/chat/sidebar/profile-dropdown-switcher.tsx | verbatim | apps/desktop/src/app/chat/sidebar/profile-dropdown-switcher.tsx | a72a211c1c17ef7486955eb302d3eb0028811d1ab2b19f8660c65cd007dbe597 | a72a211c1c17ef7486955eb302d3eb0028811d1ab2b19f8660c65cd007dbe597 |
| src/hermes/app/chat/sidebar/profile-launch-menu.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/profile-launch-menu.test.tsx | 6f5a91dcdf3e6c8a65ba7e6300bb4f1886ab23bcca7a1423b98e22847c960709 | 6f5a91dcdf3e6c8a65ba7e6300bb4f1886ab23bcca7a1423b98e22847c960709 |
| src/hermes/app/chat/sidebar/profile-launch-menu.tsx | shim | apps/desktop/src/app/chat/sidebar/profile-launch-menu.tsx | 9290f77aaad757aba9c4ea588a21cb2c19748f83e8cb34ceada102b7c45aca9d | 02225b4e6d5668fdf6c1fc7f58c10b922732d5a62f4b036c8776058499e0eda4 |
| src/hermes/app/chat/sidebar/profile-remote-override-dialog.tsx | shim | apps/desktop/src/app/chat/sidebar/profile-remote-override-dialog.tsx | aa0a47fceac974c9a80c0848df7c9ce8f683c2535f714cf72f9cc6d4558d1f09 | ebb7fbe3c863dc4b5aafb7896b023b502a25aff109e0d50464ebcde15be1393a |
| src/hermes/app/chat/sidebar/profile-scope.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/profile-scope.test.ts | 00149373b465de0eae6a739261f4e1a9572fcf6810b6e7a879ed647bfa7705b6 | 00149373b465de0eae6a739261f4e1a9572fcf6810b6e7a879ed647bfa7705b6 |
| src/hermes/app/chat/sidebar/profile-scope.ts | verbatim | apps/desktop/src/app/chat/sidebar/profile-scope.ts | 99893b8026cd11e9d5f1cd577eb13977795f2ac0072074f29e1f4863c932744d | 99893b8026cd11e9d5f1cd577eb13977795f2ac0072074f29e1f4863c932744d |
| src/hermes/app/chat/sidebar/profile-switcher.tsx | shim | apps/desktop/src/app/chat/sidebar/profile-switcher.tsx | a8cdcc28625567aef4e4938da355951e6732c047582b773a56cdcca38fa1ea5d | 1777f35a0ad0c5338308f4cb918d3c1e789c2a9ca2b308039df680dacd617911 |
| src/hermes/app/chat/sidebar/project-dialog.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/project-dialog.test.tsx | 44134c4da4db14076f8b574c2fbe8d762cebe7722c3a5e0f9c42004ecf5bfa08 | 44134c4da4db14076f8b574c2fbe8d762cebe7722c3a5e0f9c42004ecf5bfa08 |
| src/hermes/app/chat/sidebar/project-dialog.tsx | shim | apps/desktop/src/app/chat/sidebar/project-dialog.tsx | 4773aedc4c66ac756abf31f97cd413694cbc246115768b174dfb48e6fc9d1cfb | 51e7dc811bf7d3dbdc9e4bda43440c3a96f177a767ea2e7c757a06aea8f46f24 |
| src/hermes/app/chat/sidebar/project-filter.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/project-filter.test.ts | f7d589c783a4f5060d73b9fa54e4264f3d695d088fca0dedddc456bfcd0ff058 | f7d589c783a4f5060d73b9fa54e4264f3d695d088fca0dedddc456bfcd0ff058 |
| src/hermes/app/chat/sidebar/project-filter.ts | verbatim | apps/desktop/src/app/chat/sidebar/project-filter.ts | f43c6faeec1dbbbb4827fa56674e8f17d9fb016eddf5c5340fde238b50738259 | f43c6faeec1dbbbb4827fa56674e8f17d9fb016eddf5c5340fde238b50738259 |
| src/hermes/app/chat/sidebar/projects/base-branch-picker.tsx | patch | apps/desktop/src/app/chat/sidebar/projects/base-branch-picker.tsx | f0bc95ef0d62eeb2b369020e11ad40f0c22f375a6ef7515b8e362c390b10e381 | 5ef77c2fb4560287db6c3636d40e9c8953ae30ae5a9db3a56e7cc2a16a7a93ef |
| src/hermes/app/chat/sidebar/projects/entered-content.tsx | patch | apps/desktop/src/app/chat/sidebar/projects/entered-content.tsx | 37658f5ffedd253d27bf4f1973072bddd3ef4d34e101bbf0e39ea81c01366ab5 | a71bb649e8da6081c1a425147eb590507fbcf2036a206ed11e4c4d92fa5259b2 |
| src/hermes/app/chat/sidebar/projects/index.ts | verbatim | apps/desktop/src/app/chat/sidebar/projects/index.ts | 9c7522292c28bb1f12ac91fdd39c1f0859879eb6ed80012f62822a65bb6c6d56 | 9c7522292c28bb1f12ac91fdd39c1f0859879eb6ed80012f62822a65bb6c6d56 |
| src/hermes/app/chat/sidebar/projects/model.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/projects/model.test.ts | 13ae35620fd13421e9c7e0ae44ef2f216157d1a1f1326b61cfb832e361e652f8 | 13ae35620fd13421e9c7e0ae44ef2f216157d1a1f1326b61cfb832e361e652f8 |
| src/hermes/app/chat/sidebar/projects/model.ts | patch | apps/desktop/src/app/chat/sidebar/projects/model.ts | 7e6fda834064beae0322da1e265515ed790984be59b5fb08cfe722ffb1d354c5 | 4d2e81d03028da482b37573a520ef45cce46cef689e47f49769fb35bf9333d9b |
| src/hermes/app/chat/sidebar/projects/overview-row.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/overview-row.test.tsx | 10552d0bdde2fcd1a1108a4b0f358167924d813355adae1f42b1102885ee00ce | 10552d0bdde2fcd1a1108a4b0f358167924d813355adae1f42b1102885ee00ce |
| src/hermes/app/chat/sidebar/projects/overview-row.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/overview-row.tsx | e027732be900eaf81b4fd99c8271b9c03bea5d1d1004ad452922a80b0c586ad6 | e027732be900eaf81b4fd99c8271b9c03bea5d1d1004ad452922a80b0c586ad6 |
| src/hermes/app/chat/sidebar/projects/project-appearance.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/project-appearance.tsx | c86951511b0b65e2ae5b09aa2d533c5ee4bd3487053abc223d188d11f9339189 | c86951511b0b65e2ae5b09aa2d533c5ee4bd3487053abc223d188d11f9339189 |
| src/hermes/app/chat/sidebar/projects/project-menu.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/project-menu.test.tsx | 39ef3cbbfaa944fdc9dba6808ead3c32010be636e354a225633105d498df2d49 | 39ef3cbbfaa944fdc9dba6808ead3c32010be636e354a225633105d498df2d49 |
| src/hermes/app/chat/sidebar/projects/project-menu.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/project-menu.tsx | f41adefe64efb6e2146a26944dd0dbebe787327d0a3ed7b07e9ac9fb5ee81549 | f41adefe64efb6e2146a26944dd0dbebe787327d0a3ed7b07e9ac9fb5ee81549 |
| src/hermes/app/chat/sidebar/projects/workspace-group.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/workspace-group.tsx | 6b601c59fe9d1004aa8de9d259ef4703f5e428829782afa15a97a53cef2b5205 | 6b601c59fe9d1004aa8de9d259ef4703f5e428829782afa15a97a53cef2b5205 |
| src/hermes/app/chat/sidebar/projects/workspace-header.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/workspace-header.test.tsx | 8fcd65470288e89268aeee1a0c3c04373c016dfbfb6d4106e56a043d2c718189 | 8fcd65470288e89268aeee1a0c3c04373c016dfbfb6d4106e56a043d2c718189 |
| src/hermes/app/chat/sidebar/projects/workspace-header.tsx | verbatim | apps/desktop/src/app/chat/sidebar/projects/workspace-header.tsx | b2e1880b2e9c39ff1d93c8e258e71566b31ac155f77b363d72a1939a1d537d16 | b2e1880b2e9c39ff1d93c8e258e71566b31ac155f77b363d72a1939a1d537d16 |
| src/hermes/app/chat/sidebar/projects/worktree-dialog.tsx | shim | apps/desktop/src/app/chat/sidebar/projects/worktree-dialog.tsx | 4fc8bc8b39a7b9f7089fe2b782ab9f7afccb8c80e1b934c9c03f18d047c9a90a | 40875c79f3172083d9ac8a25e164a0fda276a13f479c8a2d840be1f956312ae9 |
| src/hermes/app/chat/sidebar/reorderable-list.tsx | verbatim | apps/desktop/src/app/chat/sidebar/reorderable-list.tsx | 541be8d0d808ad487b9276d2c5338a22014482b9b0530ff466eacef6956d1f47 | 541be8d0d808ad487b9276d2c5338a22014482b9b0530ff466eacef6956d1f47 |
| src/hermes/app/chat/sidebar/row-geometry.ts | verbatim | apps/desktop/src/app/chat/sidebar/row-geometry.ts | 75e50f53e54098fb17d704b567dad17aaba76bfd9b7abe8db766d0477f2f3d7a | 75e50f53e54098fb17d704b567dad17aaba76bfd9b7abe8db766d0477f2f3d7a |
| src/hermes/app/chat/sidebar/section-states.tsx | verbatim | apps/desktop/src/app/chat/sidebar/section-states.tsx | e5ce744b86c077dea7f94fcf2317e066b4a9798ff2bccd4dbef92d05c0c8a1f9 | e5ce744b86c077dea7f94fcf2317e066b4a9798ff2bccd4dbef92d05c0c8a1f9 |
| src/hermes/app/chat/sidebar/session-actions-menu.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-actions-menu.test.ts | fd183cbebf385f57e9a2ed33c3cc6830d569a9a3da2cca4a305eaa32407bd1f0 | fd183cbebf385f57e9a2ed33c3cc6830d569a9a3da2cca4a305eaa32407bd1f0 |
| src/hermes/app/chat/sidebar/session-actions-menu.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-actions-menu.test.tsx | e9860e5f4285973494c3f691c7febfaeaa1d395b8ce0fce42b04d74d89eb0a26 | e9860e5f4285973494c3f691c7febfaeaa1d395b8ce0fce42b04d74d89eb0a26 |
| src/hermes/app/chat/sidebar/session-actions-menu.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-actions-menu.tsx | fe7e15de900ae2e9dc1e042e21bc80180a3e434759dc078cac1518008063f57e | fe7e15de900ae2e9dc1e042e21bc80180a3e434759dc078cac1518008063f57e |
| src/hermes/app/chat/sidebar/session-index.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-index.test.ts | ecd54c91011548f7933aabafe1415c6dd4ce557a4c7e7febe98cdf50e742c8a6 | ecd54c91011548f7933aabafe1415c6dd4ce557a4c7e7febe98cdf50e742c8a6 |
| src/hermes/app/chat/sidebar/session-index.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-index.ts | 7e5ef2aba9ca6a32cc0867cd61c01df0553402ebb242ddd6016677aa308af69c | 7e5ef2aba9ca6a32cc0867cd61c01df0553402ebb242ddd6016677aa308af69c |
| src/hermes/app/chat/sidebar/session-row-actions.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-row-actions.test.tsx | 52215b6ee2b233296dc43468f73f59880519569fbf1297d8b381b79d24c80ce7 | 52215b6ee2b233296dc43468f73f59880519569fbf1297d8b381b79d24c80ce7 |
| src/hermes/app/chat/sidebar/session-row-details.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-row-details.test.ts | fcb1929d89ae93fcc23e16a715a236177dd6821203c3fff63716f66b75c366e4 | fcb1929d89ae93fcc23e16a715a236177dd6821203c3fff63716f66b75c366e4 |
| src/hermes/app/chat/sidebar/session-row-details.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-row-details.ts | 95f46035bc0b094be9b11af16dbba8a1b0bac0e091cdacf321b69554b8ce7a50 | 95f46035bc0b094be9b11af16dbba8a1b0bac0e091cdacf321b69554b8ce7a50 |
| src/hermes/app/chat/sidebar/session-row-gesture.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-row-gesture.test.ts | c4639294957337fc4ec2e82c9c274833dc1fdc2ba70567745983b85ce728b249 | c4639294957337fc4ec2e82c9c274833dc1fdc2ba70567745983b85ce728b249 |
| src/hermes/app/chat/sidebar/session-row-gesture.ts | verbatim | apps/desktop/src/app/chat/sidebar/session-row-gesture.ts | c8346f54da38c9dbd992de7713f6e1f9b0c6bc1072d234ddab47c44e678cd176 | c8346f54da38c9dbd992de7713f6e1f9b0c6bc1072d234ddab47c44e678cd176 |
| src/hermes/app/chat/sidebar/session-row-rename-drag.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-row-rename-drag.test.tsx | 2a8f2b3c45c5c691b7d23156c258c30dc9678c1e6132bb8ea398ef36f4b40769 | 2a8f2b3c45c5c691b7d23156c258c30dc9678c1e6132bb8ea398ef36f4b40769 |
| src/hermes/app/chat/sidebar/session-row-slots.tsx | patch | apps/desktop/src/app/chat/sidebar/session-row-slots.tsx | a039622bc2a329cdf1b346a385dd66f9ad5fbc1f55424e8b24597b0674ac1dc3 | 71ddbca01d49cc73c83dffe29a03d748a91e28b99dbf9dcbafde617106848bff |
| src/hermes/app/chat/sidebar/session-row.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-row.test.tsx | 1a337e41ca94eefb587657b6529175eda29aae3567a2bd0e0aeb45fe1ff6b3ba | 1a337e41ca94eefb587657b6529175eda29aae3567a2bd0e0aeb45fe1ff6b3ba |
| src/hermes/app/chat/sidebar/session-row.tsx | verbatim | apps/desktop/src/app/chat/sidebar/session-row.tsx | 54c81e04b31efcea908cf42c53b20956531ed3655a4b290a657dde7776ecf80f | 54c81e04b31efcea908cf42c53b20956531ed3655a4b290a657dde7776ecf80f |
| src/hermes/app/chat/sidebar/sessions-section-new-session-drag.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/sessions-section-new-session-drag.test.tsx | 66a170e5b1c1c656c1b642f72102c469712bf2717e07dc4de101fb3919d4942a | 66a170e5b1c1c656c1b642f72102c469712bf2717e07dc4de101fb3919d4942a |
| src/hermes/app/chat/sidebar/sessions-section.tsx | patch | apps/desktop/src/app/chat/sidebar/sessions-section.tsx | 4309fe015a42cddfcbcd39fc5fedd298b3296925469739183eda6402833224f8 | 0ea0eeabfd140365b399ef8febfea414e45afc0bfd887f8b3b342807476f7e66 |
| src/hermes/app/chat/sidebar/split-submenu.tsx | verbatim | apps/desktop/src/app/chat/sidebar/split-submenu.tsx | d534f32db6808a81ece114210c9fee73b46f1819b8c5292d8fee3445677e3402 | d534f32db6808a81ece114210c9fee73b46f1819b8c5292d8fee3445677e3402 |
| src/hermes/app/chat/sidebar/use-entered-project-sessions.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-entered-project-sessions.test.ts | 37035616480c63c53ecdc9db1c66957c07c45d5ec158182ae7cfac68cc646c95 | 37035616480c63c53ecdc9db1c66957c07c45d5ec158182ae7cfac68cc646c95 |
| src/hermes/app/chat/sidebar/use-entered-project-sessions.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-entered-project-sessions.ts | d42df76741e47247c32cc9fbb3e74f2f5001f3246b942b578a42beb9c86a5ffb | d42df76741e47247c32cc9fbb3e74f2f5001f3246b942b578a42beb9c86a5ffb |
| src/hermes/app/chat/sidebar/use-fleet-roster.ts | patch | apps/desktop/src/app/chat/sidebar/use-fleet-roster.ts | c3d03d8e57b626c829f26cdee76ed07d2719231e0646624b479303f0b8b2eebc | e9aada866a81d0c63cd87312795b900c264f0530fb4afeaf9c4d6e0398a59e69 |
| src/hermes/app/chat/sidebar/use-profile-prewarm.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-profile-prewarm.test.ts | f5db87b2bf3dc91ce5126d6bdb3e1fb939319873a1cfa7492296a6170e316b3f | f5db87b2bf3dc91ce5126d6bdb3e1fb939319873a1cfa7492296a6170e316b3f |
| src/hermes/app/chat/sidebar/use-profile-prewarm.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-profile-prewarm.ts | bde5d01af5ec3b6c8703438d5d029f3cd80386c858df1da80e30064a3c714f6d | bde5d01af5ec3b6c8703438d5d029f3cd80386c858df1da80e30064a3c714f6d |
| src/hermes/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts | e5dda68e04be372ad6832e3689630e8079ed1bf9ac8a1c972d2d72aedf84e64a | e5dda68e04be372ad6832e3689630e8079ed1bf9ac8a1c972d2d72aedf84e64a |
| src/hermes/app/chat/sidebar/use-profile-rail-refresh-on-active.ts | verbatim | apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts | 61a1b82cf6cabe5b74bec113c060bb71aff40ca6e58e95abffec8e9d8cc730cb | 61a1b82cf6cabe5b74bec113c060bb71aff40ca6e58e95abffec8e9d8cc730cb |
| src/hermes/app/chat/sidebar/use-server-session-search.ts | patch | apps/desktop/src/app/chat/sidebar/use-server-session-search.ts | ac1e4e3dad631cff4fef05ce5c6d3c948602b34a967b8c41f365aacddefce4d5 | 83d9c037ff9268f1037c3ed07badf0c684376167ac7384430c7d08e390d82e71 |
| src/hermes/app/chat/sidebar/virtual-session-list.test.tsx | verbatim | apps/desktop/src/app/chat/sidebar/virtual-session-list.test.tsx | e99c93b797abda2b93dee55493a6b4f68c17b70bdde7e3018a8b70fd4e2c746a | e99c93b797abda2b93dee55493a6b4f68c17b70bdde7e3018a8b70fd4e2c746a |
| src/hermes/app/chat/sidebar/virtual-session-list.tsx | verbatim | apps/desktop/src/app/chat/sidebar/virtual-session-list.tsx | e829e8f4782fdda23787e491ac0969392cd1168e3279a7b8d1db6ebd1698fab4 | e829e8f4782fdda23787e491ac0969392cd1168e3279a7b8d1db6ebd1698fab4 |
| src/hermes/app/chat/surface-vars.test.ts | verbatim | apps/desktop/src/app/chat/surface-vars.test.ts | e003858a69843ef9dc4038b3b54c23d5139b199962e3451971f1738659c05393 | e003858a69843ef9dc4038b3b54c23d5139b199962e3451971f1738659c05393 |
| src/hermes/app/chat/surface-vars.ts | verbatim | apps/desktop/src/app/chat/surface-vars.ts | c80d352d30b254489c500e122d60e9099d89c0f79a6edf0c8fa4936fa4f28a88 | c80d352d30b254489c500e122d60e9099d89c0f79a6edf0c8fa4936fa4f28a88 |
| src/hermes/app/chat/tile-zone-host.ts | verbatim | apps/desktop/src/app/chat/tile-zone-host.ts | be56f72fa71015e216d6c5d52d70836ca60c74db093d3f47b2c2ed8493e8c0d9 | be56f72fa71015e216d6c5d52d70836ca60c74db093d3f47b2c2ed8493e8c0d9 |
| src/hermes/app/chat/tour-marker.ts | verbatim | apps/desktop/src/app/chat/tour-marker.ts | cf20d8af0ab4718697ad340eff1d6d8ea8bd64ad448bfb288e2c3dafa25c070f | cf20d8af0ab4718697ad340eff1d6d8ea8bd64ad448bfb288e2c3dafa25c070f |
| src/hermes/app/chat/transcript-backfill.test.ts | verbatim | apps/desktop/src/app/chat/transcript-backfill.test.ts | 3aa8df9af3d68ae7bf74cfe1b9e0ab5ce7e06bfe83e8806df3bdbb407baba0ec | 3aa8df9af3d68ae7bf74cfe1b9e0ab5ce7e06bfe83e8806df3bdbb407baba0ec |
| src/hermes/app/chat/transcript-backfill.tool-fold-graft.test.ts | verbatim | apps/desktop/src/app/chat/transcript-backfill.tool-fold-graft.test.ts | e2e390bfd92f979500f513d1b31857f32b0e9536a045442403d2c31746b52073 | e2e390bfd92f979500f513d1b31857f32b0e9536a045442403d2c31746b52073 |
| src/hermes/app/chat/transcript-backfill.ts | verbatim | apps/desktop/src/app/chat/transcript-backfill.ts | cde052a0b0466969bffa1b38aad3e39a916fb362dfcc374c2ed6f1b52ba9d0b7 | cde052a0b0466969bffa1b38aad3e39a916fb362dfcc374c2ed6f1b52ba9d0b7 |
| src/hermes/app/chat/transcript-fold-identity.test.ts | verbatim | apps/desktop/src/app/chat/transcript-fold-identity.test.ts | 833e60797557cbab1cf225163642e2a6e21646f2b3a9d4b16c1c32da63165762 | 833e60797557cbab1cf225163642e2a6e21646f2b3a9d4b16c1c32da63165762 |
| src/hermes/app/chat/transcript-retention.test.ts | verbatim | apps/desktop/src/app/chat/transcript-retention.test.ts | 9528ef9fc30be82d7eac27577986f339eaef64202d613bcf1743aab6870d3643 | 9528ef9fc30be82d7eac27577986f339eaef64202d613bcf1743aab6870d3643 |
| src/hermes/app/chat/transcript-retention.ts | verbatim | apps/desktop/src/app/chat/transcript-retention.ts | 5bd7e9617ba1f68b14c9f667185db3865797e3bd1cc269d4ca66269096ca50b4 | 5bd7e9617ba1f68b14c9f667185db3865797e3bd1cc269d4ca66269096ca50b4 |
| src/hermes/app/chat/transcript-window-retention.test.ts | verbatim | apps/desktop/src/app/chat/transcript-window-retention.test.ts | 94c935faff9b9dd220d3f9ccdf34b66c3ea1c06c5132d3679b34880fe19054cb | 94c935faff9b9dd220d3f9ccdf34b66c3ea1c06c5132d3679b34880fe19054cb |
| src/hermes/app/chat/transcript-window.test.ts | verbatim | apps/desktop/src/app/chat/transcript-window.test.ts | 2160d4d6e6afb5d0d4b8f7072051e7ff3454824b2be32e6f685376a4782ac150 | 2160d4d6e6afb5d0d4b8f7072051e7ff3454824b2be32e6f685376a4782ac150 |
| src/hermes/app/chat/transcript-window.ts | verbatim | apps/desktop/src/app/chat/transcript-window.ts | 77e3b02dc4d0ad1f4679dfde45acbfc807f9ae37161019d1bba74c5032aa28f5 | 77e3b02dc4d0ad1f4679dfde45acbfc807f9ae37161019d1bba74c5032aa28f5 |
| src/hermes/app/chat/use-chat-bar-state.ts | patch | apps/desktop/src/app/chat/use-chat-bar-state.ts | 61aa1af796f79f1f75cd7b7573b1e1510fc45474fdd86df547088e899c81fd92 | 6823b9ab77e713a750e0f52310f2f57ca7685a8d64c66faaaaa1fb693cf3cab2 |
| src/hermes/app/chat/use-transcript-retention.test.tsx | verbatim | apps/desktop/src/app/chat/use-transcript-retention.test.tsx | adc9cc5d836031bef54bf4468eac8fdfa1eb0c00976758b040e8f1f77365fe36 | adc9cc5d836031bef54bf4468eac8fdfa1eb0c00976758b040e8f1f77365fe36 |
| src/hermes/app/chat/use-transcript-retention.ts | verbatim | apps/desktop/src/app/chat/use-transcript-retention.ts | 65008d4fbd7a7ae3eaa704a16b095129dbfdbdad4b4019bdb6a9cd58e1de61b7 | 65008d4fbd7a7ae3eaa704a16b095129dbfdbdad4b4019bdb6a9cd58e1de61b7 |
| src/hermes/app/command-center/index.tsx | verbatim | apps/desktop/src/app/command-center/index.tsx | 8d13a5c1a58e2427da0b3d40e32ac44552756b3f11cd4f7530e31faf47e16d2a | 8d13a5c1a58e2427da0b3d40e32ac44552756b3f11cd4f7530e31faf47e16d2a |
| src/hermes/app/command-center/maintenance.test.tsx | verbatim | apps/desktop/src/app/command-center/maintenance.test.tsx | 6d8a2e6361db92b2e2ee6c69cf6911dc99f9702109b01a5b8a875f4ca36796b9 | 6d8a2e6361db92b2e2ee6c69cf6911dc99f9702109b01a5b8a875f4ca36796b9 |
| src/hermes/app/command-center/maintenance.tsx | patch | apps/desktop/src/app/command-center/maintenance.tsx | 5df752a4ee16948f2284e140509bd2808c67cb03fa16523a1afcc631d1f7d4ba | 707459b5f2183a8083d31089f0a2ca9821f0b4f68638db19d75eb85c42c1a996 |
| src/hermes/app/contrib/hooks/use-background-sync.test.ts | verbatim | apps/desktop/src/app/contrib/hooks/use-background-sync.test.ts | 1a919f19b17f6fcbcf3905a6601859df3373b302e4264ec772b6fa49a7af661a | 1a919f19b17f6fcbcf3905a6601859df3373b302e4264ec772b6fa49a7af661a |
| src/hermes/app/contrib/hooks/use-background-sync.test.tsx | verbatim | apps/desktop/src/app/contrib/hooks/use-background-sync.test.tsx | c0588c4a4af94027abc3183ad546fe1193a439d1aced48e9a2d7759db35aac63 | c0588c4a4af94027abc3183ad546fe1193a439d1aced48e9a2d7759db35aac63 |
| src/hermes/app/contrib/hooks/use-background-sync.ts | verbatim | apps/desktop/src/app/contrib/hooks/use-background-sync.ts | fe93a1739d13255a59223ede1f5be5ba4db38331107a918f58657d1a5a219239 | fe93a1739d13255a59223ede1f5be5ba4db38331107a918f58657d1a5a219239 |
| src/hermes/app/contrib/session-rpc-dispatcher.test.ts | verbatim | apps/desktop/src/app/contrib/session-rpc-dispatcher.test.ts | ca75ad0818dac840a9c386859fca6d8086006678834c29b757d7e830f09cfd49 | ca75ad0818dac840a9c386859fca6d8086006678834c29b757d7e830f09cfd49 |
| src/hermes/app/contrib/session-rpc-dispatcher.ts | verbatim | apps/desktop/src/app/contrib/session-rpc-dispatcher.ts | 8b8d628818b3f4df199727b7bab76876250c77dd19438028e79c05e55282a082 | 8b8d628818b3f4df199727b7bab76876250c77dd19438028e79c05e55282a082 |
| src/hermes/app/contrib/types.ts | verbatim | apps/desktop/src/app/contrib/types.ts | e3749e26721470b50ab15830cd2d0d70b26d11bfe61251f167d81ba52208db02 | e3749e26721470b50ab15830cd2d0d70b26d11bfe61251f167d81ba52208db02 |
| src/hermes/app/cron/cron-actions.test.ts | verbatim | apps/desktop/src/app/cron/cron-actions.test.ts | b267f65619920ab894524758b1b357239bc141b794de3f32f80db86381a2a37a | b267f65619920ab894524758b1b357239bc141b794de3f32f80db86381a2a37a |
| src/hermes/app/cron/cron-actions.ts | verbatim | apps/desktop/src/app/cron/cron-actions.ts | bc4d07671bbd175bcf6671bfaeaa7d2cec2a7776b72b86ae105604a3321e06c8 | bc4d07671bbd175bcf6671bfaeaa7d2cec2a7776b72b86ae105604a3321e06c8 |
| src/hermes/app/cron/open-cron-run.test.ts | verbatim | apps/desktop/src/app/cron/open-cron-run.test.ts | 41fbe28a4695adab73f8f526cde7df0a0e4466d158a81d22e153076c8889543e | 41fbe28a4695adab73f8f526cde7df0a0e4466d158a81d22e153076c8889543e |
| src/hermes/app/cron/open-cron-run.ts | verbatim | apps/desktop/src/app/cron/open-cron-run.ts | b35bcd6486bc5e16d35d2626cd4d2402c0930fdf0a1286512d88269c70fff277 | b35bcd6486bc5e16d35d2626cd4d2402c0930fdf0a1286512d88269c70fff277 |
| src/hermes/app/gateway/hooks/use-gateway-request.test.ts | verbatim | apps/desktop/src/app/gateway/hooks/use-gateway-request.test.ts | bccaa4dc9250c5527938ff0f7e8397517b04f77a93f6d9785671ee65152d2673 | bccaa4dc9250c5527938ff0f7e8397517b04f77a93f6d9785671ee65152d2673 |
| src/hermes/app/gateway/hooks/use-gateway-request.ts | patch | apps/desktop/src/app/gateway/hooks/use-gateway-request.ts | 418a141d2fac7af7bda190f7817620c7f8284b1057eb10b98e6ccf88051e18bc | a214fdb55b15334547c5a93fa4241c4959479769a2881685b5ca30c6f4119d46 |
| src/hermes/app/hooks/use-refresh-hotkey.ts | verbatim | apps/desktop/src/app/hooks/use-refresh-hotkey.ts | 35ba33cab3fee8b7e78fa3817f35868a5afcf08a10daa3e89f5cd6bd78951827 | 35ba33cab3fee8b7e78fa3817f35868a5afcf08a10daa3e89f5cd6bd78951827 |
| src/hermes/app/hooks/use-route-enum-param.ts | verbatim | apps/desktop/src/app/hooks/use-route-enum-param.ts | 8120d06cc774f3792212b28291912b8786269fe80bfcba1044e9d8cf15b01172 | 8120d06cc774f3792212b28291912b8786269fe80bfcba1044e9d8cf15b01172 |
| src/hermes/app/hud/composer-drag.test.ts | verbatim | apps/desktop/src/app/hud/composer-drag.test.ts | caf6d07e9c71c5f9cc43ca13c180cb6456f01fe33ed2adfda25a983a3ae1e052 | caf6d07e9c71c5f9cc43ca13c180cb6456f01fe33ed2adfda25a983a3ae1e052 |
| src/hermes/app/hud/composer-drag.ts | shim | apps/desktop/src/app/hud/composer-drag.ts | cbb5506e28ee4dafae32b29a5ec651f0a4d627cb43f1ce2a0877b8930a787712 | 656775abe686e8893eb461511ec0c4159c31dfb3ad6fc2af66037248af1bc80b |
| src/hermes/app/layout-constants.ts | verbatim | apps/desktop/src/app/layout-constants.ts | 5feb17140088ae55804d79ac5db27bfb6317a4f0fe9a5b431b11b318f44e0e95 | 5feb17140088ae55804d79ac5db27bfb6317a4f0fe9a5b431b11b318f44e0e95 |
| src/hermes/app/messaging/platform-icon.tsx | verbatim | apps/desktop/src/app/messaging/platform-icon.tsx | 4dc73ea1e43b7765a5e517598538e8f43d175534a4b1d4d11d0bcf812cba0897 | 4dc73ea1e43b7765a5e517598538e8f43d175534a4b1d4d11d0bcf812cba0897 |
| src/hermes/app/open-session.test.ts | verbatim | apps/desktop/src/app/open-session.test.ts | 4a023b30167accc05cbe1f89cc49bbde15dcacacbc91693300dfc91e4b9149c2 | 4a023b30167accc05cbe1f89cc49bbde15dcacacbc91693300dfc91e4b9149c2 |
| src/hermes/app/overlays/overlay-breadcrumb-header.tsx | verbatim | apps/desktop/src/app/overlays/overlay-breadcrumb-header.tsx | 719308e6a8bd32c61dac31cbd8c8b4540403d2394dc7b40c3549ca2a2bf19c8d | 719308e6a8bd32c61dac31cbd8c8b4540403d2394dc7b40c3549ca2a2bf19c8d |
| src/hermes/app/overlays/overlay-split-layout.test.tsx | verbatim | apps/desktop/src/app/overlays/overlay-split-layout.test.tsx | 8062eab84d1504cf8675b27ae025e7de069e8c3d5c6bbfd1f2f146e9c699f417 | 8062eab84d1504cf8675b27ae025e7de069e8c3d5c6bbfd1f2f146e9c699f417 |
| src/hermes/app/overlays/overlay-split-layout.tsx | verbatim | apps/desktop/src/app/overlays/overlay-split-layout.tsx | 5d8758c5ba373da845d298e157c81df115f6df71891af968b81f869afe0c4c8a | 5d8758c5ba373da845d298e157c81df115f6df71891af968b81f869afe0c4c8a |
| src/hermes/app/overlays/overlay-view.tsx | verbatim | apps/desktop/src/app/overlays/overlay-view.tsx | 8c00d982af8f3ca4d99245695d08d3d9020eea6298072cd925886fb997444afb | 8c00d982af8f3ca4d99245695d08d3d9020eea6298072cd925886fb997444afb |
| src/hermes/app/overlays/panel.test.tsx | verbatim | apps/desktop/src/app/overlays/panel.test.tsx | a8fcc5d9bc0a9446ff9da23858ea092ed5485509a1455289bab024fa80ba585c | a8fcc5d9bc0a9446ff9da23858ea092ed5485509a1455289bab024fa80ba585c |
| src/hermes/app/overlays/panel.tsx | verbatim | apps/desktop/src/app/overlays/panel.tsx | 4d9bb2479274a0f925168152d2413b4b581e18c1b00f3b9d9c14a260f0a61184 | 4d9bb2479274a0f925168152d2413b4b581e18c1b00f3b9d9c14a260f0a61184 |
| src/hermes/app/profiles/create-profile-dialog.test.tsx | verbatim | apps/desktop/src/app/profiles/create-profile-dialog.test.tsx | 6aff534a64ba5008ff1c4463bbfd3b3be5eed0e8a5d8d2cfbd10543af58c3278 | 6aff534a64ba5008ff1c4463bbfd3b3be5eed0e8a5d8d2cfbd10543af58c3278 |
| src/hermes/app/profiles/create-profile-dialog.tsx | verbatim | apps/desktop/src/app/profiles/create-profile-dialog.tsx | 028aa8043e231d072aac3a73ab8bc2a4aa244ea74739d93502a3fac067d06134 | 028aa8043e231d072aac3a73ab8bc2a4aa244ea74739d93502a3fac067d06134 |
| src/hermes/app/right-sidebar/store.ts | verbatim | apps/desktop/src/app/right-sidebar/store.ts | 7f56b017cba0fc6c8d22bbd6466b76e2a8ba85f70dd3c2900222c3f8ca2fc014 | 7f56b017cba0fc6c8d22bbd6466b76e2a8ba85f70dd3c2900222c3f8ca2fc014 |
| src/hermes/app/right-sidebar/terminal/agent-terminal-stream.bound.test.ts | verbatim | apps/desktop/src/app/right-sidebar/terminal/agent-terminal-stream.bound.test.ts | 2fafd67489eca6350cbef213caa9c57690dd3f68c3fd789479d0db9269b2c5b8 | 2fafd67489eca6350cbef213caa9c57690dd3f68c3fd789479d0db9269b2c5b8 |
| src/hermes/app/right-sidebar/terminal/agent-terminal-stream.ts | shim | apps/desktop/src/app/right-sidebar/terminal/agent-terminal-stream.ts | 5907f9931a0c5f35179315bd2752b6d4ee611208c7f84588a9dc128471c8542e | 6226d55e8648e71444758885c3b54d54d526bd41cbacc7470f3ac5e9542a27ed |
| src/hermes/app/right-sidebar/terminal/disabled-actions.ts | new | — | — | 2c86f7ce17f82a3265beb4e684e4e05d53a5686fd5fc26e4e298f43e59fe9485 |
| src/hermes/app/right-sidebar/terminal/terminal-font.test.ts | verbatim | apps/desktop/src/app/right-sidebar/terminal/terminal-font.test.ts | fa0f0dd34705f81210810cfc5e9adee8076f09345b6aea1a24f89b829c2f5796 | fa0f0dd34705f81210810cfc5e9adee8076f09345b6aea1a24f89b829c2f5796 |
| src/hermes/app/right-sidebar/terminal/terminal-font.ts | verbatim | apps/desktop/src/app/right-sidebar/terminal/terminal-font.ts | 192bfb1176a06e0fd97c4bcd6382f305d680c2e9a43e3041df099e3089d86cfc | 192bfb1176a06e0fd97c4bcd6382f305d680c2e9a43e3041df099e3089d86cfc |
| src/hermes/app/routes.test.ts | verbatim | apps/desktop/src/app/routes.test.ts | 99e5f3d17539e2c3c057e6cfa9d73878e48ca77fe5ea18b4ceda5e7c68424240 | 99e5f3d17539e2c3c057e6cfa9d73878e48ca77fe5ea18b4ceda5e7c68424240 |
| src/hermes/app/routes.titlebar-clusters.test.ts | verbatim | apps/desktop/src/app/routes.titlebar-clusters.test.ts | 18239f559d41cdcdc3b3aed474aa066c5e62c8da242f33379664ccf274c9bfc1 | 18239f559d41cdcdc3b3aed474aa066c5e62c8da242f33379664ccf274c9bfc1 |
| src/hermes/app/session/hooks/model-pick-ledger.ts | verbatim | apps/desktop/src/app/session/hooks/model-pick-ledger.ts | 4157b05c9a9ce804cafd324ae48258955df7c687aac01b094915ab67b2117886 | 4157b05c9a9ce804cafd324ae48258955df7c687aac01b094915ab67b2117886 |
| src/hermes/app/session/hooks/profile-rail-fresh-chat-owner.test.tsx | verbatim | apps/desktop/src/app/session/hooks/profile-rail-fresh-chat-owner.test.tsx | 22a543c055a8c95de4abe5df8fcd02b05c4b082ae407dde1bd6bae8242a18775 | 22a543c055a8c95de4abe5df8fcd02b05c4b082ae407dde1bd6bae8242a18775 |
| src/hermes/app/session/hooks/session-context-drift.test.ts | verbatim | apps/desktop/src/app/session/hooks/session-context-drift.test.ts | bf7281f63b76c1792892ab1d545b466fecaba7941ca6444c6833b6a6ebdd0efa | bf7281f63b76c1792892ab1d545b466fecaba7941ca6444c6833b6a6ebdd0efa |
| src/hermes/app/session/hooks/session-context-drift.ts | verbatim | apps/desktop/src/app/session/hooks/session-context-drift.ts | ed1c8741800e8faf8ac1c8850d8541d491ee72bbd236697ebe9766a0328829fc | ed1c8741800e8faf8ac1c8850d8541d491ee72bbd236697ebe9766a0328829fc |
| src/hermes/app/session/hooks/use-background-queue-drain.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-background-queue-drain.test.tsx | 4aa450aed15577210294dac4a6177610a65cd63df62f516b8541e363499aa689 | 4aa450aed15577210294dac4a6177610a65cd63df62f516b8541e363499aa689 |
| src/hermes/app/session/hooks/use-background-queue-drain.ts | verbatim | apps/desktop/src/app/session/hooks/use-background-queue-drain.ts | 4fdd03febd14739aede3184b58eacc469fd0543d83a524d77bde0367a710ece1 | 4fdd03febd14739aede3184b58eacc469fd0543d83a524d77bde0367a710ece1 |
| src/hermes/app/session/hooks/use-busy-input-mode.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-busy-input-mode.test.tsx | 603558ebefc1b65ddecb1cccf4ce07e982944ac156b2150e74acc5f228ff386c | 603558ebefc1b65ddecb1cccf4ce07e982944ac156b2150e74acc5f228ff386c |
| src/hermes/app/session/hooks/use-busy-input-mode.ts | verbatim | apps/desktop/src/app/session/hooks/use-busy-input-mode.ts | b7bc01855535b62b29ca0cc300ed6a9aeb4b39e40301a3f130e1af8f303f5b2b | b7bc01855535b62b29ca0cc300ed6a9aeb4b39e40301a3f130e1af8f303f5b2b |
| src/hermes/app/session/hooks/use-context-suggestions.ts | patch | apps/desktop/src/app/session/hooks/use-context-suggestions.ts | 9e01b04f4c96d4b85ef1a8949475845406820ff701653402c7da9223487fdd00 | 4952cad2f0c4ae4580f6e23deb77c275396f0b9b9143c62e05a053b7649b2c00 |
| src/hermes/app/session/hooks/use-cwd-actions.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-cwd-actions.test.tsx | 93fef4f68debd2d9520ad5bfcb878194aa3ab23617f7504b13c4ee261cb590ba | 93fef4f68debd2d9520ad5bfcb878194aa3ab23617f7504b13c4ee261cb590ba |
| src/hermes/app/session/hooks/use-cwd-actions.ts | verbatim | apps/desktop/src/app/session/hooks/use-cwd-actions.ts | e2b8bf258da647172075f54fad7944977a0e6f94fa79c30c2d2e1331930f36bf | e2b8bf258da647172075f54fad7944977a0e6f94fa79c30c2d2e1331930f36bf |
| src/hermes/app/session/hooks/use-hermes-config.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-hermes-config.test.ts | 97e095459331c6d79b397fae41b9bb554af61ec8f5e7fb0935d0fa158d91f3fb | 97e095459331c6d79b397fae41b9bb554af61ec8f5e7fb0935d0fa158d91f3fb |
| src/hermes/app/session/hooks/use-hermes-config.ts | verbatim | apps/desktop/src/app/session/hooks/use-hermes-config.ts | 1e93d848b34991b7b189842c6057846fc5dd6b4fa4410b1618a670fb4917fb4e | 1e93d848b34991b7b189842c6057846fc5dd6b4fa4410b1618a670fb4917fb4e |
| src/hermes/app/session/hooks/use-message-stream/completion-boundaries.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/completion-boundaries.test.tsx | fad5dc302d1066d891826579b8f889947fe42cc80c887caedf11acf245fa85db | fad5dc302d1066d891826579b8f889947fe42cc80c887caedf11acf245fa85db |
| src/hermes/app/session/hooks/use-message-stream/duplicate-background-reply.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/duplicate-background-reply.test.tsx | c9cb94b69d613f94f41f5b46f3a1b6bdb43d804cabfd24cfadd693d66133cc11 | c9cb94b69d613f94f41f5b46f3a1b6bdb43d804cabfd24cfadd693d66133cc11 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/deferred-pick.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/deferred-pick.test.ts | 17878001f7e4aa44ca3d56537ddb81695299c574de2f7c67adc4db695d1ed7c9 | 17878001f7e4aa44ca3d56537ddb81695299c574de2f7c67adc4db695d1ed7c9 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/deferred-pick.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/deferred-pick.ts | c57731642b182ec0b16f50acec3cb8fee4f30a65368e631aea867c3361751477 | c57731642b182ec0b16f50acec3cb8fee4f30a65368e631aea867c3361751477 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.reaction.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.reaction.test.ts | 172cb8e4ef4b23e6497d5882815b0d01e0dbe2aa4ceff33ace27964738a9af95 | 172cb8e4ef4b23e6497d5882815b0d01e0dbe2aa4ceff33ace27964738a9af95 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.test.ts | 9f817e4a1736555015887138bd6f790b7027def5fd4173291bf1b0738a1d94a6 | 9f817e4a1736555015887138bd6f790b7027def5fd4173291bf1b0738a1d94a6 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts | patch | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/desktop-bridge.ts | ad428da207c72b1d68aabaefcac876791e19229152d4456e6e5a886b7daad504 | c8dcb10b2e3f0689b1d201fc7b7890b607b065baab3ef37691a539316f5d326e |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/free-tier.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/free-tier.ts | 25c13257e000240082d813cec689a41f0239a1c71301e11aaf203879252f37cf | 25c13257e000240082d813cec689a41f0239a1c71301e11aaf203879252f37cf |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/index.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/index.ts | 9cb3497c3e1e7562d1dd4f5a60f037e2c4407c219fdfca71ce6d8f40c493cf00 | 9cb3497c3e1e7562d1dd4f5a60f037e2c4407c219fdfca71ce6d8f40c493cf00 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/input-requests.approval-timeout.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.approval-timeout.test.ts | d6a455fc8eb5391e8fc3d48ac9781de45948045508bc1f034f759d1d076bfd6b | d6a455fc8eb5391e8fc3d48ac9781de45948045508bc1f034f759d1d076bfd6b |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/input-requests.settled.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.settled.test.ts | 78b525f0be94de64d17fbef21cbbc73a66f9ba3263472da82aaf01d3591bba64 | 78b525f0be94de64d17fbef21cbbc73a66f9ba3263472da82aaf01d3591bba64 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/input-requests.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.test.ts | 4b334687d581a9fcee017576aebc5336effff32439b94734bfd56eaa80cb3c40 | 4b334687d581a9fcee017576aebc5336effff32439b94734bfd56eaa80cb3c40 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/input-requests.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/input-requests.ts | 7dc56b103699280880b16fecba260033b704ab02bd1ac072cd9b33ff15e2dce4 | 7dc56b103699280880b16fecba260033b704ab02bd1ac072cd9b33ff15e2dce4 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/lifecycle.approval-cancelled.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/lifecycle.approval-cancelled.test.ts | 5055e4366062dece4a35e6402ab24a803d75c222280ba8c041b0628c451071b3 | 5055e4366062dece4a35e6402ab24a803d75c222280ba8c041b0628c451071b3 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/onboarding.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/onboarding.ts | 35b42e9f7b74e6172b3feb73c2bd252b6eaec11e1413b81f4888d63dc9549646 | 35b42e9f7b74e6172b3feb73c2bd252b6eaec11e1413b81f4888d63dc9549646 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts | 67ecf56c76f1afb7a6ff2b2b79a255b294d04ad9b99955291ef57e598ff40931 | 67ecf56c76f1afb7a6ff2b2b79a255b294d04ad9b99955291ef57e598ff40931 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/server-requests.ts | patch | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/server-requests.ts | ea2a481a6ea71d19593d0790f0a2ec2559ee1ebf1ef329204b85f012d45139e6 | 3d4aabb63b59dd94a1dbdeebae33762e1e80d43d623166b9be6404dbc1009ddb |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/session-control.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/session-control.test.ts | 1cb1c0f578bf599d1a55d0327a7e19ac5aeab59b5ffbe6d44a9f39c0ef74f4f1 | 1cb1c0f578bf599d1a55d0327a7e19ac5aeab59b5ffbe6d44a9f39c0ef74f4f1 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/session-control.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/session-control.ts | cf606d1c8924dd9a14a8b4e46b64f6bd11a04ae9a784190375dc9b560e08c0f9 | cf606d1c8924dd9a14a8b4e46b64f6bd11a04ae9a784190375dc9b560e08c0f9 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/status.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.test.ts | add4b245cc745007fdfec94556cd7e097ba957a466dd5543636698ac06121008 | add4b245cc745007fdfec94556cd7e097ba957a466dd5543636698ac06121008 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/status.ts | patch | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.ts | 29c10475890dfe0d3619cb6e1e03af35063fa0196cdb8af554e62518a23da2ac | 47ae34314d980b8cc35073bbd413aad8139dc547ecedbacae4bebaddc393baab |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/tools-preview.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/tools-preview.test.ts | a1e755867cfbd300fba12032ee97d7e1a010c6cf687239830e79c1c644bbcf40 | a1e755867cfbd300fba12032ee97d7e1a010c6cf687239830e79c1c644bbcf40 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/tools-progress.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/tools-progress.test.ts | 101f1649801d7724827d5bd40da6fd69369d482e1fcbd013ebcde3748a2e6e9a | 101f1649801d7724827d5bd40da6fd69369d482e1fcbd013ebcde3748a2e6e9a |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/tools-subagent-interrupt.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/tools-subagent-interrupt.test.ts | ae940e2c22684b5d1c838bb5c17752ddecdd7a25344c562560176520526bccc7 | ae940e2c22684b5d1c838bb5c17752ddecdd7a25344c562560176520526bccc7 |
| src/hermes/app/session/hooks/use-message-stream/gateway-event/tools.ts | patch | apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/tools.ts | b020adce78b5b43f4f9dbc34e29c46db62d45a6d62252c2b1b2a555a4f0765a9 | 4162265cc41eba322f020b1b53448bedd26499f808aa77b7090476d1f7010751 |
| src/hermes/app/session/hooks/use-message-stream/index.ts | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/index.ts | a727aad73710b90657e61e7c4aaa0ff73ffcfac25ccbeffbd7a8dd3b97a31888 | a727aad73710b90657e61e7c4aaa0ff73ffcfac25ccbeffbd7a8dd3b97a31888 |
| src/hermes/app/session/hooks/use-message-stream/interrupted-reply.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/interrupted-reply.test.tsx | 4637d39d748dc7b42aa900368b0ee2f4a10d949a819f75a41f12ba4fc77e762c | 4637d39d748dc7b42aa900368b0ee2f4a10d949a819f75a41f12ba4fc77e762c |
| src/hermes/app/session/hooks/use-message-stream/steer-arrival-order.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/steer-arrival-order.test.tsx | 40222001fe56a667c6fcb95a029dafe8b12ba99193b5aa617c5f327a392f2e03 | 40222001fe56a667c6fcb95a029dafe8b12ba99193b5aa617c5f327a392f2e03 |
| src/hermes/app/session/hooks/use-message-stream/test-harness.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/test-harness.tsx | 4b6a8b8542341d00a0d26de7d640fa76276cc8e9388491d99e776e587defc575 | 4b6a8b8542341d00a0d26de7d640fa76276cc8e9388491d99e776e587defc575 |
| src/hermes/app/session/hooks/use-message-stream/tool-drafting-event.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-message-stream/tool-drafting-event.test.tsx | 2913686f718af53e66a7e7f48e4445cb4ae035460341b81b03e1cc387e00fcae | 2913686f718af53e66a7e7f48e4445cb4ae035460341b81b03e1cc387e00fcae |
| src/hermes/app/session/hooks/use-model-controls-stale-pick.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-model-controls-stale-pick.test.tsx | 2b662168ea65b613bdf989e2648d949ea3f9a18f992c7e3007bb784d2081a799 | 2b662168ea65b613bdf989e2648d949ea3f9a18f992c7e3007bb784d2081a799 |
| src/hermes/app/session/hooks/use-model-controls.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-model-controls.test.tsx | e7e0163722cb3d5e8d7a0feea1bc800149401e3b2e691ef307b377975f909e37 | e7e0163722cb3d5e8d7a0feea1bc800149401e3b2e691ef307b377975f909e37 |
| src/hermes/app/session/hooks/use-model-controls.ts | verbatim | apps/desktop/src/app/session/hooks/use-model-controls.ts | 812d29892786cd8d62941071f9d8f0d15477d1bf2b66e9781133607d88539b12 | 812d29892786cd8d62941071f9d8f0d15477d1bf2b66e9781133607d88539b12 |
| src/hermes/app/session/hooks/use-preview-routing.ts | verbatim | apps/desktop/src/app/session/hooks/use-preview-routing.ts | f33e871751556efefbf3bfa22fc6a6975c214535df190d65004c1785d5570e93 | f33e871751556efefbf3bfa22fc6a6975c214535df190d65004c1785d5570e93 |
| src/hermes/app/session/hooks/use-prompt-actions/browser-manage.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/browser-manage.test.tsx | 10e177b600e088023d7c183e9d146c1b16c77ba593591be69ec785ed83a30b31 | 10e177b600e088023d7c183e9d146c1b16c77ba593591be69ec785ed83a30b31 |
| src/hermes/app/session/hooks/use-prompt-actions/index-test-utils.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/index-test-utils.tsx | 6649bf638e8f7aee843808527bcabfada66f7b45a6c372fa3b3133f9d84c6ab3 | 6649bf638e8f7aee843808527bcabfada66f7b45a6c372fa3b3133f9d84c6ab3 |
| src/hermes/app/session/hooks/use-prompt-actions/index.deep-cut.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/index.deep-cut.test.tsx | 10fdd4b07f11b2a1d5d353878dac24fb0b311c81a894f74c19b1b1aff2c068ca | 10fdd4b07f11b2a1d5d353878dac24fb0b311c81a894f74c19b1b1aff2c068ca |
| src/hermes/app/session/hooks/use-prompt-actions/index.sleep-wake.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/index.sleep-wake.test.tsx | 8ef5e38df404863aee40780a5de33f82d89d00f6c99160a7f526899b896e18ba | 8ef5e38df404863aee40780a5de33f82d89d00f6c99160a7f526899b896e18ba |
| src/hermes/app/session/hooks/use-prompt-actions/index.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx | 5a261c3c25b02662c356a35a5096f7acc1d503dd47d20abd7712cd91303bb2d4 | 5a261c3c25b02662c356a35a5096f7acc1d503dd47d20abd7712cd91303bb2d4 |
| src/hermes/app/session/hooks/use-prompt-actions/index.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts | 4354dc7f330675251e77e0964015ab3fec2b50d9c284754a53337e129fd8b6d0 | 4354dc7f330675251e77e0964015ab3fec2b50d9c284754a53337e129fd8b6d0 |
| src/hermes/app/session/hooks/use-prompt-actions/prepared-submissions-spent.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submissions-spent.test.ts | 04a6a3813db19cb60fcf83b1a72107632478d8d3eb1e7922c36e313a8277c01d | 04a6a3813db19cb60fcf83b1a72107632478d8d3eb1e7922c36e313a8277c01d |
| src/hermes/app/session/hooks/use-prompt-actions/prepared-submissions.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submissions.test.ts | e56d7383740ce77d6d08cca94c0f03f2e3a6da763151ac687c9a6f2386e82e7a | e56d7383740ce77d6d08cca94c0f03f2e3a6da763151ac687c9a6f2386e82e7a |
| src/hermes/app/session/hooks/use-prompt-actions/prepared-submissions.ts | patch | apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submissions.ts | f1da970d9bcc9818b4f59683d3523cd173eacfa48f21fdb0dc5f8b62f2795f8a | d65208ffded5e038e12d1c75b98658c8846e433997a8fdcbfb9e479cb03befd8 |
| src/hermes/app/session/hooks/use-prompt-actions/prompt-actions-harness.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/prompt-actions-harness.tsx | 74821450fb08e41538f2e0a7c1251d8701311cc572f99502f0660227b5ffc897 | 74821450fb08e41538f2e0a7c1251d8701311cc572f99502f0660227b5ffc897 |
| src/hermes/app/session/hooks/use-prompt-actions/queue-if-busy.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/queue-if-busy.test.ts | 15d5c66bbfc27f7efcad095ed0e0da83bc8ac6802adabe566bb11d786965670e | 15d5c66bbfc27f7efcad095ed0e0da83bc8ac6802adabe566bb11d786965670e |
| src/hermes/app/session/hooks/use-prompt-actions/queue-if-busy.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/queue-if-busy.ts | 9d78e474a9d96f1f920eed8025f7b52796ed4efa06d8abd7587dbca4ca5aecb4 | 9d78e474a9d96f1f920eed8025f7b52796ed4efa06d8abd7587dbca4ca5aecb4 |
| src/hermes/app/session/hooks/use-prompt-actions/queued-first-input.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/queued-first-input.test.tsx | 1c1f49930a6f57bb42d35c51951211acd6a7b8789a1f0116c3516d594cee4616 | 1c1f49930a6f57bb42d35c51951211acd6a7b8789a1f0116c3516d594cee4616 |
| src/hermes/app/session/hooks/use-prompt-actions/resolve-target-session.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/resolve-target-session.test.ts | 00743326d89ad5443d7ec4446e999a9beb736ba03707b61b7759bad443f27d93 | 00743326d89ad5443d7ec4446e999a9beb736ba03707b61b7759bad443f27d93 |
| src/hermes/app/session/hooks/use-prompt-actions/resolve-target-session.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/resolve-target-session.ts | e3ef1f5533400ceb70ff3c1812e51f078e040cacc41a1689e9a64a83f80f19d0 | e3ef1f5533400ceb70ff3c1812e51f078e040cacc41a1689e9a64a83f80f19d0 |
| src/hermes/app/session/hooks/use-prompt-actions/rewind.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/rewind.test.ts | fcce777bd28485831ab9daac39e139075340275241bec5c3fde21bca50f509ac | fcce777bd28485831ab9daac39e139075340275241bec5c3fde21bca50f509ac |
| src/hermes/app/session/hooks/use-prompt-actions/rewind.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/rewind.ts | 45db2302fda36ee621e7c9a453f765f36a65d70fecd416708043a3a5f3534729 | 45db2302fda36ee621e7c9a453f765f36a65d70fecd416708043a3a5f3534729 |
| src/hermes/app/session/hooks/use-prompt-actions/single-flight-resume.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/single-flight-resume.test.ts | f720e6a43043588cbf44b3862b5dc1855e75d4f63533d0e9340dbf176f2db68a | f720e6a43043588cbf44b3862b5dc1855e75d4f63533d0e9340dbf176f2db68a |
| src/hermes/app/session/hooks/use-prompt-actions/single-flight-resume.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/single-flight-resume.ts | 9a5acc1703def9809eee6bb2a9d7fa695af57d1421e3adb1d583ae0f7f8f453a | 9a5acc1703def9809eee6bb2a9d7fa695af57d1421e3adb1d583ae0f7f8f453a |
| src/hermes/app/session/hooks/use-prompt-actions/slash-desktop-commands.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/slash-desktop-commands.test.tsx | 1c98e156804a2d9602bd55e8b79e39d0ce787c5fbcf5b0db753cf34ef174e866 | 1c98e156804a2d9602bd55e8b79e39d0ce787c5fbcf5b0db753cf34ef174e866 |
| src/hermes/app/session/hooks/use-prompt-actions/slash-desktop-commands.ts | patch | apps/desktop/src/app/session/hooks/use-prompt-actions/slash-desktop-commands.ts | 6907a7c9fff6580560b51e1c4d5bf5590fdd760c42c2a3422118c7014dc21933 | 219fcb2fa7f581e5ebe4ba72b0e7a3e459dfd39239744025ba289134765b1341 |
| src/hermes/app/session/hooks/use-prompt-actions/slash-session-commands.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/slash-session-commands.test.tsx | 9b77e8cfad6d3e415e2722a399b58b862da48e95f125a77613de40791654a19d | 9b77e8cfad6d3e415e2722a399b58b862da48e95f125a77613de40791654a19d |
| src/hermes/app/session/hooks/use-prompt-actions/slash-session-commands.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/slash-session-commands.ts | 7a1724c3ab2f6622ed76a61f64692df9f0f0d0de36eec243f00c87f02663654b | 7a1724c3ab2f6622ed76a61f64692df9f0f0d0de36eec243f00c87f02663654b |
| src/hermes/app/session/hooks/use-prompt-actions/slash-session-user-turns.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/slash-session-user-turns.test.ts | ef48e7678de86b520a290596fb52a15e1636772b531d6f3cefaca519bea642c9 | ef48e7678de86b520a290596fb52a15e1636772b531d6f3cefaca519bea642c9 |
| src/hermes/app/session/hooks/use-prompt-actions/slash.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts | 8d787a55296c5b7dd5d1cbc9d1e376d07a1e095d1abb1059dfd2025239b0b1ed | 8d787a55296c5b7dd5d1cbc9d1e376d07a1e095d1abb1059dfd2025239b0b1ed |
| src/hermes/app/session/hooks/use-prompt-actions/steering-recovery.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/steering-recovery.test.tsx | 792d405edc845eefb0384df8a26b9d224f56e874946f086b2f19bdb0421732c9 | 792d405edc845eefb0384df8a26b9d224f56e874946f086b2f19bdb0421732c9 |
| src/hermes/app/session/hooks/use-prompt-actions/steering-session.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/steering-session.ts | 13d5c7dfea0ee910853b2144bb538518a6e9007c3169d825a631c6437b96727f | 13d5c7dfea0ee910853b2144bb538518a6e9007c3169d825a631c6437b96727f |
| src/hermes/app/session/hooks/use-prompt-actions/steering-undelivered-refusal.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/steering-undelivered-refusal.test.tsx | 9a99857c6ec0a0775c1e242116f3d8b04fb33a9aeb339f572447f425103cd592 | 9a99857c6ec0a0775c1e242116f3d8b04fb33a9aeb339f572447f425103cd592 |
| src/hermes/app/session/hooks/use-prompt-actions/submission-destination.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submission-destination.test.tsx | 36c30e09e65a2de2b3b087de90b4f099430a868d0e242151eef1eb760d5a5330 | 36c30e09e65a2de2b3b087de90b4f099430a868d0e242151eef1eb760d5a5330 |
| src/hermes/app/session/hooks/use-prompt-actions/submission-destination.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submission-destination.ts | 24eb4f10780c74f74ad132b63912f920ff26dcfe99f329917e416cbddc90cdb3 | 24eb4f10780c74f74ad132b63912f920ff26dcfe99f329917e416cbddc90cdb3 |
| src/hermes/app/session/hooks/use-prompt-actions/submit-unknown-receipt.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submit-unknown-receipt.test.tsx | 65c55ef84d4b25fc935c830522eb3013aa59209d7c287bfc086e4a111fead841 | 65c55ef84d4b25fc935c830522eb3013aa59209d7c287bfc086e4a111fead841 |
| src/hermes/app/session/hooks/use-prompt-actions/submit-view-binding.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submit-view-binding.test.ts | c2ede8d512b0e6298439d45cfdcc444d9a230e1bad4f5c65897c15e8a1baf00b | c2ede8d512b0e6298439d45cfdcc444d9a230e1bad4f5c65897c15e8a1baf00b |
| src/hermes/app/session/hooks/use-prompt-actions/submit-voice-stopped.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submit-voice-stopped.test.tsx | caf7976c8efafdd1608b323fa4a944a68e169aff94b4ca943b4420e5f5517b12 | caf7976c8efafdd1608b323fa4a944a68e169aff94b4ca943b4420e5f5517b12 |
| src/hermes/app/session/hooks/use-prompt-actions/submit.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts | a878886a8e4b85434b28fbb00731defbab6605792a9c319913678c1f6a8a0779 | a878886a8e4b85434b28fbb00731defbab6605792a9c319913678c1f6a8a0779 |
| src/hermes/app/session/hooks/use-prompt-actions/upload-server-image.ts | patch | apps/desktop/src/app/session/hooks/use-prompt-actions/upload-server-image.ts | 6c9d283f15f19758484cf69146a3f03000a371bcfe0fed04081cbfac8b8b8306 | b5e039935223c5947090a9b9f89d70841017834f02c49f2d31bd4919365449ab |
| src/hermes/app/session/hooks/use-prompt-actions/utils.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-prompt-actions/utils.test.ts | 2de6d8b6a066c9f05cc89031646405d17e14ab66e3f75d8c3b95b768b8af0be6 | 2de6d8b6a066c9f05cc89031646405d17e14ab66e3f75d8c3b95b768b8af0be6 |
| src/hermes/app/session/hooks/use-prompt-actions/utils.ts | patch | apps/desktop/src/app/session/hooks/use-prompt-actions/utils.ts | b21703f4b23fc0f09f39a28c074acfce2ad1d96fd33f88c7024dba1a286be41e | 0dc4361575b2bcf20aee8018ebe6f0e28fd618be584729e44c4384bf42ca8c05 |
| src/hermes/app/session/hooks/use-route-resume.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-route-resume.test.tsx | 408b2b81a2fdebd0e73f92e15a69b2c95b0080800f488ea83c0ea42f82cfe6b2 | 408b2b81a2fdebd0e73f92e15a69b2c95b0080800f488ea83c0ea42f82cfe6b2 |
| src/hermes/app/session/hooks/use-route-resume.ts | verbatim | apps/desktop/src/app/session/hooks/use-route-resume.ts | cef024e82e0b3d9880a52c558d3e08f1f850d53c996323c7c53bc3379d6c2f5f | cef024e82e0b3d9880a52c558d3e08f1f850d53c996323c7c53bc3379d6c2f5f |
| src/hermes/app/session/hooks/use-session-actions.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-session-actions.test.tsx | 6fb8efed41252fe16141215a894337afdd2a3cc279b0c8a519409e667e1f44a7 | 6fb8efed41252fe16141215a894337afdd2a3cc279b0c8a519409e667e1f44a7 |
| src/hermes/app/session/hooks/use-session-actions/branch-boundary.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/branch-boundary.test.ts | fc6aac0817e3cb1736e8d1e1906b16548c93cfaf310e6ae292a218292bb054ea | fc6aac0817e3cb1736e8d1e1906b16548c93cfaf310e6ae292a218292bb054ea |
| src/hermes/app/session/hooks/use-session-actions/branch-boundary.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/branch-boundary.ts | 16983ffac330d880289baf16547f312da8ed7d3f42db2100da18663f12267bbc | 16983ffac330d880289baf16547f312da8ed7d3f42db2100da18663f12267bbc |
| src/hermes/app/session/hooks/use-session-actions/branch-create-key.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/branch-create-key.ts | 4e79018046fae6bdfd459d69d4dbb2eae689e9273138cc71138ef52169a0c404 | 4e79018046fae6bdfd459d69d4dbb2eae689e9273138cc71138ef52169a0c404 |
| src/hermes/app/session/hooks/use-session-actions/create-overrides.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/create-overrides.ts | a5f64048ba12b0681747b4a27757ffb8b68207eb979b1711a23ef96ae4237449 | a5f64048ba12b0681747b4a27757ffb8b68207eb979b1711a23ef96ae4237449 |
| src/hermes/app/session/hooks/use-session-actions/created-this-run.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/created-this-run.ts | 56de1a5b2af77d213dc6c0fb1be570120ee08ad417d386e1242537f6896909c2 | 56de1a5b2af77d213dc6c0fb1be570120ee08ad417d386e1242537f6896909c2 |
| src/hermes/app/session/hooks/use-session-actions/display-hydration.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/display-hydration.ts | 7753d86462515190daf8782043cbf2f04c9e272fa6d28a95328efd0fcd93cfbb | 7753d86462515190daf8782043cbf2f04c9e272fa6d28a95328efd0fcd93cfbb |
| src/hermes/app/session/hooks/use-session-actions/index.ts | patch | apps/desktop/src/app/session/hooks/use-session-actions/index.ts | 41ef12485c0934d1db78a32d4fec70deb70e4d025484563a02460dc24136b825 | bf36d2ef02c930890705a14064359e8b719684fef5acc242291545c862480a5c |
| src/hermes/app/session/hooks/use-session-actions/live-turn-remainder.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/live-turn-remainder.test.ts | 6c278ff583b68aeea24afb89b63be6482f524c9290d210c34221a3bb02ecbe4f | 6c278ff583b68aeea24afb89b63be6482f524c9290d210c34221a3bb02ecbe4f |
| src/hermes/app/session/hooks/use-session-actions/live-turn-remainder.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/live-turn-remainder.ts | 7af7fc8794eda10f674c1a0253184850c9e6d8c0c52883575e26a753571a3881 | 7af7fc8794eda10f674c1a0253184850c9e6d8c0c52883575e26a753571a3881 |
| src/hermes/app/session/hooks/use-session-actions/persisted-live-turn.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/persisted-live-turn.ts | 733386770c61070a0540416009eb5692cf510182327522ba9c94887ed7cb1163 | 733386770c61070a0540416009eb5692cf510182327522ba9c94887ed7cb1163 |
| src/hermes/app/session/hooks/use-session-actions/provisional-transcript.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/provisional-transcript.ts | 9c8250cad40b06d6cb240a2f5af4e95acc0fdab605e32146053c77a1a8ed5649 | 9c8250cad40b06d6cb240a2f5af4e95acc0fdab605e32146053c77a1a8ed5649 |
| src/hermes/app/session/hooks/use-session-actions/remembered-owner.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/remembered-owner.test.ts | 1782f1aa4b2d3cc7fccada61c7259f14f81890942fe89cacdb1778a98abeb544 | 1782f1aa4b2d3cc7fccada61c7259f14f81890942fe89cacdb1778a98abeb544 |
| src/hermes/app/session/hooks/use-session-actions/remembered-owner.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/remembered-owner.ts | 58f91b284ab8b1de240a5027707627d5e551dcbefc75f3f461b356c1c2ab7d9d | 58f91b284ab8b1de240a5027707627d5e551dcbefc75f3f461b356c1c2ab7d9d |
| src/hermes/app/session/hooks/use-session-actions/restore-pending-approval.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/restore-pending-approval.ts | 1de293155e31a9bd840088194f6ef162ce48532432e953cc7d82141d1b28e8b9 | 1de293155e31a9bd840088194f6ef162ce48532432e953cc7d82141d1b28e8b9 |
| src/hermes/app/session/hooks/use-session-actions/restore-pending-clarify.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/restore-pending-clarify.test.ts | d0fc33892fb67129dc25688ef5ab2723d10fa1cf2befc6010986177dfaa3e919 | d0fc33892fb67129dc25688ef5ab2723d10fa1cf2befc6010986177dfaa3e919 |
| src/hermes/app/session/hooks/use-session-actions/restore-pending-clarify.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/restore-pending-clarify.ts | d665b75c9fad9c2b6e1929d7bb7f3a8f6664d1812699308d5868ff335e5d2210 | d665b75c9fad9c2b6e1929d7bb7f3a8f6664d1812699308d5868ff335e5d2210 |
| src/hermes/app/session/hooks/use-session-actions/restore-pending-connection.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/restore-pending-connection.ts | b5d8f69cd96c874a6a34960e6058aab7a1f11e4714130f1f44b220b140c68c56 | b5d8f69cd96c874a6a34960e6058aab7a1f11e4714130f1f44b220b140c68c56 |
| src/hermes/app/session/hooks/use-session-actions/resume-owner.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/resume-owner.ts | 7a25e6650b71dca77d59bc9e8240fb3383918b918e9ac5c344fe99927d0594aa | 7a25e6650b71dca77d59bc9e8240fb3383918b918e9ac5c344fe99927d0594aa |
| src/hermes/app/session/hooks/use-session-actions/session-create-request.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/session-create-request.test.ts | 242d3fb99cf58d6b504550f697aa7c9bb8eca8f578d47dfc4efb80586bae7c6c | 242d3fb99cf58d6b504550f697aa7c9bb8eca8f578d47dfc4efb80586bae7c6c |
| src/hermes/app/session/hooks/use-session-actions/session-create-request.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/session-create-request.ts | 9985550d5c742eac7f478f1c526ce448fcf66188438bb91917a006d98b868c89 | 9985550d5c742eac7f478f1c526ce448fcf66188438bb91917a006d98b868c89 |
| src/hermes/app/session/hooks/use-session-actions/transcript-provenance.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/transcript-provenance.test.ts | 392038909094b6f3f9f238fa53d1be14b4ee3f19c2dc29633ec58578592f06a9 | 392038909094b6f3f9f238fa53d1be14b4ee3f19c2dc29633ec58578592f06a9 |
| src/hermes/app/session/hooks/use-session-actions/transcript-provenance.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/transcript-provenance.ts | b140200734faf5fad98048640da36aa4e8f07d347cdb94ceadb865982e4718b9 | b140200734faf5fad98048640da36aa4e8f07d347cdb94ceadb865982e4718b9 |
| src/hermes/app/session/hooks/use-session-actions/utils.test.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-actions/utils.test.ts | fc22b73dd9faba067cb51092330fa12b011e593e5c91b218e678c7f1e58f43a8 | fc22b73dd9faba067cb51092330fa12b011e593e5c91b218e678c7f1e58f43a8 |
| src/hermes/app/session/hooks/use-session-list-actions.reactivation.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-session-list-actions.reactivation.test.tsx | 15eeccfb9c89af6a9ddd276f8936035d8db93779f4e7d9991867d406522eea86 | 15eeccfb9c89af6a9ddd276f8936035d8db93779f4e7d9991867d406522eea86 |
| src/hermes/app/session/hooks/use-session-list-actions.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-session-list-actions.test.tsx | a6654a0883e1e7237f053593acd345ab1110ae6a68a89ae9e67a1d0ae2715a78 | a6654a0883e1e7237f053593acd345ab1110ae6a68a89ae9e67a1d0ae2715a78 |
| src/hermes/app/session/hooks/use-session-list-actions.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-list-actions.ts | 4f3fa082a0a0125d4955faf243bc7606781ae0031053ce7810eeeda337dd0fdb | 4f3fa082a0a0125d4955faf243bc7606781ae0031053ce7810eeeda337dd0fdb |
| src/hermes/app/session/hooks/use-session-state-cache.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-session-state-cache.test.tsx | 3a6da775d671dff35c603e57a74375e8992b60c7f89c6956ab8fcb5768651a7b | 3a6da775d671dff35c603e57a74375e8992b60c7f89c6956ab8fcb5768651a7b |
| src/hermes/app/session/hooks/use-session-state-cache.ts | verbatim | apps/desktop/src/app/session/hooks/use-session-state-cache.ts | 624aa92b5afd46441c64864a1ac0eb4ea978383e90439c7da1dab94944bb98fe | 624aa92b5afd46441c64864a1ac0eb4ea978383e90439c7da1dab94944bb98fe |
| src/hermes/app/session/hooks/use-transcript-peer-sync.test.tsx | verbatim | apps/desktop/src/app/session/hooks/use-transcript-peer-sync.test.tsx | 8f3a0a283eb0fa7016a21a1072ff3c4f1f3459975471b1a779fcb2dc6a970201 | 8f3a0a283eb0fa7016a21a1072ff3c4f1f3459975471b1a779fcb2dc6a970201 |
| src/hermes/app/session/hooks/use-transcript-peer-sync.ts | verbatim | apps/desktop/src/app/session/hooks/use-transcript-peer-sync.ts | 3c2a94dd3a08b0a205aea39f0529655f415a89e1561442f31c58f02a65bb7cf9 | 3c2a94dd3a08b0a205aea39f0529655f415a89e1561442f31c58f02a65bb7cf9 |
| src/hermes/app/session/new-session-route.ts | verbatim | apps/desktop/src/app/session/new-session-route.ts | 89b0fc40d4ab94f61c68a7bbeb0afb47179facfda43ab0574f1947af7e6dca46 | 89b0fc40d4ab94f61c68a7bbeb0afb47179facfda43ab0574f1947af7e6dca46 |
| src/hermes/app/session/session-state-cache.test.ts | verbatim | apps/desktop/src/app/session/session-state-cache.test.ts | c2eae1d4c5f5801e127d118a977707da217cae7fe22a4d52e1cc7f24c2d7cca7 | c2eae1d4c5f5801e127d118a977707da217cae7fe22a4d52e1cc7f24c2d7cca7 |
| src/hermes/app/session/session-state-cache.ts | verbatim | apps/desktop/src/app/session/session-state-cache.ts | 15a47840d5495742ee3d77fba19f14d42681e20d00479839049aa61adda5d630 | 15a47840d5495742ee3d77fba19f14d42681e20d00479839049aa61adda5d630 |
| src/hermes/app/settings/billing/api.test.ts | verbatim | apps/desktop/src/app/settings/billing/api.test.ts | 7d6306a8b18f68b2abb157b17b8d4fcb6d81bf70aa554466bf7c03d15adabbe9 | 7d6306a8b18f68b2abb157b17b8d4fcb6d81bf70aa554466bf7c03d15adabbe9 |
| src/hermes/app/settings/billing/api.ts | verbatim | apps/desktop/src/app/settings/billing/api.ts | 78ffae1e7ef1e358ecda389524a7522faf319471bf82686d289f2ad95832fb21 | 78ffae1e7ef1e358ecda389524a7522faf319471bf82686d289f2ad95832fb21 |
| src/hermes/app/settings/billing/types.ts | verbatim | apps/desktop/src/app/settings/billing/types.ts | 5313255acf459bdefd1de671c03ae714d53f2f6b55c747b1cdf5381849f8bbaf | 5313255acf459bdefd1de671c03ae714d53f2f6b55c747b1cdf5381849f8bbaf |
| src/hermes/app/settings/constants.ts | patch | apps/desktop/src/app/settings/constants.ts | 6865711bcf243cbae85b032e7f3241b837568a81ee0a3da077430f9c6c075654 | b5d7c4ecdde0809b773b47e813111c3a733dbd591567464284895a88ae3cab94 |
| src/hermes/app/settings/field-copy.ts | verbatim | apps/desktop/src/app/settings/field-copy.ts | 55e84fc8c967874d41070b7a4b29c8af648035061dee60f7ddbc716a0738251d | 55e84fc8c967874d41070b7a4b29c8af648035061dee60f7ddbc716a0738251d |
| src/hermes/app/settings/types.ts | verbatim | apps/desktop/src/app/settings/types.ts | b0af3b7b34e3e9536aa9ee082c3a89b60f9752fcae8c356895dce160df981bb0 | b0af3b7b34e3e9536aa9ee082c3a89b60f9752fcae8c356895dce160df981bb0 |
| src/hermes/app/shell/context-usage-panel.tsx | verbatim | apps/desktop/src/app/shell/context-usage-panel.tsx | 0575f6151a3b5be73d6e626b074ad986da0718b99e3de3073a4a6acf5734bca4 | 0575f6151a3b5be73d6e626b074ad986da0718b99e3de3073a4a6acf5734bca4 |
| src/hermes/app/shell/local-setup-menu-row.tsx | verbatim | apps/desktop/src/app/shell/local-setup-menu-row.tsx | dff0527df8c42be58d4e89d634c30b2b36fa77171c9b24894d08638e4124834d | dff0527df8c42be58d4e89d634c30b2b36fa77171c9b24894d08638e4124834d |
| src/hermes/app/shell/model-catalog-menu.search-fold.test.tsx | verbatim | apps/desktop/src/app/shell/model-catalog-menu.search-fold.test.tsx | 55f0c4b321015e140abc4d8e9d3417ded8ee9ec3c3c3a99fed7ba5065449fff7 | 55f0c4b321015e140abc4d8e9d3417ded8ee9ec3c3c3a99fed7ba5065449fff7 |
| src/hermes/app/shell/model-catalog-menu.test.tsx | verbatim | apps/desktop/src/app/shell/model-catalog-menu.test.tsx | 960e1ad1b4afe7ff846e4fb561e742bd7172716d68a1cd47d781697873288e0b | 960e1ad1b4afe7ff846e4fb561e742bd7172716d68a1cd47d781697873288e0b |
| src/hermes/app/shell/model-catalog-menu.tsx | verbatim | apps/desktop/src/app/shell/model-catalog-menu.tsx | b42c0d40d76cd2e0c9bf3e2d897af92ec771ce2a01a91424b6d74c0e2db018fa | b42c0d40d76cd2e0c9bf3e2d897af92ec771ce2a01a91424b6d74c0e2db018fa |
| src/hermes/app/shell/model-edit-submenu.test.tsx | verbatim | apps/desktop/src/app/shell/model-edit-submenu.test.tsx | e0e8bc91fa5509a8960fdc63e20d33d324d7ef35e04d7dad090eb3d36d1e7672 | e0e8bc91fa5509a8960fdc63e20d33d324d7ef35e04d7dad090eb3d36d1e7672 |
| src/hermes/app/shell/model-edit-submenu.tsx | verbatim | apps/desktop/src/app/shell/model-edit-submenu.tsx | fdee6b91271fcbd455739f1177c6ae99b0d919de3206b6562da784b865297249 | fdee6b91271fcbd455739f1177c6ae99b0d919de3206b6562da784b865297249 |
| src/hermes/app/shell/model-menu-panel.test.tsx | verbatim | apps/desktop/src/app/shell/model-menu-panel.test.tsx | 5217c11130f5d4bf1e67c27654618e7880d69326fbe877e57edad7dd0b68a1cd | 5217c11130f5d4bf1e67c27654618e7880d69326fbe877e57edad7dd0b68a1cd |
| src/hermes/app/shell/model-menu-panel.tsx | verbatim | apps/desktop/src/app/shell/model-menu-panel.tsx | 100b30e7798a3301f058c795dee5c032671d2ad04a020f72492ad137c3b73fdd | 100b30e7798a3301f058c795dee5c032671d2ad04a020f72492ad137c3b73fdd |
| src/hermes/app/shell/model-menu-row-decorations.tsx | verbatim | apps/desktop/src/app/shell/model-menu-row-decorations.tsx | 6d9260efa6ba99fc0b85a52874dc534280ec4868ec7fe259977568c031a6e140 | 6d9260efa6ba99fc0b85a52874dc534280ec4868ec7fe259977568c031a6e140 |
| src/hermes/app/shell/sidebar-label.tsx | verbatim | apps/desktop/src/app/shell/sidebar-label.tsx | 17cd70b27728a0ac99e45c759a499dc1f965e1f3b5f0cacb6ccef91f56d14347 | 17cd70b27728a0ac99e45c759a499dc1f965e1f3b5f0cacb6ccef91f56d14347 |
| src/hermes/app/shell/titlebar-icon.tsx | verbatim | apps/desktop/src/app/shell/titlebar-icon.tsx | 1d14165d79655a902b6e6812741949b4af028c669ab77e085048738271030da5 | 1d14165d79655a902b6e6812741949b4af028c669ab77e085048738271030da5 |
| src/hermes/app/shell/titlebar.test.ts | verbatim | apps/desktop/src/app/shell/titlebar.test.ts | 768d53eabde82ce3b8952db0e88fa89c2b200be4e544cc9afd586c08b4efc666 | 768d53eabde82ce3b8952db0e88fa89c2b200be4e544cc9afd586c08b4efc666 |
| src/hermes/app/shell/titlebar.ts | patch | apps/desktop/src/app/shell/titlebar.ts | 917bdc702ff9e5d6b3d7141c787fa855d5da337efa34c2564ae4c7db4417b544 | 7e2a1e23c291ffebee316e9e43ab2e5f8bd9fc78b6930b698b61a6a9bd8c7b26 |
| src/hermes/app/shell/use-model-menu-controller.ts | verbatim | apps/desktop/src/app/shell/use-model-menu-controller.ts | 357d3a839f6a9e93151fd75f193e468b604690d0c43d8c66ca60d2247ca95712 | 357d3a839f6a9e93151fd75f193e468b604690d0c43d8c66ca60d2247ca95712 |
| src/hermes/components/Backdrop.tsx | verbatim | apps/desktop/src/components/Backdrop.tsx | 90dfa53be70c350ab8e2facc6ba4fec59290be5845781a3ebc86bcbf463379d3 | 90dfa53be70c350ab8e2facc6ba4fec59290be5845781a3ebc86bcbf463379d3 |
| src/hermes/components/assistant-ui/ansi-text.tsx | verbatim | apps/desktop/src/components/assistant-ui/ansi-text.tsx | f89879ed0846803b578519f02036e5b75ab8dc3e5cdad6a0de18c9170c1789ce | f89879ed0846803b578519f02036e5b75ab8dc3e5cdad6a0de18c9170c1789ce |
| src/hermes/components/assistant-ui/artifact-card.tsx | verbatim | apps/desktop/src/components/assistant-ui/artifact-card.tsx | 39cf5b8ec708c19692be17eb2aaf21aedfdb2b97808596e56fce1d26eb6fd92c | 39cf5b8ec708c19692be17eb2aaf21aedfdb2b97808596e56fce1d26eb6fd92c |
| src/hermes/components/assistant-ui/catalog-advanced-dialog.tsx | verbatim | apps/desktop/src/components/assistant-ui/catalog-advanced-dialog.tsx | beaed25c08ca345033ad43d834dfe6112c0fd87b91f77580a4d0720b6cd19cc3 | beaed25c08ca345033ad43d834dfe6112c0fd87b91f77580a4d0720b6cd19cc3 |
| src/hermes/components/assistant-ui/catalog-install-tool.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/catalog-install-tool.test.tsx | f79bd47943a9a437c2faa81b6783bac9bdee21db76588885fd793f9911226f2b | f79bd47943a9a437c2faa81b6783bac9bdee21db76588885fd793f9911226f2b |
| src/hermes/components/assistant-ui/catalog-install-tool.tsx | shim | apps/desktop/src/components/assistant-ui/catalog-install-tool.tsx | 083f6dfead7e0f97ad4eaf1f08f30d84f85e702bbb5b3fd0a59f36f8571e76ad | cae1d233b021ed66f85d63a6463053b1d3369bec728000c79ba37f5424bef801 |
| src/hermes/components/assistant-ui/chat-empty-slot.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/chat-empty-slot.test.tsx | b15acd52cb3760f160441e584ef8cef89b77becb3023da358cb3405bc4e7751e | b15acd52cb3760f160441e584ef8cef89b77becb3023da358cb3405bc4e7751e |
| src/hermes/components/assistant-ui/chat-empty-slot.tsx | patch | apps/desktop/src/components/assistant-ui/chat-empty-slot.tsx | 121d73bda58c1ef284195d70f6cb0a67984c14849d44aaa56cd01cac4d38e745 | 9ef7f0bb86425310bb8b350943e3a0a654cb4f7642196d207b9e9147f73e8086 |
| src/hermes/components/assistant-ui/clarify/clarify-tool.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/clarify-tool.test.tsx | 6dc7e3be7d2ebc1bf2da1e354e9b45c10901160ed95f0777bef111a5f3de6d20 | 6dc7e3be7d2ebc1bf2da1e354e9b45c10901160ed95f0777bef111a5f3de6d20 |
| src/hermes/components/assistant-ui/clarify/core/choice-row.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/choice-row.tsx | 7f7250339c6ff91c7705b545873a8aacbb1eac51a49ae3c8c8fbc4ea09c5de3c | 7f7250339c6ff91c7705b545873a8aacbb1eac51a49ae3c8c8fbc4ea09c5de3c |
| src/hermes/components/assistant-ui/clarify/core/confirm-bar.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/confirm-bar.tsx | 09ad52b35b0e31134b27b4817a3ec1f85eccfb22ef6bfc4df3c7e1f50fc0f926 | 09ad52b35b0e31134b27b4817a3ec1f85eccfb22ef6bfc4df3c7e1f50fc0f926 |
| src/hermes/components/assistant-ui/clarify/core/question-block.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/question-block.tsx | 477f69d202f1a623dd2fb4a1fe413c2f31e83f8895d08820d6c858ab32f7bfb5 | 477f69d202f1a623dd2fb4a1fe413c2f31e83f8895d08820d6c858ab32f7bfb5 |
| src/hermes/components/assistant-ui/clarify/core/shell.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/shell.tsx | 9ca3a0c1ab994b9b5d44a03fb0a4de5c02adeb56a694b6475ac32a33e9716fe0 | 9ca3a0c1ab994b9b5d44a03fb0a4de5c02adeb56a694b6475ac32a33e9716fe0 |
| src/hermes/components/assistant-ui/clarify/core/use-clarify-keys-handlers.ts | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/use-clarify-keys-handlers.ts | 68eca7569c5adedbc63eb6ce638ea996bce9ec7172ba44f3e1f95fb254a2429e | 68eca7569c5adedbc63eb6ce638ea996bce9ec7172ba44f3e1f95fb254a2429e |
| src/hermes/components/assistant-ui/clarify/core/use-clarify-keys.ts | verbatim | apps/desktop/src/components/assistant-ui/clarify/core/use-clarify-keys.ts | 70a8224f1eecc769f79c13eca792d6368936150654fc337ea22ace36b261fbc6 | 70a8224f1eecc769f79c13eca792d6368936150654fc337ea22ace36b261fbc6 |
| src/hermes/components/assistant-ui/clarify/index.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/index.tsx | 2b2790a41f08b6819a04231c18019b75e66c0258deb1136ad05d184318e8c026 | 2b2790a41f08b6819a04231c18019b75e66c0258deb1136ad05d184318e8c026 |
| src/hermes/components/assistant-ui/clarify/parse.ts | verbatim | apps/desktop/src/components/assistant-ui/clarify/parse.ts | 06001fc85261b4903446cd9fbc6c26782ee431b93990a84c3d974d4ef61a78a9 | 06001fc85261b4903446cd9fbc6c26782ee431b93990a84c3d974d4ef61a78a9 |
| src/hermes/components/assistant-ui/clarify/pending.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/pending.tsx | 5b0661f7ae0719ba5c85641c23dda6d90e0379221d70112cd1b75e0e590f4037 | 5b0661f7ae0719ba5c85641c23dda6d90e0379221d70112cd1b75e0e590f4037 |
| src/hermes/components/assistant-ui/clarify/settled.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/settled.tsx | 3ea384e50b40b2798cfc78ea4af61b81985fe92cad575123be33c2a83fd683a6 | 3ea384e50b40b2798cfc78ea4af61b81985fe92cad575123be33c2a83fd683a6 |
| src/hermes/components/assistant-ui/clarify/submit-shortcut.ts | verbatim | apps/desktop/src/components/assistant-ui/clarify/submit-shortcut.ts | f1a763ed91aeae9bc5678e8710b4aa9adabcbaf4236955327260e00ab82f8033 | f1a763ed91aeae9bc5678e8710b4aa9adabcbaf4236955327260e00ab82f8033 |
| src/hermes/components/assistant-ui/clarify/undelivered-notice.tsx | verbatim | apps/desktop/src/components/assistant-ui/clarify/undelivered-notice.tsx | 12e168f32e46360ccc04f50ffac59b41a9e56bd76c0e6ab2e4b98e73d5acc2f1 | 12e168f32e46360ccc04f50ffac59b41a9e56bd76c0e6ab2e4b98e73d5acc2f1 |
| src/hermes/components/assistant-ui/clarify/use-undelivered.ts | verbatim | apps/desktop/src/components/assistant-ui/clarify/use-undelivered.ts | e69e4bd2debea0158d64e2869542349e35791ae9cfef187a4731dfac7765d982 | e69e4bd2debea0158d64e2869542349e35791ae9cfef187a4731dfac7765d982 |
| src/hermes/components/assistant-ui/connector-directory-card.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/connector-directory-card.test.tsx | c012655d264b1a529e26a32cc916f362009ae2e1482a8aa8b1c02ce9ac76191c | c012655d264b1a529e26a32cc916f362009ae2e1482a8aa8b1c02ce9ac76191c |
| src/hermes/components/assistant-ui/connector-directory-card.tsx | shim | apps/desktop/src/components/assistant-ui/connector-directory-card.tsx | c3205b314681c15e16d1d027a76b7d271e3a366c46af3eb358343d3e1bb88898 | 81725952e5bd13366b0f0906f5d44f6560cccfedb01d93ba04905da7dc131bcb |
| src/hermes/components/assistant-ui/connector-order.ts | verbatim | apps/desktop/src/components/assistant-ui/connector-order.ts | 4f6aa053a2f3a8eee729bb2ae082e6bfbb9cb889c4a62048af80fc53ac9b15f5 | 4f6aa053a2f3a8eee729bb2ae082e6bfbb9cb889c4a62048af80fc53ac9b15f5 |
| src/hermes/components/assistant-ui/connector-tool.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/connector-tool.test.tsx | 9990a1afe6ceba1c8d6d762a2c91d90bb75742d4c3ed72e002f98f6d69353152 | 9990a1afe6ceba1c8d6d762a2c91d90bb75742d4c3ed72e002f98f6d69353152 |
| src/hermes/components/assistant-ui/connector-tool.tsx | shim | apps/desktop/src/components/assistant-ui/connector-tool.tsx | bf2f54159b27523f0e6b3736502fc8a15c53a7196381af0a457eba37126ccbe0 | 9cb431ce072a8669f453fcae3e9709bfe9a566244d50ecca992b28438f2209cd |
| src/hermes/components/assistant-ui/directive-text.test.ts | verbatim | apps/desktop/src/components/assistant-ui/directive-text.test.ts | 7d29a4bfc9ff35afcdbd129cf9aaa0c50e8ea0ca090580cf23b0e0dd2cddb7e6 | 7d29a4bfc9ff35afcdbd129cf9aaa0c50e8ea0ca090580cf23b0e0dd2cddb7e6 |
| src/hermes/components/assistant-ui/directive-text.tsx | patch | apps/desktop/src/components/assistant-ui/directive-text.tsx | a45fb87126e1508cd6ddea44d871577066c41a127a00c2959c4fc8071d11627c | 7345cc3e832e075183d7f7871d6ab4b9d76379ac8ac531c80a7cdd8746912f8f |
| src/hermes/components/assistant-ui/embeds/alert.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/alert.test.tsx | bc7732b2e027cee1e397a6b8aa4bfe15bed88d056e4a4bd7939a28827dcf51b7 | bc7732b2e027cee1e397a6b8aa4bfe15bed88d056e4a4bd7939a28827dcf51b7 |
| src/hermes/components/assistant-ui/embeds/alert.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/alert.tsx | 18932a4426d0fa661e369235fe077e7592ae83a243fa97579357d60e819c79ef | 18932a4426d0fa661e369235fe077e7592ae83a243fa97579357d60e819c79ef |
| src/hermes/components/assistant-ui/embeds/embed-consent.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/embed-consent.tsx | 63688ebd64b372632fe4d1abe7a0c9d331213731cf96c52bda56021a29a943e0 | 63688ebd64b372632fe4d1abe7a0c9d331213731cf96c52bda56021a29a943e0 |
| src/hermes/components/assistant-ui/embeds/embed-size.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/embed-size.ts | 3316ea3d020af21085d2dfa996d56e954bc20c25dfba4db046d72d14e1841d53 | 3316ea3d020af21085d2dfa996d56e954bc20c25dfba4db046d72d14e1841d53 |
| src/hermes/components/assistant-ui/embeds/fail.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/fail.tsx | 36f549f775de2234ea665afe05635269cf74b3773159d57bc84513e19ccebb15 | 36f549f775de2234ea665afe05635269cf74b3773159d57bc84513e19ccebb15 |
| src/hermes/components/assistant-ui/embeds/frame-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/frame-embed.tsx | 275cad304c8c5690f60b78004f6156fd03e6bf34a44e640f08a7348caf165950 | 275cad304c8c5690f60b78004f6156fd03e6bf34a44e640f08a7348caf165950 |
| src/hermes/components/assistant-ui/embeds/index.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/index.ts | 3cf1f3b001f2d3ff916dc8f381a03b05f117dca301a868f7dbd824675ea6f6ee | 3cf1f3b001f2d3ff916dc8f381a03b05f117dca301a868f7dbd824675ea6f6ee |
| src/hermes/components/assistant-ui/embeds/listing-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/listing-embed.tsx | 7e1a4839fd1dc91ecb430ae39d900941e8881319fd52014ec3465e484de17797 | 7e1a4839fd1dc91ecb430ae39d900941e8881319fd52014ec3465e484de17797 |
| src/hermes/components/assistant-ui/embeds/mermaid-embed.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/mermaid-embed.test.tsx | 0d6ecf60021953a7ae11a4d74c0e89f703f7530ab0940d2693feb36997615997 | 0d6ecf60021953a7ae11a4d74c0e89f703f7530ab0940d2693feb36997615997 |
| src/hermes/components/assistant-ui/embeds/mermaid-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/mermaid-embed.tsx | 1739025ddebe76b3c85878d5bfadeb4d7f37c46ea410de802a61760e0ac62b0f | 1739025ddebe76b3c85878d5bfadeb4d7f37c46ea410de802a61760e0ac62b0f |
| src/hermes/components/assistant-ui/embeds/mermaid-render-cache.test.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/mermaid-render-cache.test.ts | 7c59c2765ad7929948fe177269d610e0f0e0bb409b06b1f3ea93a79c91726867 | 7c59c2765ad7929948fe177269d610e0f0e0bb409b06b1f3ea93a79c91726867 |
| src/hermes/components/assistant-ui/embeds/mermaid-render-cache.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/mermaid-render-cache.ts | 19f9bd15082b8e817c463da6483b85a18dca98e165f8045eef2b4b191d870bc8 | 19f9bd15082b8e817c463da6483b85a18dca98e165f8045eef2b4b191d870bc8 |
| src/hermes/components/assistant-ui/embeds/providers/index.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/index.ts | 93671b6d5e7e53e5ef42274229452f3424711e336ccabffe0781acd7bfe2c7a6 | 93671b6d5e7e53e5ef42274229452f3424711e336ccabffe0781acd7bfe2c7a6 |
| src/hermes/components/assistant-ui/embeds/providers/instagram.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/instagram.ts | 94c5264dc1ee2f1a871e5cfe8efe23b77655d8de3efd9f7d6d65683c40df9e48 | 94c5264dc1ee2f1a871e5cfe8efe23b77655d8de3efd9f7d6d65683c40df9e48 |
| src/hermes/components/assistant-ui/embeds/providers/maps.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/maps.ts | ac1659ce4b8ee5dfb256888dd983a0012db604cb8c506fe3f68e4b8f181a4cf4 | ac1659ce4b8ee5dfb256888dd983a0012db604cb8c506fe3f68e4b8f181a4cf4 |
| src/hermes/components/assistant-ui/embeds/providers/pinterest.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/pinterest.ts | 1de8ebc274e77739376a6d581a77b02f5930d6a1c94f1de5e2d89fb720021253 | 1de8ebc274e77739376a6d581a77b02f5930d6a1c94f1de5e2d89fb720021253 |
| src/hermes/components/assistant-ui/embeds/providers/spotify.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/spotify.ts | 98f98a28dfb6c599326203a5b5d5d2b5955b5e2b2d97f5b4c7d3e31718f53748 | 98f98a28dfb6c599326203a5b5d5d2b5955b5e2b2d97f5b4c7d3e31718f53748 |
| src/hermes/components/assistant-ui/embeds/providers/tiktok.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/tiktok.ts | eafec2f5f140d8600181b8b1f3da5ffa75f61b42a7bb6bb84124b8f8d4831a8a | eafec2f5f140d8600181b8b1f3da5ffa75f61b42a7bb6bb84124b8f8d4831a8a |
| src/hermes/components/assistant-ui/embeds/providers/twitter.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/twitter.ts | ff9ca3ed9dfa017a36c6f349b772a7a368042ff67074f84922b3fccd93b5d175 | ff9ca3ed9dfa017a36c6f349b772a7a368042ff67074f84922b3fccd93b5d175 |
| src/hermes/components/assistant-ui/embeds/providers/types.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/types.ts | 6fc9fdde7236e8da60b775c376d7178d33a518ba7cbc6a2fe810036d620db763 | 6fc9fdde7236e8da60b775c376d7178d33a518ba7cbc6a2fe810036d620db763 |
| src/hermes/components/assistant-ui/embeds/providers/vimeo.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/vimeo.ts | 440c495c1e65b4539254c41946156a4e3c322229fc63f8ebfd163e5b0df5ed04 | 440c495c1e65b4539254c41946156a4e3c322229fc63f8ebfd163e5b0df5ed04 |
| src/hermes/components/assistant-ui/embeds/providers/youtube.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/providers/youtube.ts | 17408e2684f196c73bef3bfafced0395a0833fcd677a6e5d07fad0adc8b3e5f8 | 17408e2684f196c73bef3bfafced0395a0833fcd677a6e5d07fad0adc8b3e5f8 |
| src/hermes/components/assistant-ui/embeds/registry.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/registry.tsx | b816cef9aa42319b9596b7e66dc69724e0f250f1560870f1f8327336c15ddc53 | b816cef9aa42319b9596b7e66dc69724e0f250f1560870f1f8327336c15ddc53 |
| src/hermes/components/assistant-ui/embeds/rich-boundary.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/rich-boundary.tsx | d02e6eb69f03e1906110c3af61a0a5443bf2820a15d0f5e9b1a75655df737628 | d02e6eb69f03e1906110c3af61a0a5443bf2820a15d0f5e9b1a75655df737628 |
| src/hermes/components/assistant-ui/embeds/scroll-gate.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/scroll-gate.tsx | c35c0debf2373ad0718783f4aadb0b7ddd49094571985929b06cef68a5861b31 | c35c0debf2373ad0718783f4aadb0b7ddd49094571985929b06cef68a5861b31 |
| src/hermes/components/assistant-ui/embeds/social-embed.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/social-embed.test.tsx | 1dc760ff5afc049a422d61c000d194cf1de0eeb54eaf1b6303b2c304972236ab | 1dc760ff5afc049a422d61c000d194cf1de0eeb54eaf1b6303b2c304972236ab |
| src/hermes/components/assistant-ui/embeds/social-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/social-embed.tsx | e471442964d1775069779777cc77e0e368822556b66f9d0b47500cd3b91866e0 | e471442964d1775069779777cc77e0e368822556b66f9d0b47500cd3b91866e0 |
| src/hermes/components/assistant-ui/embeds/spotify-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/spotify-embed.tsx | 7147ca68f2371f42aec868c271599d76e18f99f251e9c0625dcc308cb445cbce | 7147ca68f2371f42aec868c271599d76e18f99f251e9c0625dcc308cb445cbce |
| src/hermes/components/assistant-ui/embeds/svg-embed.tsx | verbatim | apps/desktop/src/components/assistant-ui/embeds/svg-embed.tsx | a4280c653f890f313deb2b15abbd9b2a6b53a411e7e5a6188a08c6cdf83a9630 | a4280c653f890f313deb2b15abbd9b2a6b53a411e7e5a6188a08c6cdf83a9630 |
| src/hermes/components/assistant-ui/embeds/types.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/types.ts | 47821115e8f0c828072faa2d9c4a7524506e0e3896215620afafa77f123ecf77 | 47821115e8f0c828072faa2d9c4a7524506e0e3896215620afafa77f123ecf77 |
| src/hermes/components/assistant-ui/embeds/url-embed.tsx | patch | apps/desktop/src/components/assistant-ui/embeds/url-embed.tsx | 0107ab4b99a9b0c222a78aaa3c27f5dafa7323a06a5eeeb6d384a59747e4b793 | 1009bb6cf510057a4e837a506761c08650fd322248f3aa46166723c78686a2b4 |
| src/hermes/components/assistant-ui/embeds/use-is-dark.ts | verbatim | apps/desktop/src/components/assistant-ui/embeds/use-is-dark.ts | fe256b3674612f33cfee561d9adbcc7d631864a75c9ba4cce943c77ca2fa2b83 | fe256b3674612f33cfee561d9adbcc7d631864a75c9ba4cce943c77ca2fa2b83 |
| src/hermes/components/assistant-ui/embeds/youtube-embed.tsx | patch | apps/desktop/src/components/assistant-ui/embeds/youtube-embed.tsx | 9953b00723b34e59e234230d996ba166b6660f197543087c4336d43d54024d59 | 17ad671b99486c9ebde04a48d92ea8f48a4d4324c03bb52b67b12b00f79d4ac0 |
| src/hermes/components/assistant-ui/markdown-table.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-table.tsx | 6f3dc8e53dde5f83fd7d6e2e8d8e49485d3e8824a358cdec0598b576655fea96 | 6f3dc8e53dde5f83fd7d6e2e8d8e49485d3e8824a358cdec0598b576655fea96 |
| src/hermes/components/assistant-ui/markdown-text.artifacts.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.artifacts.test.tsx | fdd1a36bc01f5f38d0699f750aa9f145aa6b375ee6c367bf8e0537468313f0e0 | fdd1a36bc01f5f38d0699f750aa9f145aa6b375ee6c367bf8e0537468313f0e0 |
| src/hermes/components/assistant-ui/markdown-text.bare-url.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.bare-url.test.tsx | 8c4c5bba50a3ab42a6d3fa27f62941942301bb692d4b07f119fef0edc8454b99 | 8c4c5bba50a3ab42a6d3fa27f62941942301bb692d4b07f119fef0edc8454b99 |
| src/hermes/components/assistant-ui/markdown-text.linklabels.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.linklabels.test.tsx | 5ae2772e43faa69fff7b9d1969da6e9a6a1c1d4635eea122eb970d034a8371f2 | 5ae2772e43faa69fff7b9d1969da6e9a6a1c1d4635eea122eb970d034a8371f2 |
| src/hermes/components/assistant-ui/markdown-text.media-md.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.media-md.test.tsx | d5dd2102ce4bf4f5c5a2ee6ec2ac7cf9e8e60c3647d3e0ceccc49862a463d4a8 | d5dd2102ce4bf4f5c5a2ee6ec2ac7cf9e8e60c3647d3e0ceccc49862a463d4a8 |
| src/hermes/components/assistant-ui/markdown-text.overflow.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.overflow.test.tsx | b61f0173e5eae7fe7f9195b98bb3e563b45b1cf7c491adb92d85a7de9855df7a | b61f0173e5eae7fe7f9195b98bb3e563b45b1cf7c491adb92d85a7de9855df7a |
| src/hermes/components/assistant-ui/markdown-text.prose-safety.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.prose-safety.test.tsx | 8713594f20ea9bc9b947e0be8d6a9e4de3415e5734364bb36ad9b34d9169fab6 | 8713594f20ea9bc9b947e0be8d6a9e4de3415e5734364bb36ad9b34d9169fab6 |
| src/hermes/components/assistant-ui/markdown-text.reasoning.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.reasoning.test.tsx | be6f1e6ea1174c1a233603734ce62faf60f379e06963ae7d77b5888aa6bd69cc | be6f1e6ea1174c1a233603734ce62faf60f379e06963ae7d77b5888aa6bd69cc |
| src/hermes/components/assistant-ui/markdown-text.session.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.session.test.tsx | 3310a4cab08dea290ec5ab641cdbe39cabde66c14eaf303e360da8b152d7d540 | 3310a4cab08dea290ec5ab641cdbe39cabde66c14eaf303e360da8b152d7d540 |
| src/hermes/components/assistant-ui/markdown-text.test.ts | verbatim | apps/desktop/src/components/assistant-ui/markdown-text.test.ts | 4b5d65f51be577a6fb1f753b5283abe5e8c3484c93585a31edd9597c8743480b | 4b5d65f51be577a6fb1f753b5283abe5e8c3484c93585a31edd9597c8743480b |
| src/hermes/components/assistant-ui/markdown-text.tsx | patch | apps/desktop/src/components/assistant-ui/markdown-text.tsx | 06ba5d057624f939f1554229c9ac1959f8dc300f407c308f5eefe3530a77a651 | 4c8933830b76a8982f49594d2cdf08923b9dfd42848439bf4d2f6dcccf8a9218 |
| src/hermes/components/assistant-ui/mcp-setup-tool.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/mcp-setup-tool.test.tsx | f24d08567992bee6d3a9a3d641001f9037d4629d257010bb114e2dc87321a199 | f24d08567992bee6d3a9a3d641001f9037d4629d257010bb114e2dc87321a199 |
| src/hermes/components/assistant-ui/mcp-setup-tool.tsx | shim | apps/desktop/src/components/assistant-ui/mcp-setup-tool.tsx | 5af388d9ab47b6077aef294f21a3bfceab6e20c0ab6478aa17eef2a5e8fbfdd3 | fa7f2afb5429c15dbb610f814a3e64ade3134c7831e484ac9f157a57d8ac0d07 |
| src/hermes/components/assistant-ui/message-render-boundary.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/message-render-boundary.test.tsx | 3dab6c340b5c470dbffbeb9d0d6135e07fad3dec928168c9948222242a0339be | 3dab6c340b5c470dbffbeb9d0d6135e07fad3dec928168c9948222242a0339be |
| src/hermes/components/assistant-ui/message-render-boundary.tsx | verbatim | apps/desktop/src/components/assistant-ui/message-render-boundary.tsx | a6c84de82bd8fab892d8dd132adf0f98c2b834ee281845d702b4f3f1464aa5de | a6c84de82bd8fab892d8dd132adf0f98c2b834ee281845d702b4f3f1464aa5de |
| src/hermes/components/assistant-ui/setup-beat/beat-cards.tsx | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/beat-cards.tsx | c54f5e70d0e3ffc441194d3803833d1fbd03ba2ff75b8786d5f0a662f1e44a1e | c54f5e70d0e3ffc441194d3803833d1fbd03ba2ff75b8786d5f0a662f1e44a1e |
| src/hermes/components/assistant-ui/setup-beat/card-type.ts | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/card-type.ts | a6ddcb85ac6bb31a9bb016f167beab92281a02f56464fdccb2f68aca1d46bbd6 | a6ddcb85ac6bb31a9bb016f167beab92281a02f56464fdccb2f68aca1d46bbd6 |
| src/hermes/components/assistant-ui/setup-beat/plugins-card.tsx | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/plugins-card.tsx | 7d76796d2c34174ee5ecca5e7619544dc98da31f22ec6b4487106360c8967849 | 7d76796d2c34174ee5ecca5e7619544dc98da31f22ec6b4487106360c8967849 |
| src/hermes/components/assistant-ui/setup-beat/receipt.tsx | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/receipt.tsx | e72dec2a110eb1613c1df3687b2c7b5da716c321ea11633265a60fd2977f70c3 | e72dec2a110eb1613c1df3687b2c7b5da716c321ea11633265a60fd2977f70c3 |
| src/hermes/components/assistant-ui/setup-beat/setup-beat-tool.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/setup-beat-tool.test.tsx | f152aabc57e010d610ba40806e68c73361ed3dafcbed6816e7987a34177b6644 | f152aabc57e010d610ba40806e68c73361ed3dafcbed6816e7987a34177b6644 |
| src/hermes/components/assistant-ui/setup-beat/setup-beat-tool.tsx | verbatim | apps/desktop/src/components/assistant-ui/setup-beat/setup-beat-tool.tsx | 714d52daf5ce6190ff7620881662af01acfd3db165ef6d08801371b1e59e809c | 714d52daf5ce6190ff7620881662af01acfd3db165ef6d08801371b1e59e809c |
| src/hermes/components/assistant-ui/test-utils.tsx | verbatim | apps/desktop/src/components/assistant-ui/test-utils.tsx | 24ef3e934394e7cc8e421efd74aa47399a566158e3c66a2e341e51657c10f6e3 | 24ef3e934394e7cc8e421efd74aa47399a566158e3c66a2e341e51657c10f6e3 |
| src/hermes/components/assistant-ui/theme-picker/accent-picker-card.tsx | shim | apps/desktop/src/components/assistant-ui/theme-picker/accent-picker-card.tsx | 006ea0eaa23e1d246cdf0c8718cde8d8926811e5f1972e34c8e6d70601790d12 | 452dc6c3f739c138bb909b0406cc365e1f10c57df66b2fc27834dd7b47a2ff75 |
| src/hermes/components/assistant-ui/theme-picker/args.ts | verbatim | apps/desktop/src/components/assistant-ui/theme-picker/args.ts | 436daa4ebafa71a40f1d9f360ad531ea719bcb38789cd2ce29d7afc6efe96899 | 436daa4ebafa71a40f1d9f360ad531ea719bcb38789cd2ce29d7afc6efe96899 |
| src/hermes/components/assistant-ui/theme-picker/layout-picker-card.tsx | shim | apps/desktop/src/components/assistant-ui/theme-picker/layout-picker-card.tsx | d07dbf41ac9e10a42241d806e30880eb9d697c336647f2e746179837c96c2332 | afd6c4705ab2bcfe397412ece7e497f9d6ea098155741f9336d3786cc0dedfa6 |
| src/hermes/components/assistant-ui/theme-picker/theme-tool.tsx | shim | apps/desktop/src/components/assistant-ui/theme-picker/theme-tool.tsx | f2bed2ad37b46341f8a34769ef1ff69763601d9690d9bf18230c4f295f657355 | 163efc2129eeed6a66d89dec3e3422b82457ccc59a34429bd5a20dc0e21885fc |
| src/hermes/components/assistant-ui/thread/agent-delivery.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/agent-delivery.test.tsx | 06ff3e4e8a7eb739241b83c8c67b9ed06040c54846b7c6e4d337f3234405bf9b | 06ff3e4e8a7eb739241b83c8c67b9ed06040c54846b7c6e4d337f3234405bf9b |
| src/hermes/components/assistant-ui/thread/agent-delivery.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/agent-delivery.tsx | 17bcbaee1592b7c9743ae77c65585578ceedf456548bdb16c1650cbc6d4517c6 | 17bcbaee1592b7c9743ae77c65585578ceedf456548bdb16c1650cbc6d4517c6 |
| src/hermes/components/assistant-ui/thread/assistant-message.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/assistant-message.test.tsx | f46ef6ac980642078b3dc3a7eac2ab01a19ab5e9fa6b105918a853ef6a44fc87 | f46ef6ac980642078b3dc3a7eac2ab01a19ab5e9fa6b105918a853ef6a44fc87 |
| src/hermes/components/assistant-ui/thread/assistant-message.tsx | patch | apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx | f996a66066e428bf66d5d8e4d1ee69e84bd0ea192b7f5534aee33c87b8a7a4c5 | 4049a91a715342aa212356806c51d790d9d28a611eccc8d99a4ef1c764c2b813 |
| src/hermes/components/assistant-ui/thread/background-resume.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/background-resume.test.tsx | ef47ec04dee01aa2c9c59590e74327da70e0544b62e057c5fbd883db7581af29 | ef47ec04dee01aa2c9c59590e74327da70e0544b62e057c5fbd883db7581af29 |
| src/hermes/components/assistant-ui/thread/changed-files-card.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/changed-files-card.tsx | b8b923268608d8f2a73f8d9a8f0b101b96c14c46fc876f0787af92df5ca98715 | b8b923268608d8f2a73f8d9a8f0b101b96c14c46fc876f0787af92df5ca98715 |
| src/hermes/components/assistant-ui/thread/changed-files.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/changed-files.ts | fc319aa2ac197641a16bb86d80ef83a11a0d2e0c3ba51a1ce89c7a977ad17c05 | fc319aa2ac197641a16bb86d80ef83a11a0d2e0c3ba51a1ce89c7a977ad17c05 |
| src/hermes/components/assistant-ui/thread/content.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/content.test.ts | d12036f68ee54e4a93509d5dfb3578308be209fb47486e77cf25734ec1dbee11 | d12036f68ee54e4a93509d5dfb3578308be209fb47486e77cf25734ec1dbee11 |
| src/hermes/components/assistant-ui/thread/content.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/content.ts | 6adcb3fa56d487da7c2e80ca7dfa42a37c82cd2eb4fef6019079bed5d451d98e | 6adcb3fa56d487da7c2e80ca7dfa42a37c82cd2eb4fef6019079bed5d451d98e |
| src/hermes/components/assistant-ui/thread/double-click-reaction.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/double-click-reaction.test.tsx | fb3d365a10d6b68dfd211c604f4f412cdaec82a580aa7f0dfa70d26c0e36fb3b | fb3d365a10d6b68dfd211c604f4f412cdaec82a580aa7f0dfa70d26c0e36fb3b |
| src/hermes/components/assistant-ui/thread/edit-context.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/edit-context.test.tsx | 86a4c1eb349a61ffad46ac593464f357977c43aa9e93e9c48a22b16bbc1bcb67 | 86a4c1eb349a61ffad46ac593464f357977c43aa9e93e9c48a22b16bbc1bcb67 |
| src/hermes/components/assistant-ui/thread/index.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/index.test.tsx | 1a951f1980ad69778341c8dc832bbe62c4fe55599d0cee22276e081b47521043 | 1a951f1980ad69778341c8dc832bbe62c4fe55599d0cee22276e081b47521043 |
| src/hermes/components/assistant-ui/thread/index.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/index.tsx | 4e1589154405f115f503bba8b0545ff83ba5797d2626f6a3ffe59af5912dbdce | 4e1589154405f115f503bba8b0545ff83ba5797d2626f6a3ffe59af5912dbdce |
| src/hermes/components/assistant-ui/thread/list-auto-show-earlier.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/list-auto-show-earlier.test.tsx | 4c56431b8f59d5b60e34724395962cad127231e0bd503624848bb8ef03b981f4 | 4c56431b8f59d5b60e34724395962cad127231e0bd503624848bb8ef03b981f4 |
| src/hermes/components/assistant-ui/thread/list-restore-composer-resize.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/list-restore-composer-resize.test.tsx | 08577295480569d5aab1e3b98b30c58c8c06a1857967cf81c6dafcca1d9463e9 | 08577295480569d5aab1e3b98b30c58c8c06a1857967cf81c6dafcca1d9463e9 |
| src/hermes/components/assistant-ui/thread/list-session-scroll.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/list-session-scroll.test.tsx | b628d4527f90e6ed72d4f568ff481d9e6627f12712ee1d3c83a77acdbbc7bdfa | b628d4527f90e6ed72d4f568ff481d9e6627f12712ee1d3c83a77acdbbc7bdfa |
| src/hermes/components/assistant-ui/thread/list.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/list.test.ts | d68b69594d7d04e0316aab48998c766bfa4d5713d3dea012754c431eb4117201 | d68b69594d7d04e0316aab48998c766bfa4d5713d3dea012754c431eb4117201 |
| src/hermes/components/assistant-ui/thread/list.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/list.tsx | a11b128d7c9a2790c7de71e7fefbccf1e18439db4fcc9938ea816a3310bb8fcf | a11b128d7c9a2790c7de71e7fefbccf1e18439db4fcc9938ea816a3310bb8fcf |
| src/hermes/components/assistant-ui/thread/message-hover-time.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/message-hover-time.tsx | 5f120c3e4af2ddde9b30ef4cf4f67d6653f5dd8b89f4d575b687b23ef402b9a6 | 5f120c3e4af2ddde9b30ef4cf4f67d6653f5dd8b89f4d575b687b23ef402b9a6 |
| src/hermes/components/assistant-ui/thread/message-parts-connectors.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/message-parts-connectors.test.tsx | 8e6299b8fdf5759706345819da6b7be80c4366d20ca3e56302f8a50534d2e04b | 8e6299b8fdf5759706345819da6b7be80c4366d20ca3e56302f8a50534d2e04b |
| src/hermes/components/assistant-ui/thread/message-parts.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/message-parts.tsx | 196535328a26fc53543f164ba4173f2d56d1f295d3666097b817e7655a8fb613 | 196535328a26fc53543f164ba4173f2d56d1f295d3666097b817e7655a8fb613 |
| src/hermes/components/assistant-ui/thread/message-reactions.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/message-reactions.tsx | 5b6548e5decc202e4aa431d2667d5313efe09b67cca732e4f5882e6ed54f0505 | 5b6548e5decc202e4aa431d2667d5313efe09b67cca732e4f5882e6ed54f0505 |
| src/hermes/components/assistant-ui/thread/response-group.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/response-group.test.tsx | 1563a4e528f5020907b47f9a2620d99a5bec170885bc5e397d0bbb81857d9a9f | 1563a4e528f5020907b47f9a2620d99a5bec170885bc5e397d0bbb81857d9a9f |
| src/hermes/components/assistant-ui/thread/response-group.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/response-group.tsx | 83b11fe725e5f48c322ebfe7a0d7fdc41e2bca9c97ea8779bb2bddf1af9b8f41 | 83b11fe725e5f48c322ebfe7a0d7fdc41e2bca9c97ea8779bb2bddf1af9b8f41 |
| src/hermes/components/assistant-ui/thread/session-switching.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/session-switching.ts | 71fe973539decc77e1a4877803722d38f3a71470c9d24f0331733b63982cfa86 | 71fe973539decc77e1a4877803722d38f3a71470c9d24f0331733b63982cfa86 |
| src/hermes/components/assistant-ui/thread/should-auto-show-earlier.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/should-auto-show-earlier.test.ts | 2991363cdcc28a5049ba0e879d9052d38936caf0c35ba67a84c845d47d01a0a9 | 2991363cdcc28a5049ba0e879d9052d38936caf0c35ba67a84c845d47d01a0a9 |
| src/hermes/components/assistant-ui/thread/status-invalidation-scope.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/status-invalidation-scope.test.tsx | fe07670fbeda9b5da01783da62147837cd5127393cfc80a9f8369ad385611d37 | fe07670fbeda9b5da01783da62147837cd5127393cfc80a9f8369ad385611d37 |
| src/hermes/components/assistant-ui/thread/status-tail-only.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/status-tail-only.test.tsx | 0aed1e25443e4cb382685f35de8714be3e77cfc441d7c27a62ced08a6ffe0d61 | 0aed1e25443e4cb382685f35de8714be3e77cfc441d7c27a62ced08a6ffe0d61 |
| src/hermes/components/assistant-ui/thread/status.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/status.test.tsx | 751d96968c63acaab9fe0d4811b411c4dba124e368926cab49c601e4a7c93086 | 751d96968c63acaab9fe0d4811b411c4dba124e368926cab49c601e4a7c93086 |
| src/hermes/components/assistant-ui/thread/status.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/status.tsx | 432d3de5bf9b7bc03e49215b2758d5694287cb1cb96ac468337a8246c456f43a | 432d3de5bf9b7bc03e49215b2758d5694287cb1cb96ac468337a8246c456f43a |
| src/hermes/components/assistant-ui/thread/stream-settled-renders.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/stream-settled-renders.test.tsx | 655d58208daabda05e43baa2dc0ce29760085e232f987c32db89bdc25e6414dc | 655d58208daabda05e43baa2dc0ce29760085e232f987c32db89bdc25e6414dc |
| src/hermes/components/assistant-ui/thread/system-message.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/system-message.test.tsx | b6ae7d48d99873b278023fd4daed5ea99a91ed9a28f8ae1fdb7d8ad149a78a2b | b6ae7d48d99873b278023fd4daed5ea99a91ed9a28f8ae1fdb7d8ad149a78a2b |
| src/hermes/components/assistant-ui/thread/system-message.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/system-message.tsx | 2ab6dbddb2bc5273767d4bd975996128015f372b4e260434911a7be8a50b5a77 | 2ab6dbddb2bc5273767d4bd975996128015f372b4e260434911a7be8a50b5a77 |
| src/hermes/components/assistant-ui/thread/text-direction.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/text-direction.test.tsx | a03388f504c68b4ab3b33a93fa15f759f5add4840d3129a605bcaf54d206a151 | a03388f504c68b4ab3b33a93fa15f759f5add4840d3129a605bcaf54d206a151 |
| src/hermes/components/assistant-ui/thread/thread-message-index.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/thread-message-index.test.ts | 346cef3e029ed8987e7d41e7ee0627734e218eb01347af5a38668bb7f6424a32 | 346cef3e029ed8987e7d41e7ee0627734e218eb01347af5a38668bb7f6424a32 |
| src/hermes/components/assistant-ui/thread/thread-message-index.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/thread-message-index.ts | ff21706afce9ba37fe9b20275871668c820d6e41d9a9c9751b7f267133cb946f | ff21706afce9ba37fe9b20275871668c820d6e41d9a9c9751b7f267133cb946f |
| src/hermes/components/assistant-ui/thread/timeline-data.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-data.test.ts | 7b7205eac81d9dd49a63ba5b75df7f183f2e023a729f51385d3097820cc9bdcf | 7b7205eac81d9dd49a63ba5b75df7f183f2e023a729f51385d3097820cc9bdcf |
| src/hermes/components/assistant-ui/thread/timeline-data.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-data.ts | 4e0b7325af565c99a28f727d09b66ed94829d48cd1b845ab274512b6740166c7 | 4e0b7325af565c99a28f727d09b66ed94829d48cd1b845ab274512b6740166c7 |
| src/hermes/components/assistant-ui/thread/timeline-idle.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-idle.test.tsx | f7032cafcf8857f426c256c5bcb21376af7cfc707263dea4347e3aa91866473c | f7032cafcf8857f426c256c5bcb21376af7cfc707263dea4347e3aa91866473c |
| src/hermes/components/assistant-ui/thread/timeline-index.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-index.test.ts | 2759a05ef3435febb6c5c322e7033a3175ce2cb8d4da72b3c723230d5667e644 | 2759a05ef3435febb6c5c322e7033a3175ce2cb8d4da72b3c723230d5667e644 |
| src/hermes/components/assistant-ui/thread/timeline-index.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-index.ts | 08e9b561c59185feae18cb14ff7b555442763f4aa68d12ef5e27edc87140b4b2 | 08e9b561c59185feae18cb14ff7b555442763f4aa68d12ef5e27edc87140b4b2 |
| src/hermes/components/assistant-ui/thread/timeline-position.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-position.test.tsx | 4e48f3d67a21eeaa18d17423d030095097767bb59503a3816f0394b8eca66a09 | 4e48f3d67a21eeaa18d17423d030095097767bb59503a3816f0394b8eca66a09 |
| src/hermes/components/assistant-ui/thread/timeline-position.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-position.ts | 3a3053cd37b1417d17d8cb10613fcbde7d87971ccdf912b0f8198f699be5ab7d | 3a3053cd37b1417d17d8cb10613fcbde7d87971ccdf912b0f8198f699be5ab7d |
| src/hermes/components/assistant-ui/thread/timeline-rail.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-rail.test.tsx | 426c6b8f600f2b924ab837315546724587c49e517308914074616913678a45fa | 426c6b8f600f2b924ab837315546724587c49e517308914074616913678a45fa |
| src/hermes/components/assistant-ui/thread/timeline-rail.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-rail.tsx | 6f104cd714617622a569be35e4efb801ed6879889c908489316889443ff3cce7 | 6f104cd714617622a569be35e4efb801ed6879889c908489316889443ff3cce7 |
| src/hermes/components/assistant-ui/thread/timeline-timestamp.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-timestamp.test.tsx | 2be616180bba577e4412546f111a696ac6f07b0fd762e0ec8d5acae6b80da213 | 2be616180bba577e4412546f111a696ac6f07b0fd762e0ec8d5acae6b80da213 |
| src/hermes/components/assistant-ui/thread/timeline-timestamp.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline-timestamp.tsx | 5e2e1710279d0e6b70bf0ecb9c2f1210ce7958671e5d805cd08ef73a320d7f06 | 5e2e1710279d0e6b70bf0ecb9c2f1210ce7958671e5d805cd08ef73a320d7f06 |
| src/hermes/components/assistant-ui/thread/timeline.css | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline.css | c68d571e89ba477613d6097014e28b7b0a85299a6a9f360c975a2e7b3779fc98 | c68d571e89ba477613d6097014e28b7b0a85299a6a9f360c975a2e7b3779fc98 |
| src/hermes/components/assistant-ui/thread/timeline.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline.test.ts | 0e3fa3fb6c75c61704d5c9ecd173fffe33a761394fafb76d9e502e15af910bd4 | 0e3fa3fb6c75c61704d5c9ecd173fffe33a761394fafb76d9e502e15af910bd4 |
| src/hermes/components/assistant-ui/thread/timeline.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/timeline.tsx | 723b1b9773a59fd2590e252ec9975a3b19abb3a3564162623f9c7ecb88350831 | 723b1b9773a59fd2590e252ec9975a3b19abb3a3564162623f9c7ecb88350831 |
| src/hermes/components/assistant-ui/thread/timestamp.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timestamp.test.ts | 99aa928822c9b3b192855ed8f70a2498c9d286409a6d61aa11727647708704c8 | 99aa928822c9b3b192855ed8f70a2498c9d286409a6d61aa11727647708704c8 |
| src/hermes/components/assistant-ui/thread/timestamp.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/timestamp.ts | 2c210557bc3d072b506bcb512d15e0c0de29b14f3c034291f81d74b51dbaa2f4 | 2c210557bc3d072b506bcb512d15e0c0de29b14f3c034291f81d74b51dbaa2f4 |
| src/hermes/components/assistant-ui/thread/transcript-window.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/transcript-window.test.ts | bfbacf49a3407d5e18b65d2bcd2d1a53e8709d571e76dcec08585c052e639d8f | bfbacf49a3407d5e18b65d2bcd2d1a53e8709d571e76dcec08585c052e639d8f |
| src/hermes/components/assistant-ui/thread/transcript-window.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/transcript-window.tsx | 44738dfebf108378a47693058a6abeef92aba87efc9e81270a401244ae2fb3af | 44738dfebf108378a47693058a6abeef92aba87efc9e81270a401244ae2fb3af |
| src/hermes/components/assistant-ui/thread/turn-activity.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/turn-activity.test.ts | 884d00989da66d49753b20d8cdfef3e9ae2e97705a039e56d3e4f9c0770c9765 | 884d00989da66d49753b20d8cdfef3e9ae2e97705a039e56d3e4f9c0770c9765 |
| src/hermes/components/assistant-ui/thread/turn-activity.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/turn-activity.ts | 3228e1639ffa4ba74424dcddf5e4ac06335dda113b78a80e71a24da4711b2772 | 3228e1639ffa4ba74424dcddf5e4ac06335dda113b78a80e71a24da4711b2772 |
| src/hermes/components/assistant-ui/thread/turn-gap-indicator.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/turn-gap-indicator.test.tsx | e20a1a738ce3cddf16dd45cc35aa4a910b9e7a854aea7f5f0f64d805c17bf8de | e20a1a738ce3cddf16dd45cc35aa4a910b9e7a854aea7f5f0f64d805c17bf8de |
| src/hermes/components/assistant-ui/thread/types.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/types.ts | 106c20cc410f828e98d44d9c1c6a9783dc0df3859786ecceea16787459e70726 | 106c20cc410f828e98d44d9c1c6a9783dc0df3859786ecceea16787459e70726 |
| src/hermes/components/assistant-ui/thread/use-message-reactions.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-message-reactions.ts | cd02fa42b107f494cf7b8602c1cac47ee7d3bfe67925af28b1edfa04573e7e15 | cd02fa42b107f494cf7b8602c1cac47ee7d3bfe67925af28b1edfa04573e7e15 |
| src/hermes/components/assistant-ui/thread/use-messages-below-perf.test.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-messages-below-perf.test.ts | 343d8ed33e4e26ba5eb276d9aa0dd9e7727580c4c3bc5a1b9b84fd487e81bae2 | 343d8ed33e4e26ba5eb276d9aa0dd9e7727580c4c3bc5a1b9b84fd487e81bae2 |
| src/hermes/components/assistant-ui/thread/use-messages-below.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/use-messages-below.test.tsx | ce7e450df47e0b39924fb6a4653863922642ca9f8ab023fe05e759dd0689b948 | ce7e450df47e0b39924fb6a4653863922642ca9f8ab023fe05e759dd0689b948 |
| src/hermes/components/assistant-ui/thread/use-messages-below.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-messages-below.ts | 8b7d55a435c156c2edbf24094155528441e78904874b0aaab43767ffea0c14cf | 8b7d55a435c156c2edbf24094155528441e78904874b0aaab43767ffea0c14cf |
| src/hermes/components/assistant-ui/thread/use-sticky-prompt-clip.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/use-sticky-prompt-clip.test.tsx | 76f3f60fcc780353606a05c2a736e5f7de60455e6c55cceed60afdb8ceb77e84 | 76f3f60fcc780353606a05c2a736e5f7de60455e6c55cceed60afdb8ceb77e84 |
| src/hermes/components/assistant-ui/thread/use-sticky-prompt-clip.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-sticky-prompt-clip.ts | 1a2d5f2a4466449c0f1955c29b6529bcf5136fb778d3fae33775b8e6c03e9b79 | 1a2d5f2a4466449c0f1955c29b6529bcf5136fb778d3fae33775b8e6c03e9b79 |
| src/hermes/components/assistant-ui/thread/use-timeline-history.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/use-timeline-history.test.tsx | 9370e8de83109bbc4fb0569d530dbb831394a78e8e112c8c21bc8efe20047d7a | 9370e8de83109bbc4fb0569d530dbb831394a78e8e112c8c21bc8efe20047d7a |
| src/hermes/components/assistant-ui/thread/use-timeline-history.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-timeline-history.ts | 26d7d4eee402f9aa50cdd50ac845bfd31e156ea323b45b15547a980a16d65775 | 26d7d4eee402f9aa50cdd50ac845bfd31e156ea323b45b15547a980a16d65775 |
| src/hermes/components/assistant-ui/thread/use-timeline-reveal.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/use-timeline-reveal.test.tsx | 41a889c1ac3895b001bc731cba0060407234e2304fc236a68c0bced07c6b73f1 | 41a889c1ac3895b001bc731cba0060407234e2304fc236a68c0bced07c6b73f1 |
| src/hermes/components/assistant-ui/thread/use-timeline-reveal.ts | verbatim | apps/desktop/src/components/assistant-ui/thread/use-timeline-reveal.ts | b03fd8fca39e825d7ee198b505006f8e4aa61e53dbc69e13c69dc7753aa5d0d9 | b03fd8fca39e825d7ee198b505006f8e4aa61e53dbc69e13c69dc7753aa5d0d9 |
| src/hermes/components/assistant-ui/thread/user-edit-composer-owner-route.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-edit-composer-owner-route.test.tsx | 4d1426352c98d20a8065c8e5d4d916156d63b6d9c1b5994d2130d49aa4081d4a | 4d1426352c98d20a8065c8e5d4d916156d63b6d9c1b5994d2130d49aa4081d4a |
| src/hermes/components/assistant-ui/thread/user-edit-composer.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-edit-composer.tsx | d7aa1b45468d807307a56cff11d23354bce377110c607399be15d68faead1b8a | d7aa1b45468d807307a56cff11d23354bce377110c607399be15d68faead1b8a |
| src/hermes/components/assistant-ui/thread/user-message-edit-gesture.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-edit-gesture.test.tsx | 5d4b0bf1be13747dee8f68a075a1d9e0a40abb9b674d5ca6fee4331121b0a7cc | 5d4b0bf1be13747dee8f68a075a1d9e0a40abb9b674d5ca6fee4331121b0a7cc |
| src/hermes/components/assistant-ui/thread/user-message-edit-shared-composer.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-edit-shared-composer.test.tsx | a276b88a10fa8f9fae919035d448fdf7c256629cd31b4f0025384ca646759d9a | a276b88a10fa8f9fae919035d448fdf7c256629cd31b4f0025384ca646759d9a |
| src/hermes/components/assistant-ui/thread/user-message-edit-undo-cut-drag.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-edit-undo-cut-drag.test.tsx | ce6d23abbb05472f72123187de4a74a236d7e196ddbf5bf9267c89f1c733dff5 | ce6d23abbb05472f72123187de4a74a236d7e196ddbf5bf9267c89f1c733dff5 |
| src/hermes/components/assistant-ui/thread/user-message-edit.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-edit.test.tsx | 11150e68905764199bead112822effc32e6ef322e2ce2c037c7b0fcd5aac2490 | 11150e68905764199bead112822effc32e6ef322e2ce2c037c7b0fcd5aac2490 |
| src/hermes/components/assistant-ui/thread/user-message-text.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-text.test.tsx | b1ae146cb65066a6c509f95c47d8639a91c6b5e9587192fcb864a9deb023b7d7 | b1ae146cb65066a6c509f95c47d8639a91c6b5e9587192fcb864a9deb023b7d7 |
| src/hermes/components/assistant-ui/thread/user-message-text.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message-text.tsx | 68fca985f60dbfb0944dd70d75cf32267f71a5793ef8b78fe8260a0aba01dd9b | 68fca985f60dbfb0944dd70d75cf32267f71a5793ef8b78fe8260a0aba01dd9b |
| src/hermes/components/assistant-ui/thread/user-message.tsx | verbatim | apps/desktop/src/components/assistant-ui/thread/user-message.tsx | 9e6d5a3c42ad78f87dd2ad362a72c09b5d13fcf4c991133f69fdc438638d425c | 9e6d5a3c42ad78f87dd2ad362a72c09b5d13fcf4c991133f69fdc438638d425c |
| src/hermes/components/assistant-ui/tool/approval-activity.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/approval-activity.ts | 6568b43f831c71c4605689e2a6d73854357cd77428b534794909c5458d559500 | 6568b43f831c71c4605689e2a6d73854357cd77428b534794909c5458d559500 |
| src/hermes/components/assistant-ui/tool/approval.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/approval.test.tsx | c84dc76f12e28fb313319cf4777774b99db2ffc5ed29a4ee638eb2d31db63921 | c84dc76f12e28fb313319cf4777774b99db2ffc5ed29a4ee638eb2d31db63921 |
| src/hermes/components/assistant-ui/tool/approval.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/approval.tsx | 105f7da6dfb089f69e2ff7bb7897a246ecb8c7bb0501deb750196a4cac0dcedd | 105f7da6dfb089f69e2ff7bb7897a246ecb8c7bb0501deb750196a4cac0dcedd |
| src/hermes/components/assistant-ui/tool/delegate-model.test.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/delegate-model.test.ts | 21e40a4dab3f36c89f6c5f62ebf1cfe3d0b298d257e69de0ed5afb86d79b1e41 | 21e40a4dab3f36c89f6c5f62ebf1cfe3d0b298d257e69de0ed5afb86d79b1e41 |
| src/hermes/components/assistant-ui/tool/delegate-model.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/delegate-model.ts | 132f057d166a476b63715a7134229b98825dfda9e60b25956063c399bf0bcc2e | 132f057d166a476b63715a7134229b98825dfda9e60b25956063c399bf0bcc2e |
| src/hermes/components/assistant-ui/tool/delegate.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/delegate.test.tsx | b1c6745f4d226bc7f976240409f4149592c335a12df2466a0587c676552d396f | b1c6745f4d226bc7f976240409f4149592c335a12df2466a0587c676552d396f |
| src/hermes/components/assistant-ui/tool/delegate.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/delegate.tsx | 3ca6631933fb84fa8bf83bb2121f67da04e4e06a34929617f863379a3c1903a8 | 3ca6631933fb84fa8bf83bb2121f67da04e4e06a34929617f863379a3c1903a8 |
| src/hermes/components/assistant-ui/tool/fallback-model.test.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-model.test.ts | d720a9645302d19903690ab78a8a6fd95ab5918594ebc08e52bfcb58e9ede7ca | d720a9645302d19903690ab78a8a6fd95ab5918594ebc08e52bfcb58e9ede7ca |
| src/hermes/components/assistant-ui/tool/fallback-model/format.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-model/format.ts | ec5b9bf624ecd56659e78b20a52b13ca10cc6beb7c38a6452a556ad7420a5667 | ec5b9bf624ecd56659e78b20a52b13ca10cc6beb7c38a6452a556ad7420a5667 |
| src/hermes/components/assistant-ui/tool/fallback-model/index.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-model/index.ts | db113f2140547060e2299794f87873a108ee5f0f21f4692d84ea1a7e50b3fb61 | db113f2140547060e2299794f87873a108ee5f0f21f4692d84ea1a7e50b3fb61 |
| src/hermes/components/assistant-ui/tool/fallback-model/targets.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-model/targets.ts | e2dd894142b30702b62bab99c3dd92c08b7f76057745434f3d60c39629633fc4 | e2dd894142b30702b62bab99c3dd92c08b7f76057745434f3d60c39629633fc4 |
| src/hermes/components/assistant-ui/tool/fallback-model/types.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-model/types.ts | ccf85fafb3a0363ce221804664077ec6bba0bfb099e0e707b2d19c44d7efb5f5 | ccf85fafb3a0363ce221804664077ec6bba0bfb099e0e707b2d19c44d7efb5f5 |
| src/hermes/components/assistant-ui/tool/fallback-preview-scope.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback-preview-scope.test.tsx | 087dbe3e45ea2d123fb9f9b208d3818fab9f8a89f4d0569a507090628775d83b | 087dbe3e45ea2d123fb9f9b208d3818fab9f8a89f4d0569a507090628775d83b |
| src/hermes/components/assistant-ui/tool/fallback.test.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback.test.ts | 7cc0c71c181e68b9d1f6a092f524ab73d7049b35f1f1fde9a776382ad64910c8 | 7cc0c71c181e68b9d1f6a092f524ab73d7049b35f1f1fde9a776382ad64910c8 |
| src/hermes/components/assistant-ui/tool/fallback.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/fallback.tsx | acc9999558a6caee84aba4ea7326f932c651727e23dc96f1d8ec4256562265a4 | acc9999558a6caee84aba4ea7326f932c651727e23dc96f1d8ec4256562265a4 |
| src/hermes/components/assistant-ui/tool/inline-edit-persistence.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/inline-edit-persistence.test.tsx | 42b8b8379f028b64ca06f933ea4c0c9c82e8f89215ce06ae702bd035cd4457f9 | 42b8b8379f028b64ca06f933ea4c0c9c82e8f89215ce06ae702bd035cd4457f9 |
| src/hermes/components/assistant-ui/tool/run-summary.test.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/run-summary.test.ts | 811e22886a35a2f52f330e9d7ded879e038e8b740dc3c845e9e7d5b0a71785dd | 811e22886a35a2f52f330e9d7ded879e038e8b740dc3c845e9e7d5b0a71785dd |
| src/hermes/components/assistant-ui/tool/run-summary.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/run-summary.ts | b555a3736eb3637824e7784448d85842b6727de225b8460d97f014a298996c3b | b555a3736eb3637824e7784448d85842b6727de225b8460d97f014a298996c3b |
| src/hermes/components/assistant-ui/tool/run-ticker.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/run-ticker.tsx | 23ecb048bf3acc4397257c10a5766fc78d0711384c92e35fddc68a0f9ff34799 | 23ecb048bf3acc4397257c10a5766fc78d0711384c92e35fddc68a0f9ff34799 |
| src/hermes/components/assistant-ui/tool/skill-activity.test.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/skill-activity.test.ts | e79f33472615459c48e4191d71458d7a1adcabe7a1aad97bea4b58735b6fad73 | e79f33472615459c48e4191d71458d7a1adcabe7a1aad97bea4b58735b6fad73 |
| src/hermes/components/assistant-ui/tool/skill-activity.ts | verbatim | apps/desktop/src/components/assistant-ui/tool/skill-activity.ts | 72a11eabe5ff40ece353464f206323b15389f94f1c1724c8d808a99bcf6545dc | 72a11eabe5ff40ece353464f206323b15389f94f1c1724c8d808a99bcf6545dc |
| src/hermes/components/assistant-ui/tool/tool-feed-chrome.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/tool-feed-chrome.test.tsx | 6f22c4b8e7b72a36e185114b60d80cf144e2da5b06f843571bde15f3dc1c088a | 6f22c4b8e7b72a36e185114b60d80cf144e2da5b06f843571bde15f3dc1c088a |
| src/hermes/components/assistant-ui/tool/tool-group.test.tsx | verbatim | apps/desktop/src/components/assistant-ui/tool/tool-group.test.tsx | 9baaf1771cbad6007fb28252ebb26011b46be60cac236109de0f2fd6bdbcb674 | 9baaf1771cbad6007fb28252ebb26011b46be60cac236109de0f2fd6bdbcb674 |
| src/hermes/components/assistant-ui/tooltip-icon-button.tsx | verbatim | apps/desktop/src/components/assistant-ui/tooltip-icon-button.tsx | 2bce424a0de9a914c4908d9037e739682bb92075919f8f7b58f94be6ce3ab218 | 2bce424a0de9a914c4908d9037e739682bb92075919f8f7b58f94be6ce3ab218 |
| src/hermes/components/assistant-ui/transcript-directive.tsx | patch | apps/desktop/src/components/assistant-ui/transcript-directive.tsx | 54ad94359f84bd9c296784c613a66fc9c9ef90a612f689f29c8654b82349a056 | 2e2f8aeff30b461dab2b47ac216ad242081e973ef2f426d62c1d12ca64bf3212 |
| src/hermes/components/assistant-ui/typed-answer.tsx | verbatim | apps/desktop/src/components/assistant-ui/typed-answer.tsx | 7152a0b4ae652258b62f0117017102821d5faadf31ef116f6bac86e269408fcf | 7152a0b4ae652258b62f0117017102821d5faadf31ef116f6bac86e269408fcf |
| src/hermes/components/assistant-ui/use-connection-owner.ts | verbatim | apps/desktop/src/components/assistant-ui/use-connection-owner.ts | 8f6a9c7c07363470cadf5148dd82df9885eafc4fd1153a1f9f9baec19f476e18 | 8f6a9c7c07363470cadf5148dd82df9885eafc4fd1153a1f9f9baec19f476e18 |
| src/hermes/components/billing-banner.tsx | verbatim | apps/desktop/src/components/billing-banner.tsx | 0e1a02d45a62005aa55b13cd2dff8deabe2ba1e2bf9dfbc61c00756442e1bd11 | 0e1a02d45a62005aa55b13cd2dff8deabe2ba1e2bf9dfbc61c00756442e1bd11 |
| src/hermes/components/chat/activity-timer-text.tsx | verbatim | apps/desktop/src/components/chat/activity-timer-text.tsx | a2b6a23c43d23ad0b5638b746a209f6fd83abaa39e7b2ebbc2210f745b2c6684 | a2b6a23c43d23ad0b5638b746a209f6fd83abaa39e7b2ebbc2210f745b2c6684 |
| src/hermes/components/chat/activity-timer.test.tsx | verbatim | apps/desktop/src/components/chat/activity-timer.test.tsx | 19d0484f438d634f212b06db8184d7ac8c402dd2e86dcffdbfb88c87da2ea646 | 19d0484f438d634f212b06db8184d7ac8c402dd2e86dcffdbfb88c87da2ea646 |
| src/hermes/components/chat/activity-timer.ts | verbatim | apps/desktop/src/components/chat/activity-timer.ts | f29360613a171de3215c2f07a843639932f8ce5b8dd2e8a738303f4cce9a2e2e | f29360613a171de3215c2f07a843639932f8ce5b8dd2e8a738303f4cce9a2e2e |
| src/hermes/components/chat/code-card.tsx | verbatim | apps/desktop/src/components/chat/code-card.tsx | d6829161de337d84d041dcc92c3c4b992e95a515471cf9ac3bad0e3211a7c80c | d6829161de337d84d041dcc92c3c4b992e95a515471cf9ac3bad0e3211a7c80c |
| src/hermes/components/chat/compact-markdown.overflow.test.tsx | verbatim | apps/desktop/src/components/chat/compact-markdown.overflow.test.tsx | 13d46f100a3f086dc251436446f7f45d435ea700fbfb2c8e33666231d9cebe1b | 13d46f100a3f086dc251436446f7f45d435ea700fbfb2c8e33666231d9cebe1b |
| src/hermes/components/chat/compact-markdown.tsx | verbatim | apps/desktop/src/components/chat/compact-markdown.tsx | 00c00d6e57289bc0ab7d828872cf75af5d946d56fa40086ac18052dfa0eaf34b | 00c00d6e57289bc0ab7d828872cf75af5d946d56fa40086ac18052dfa0eaf34b |
| src/hermes/components/chat/composer-dock.ts | verbatim | apps/desktop/src/components/chat/composer-dock.ts | 6f5f17c234389a872423bbe8c567616de0a8f2872823c3725bc97e3872da53be | 6f5f17c234389a872423bbe8c567616de0a8f2872823c3725bc97e3872da53be |
| src/hermes/components/chat/diff-lines.test.tsx | verbatim | apps/desktop/src/components/chat/diff-lines.test.tsx | f06a69308b5cb517a175356d2dd4b57404c38b3862a3fbdda9cf7d487c9556e7 | f06a69308b5cb517a175356d2dd4b57404c38b3862a3fbdda9cf7d487c9556e7 |
| src/hermes/components/chat/diff-lines.tsx | verbatim | apps/desktop/src/components/chat/diff-lines.tsx | 85058b77236b5ee3bacd796ab5db7a115a4e2d5ecd957e48ecda441147f49011 | 85058b77236b5ee3bacd796ab5db7a115a4e2d5ecd957e48ecda441147f49011 |
| src/hermes/components/chat/disclosure-row.tsx | verbatim | apps/desktop/src/components/chat/disclosure-row.tsx | 3055f85ec8f9e89b31f6e9f31064b84e12ecfc84e39b91ddcc4f64606064f0e0 | 3055f85ec8f9e89b31f6e9f31064b84e12ecfc84e39b91ddcc4f64606064f0e0 |
| src/hermes/components/chat/expandable-block.test.tsx | verbatim | apps/desktop/src/components/chat/expandable-block.test.tsx | 99b2791a802e030ac85aecaabf33a99f85955bc14ed5625cf1ff744d69e490a9 | 99b2791a802e030ac85aecaabf33a99f85955bc14ed5625cf1ff744d69e490a9 |
| src/hermes/components/chat/expandable-block.tsx | verbatim | apps/desktop/src/components/chat/expandable-block.tsx | f1537231f23186e441847fd1322fc61423312f1272a0df23d88b6341c93b744d | f1537231f23186e441847fd1322fc61423312f1272a0df23d88b6341c93b744d |
| src/hermes/components/chat/fixed-row-window.ts | verbatim | apps/desktop/src/components/chat/fixed-row-window.ts | 338e3d45aadcd6dc697c76762e418d098c9ced20cba35a54e728ff7d3ad4310a | 338e3d45aadcd6dc697c76762e418d098c9ced20cba35a54e728ff7d3ad4310a |
| src/hermes/components/chat/generated-image-result.tsx | patch | apps/desktop/src/components/chat/generated-image-result.tsx | 2fe8f506c3bb01d929a7bad7a37b0d05f30719c20f2d71f3da310db943f7515e | d5f8c461c9dabfe6696a9cb22046c2d9c5cd6191eee9f5883151167b5de47c6b |
| src/hermes/components/chat/image-generation-placeholder.test.tsx | verbatim | apps/desktop/src/components/chat/image-generation-placeholder.test.tsx | c0cef6a048b577050e5b5ec53e378e1ab8378383dbc6931edb45d95bf016744e | c0cef6a048b577050e5b5ec53e378e1ab8378383dbc6931edb45d95bf016744e |
| src/hermes/components/chat/image-generation-placeholder.tsx | verbatim | apps/desktop/src/components/chat/image-generation-placeholder.tsx | bb5ff3a75de7ce543e349d9fcdd0ba481c0152934e8411df75bcd1c7b6a51abb | bb5ff3a75de7ce543e349d9fcdd0ba481c0152934e8411df75bcd1c7b6a51abb |
| src/hermes/components/chat/intro-copy.jsonl | verbatim | apps/desktop/src/components/chat/intro-copy.jsonl | 96c863aaa7b1803fe16d357d617c7c19bb181253ccfbb4dd9b4cf78390b6d03b | 96c863aaa7b1803fe16d357d617c7c19bb181253ccfbb4dd9b4cf78390b6d03b |
| src/hermes/components/chat/intro.test.tsx | verbatim | apps/desktop/src/components/chat/intro.test.tsx | f76d46065a01b8a0658afbfe469d8eebd8ccae6155651c0011329618f6cb1dae | f76d46065a01b8a0658afbfe469d8eebd8ccae6155651c0011329618f6cb1dae |
| src/hermes/components/chat/intro.tsx | verbatim | apps/desktop/src/components/chat/intro.tsx | d2f3fb2115357c4c724b7bed5741c5def854ac346c9f4c570a68825ec65900d7 | d2f3fb2115357c4c724b7bed5741c5def854ac346c9f4c570a68825ec65900d7 |
| src/hermes/components/chat/log-search.tsx | verbatim | apps/desktop/src/components/chat/log-search.tsx | a437e35a811abdfcb6add023958456a8a8976069d7dc37279fed1e3faf2ccdc2 | a437e35a811abdfcb6add023958456a8a8976069d7dc37279fed1e3faf2ccdc2 |
| src/hermes/components/chat/log-tail.test.tsx | verbatim | apps/desktop/src/components/chat/log-tail.test.tsx | f818a12c3d1715cee98ea716441a13da58a1b87c8908fbf3b0b9341fdba90064 | f818a12c3d1715cee98ea716441a13da58a1b87c8908fbf3b0b9341fdba90064 |
| src/hermes/components/chat/log-tail.tsx | verbatim | apps/desktop/src/components/chat/log-tail.tsx | d79d8b14c98052d308a7168ef68dcb6c7e0f3444378926ad8916204fc50cf9f5 | d79d8b14c98052d308a7168ef68dcb6c7e0f3444378926ad8916204fc50cf9f5 |
| src/hermes/components/chat/preview-attachment.tsx | patch | apps/desktop/src/components/chat/preview-attachment.tsx | a6ccc966b44ca13b25b604e10ed32e53a2f236e86d6acaf6197cc304d0881b81 | 56d4db2b522af695df8cfa83a07ab52b5ead42c2fb0ad1f52ae55d0cd53bf9c1 |
| src/hermes/components/chat/scaffold-row.tsx | verbatim | apps/desktop/src/components/chat/scaffold-row.tsx | e889370591811c0d5be3a1f77fb481ceeaf6aaed69fe3f2bd90b7d3a16007623 | e889370591811c0d5be3a1f77fb481ceeaf6aaed69fe3f2bd90b7d3a16007623 |
| src/hermes/components/chat/shiki-block.test.tsx | verbatim | apps/desktop/src/components/chat/shiki-block.test.tsx | 95adec104eb2ad759936f7fc62cc8c936b9dd7f3400435d0cd32f9d7a9783044 | 95adec104eb2ad759936f7fc62cc8c936b9dd7f3400435d0cd32f9d7a9783044 |
| src/hermes/components/chat/shiki-block.tsx | verbatim | apps/desktop/src/components/chat/shiki-block.tsx | c14f8b10b5b030b9fc67a3f9e420be4c120c6fa2254c51e03049db1cbb32283e | c14f8b10b5b030b9fc67a3f9e420be4c120c6fa2254c51e03049db1cbb32283e |
| src/hermes/components/chat/shiki-chunk-boundary.tsx | verbatim | apps/desktop/src/components/chat/shiki-chunk-boundary.tsx | 21168d591d349e8322b11efccadb60b86bd69674bcce5eb2b4c4514c14553a5a | 21168d591d349e8322b11efccadb60b86bd69674bcce5eb2b4c4514c14553a5a |
| src/hermes/components/chat/shiki-config.ts | verbatim | apps/desktop/src/components/chat/shiki-config.ts | c8bb76d8daccbd476756b0f502200782472bab32d87872881d668780000bca5b | c8bb76d8daccbd476756b0f502200782472bab32d87872881d668780000bca5b |
| src/hermes/components/chat/shiki-highlight-cache.test.ts | verbatim | apps/desktop/src/components/chat/shiki-highlight-cache.test.ts | ef4122b52b40c848ea8515033198b14c0e91010ffe2f89a6809fcde35a092387 | ef4122b52b40c848ea8515033198b14c0e91010ffe2f89a6809fcde35a092387 |
| src/hermes/components/chat/shiki-highlight-cache.ts | verbatim | apps/desktop/src/components/chat/shiki-highlight-cache.ts | 1dc29d36d9d417bf5b9d2bfee8575a0b3702da45e00f1a978e342bcee9b8a999 | 1dc29d36d9d417bf5b9d2bfee8575a0b3702da45e00f1a978e342bcee9b8a999 |
| src/hermes/components/chat/shiki-highlighter.test.ts | verbatim | apps/desktop/src/components/chat/shiki-highlighter.test.ts | b4b8e714c878ea24ea0db4acb6fb62004fd914d61bfa08f359c6f0614036bcce | b4b8e714c878ea24ea0db4acb6fb62004fd914d61bfa08f359c6f0614036bcce |
| src/hermes/components/chat/shiki-highlighter.test.tsx | verbatim | apps/desktop/src/components/chat/shiki-highlighter.test.tsx | f9dbe61055a8ff25640c57d5ff92ebeff7089f19ad20479aa6bd2dee8186c79f | f9dbe61055a8ff25640c57d5ff92ebeff7089f19ad20479aa6bd2dee8186c79f |
| src/hermes/components/chat/shiki-highlighter.tsx | verbatim | apps/desktop/src/components/chat/shiki-highlighter.tsx | 9decfe05e146b8f5d869362ac6f7f2eb2b57222139b1acaa59b2afc62a51da14 | 9decfe05e146b8f5d869362ac6f7f2eb2b57222139b1acaa59b2afc62a51da14 |
| src/hermes/components/chat/shiki-plain.tsx | verbatim | apps/desktop/src/components/chat/shiki-plain.tsx | 1d023c2cb2e6df489bac929ef9b1641e8561a7d29521fbe1f855df69f0d9695f | 1d023c2cb2e6df489bac929ef9b1641e8561a7d29521fbe1f855df69f0d9695f |
| src/hermes/components/chat/skeletons.tsx | verbatim | apps/desktop/src/components/chat/skeletons.tsx | 0516fb7fbce8b86a46ebbf4d3c58106a579725cf1ad136168e68e0b107a82ebe | 0516fb7fbce8b86a46ebbf4d3c58106a579725cf1ad136168e68e0b107a82ebe |
| src/hermes/components/chat/stable-text.tsx | verbatim | apps/desktop/src/components/chat/stable-text.tsx | 5b520436602db6b9415f414e35be44f8b5182bd67ce1e6ac327fa6a9afe9c218 | 5b520436602db6b9415f414e35be44f8b5182bd67ce1e6ac327fa6a9afe9c218 |
| src/hermes/components/chat/status-control-row.tsx | verbatim | apps/desktop/src/components/chat/status-control-row.tsx | 37576f7e2e3a966a4894706d20d9c1318ced3b43d8949d9c0828b14ebfeafc1f | 37576f7e2e3a966a4894706d20d9c1318ced3b43d8949d9c0828b14ebfeafc1f |
| src/hermes/components/chat/status-dismiss-button.tsx | verbatim | apps/desktop/src/components/chat/status-dismiss-button.tsx | 1da841357047e5a707ff7a3c81d0553591ed0d5fabfef3851fcd72e016d58627 | 1da841357047e5a707ff7a3c81d0553591ed0d5fabfef3851fcd72e016d58627 |
| src/hermes/components/chat/status-pending-icon.tsx | verbatim | apps/desktop/src/components/chat/status-pending-icon.tsx | ef03b071f5bb0aaea16d3d900cf4c8e2375d7aebb50daaa9b036966d49c1b283 | ef03b071f5bb0aaea16d3d900cf4c8e2375d7aebb50daaa9b036966d49c1b283 |
| src/hermes/components/chat/status-row.test.tsx | verbatim | apps/desktop/src/components/chat/status-row.test.tsx | 4a86424d579ec85e73f77e567c47e331a6fccadd4dc50e9e6f1092a2e32e6257 | 4a86424d579ec85e73f77e567c47e331a6fccadd4dc50e9e6f1092a2e32e6257 |
| src/hermes/components/chat/status-row.tsx | verbatim | apps/desktop/src/components/chat/status-row.tsx | 243868bf9d7d7adbd9a4f07d433ebab1b9a7a8d58e813e986ff64007d547e105 | 243868bf9d7d7adbd9a4f07d433ebab1b9a7a8d58e813e986ff64007d547e105 |
| src/hermes/components/chat/status-section.tsx | verbatim | apps/desktop/src/components/chat/status-section.tsx | f9237b29ec497775a105b0047afacff0926a23649737a01bd2534f7d68b6a760 | f9237b29ec497775a105b0047afacff0926a23649737a01bd2534f7d68b6a760 |
| src/hermes/components/chat/status-stack.css | verbatim | apps/desktop/src/components/chat/status-stack.css | 3008847c29c7b9562fa01d833b969863c195594abe3ea365562239f624d8aebb | 3008847c29c7b9562fa01d833b969863c195594abe3ea365562239f624d8aebb |
| src/hermes/components/chat/syntax-diff.tsx | verbatim | apps/desktop/src/components/chat/syntax-diff.tsx | 20a6d268d38596113f7409bc87fae245ec547c0152624f9f1bffa968a0f1c8f7 | 20a6d268d38596113f7409bc87fae245ec547c0152624f9f1bffa968a0f1c8f7 |
| src/hermes/components/chat/transcript-video.test.tsx | verbatim | apps/desktop/src/components/chat/transcript-video.test.tsx | 7781ba24ddbd7781fd1e19a1e5b33b3d6ad51073af28c32ac7c0807f9ec774f7 | 7781ba24ddbd7781fd1e19a1e5b33b3d6ad51073af28c32ac7c0807f9ec774f7 |
| src/hermes/components/chat/transcript-video.tsx | verbatim | apps/desktop/src/components/chat/transcript-video.tsx | 55272d490046ccca0e3cd39dd3785ade7c3f21a4633e9c953d7ad3659bdf7ac9 | 55272d490046ccca0e3cd39dd3785ade7c3f21a4633e9c953d7ad3659bdf7ac9 |
| src/hermes/components/chat/widget-shell.ts | verbatim | apps/desktop/src/components/chat/widget-shell.ts | e7cc72df5e145fd46936f708d4bcc6fdfd2db9ca7ff23c21faaed62b9dc1f7b9 | e7cc72df5e145fd46936f708d4bcc6fdfd2db9ca7ff23c21faaed62b9dc1f7b9 |
| src/hermes/components/chat/wordmark.tsx | verbatim | apps/desktop/src/components/chat/wordmark.tsx | 655c53b6e6cc178e00b41e29c185486e8a91756196287c05e05fd391646fa227 | 655c53b6e6cc178e00b41e29c185486e8a91756196287c05e05fd391646fa227 |
| src/hermes/components/chat/zoomable-image.test.tsx | verbatim | apps/desktop/src/components/chat/zoomable-image.test.tsx | 392f2f1aaa93e79361a95c1013509d0cebbc18c79bcbb630c51976a4912830c7 | 392f2f1aaa93e79361a95c1013509d0cebbc18c79bcbb630c51976a4912830c7 |
| src/hermes/components/chat/zoomable-image.tsx | verbatim | apps/desktop/src/components/chat/zoomable-image.tsx | 2202452a269531374b97bab74c4e7891ed0205e6473617902889533684f28166 | 2202452a269531374b97bab74c4e7891ed0205e6473617902889533684f28166 |
| src/hermes/components/error-boundary.test.tsx | verbatim | apps/desktop/src/components/error-boundary.test.tsx | 4d187c433238455a57f9ab7d082355cefbfab6f082806e8664bb37ba071df85a | 4d187c433238455a57f9ab7d082355cefbfab6f082806e8664bb37ba071df85a |
| src/hermes/components/error-boundary.tsx | patch | apps/desktop/src/components/error-boundary.tsx | 95c86078f8510f7ffdf558b9b147f387edadbc16f97dd23fa436dfe6c731559e | 24b11b248347e786b7aef0f39e6b1034562667834d03dea74a70dae6ed3ae21a |
| src/hermes/components/free-tier/notice-strip.tsx | shim | apps/desktop/src/components/free-tier/notice-strip.tsx | 64a2385fb4f2ec63275bbd4c4b382f1b66713143adbdb72f44a3dd0fd0cd83ca | 0c800cfc0c61d9821f2bc728348cda867ed677c211377e2d0287177f67b4558b |
| src/hermes/components/model-picker.test.tsx | verbatim | apps/desktop/src/components/model-picker.test.tsx | 44b04e23e581a8918a59b5335049c69a29270e99354ea9f08e71b2e558ed84ac | 44b04e23e581a8918a59b5335049c69a29270e99354ea9f08e71b2e558ed84ac |
| src/hermes/components/model-picker.tsx | verbatim | apps/desktop/src/components/model-picker.tsx | c8635aded1cf35ac4a088c7d741ff26c2a789bbf5df176dcc769834698e86d13 | c8635aded1cf35ac4a088c7d741ff26c2a789bbf5df176dcc769834698e86d13 |
| src/hermes/components/notifications.test.tsx | verbatim | apps/desktop/src/components/notifications.test.tsx | 2c0518fb715285afee3bc911ae99a319240e333f88ca2be04e7af08dc93db0fe | 2c0518fb715285afee3bc911ae99a319240e333f88ca2be04e7af08dc93db0fe |
| src/hermes/components/notifications.tsx | verbatim | apps/desktop/src/components/notifications.tsx | 7cd303c2bbe063fc9dac89dea17bc3095a4c22349e1c6aff564d726166717739 | 7cd303c2bbe063fc9dac89dea17bc3095a4c22349e1c6aff564d726166717739 |
| src/hermes/components/onboarding/first-run/assembly.ts | shim | apps/desktop/src/components/onboarding/first-run/assembly.ts | 19e0be4b1eb852df0a9660e6522be0c98bc015b77d4c2f7c0d880d3a6bf8c1a8 | 8aa4270d4319c7c7cb4c2012da01f3055accde9b108416ce3267c9029379e815 |
| src/hermes/components/onboarding/first-run/intro-copy.tsx | shim | apps/desktop/src/components/onboarding/first-run/intro-copy.tsx | e28a6c30bc84e6ce7c296208f8cb7775a4913f876248467f6d39a81321883d6f | a9a4a5b704d732b1cbf1baebdf156c2d87da3ae1c4805bf3149b33c627b90a06 |
| src/hermes/components/onboarding/first-run/intro.ts | shim | apps/desktop/src/components/onboarding/first-run/intro.ts | f74d32d05e1ed3b6f1d573fbe76c1d9ea699b744b25357ef94e34a80353dc85b | 50f5a3bf78b35efe0a103291aab1aeaa59cf050e57d30b7bb4648490479075b8 |
| src/hermes/components/onboarding/skip-setup.test.tsx | verbatim | apps/desktop/src/components/onboarding/skip-setup.test.tsx | 3a499f39c3022ac378adeb66b4765e7d77b1122036640eeea0da261a416b7572 | 3a499f39c3022ac378adeb66b4765e7d77b1122036640eeea0da261a416b7572 |
| src/hermes/components/onboarding/skip-setup.tsx | shim | apps/desktop/src/components/onboarding/skip-setup.tsx | 2a920db4c45b7fcbdbafde74d7af9b5488e5d2f15f80d98f17cdc99a5b5676ba | b3a603ae1bb10766eb8c4ad778b7e44a1552b54ee528b05ab850f8bbd18fd628 |
| src/hermes/components/page-loader.tsx | verbatim | apps/desktop/src/components/page-loader.tsx | 7e099e1e887d1fa4d59c9fcf4a627d1206a1aed19a1aaf484f2c537eba9ac0d5 | 7e099e1e887d1fa4d59c9fcf4a627d1206a1aed19a1aaf484f2c537eba9ac0d5 |
| src/hermes/components/pane-shell/index.ts | shim | apps/desktop/src/components/pane-shell/index.ts | 73c65386def9d4738e49b26b724221ca3935bedd4bfc574cfb2a9a1655ae317c | 7c6cc44eda4ed98936c3e3178dd22f43309ad904feb5a917ead74bfc81e60fe2 |
| src/hermes/components/pane-shell/pane-lifecycle.test.ts | verbatim | apps/desktop/src/components/pane-shell/pane-lifecycle.test.ts | bf0b1815bcfc77966acb99ab7808dd4280b3d3b1769d58e159bae93748cada30 | bf0b1815bcfc77966acb99ab7808dd4280b3d3b1769d58e159bae93748cada30 |
| src/hermes/components/pane-shell/pane-lifecycle.ts | verbatim | apps/desktop/src/components/pane-shell/pane-lifecycle.ts | 939e2f54a52783aa6cfd3dbfbb94a7cfe1be8f138940d9669488b9cbdb53e548 | 939e2f54a52783aa6cfd3dbfbb94a7cfe1be8f138940d9669488b9cbdb53e548 |
| src/hermes/components/pane-shell/pane-visibility.ts | verbatim | apps/desktop/src/components/pane-shell/pane-visibility.ts | c1aec94e8466939cf4133f45cb6e9fb9c2c678ecef7f500ed7194702e7754f30 | c1aec94e8466939cf4133f45cb6e9fb9c2c678ecef7f500ed7194702e7754f30 |
| src/hermes/components/pane-shell/tree/preset-tree.test.ts | verbatim | apps/desktop/src/components/pane-shell/tree/preset-tree.test.ts | 5c88e3132e235c080e21e9a94e4c7a4469c07fdada0994ec240e84ddf9b53ef3 | 5c88e3132e235c080e21e9a94e4c7a4469c07fdada0994ec240e84ddf9b53ef3 |
| src/hermes/components/pane-shell/tree/preset-tree.ts | verbatim | apps/desktop/src/components/pane-shell/tree/preset-tree.ts | 3bb05c68f376ff7d10688c91ce1ec5ebe267cdff44c687e033f4ce50dafd5746 | 3bb05c68f376ff7d10688c91ce1ec5ebe267cdff44c687e033f4ce50dafd5746 |
| src/hermes/components/pane-shell/tree/presets.test.ts | verbatim | apps/desktop/src/components/pane-shell/tree/presets.test.ts | ae69c6f5034fb3d9a07a25f4b167be37850526582e53273a8b8b19a572010209 | ae69c6f5034fb3d9a07a25f4b167be37850526582e53273a8b8b19a572010209 |
| src/hermes/components/pane-shell/tree/presets.ts | verbatim | apps/desktop/src/components/pane-shell/tree/presets.ts | 05db9ef8e979325cac7c928b1d852e78d0cb805550f3e77c689355ffd0f65e5a | 05db9ef8e979325cac7c928b1d852e78d0cb805550f3e77c689355ffd0f65e5a |
| src/hermes/components/pane-shell/tree/renderer/drag-session.ts | shim | apps/desktop/src/components/pane-shell/tree/renderer/drag-session.ts | 3b0090def6f63f0a945f020a06082ad07ea27065e910e7351c1126e7d579a4ef | b05bcc9b67012b3c4ce59d426267f75c0ecda60abbd41ddb6631e24372645043 |
| src/hermes/components/pane-shell/tree/zones-engine.ts | verbatim | apps/desktop/src/components/pane-shell/tree/zones-engine.ts | f12f2e6a7e101eb72ff0a141c18a6f5272fd9191df57c25ead47bb8903a0e1e2 | f12f2e6a7e101eb72ff0a141c18a6f5272fd9191df57c25ead47bb8903a0e1e2 |
| src/hermes/components/prompt-overlays.test.tsx | verbatim | apps/desktop/src/components/prompt-overlays.test.tsx | 13d0b07e454de4b7a0fa7f6ae5ebb65260d371ae48711ae2ca2c6c8030e31377 | 13d0b07e454de4b7a0fa7f6ae5ebb65260d371ae48711ae2ca2c6c8030e31377 |
| src/hermes/components/prompt-overlays.tsx | shim | apps/desktop/src/components/prompt-overlays.tsx | 865a1915dd892fe79a6b42cf8fa6d53304ba8567ec688f88532b931024dbc117 | 2c7a453b81a98fb0295bc903ee13e361fc8a0f7251359be03e2bf7f2f724e26b |
| src/hermes/components/prompt-overlays.vault-code.test.tsx | verbatim | apps/desktop/src/components/prompt-overlays.vault-code.test.tsx | dd365a216df76078a1b8b45a2d3a4bd620dc0eda35ef2c48030a356370cac992 | dd365a216df76078a1b8b45a2d3a4bd620dc0eda35ef2c48030a356370cac992 |
| src/hermes/components/prompt-overlays.vault-save-login.test.tsx | verbatim | apps/desktop/src/components/prompt-overlays.vault-save-login.test.tsx | 88d8e1fc5cb8fa6c411c08f7a54209bcd9196ef73eae60cd6259d6209d2fb2fe | 88d8e1fc5cb8fa6c411c08f7a54209bcd9196ef73eae60cd6259d6209d2fb2fe |
| src/hermes/components/prompt-overlays.vault-unlock.test.tsx | verbatim | apps/desktop/src/components/prompt-overlays.vault-unlock.test.tsx | 08cabac821e400f995a170c52c7db7b7db20fce846ae80b14e1aefa66311abb9 | 08cabac821e400f995a170c52c7db7b7db20fce846ae80b14e1aefa66311abb9 |
| src/hermes/components/provider-status-chip.test.tsx | verbatim | apps/desktop/src/components/provider-status-chip.test.tsx | 0ec30f7b3ae15746c0da744b5c2d3eb62d96203491a5a89f8873aa836df24b2d | 0ec30f7b3ae15746c0da744b5c2d3eb62d96203491a5a89f8873aa836df24b2d |
| src/hermes/components/provider-status-chip.tsx | verbatim | apps/desktop/src/components/provider-status-chip.tsx | 3843df68324872145a7ddab91acf5638eadf591736ee5bc310ecd449c74b322b | 3843df68324872145a7ddab91acf5638eadf591736ee5bc310ecd449c74b322b |
| src/hermes/components/shared-metrics/consent-strip.test.tsx | verbatim | apps/desktop/src/components/shared-metrics/consent-strip.test.tsx | 911d777964769e3ac4dc9fe84b337156c1cc7338613fe81a865b9bac7620466f | 911d777964769e3ac4dc9fe84b337156c1cc7338613fe81a865b9bac7620466f |
| src/hermes/components/shared-metrics/consent-strip.tsx | verbatim | apps/desktop/src/components/shared-metrics/consent-strip.tsx | cdc9348e963da1ab851a0f824613af4900240dfd22b92923ac16b5479cc2ef70 | cdc9348e963da1ab851a0f824613af4900240dfd22b92923ac16b5479cc2ef70 |
| src/hermes/components/ui/action-status.tsx | verbatim | apps/desktop/src/components/ui/action-status.tsx | b7a13f485757ae72dcd5b25a43eb6532940fba2f1adc817d50d5b9659f79629e | b7a13f485757ae72dcd5b25a43eb6532940fba2f1adc817d50d5b9659f79629e |
| src/hermes/components/ui/actions-menu.test.tsx | verbatim | apps/desktop/src/components/ui/actions-menu.test.tsx | d00cb5dd74c756fd939697d575b00e8d283dbeb3243f4342f2c00b1be6dfca06 | d00cb5dd74c756fd939697d575b00e8d283dbeb3243f4342f2c00b1be6dfca06 |
| src/hermes/components/ui/actions-menu.tsx | verbatim | apps/desktop/src/components/ui/actions-menu.tsx | bab167480d6e554aa6c44b79a8375e887cd6aab9c5ac18a90294a6e0f9bb0b62 | bab167480d6e554aa6c44b79a8375e887cd6aab9c5ac18a90294a6e0f9bb0b62 |
| src/hermes/components/ui/alert.tsx | verbatim | apps/desktop/src/components/ui/alert.tsx | 5ef5a4a4d9da27cca6bf946c9ab187236a81805e2dff61c1d1a88d75814bc241 | 5ef5a4a4d9da27cca6bf946c9ab187236a81805e2dff61c1d1a88d75814bc241 |
| src/hermes/components/ui/avatar-chip.test.tsx | verbatim | apps/desktop/src/components/ui/avatar-chip.test.tsx | 3670f511869faa73a9657f9d36375323f167bfae10fbf4cf2fde2fc7d28081e7 | 3670f511869faa73a9657f9d36375323f167bfae10fbf4cf2fde2fc7d28081e7 |
| src/hermes/components/ui/avatar-chip.tsx | verbatim | apps/desktop/src/components/ui/avatar-chip.tsx | e6498aba1738560bb3b977270304f9a4076f235a2ce0f88b214d299ccec0485f | e6498aba1738560bb3b977270304f9a4076f235a2ce0f88b214d299ccec0485f |
| src/hermes/components/ui/badge.tsx | verbatim | apps/desktop/src/components/ui/badge.tsx | 86ef2f4fdc05f1280517a3f8b93f14f2baa03b988f4fd9e1e5e1f3c3fbdf10c8 | 86ef2f4fdc05f1280517a3f8b93f14f2baa03b988f4fd9e1e5e1f3c3fbdf10c8 |
| src/hermes/components/ui/button.tsx | verbatim | apps/desktop/src/components/ui/button.tsx | c598b7169674e3540ba5980d674f7d57618ef6b8ca62676d1966332443f87d76 | c598b7169674e3540ba5980d674f7d57618ef6b8ca62676d1966332443f87d76 |
| src/hermes/components/ui/card-stack.test.tsx | verbatim | apps/desktop/src/components/ui/card-stack.test.tsx | daad44f3f89baa25a23b44b1c2578e39bb3346f1f883a89f895a64e4e6a60895 | daad44f3f89baa25a23b44b1c2578e39bb3346f1f883a89f895a64e4e6a60895 |
| src/hermes/components/ui/card-stack.tsx | verbatim | apps/desktop/src/components/ui/card-stack.tsx | a9f2ccf1823dfb30dd74521d3a6f703223711a6f7d4ecbdfa6d914c1a8db3723 | a9f2ccf1823dfb30dd74521d3a6f703223711a6f7d4ecbdfa6d914c1a8db3723 |
| src/hermes/components/ui/checkbox.tsx | verbatim | apps/desktop/src/components/ui/checkbox.tsx | 1845051a23d947fd4de4dcbcd6a59504d4fa11ebb8ebbb582ab7ca5385b566c9 | 1845051a23d947fd4de4dcbcd6a59504d4fa11ebb8ebbb582ab7ca5385b566c9 |
| src/hermes/components/ui/chip.tsx | verbatim | apps/desktop/src/components/ui/chip.tsx | 477f60948c9ce83f95a62e552d3b6aab8fd95cf590e95c34d819543e704277e3 | 477f60948c9ce83f95a62e552d3b6aab8fd95cf590e95c34d819543e704277e3 |
| src/hermes/components/ui/codicon.tsx | verbatim | apps/desktop/src/components/ui/codicon.tsx | 47a57d2f57f2660a18aaafee65ed8ceeda8aa65e9c9e04fd0d3d09d09bbd8761 | 47a57d2f57f2660a18aaafee65ed8ceeda8aa65e9c9e04fd0d3d09d09bbd8761 |
| src/hermes/components/ui/color-swatches.tsx | verbatim | apps/desktop/src/components/ui/color-swatches.tsx | 92685c9a13f74cb375728061062fd4ca5cb1998bd49bfad0eace8e4289b7d01d | 92685c9a13f74cb375728061062fd4ca5cb1998bd49bfad0eace8e4289b7d01d |
| src/hermes/components/ui/command.tsx | verbatim | apps/desktop/src/components/ui/command.tsx | 53bd10e9a6c4ce512f8da77e4a5c01e8bfe83d042a5133d7f5f58ccb13d6d1a4 | 53bd10e9a6c4ce512f8da77e4a5c01e8bfe83d042a5133d7f5f58ccb13d6d1a4 |
| src/hermes/components/ui/confirm-dialog-unmount.test.tsx | verbatim | apps/desktop/src/components/ui/confirm-dialog-unmount.test.tsx | f01cc1fa452377d5adf71f420978801148d2076e3f3a2487c53cd0d8d12f8fbf | f01cc1fa452377d5adf71f420978801148d2076e3f3a2487c53cd0d8d12f8fbf |
| src/hermes/components/ui/confirm-dialog.test.tsx | verbatim | apps/desktop/src/components/ui/confirm-dialog.test.tsx | abcd8960511c9d6e6778740d6ee2444ae5778e9a27710bfde3d411f58f7745b5 | abcd8960511c9d6e6778740d6ee2444ae5778e9a27710bfde3d411f58f7745b5 |
| src/hermes/components/ui/confirm-dialog.tsx | verbatim | apps/desktop/src/components/ui/confirm-dialog.tsx | 25fb0355827e4e289de19423ff46ab40cff3c74f1013cbe7e0903b2678ec0b36 | 25fb0355827e4e289de19423ff46ab40cff3c74f1013cbe7e0903b2678ec0b36 |
| src/hermes/components/ui/connector-card.tsx | verbatim | apps/desktop/src/components/ui/connector-card.tsx | e5093f7110d8ae4ba2a6d0be5731227f584339c26b575cda4d16dcfc10beb02c | e5093f7110d8ae4ba2a6d0be5731227f584339c26b575cda4d16dcfc10beb02c |
| src/hermes/components/ui/connector-logo.tsx | verbatim | apps/desktop/src/components/ui/connector-logo.tsx | 768088afee6442ca9b71bfab918619a937e3cab330d2b27cae20e1de415cadce | 768088afee6442ca9b71bfab918619a937e3cab330d2b27cae20e1de415cadce |
| src/hermes/components/ui/context-menu.tsx | verbatim | apps/desktop/src/components/ui/context-menu.tsx | 55e4f39c49e9041f3373d7fbccebca396c0b9f86ec9bbaeaedd8ed6dd9c1dd8f | 55e4f39c49e9041f3373d7fbccebca396c0b9f86ec9bbaeaedd8ed6dd9c1dd8f |
| src/hermes/components/ui/control.ts | verbatim | apps/desktop/src/components/ui/control.ts | 5f24bdd445afc9acb7492647ce661ff1dde11313ab65ec3f1340cb32de225c61 | 5f24bdd445afc9acb7492647ce661ff1dde11313ab65ec3f1340cb32de225c61 |
| src/hermes/components/ui/copy-button.tsx | patch | apps/desktop/src/components/ui/copy-button.tsx | a852327bb008d53d84bc5fa7f7fa81fa2941222dffcc540bc114f5e6776402ea | e7535fa43d990f0122d842cf9183c94b5a029623bb56bf76cfa50448ba1c1af9 |
| src/hermes/components/ui/dialog-portal-context.ts | verbatim | apps/desktop/src/components/ui/dialog-portal-context.ts | 54560cfe6198b2088f55a0f2cb808059bf9e18fda0cca01af04b614e7b97f436 | 54560cfe6198b2088f55a0f2cb808059bf9e18fda0cca01af04b614e7b97f436 |
| src/hermes/components/ui/dialog.test.tsx | verbatim | apps/desktop/src/components/ui/dialog.test.tsx | 70f3bd4db0262fa10d61de77445d205cb924d8e07c37c8805badea8c7256e2ac | 70f3bd4db0262fa10d61de77445d205cb924d8e07c37c8805badea8c7256e2ac |
| src/hermes/components/ui/dialog.tsx | verbatim | apps/desktop/src/components/ui/dialog.tsx | 1b39f8ff9ba7c31199cc6572256c5c666ce8cdf6699f1db2b6fbbd26a2e069f3 | 1b39f8ff9ba7c31199cc6572256c5c666ce8cdf6699f1db2b6fbbd26a2e069f3 |
| src/hermes/components/ui/diff-count.tsx | verbatim | apps/desktop/src/components/ui/diff-count.tsx | b0f4ec0dea23bd4c798dcad68d07c4dbbfd38252805a56db14c58ebeea22ae3b | b0f4ec0dea23bd4c798dcad68d07c4dbbfd38252805a56db14c58ebeea22ae3b |
| src/hermes/components/ui/disclosure-caret.tsx | verbatim | apps/desktop/src/components/ui/disclosure-caret.tsx | 9e9babdde36fc5b3d1d62bcb4f6f67ddc1a203739effcb6616d6e845d3f38fb4 | 9e9babdde36fc5b3d1d62bcb4f6f67ddc1a203739effcb6616d6e845d3f38fb4 |
| src/hermes/components/ui/drop-affordance.tsx | verbatim | apps/desktop/src/components/ui/drop-affordance.tsx | 25cf37bfb1dc86b31be5bbd2cf024b395c6d3e9df782d25518066267cc9088b3 | 25cf37bfb1dc86b31be5bbd2cf024b395c6d3e9df782d25518066267cc9088b3 |
| src/hermes/components/ui/dropdown-menu.test.tsx | verbatim | apps/desktop/src/components/ui/dropdown-menu.test.tsx | c2e798e18330c31d4ea9e8b4d599c7053f75df28832c8e23b1fc8f58cb662e5a | c2e798e18330c31d4ea9e8b4d599c7053f75df28832c8e23b1fc8f58cb662e5a |
| src/hermes/components/ui/dropdown-menu.tsx | verbatim | apps/desktop/src/components/ui/dropdown-menu.tsx | 1ce60f59a8c8734abf023bb240a629e4bd4a2dd42d009188ed62dee20c5649d1 | 1ce60f59a8c8734abf023bb240a629e4bd4a2dd42d009188ed62dee20c5649d1 |
| src/hermes/components/ui/error-state.tsx | verbatim | apps/desktop/src/components/ui/error-state.tsx | 3966b267d8fb652e0e31c31bf33e67a27801b003851e03ca3888eec12296fd30 | 3966b267d8fb652e0e31c31bf33e67a27801b003851e03ca3888eec12296fd30 |
| src/hermes/components/ui/fade-scroll.test.ts | verbatim | apps/desktop/src/components/ui/fade-scroll.test.ts | 8aa8fe9c1709dd4f99334a872dbba9074402eebead984806b3602d32582dc9f5 | 8aa8fe9c1709dd4f99334a872dbba9074402eebead984806b3602d32582dc9f5 |
| src/hermes/components/ui/fade-scroll.tsx | verbatim | apps/desktop/src/components/ui/fade-scroll.tsx | 4e7ec89927971651539afb87caf5f67800c47739043ee46eff85b8ff28a46f7a | 4e7ec89927971651539afb87caf5f67800c47739043ee46eff85b8ff28a46f7a |
| src/hermes/components/ui/fade-text.tsx | verbatim | apps/desktop/src/components/ui/fade-text.tsx | 8c4b6a1da9579e264006b249f58ff63b033d748866a717b7eed858086cf6dcbc | 8c4b6a1da9579e264006b249f58ff63b033d748866a717b7eed858086cf6dcbc |
| src/hermes/components/ui/fan-menu-tooltip.test.tsx | verbatim | apps/desktop/src/components/ui/fan-menu-tooltip.test.tsx | 632b0d8cecd33c10edcd403691e305b0c0f5feb15c55abc9ee5461899a992faf | 632b0d8cecd33c10edcd403691e305b0c0f5feb15c55abc9ee5461899a992faf |
| src/hermes/components/ui/fan-menu.test.ts | verbatim | apps/desktop/src/components/ui/fan-menu.test.ts | 56cc996a1a36f362dd0ee3de3d970a83aa6b5cbfff541217c1c997227d840b62 | 56cc996a1a36f362dd0ee3de3d970a83aa6b5cbfff541217c1c997227d840b62 |
| src/hermes/components/ui/fan-menu.tsx | verbatim | apps/desktop/src/components/ui/fan-menu.tsx | a640bde9c57fd4eb7e66c97d4fbd7ba7a2018e7893cfdd198bf0975c7b37a3ab | a640bde9c57fd4eb7e66c97d4fbd7ba7a2018e7893cfdd198bf0975c7b37a3ab |
| src/hermes/components/ui/favicon.tsx | patch | apps/desktop/src/components/ui/favicon.tsx | 3184e87023873762f363edd6a36cd18ab93cd53e941e75a658c3585cbd280c9b | 92ecd6a5340bb0a068bf1dad1120a531196c609d2788fb58fed68f3bbfabe667 |
| src/hermes/components/ui/field.tsx | verbatim | apps/desktop/src/components/ui/field.tsx | 02a28f130b9ad0830b22253a9d05dfcd2455adf1efad3d778734163d0f861e84 | 02a28f130b9ad0830b22253a9d05dfcd2455adf1efad3d778734163d0f861e84 |
| src/hermes/components/ui/file-type-icon.tsx | verbatim | apps/desktop/src/components/ui/file-type-icon.tsx | 8b033936b9c9c856547687c93c6890c3db52ba467b6f32666946ae43364f13ac | 8b033936b9c9c856547687c93c6890c3db52ba467b6f32666946ae43364f13ac |
| src/hermes/components/ui/glyph-spinner.css | verbatim | apps/desktop/src/components/ui/glyph-spinner.css | 3c1de26f67756eb0ef30348fd2c88e95623947e5361045c7abdc61ff94951730 | 3c1de26f67756eb0ef30348fd2c88e95623947e5361045c7abdc61ff94951730 |
| src/hermes/components/ui/glyph-spinner.test.tsx | verbatim | apps/desktop/src/components/ui/glyph-spinner.test.tsx | 4b8a90674540587e9ca13dcbe3d0c540356aa71a729ce40fd8d6e4a33876d9d3 | 4b8a90674540587e9ca13dcbe3d0c540356aa71a729ce40fd8d6e4a33876d9d3 |
| src/hermes/components/ui/glyph-spinner.tsx | verbatim | apps/desktop/src/components/ui/glyph-spinner.tsx | 5ed4806e588b98286dc0dbebaf7ebda27dc6e8abfe4b4b1fe3de391a7ecf7108 | 5ed4806e588b98286dc0dbebaf7ebda27dc6e8abfe4b4b1fe3de391a7ecf7108 |
| src/hermes/components/ui/highlight-matches-model.test.tsx | verbatim | apps/desktop/src/components/ui/highlight-matches-model.test.tsx | 32d219d1d42be16f1704b9b36662523498f952e9b987c81654abd513aaed9ae2 | 32d219d1d42be16f1704b9b36662523498f952e9b987c81654abd513aaed9ae2 |
| src/hermes/components/ui/highlight-matches.test.tsx | verbatim | apps/desktop/src/components/ui/highlight-matches.test.tsx | aa6d027f6ca364c5d4e13579664de9eb46ed94fbecd91bea536cdf3052680137 | aa6d027f6ca364c5d4e13579664de9eb46ed94fbecd91bea536cdf3052680137 |
| src/hermes/components/ui/highlight-matches.tsx | verbatim | apps/desktop/src/components/ui/highlight-matches.tsx | ff4831b2c5b3ddc755516ff91ec608125bdf59c839c512ce907d1076b4a2fdd0 | ff4831b2c5b3ddc755516ff91ec608125bdf59c839c512ce907d1076b4a2fdd0 |
| src/hermes/components/ui/input.test.tsx | verbatim | apps/desktop/src/components/ui/input.test.tsx | 3d43f16baf2a0980f19ab9478640ac26e6b53a195903f4a8e6a7a8e9bc80678e | 3d43f16baf2a0980f19ab9478640ac26e6b53a195903f4a8e6a7a8e9bc80678e |
| src/hermes/components/ui/input.tsx | verbatim | apps/desktop/src/components/ui/input.tsx | 59a7fdf36e22f7818f82d231c50e35e15cc56b50b1f18ea3b61a23a7315c7cfd | 59a7fdf36e22f7818f82d231c50e35e15cc56b50b1f18ea3b61a23a7315c7cfd |
| src/hermes/components/ui/kbd.tsx | verbatim | apps/desktop/src/components/ui/kbd.tsx | 21054092206e209536865628c0ef1349c4f088b1790594f172e164b0951db584 | 21054092206e209536865628c0ef1349c4f088b1790594f172e164b0951db584 |
| src/hermes/components/ui/keyboard-first.test.ts | verbatim | apps/desktop/src/components/ui/keyboard-first.test.ts | 10fe8292aa8d837b071589fec1a5b5cd315696d6611ec162c09a65b7ad2d956e | 10fe8292aa8d837b071589fec1a5b5cd315696d6611ec162c09a65b7ad2d956e |
| src/hermes/components/ui/keyboard-first.ts | verbatim | apps/desktop/src/components/ui/keyboard-first.ts | b1e013e6ae17292a8127cc4133228fd32cb9d25ae0886b905e0518bfbed5b1b0 | b1e013e6ae17292a8127cc4133228fd32cb9d25ae0886b905e0518bfbed5b1b0 |
| src/hermes/components/ui/loader.tsx | verbatim | apps/desktop/src/components/ui/loader.tsx | 55ac619670db214533b189b8aa52b7854439397d4d1a87e9ab878a596c7eed82 | 55ac619670db214533b189b8aa52b7854439397d4d1a87e9ab878a596c7eed82 |
| src/hermes/components/ui/log-view.tsx | verbatim | apps/desktop/src/components/ui/log-view.tsx | f2d75d9ac0c8808c17b915d0c89a241d0d529e33fce042a5ce18e471c7a93687 | f2d75d9ac0c8808c17b915d0c89a241d0d529e33fce042a5ce18e471c7a93687 |
| src/hermes/components/ui/menu.ts | verbatim | apps/desktop/src/components/ui/menu.ts | a99de0d6f2c31010d127abaa55cd36abb9954cf626fcda922abe35168683b2d3 | a99de0d6f2c31010d127abaa55cd36abb9954cf626fcda922abe35168683b2d3 |
| src/hermes/components/ui/popover.tsx | verbatim | apps/desktop/src/components/ui/popover.tsx | 8fa80a2418bee1d9611aecc41c0e353c98e840da168a52841a3d3cca1aeddc49 | 8fa80a2418bee1d9611aecc41c0e353c98e840da168a52841a3d3cca1aeddc49 |
| src/hermes/components/ui/profile-glyph.test.tsx | verbatim | apps/desktop/src/components/ui/profile-glyph.test.tsx | d3e35f4314a793d4bb77d87421f7d60212e09c5a68a176c9e09bbc822db83c6d | d3e35f4314a793d4bb77d87421f7d60212e09c5a68a176c9e09bbc822db83c6d |
| src/hermes/components/ui/profile-glyph.tsx | verbatim | apps/desktop/src/components/ui/profile-glyph.tsx | 08737d5ce8c2b9b9eb5e0e4b57f33dc82de646ad1e2f159700a0c7f4fddcdd2e | 08737d5ce8c2b9b9eb5e0e4b57f33dc82de646ad1e2f159700a0c7f4fddcdd2e |
| src/hermes/components/ui/progress.tsx | verbatim | apps/desktop/src/components/ui/progress.tsx | 723e7f105f776985d531a7c2cb94a8bc9da09572d902606e967e17162f7acee9 | 723e7f105f776985d531a7c2cb94a8bc9da09572d902606e967e17162f7acee9 |
| src/hermes/components/ui/row-button.test.tsx | verbatim | apps/desktop/src/components/ui/row-button.test.tsx | 6b47f40c6b8efcdf32b0fb494f6f0b4ad80228a132829b145dfad3cfc8d1cb43 | 6b47f40c6b8efcdf32b0fb494f6f0b4ad80228a132829b145dfad3cfc8d1cb43 |
| src/hermes/components/ui/row-button.tsx | verbatim | apps/desktop/src/components/ui/row-button.tsx | cdde5f344a0c3dfbe6c6a12d8f7446c828db67d97faf0a48633bc88b9332e028 | cdde5f344a0c3dfbe6c6a12d8f7446c828db67d97faf0a48633bc88b9332e028 |
| src/hermes/components/ui/sanitized-input.tsx | verbatim | apps/desktop/src/components/ui/sanitized-input.tsx | 4758ad11a769d3c60d030d7f59a7f3afb5b5feca7941e8382034442796a46caa | 4758ad11a769d3c60d030d7f59a7f3afb5b5feca7941e8382034442796a46caa |
| src/hermes/components/ui/search-field.test.tsx | verbatim | apps/desktop/src/components/ui/search-field.test.tsx | 9fc2a97a184147b32c99d3bafc88a94a9ee48fc2c84c4e09f505d1cbe2238229 | 9fc2a97a184147b32c99d3bafc88a94a9ee48fc2c84c4e09f505d1cbe2238229 |
| src/hermes/components/ui/search-field.tsx | verbatim | apps/desktop/src/components/ui/search-field.tsx | 59f32091c50e37155ba4a66148eea9f2ccd97e049e704730b77528da182a377a | 59f32091c50e37155ba4a66148eea9f2ccd97e049e704730b77528da182a377a |
| src/hermes/components/ui/segmented-control.tsx | verbatim | apps/desktop/src/components/ui/segmented-control.tsx | ccd6c9aa06ae83e25fac017d9008187e387db2a0b25429281bdc57d8ee9c60e7 | ccd6c9aa06ae83e25fac017d9008187e387db2a0b25429281bdc57d8ee9c60e7 |
| src/hermes/components/ui/select.tsx | verbatim | apps/desktop/src/components/ui/select.tsx | 43ddfccf9078c457320dc6b377f08fd61172dfbc0a37d61685e160005aaf05a3 | 43ddfccf9078c457320dc6b377f08fd61172dfbc0a37d61685e160005aaf05a3 |
| src/hermes/components/ui/separator.tsx | verbatim | apps/desktop/src/components/ui/separator.tsx | f39e1a2dfd4453ca21348401d1eb5e5a82e20c9281c1fde5042b2c8bb1c45c21 | f39e1a2dfd4453ca21348401d1eb5e5a82e20c9281c1fde5042b2c8bb1c45c21 |
| src/hermes/components/ui/setup-field-list.tsx | verbatim | apps/desktop/src/components/ui/setup-field-list.tsx | 5afb6be004df7395eecff7417a3c9f5e853bbda36f3750be5040dbb8753befa9 | 5afb6be004df7395eecff7417a3c9f5e853bbda36f3750be5040dbb8753befa9 |
| src/hermes/components/ui/sheet.tsx | verbatim | apps/desktop/src/components/ui/sheet.tsx | 99db0d3fd9d3fdda7a25bf65d4bd448c2ca99e7e98999f071e26a68336d5f6fc | 99db0d3fd9d3fdda7a25bf65d4bd448c2ca99e7e98999f071e26a68336d5f6fc |
| src/hermes/components/ui/sidebar.tsx | verbatim | apps/desktop/src/components/ui/sidebar.tsx | 5f4df4a2ccee11ccfad1afdeba9ef74ce7de27baa56ec328ea08971cbe43f2f9 | 5f4df4a2ccee11ccfad1afdeba9ef74ce7de27baa56ec328ea08971cbe43f2f9 |
| src/hermes/components/ui/skeleton.tsx | verbatim | apps/desktop/src/components/ui/skeleton.tsx | 0c7de55885552be4582fd2a030f4ae40a4d5f96ee4765e6a0ef6750f3174088e | 0c7de55885552be4582fd2a030f4ae40a4d5f96ee4765e6a0ef6750f3174088e |
| src/hermes/components/ui/split-button.tsx | verbatim | apps/desktop/src/components/ui/split-button.tsx | a1c92e1ca7113620d950da5afed9dbba2d4933e9d0cf81e515a303f579fc2bbc | a1c92e1ca7113620d950da5afed9dbba2d4933e9d0cf81e515a303f579fc2bbc |
| src/hermes/components/ui/status-pulse.test.tsx | verbatim | apps/desktop/src/components/ui/status-pulse.test.tsx | 39935769293089f749725989f7b463a5516a410a83b8c698a678aafd03caf600 | 39935769293089f749725989f7b463a5516a410a83b8c698a678aafd03caf600 |
| src/hermes/components/ui/status-pulse.tsx | verbatim | apps/desktop/src/components/ui/status-pulse.tsx | e33ec323ce1c310449bfcd9d5eb4403c1598802ef88a0a232ceb5a7b5a0adde2 | e33ec323ce1c310449bfcd9d5eb4403c1598802ef88a0a232ceb5a7b5a0adde2 |
| src/hermes/components/ui/switch.tsx | verbatim | apps/desktop/src/components/ui/switch.tsx | 7b2d12240d95f16db43ef56bad30df34b1e9926059b920362df028f13fe2dd9c | 7b2d12240d95f16db43ef56bad30df34b1e9926059b920362df028f13fe2dd9c |
| src/hermes/components/ui/tab-dropdown.tsx | verbatim | apps/desktop/src/components/ui/tab-dropdown.tsx | 124482215c657627bd806ca975e29466e6191d573309a56e750afede55fd5604 | 124482215c657627bd806ca975e29466e6191d573309a56e750afede55fd5604 |
| src/hermes/components/ui/text-tab.tsx | verbatim | apps/desktop/src/components/ui/text-tab.tsx | 8bb20153fc653317189273c01072d60c5d686e742adad072897b50c258b4b7a1 | 8bb20153fc653317189273c01072d60c5d686e742adad072897b50c258b4b7a1 |
| src/hermes/components/ui/textarea.tsx | verbatim | apps/desktop/src/components/ui/textarea.tsx | e996fa0c90b98e98856a018201a3c58a202621f40c0e946ab73fbf3e97a6ddf3 | e996fa0c90b98e98856a018201a3c58a202621f40c0e946ab73fbf3e97a6ddf3 |
| src/hermes/components/ui/title-menu-trigger.tsx | verbatim | apps/desktop/src/components/ui/title-menu-trigger.tsx | b691d40187ca17978e99e3e1d050a8f71ac7a0e85d4224eae94f1d6c83e2acd3 | b691d40187ca17978e99e3e1d050a8f71ac7a0e85d4224eae94f1d6c83e2acd3 |
| src/hermes/components/ui/tool-icon.tsx | verbatim | apps/desktop/src/components/ui/tool-icon.tsx | ba52306db657f7471f40334e5527e80121140174b009f65d2d2896b8019044ae | ba52306db657f7471f40334e5527e80121140174b009f65d2d2896b8019044ae |
| src/hermes/components/ui/tooltip-pane-layout.test.tsx | verbatim | apps/desktop/src/components/ui/tooltip-pane-layout.test.tsx | 7f18bd2637a6bb4742ad6327ce5abe2e5ff3f57f95045df3e572042d52633653 | 7f18bd2637a6bb4742ad6327ce5abe2e5ff3f57f95045df3e572042d52633653 |
| src/hermes/components/ui/tooltip-placement.test.tsx | verbatim | apps/desktop/src/components/ui/tooltip-placement.test.tsx | 4bc6b4f735c300ec2c7c10867c4910c88c3b22e8156cc3b60fb6a9caac23eb07 | 4bc6b4f735c300ec2c7c10867c4910c88c3b22e8156cc3b60fb6a9caac23eb07 |
| src/hermes/components/ui/tooltip-placement.ts | verbatim | apps/desktop/src/components/ui/tooltip-placement.ts | 3fe94deb68735d7e5d96bab26abd28eb223b60fc401e8e38749e5ca117d4d36b | 3fe94deb68735d7e5d96bab26abd28eb223b60fc401e8e38749e5ca117d4d36b |
| src/hermes/components/ui/tooltip-rail-edge.test.tsx | verbatim | apps/desktop/src/components/ui/tooltip-rail-edge.test.tsx | cf176a4c7342b98bee9e628d2bfdd173d27dbb144a166acbbfe658ab68302db1 | cf176a4c7342b98bee9e628d2bfdd173d27dbb144a166acbbfe658ab68302db1 |
| src/hermes/components/ui/tooltip.css | verbatim | apps/desktop/src/components/ui/tooltip.css | 9d44f73a21f4cdf46cb5349c62816b886a1d0c139c2bdab653e783c9c5eec43f | 9d44f73a21f4cdf46cb5349c62816b886a1d0c139c2bdab653e783c9c5eec43f |
| src/hermes/components/ui/tooltip.test.tsx | verbatim | apps/desktop/src/components/ui/tooltip.test.tsx | f59976959bfb94fc4d5a40a95034c692d4962e0d41268de90440324239b3cde7 | f59976959bfb94fc4d5a40a95034c692d4962e0d41268de90440324239b3cde7 |
| src/hermes/components/ui/tooltip.tsx | verbatim | apps/desktop/src/components/ui/tooltip.tsx | dc3ef874ec2f0bd39a60413fa594cc13fc5394cbfb1ef871573b1ace6237cb9a | dc3ef874ec2f0bd39a60413fa594cc13fc5394cbfb1ef871573b1ace6237cb9a |
| src/hermes/components/ui/use-zoom-pan.ts | verbatim | apps/desktop/src/components/ui/use-zoom-pan.ts | d62058c229d8d8e0df76cc3594bfb4fea4b3857b399042df4eae2f82f14b72ae | d62058c229d8d8e0df76cc3594bfb4fea4b3857b399042df4eae2f82f14b72ae |
| src/hermes/components/ui/zoomable-fit.test.tsx | verbatim | apps/desktop/src/components/ui/zoomable-fit.test.tsx | 05805e14a384cc3d1711cdfce97ec9fd9bae1f2854bfd99b656082de41ec5683 | 05805e14a384cc3d1711cdfce97ec9fd9bae1f2854bfd99b656082de41ec5683 |
| src/hermes/components/ui/zoomable.test.tsx | verbatim | apps/desktop/src/components/ui/zoomable.test.tsx | 9effeefb5dcb87f89e07d44b4a979488a5fdadd0660d71ac258f2637351e768d | 9effeefb5dcb87f89e07d44b4a979488a5fdadd0660d71ac258f2637351e768d |
| src/hermes/components/ui/zoomable.tsx | verbatim | apps/desktop/src/components/ui/zoomable.tsx | f80019894bfffe505712aeffc51a884515c90ef670ebfe8f7efc42bca08bc50f | f80019894bfffe505712aeffc51a884515c90ef670ebfe8f7efc42bca08bc50f |
| src/hermes/contrib/index.ts | patch | apps/desktop/src/contrib/index.ts | 9b1f0be579f6cca2630472007af646376d4f1217c75f8987d92927bc4657dff7 | 7a9f448eb5a42fe7035a71b2a72c61248fb836cb232484866277fb50cbfb4eb8 |
| src/hermes/contrib/react/boundary.tsx | verbatim | apps/desktop/src/contrib/react/boundary.tsx | ca587e00b20a6403f3883500a12e281b51d160a4ffbb4b83cde8e5c7b40aa3de | ca587e00b20a6403f3883500a12e281b51d160a4ffbb4b83cde8e5c7b40aa3de |
| src/hermes/contrib/react/slot.test.tsx | verbatim | apps/desktop/src/contrib/react/slot.test.tsx | b49d79018c2ca9b8251655cd5188e3b5010fc4047863ce52a5004a4b893c902f | b49d79018c2ca9b8251655cd5188e3b5010fc4047863ce52a5004a4b893c902f |
| src/hermes/contrib/react/slot.tsx | verbatim | apps/desktop/src/contrib/react/slot.tsx | 3317c09167457baf5a6ac1201b779fbfd70005975e2e83d9b7d0700da53f2f3d | 3317c09167457baf5a6ac1201b779fbfd70005975e2e83d9b7d0700da53f2f3d |
| src/hermes/contrib/react/use-contributions.ts | verbatim | apps/desktop/src/contrib/react/use-contributions.ts | 24d7e56cba5b632a7fdc60cb3ed28039166ee5009673f462d876dce5eca90ef4 | 24d7e56cba5b632a7fdc60cb3ed28039166ee5009673f462d876dce5eca90ef4 |
| src/hermes/contrib/registry.test.ts | verbatim | apps/desktop/src/contrib/registry.test.ts | ec85f1cbdd249a798f46f019ffe52cb494e0ca2270c140b3490608284e47973d | ec85f1cbdd249a798f46f019ffe52cb494e0ca2270c140b3490608284e47973d |
| src/hermes/contrib/registry.ts | verbatim | apps/desktop/src/contrib/registry.ts | fbae365ca1e4e5a0f75a9ab45f6496f1b66dd67a915284e65ebb32d0e688efbb | fbae365ca1e4e5a0f75a9ab45f6496f1b66dd67a915284e65ebb32d0e688efbb |
| src/hermes/hermes-capability-scope.test.ts | verbatim | apps/desktop/src/hermes-capability-scope.test.ts | 0927702bac3abcdbdbc283e5fdd9b466073036f4ea3301ee6b5cf1233124ef90 | 0927702bac3abcdbdbc283e5fdd9b466073036f4ea3301ee6b5cf1233124ef90 |
| src/hermes/hermes-cron-scope.test.ts | verbatim | apps/desktop/src/hermes-cron-scope.test.ts | e174a7da81ee64f6bdb2a449824e2441b9fcb2a0750d641808a9e2c375cfe8e5 | e174a7da81ee64f6bdb2a449824e2441b9fcb2a0750d641808a9e2c375cfe8e5 |
| src/hermes/hermes-parity.test.ts | verbatim | apps/desktop/src/hermes-parity.test.ts | 33b6f84c540989dd4785353679be50c3f06263d76f2de71f84e3e3359c767852 | 33b6f84c540989dd4785353679be50c3f06263d76f2de71f84e3e3359c767852 |
| src/hermes/hermes-profile-scope.test.ts | verbatim | apps/desktop/src/hermes-profile-scope.test.ts | 08522b892f0a1e1aa60f21e4ce3795f6e79f2ec5ff3b5c1b655a3c26a5167591 | 08522b892f0a1e1aa60f21e4ce3795f6e79f2ec5ff3b5c1b655a3c26a5167591 |
| src/hermes/hermes.test.ts | verbatim | apps/desktop/src/hermes.test.ts | 0a8fdc9cb183d1c9e52e8047a05ffa7afc9c3c169d82b46b2d3f6313f8ed239e | 0a8fdc9cb183d1c9e52e8047a05ffa7afc9c3c169d82b46b2d3f6313f8ed239e |
| src/hermes/hooks/use-image-download.test.ts | verbatim | apps/desktop/src/hooks/use-image-download.test.ts | ee559d19d214541bf4548184214b25d107e10c7ed3cd2226ef44406439931e5f | ee559d19d214541bf4548184214b25d107e10c7ed3cd2226ef44406439931e5f |
| src/hermes/hooks/use-image-download.ts | patch | apps/desktop/src/hooks/use-image-download.ts | b227cd1621b97f2a8658a890b0a6d2174211a32f42f0b1b54f4fda0e69be8f26 | d91c4c51250a10c0aaa24856c7607d6258de62cf35458ddc6028365241d67244 |
| src/hermes/hooks/use-media-image.frames.test.tsx | verbatim | apps/desktop/src/hooks/use-media-image.frames.test.tsx | 876c31e015ec100ef5c331f9f9f6d06695b055c8b8d34b1b40b1e4b9750056d1 | 876c31e015ec100ef5c331f9f9f6d06695b055c8b8d34b1b40b1e4b9750056d1 |
| src/hermes/hooks/use-media-image.ts | verbatim | apps/desktop/src/hooks/use-media-image.ts | ea2c44d765bafb8e4b8575aa4ba8191b1b9a203dc2680548eef386e818a3f77c | ea2c44d765bafb8e4b8575aa4ba8191b1b9a203dc2680548eef386e818a3f77c |
| src/hermes/hooks/use-media-query.ts | verbatim | apps/desktop/src/hooks/use-media-query.ts | 288422d8d657a00793301b720428538adba8083877558677584c69a612afe9ea | 288422d8d657a00793301b720428538adba8083877558677584c69a612afe9ea |
| src/hermes/hooks/use-mobile.ts | verbatim | apps/desktop/src/hooks/use-mobile.ts | 79aa2e219bcd385933dec940d3378d4215251d0549ac468f688d16861bccc6ef | 79aa2e219bcd385933dec940d3378d4215251d0549ac468f688d16861bccc6ef |
| src/hermes/hooks/use-resize-observer.ts | verbatim | apps/desktop/src/hooks/use-resize-observer.ts | cc16298e3d1a9a6b3c822e9a976ce1a5f95c020e5b1a72de0c800c07063dab52 | cc16298e3d1a9a6b3c822e9a976ce1a5f95c020e5b1a72de0c800c07063dab52 |
| src/hermes/hooks/use-theme-epoch.ts | verbatim | apps/desktop/src/hooks/use-theme-epoch.ts | 7ee0298013dd7683b47e1be3945fc47f045f8d5d3fd4e1ac061241f613d7d52c | 7ee0298013dd7683b47e1be3945fc47f045f8d5d3fd4e1ac061241f613d7d52c |
| src/hermes/hooks/use-viewed-interval.ts | verbatim | apps/desktop/src/hooks/use-viewed-interval.ts | 32afdce266f79bc3f5985d99080abaac750de8720151550e32b4929bf6f6e266 | 32afdce266f79bc3f5985d99080abaac750de8720151550e32b4929bf6f6e266 |
| src/hermes/i18n/ar.ts | verbatim | apps/desktop/src/i18n/ar.ts | 2359485a8a0b347803fefcebde6090dd9893a2993a84fd8c08ec4d1402413c01 | 2359485a8a0b347803fefcebde6090dd9893a2993a84fd8c08ec4d1402413c01 |
| src/hermes/i18n/ar_artifacts.ts | verbatim | apps/desktop/src/i18n/ar_artifacts.ts | 7453f3eec63e557c0c1830a5ea5cbcfe6523d3b61f99e2b6f1158c721fe70fc1 | 7453f3eec63e557c0c1830a5ea5cbcfe6523d3b61f99e2b6f1158c721fe70fc1 |
| src/hermes/i18n/ar_assistant.ts | verbatim | apps/desktop/src/i18n/ar_assistant.ts | f15cf723380ff9f1d8c42915427dc8fe81f57442dcb38f5a94966db3e176bb43 | f15cf723380ff9f1d8c42915427dc8fe81f57442dcb38f5a94966db3e176bb43 |
| src/hermes/i18n/ar_boot.ts | verbatim | apps/desktop/src/i18n/ar_boot.ts | 074cc19d6daa1b0232f9ad027339b16cca3a8f9116f8f1e104325683ae52f225 | 074cc19d6daa1b0232f9ad027339b16cca3a8f9116f8f1e104325683ae52f225 |
| src/hermes/i18n/ar_capabilities.ts | verbatim | apps/desktop/src/i18n/ar_capabilities.ts | 30b33d9ee8424e60b8eb35608897b20540d7eb77056b26a416b2e0e957f7c35a | 30b33d9ee8424e60b8eb35608897b20540d7eb77056b26a416b2e0e957f7c35a |
| src/hermes/i18n/ar_chat.ts | verbatim | apps/desktop/src/i18n/ar_chat.ts | 0101160f7692c060d230f828e68bc77e398def8ac0e61f0350fe51011f6b1990 | 0101160f7692c060d230f828e68bc77e398def8ac0e61f0350fe51011f6b1990 |
| src/hermes/i18n/ar_chrome.ts | verbatim | apps/desktop/src/i18n/ar_chrome.ts | af8c7319684b8a5a4102f02dc7337bdc1555500cb5aa7fd93ad4ae05bf3fd736 | af8c7319684b8a5a4102f02dc7337bdc1555500cb5aa7fd93ad4ae05bf3fd736 |
| src/hermes/i18n/ar_command_center.ts | verbatim | apps/desktop/src/i18n/ar_command_center.ts | 81304503c5536b3fac2dccda9c46702a68c0b2c1fec1367c3c445612520bc205 | 81304503c5536b3fac2dccda9c46702a68c0b2c1fec1367c3c445612520bc205 |
| src/hermes/i18n/ar_common.ts | verbatim | apps/desktop/src/i18n/ar_common.ts | 6d3af3077343e34a5b5888e89bb22d1fd5b8eae020b2bfc5c81d3c1dbc99b858 | 6d3af3077343e34a5b5888e89bb22d1fd5b8eae020b2bfc5c81d3c1dbc99b858 |
| src/hermes/i18n/ar_connectors.ts | verbatim | apps/desktop/src/i18n/ar_connectors.ts | 1cf1de022f88cd07e515228942edb49c1252d736d42d655915590d35d8869b68 | 1cf1de022f88cd07e515228942edb49c1252d736d42d655915590d35d8869b68 |
| src/hermes/i18n/ar_delegation_routing.ts | verbatim | apps/desktop/src/i18n/ar_delegation_routing.ts | 2f2426896be3255a412c3763d27c7ee0bc8ff696e35d216aa5bb07508e72f1e3 | 2f2426896be3255a412c3763d27c7ee0bc8ff696e35d216aa5bb07508e72f1e3 |
| src/hermes/i18n/ar_desktop_commands.ts | verbatim | apps/desktop/src/i18n/ar_desktop_commands.ts | d868c240d509236d681edaf995261052d063e889a0724ddcf9c20b8556d9a446 | d868c240d509236d681edaf995261052d063e889a0724ddcf9c20b8556d9a446 |
| src/hermes/i18n/ar_diagnostics.ts | verbatim | apps/desktop/src/i18n/ar_diagnostics.ts | 40dc6482bffeceb61daa1fb73f8cc7bdd78ad1ef26da27d65a8c30c21d5bf026 | 40dc6482bffeceb61daa1fb73f8cc7bdd78ad1ef26da27d65a8c30c21d5bf026 |
| src/hermes/i18n/ar_first_run.ts | verbatim | apps/desktop/src/i18n/ar_first_run.ts | 31c68dce454eaff254ec8576abe56dbd136f51bf044d4447a28bf23dacda5704 | 31c68dce454eaff254ec8576abe56dbd136f51bf044d4447a28bf23dacda5704 |
| src/hermes/i18n/ar_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/ar_plugin_preview_access.ts | 803d15ea50e91cb986ab446538a393a55ed1148e976d0b3a68467c053191a19e | 803d15ea50e91cb986ab446538a393a55ed1148e976d0b3a68467c053191a19e |
| src/hermes/i18n/ar_session_commands.ts | verbatim | apps/desktop/src/i18n/ar_session_commands.ts | a2af037b4bce55b7bd87cb72b909a2342b4c32459afe476857454554dffd6f4d | a2af037b4bce55b7bd87cb72b909a2342b4c32459afe476857454554dffd6f4d |
| src/hermes/i18n/ar_settings.ts | verbatim | apps/desktop/src/i18n/ar_settings.ts | ea32c1b8aa73ed15f3d633bb0a545a52dc0d0586f45e193a6b86e9f30cc74ce0 | ea32c1b8aa73ed15f3d633bb0a545a52dc0d0586f45e193a6b86e9f30cc74ce0 |
| src/hermes/i18n/ar_theme_picker.ts | verbatim | apps/desktop/src/i18n/ar_theme_picker.ts | fc7d421ca0c9e5f124d615f95cc06617c2639ea2cc327e7d699fb49554c8cbb8 | fc7d421ca0c9e5f124d615f95cc06617c2639ea2cc327e7d699fb49554c8cbb8 |
| src/hermes/i18n/ar_update_channel.ts | verbatim | apps/desktop/src/i18n/ar_update_channel.ts | 9bd4c6c2e54a6b74ac70ce20995d98623bc274cf41db6875f2702b90f8014ffc | 9bd4c6c2e54a6b74ac70ce20995d98623bc274cf41db6875f2702b90f8014ffc |
| src/hermes/i18n/catalog-completeness.test.ts | verbatim | apps/desktop/src/i18n/catalog-completeness.test.ts | a7d51704cdd10ba2616b3a4b8de2a1f8ec157b05a6ed2efaf42c104f5ad14d4d | a7d51704cdd10ba2616b3a4b8de2a1f8ec157b05a6ed2efaf42c104f5ad14d4d |
| src/hermes/i18n/catalog.ts | verbatim | apps/desktop/src/i18n/catalog.ts | 4605ddd5f515358855767bdffa24453a3c4bf651562ab03e44d31deef2bd337b | 4605ddd5f515358855767bdffa24453a3c4bf651562ab03e44d31deef2bd337b |
| src/hermes/i18n/context.test.tsx | verbatim | apps/desktop/src/i18n/context.test.tsx | ac6d658fd2ce14bd136973c08ea428fe353f4695f2556c11211ead5e66ea536f | ac6d658fd2ce14bd136973c08ea428fe353f4695f2556c11211ead5e66ea536f |
| src/hermes/i18n/context.tsx | patch | apps/desktop/src/i18n/context.tsx | bc9b9522fe22b2cbe0e029f32aa4134652f295af847ba096ad620a78936d0e6c | 65780af4e433cb4a61cab0cdd23fe30324fc2638c5de5bc5bc50c1daa353060f |
| src/hermes/i18n/de.ts | verbatim | apps/desktop/src/i18n/de.ts | 4212002623b4ff3361d68b51bdbc9ceacb3971977697530eebe344d0af00a1a9 | 4212002623b4ff3361d68b51bdbc9ceacb3971977697530eebe344d0af00a1a9 |
| src/hermes/i18n/de_aux_tasks.ts | verbatim | apps/desktop/src/i18n/de_aux_tasks.ts | b1581d68fb76682e445872169f1c8a62f568d96ce920fde0663a176307680f3c | b1581d68fb76682e445872169f1c8a62f568d96ce920fde0663a176307680f3c |
| src/hermes/i18n/de_boot.ts | verbatim | apps/desktop/src/i18n/de_boot.ts | 6fa309abfd05ac2173c1dde656d1e45ff7ab18a997b4821500a42d71b3d33ee0 | 6fa309abfd05ac2173c1dde656d1e45ff7ab18a997b4821500a42d71b3d33ee0 |
| src/hermes/i18n/de_clarify.ts | verbatim | apps/desktop/src/i18n/de_clarify.ts | db6be34b7d506b250d7e363469197ade4d6b700c55aaf7fed885f0c3c2be8d27 | db6be34b7d506b250d7e363469197ade4d6b700c55aaf7fed885f0c3c2be8d27 |
| src/hermes/i18n/de_connector_accounts.ts | verbatim | apps/desktop/src/i18n/de_connector_accounts.ts | d2159f3d6a23c15e279ec6f0db61754b00860bc23394536edd829cbf450c8ddc | d2159f3d6a23c15e279ec6f0db61754b00860bc23394536edd829cbf450c8ddc |
| src/hermes/i18n/de_connector_card.ts | verbatim | apps/desktop/src/i18n/de_connector_card.ts | 33f7197ee07be5b5a4a0b57669f2290e4642067514367984333f32aa54dc43df | 33f7197ee07be5b5a4a0b57669f2290e4642067514367984333f32aa54dc43df |
| src/hermes/i18n/de_delegation_routing.ts | verbatim | apps/desktop/src/i18n/de_delegation_routing.ts | be727bdd35add25c3e504a6bf7b319fbd3732d731588a53875fe140aa82641a2 | be727bdd35add25c3e504a6bf7b319fbd3732d731588a53875fe140aa82641a2 |
| src/hermes/i18n/de_desktop_commands.ts | verbatim | apps/desktop/src/i18n/de_desktop_commands.ts | 2cd554531425d6df54d51ca17ec5d29d8e0936b72381fa5a8ac43e3058135f6f | 2cd554531425d6df54d51ca17ec5d29d8e0936b72381fa5a8ac43e3058135f6f |
| src/hermes/i18n/de_error_layers.ts | verbatim | apps/desktop/src/i18n/de_error_layers.ts | b34304885d2c600e18689d96042f0ef5f43b468a01e4de5f7714d91969eed44b | b34304885d2c600e18689d96042f0ef5f43b468a01e4de5f7714d91969eed44b |
| src/hermes/i18n/de_first_run.ts | verbatim | apps/desktop/src/i18n/de_first_run.ts | 7e118e2fe8fa1c87868389373e085c859c60f92d6b050b028ea97dcf37e13cef | 7e118e2fe8fa1c87868389373e085c859c60f92d6b050b028ea97dcf37e13cef |
| src/hermes/i18n/de_local_models.ts | verbatim | apps/desktop/src/i18n/de_local_models.ts | 666ef52bed3b73412a6e25d69211c49b93f0bc99c29258a95f9875c44d2b4fe6 | 666ef52bed3b73412a6e25d69211c49b93f0bc99c29258a95f9875c44d2b4fe6 |
| src/hermes/i18n/de_model_menu.ts | verbatim | apps/desktop/src/i18n/de_model_menu.ts | a185a93407756049feaa07f64a18b55400eaf9ae85fe299dcf76977906432819 | a185a93407756049feaa07f64a18b55400eaf9ae85fe299dcf76977906432819 |
| src/hermes/i18n/de_model_settings.ts | verbatim | apps/desktop/src/i18n/de_model_settings.ts | b368992281ee1c4771ac9ee99e1aa56eed95aa977db794ae38442577967e720d | b368992281ee1c4771ac9ee99e1aa56eed95aa977db794ae38442577967e720d |
| src/hermes/i18n/de_notices.ts | verbatim | apps/desktop/src/i18n/de_notices.ts | 3def71dc977e4d24f355a7f0f9222cdffe40db0537235fa7efca2e1700395304 | 3def71dc977e4d24f355a7f0f9222cdffe40db0537235fa7efca2e1700395304 |
| src/hermes/i18n/de_notifications.ts | verbatim | apps/desktop/src/i18n/de_notifications.ts | eeea80cda381c830e2ea96dab7dc16618b232ea4204f81b69d5267f60b033c54 | eeea80cda381c830e2ea96dab7dc16618b232ea4204f81b69d5267f60b033c54 |
| src/hermes/i18n/de_onboarding.ts | verbatim | apps/desktop/src/i18n/de_onboarding.ts | 991022f45f5f9d6407adbb553a9298a9f608e364d06939adb7f8b3868920d143 | 991022f45f5f9d6407adbb553a9298a9f608e364d06939adb7f8b3868920d143 |
| src/hermes/i18n/de_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/de_plugin_preview_access.ts | 739de4314f73bbe9f6503d71f7d6506e0579a6eaf8f06c6175377b5cb16f6c5e | 739de4314f73bbe9f6503d71f7d6506e0579a6eaf8f06c6175377b5cb16f6c5e |
| src/hermes/i18n/de_plugin_update.ts | verbatim | apps/desktop/src/i18n/de_plugin_update.ts | 3873dd799edc2dbda2e5d4eb99276d1e219a867622288dfbaf19b95c7cd6405f | 3873dd799edc2dbda2e5d4eb99276d1e219a867622288dfbaf19b95c7cd6405f |
| src/hermes/i18n/de_projects.ts | verbatim | apps/desktop/src/i18n/de_projects.ts | 9ad42258e512e6b57bcae9eedb33ae1d0dd05a3735a9d66403f06f6c43ee1e81 | 9ad42258e512e6b57bcae9eedb33ae1d0dd05a3735a9d66403f06f6c43ee1e81 |
| src/hermes/i18n/de_session_commands.ts | verbatim | apps/desktop/src/i18n/de_session_commands.ts | 3e92c542bf71b6bc73ce3b36f68b4d7aeb082cb3eea64d10e35f7eb4ba83173a | 3e92c542bf71b6bc73ce3b36f68b4d7aeb082cb3eea64d10e35f7eb4ba83173a |
| src/hermes/i18n/de_shared_metrics.ts | verbatim | apps/desktop/src/i18n/de_shared_metrics.ts | e823ec99d3918dab0671e111ff5f9205abb6026d83c91db190c15e40c2acd629 | e823ec99d3918dab0671e111ff5f9205abb6026d83c91db190c15e40c2acd629 |
| src/hermes/i18n/de_theme_picker.ts | verbatim | apps/desktop/src/i18n/de_theme_picker.ts | 3844e78f63d2b585a16ca3ac3eacdcac35ee2345b1cc91bd814c2707a96d7a23 | 3844e78f63d2b585a16ca3ac3eacdcac35ee2345b1cc91bd814c2707a96d7a23 |
| src/hermes/i18n/de_update_channel.ts | verbatim | apps/desktop/src/i18n/de_update_channel.ts | b377886c78d88662106ffa4369f2da51c0b730143331faf51cacc754903cef09 | b377886c78d88662106ffa4369f2da51c0b730143331faf51cacc754903cef09 |
| src/hermes/i18n/define-locale.ts | verbatim | apps/desktop/src/i18n/define-locale.ts | df8a422c2f8282dfcef6729aed5e3de0971c6e2781d8fea228cdf8e7df4df6e4 | df8a422c2f8282dfcef6729aed5e3de0971c6e2781d8fea228cdf8e7df4df6e4 |
| src/hermes/i18n/en.ts | verbatim | apps/desktop/src/i18n/en.ts | db0a08a7afd56f57fa7510c7d44485a591b5cbc997c182c7a2d85649004b8f35 | db0a08a7afd56f57fa7510c7d44485a591b5cbc997c182c7a2d85649004b8f35 |
| src/hermes/i18n/en_app_tour.ts | verbatim | apps/desktop/src/i18n/en_app_tour.ts | 0d43bdf4b733345e6343d12193ad3b75299ed4266de2ad6a47281c5b780b255b | 0d43bdf4b733345e6343d12193ad3b75299ed4266de2ad6a47281c5b780b255b |
| src/hermes/i18n/en_aux_tasks.ts | verbatim | apps/desktop/src/i18n/en_aux_tasks.ts | 4db55edbc80469028c412bf8aedfaf457e53ea80951156232bc766e78635d842 | 4db55edbc80469028c412bf8aedfaf457e53ea80951156232bc766e78635d842 |
| src/hermes/i18n/en_billing.ts | verbatim | apps/desktop/src/i18n/en_billing.ts | 4b8112d707f35aa3e8efb6728c9eda1b3a5831df34a06bd02fbb4a6d61d7709c | 4b8112d707f35aa3e8efb6728c9eda1b3a5831df34a06bd02fbb4a6d61d7709c |
| src/hermes/i18n/en_boot.ts | verbatim | apps/desktop/src/i18n/en_boot.ts | a0af02750cdf0d5790e581b53f894563f9e37069b7ded91d7cfd1de4fb9c1623 | a0af02750cdf0d5790e581b53f894563f9e37069b7ded91d7cfd1de4fb9c1623 |
| src/hermes/i18n/en_catalog.ts | verbatim | apps/desktop/src/i18n/en_catalog.ts | 110a5b886f59871c118a60bf8f9770a5948432cdde278af98352a728611ee664 | 110a5b886f59871c118a60bf8f9770a5948432cdde278af98352a728611ee664 |
| src/hermes/i18n/en_catalog_install.ts | verbatim | apps/desktop/src/i18n/en_catalog_install.ts | b08d9189d5b55645b2edcababd4beb1b64ebcc78cecd9b2be2b0ca166b57fe91 | b08d9189d5b55645b2edcababd4beb1b64ebcc78cecd9b2be2b0ca166b57fe91 |
| src/hermes/i18n/en_clarify.ts | verbatim | apps/desktop/src/i18n/en_clarify.ts | c78dd87851977adb27f9672cd85677ec78b1269426a987ae183248e34f6d988a | c78dd87851977adb27f9672cd85677ec78b1269426a987ae183248e34f6d988a |
| src/hermes/i18n/en_connector_accounts.ts | verbatim | apps/desktop/src/i18n/en_connector_accounts.ts | 46a3f475625de96be38fe314e0cead8cc98c4770e9a995be76ca1278d6547384 | 46a3f475625de96be38fe314e0cead8cc98c4770e9a995be76ca1278d6547384 |
| src/hermes/i18n/en_connector_card.ts | verbatim | apps/desktop/src/i18n/en_connector_card.ts | ad70704b2b0ce9e8ff356111e257960d7bc4e7aa64c96334532bfccd1f3c1855 | ad70704b2b0ce9e8ff356111e257960d7bc4e7aa64c96334532bfccd1f3c1855 |
| src/hermes/i18n/en_deep_links.ts | verbatim | apps/desktop/src/i18n/en_deep_links.ts | 7dde33902a792425178b5cff1ad8c582ac38e46f719ad2a29cceb65445c4ef95 | 7dde33902a792425178b5cff1ad8c582ac38e46f719ad2a29cceb65445c4ef95 |
| src/hermes/i18n/en_delegation_routing.ts | verbatim | apps/desktop/src/i18n/en_delegation_routing.ts | 889c0376dd7de24d0b463405f257249fa66543f765fb7574c2a1c432bb613a89 | 889c0376dd7de24d0b463405f257249fa66543f765fb7574c2a1c432bb613a89 |
| src/hermes/i18n/en_desktop_commands.ts | verbatim | apps/desktop/src/i18n/en_desktop_commands.ts | 4b9896c059428dce666ec12e04d194afce264cf42e92b974537993e72a03238f | 4b9896c059428dce666ec12e04d194afce264cf42e92b974537993e72a03238f |
| src/hermes/i18n/en_error_codes.ts | verbatim | apps/desktop/src/i18n/en_error_codes.ts | bac5c1c7a378b674088f35ce0b6c9b68439841204d35b09ae5e47a3665591b5d | bac5c1c7a378b674088f35ce0b6c9b68439841204d35b09ae5e47a3665591b5d |
| src/hermes/i18n/en_error_layers.ts | verbatim | apps/desktop/src/i18n/en_error_layers.ts | 5f10cb50299f0a176f48732b1680df765a675c280ff166b6d5b9de23f53874ac | 5f10cb50299f0a176f48732b1680df765a675c280ff166b6d5b9de23f53874ac |
| src/hermes/i18n/en_external_open.ts | verbatim | apps/desktop/src/i18n/en_external_open.ts | ddcf9ef30eb670878053b3f9caa26c2de0162e17ccc8d8a50b8ba0bb7351be13 | ddcf9ef30eb670878053b3f9caa26c2de0162e17ccc8d8a50b8ba0bb7351be13 |
| src/hermes/i18n/en_first_run.ts | verbatim | apps/desktop/src/i18n/en_first_run.ts | ca7ff04e85931ee1f9411bf439ba27a907363e3b2645cedabb2526ef8504d2a7 | ca7ff04e85931ee1f9411bf439ba27a907363e3b2645cedabb2526ef8504d2a7 |
| src/hermes/i18n/en_local_models.ts | verbatim | apps/desktop/src/i18n/en_local_models.ts | 8145ce197260a25b89bbc5254ce4074cf26c8454c7c2deadd25048af1e171b2f | 8145ce197260a25b89bbc5254ce4074cf26c8454c7c2deadd25048af1e171b2f |
| src/hermes/i18n/en_model_menu.ts | verbatim | apps/desktop/src/i18n/en_model_menu.ts | 81edb3021c5003cdd7725e3e8811460970fbe74aad720ef330fcbc2031bf3bc3 | 81edb3021c5003cdd7725e3e8811460970fbe74aad720ef330fcbc2031bf3bc3 |
| src/hermes/i18n/en_model_settings.ts | verbatim | apps/desktop/src/i18n/en_model_settings.ts | 60b9bfb1cd6bc105743622ae2627ecee6c0fd71657b095998c3f7db5769fa5ec | 60b9bfb1cd6bc105743622ae2627ecee6c0fd71657b095998c3f7db5769fa5ec |
| src/hermes/i18n/en_notices.ts | verbatim | apps/desktop/src/i18n/en_notices.ts | 767f3339b17c100d909dff99c7eb2c322136a301927a978699e205bf72f2ab37 | 767f3339b17c100d909dff99c7eb2c322136a301927a978699e205bf72f2ab37 |
| src/hermes/i18n/en_notifications.ts | verbatim | apps/desktop/src/i18n/en_notifications.ts | ab15fd933dc1d66789554e8aa95c5793408b7c3363f1a2eb5ebe158f4a17fbda | ab15fd933dc1d66789554e8aa95c5793408b7c3363f1a2eb5ebe158f4a17fbda |
| src/hermes/i18n/en_onboarding.ts | verbatim | apps/desktop/src/i18n/en_onboarding.ts | dc9400785350bab13c0a123b209a05c440aaf6e445294f69e52a69d472b33e16 | dc9400785350bab13c0a123b209a05c440aaf6e445294f69e52a69d472b33e16 |
| src/hermes/i18n/en_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/en_plugin_preview_access.ts | 764d39eae0886b3199681eb51433612dadc0e4bbf60e0a13b0678b3b6161e5fd | 764d39eae0886b3199681eb51433612dadc0e4bbf60e0a13b0678b3b6161e5fd |
| src/hermes/i18n/en_plugin_update.ts | verbatim | apps/desktop/src/i18n/en_plugin_update.ts | cc18c8aec1450698951c00587ab1a961fd48c895d3214f48f57c2fe3da16f855 | cc18c8aec1450698951c00587ab1a961fd48c895d3214f48f57c2fe3da16f855 |
| src/hermes/i18n/en_projects.ts | verbatim | apps/desktop/src/i18n/en_projects.ts | f0e8ea3ebedba56017ca5adfac311fb9210f4ac56413d9a543427cd3329b78f5 | f0e8ea3ebedba56017ca5adfac311fb9210f4ac56413d9a543427cd3329b78f5 |
| src/hermes/i18n/en_session_commands.ts | verbatim | apps/desktop/src/i18n/en_session_commands.ts | 909875a79bafadd7d26312e0d022350e1150ba677145ee4d190cca3167a21908 | 909875a79bafadd7d26312e0d022350e1150ba677145ee4d190cca3167a21908 |
| src/hermes/i18n/en_shared_metrics.ts | verbatim | apps/desktop/src/i18n/en_shared_metrics.ts | 12369e58a9da30595d05385ef20fb57fbd8251f40a12182a20b12d872426cfd9 | 12369e58a9da30595d05385ef20fb57fbd8251f40a12182a20b12d872426cfd9 |
| src/hermes/i18n/en_theme_picker.ts | verbatim | apps/desktop/src/i18n/en_theme_picker.ts | f77391e672b1f3a19ae81c377b079b68117a81dbad210d29011d018e1dfdd3ec | f77391e672b1f3a19ae81c377b079b68117a81dbad210d29011d018e1dfdd3ec |
| src/hermes/i18n/en_uninstall_section.ts | verbatim | apps/desktop/src/i18n/en_uninstall_section.ts | c5e2fa30930ef51552328d461a72a90f2f95b31e706fb7ea03ba4218b749b590 | c5e2fa30930ef51552328d461a72a90f2f95b31e706fb7ea03ba4218b749b590 |
| src/hermes/i18n/en_update_channel.ts | verbatim | apps/desktop/src/i18n/en_update_channel.ts | c2c2bf99c48745266f21a28e6e671244a8a6b7abfd3141c8471aff9c13657c9e | c2c2bf99c48745266f21a28e6e671244a8a6b7abfd3141c8471aff9c13657c9e |
| src/hermes/i18n/es.ts | verbatim | apps/desktop/src/i18n/es.ts | b5bb4a0780f453c604ce9cf9e1bdfe43a191fcc715092132eab4e81447af55bc | b5bb4a0780f453c604ce9cf9e1bdfe43a191fcc715092132eab4e81447af55bc |
| src/hermes/i18n/es_aux_tasks.ts | verbatim | apps/desktop/src/i18n/es_aux_tasks.ts | 17e2b871ac83a6e1bdd18778832745e5dd1131e650eb483680aa33092acc06ac | 17e2b871ac83a6e1bdd18778832745e5dd1131e650eb483680aa33092acc06ac |
| src/hermes/i18n/es_boot.ts | verbatim | apps/desktop/src/i18n/es_boot.ts | dd70ad2ac707946d4c44c5259b95f00262ddbe5f6b1667c0659fbb1eecb15a87 | dd70ad2ac707946d4c44c5259b95f00262ddbe5f6b1667c0659fbb1eecb15a87 |
| src/hermes/i18n/es_clarify.ts | verbatim | apps/desktop/src/i18n/es_clarify.ts | d20b87540d1e6c834cd6bb29bacdcbda5193b0bc38525cb20eef62409178f7f1 | d20b87540d1e6c834cd6bb29bacdcbda5193b0bc38525cb20eef62409178f7f1 |
| src/hermes/i18n/es_connector_accounts.ts | verbatim | apps/desktop/src/i18n/es_connector_accounts.ts | b48f4fe36d722a3f0ad87f7ac06f6770ef300b3df78175a4e26e8c411633b21f | b48f4fe36d722a3f0ad87f7ac06f6770ef300b3df78175a4e26e8c411633b21f |
| src/hermes/i18n/es_connector_card.ts | verbatim | apps/desktop/src/i18n/es_connector_card.ts | 2f26eff80129a35ece7643e964f652a77353361e1c5f523a11ee83a55ff9b69e | 2f26eff80129a35ece7643e964f652a77353361e1c5f523a11ee83a55ff9b69e |
| src/hermes/i18n/es_delegation_routing.ts | verbatim | apps/desktop/src/i18n/es_delegation_routing.ts | f9a97531e78e1dc6b039b8d435d698bae300b04e0d81235739b4787272952117 | f9a97531e78e1dc6b039b8d435d698bae300b04e0d81235739b4787272952117 |
| src/hermes/i18n/es_desktop_commands.ts | verbatim | apps/desktop/src/i18n/es_desktop_commands.ts | ae3383401ba850f5f5e71b716d89aaa0a1fdfe5e12869246460f6e33328c650d | ae3383401ba850f5f5e71b716d89aaa0a1fdfe5e12869246460f6e33328c650d |
| src/hermes/i18n/es_error_layers.ts | verbatim | apps/desktop/src/i18n/es_error_layers.ts | 2493f3a74bee1fc60012044bc76cc2a24eaa7c9094085515a48aeeaeed6d6cba | 2493f3a74bee1fc60012044bc76cc2a24eaa7c9094085515a48aeeaeed6d6cba |
| src/hermes/i18n/es_first_run.ts | verbatim | apps/desktop/src/i18n/es_first_run.ts | 122bffef22fba341361be11fd8437f0742d7a6c0ee6cf284450d5cba92c32c17 | 122bffef22fba341361be11fd8437f0742d7a6c0ee6cf284450d5cba92c32c17 |
| src/hermes/i18n/es_local_models.ts | verbatim | apps/desktop/src/i18n/es_local_models.ts | 6fddec7340339ce74af63e5f3d3ae6bfc43858474759e835ac303f8ea2cdaa9c | 6fddec7340339ce74af63e5f3d3ae6bfc43858474759e835ac303f8ea2cdaa9c |
| src/hermes/i18n/es_model_menu.ts | verbatim | apps/desktop/src/i18n/es_model_menu.ts | bb1cdcfee53391d2bcf208699ad55212c1612a65c226b1a3a9b6db0d03a53caa | bb1cdcfee53391d2bcf208699ad55212c1612a65c226b1a3a9b6db0d03a53caa |
| src/hermes/i18n/es_model_settings.ts | verbatim | apps/desktop/src/i18n/es_model_settings.ts | f94663b772872fd8c9d033c828ffa5abcb8280a296c25b957eea53bf34b0bae5 | f94663b772872fd8c9d033c828ffa5abcb8280a296c25b957eea53bf34b0bae5 |
| src/hermes/i18n/es_notices.ts | verbatim | apps/desktop/src/i18n/es_notices.ts | f451bea69097c1cbf9420e5caa09a320bde8e985213727dd542b909e0dc5754b | f451bea69097c1cbf9420e5caa09a320bde8e985213727dd542b909e0dc5754b |
| src/hermes/i18n/es_notifications.ts | verbatim | apps/desktop/src/i18n/es_notifications.ts | b27418709aeb518d42e5c0c22e16eadbc7457a332cc4e797befab5d3251fccbe | b27418709aeb518d42e5c0c22e16eadbc7457a332cc4e797befab5d3251fccbe |
| src/hermes/i18n/es_onboarding.ts | verbatim | apps/desktop/src/i18n/es_onboarding.ts | 309ff81786e4b3123d138550416f3784a0230caa54008063deccd355d7049e8c | 309ff81786e4b3123d138550416f3784a0230caa54008063deccd355d7049e8c |
| src/hermes/i18n/es_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/es_plugin_preview_access.ts | c8455b2566414790c0d6892953f1218bfbbcba9f714f7f37813afd268ce209c2 | c8455b2566414790c0d6892953f1218bfbbcba9f714f7f37813afd268ce209c2 |
| src/hermes/i18n/es_plugin_update.ts | verbatim | apps/desktop/src/i18n/es_plugin_update.ts | 53622d25fc35427b91b760b4057fe4998faeb90072a3fc858a851f0debc55315 | 53622d25fc35427b91b760b4057fe4998faeb90072a3fc858a851f0debc55315 |
| src/hermes/i18n/es_projects.ts | verbatim | apps/desktop/src/i18n/es_projects.ts | ff664789f8cd5a5eb1441bc153f09a5978484006554e5724be5db6247a0f1f54 | ff664789f8cd5a5eb1441bc153f09a5978484006554e5724be5db6247a0f1f54 |
| src/hermes/i18n/es_session_commands.ts | verbatim | apps/desktop/src/i18n/es_session_commands.ts | d2f21eb2c46b26134dbe51b29ddb6088053b545291b2552255c7f72e36de6883 | d2f21eb2c46b26134dbe51b29ddb6088053b545291b2552255c7f72e36de6883 |
| src/hermes/i18n/es_shared_metrics.ts | verbatim | apps/desktop/src/i18n/es_shared_metrics.ts | 4f3e4871987a31be92c97bda250bd800ec7fc4d473bce46abb92526dcf96cff8 | 4f3e4871987a31be92c97bda250bd800ec7fc4d473bce46abb92526dcf96cff8 |
| src/hermes/i18n/es_theme_picker.ts | verbatim | apps/desktop/src/i18n/es_theme_picker.ts | 568c0790abf6040a957c92a109789d33a63d286e0ade69006cd7a025270d87e9 | 568c0790abf6040a957c92a109789d33a63d286e0ade69006cd7a025270d87e9 |
| src/hermes/i18n/es_update_channel.ts | verbatim | apps/desktop/src/i18n/es_update_channel.ts | f3de329c48b6d7f511a74a336d46eed10f36af9591b6775837846666463a1b49 | f3de329c48b6d7f511a74a336d46eed10f36af9591b6775837846666463a1b49 |
| src/hermes/i18n/fr.ts | verbatim | apps/desktop/src/i18n/fr.ts | 09395001dfc531d70ed44fc6e6ff0d97ac0dcc361a8a4c46877d94b0cddc30cf | 09395001dfc531d70ed44fc6e6ff0d97ac0dcc361a8a4c46877d94b0cddc30cf |
| src/hermes/i18n/fr_aux_tasks.ts | verbatim | apps/desktop/src/i18n/fr_aux_tasks.ts | aabcbfdce57c3d5a3add58c8c22d6b1e10a4d61251a333665d5d05f556923278 | aabcbfdce57c3d5a3add58c8c22d6b1e10a4d61251a333665d5d05f556923278 |
| src/hermes/i18n/fr_boot.ts | verbatim | apps/desktop/src/i18n/fr_boot.ts | 5dfd1bbf5666aaafbaed3bc16114fcea0317abc034357d4b253806b6c99c879a | 5dfd1bbf5666aaafbaed3bc16114fcea0317abc034357d4b253806b6c99c879a |
| src/hermes/i18n/fr_clarify.ts | verbatim | apps/desktop/src/i18n/fr_clarify.ts | fe918e64c3b3b2fa2f87cb12074218e8ce5bda7c8595b382c2591e5ff8290c8c | fe918e64c3b3b2fa2f87cb12074218e8ce5bda7c8595b382c2591e5ff8290c8c |
| src/hermes/i18n/fr_connector_accounts.ts | verbatim | apps/desktop/src/i18n/fr_connector_accounts.ts | 38b9b08f55889323753ac9d3e909cd314490b16b39b7815455877755dd73fb47 | 38b9b08f55889323753ac9d3e909cd314490b16b39b7815455877755dd73fb47 |
| src/hermes/i18n/fr_connector_card.ts | verbatim | apps/desktop/src/i18n/fr_connector_card.ts | 17deffc8f057f2b4ef5f089f87b699916bb9eef3e0ef33383703676d3607268a | 17deffc8f057f2b4ef5f089f87b699916bb9eef3e0ef33383703676d3607268a |
| src/hermes/i18n/fr_delegation_routing.ts | verbatim | apps/desktop/src/i18n/fr_delegation_routing.ts | 378c0f0bd85dd7d4a8c3703199047e902f6886be7e5e84b76b5d58a3aa3495b1 | 378c0f0bd85dd7d4a8c3703199047e902f6886be7e5e84b76b5d58a3aa3495b1 |
| src/hermes/i18n/fr_desktop_commands.ts | verbatim | apps/desktop/src/i18n/fr_desktop_commands.ts | 31cf9f0b1b2fcc1a5b8660057beaf2090bfe4e0d3c3763efbc336867c6753775 | 31cf9f0b1b2fcc1a5b8660057beaf2090bfe4e0d3c3763efbc336867c6753775 |
| src/hermes/i18n/fr_error_layers.ts | verbatim | apps/desktop/src/i18n/fr_error_layers.ts | f3c4daaf9f3cc8a162db9465c1a277a3d2922f3ab9f3f4c278b4e0d9612c29cc | f3c4daaf9f3cc8a162db9465c1a277a3d2922f3ab9f3f4c278b4e0d9612c29cc |
| src/hermes/i18n/fr_first_run.ts | verbatim | apps/desktop/src/i18n/fr_first_run.ts | 78f6c6e1c63ba1461544238604cbe34f7d11d24ae3e423b12c415f5b0efd3439 | 78f6c6e1c63ba1461544238604cbe34f7d11d24ae3e423b12c415f5b0efd3439 |
| src/hermes/i18n/fr_local_models.ts | verbatim | apps/desktop/src/i18n/fr_local_models.ts | 786ddfde7677aacdd2ac523d22842d7793e4736bd9168704f4be926ce63819d9 | 786ddfde7677aacdd2ac523d22842d7793e4736bd9168704f4be926ce63819d9 |
| src/hermes/i18n/fr_model_menu.ts | verbatim | apps/desktop/src/i18n/fr_model_menu.ts | 559e21b630a6130e5da45990b99b07cb9260f0507eab834a7d50ec826f63d25e | 559e21b630a6130e5da45990b99b07cb9260f0507eab834a7d50ec826f63d25e |
| src/hermes/i18n/fr_model_settings.ts | verbatim | apps/desktop/src/i18n/fr_model_settings.ts | 8562c41f4324cd3458453e887b4ba3cd5c8fb842f9dac71af7c75debb9e11b65 | 8562c41f4324cd3458453e887b4ba3cd5c8fb842f9dac71af7c75debb9e11b65 |
| src/hermes/i18n/fr_notices.ts | verbatim | apps/desktop/src/i18n/fr_notices.ts | ef9654db49ed0196a264bcdb6d5e8c870cc177b91bc2cce9e9877ffc795a2db9 | ef9654db49ed0196a264bcdb6d5e8c870cc177b91bc2cce9e9877ffc795a2db9 |
| src/hermes/i18n/fr_notifications.ts | verbatim | apps/desktop/src/i18n/fr_notifications.ts | d7ffc49612a076a74735fbc148c523a68a17ea37b8e33471fd944e5cd9d5b039 | d7ffc49612a076a74735fbc148c523a68a17ea37b8e33471fd944e5cd9d5b039 |
| src/hermes/i18n/fr_onboarding.ts | verbatim | apps/desktop/src/i18n/fr_onboarding.ts | 37a9e2d8dd1c7c1805095cb70a5522801b05a3ceeb864c55c877842ec21aa5ce | 37a9e2d8dd1c7c1805095cb70a5522801b05a3ceeb864c55c877842ec21aa5ce |
| src/hermes/i18n/fr_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/fr_plugin_preview_access.ts | 1e04d7659d7afa8cba733dc315f766d71c20b60691c73ac22c7296720f41a1bd | 1e04d7659d7afa8cba733dc315f766d71c20b60691c73ac22c7296720f41a1bd |
| src/hermes/i18n/fr_plugin_update.ts | verbatim | apps/desktop/src/i18n/fr_plugin_update.ts | 01a88c7d1d707a6b220d86cbedc8af8e02c2521601f1c1c6e089266c7df81052 | 01a88c7d1d707a6b220d86cbedc8af8e02c2521601f1c1c6e089266c7df81052 |
| src/hermes/i18n/fr_projects.ts | verbatim | apps/desktop/src/i18n/fr_projects.ts | 48ee0f6662ba54e471988a2e49a920e090e710e9c771b6bd0a8d4ca3df7f1c1a | 48ee0f6662ba54e471988a2e49a920e090e710e9c771b6bd0a8d4ca3df7f1c1a |
| src/hermes/i18n/fr_session_commands.ts | verbatim | apps/desktop/src/i18n/fr_session_commands.ts | cd4b87157ef995827ee6429a8ecc80bd80667a5215a2e246eba9fae6b3fe1f17 | cd4b87157ef995827ee6429a8ecc80bd80667a5215a2e246eba9fae6b3fe1f17 |
| src/hermes/i18n/fr_shared_metrics.ts | verbatim | apps/desktop/src/i18n/fr_shared_metrics.ts | c768ffd29021d6b49f0dec845c336fd36acb2829deafbf4c03a7f13fc4abd509 | c768ffd29021d6b49f0dec845c336fd36acb2829deafbf4c03a7f13fc4abd509 |
| src/hermes/i18n/fr_theme_picker.ts | verbatim | apps/desktop/src/i18n/fr_theme_picker.ts | d9c19fa1cadb994416039b829e4a92208bd0ae5bb29635a3f24c4e67b12661ee | d9c19fa1cadb994416039b829e4a92208bd0ae5bb29635a3f24c4e67b12661ee |
| src/hermes/i18n/fr_update_channel.ts | verbatim | apps/desktop/src/i18n/fr_update_channel.ts | f55d9a6ca64554d580d191f74ef454853355bb00ffc9519122bc5f335e9fdc67 | f55d9a6ca64554d580d191f74ef454853355bb00ffc9519122bc5f335e9fdc67 |
| src/hermes/i18n/intro-de.ts | verbatim | apps/desktop/src/i18n/intro-de.ts | 3f1fd4dae3aab0dba94073d7a2b76b410b6d1538f6048e6c33da2b61ab57e617 | 3f1fd4dae3aab0dba94073d7a2b76b410b6d1538f6048e6c33da2b61ab57e617 |
| src/hermes/i18n/intro-es.ts | verbatim | apps/desktop/src/i18n/intro-es.ts | ddd18556458d5f26788bffbca02814ded6956dbf5a558c3c4aa5fb3f8726595e | ddd18556458d5f26788bffbca02814ded6956dbf5a558c3c4aa5fb3f8726595e |
| src/hermes/i18n/intro-fr.ts | verbatim | apps/desktop/src/i18n/intro-fr.ts | ee221e3f009f42d677b854c8373c2fbd74926eda81968a888c45e9acdd19c77c | ee221e3f009f42d677b854c8373c2fbd74926eda81968a888c45e9acdd19c77c |
| src/hermes/i18n/intro-ja.ts | verbatim | apps/desktop/src/i18n/intro-ja.ts | 966081d5662e8497fd7d4c78b0da76edaf47085be5c13a74e9dac2e6f5241c3f | 966081d5662e8497fd7d4c78b0da76edaf47085be5c13a74e9dac2e6f5241c3f |
| src/hermes/i18n/intro-zh-hant.ts | verbatim | apps/desktop/src/i18n/intro-zh-hant.ts | 9190c6d2f208e6c3b605b20f37b65f0dec18a1575e79d605606eb8d4390d8297 | 9190c6d2f208e6c3b605b20f37b65f0dec18a1575e79d605606eb8d4390d8297 |
| src/hermes/i18n/intro-zh.ts | verbatim | apps/desktop/src/i18n/intro-zh.ts | b6439265a6ae5fdb8d9efad47a3099283e88a6a2cd322974e941ef2aef507bde | b6439265a6ae5fdb8d9efad47a3099283e88a6a2cd322974e941ef2aef507bde |
| src/hermes/i18n/ja.ts | verbatim | apps/desktop/src/i18n/ja.ts | 4c7c22b8f6bbdb3ad404600410a26964e8e29043dca7a351c03f3853ab2fdf52 | 4c7c22b8f6bbdb3ad404600410a26964e8e29043dca7a351c03f3853ab2fdf52 |
| src/hermes/i18n/ja_aux_tasks.ts | verbatim | apps/desktop/src/i18n/ja_aux_tasks.ts | d86f4e7a34604db6f999e985db1ede0f4de0683c99b5914d20947e1b4086e1e7 | d86f4e7a34604db6f999e985db1ede0f4de0683c99b5914d20947e1b4086e1e7 |
| src/hermes/i18n/ja_boot.ts | verbatim | apps/desktop/src/i18n/ja_boot.ts | 94fdcfc369f1ae4c67a74d6497feac1c7a4a13730286639baefe36938e732e4d | 94fdcfc369f1ae4c67a74d6497feac1c7a4a13730286639baefe36938e732e4d |
| src/hermes/i18n/ja_catalog.ts | verbatim | apps/desktop/src/i18n/ja_catalog.ts | a221bff4fad9da6e18b51e6b124e655bcc11aaeb74564a64ca0de650cd8d670f | a221bff4fad9da6e18b51e6b124e655bcc11aaeb74564a64ca0de650cd8d670f |
| src/hermes/i18n/ja_clarify.ts | verbatim | apps/desktop/src/i18n/ja_clarify.ts | de78725c557c3cc00f1e66965cecb288d66e353fc999510a66ff7424be3311ba | de78725c557c3cc00f1e66965cecb288d66e353fc999510a66ff7424be3311ba |
| src/hermes/i18n/ja_connectors.ts | verbatim | apps/desktop/src/i18n/ja_connectors.ts | 9e8b11701705c0c256e036fd12feb1cdaad13e315779476edd2485010b887e14 | 9e8b11701705c0c256e036fd12feb1cdaad13e315779476edd2485010b887e14 |
| src/hermes/i18n/ja_delegation_routing.ts | verbatim | apps/desktop/src/i18n/ja_delegation_routing.ts | 8e1a67114ec763829cfd3a856037d281093c6b5a2d316abdbcebb361da9b09ef | 8e1a67114ec763829cfd3a856037d281093c6b5a2d316abdbcebb361da9b09ef |
| src/hermes/i18n/ja_desktop_commands.ts | verbatim | apps/desktop/src/i18n/ja_desktop_commands.ts | 8dace14939cd5b9b438a9209cffe67a5c23c2f3d671bde4afa6eed1a99ea66cf | 8dace14939cd5b9b438a9209cffe67a5c23c2f3d671bde4afa6eed1a99ea66cf |
| src/hermes/i18n/ja_error_layers.ts | verbatim | apps/desktop/src/i18n/ja_error_layers.ts | 48504baeca4b30a7e3a149799ebd3fc2e2155c988ee0df52bacd495ed9d25ea8 | 48504baeca4b30a7e3a149799ebd3fc2e2155c988ee0df52bacd495ed9d25ea8 |
| src/hermes/i18n/ja_first_run.ts | verbatim | apps/desktop/src/i18n/ja_first_run.ts | 09e0ab0d1228342294ab6383fad2472bcb8e66301fa9ab0c98a28d7678b10a1e | 09e0ab0d1228342294ab6383fad2472bcb8e66301fa9ab0c98a28d7678b10a1e |
| src/hermes/i18n/ja_local_models.ts | verbatim | apps/desktop/src/i18n/ja_local_models.ts | bfb99a232da9db0a6c228b5419a65465e2c6d7edde527ba55a0fe63b34bfc28a | bfb99a232da9db0a6c228b5419a65465e2c6d7edde527ba55a0fe63b34bfc28a |
| src/hermes/i18n/ja_model_menu.ts | verbatim | apps/desktop/src/i18n/ja_model_menu.ts | 05763b45ee9265988f245c2c2dd609d6324a96e5f006f94238782c104e96ca71 | 05763b45ee9265988f245c2c2dd609d6324a96e5f006f94238782c104e96ca71 |
| src/hermes/i18n/ja_model_settings.ts | verbatim | apps/desktop/src/i18n/ja_model_settings.ts | 665398a56a63f91373446dc786d66cf02edf0d5221a77ecc8996358a8d0e5423 | 665398a56a63f91373446dc786d66cf02edf0d5221a77ecc8996358a8d0e5423 |
| src/hermes/i18n/ja_notices.ts | verbatim | apps/desktop/src/i18n/ja_notices.ts | 0b96d7f6380010650be4a106e03912ec833866442bae6715719aa21ef9853b02 | 0b96d7f6380010650be4a106e03912ec833866442bae6715719aa21ef9853b02 |
| src/hermes/i18n/ja_notifications.ts | verbatim | apps/desktop/src/i18n/ja_notifications.ts | c89b99c0d1fde7addf748937d744ef4e7d2d50aaec414598fed147d384b9415d | c89b99c0d1fde7addf748937d744ef4e7d2d50aaec414598fed147d384b9415d |
| src/hermes/i18n/ja_onboarding.ts | verbatim | apps/desktop/src/i18n/ja_onboarding.ts | 283be0d3e36bb9166d980fde1f65a72ea6b3f481dedf4ab2324ab18efd868fa7 | 283be0d3e36bb9166d980fde1f65a72ea6b3f481dedf4ab2324ab18efd868fa7 |
| src/hermes/i18n/ja_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/ja_plugin_preview_access.ts | e3f5c0f7cd3f2e4ae49f2cf4f0cd15952ad29efcba9764bc9815bb59dfa78cd6 | e3f5c0f7cd3f2e4ae49f2cf4f0cd15952ad29efcba9764bc9815bb59dfa78cd6 |
| src/hermes/i18n/ja_plugins.ts | verbatim | apps/desktop/src/i18n/ja_plugins.ts | eb19ea55e1a8309e9e732cc50723b8b6686c2437a9ee17eef0f8b3d9cb8f5511 | eb19ea55e1a8309e9e732cc50723b8b6686c2437a9ee17eef0f8b3d9cb8f5511 |
| src/hermes/i18n/ja_projects.ts | verbatim | apps/desktop/src/i18n/ja_projects.ts | af247e71b7ee369bdd621e3c3a5dc28591f0ed326131805bca75b836f9195957 | af247e71b7ee369bdd621e3c3a5dc28591f0ed326131805bca75b836f9195957 |
| src/hermes/i18n/ja_session_commands.ts | verbatim | apps/desktop/src/i18n/ja_session_commands.ts | 2985ed9fbf04d6636cb185ce31671f5f1a739bea65804423f6964053159cdb45 | 2985ed9fbf04d6636cb185ce31671f5f1a739bea65804423f6964053159cdb45 |
| src/hermes/i18n/ja_shared_metrics.ts | verbatim | apps/desktop/src/i18n/ja_shared_metrics.ts | 31e183e3c072e01ee6e4bff6fad8ba153f182a5cc366ade8ec210dbefd07ca93 | 31e183e3c072e01ee6e4bff6fad8ba153f182a5cc366ade8ec210dbefd07ca93 |
| src/hermes/i18n/ja_theme_picker.ts | verbatim | apps/desktop/src/i18n/ja_theme_picker.ts | 7be36c5ac05aaf9b1913bdae1d4d49f1da72ebee0c6bb04abd318d25bc5fe07e | 7be36c5ac05aaf9b1913bdae1d4d49f1da72ebee0c6bb04abd318d25bc5fe07e |
| src/hermes/i18n/ja_update_channel.ts | verbatim | apps/desktop/src/i18n/ja_update_channel.ts | f5df2df76db436538bcfb79c3c1a21e0849daa2655d350f97c565f88f2c4f507 | f5df2df76db436538bcfb79c3c1a21e0849daa2655d350f97c565f88f2c4f507 |
| src/hermes/i18n/languages.test.ts | verbatim | apps/desktop/src/i18n/languages.test.ts | c2c7f35f3a6d5c1acd39a550e0c8a4278b57ee4ce7db8f49409d50c230c77773 | c2c7f35f3a6d5c1acd39a550e0c8a4278b57ee4ce7db8f49409d50c230c77773 |
| src/hermes/i18n/languages.ts | verbatim | apps/desktop/src/i18n/languages.ts | 62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b | 62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b |
| src/hermes/i18n/localized-tab-title.tsx | verbatim | apps/desktop/src/i18n/localized-tab-title.tsx | a05209129451ebc1379ca2cb3e77d3517f41f61e2f562a2f3875cc5a81c764a5 | a05209129451ebc1379ca2cb3e77d3517f41f61e2f562a2f3875cc5a81c764a5 |
| src/hermes/i18n/plugin-i18n.test.tsx | verbatim | apps/desktop/src/i18n/plugin-i18n.test.tsx | 352740cb6163d1ab517ef8ccbf4f2269b75c2e65dcf88100dc934c6d5ca80b17 | 352740cb6163d1ab517ef8ccbf4f2269b75c2e65dcf88100dc934c6d5ca80b17 |
| src/hermes/i18n/plugin-i18n.ts | verbatim | apps/desktop/src/i18n/plugin-i18n.ts | 196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf | 196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf |
| src/hermes/i18n/registry.test.ts | verbatim | apps/desktop/src/i18n/registry.test.ts | a616c40950aea5db9dabbbab6e3a3d7ad79ee606b144ac9abddcf7eba11ac5b8 | a616c40950aea5db9dabbbab6e3a3d7ad79ee606b144ac9abddcf7eba11ac5b8 |
| src/hermes/i18n/registry.ts | verbatim | apps/desktop/src/i18n/registry.ts | 67ecf7edeb165f961362c2699fdae848d4749ffb0f6825986ddf7a49a154a0fe | 67ecf7edeb165f961362c2699fdae848d4749ffb0f6825986ddf7a49a154a0fe |
| src/hermes/i18n/ru.ts | verbatim | apps/desktop/src/i18n/ru.ts | a1536e83e21d130f01134e181462b6b57f8b808e7e48f5073727405b2135ea52 | a1536e83e21d130f01134e181462b6b57f8b808e7e48f5073727405b2135ea52 |
| src/hermes/i18n/ru_aux_tasks.ts | verbatim | apps/desktop/src/i18n/ru_aux_tasks.ts | f8ae61e2836ab09c477f2078e85611dcb881e61b4667bbe7234f67aff2033f1b | f8ae61e2836ab09c477f2078e85611dcb881e61b4667bbe7234f67aff2033f1b |
| src/hermes/i18n/ru_boot.ts | verbatim | apps/desktop/src/i18n/ru_boot.ts | 537715156f5e71462fa574a70fb749853a151d402ce2ecca2cfbc6673119cb3d | 537715156f5e71462fa574a70fb749853a151d402ce2ecca2cfbc6673119cb3d |
| src/hermes/i18n/ru_catalog.ts | verbatim | apps/desktop/src/i18n/ru_catalog.ts | a63dc02d40d8cc8a8239875d73130f20889ced90976da1a504768e7058fadcb8 | a63dc02d40d8cc8a8239875d73130f20889ced90976da1a504768e7058fadcb8 |
| src/hermes/i18n/ru_clarify.ts | verbatim | apps/desktop/src/i18n/ru_clarify.ts | be4469113c05ffc8a696a7edd2e167a9a6a11d9b036922f8cd3d8952c7e3b183 | be4469113c05ffc8a696a7edd2e167a9a6a11d9b036922f8cd3d8952c7e3b183 |
| src/hermes/i18n/ru_connectors.ts | verbatim | apps/desktop/src/i18n/ru_connectors.ts | 5481c278111cb346b5d91850dbe04603c3d12b07515101e52b2e2adbac9a23bb | 5481c278111cb346b5d91850dbe04603c3d12b07515101e52b2e2adbac9a23bb |
| src/hermes/i18n/ru_delegation_routing.ts | verbatim | apps/desktop/src/i18n/ru_delegation_routing.ts | 1fead4d82f5d93536f86a64f713f59bbe9df2114a59b21d547e0803c260a0a9c | 1fead4d82f5d93536f86a64f713f59bbe9df2114a59b21d547e0803c260a0a9c |
| src/hermes/i18n/ru_desktop_commands.ts | verbatim | apps/desktop/src/i18n/ru_desktop_commands.ts | e6982af5bd05f1cbfbcbfb7077aeed290b46982f13e52b7bd0e09bcd3a907d7c | e6982af5bd05f1cbfbcbfb7077aeed290b46982f13e52b7bd0e09bcd3a907d7c |
| src/hermes/i18n/ru_error_codes.ts | verbatim | apps/desktop/src/i18n/ru_error_codes.ts | d8c728a52855d4a7ef5c5dc22a788fa198c16cb9aa133a6c5384442606bdd953 | d8c728a52855d4a7ef5c5dc22a788fa198c16cb9aa133a6c5384442606bdd953 |
| src/hermes/i18n/ru_first_run.ts | verbatim | apps/desktop/src/i18n/ru_first_run.ts | 5163acc3c574da779b040bebca0cb9702c7a58f0b7c5a69a2b39d15597dd9008 | 5163acc3c574da779b040bebca0cb9702c7a58f0b7c5a69a2b39d15597dd9008 |
| src/hermes/i18n/ru_model_menu.ts | verbatim | apps/desktop/src/i18n/ru_model_menu.ts | 08edbae2d46316d7fe439c3bc615db288fdc2a5f129bab08e101d178f75aadab | 08edbae2d46316d7fe439c3bc615db288fdc2a5f129bab08e101d178f75aadab |
| src/hermes/i18n/ru_model_settings.ts | verbatim | apps/desktop/src/i18n/ru_model_settings.ts | a91c910b15808463c2a174006ab1c35906975d82b0b21d7211ffcbb3486cf7fc | a91c910b15808463c2a174006ab1c35906975d82b0b21d7211ffcbb3486cf7fc |
| src/hermes/i18n/ru_notices.ts | verbatim | apps/desktop/src/i18n/ru_notices.ts | b64886a6d2445276437ea071824050107be0119ec8e1078ec891601ec4463b9a | b64886a6d2445276437ea071824050107be0119ec8e1078ec891601ec4463b9a |
| src/hermes/i18n/ru_notifications.ts | verbatim | apps/desktop/src/i18n/ru_notifications.ts | 4aeb3fdb273280436ea810e1d131626d77295fe1c4d31edf011fa7c56cbbd065 | 4aeb3fdb273280436ea810e1d131626d77295fe1c4d31edf011fa7c56cbbd065 |
| src/hermes/i18n/ru_onboarding.ts | verbatim | apps/desktop/src/i18n/ru_onboarding.ts | 9f92cb6913f002f7833a9588811a98bcea39038fcab319bbdc83a3185ed68209 | 9f92cb6913f002f7833a9588811a98bcea39038fcab319bbdc83a3185ed68209 |
| src/hermes/i18n/ru_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/ru_plugin_preview_access.ts | eecaa1157b55ee4855e55cde3cbbd233e259eb1bc15e64efe83e942568e9bacd | eecaa1157b55ee4855e55cde3cbbd233e259eb1bc15e64efe83e942568e9bacd |
| src/hermes/i18n/ru_plugins.ts | verbatim | apps/desktop/src/i18n/ru_plugins.ts | 4b72a1d25e3e3c6b1a6da3e9f3672139442e6a6918333f48a09018c357f6cd08 | 4b72a1d25e3e3c6b1a6da3e9f3672139442e6a6918333f48a09018c357f6cd08 |
| src/hermes/i18n/ru_projects.ts | verbatim | apps/desktop/src/i18n/ru_projects.ts | c3d73414e49527f44073c495676b43d79e6b8656f731b3b7862423eaea2d245f | c3d73414e49527f44073c495676b43d79e6b8656f731b3b7862423eaea2d245f |
| src/hermes/i18n/ru_session_commands.ts | verbatim | apps/desktop/src/i18n/ru_session_commands.ts | 67e70e1d10ea8a2fe0d0cb9fbacd6179988869a7effff86e8ba08762ac55fec7 | 67e70e1d10ea8a2fe0d0cb9fbacd6179988869a7effff86e8ba08762ac55fec7 |
| src/hermes/i18n/ru_shared_metrics.ts | verbatim | apps/desktop/src/i18n/ru_shared_metrics.ts | 9c580ec937cb6e4146721a92757fa47af3c5db63b5d9befaadfbe0668f87dff7 | 9c580ec937cb6e4146721a92757fa47af3c5db63b5d9befaadfbe0668f87dff7 |
| src/hermes/i18n/ru_theme_picker.ts | verbatim | apps/desktop/src/i18n/ru_theme_picker.ts | bd6e6b82f6cd62c63507e6f8ff3b8422f80742015106d35f39fe7c4c98ee47e1 | bd6e6b82f6cd62c63507e6f8ff3b8422f80742015106d35f39fe7c4c98ee47e1 |
| src/hermes/i18n/ru_update_channel.ts | verbatim | apps/desktop/src/i18n/ru_update_channel.ts | 515acb9e3eb4784c1666c48b203ff6e606ddd61e596110d45fa3bf8b9171cf67 | 515acb9e3eb4784c1666c48b203ff6e606ddd61e596110d45fa3bf8b9171cf67 |
| src/hermes/i18n/runtime.test.ts | verbatim | apps/desktop/src/i18n/runtime.test.ts | 510022deebb3db7c410ab1c3de5117a24f19a411a5c2b27b0608c3f44f868fa6 | 510022deebb3db7c410ab1c3de5117a24f19a411a5c2b27b0608c3f44f868fa6 |
| src/hermes/i18n/runtime.ts | verbatim | apps/desktop/src/i18n/runtime.ts | 2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f | 2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f |
| src/hermes/i18n/types.ts | verbatim | apps/desktop/src/i18n/types.ts | 4c689011c27b01b7eb16df24954704ac10258287a5da39ac67d205147ac1099d | 4c689011c27b01b7eb16df24954704ac10258287a5da39ac67d205147ac1099d |
| src/hermes/i18n/types_app_tour.ts | verbatim | apps/desktop/src/i18n/types_app_tour.ts | 7baa8b0684c870d1bd232095ab786557d20600e200638bb00652bf1c32452352 | 7baa8b0684c870d1bd232095ab786557d20600e200638bb00652bf1c32452352 |
| src/hermes/i18n/types_aux_tasks.ts | verbatim | apps/desktop/src/i18n/types_aux_tasks.ts | c21b127ea3f420ee0b1cafe95c4e8ebea3d1c9100902d162f4a16ed3fd67fd03 | c21b127ea3f420ee0b1cafe95c4e8ebea3d1c9100902d162f4a16ed3fd67fd03 |
| src/hermes/i18n/types_billing.ts | verbatim | apps/desktop/src/i18n/types_billing.ts | 7aa6c6ce74f66a305a4600eeb592b09978f03aabbb4d479a543c010295e32a65 | 7aa6c6ce74f66a305a4600eeb592b09978f03aabbb4d479a543c010295e32a65 |
| src/hermes/i18n/types_boot.ts | verbatim | apps/desktop/src/i18n/types_boot.ts | 64549f95f85af894e0b7cca2bfbbd8ce68b2981190f7b7078b6e7c649e450d0d | 64549f95f85af894e0b7cca2bfbbd8ce68b2981190f7b7078b6e7c649e450d0d |
| src/hermes/i18n/types_catalog.ts | verbatim | apps/desktop/src/i18n/types_catalog.ts | ea1aeb4534f4bfb571213b907390f2d4cd67ea27221ef492c199a9bea220722b | ea1aeb4534f4bfb571213b907390f2d4cd67ea27221ef492c199a9bea220722b |
| src/hermes/i18n/types_catalog_install.ts | verbatim | apps/desktop/src/i18n/types_catalog_install.ts | 2a7e97b9ab418adfe4269e7e3ac0f9a350c480feccfc6c27b7fa0edcac0556e4 | 2a7e97b9ab418adfe4269e7e3ac0f9a350c480feccfc6c27b7fa0edcac0556e4 |
| src/hermes/i18n/types_clarify.ts | verbatim | apps/desktop/src/i18n/types_clarify.ts | aa4aba047971a49f6d2c04d5eb1cacae732e433f78fbe95fd61136202ddcb239 | aa4aba047971a49f6d2c04d5eb1cacae732e433f78fbe95fd61136202ddcb239 |
| src/hermes/i18n/types_connectors.ts | verbatim | apps/desktop/src/i18n/types_connectors.ts | 4008856fb736e3038ea744c0e2d7500f19fbb8eec0724fb036ce71881fb9f516 | 4008856fb736e3038ea744c0e2d7500f19fbb8eec0724fb036ce71881fb9f516 |
| src/hermes/i18n/types_deep_links.ts | verbatim | apps/desktop/src/i18n/types_deep_links.ts | 449ea2fe45a50892e59fffd440295b91dd6a380defc5f47a3e8193247510fa8d | 449ea2fe45a50892e59fffd440295b91dd6a380defc5f47a3e8193247510fa8d |
| src/hermes/i18n/types_delegation_routing.ts | verbatim | apps/desktop/src/i18n/types_delegation_routing.ts | e17cef71f17e0968b1c3540a3e7f9aa4afc29ef97d5dc9f305ef90ab37e9f26a | e17cef71f17e0968b1c3540a3e7f9aa4afc29ef97d5dc9f305ef90ab37e9f26a |
| src/hermes/i18n/types_desktop_commands.ts | verbatim | apps/desktop/src/i18n/types_desktop_commands.ts | 3ac74d06dd30f0305c889329f8bf0966a18c6efca70be15258700983f232a733 | 3ac74d06dd30f0305c889329f8bf0966a18c6efca70be15258700983f232a733 |
| src/hermes/i18n/types_external_open.ts | verbatim | apps/desktop/src/i18n/types_external_open.ts | 0ac018a3dbfdb9e0692dd98ee18d5166465151e3f86fce5482fbd76eee7891f4 | 0ac018a3dbfdb9e0692dd98ee18d5166465151e3f86fce5482fbd76eee7891f4 |
| src/hermes/i18n/types_first_run.ts | verbatim | apps/desktop/src/i18n/types_first_run.ts | 8fa6ad6616e10ce4a00dc9e9585b61548347d2f94076422a9e0fb1a4f0cb646e | 8fa6ad6616e10ce4a00dc9e9585b61548347d2f94076422a9e0fb1a4f0cb646e |
| src/hermes/i18n/types_model_menu.ts | verbatim | apps/desktop/src/i18n/types_model_menu.ts | f6dd54be67ac82bd2683b49c2da802000959f105ed85a00da9c9d6aca493559d | f6dd54be67ac82bd2683b49c2da802000959f105ed85a00da9c9d6aca493559d |
| src/hermes/i18n/types_model_settings.ts | verbatim | apps/desktop/src/i18n/types_model_settings.ts | 91b827e8f7ea6460aea00fc23fa5ce514b5fdd5efadb4fb921afbeae697c5e52 | 91b827e8f7ea6460aea00fc23fa5ce514b5fdd5efadb4fb921afbeae697c5e52 |
| src/hermes/i18n/types_notices.ts | verbatim | apps/desktop/src/i18n/types_notices.ts | a9299cfc95c137d932c43bfe7b18b66c7d8092dc349ef3cb9eb492adced15a8a | a9299cfc95c137d932c43bfe7b18b66c7d8092dc349ef3cb9eb492adced15a8a |
| src/hermes/i18n/types_notifications.ts | verbatim | apps/desktop/src/i18n/types_notifications.ts | 95bb0945d4d2c8e13be3a6baad43ee3a1958e4a289fd98b15a3a03efb6a5e790 | 95bb0945d4d2c8e13be3a6baad43ee3a1958e4a289fd98b15a3a03efb6a5e790 |
| src/hermes/i18n/types_onboarding.ts | verbatim | apps/desktop/src/i18n/types_onboarding.ts | f43f19589f32b7df052d1fed8448ac25652270ff80bcd2df23d60d46606caed1 | f43f19589f32b7df052d1fed8448ac25652270ff80bcd2df23d60d46606caed1 |
| src/hermes/i18n/types_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/types_plugin_preview_access.ts | 8b058a0a3090a4eedc9b5e96293cf585982fd38007056f11f4d05f441c8d5002 | 8b058a0a3090a4eedc9b5e96293cf585982fd38007056f11f4d05f441c8d5002 |
| src/hermes/i18n/types_plugin_update.ts | verbatim | apps/desktop/src/i18n/types_plugin_update.ts | 0712362f933edc6d4ce75e919416ae2143538d98e38dc46483649da6bcc85ef0 | 0712362f933edc6d4ce75e919416ae2143538d98e38dc46483649da6bcc85ef0 |
| src/hermes/i18n/types_projects.ts | verbatim | apps/desktop/src/i18n/types_projects.ts | 5ce6d290343a530f1fb3b7e34675b00d21ca71cd4d180bed871d1c0f63500f0b | 5ce6d290343a530f1fb3b7e34675b00d21ca71cd4d180bed871d1c0f63500f0b |
| src/hermes/i18n/types_shared_metrics.ts | verbatim | apps/desktop/src/i18n/types_shared_metrics.ts | b7152a7c970db5d09d0bc21be760e76f5cd2d86ad09304151ac5dda981248ae9 | b7152a7c970db5d09d0bc21be760e76f5cd2d86ad09304151ac5dda981248ae9 |
| src/hermes/i18n/types_theme_picker.ts | verbatim | apps/desktop/src/i18n/types_theme_picker.ts | 3815558ab794bdfd8cea4e5277cd39293dd0bd34ef7e6f1ed8ac9216b9233b69 | 3815558ab794bdfd8cea4e5277cd39293dd0bd34ef7e6f1ed8ac9216b9233b69 |
| src/hermes/i18n/types_uninstall_section.ts | verbatim | apps/desktop/src/i18n/types_uninstall_section.ts | 08f01cf63d5a627b03c7d9fa63678022fd5e947839db21aca8418bf7778aedf6 | 08f01cf63d5a627b03c7d9fa63678022fd5e947839db21aca8418bf7778aedf6 |
| src/hermes/i18n/types_update_channel.ts | verbatim | apps/desktop/src/i18n/types_update_channel.ts | e3e5fe046dcaa5ee0471c206132e92180ce1bdf7080fb8d0581ea03a67efa139 | e3e5fe046dcaa5ee0471c206132e92180ce1bdf7080fb8d0581ea03a67efa139 |
| src/hermes/i18n/zh-hant.ts | verbatim | apps/desktop/src/i18n/zh-hant.ts | e094e5d0d5ff54cf828cd6c644a1e576ce88b16e0717a80f88e49f53ff884ead | e094e5d0d5ff54cf828cd6c644a1e576ce88b16e0717a80f88e49f53ff884ead |
| src/hermes/i18n/zh-hant_artifacts.ts | verbatim | apps/desktop/src/i18n/zh-hant_artifacts.ts | ee6725df356ec8830d8fb42502ced09b194ff4095c0691124da16f4ad41be359 | ee6725df356ec8830d8fb42502ced09b194ff4095c0691124da16f4ad41be359 |
| src/hermes/i18n/zh-hant_assistant.ts | verbatim | apps/desktop/src/i18n/zh-hant_assistant.ts | 5b39548123f7a3f30d8d7810baaa16c34745aa91263dbf6a83b4ae84e68ea0a5 | 5b39548123f7a3f30d8d7810baaa16c34745aa91263dbf6a83b4ae84e68ea0a5 |
| src/hermes/i18n/zh-hant_boot.ts | verbatim | apps/desktop/src/i18n/zh-hant_boot.ts | 6b7bac54d81ff15e46d4eef9c8e8dd27fd9dc1c49acf6f965dbf270c4e047564 | 6b7bac54d81ff15e46d4eef9c8e8dd27fd9dc1c49acf6f965dbf270c4e047564 |
| src/hermes/i18n/zh-hant_capabilities.ts | verbatim | apps/desktop/src/i18n/zh-hant_capabilities.ts | b866233209517d239fa4fbf5e73b53abb876da0eea21a8a91b1e50ba441d3568 | b866233209517d239fa4fbf5e73b53abb876da0eea21a8a91b1e50ba441d3568 |
| src/hermes/i18n/zh-hant_chat.ts | verbatim | apps/desktop/src/i18n/zh-hant_chat.ts | c2b50eb8046b39173ac08b7ae5258a2f1f5678c11b0ef461831efe60c8dcf0b6 | c2b50eb8046b39173ac08b7ae5258a2f1f5678c11b0ef461831efe60c8dcf0b6 |
| src/hermes/i18n/zh-hant_chrome.ts | verbatim | apps/desktop/src/i18n/zh-hant_chrome.ts | 8785f4c8fc8837de1973fd6fabc119f3fa6f872fb0475b249e85d30c5891187e | 8785f4c8fc8837de1973fd6fabc119f3fa6f872fb0475b249e85d30c5891187e |
| src/hermes/i18n/zh-hant_command_center.ts | verbatim | apps/desktop/src/i18n/zh-hant_command_center.ts | b9342edb1577bcb96cd4d870ba6102297b7dbdc38bc6e5d8f212f384e50fb3eb | b9342edb1577bcb96cd4d870ba6102297b7dbdc38bc6e5d8f212f384e50fb3eb |
| src/hermes/i18n/zh-hant_common.ts | verbatim | apps/desktop/src/i18n/zh-hant_common.ts | 154236dccae627f0061b212b952308bf861ff1e6b2925daa1e20ccf179f124f3 | 154236dccae627f0061b212b952308bf861ff1e6b2925daa1e20ccf179f124f3 |
| src/hermes/i18n/zh-hant_connectors.ts | verbatim | apps/desktop/src/i18n/zh-hant_connectors.ts | 0ad579d5d63d9e8ef685c869374df93e894b15edac3411da3d153f32be3c463f | 0ad579d5d63d9e8ef685c869374df93e894b15edac3411da3d153f32be3c463f |
| src/hermes/i18n/zh-hant_delegation_routing.ts | verbatim | apps/desktop/src/i18n/zh-hant_delegation_routing.ts | 11ac14f6af7c8e3aab16735f4fa84821fecf23c2c2330f0567539faec85d2832 | 11ac14f6af7c8e3aab16735f4fa84821fecf23c2c2330f0567539faec85d2832 |
| src/hermes/i18n/zh-hant_desktop_commands.ts | verbatim | apps/desktop/src/i18n/zh-hant_desktop_commands.ts | b85270c8471a17aa0284c3adc28d319e89b36f82e751a0864d434330649d0c72 | b85270c8471a17aa0284c3adc28d319e89b36f82e751a0864d434330649d0c72 |
| src/hermes/i18n/zh-hant_diagnostics.ts | verbatim | apps/desktop/src/i18n/zh-hant_diagnostics.ts | 0d58b7cdac6b408c0ce7486d6df2fcbd56bbdaa974dd69fdb935f97a3fb730bd | 0d58b7cdac6b408c0ce7486d6df2fcbd56bbdaa974dd69fdb935f97a3fb730bd |
| src/hermes/i18n/zh-hant_first_run.ts | verbatim | apps/desktop/src/i18n/zh-hant_first_run.ts | f88c1ed9e9b5437bab6a447f8fa12fc7aed2d31b630f0c8e290f970f09d8ac38 | f88c1ed9e9b5437bab6a447f8fa12fc7aed2d31b630f0c8e290f970f09d8ac38 |
| src/hermes/i18n/zh-hant_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/zh-hant_plugin_preview_access.ts | 04ed1d560d870273fc6d596307f457bee5035c7f68b0a257a6e18e2d9c265554 | 04ed1d560d870273fc6d596307f457bee5035c7f68b0a257a6e18e2d9c265554 |
| src/hermes/i18n/zh-hant_session_commands.ts | verbatim | apps/desktop/src/i18n/zh-hant_session_commands.ts | ba8829af5c9f3a62203a8ced8910a9fd6331b0ed20dcf69879ad10deb704f811 | ba8829af5c9f3a62203a8ced8910a9fd6331b0ed20dcf69879ad10deb704f811 |
| src/hermes/i18n/zh-hant_settings.ts | verbatim | apps/desktop/src/i18n/zh-hant_settings.ts | 780135de253a3a4f41cbf4fa14902f566e3b000844c7e824c50dd6974ca34a33 | 780135de253a3a4f41cbf4fa14902f566e3b000844c7e824c50dd6974ca34a33 |
| src/hermes/i18n/zh-hant_theme_picker.ts | verbatim | apps/desktop/src/i18n/zh-hant_theme_picker.ts | a3b9ed4ac5ffc0384530fb802dc6b1de54ebdde0fd1e1590e4072015584d08da | a3b9ed4ac5ffc0384530fb802dc6b1de54ebdde0fd1e1590e4072015584d08da |
| src/hermes/i18n/zh-hant_update_channel.ts | verbatim | apps/desktop/src/i18n/zh-hant_update_channel.ts | be1d87c42b9af8a8b090101e7c63653e2858f509cd5ab56ac6ccd41076d839e3 | be1d87c42b9af8a8b090101e7c63653e2858f509cd5ab56ac6ccd41076d839e3 |
| src/hermes/i18n/zh.ts | verbatim | apps/desktop/src/i18n/zh.ts | 8b921de7da816ca13bb9a2a37c60ca1334ae965aca212c2bc3393e3f34a39370 | 8b921de7da816ca13bb9a2a37c60ca1334ae965aca212c2bc3393e3f34a39370 |
| src/hermes/i18n/zh_aux_tasks.ts | verbatim | apps/desktop/src/i18n/zh_aux_tasks.ts | 52e354ab4d0c74ce7becdea4dec854774932cd8be3f02a3dcd29d7d7fbdc570e | 52e354ab4d0c74ce7becdea4dec854774932cd8be3f02a3dcd29d7d7fbdc570e |
| src/hermes/i18n/zh_catalog.ts | verbatim | apps/desktop/src/i18n/zh_catalog.ts | ff85ce02281534d8a38af821ed13297f252c551ea806e751361596b5f65150ce | ff85ce02281534d8a38af821ed13297f252c551ea806e751361596b5f65150ce |
| src/hermes/i18n/zh_clarify.ts | verbatim | apps/desktop/src/i18n/zh_clarify.ts | 7da605924646c57ad66bc3688608ff17a944ea8d506f4753ae0505c7ad94f3a7 | 7da605924646c57ad66bc3688608ff17a944ea8d506f4753ae0505c7ad94f3a7 |
| src/hermes/i18n/zh_connector_card.ts | verbatim | apps/desktop/src/i18n/zh_connector_card.ts | 38efb06320ad6feea09b736e1f75470e43e5edeff179b0c2833abb8af16430da | 38efb06320ad6feea09b736e1f75470e43e5edeff179b0c2833abb8af16430da |
| src/hermes/i18n/zh_delegation_routing.ts | verbatim | apps/desktop/src/i18n/zh_delegation_routing.ts | bedc57b15ba598eaaceff310cb7abd462d0e56cebb57dcaa00b3944e09512835 | bedc57b15ba598eaaceff310cb7abd462d0e56cebb57dcaa00b3944e09512835 |
| src/hermes/i18n/zh_desktop_commands.ts | verbatim | apps/desktop/src/i18n/zh_desktop_commands.ts | d6b3494692735a1465561f3a4f1889bd4d96147b07a06eba21d3b09ff914524f | d6b3494692735a1465561f3a4f1889bd4d96147b07a06eba21d3b09ff914524f |
| src/hermes/i18n/zh_error_layers.ts | verbatim | apps/desktop/src/i18n/zh_error_layers.ts | ac69b1f18a62d42747af48c1d049699bed94596546c039dc1124d2ee229362d4 | ac69b1f18a62d42747af48c1d049699bed94596546c039dc1124d2ee229362d4 |
| src/hermes/i18n/zh_first_run.ts | verbatim | apps/desktop/src/i18n/zh_first_run.ts | 1be3941a61c45f773684c2419962d2f5f9176388b85f1d10981f6437b1db28e8 | 1be3941a61c45f773684c2419962d2f5f9176388b85f1d10981f6437b1db28e8 |
| src/hermes/i18n/zh_local_models.ts | verbatim | apps/desktop/src/i18n/zh_local_models.ts | f04a37cd58fbec0b8035b11937cc3ae1694d40574d5f4a27da298f02f4cfafd1 | f04a37cd58fbec0b8035b11937cc3ae1694d40574d5f4a27da298f02f4cfafd1 |
| src/hermes/i18n/zh_model_menu.ts | verbatim | apps/desktop/src/i18n/zh_model_menu.ts | d40c6075d238e6ee75b159ecbf28c9334784b547b6472deba2a028417994e6a8 | d40c6075d238e6ee75b159ecbf28c9334784b547b6472deba2a028417994e6a8 |
| src/hermes/i18n/zh_model_settings.ts | verbatim | apps/desktop/src/i18n/zh_model_settings.ts | 2960e3f844224253f1c26e721773d8352dadf42630ac66199ce85e208197c290 | 2960e3f844224253f1c26e721773d8352dadf42630ac66199ce85e208197c290 |
| src/hermes/i18n/zh_notices.ts | verbatim | apps/desktop/src/i18n/zh_notices.ts | 1261f0233bd8eacca1607766ea226d6e15f319f845c1bf669fc8717182954a98 | 1261f0233bd8eacca1607766ea226d6e15f319f845c1bf669fc8717182954a98 |
| src/hermes/i18n/zh_notifications.ts | verbatim | apps/desktop/src/i18n/zh_notifications.ts | effee79cdbc44be817308340131b381aff05d1df99ad80d46bc6c75f7a269925 | effee79cdbc44be817308340131b381aff05d1df99ad80d46bc6c75f7a269925 |
| src/hermes/i18n/zh_onboarding.ts | verbatim | apps/desktop/src/i18n/zh_onboarding.ts | ee3de9c10f2c5ea881269ba05977686d071dbf123ac0c488fefebc74001160dd | ee3de9c10f2c5ea881269ba05977686d071dbf123ac0c488fefebc74001160dd |
| src/hermes/i18n/zh_plugin_preview_access.ts | verbatim | apps/desktop/src/i18n/zh_plugin_preview_access.ts | 152949cc623a898f42a22ab6ec1f576714fb247604bdda2b793054f9477e75e3 | 152949cc623a898f42a22ab6ec1f576714fb247604bdda2b793054f9477e75e3 |
| src/hermes/i18n/zh_plugin_update.ts | verbatim | apps/desktop/src/i18n/zh_plugin_update.ts | 68842366a41f25489ccd7e7f52da095c9aae8e58569a2440f7954846b7ded0ae | 68842366a41f25489ccd7e7f52da095c9aae8e58569a2440f7954846b7ded0ae |
| src/hermes/i18n/zh_projects.ts | verbatim | apps/desktop/src/i18n/zh_projects.ts | 2c8024a9cd2ead72e3470a3604536edd6c29a221971f0847da05c5cd965da856 | 2c8024a9cd2ead72e3470a3604536edd6c29a221971f0847da05c5cd965da856 |
| src/hermes/i18n/zh_session_commands.ts | verbatim | apps/desktop/src/i18n/zh_session_commands.ts | 9ac8cf40700b56b3bd73819e85d1fb335630e1a08bee836dfc8ee46b94d9e25a | 9ac8cf40700b56b3bd73819e85d1fb335630e1a08bee836dfc8ee46b94d9e25a |
| src/hermes/i18n/zh_shared_metrics.ts | verbatim | apps/desktop/src/i18n/zh_shared_metrics.ts | 78f2b06e37672fd112710645900bcf38981dd3551431a2363ceae750568a9335 | 78f2b06e37672fd112710645900bcf38981dd3551431a2363ceae750568a9335 |
| src/hermes/i18n/zh_theme_picker.ts | verbatim | apps/desktop/src/i18n/zh_theme_picker.ts | b354ef974a4935ce729ef42c3a97937f6002801b40dadda413765896e242a50a | b354ef974a4935ce729ef42c3a97937f6002801b40dadda413765896e242a50a |
| src/hermes/i18n/zh_update_channel.ts | verbatim | apps/desktop/src/i18n/zh_update_channel.ts | 0c25c5e4791f300b04101c258e8c5f2c48d23dfcb9ed3a767604cc096dca2498 | 0c25c5e4791f300b04101c258e8c5f2c48d23dfcb9ed3a767604cc096dca2498 |
| src/hermes/lib/ansi.test.ts | verbatim | apps/desktop/src/lib/ansi.test.ts | 50b435e07974850b250d3c4de6114d6390a5ce9dc534c566ce24837adfc177c2 | 50b435e07974850b250d3c4de6114d6390a5ce9dc534c566ce24837adfc177c2 |
| src/hermes/lib/ansi.ts | verbatim | apps/desktop/src/lib/ansi.ts | 20b53b83ff4005028e23fe7319be21b702816978222d6e7cb31a89d30270cbc7 | 20b53b83ff4005028e23fe7319be21b702816978222d6e7cb31a89d30270cbc7 |
| src/hermes/lib/artifact-detect.test.ts | verbatim | apps/desktop/src/lib/artifact-detect.test.ts | 3c19dc10fcc683b8ede9ea70e56a45165946ff7ac0938bfe468918ebd663d4ce | 3c19dc10fcc683b8ede9ea70e56a45165946ff7ac0938bfe468918ebd663d4ce |
| src/hermes/lib/artifact-detect.ts | verbatim | apps/desktop/src/lib/artifact-detect.ts | d7e0bcc866ab48372ca767996d4067dcf88a858fa2cfa3bdf719b0e6ebe9c987 | d7e0bcc866ab48372ca767996d4067dcf88a858fa2cfa3bdf719b0e6ebe9c987 |
| src/hermes/lib/attachment-upload-policy.ts | verbatim | apps/desktop/src/lib/attachment-upload-policy.ts | 3b2468692b62e8f9d3913490c97b3c00047b3cdabad4a9e09ec3fa041d260058 | 3b2468692b62e8f9d3913490c97b3c00047b3cdabad4a9e09ec3fa041d260058 |
| src/hermes/lib/audio-context.ts | verbatim | apps/desktop/src/lib/audio-context.ts | d09c59dffbe80978b2264608812d8db6c10372fd4c95729813ec325d1631096e | d09c59dffbe80978b2264608812d8db6c10372fd4c95729813ec325d1631096e |
| src/hermes/lib/brand-icon.test.ts | verbatim | apps/desktop/src/lib/brand-icon.test.ts | 31b91fc07b77420ed5e5495694e910a3dd3fd195b03f8208b03ad689a48aee3d | 31b91fc07b77420ed5e5495694e910a3dd3fd195b03f8208b03ad689a48aee3d |
| src/hermes/lib/brand-icon.ts | verbatim | apps/desktop/src/lib/brand-icon.ts | 179d5ad3a00fc740cda69cef8ac6e2851e8d674554e0de186a5292370f9384cc | 179d5ad3a00fc740cda69cef8ac6e2851e8d674554e0de186a5292370f9384cc |
| src/hermes/lib/budgeted-loop.test.ts | verbatim | apps/desktop/src/lib/budgeted-loop.test.ts | cc194b810ea6f964a9ebe2564202febbc260e443bd8d9f068cf2b4766351e9e7 | cc194b810ea6f964a9ebe2564202febbc260e443bd8d9f068cf2b4766351e9e7 |
| src/hermes/lib/budgeted-loop.ts | verbatim | apps/desktop/src/lib/budgeted-loop.ts | 99847ac01750fc1092b2f3541f7a0be8095096e447e18ecf9b83cda1a0c5e546 | 99847ac01750fc1092b2f3541f7a0be8095096e447e18ecf9b83cda1a0c5e546 |
| src/hermes/lib/chat-empty.ts | verbatim | apps/desktop/src/lib/chat-empty.ts | fe3473fa32f0477b21dddca078b7ebfda5b9a7def324b79c3e8fcaaba752d8ce | fe3473fa32f0477b21dddca078b7ebfda5b9a7def324b79c3e8fcaaba752d8ce |
| src/hermes/lib/chat-messages/hydration-model-only.test.ts | verbatim | apps/desktop/src/lib/chat-messages/hydration-model-only.test.ts | 0221c14f7d192254d0460e2b590a89d3deeb125b6031a30319ec168893782721 | 0221c14f7d192254d0460e2b590a89d3deeb125b6031a30319ec168893782721 |
| src/hermes/lib/chat-messages/parts.capture.test.ts | verbatim | apps/desktop/src/lib/chat-messages/parts.capture.test.ts | 4870c85c0795e28e22bcb555ead950cbd59ff365d319d76958a03b126b971e81 | 4870c85c0795e28e22bcb555ead950cbd59ff365d319d76958a03b126b971e81 |
| src/hermes/lib/composer-input-sanitize.test.ts | verbatim | apps/desktop/src/lib/composer-input-sanitize.test.ts | 197ce0d3becd193319a9cecf70e0224e3aefeb041a2ba5e4ec0453cab8992444 | 197ce0d3becd193319a9cecf70e0224e3aefeb041a2ba5e4ec0453cab8992444 |
| src/hermes/lib/composer-input-sanitize.ts | verbatim | apps/desktop/src/lib/composer-input-sanitize.ts | ff89681397a4156a536d415ebed69a2af2098ac4983950e5bb00f597bd068443 | ff89681397a4156a536d415ebed69a2af2098ac4983950e5bb00f597bd068443 |
| src/hermes/lib/composer-native.ts | new | — | — | fcef1faf70fe9f0457b4491039408c67885c8bd53998c2ce40edfc022c34914a |
| src/hermes/lib/connection-display.test.ts | verbatim | apps/desktop/src/lib/connection-display.test.ts | c710078639cb0ad4b3f3b924675925f36c08bdb4873acb308fe8906708bf85ce | c710078639cb0ad4b3f3b924675925f36c08bdb4873acb308fe8906708bf85ce |
| src/hermes/lib/connection-display.ts | verbatim | apps/desktop/src/lib/connection-display.ts | 92dc5e519fef2c7e86eba787b703cd94dc82b21d2214e48e0196b93f56dbe270 | 92dc5e519fef2c7e86eba787b703cd94dc82b21d2214e48e0196b93f56dbe270 |
| src/hermes/lib/desktop-fs-types.ts | new | — | — | 1bdfa775f9290f72f80535d848147a2271d5dac374a910d054834f1a9e51270a |
| src/hermes/lib/desktop-fs.ts | shim | apps/desktop/src/lib/desktop-fs.ts | 56c72d15b91e5fb00f942936b22ae1db0ebc8dd5beab4cf6f832f30d9ddf4656 | 9e2afeb1602b5b90cac51a93eeec7fed116eadd0204ad0bf17466241f07d2d3a |
| src/hermes/lib/desktop-git.test.ts | verbatim | apps/desktop/src/lib/desktop-git.test.ts | a6f94308310d9f36387657a2b101506eddb09802024faa729136584bae483c1b | a6f94308310d9f36387657a2b101506eddb09802024faa729136584bae483c1b |
| src/hermes/lib/desktop-git.ts | shim | apps/desktop/src/lib/desktop-git.ts | 28fc201e9f09a565a973f78e74e3c6248422ef920d0da3266555c3d9616bec5e | 917ad49457bd8476963d539ad43b4d21ef4de92014f5361dab0f43f9de116cb6 |
| src/hermes/lib/desktop-slash-commands.test.ts | verbatim | apps/desktop/src/lib/desktop-slash-commands.test.ts | a25ae29097139645c3a10d8bea52cd7315bd5a065ed08876e3635b5773808b8e | a25ae29097139645c3a10d8bea52cd7315bd5a065ed08876e3635b5773808b8e |
| src/hermes/lib/desktop-slash-commands.ts | verbatim | apps/desktop/src/lib/desktop-slash-commands.ts | 6aad656575f5c5a51e9823cd619e571fb1bd190b5893d1575fb976d0fbce63fc | 6aad656575f5c5a51e9823cd619e571fb1bd190b5893d1575fb976d0fbce63fc |
| src/hermes/lib/desktop-slash-registry.json | verbatim | apps/desktop/src/lib/desktop-slash-registry.json | 1afb3758d6e1bc4a819fb70898bdd80aa9950161e829f46a5132310dfeda5af3 | 1afb3758d6e1bc4a819fb70898bdd80aa9950161e829f46a5132310dfeda5af3 |
| src/hermes/lib/display-path.test.ts | verbatim | apps/desktop/src/lib/display-path.test.ts | 47059e59f81a9c75b3f0b813d8d2ff4e50ec6e1a1d1a97690cfde138c234539c | 47059e59f81a9c75b3f0b813d8d2ff4e50ec6e1a1d1a97690cfde138c234539c |
| src/hermes/lib/display-path.ts | verbatim | apps/desktop/src/lib/display-path.ts | 171a1b9a609b5ef8ad808c5532e05a56cfbeb1e663cad9025df69ba7e7089990 | 171a1b9a609b5ef8ad808c5532e05a56cfbeb1e663cad9025df69ba7e7089990 |
| src/hermes/lib/error-surface-copy.test.ts | verbatim | apps/desktop/src/lib/error-surface-copy.test.ts | 0e1ad687f130eb9183eaf3d3bcc31ecc16ac67e715960ca56adf393c7b826410 | 0e1ad687f130eb9183eaf3d3bcc31ecc16ac67e715960ca56adf393c7b826410 |
| src/hermes/lib/error-surface-copy.ts | verbatim | apps/desktop/src/lib/error-surface-copy.ts | 00cc95cce8d2e9cc70cffcecd9cea77869ff68536f22d35b0a613c95ef075727 | 00cc95cce8d2e9cc70cffcecd9cea77869ff68536f22d35b0a613c95ef075727 |
| src/hermes/lib/error-surface.test.ts | verbatim | apps/desktop/src/lib/error-surface.test.ts | 8855e7f2e21efdaaa73e057bb773605c0879016aa7d6d1e68bc1789d9c8bdac8 | 8855e7f2e21efdaaa73e057bb773605c0879016aa7d6d1e68bc1789d9c8bdac8 |
| src/hermes/lib/escape-layers.test.ts | verbatim | apps/desktop/src/lib/escape-layers.test.ts | 41326afa07382d8f4dd4cf5b02745af1471eebde069448d5a53dc4b36e486518 | 41326afa07382d8f4dd4cf5b02745af1471eebde069448d5a53dc4b36e486518 |
| src/hermes/lib/escape-layers.ts | verbatim | apps/desktop/src/lib/escape-layers.ts | 28862d0d46692b3166ef60d2faf18036f6b8abbe9e4791ab177b8529e377f779 | 28862d0d46692b3166ef60d2faf18036f6b8abbe9e4791ab177b8529e377f779 |
| src/hermes/lib/excluded-paths.ts | verbatim | apps/desktop/src/lib/excluded-paths.ts | 0e2a858a1c57eab7688581089e3cb80d72ad7d72130eeaa6db1b94cbd135b02c | 0e2a858a1c57eab7688581089e3cb80d72ad7d72130eeaa6db1b94cbd135b02c |
| src/hermes/lib/external-link.tsx | patch | apps/desktop/src/lib/external-link.tsx | 38ba5db9523a00c5c7918a4ec2fd0895aefb58d95cfc094a394db08dc800149b | c7e71360391e2171640e86b8ce8c2779a800188e49b0337590112773467f8669 |
| src/hermes/lib/find-in-page-scope.test.ts | verbatim | apps/desktop/src/lib/find-in-page-scope.test.ts | ac449e5db29dc1877f5ca69b6aeb64f77b29c7f1eccf3c6175ed8dfd79ce69f5 | ac449e5db29dc1877f5ca69b6aeb64f77b29c7f1eccf3c6175ed8dfd79ce69f5 |
| src/hermes/lib/find-in-page-scope.ts | verbatim | apps/desktop/src/lib/find-in-page-scope.ts | 4d4e8cbe0220dfc6ed835da9eee1900864de4b9b91c5a4a222ff52069abdfe1b | 4d4e8cbe0220dfc6ed835da9eee1900864de4b9b91c5a4a222ff52069abdfe1b |
| src/hermes/lib/find-in-page.ts | verbatim | apps/desktop/src/lib/find-in-page.ts | bb4358ae5bf6908bb3c931991469b7ec7a4e203921d7b87455f9fb12da28cad3 | bb4358ae5bf6908bb3c931991469b7ec7a4e203921d7b87455f9fb12da28cad3 |
| src/hermes/lib/guarded-model-switch.ts | verbatim | apps/desktop/src/lib/guarded-model-switch.ts | 0c9f67c1af1e942dc6a855f19860f3d2f7be6e0c92c89d37391321cf91a09af7 | 0c9f67c1af1e942dc6a855f19860f3d2f7be6e0c92c89d37391321cf91a09af7 |
| src/hermes/lib/icons.ts | verbatim | apps/desktop/src/lib/icons.ts | b70ce83c1b763f8da1f4e87f564483198d593f496c2d3107ed9c89b0bffd8281 | b70ce83c1b763f8da1f4e87f564483198d593f496c2d3107ed9c89b0bffd8281 |
| src/hermes/lib/image-resize.test.ts | verbatim | apps/desktop/src/lib/image-resize.test.ts | d674f6534b0984bae76dab77afeb15f9e0b42b0b74306519998cc0330112a3e0 | d674f6534b0984bae76dab77afeb15f9e0b42b0b74306519998cc0330112a3e0 |
| src/hermes/lib/image-resize.ts | verbatim | apps/desktop/src/lib/image-resize.ts | 1cd7b25288e7690887d14cd0d85d0f97d130b28f84ffce6becaf7b6cc7ca5c28 | 1cd7b25288e7690887d14cd0d85d0f97d130b28f84ffce6becaf7b6cc7ca5c28 |
| src/hermes/lib/ime.test.ts | verbatim | apps/desktop/src/lib/ime.test.ts | fbd2ee97f1c1aca808524641d4f5e4f440b81850e06040541de5f968654e8503 | fbd2ee97f1c1aca808524641d4f5e4f440b81850e06040541de5f968654e8503 |
| src/hermes/lib/ime.ts | verbatim | apps/desktop/src/lib/ime.ts | 310f5eb24e69609fb2c2aba7b09f3008e1a675a0c2b86800f56035d5c54b06de | 310f5eb24e69609fb2c2aba7b09f3008e1a675a0c2b86800f56035d5c54b06de |
| src/hermes/lib/incremental-external-store-runtime.test.ts | verbatim | apps/desktop/src/lib/incremental-external-store-runtime.test.ts | 21ff1df757e2403169673732660ccee94d461ee9803646473857baecae4be779 | 21ff1df757e2403169673732660ccee94d461ee9803646473857baecae4be779 |
| src/hermes/lib/incremental-external-store-runtime.ts | verbatim | apps/desktop/src/lib/incremental-external-store-runtime.ts | 8e86aeeabd50ec838e6aabeb8172b0639b368da69ad488fda8e261df6986a4a0 | 8e86aeeabd50ec838e6aabeb8172b0639b368da69ad488fda8e261df6986a4a0 |
| src/hermes/lib/incremental-runtime-adapter-notify.test.ts | verbatim | apps/desktop/src/lib/incremental-runtime-adapter-notify.test.ts | 2242ef3279079c4090b894e80c3ee60b1b9bf32145447fb3a801a55022524bae | 2242ef3279079c4090b894e80c3ee60b1b9bf32145447fb3a801a55022524bae |
| src/hermes/lib/input-modality.test.ts | verbatim | apps/desktop/src/lib/input-modality.test.ts | 75c21fe229d4867a10de4c2e51ebcea66e6ace229c06f7f78e4174fdbaea8ca2 | 75c21fe229d4867a10de4c2e51ebcea66e6ace229c06f7f78e4174fdbaea8ca2 |
| src/hermes/lib/input-modality.ts | verbatim | apps/desktop/src/lib/input-modality.ts | 9ac9c768f2b9bba8ec961cca106181ceecd98793c7b0881dd8c3ede77b3420f5 | 9ac9c768f2b9bba8ec961cca106181ceecd98793c7b0881dd8c3ede77b3420f5 |
| src/hermes/lib/json-format.test.ts | verbatim | apps/desktop/src/lib/json-format.test.ts | 5207394db7f143acdb36fb514ebe63b6ffc3879c6e09c38fbf9e9b12a937885f | 5207394db7f143acdb36fb514ebe63b6ffc3879c6e09c38fbf9e9b12a937885f |
| src/hermes/lib/json-format.ts | verbatim | apps/desktop/src/lib/json-format.ts | b197365d14c36a6821d8a34acb248d56947c110fad85995e18ade98dc2ea164a | b197365d14c36a6821d8a34acb248d56947c110fad85995e18ade98dc2ea164a |
| src/hermes/lib/katex-memo.ts | verbatim | apps/desktop/src/lib/katex-memo.ts | 0c0138a10a12954c6ed5e2c1d96f11251de9b3a87db5166194f3ce1a34315837 | 0c0138a10a12954c6ed5e2c1d96f11251de9b3a87db5166194f3ce1a34315837 |
| src/hermes/lib/keybinds/actions.test.ts | verbatim | apps/desktop/src/lib/keybinds/actions.test.ts | c982af6a420d99d6f7c7fc7b3c3850bb2d917c2e76c94fbb02108ad9d43f973a | c982af6a420d99d6f7c7fc7b3c3850bb2d917c2e76c94fbb02108ad9d43f973a |
| src/hermes/lib/keybinds/actions.ts | verbatim | apps/desktop/src/lib/keybinds/actions.ts | ff3b0c46bd359ccbf54827f0a1a69daf7724d86a71dbf371a13e1e123fa820aa | ff3b0c46bd359ccbf54827f0a1a69daf7724d86a71dbf371a13e1e123fa820aa |
| src/hermes/lib/keybinds/approval-keys.ts | verbatim | apps/desktop/src/lib/keybinds/approval-keys.ts | 7849e1f5bdf0bd8eb8b03ed59097c66268e82be778c7570073481c71dedcf562 | 7849e1f5bdf0bd8eb8b03ed59097c66268e82be778c7570073481c71dedcf562 |
| src/hermes/lib/keybinds/chords.ts | verbatim | apps/desktop/src/lib/keybinds/chords.ts | eb4aeced1cad058c73db2aaddbbd2ca90f345103f0e79a3248e8eb40f6245e0c | eb4aeced1cad058c73db2aaddbbd2ca90f345103f0e79a3248e8eb40f6245e0c |
| src/hermes/lib/keybinds/combo.test.ts | verbatim | apps/desktop/src/lib/keybinds/combo.test.ts | 1621113bfa676a57060d433a172619b147048aee98571216b485b909af23bc1a | 1621113bfa676a57060d433a172619b147048aee98571216b485b909af23bc1a |
| src/hermes/lib/keybinds/combo.ts | verbatim | apps/desktop/src/lib/keybinds/combo.ts | 1c509697188555d7d507f8ba76d7948b625e51e3aa2239c629f65ebdedb4f065 | 1c509697188555d7d507f8ba76d7948b625e51e3aa2239c629f65ebdedb4f065 |
| src/hermes/lib/keybinds/composer-focus-keys.test.ts | verbatim | apps/desktop/src/lib/keybinds/composer-focus-keys.test.ts | 2b54bde31a961eb392e4f53ec13937224f1804b5f8ee03e42b496b779d4181be | 2b54bde31a961eb392e4f53ec13937224f1804b5f8ee03e42b496b779d4181be |
| src/hermes/lib/keybinds/composer-focus-keys.ts | verbatim | apps/desktop/src/lib/keybinds/composer-focus-keys.ts | 02bf671b47214f09147bc26c6e8def0d56a6f94383383e831adf9aff81ae4248 | 02bf671b47214f09147bc26c6e8def0d56a6f94383383e831adf9aff81ae4248 |
| src/hermes/lib/keybinds/use-keybind-hint.ts | verbatim | apps/desktop/src/lib/keybinds/use-keybind-hint.ts | 9964a500c08f81c411bb77bf205e1393672edfe2262ab0d3bad5f21673f7e7b0 | 9964a500c08f81c411bb77bf205e1393672edfe2262ab0d3bad5f21673f7e7b0 |
| src/hermes/lib/layout-persistence.test.ts | verbatim | apps/desktop/src/lib/layout-persistence.test.ts | 064474a936cf36ac6065d5d1b5fc9c6070b227d1db3306fc2ec2007d01940a64 | 064474a936cf36ac6065d5d1b5fc9c6070b227d1db3306fc2ec2007d01940a64 |
| src/hermes/lib/layout-persistence.ts | verbatim | apps/desktop/src/lib/layout-persistence.ts | 32f2128b519a8b1d6a4a56fa87b644500715b28a54527c6a1986c121066b3de4 | 32f2128b519a8b1d6a4a56fa87b644500715b28a54527c6a1986c121066b3de4 |
| src/hermes/lib/listing-embed.test.ts | verbatim | apps/desktop/src/lib/listing-embed.test.ts | 36a917ae00986f5724ac03ad19b1fd98a780e46b77453f3b5989ecdc2531e12d | 36a917ae00986f5724ac03ad19b1fd98a780e46b77453f3b5989ecdc2531e12d |
| src/hermes/lib/listing-embed.ts | verbatim | apps/desktop/src/lib/listing-embed.ts | 41e5c4dafb43f632a0049d4a3d5c9b48e636a2ff5b73a707fcd8d6c4bcf249cb | 41e5c4dafb43f632a0049d4a3d5c9b48e636a2ff5b73a707fcd8d6c4bcf249cb |
| src/hermes/lib/local-preview.ts | patch | apps/desktop/src/lib/local-preview.ts | e98fb34548bd43405d2b77ccdecf93f9c9f8fe43b9a009f1c3bd093d91cf33e2 | d71eac53d3744f046d1831b8e267eab2fb78cf51765c813ff71319c01b3c9c3e |
| src/hermes/lib/log-search.test.ts | verbatim | apps/desktop/src/lib/log-search.test.ts | e4ec2f04bb6063dffb34dc35ae60608ca388c4dbe1e09c3091f04a793c401c6c | e4ec2f04bb6063dffb34dc35ae60608ca388c4dbe1e09c3091f04a793c401c6c |
| src/hermes/lib/log-search.ts | verbatim | apps/desktop/src/lib/log-search.ts | 17724a3e65d029f13bda0c0a2eb7df39d6f07bb3ade7000841afd931e97e40fb | 17724a3e65d029f13bda0c0a2eb7df39d6f07bb3ade7000841afd931e97e40fb |
| src/hermes/lib/markdown-blocks.test.ts | verbatim | apps/desktop/src/lib/markdown-blocks.test.ts | c252f58609a433f3847d5e27bee2b90cb88dbd1d6cbc3af5e014a9277f66e3f2 | c252f58609a433f3847d5e27bee2b90cb88dbd1d6cbc3af5e014a9277f66e3f2 |
| src/hermes/lib/markdown-blocks.ts | verbatim | apps/desktop/src/lib/markdown-blocks.ts | 405f85609a89509b08ad2f2160cf5674d4428a995584ed68ae26192385110a6c | 405f85609a89509b08ad2f2160cf5674d4428a995584ed68ae26192385110a6c |
| src/hermes/lib/markdown-code.test.ts | verbatim | apps/desktop/src/lib/markdown-code.test.ts | 1eeaa98dd497dbd9a2e3549049c20546380e933ad3e8a7181005836d93cc3a24 | 1eeaa98dd497dbd9a2e3549049c20546380e933ad3e8a7181005836d93cc3a24 |
| src/hermes/lib/markdown-code.ts | verbatim | apps/desktop/src/lib/markdown-code.ts | 13e883ccd330f9c2d00fa22db918ed351413b65f261e8e81c5f3657deb0a21ef | 13e883ccd330f9c2d00fa22db918ed351413b65f261e8e81c5f3657deb0a21ef |
| src/hermes/lib/markdown-html-depth.test.ts | verbatim | apps/desktop/src/lib/markdown-html-depth.test.ts | de0c2fafdacdf6634cd3d43b73b7d657a8ee14358f7f27fa44931ac7623dfd5d | de0c2fafdacdf6634cd3d43b73b7d657a8ee14358f7f27fa44931ac7623dfd5d |
| src/hermes/lib/markdown-html-depth.ts | verbatim | apps/desktop/src/lib/markdown-html-depth.ts | 96568676f9c8f6fcb3969e502fb9a052ea64904b318d2628ddce084313a10622 | 96568676f9c8f6fcb3969e502fb9a052ea64904b318d2628ddce084313a10622 |
| src/hermes/lib/markdown-preprocess.autolink.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.autolink.test.ts | 937857d00ee5cb71477b01621a11ff74279b37ac5e4d4ed6cc40e472e37bd090 | 937857d00ee5cb71477b01621a11ff74279b37ac5e4d4ed6cc40e472e37bd090 |
| src/hermes/lib/markdown-preprocess.citation.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.citation.test.ts | c00d3ce7f2c010ef783a100ee49fc88d6f18f6997f5e04f6bc5fe85172f507e8 | c00d3ce7f2c010ef783a100ee49fc88d6f18f6997f5e04f6bc5fe85172f507e8 |
| src/hermes/lib/markdown-preprocess.directives.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.directives.test.ts | 396add3efe62a390d24a036e29281ae7495b7316d098f2626c84327679ec1525 | 396add3efe62a390d24a036e29281ae7495b7316d098f2626c84327679ec1525 |
| src/hermes/lib/markdown-preprocess.fences.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.fences.test.ts | 6b8269911028950bc5e17aae5e803c3feecb634568a356028e7db9813a551dc6 | 6b8269911028950bc5e17aae5e803c3feecb634568a356028e7db9813a551dc6 |
| src/hermes/lib/markdown-preprocess.file-preview.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.file-preview.test.ts | bc1ba2cb5e4aac78a5b93cfef9794fa91efc8d548c1d2258605c1d4311e5f9d9 | bc1ba2cb5e4aac78a5b93cfef9794fa91efc8d548c1d2258605c1d4311e5f9d9 |
| src/hermes/lib/markdown-preprocess.reasoning.test.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.reasoning.test.ts | 2e44d30b190aadf1f8219ff07b6bdac21a22df8dd9ac810e851c45ea6efe0115 | 2e44d30b190aadf1f8219ff07b6bdac21a22df8dd9ac810e851c45ea6efe0115 |
| src/hermes/lib/markdown-preprocess.ts | verbatim | apps/desktop/src/lib/markdown-preprocess.ts | 72a206d1dc8fe8d437dde78da42f00e70e758ef4f145bae1a9930f6164a25321 | 72a206d1dc8fe8d437dde78da42f00e70e758ef4f145bae1a9930f6164a25321 |
| src/hermes/lib/markdown-table-widths.ts | verbatim | apps/desktop/src/lib/markdown-table-widths.ts | 3ee9d55c9ec89d960e8f40692f85d48b78b93f2148b688054f71feda208dfb51 | 3ee9d55c9ec89d960e8f40692f85d48b78b93f2148b688054f71feda208dfb51 |
| src/hermes/lib/mcp-brands.tsx | verbatim | apps/desktop/src/lib/mcp-brands.tsx | 34d3238431cfdafd80f10e7ecc3d8497854619c18e30ef146d8b468e127d6b4b | 34d3238431cfdafd80f10e7ecc3d8497854619c18e30ef146d8b468e127d6b4b |
| src/hermes/lib/mcp-cost.test.ts | verbatim | apps/desktop/src/lib/mcp-cost.test.ts | 382ad504efcd2c49993adeca60c6304ce192fe13b7ca88cf6d667e6ed91f1c1f | 382ad504efcd2c49993adeca60c6304ce192fe13b7ca88cf6d667e6ed91f1c1f |
| src/hermes/lib/mcp-cost.ts | verbatim | apps/desktop/src/lib/mcp-cost.ts | 355d719293d2135ba556894133b2d27e0f394aaa30cd7853106c177364f0c17f | 355d719293d2135ba556894133b2d27e0f394aaa30cd7853106c177364f0c17f |
| src/hermes/lib/mcp-enabled-cases.json | verbatim | apps/desktop/src/lib/mcp-enabled-cases.json | 43333a71a3d22de9d8e205d8456f006a267fe3ebf38efe2f1511b1bd070c89e5 | 43333a71a3d22de9d8e205d8456f006a267fe3ebf38efe2f1511b1bd070c89e5 |
| src/hermes/lib/mcp-probe-cache.test.ts | verbatim | apps/desktop/src/lib/mcp-probe-cache.test.ts | 3beaf5958fa195091ec96ab36b14b4d994979a6e442fab4c9b4b25b1e541b549 | 3beaf5958fa195091ec96ab36b14b4d994979a6e442fab4c9b4b25b1e541b549 |
| src/hermes/lib/mcp-probe-cache.ts | verbatim | apps/desktop/src/lib/mcp-probe-cache.ts | facf36b84463d771bddfd704bf47d2d3c7f06e067644dd36f2393864017599da | facf36b84463d771bddfd704bf47d2d3c7f06e067644dd36f2393864017599da |
| src/hermes/lib/mcp-servers.test.ts | verbatim | apps/desktop/src/lib/mcp-servers.test.ts | 45b70135ede9bfbcf7d57cef52d0ca5fc376cbaa90d42bf4b935983da7039f37 | 45b70135ede9bfbcf7d57cef52d0ca5fc376cbaa90d42bf4b935983da7039f37 |
| src/hermes/lib/mcp-servers.ts | verbatim | apps/desktop/src/lib/mcp-servers.ts | d94d93f4b627af4bb145d6cb65df20f0c5180b40addfd2cf157d55ab1ee69651 | d94d93f4b627af4bb145d6cb65df20f0c5180b40addfd2cf157d55ab1ee69651 |
| src/hermes/lib/mcp-tool-filter.test.ts | verbatim | apps/desktop/src/lib/mcp-tool-filter.test.ts | 6bfa7d81152298cc6eb301cb864ea75cb5184bc64a7b33fc3fb88916238ffa12 | 6bfa7d81152298cc6eb301cb864ea75cb5184bc64a7b33fc3fb88916238ffa12 |
| src/hermes/lib/mcp-tool-filter.ts | verbatim | apps/desktop/src/lib/mcp-tool-filter.ts | 9cf342cf3633e65b40d04b9edc41d8cddcb10535325c096558295bf1f76eb09e | 9cf342cf3633e65b40d04b9edc41d8cddcb10535325c096558295bf1f76eb09e |
| src/hermes/lib/mic-meter-context.ts | verbatim | apps/desktop/src/lib/mic-meter-context.ts | 865bb52cd7e0e0f61a0224adb99720dafadb5dc76cbb2b1d81e7e01a6301fc74 | 865bb52cd7e0e0f61a0224adb99720dafadb5dc76cbb2b1d81e7e01a6301fc74 |
| src/hermes/lib/middle-click.test.tsx | verbatim | apps/desktop/src/lib/middle-click.test.tsx | 88bcb3080faa6ce7c5bcd8617b8181c21fbc9e159d7ab127d7d086308bc41e0c | 88bcb3080faa6ce7c5bcd8617b8181c21fbc9e159d7ab127d7d086308bc41e0c |
| src/hermes/lib/middle-click.ts | verbatim | apps/desktop/src/lib/middle-click.ts | 4b0df4d76d0a72c5b486f8781077c47016222a08148874592bb175b3e0d7bf59 | 4b0df4d76d0a72c5b486f8781077c47016222a08148874592bb175b3e0d7bf59 |
| src/hermes/lib/model-options.test.ts | verbatim | apps/desktop/src/lib/model-options.test.ts | 415a4f405be885ccfe4e931888439eeec32a3248d1306e99ba72bbcaba06a5f6 | 415a4f405be885ccfe4e931888439eeec32a3248d1306e99ba72bbcaba06a5f6 |
| src/hermes/lib/model-status-label.test.ts | verbatim | apps/desktop/src/lib/model-status-label.test.ts | b5d28f2f8449d3f29374445cdb51dae275d2e1ba9e1ad388dba0567e3da4a052 | b5d28f2f8449d3f29374445cdb51dae275d2e1ba9e1ad388dba0567e3da4a052 |
| src/hermes/lib/model-status-label.ts | verbatim | apps/desktop/src/lib/model-status-label.ts | 4a834ee6ab1207d952c0f2a30f1f7ad09de0fd983f6bb39772d57406caadb3f2 | 4a834ee6ab1207d952c0f2a30f1f7ad09de0fd983f6bb39772d57406caadb3f2 |
| src/hermes/lib/mutable-ref.ts | verbatim | apps/desktop/src/lib/mutable-ref.ts | 460f65513d35b6db063efef9b304a266df56279415480c8eb35f1b2882f47914 | 460f65513d35b6db063efef9b304a266df56279415480c8eb35f1b2882f47914 |
| src/hermes/lib/oneshot.ts | verbatim | apps/desktop/src/lib/oneshot.ts | 5e1c4b001ebba7fe7f0944037212d9d14a802f24a440c45a9401e43697ece57c | 5e1c4b001ebba7fe7f0944037212d9d14a802f24a440c45a9401e43697ece57c |
| src/hermes/lib/path-compare.test.ts | verbatim | apps/desktop/src/lib/path-compare.test.ts | 0fd23542517e307b9b04c663b9ff424ff6b98cbde4efbaa4c69a4c0d3052bc29 | 0fd23542517e307b9b04c663b9ff424ff6b98cbde4efbaa4c69a4c0d3052bc29 |
| src/hermes/lib/path-compare.ts | verbatim | apps/desktop/src/lib/path-compare.ts | 6d1bce9cca087287f485263bde963493bd047618d19bd633638fe145f90eea46 | 6d1bce9cca087287f485263bde963493bd047618d19bd633638fe145f90eea46 |
| src/hermes/lib/platform.ts | verbatim | apps/desktop/src/lib/platform.ts | 0b3adae78b5a83f2a83c0872ccebc2ddef050513664489be4d0b3e190a815508 | 0b3adae78b5a83f2a83c0872ccebc2ddef050513664489be4d0b3e190a815508 |
| src/hermes/lib/plugin-catalog.test.ts | verbatim | apps/desktop/src/lib/plugin-catalog.test.ts | e4d7fe3fe895271f137bfef5807ac8447df0f40cdcbdd4933a5003d7ce30d46b | e4d7fe3fe895271f137bfef5807ac8447df0f40cdcbdd4933a5003d7ce30d46b |
| src/hermes/lib/plugin-catalog.ts | verbatim | apps/desktop/src/lib/plugin-catalog.ts | 8b2cbf73f2200dc7baf8746eb14c2ff182f691672955a48a72b9e0608b603cfd | 8b2cbf73f2200dc7baf8746eb14c2ff182f691672955a48a72b9e0608b603cfd |
| src/hermes/lib/pool.test.ts | verbatim | apps/desktop/src/lib/pool.test.ts | 50f3b2a2553fee2883367c7959c6e50ea716ce2f4988b974d9021585000dd212 | 50f3b2a2553fee2883367c7959c6e50ea716ce2f4988b974d9021585000dd212 |
| src/hermes/lib/pool.ts | verbatim | apps/desktop/src/lib/pool.ts | a3b753e6b074e5754891654c537f662df061d80e773b5c1d77fbc3f1ea39ac45 | a3b753e6b074e5754891654c537f662df061d80e773b5c1d77fbc3f1ea39ac45 |
| src/hermes/lib/preview-act/act-in-page.test.ts | verbatim | apps/desktop/src/lib/preview-act/act-in-page.test.ts | 3ef4bd28e9116df65a7f06ce71c163c2c941c96f33fa2890d9624daaccd32fa0 | 3ef4bd28e9116df65a7f06ce71c163c2c941c96f33fa2890d9624daaccd32fa0 |
| src/hermes/lib/preview-act/act-in-page.ts | verbatim | apps/desktop/src/lib/preview-act/act-in-page.ts | 0f8052df4c1e0f930449122802bdfef57a2af0d072daa2afa923417a426813bf | 0f8052df4c1e0f930449122802bdfef57a2af0d072daa2afa923417a426813bf |
| src/hermes/lib/preview-act/identity.ts | verbatim | apps/desktop/src/lib/preview-act/identity.ts | c6d01a49c418f852d580041b38ce1164a4b7b36191d30614094cba3bf623b0a9 | c6d01a49c418f852d580041b38ce1164a4b7b36191d30614094cba3bf623b0a9 |
| src/hermes/lib/preview-act/naming.test.ts | verbatim | apps/desktop/src/lib/preview-act/naming.test.ts | 0d031771386dd55a4f0782fc7636233d9153c6118dafa570de0766432c34f380 | 0d031771386dd55a4f0782fc7636233d9153c6118dafa570de0766432c34f380 |
| src/hermes/lib/preview-act/naming.ts | verbatim | apps/desktop/src/lib/preview-act/naming.ts | e803e94f04c07a09ebf0f79623cea44b71b5dcf46b74878290a28d56f569bc50 | e803e94f04c07a09ebf0f79623cea44b71b5dcf46b74878290a28d56f569bc50 |
| src/hermes/lib/preview-act/types.ts | verbatim | apps/desktop/src/lib/preview-act/types.ts | 799fdf4ac4f209c13be52ecf1dc7e1048b263ff2069e8aeea2851712f576aacb | 799fdf4ac4f209c13be52ecf1dc7e1048b263ff2069e8aeea2851712f576aacb |
| src/hermes/lib/preview-act/visibility.ts | verbatim | apps/desktop/src/lib/preview-act/visibility.ts | 785708f3dbb73300977686363a2d577647159dc471b5696b04e025a70de475ba | 785708f3dbb73300977686363a2d577647159dc471b5696b04e025a70de475ba |
| src/hermes/lib/preview-act/watch-in-page.test.ts | verbatim | apps/desktop/src/lib/preview-act/watch-in-page.test.ts | 0853a8b386062e83f94a7f76008635e0cf6b41b754809fdf3f50f57a5c06d33a | 0853a8b386062e83f94a7f76008635e0cf6b41b754809fdf3f50f57a5c06d33a |
| src/hermes/lib/preview-act/watch-in-page.ts | verbatim | apps/desktop/src/lib/preview-act/watch-in-page.ts | d161553c26beedb39f014a6e027f40f5c0243fd1caa42500559fb97f131440de | d161553c26beedb39f014a6e027f40f5c0243fd1caa42500559fb97f131440de |
| src/hermes/lib/preview-annotate/flush.ts | verbatim | apps/desktop/src/lib/preview-annotate/flush.ts | 88566ce43c6b2759a3ac33392b5bff15ea8800a08244eec761df8e5248c7deb1 | 88566ce43c6b2759a3ac33392b5bff15ea8800a08244eec761df8e5248c7deb1 |
| src/hermes/lib/preview-annotate/group.test.ts | verbatim | apps/desktop/src/lib/preview-annotate/group.test.ts | 9c7bcc23dbec825fddd3d7db16ed0976560735e3df57d740a56e414323d573bc | 9c7bcc23dbec825fddd3d7db16ed0976560735e3df57d740a56e414323d573bc |
| src/hermes/lib/preview-annotate/group.ts | verbatim | apps/desktop/src/lib/preview-annotate/group.ts | a50e05a1a8b42ba3a16ce7f2239c88cdf008a6a80a23ca8a1276f0a63914444c | a50e05a1a8b42ba3a16ce7f2239c88cdf008a6a80a23ca8a1276f0a63914444c |
| src/hermes/lib/preview-annotate/handoff.ts | shim | apps/desktop/src/lib/preview-annotate/handoff.ts | 4eb09d5f175b3a7b75ead2e62057f621168a03d36d6bc697eb7366492385c9b3 | c9754ad54a793242a8acd538da4d022415af9f11ae52967f7313bd2cee31fd44 |
| src/hermes/lib/preview-annotate/identity.ts | verbatim | apps/desktop/src/lib/preview-annotate/identity.ts | 9556a23a4a85fdd2a3f176e46a0cde59ca81c10c005953f7044664b4c67fe69b | 9556a23a4a85fdd2a3f176e46a0cde59ca81c10c005953f7044664b4c67fe69b |
| src/hermes/lib/preview-annotate/pack.test.ts | verbatim | apps/desktop/src/lib/preview-annotate/pack.test.ts | b0a4654865208f79956765fe9cd9613c596e28c8b37314f105255711830c7d00 | b0a4654865208f79956765fe9cd9613c596e28c8b37314f105255711830c7d00 |
| src/hermes/lib/preview-annotate/pack.ts | verbatim | apps/desktop/src/lib/preview-annotate/pack.ts | 8a324be23d0cf14ef53cbd79e00d7036885427bf7828e14a58bc9cbd9bc1da50 | 8a324be23d0cf14ef53cbd79e00d7036885427bf7828e14a58bc9cbd9bc1da50 |
| src/hermes/lib/preview-annotate/stack.test.ts | verbatim | apps/desktop/src/lib/preview-annotate/stack.test.ts | 69805cd60f8c6356cca43f2bbd603cd56db146c8ff381a568fdfeec1b95890fa | 69805cd60f8c6356cca43f2bbd603cd56db146c8ff381a568fdfeec1b95890fa |
| src/hermes/lib/preview-annotate/stack.ts | verbatim | apps/desktop/src/lib/preview-annotate/stack.ts | 43fa072ba774fd49c860eba34248badc66c663a9ef081d70c056905c846a499b | 43fa072ba774fd49c860eba34248badc66c663a9ef081d70c056905c846a499b |
| src/hermes/lib/preview-annotate/tokens.ts | verbatim | apps/desktop/src/lib/preview-annotate/tokens.ts | 84d7125eb8803164ff8b65fb9fe341b94f7f3ae2adc7ce54217807c1f38c5fcf | 84d7125eb8803164ff8b65fb9fe341b94f7f3ae2adc7ce54217807c1f38c5fcf |
| src/hermes/lib/preview-reach.test.ts | verbatim | apps/desktop/src/lib/preview-reach.test.ts | 4c32e3c719e6dc252bead9885076ba3b1dda65cac2bf06c8e23b4acc954864c0 | 4c32e3c719e6dc252bead9885076ba3b1dda65cac2bf06c8e23b4acc954864c0 |
| src/hermes/lib/preview-reach.ts | patch | apps/desktop/src/lib/preview-reach.ts | cd1d7366df1b56014b7a54eefc753231b8a0234c4f5308763375ac223fc59b13 | eb57a95084e458f1c8edc71b5a7496d58fc858d2a956629f7fc66fe7063a5155 |
| src/hermes/lib/preview-targets.test.ts | verbatim | apps/desktop/src/lib/preview-targets.test.ts | 82df8ad24f183cecabb3bf73ee77dd691c2229a3722a307738e6b1d35a7c6759 | 82df8ad24f183cecabb3bf73ee77dd691c2229a3722a307738e6b1d35a7c6759 |
| src/hermes/lib/preview-targets.ts | verbatim | apps/desktop/src/lib/preview-targets.ts | d86522c04354c22ef7e7d3071b84462ce609963110f7086c8880e20c0f2666ba | d86522c04354c22ef7e7d3071b84462ce609963110f7086c8880e20c0f2666ba |
| src/hermes/lib/profile-color.ts | verbatim | apps/desktop/src/lib/profile-color.ts | 3299be74ca51eb48530889d5c1cb215a00c8def41fa89cdbee761db7d0a06c4f | 3299be74ca51eb48530889d5c1cb215a00c8def41fa89cdbee761db7d0a06c4f |
| src/hermes/lib/profile-order.ts | verbatim | apps/desktop/src/lib/profile-order.ts | 25e6486ff609c564b292b460e102715aa337bb472914541fef0457a932650ae9 | 25e6486ff609c564b292b460e102715aa337bb472914541fef0457a932650ae9 |
| src/hermes/lib/profile-short-label.ts | verbatim | apps/desktop/src/lib/profile-short-label.ts | 428d2d306a8e4fb9bea896800a700c7e2ca30282db01586ba30f2aed4b1ada5b | 428d2d306a8e4fb9bea896800a700c7e2ca30282db01586ba30f2aed4b1ada5b |
| src/hermes/lib/provider-limit.test.ts | verbatim | apps/desktop/src/lib/provider-limit.test.ts | bcb7b2cdeb3a781a9883ee0f7ab30a21ee995cc3273ec95d1645426e5b86fd70 | bcb7b2cdeb3a781a9883ee0f7ab30a21ee995cc3273ec95d1645426e5b86fd70 |
| src/hermes/lib/provider-limit.ts | verbatim | apps/desktop/src/lib/provider-limit.ts | 0ca798d2f54b5b4aa61973faa182c7822073a4b5d279a7a15ff788efcca327d5 | 0ca798d2f54b5b4aa61973faa182c7822073a4b5d279a7a15ff788efcca327d5 |
| src/hermes/lib/provider-setup-errors.test.ts | verbatim | apps/desktop/src/lib/provider-setup-errors.test.ts | 130cc5528c1f6a16c68a4c70c1774b2d0fbc3e973e27b04477713156116fa44a | 130cc5528c1f6a16c68a4c70c1774b2d0fbc3e973e27b04477713156116fa44a |
| src/hermes/lib/provider-setup-errors.ts | verbatim | apps/desktop/src/lib/provider-setup-errors.ts | 4062967a9b4182cb9a0e0c11b7b58a92da2b28247334ee25e7820021d5796682 | 4062967a9b4182cb9a0e0c11b7b58a92da2b28247334ee25e7820021d5796682 |
| src/hermes/lib/query-client.test.ts | verbatim | apps/desktop/src/lib/query-client.test.ts | a12c335756a7be2da55f7fa8cbdae46542ba12c247f06148a95bd57b53e99494 | a12c335756a7be2da55f7fa8cbdae46542ba12c247f06148a95bd57b53e99494 |
| src/hermes/lib/query-client.ts | verbatim | apps/desktop/src/lib/query-client.ts | 4b0ec0a2b54738f253d960716168d3dd1d0d22fabffe2c398684e42754cf23de | 4b0ec0a2b54738f253d960716168d3dd1d0d22fabffe2c398684e42754cf23de |
| src/hermes/lib/reasoning-blocks.test.ts | verbatim | apps/desktop/src/lib/reasoning-blocks.test.ts | b208825bfb09da220027d75d296c514d00ca3a5ae519112deb234ab824a51dd4 | b208825bfb09da220027d75d296c514d00ca3a5ae519112deb234ab824a51dd4 |
| src/hermes/lib/reasoning-blocks.ts | verbatim | apps/desktop/src/lib/reasoning-blocks.ts | bec1e23863bd1256c61c67c5c0ac1dee71898a08f78a290fe553478865d6b51b | bec1e23863bd1256c61c67c5c0ac1dee71898a08f78a290fe553478865d6b51b |
| src/hermes/lib/reasoning-effort.test.ts | verbatim | apps/desktop/src/lib/reasoning-effort.test.ts | 931ce3fcf7095adbb870a0489907b020cc27f4af7b90423ea90d0dd1d27978c4 | 931ce3fcf7095adbb870a0489907b020cc27f4af7b90423ea90d0dd1d27978c4 |
| src/hermes/lib/reasoning-effort.ts | verbatim | apps/desktop/src/lib/reasoning-effort.ts | efb7fba56bda1e3d1a37f227fa7c94eec8c461aa82b1801f6d594bf2e6718ad4 | efb7fba56bda1e3d1a37f227fa7c94eec8c461aa82b1801f6d594bf2e6718ad4 |
| src/hermes/lib/reasoning-slash.test.ts | verbatim | apps/desktop/src/lib/reasoning-slash.test.ts | 834c826092087e77f84fa4be95c9450d48a16a3808514518b604b4c2a4116ed8 | 834c826092087e77f84fa4be95c9450d48a16a3808514518b604b4c2a4116ed8 |
| src/hermes/lib/reasoning-slash.ts | verbatim | apps/desktop/src/lib/reasoning-slash.ts | 89583dbae727a37ac36ae86a9c2cfd441098e82fd85b0506bf8c3e51e053d00a | 89583dbae727a37ac36ae86a9c2cfd441098e82fd85b0506bf8c3e51e053d00a |
| src/hermes/lib/remark-soft-breaks.test.ts | verbatim | apps/desktop/src/lib/remark-soft-breaks.test.ts | 2dafbd6303dbd6d479a596d690088c28a1e8fa8ee5569560583f41219270e244 | 2dafbd6303dbd6d479a596d690088c28a1e8fa8ee5569560583f41219270e244 |
| src/hermes/lib/remark-soft-breaks.ts | verbatim | apps/desktop/src/lib/remark-soft-breaks.ts | f320771046457c313a591ba3d55f0dd61fcd0594a5dadf769a2bb54d9906c790 | f320771046457c313a591ba3d55f0dd61fcd0594a5dadf769a2bb54d9906c790 |
| src/hermes/lib/render-weight.test.ts | verbatim | apps/desktop/src/lib/render-weight.test.ts | 61952cad5cba788770b6d75a16c3fc7dc78ec413b38b6d243e434c531d7bc950 | 61952cad5cba788770b6d75a16c3fc7dc78ec413b38b6d243e434c531d7bc950 |
| src/hermes/lib/render-weight.ts | verbatim | apps/desktop/src/lib/render-weight.ts | 79e5a29a91c00c64668bbe405f9747e7299cb08b14b7fb8a2ab9d2210e71a58a | 79e5a29a91c00c64668bbe405f9747e7299cb08b14b7fb8a2ab9d2210e71a58a |
| src/hermes/lib/renderer-loop-pause.test.ts | verbatim | apps/desktop/src/lib/renderer-loop-pause.test.ts | f3ab88e79cad741f71c6516e4bcc43c8cf2884055908172737283698f6a8c581 | f3ab88e79cad741f71c6516e4bcc43c8cf2884055908172737283698f6a8c581 |
| src/hermes/lib/renderer-loop-pause.ts | patch | apps/desktop/src/lib/renderer-loop-pause.ts | 4c0d2938b84169e7740fbdb4740beaba45f6f9e816a2c69f61472d7d76707bd6 | 42623a5167cf7f5e5f8936392e85ae675508d97e144b3661822d4446e1f16686 |
| src/hermes/lib/reorder.ts | verbatim | apps/desktop/src/lib/reorder.ts | 37203c064466b869203a533b3854ab7973c97c554c7a4c0faa66d14627541b21 | 37203c064466b869203a533b3854ab7973c97c554c7a4c0faa66d14627541b21 |
| src/hermes/lib/sanitize.test.ts | verbatim | apps/desktop/src/lib/sanitize.test.ts | 9f93e6b8504dc7a1cd68d919c07670d2aca3bfc6818434ad6b165862d1bb6224 | 9f93e6b8504dc7a1cd68d919c07670d2aca3bfc6818434ad6b165862d1bb6224 |
| src/hermes/lib/sanitize.ts | verbatim | apps/desktop/src/lib/sanitize.ts | 07787e209585df3aa27de343db9ed8bba415ed23ddfd6f647f15252ef5c144b3 | 07787e209585df3aa27de343db9ed8bba415ed23ddfd6f647f15252ef5c144b3 |
| src/hermes/lib/selectable-card.ts | verbatim | apps/desktop/src/lib/selectable-card.ts | 42334aebd36c8456d9c9e9ad83900ea5df100b361476f43c547e508ab8fb9f6f | 42334aebd36c8456d9c9e9ad83900ea5df100b361476f43c547e508ab8fb9f6f |
| src/hermes/lib/session-branch-tree.test.ts | verbatim | apps/desktop/src/lib/session-branch-tree.test.ts | 9f386c3e2f3547fe5fee471e9d2a03e8b22969dcbf1ba83efeecd9fc8129c66f | 9f386c3e2f3547fe5fee471e9d2a03e8b22969dcbf1ba83efeecd9fc8129c66f |
| src/hermes/lib/session-branch-tree.ts | verbatim | apps/desktop/src/lib/session-branch-tree.ts | c687b238b216653d7a621b2935f0f33bde0093e27ea5519def56bb714897e89e | c687b238b216653d7a621b2935f0f33bde0093e27ea5519def56bb714897e89e |
| src/hermes/lib/session-date-groups.test.ts | verbatim | apps/desktop/src/lib/session-date-groups.test.ts | ea2adfdbaf8f2df0f9df352a3bbf43410737884d72776cbf3358533765e669a9 | ea2adfdbaf8f2df0f9df352a3bbf43410737884d72776cbf3358533765e669a9 |
| src/hermes/lib/session-date-groups.ts | verbatim | apps/desktop/src/lib/session-date-groups.ts | 78703db0777ea306f9a9efe265c23523b8307d63d6cf71bda37d99a1f641e875 | 78703db0777ea306f9a9efe265c23523b8307d63d6cf71bda37d99a1f641e875 |
| src/hermes/lib/session-export.ts | verbatim | apps/desktop/src/lib/session-export.ts | 3fc687a21f2f1eb935495a63f362c4629689eeae2bb41087d18fcd6c074eaa36 | 3fc687a21f2f1eb935495a63f362c4629689eeae2bb41087d18fcd6c074eaa36 |
| src/hermes/lib/session-link-title.ts | patch | apps/desktop/src/lib/session-link-title.ts | 9a571c0743ad18b3e6a398250e9c1ae71773896298447052d3d64404481195ec | 9cea65929f8254ee3ad670b607f90d7d0f0cf9b5b1de4820d0d2ec42e9f4456c |
| src/hermes/lib/session-project-label.ts | verbatim | apps/desktop/src/lib/session-project-label.ts | 86150809aac431a6d220b1ec44fb2524087d3da644dec184ba12524935c39548 | 86150809aac431a6d220b1ec44fb2524087d3da644dec184ba12524935c39548 |
| src/hermes/lib/session-refs.test.ts | verbatim | apps/desktop/src/lib/session-refs.test.ts | 1dfeebbe061c347bbc5456b90e3623021d6512958ce01d933dbc3de12a9509b4 | 1dfeebbe061c347bbc5456b90e3623021d6512958ce01d933dbc3de12a9509b4 |
| src/hermes/lib/session-refs.ts | verbatim | apps/desktop/src/lib/session-refs.ts | 48e4b2b406e806690c6acf574673e810bea03961edb531499e1bb846300f7d2a | 48e4b2b406e806690c6acf574673e810bea03961edb531499e1bb846300f7d2a |
| src/hermes/lib/session-row-slots.ts | verbatim | apps/desktop/src/lib/session-row-slots.ts | 6908905ef24a10bf481650270336bbe9e99c6e636797660770bf5dd6e625b8cf | 6908905ef24a10bf481650270336bbe9e99c6e636797660770bf5dd6e625b8cf |
| src/hermes/lib/session-search.test.ts | verbatim | apps/desktop/src/lib/session-search.test.ts | 1334f0c69eb42f31b38d9dd74422a605d1aa6a658223f46b3330daf32125f339 | 1334f0c69eb42f31b38d9dd74422a605d1aa6a658223f46b3330daf32125f339 |
| src/hermes/lib/session-search.ts | verbatim | apps/desktop/src/lib/session-search.ts | a80dd0401b1e8c72980539dce35eac85f935d374fc2d1426dbe89e7822bb3259 | a80dd0401b1e8c72980539dce35eac85f935d374fc2d1426dbe89e7822bb3259 |
| src/hermes/lib/session-signatures.test.ts | verbatim | apps/desktop/src/lib/session-signatures.test.ts | 643821156bcd6a41dc075a0e21597baa0c1cbc87fea49125dd3d488951bc9533 | 643821156bcd6a41dc075a0e21597baa0c1cbc87fea49125dd3d488951bc9533 |
| src/hermes/lib/session-signatures.ts | verbatim | apps/desktop/src/lib/session-signatures.ts | a1fbb1c24ec7fe0ddcb7ec397791ad3b3406df2b858798465fc18c58de068341 | a1fbb1c24ec7fe0ddcb7ec397791ad3b3406df2b858798465fc18c58de068341 |
| src/hermes/lib/shared-gateway-restart.test.ts | verbatim | apps/desktop/src/lib/shared-gateway-restart.test.ts | 90a9639acc3b5a95dd6028cf8e2fa9a4ed118adda9e07161a113aab7abb85c7a | 90a9639acc3b5a95dd6028cf8e2fa9a4ed118adda9e07161a113aab7abb85c7a |
| src/hermes/lib/shared-gateway-restart.ts | verbatim | apps/desktop/src/lib/shared-gateway-restart.ts | 24380650b48ff25bf745beed7f4b4443afe81119abdf6f218824cf278c71a2f8 | 24380650b48ff25bf745beed7f4b4443afe81119abdf6f218824cf278c71a2f8 |
| src/hermes/lib/sibling-ws-url.test.ts | verbatim | apps/desktop/src/lib/sibling-ws-url.test.ts | 32ff150733590d11cc3f20cc8e87fc531dfdb57babcbfcd8421aabdf34a43354 | 32ff150733590d11cc3f20cc8e87fc531dfdb57babcbfcd8421aabdf34a43354 |
| src/hermes/lib/sibling-ws-url.ts | patch | apps/desktop/src/lib/sibling-ws-url.ts | ecd1b323e23111062ca590ae55455db68413a4ced10f3a6c6b9ae01c89ec40c9 | b651e828984427996ec068224b7c51f965af9f685996e46dea7655233d6e3d9f |
| src/hermes/lib/slash-completion-cache.ts | verbatim | apps/desktop/src/lib/slash-completion-cache.ts | ad19508975bc44ad6df43b9eb054f9535cc9f0464c0c872ec110d704017d187e | ad19508975bc44ad6df43b9eb054f9535cc9f0464c0c872ec110d704017d187e |
| src/hermes/lib/speech-text.identifier-corpus.test.ts | verbatim | apps/desktop/src/lib/speech-text.identifier-corpus.test.ts | c988c866388d1a61adb4a0778b41a8aa0f52166219dd616ddd30be1704d42023 | c988c866388d1a61adb4a0778b41a8aa0f52166219dd616ddd30be1704d42023 |
| src/hermes/lib/speech-text.test.ts | verbatim | apps/desktop/src/lib/speech-text.test.ts | 7ee2ff0f891e33ca528175083e067cbecc5495876c3ad50ea0044111cb79dfe2 | 7ee2ff0f891e33ca528175083e067cbecc5495876c3ad50ea0044111cb79dfe2 |
| src/hermes/lib/speech-text.ts | verbatim | apps/desktop/src/lib/speech-text.ts | 8b2af85948ef41e69b072f2690b487229cc9bc0973c4ff67ffa4ae3a1a7103bf | 8b2af85948ef41e69b072f2690b487229cc9bc0973c4ff67ffa4ae3a1a7103bf |
| src/hermes/lib/stt-lease.test.ts | verbatim | apps/desktop/src/lib/stt-lease.test.ts | 779dc081d9da58448eae4d80a32f09182db84406db8253b115002cb317283b1f | 779dc081d9da58448eae4d80a32f09182db84406db8253b115002cb317283b1f |
| src/hermes/lib/stt-lease.ts | verbatim | apps/desktop/src/lib/stt-lease.ts | 26b74e6afc3eac68547bbf2aa4d65f865a626c179e862c1a860220997623d4df | 26b74e6afc3eac68547bbf2aa4d65f865a626c179e862c1a860220997623d4df |
| src/hermes/lib/summarize-command.test.ts | verbatim | apps/desktop/src/lib/summarize-command.test.ts | 292ef1ee4d2817715545685fdbe591e681f87e8390dfb52206beda933798a600 | 292ef1ee4d2817715545685fdbe591e681f87e8390dfb52206beda933798a600 |
| src/hermes/lib/summarize-command.ts | verbatim | apps/desktop/src/lib/summarize-command.ts | 8dbfd7d16fabc4ec9e142717e7989cdacaffa05d1c38733c772f3ab4c090fdc7 | 8dbfd7d16fabc4ec9e142717e7989cdacaffa05d1c38733c772f3ab4c090fdc7 |
| src/hermes/lib/svg-image-inline-cap.test.ts | verbatim | apps/desktop/src/lib/svg-image-inline-cap.test.ts | b5c6a8c3c2510495731b60811878c4595f29dee4409bbba38530a9d895a8cd09 | b5c6a8c3c2510495731b60811878c4595f29dee4409bbba38530a9d895a8cd09 |
| src/hermes/lib/svg-image.test.ts | verbatim | apps/desktop/src/lib/svg-image.test.ts | 411de0a6d495da0fbfe9913bb64c2da098e207135b591f4e8660354fe597c7ed | 411de0a6d495da0fbfe9913bb64c2da098e207135b591f4e8660354fe597c7ed |
| src/hermes/lib/svg-image.ts | verbatim | apps/desktop/src/lib/svg-image.ts | 6143d931a581fc9648aba0ec79fc32fcd5faabdb64e47b0edbd8e797a846b431 | 6143d931a581fc9648aba0ec79fc32fcd5faabdb64e47b0edbd8e797a846b431 |
| src/hermes/lib/thinking-sound.test.ts | verbatim | apps/desktop/src/lib/thinking-sound.test.ts | 13b3ba28894ea00aa26901db51e42fcb135962345eb3c1bdab4a96f0cf70c96c | 13b3ba28894ea00aa26901db51e42fcb135962345eb3c1bdab4a96f0cf70c96c |
| src/hermes/lib/thinking-sound.ts | verbatim | apps/desktop/src/lib/thinking-sound.ts | b059a7effa12a0dd2d5b7fa7b12be630cc44664c407c97f7244a62ed39e2f911 | b059a7effa12a0dd2d5b7fa7b12be630cc44664c407c97f7244a62ed39e2f911 |
| src/hermes/lib/time.test.ts | verbatim | apps/desktop/src/lib/time.test.ts | cff622b4e6ddf2e3303b3356855bf441831cc2f4ddab66ae760bf1d3d1f097f8 | cff622b4e6ddf2e3303b3356855bf441831cc2f4ddab66ae760bf1d3d1f097f8 |
| src/hermes/lib/time.ts | verbatim | apps/desktop/src/lib/time.ts | 00eabe90a297e0ae5c13ca2b2e048d089b47d8dec9f9c7ba3f5ac57acbe3c3f4 | 00eabe90a297e0ae5c13ca2b2e048d089b47d8dec9f9c7ba3f5ac57acbe3c3f4 |
| src/hermes/lib/tips/catalog.ts | verbatim | apps/desktop/src/lib/tips/catalog.ts | 2b60528ab693c21e5304dedc2bd95a6558e9c431c95f4aa6296cbb32dc87e0bc | 2b60528ab693c21e5304dedc2bd95a6558e9c431c95f4aa6296cbb32dc87e0bc |
| src/hermes/lib/tool-render-class.ts | verbatim | apps/desktop/src/lib/tool-render-class.ts | a9ea8e58b4914a19a2199967ff8ae824c1c4e41b6b87ddbc0dfeb3a91d29ba67 | a9ea8e58b4914a19a2199967ff8ae824c1c4e41b6b87ddbc0dfeb3a91d29ba67 |
| src/hermes/lib/tool-result-summary.test.ts | verbatim | apps/desktop/src/lib/tool-result-summary.test.ts | 2ebff3dad0e2447a1f245af86a86ebbe3702d019f80deb3a8ef3107a73d07f49 | 2ebff3dad0e2447a1f245af86a86ebbe3702d019f80deb3a8ef3107a73d07f49 |
| src/hermes/lib/tool-result-summary.ts | verbatim | apps/desktop/src/lib/tool-result-summary.ts | 716bc023b16ea0345e2054d864c6fe775fe515fbbdb712f08bb6028de400e787 | 716bc023b16ea0345e2054d864c6fe775fe515fbbdb712f08bb6028de400e787 |
| src/hermes/lib/tour/collect-targets.ts | verbatim | apps/desktop/src/lib/tour/collect-targets.ts | 57f8d8fd8404620f3578fba539137c91bb44e4633751d7c7922caef3777f2345 | 57f8d8fd8404620f3578fba539137c91bb44e4633751d7c7922caef3777f2345 |
| src/hermes/lib/tour/engine.test.ts | verbatim | apps/desktop/src/lib/tour/engine.test.ts | 01685942e7e5eee03ecb9f304a831d7b20fdd9d42bf13a583d7f664f59359240 | 01685942e7e5eee03ecb9f304a831d7b20fdd9d42bf13a583d7f664f59359240 |
| src/hermes/lib/tour/engine.ts | verbatim | apps/desktop/src/lib/tour/engine.ts | 579fa87cfc15bad0701e77dd7f1a178249e433f567c01da508c06dbcbfe1fbc0 | 579fa87cfc15bad0701e77dd7f1a178249e433f567c01da508c06dbcbfe1fbc0 |
| src/hermes/lib/tour/index.ts | verbatim | apps/desktop/src/lib/tour/index.ts | ebc4d65800abf5aa171c415f33d4e2cedc6d4faac3431a247d825867848aa0fc | ebc4d65800abf5aa171c415f33d4e2cedc6d4faac3431a247d825867848aa0fc |
| src/hermes/lib/tour/run-tour.ts | shim | apps/desktop/src/lib/tour/run-tour.ts | 3d8eb43237c3f0994585c98f81f1cef8974e76d1303c16d346a76aeab6a857d2 | 88c93aff9202acdae7afcc55a317782736c8881aaa7a3f41af58d1893427ebd6 |
| src/hermes/lib/trackpad-gestures.ts | verbatim | apps/desktop/src/lib/trackpad-gestures.ts | 2560cac2eaf3ce88b9ef334b3ebd90781396bfe22742fc504b154f84aefa0809 | 2560cac2eaf3ce88b9ef334b3ebd90781396bfe22742fc504b154f84aefa0809 |
| src/hermes/lib/transcript-directives.test.ts | verbatim | apps/desktop/src/lib/transcript-directives.test.ts | c770587b8c529dd6d1732701bdb951013d4a1db2b3ae8282cc9bc529e124f39e | c770587b8c529dd6d1732701bdb951013d4a1db2b3ae8282cc9bc529e124f39e |
| src/hermes/lib/transcript-directives.ts | verbatim | apps/desktop/src/lib/transcript-directives.ts | e65532cdda996ec9c3e6555c799aa041312de0a3104904d664a4de78a584fc96 | e65532cdda996ec9c3e6555c799aa041312de0a3104904d664a4de78a584fc96 |
| src/hermes/lib/tts-lease.test.ts | verbatim | apps/desktop/src/lib/tts-lease.test.ts | 33c04394bf155043e33d1de4dce35a9b119c0a6f934b884754477de74bd4358d | 33c04394bf155043e33d1de4dce35a9b119c0a6f934b884754477de74bd4358d |
| src/hermes/lib/tts-lease.ts | verbatim | apps/desktop/src/lib/tts-lease.ts | 5af3a5ed5f698b13ec5ca3d756703ac3da9780fe586b7b40cec6f326702653a1 | 5af3a5ed5f698b13ec5ca3d756703ac3da9780fe586b7b40cec6f326702653a1 |
| src/hermes/lib/usage-percent.test.ts | verbatim | apps/desktop/src/lib/usage-percent.test.ts | 041de5c0abe6f0a1e688a50a5db83967f2ac4d1abdd00268f90e54a6f7c235f8 | 041de5c0abe6f0a1e688a50a5db83967f2ac4d1abdd00268f90e54a6f7c235f8 |
| src/hermes/lib/usage-percent.ts | verbatim | apps/desktop/src/lib/usage-percent.ts | eb62fe3f990825c635601b4c3613c69e9be62a33afaa71016408b7f0a3ab5598 | eb62fe3f990825c635601b4c3613c69e9be62a33afaa71016408b7f0a3ab5598 |
| src/hermes/lib/use-enter-animation.test.tsx | verbatim | apps/desktop/src/lib/use-enter-animation.test.tsx | 0064d01f50dd42aecd86f98fd3bea7ea64bca7449ed864f0521f6f26aea8a0a1 | 0064d01f50dd42aecd86f98fd3bea7ea64bca7449ed864f0521f6f26aea8a0a1 |
| src/hermes/lib/use-enter-animation.ts | verbatim | apps/desktop/src/lib/use-enter-animation.ts | a8ddc182fbbf2ec07922014883d3b5472db65dc56914e7ecaf1aa3ff71ba412f | a8ddc182fbbf2ec07922014883d3b5472db65dc56914e7ecaf1aa3ff71ba412f |
| src/hermes/lib/use-session-slice.ts | verbatim | apps/desktop/src/lib/use-session-slice.ts | 98ce5ab9469825f044d79e563e04aee10a336d12f9c58551971ff7bfcbe14119 | 98ce5ab9469825f044d79e563e04aee10a336d12f9c58551971ff7bfcbe14119 |
| src/hermes/lib/use-stores-selector.test.tsx | verbatim | apps/desktop/src/lib/use-stores-selector.test.tsx | a49e9c91619b2c20cbe251028e7add0fa1952a2eb6ac2d7b7ed14bd047527d87 | a49e9c91619b2c20cbe251028e7add0fa1952a2eb6ac2d7b7ed14bd047527d87 |
| src/hermes/lib/utils.ts | verbatim | apps/desktop/src/lib/utils.ts | 746e4d9beb1a5b6f0e047ec19d5f1c542c9f8117f4ea7c80d7b9bd0fe2128e91 | 746e4d9beb1a5b6f0e047ec19d5f1c542c9f8117f4ea7c80d7b9bd0fe2128e91 |
| src/hermes/lib/voice-barge-in.test.ts | verbatim | apps/desktop/src/lib/voice-barge-in.test.ts | b90445cd590853f7780392b749a136f6ce0804b604d43b6690376de7f1246686 | b90445cd590853f7780392b749a136f6ce0804b604d43b6690376de7f1246686 |
| src/hermes/lib/voice-barge-in.ts | verbatim | apps/desktop/src/lib/voice-barge-in.ts | 08a8e7cb8bd2266d4fa5e573c0e16166e4e4e6445eb76da5282d47c7f268f460 | 08a8e7cb8bd2266d4fa5e573c0e16166e4e4e6445eb76da5282d47c7f268f460 |
| src/hermes/lib/voice-client-direct.test.ts | verbatim | apps/desktop/src/lib/voice-client-direct.test.ts | 46885941b1315de7b73fdec4156bcd5461e786df3aa64a0bc13489f0bcf303d0 | 46885941b1315de7b73fdec4156bcd5461e786df3aa64a0bc13489f0bcf303d0 |
| src/hermes/lib/voice-client-direct.ts | verbatim | apps/desktop/src/lib/voice-client-direct.ts | 40dee7c570686308dcd93fb5e32488d86a875d6e42578363efc1d53907b004d3 | 40dee7c570686308dcd93fb5e32488d86a875d6e42578363efc1d53907b004d3 |
| src/hermes/lib/voice-live.routing.test.ts | verbatim | apps/desktop/src/lib/voice-live.routing.test.ts | 0fa4fe0f46e1341c691e983b6f4424738a6d9c05026e36ef6f28ceea2f782748 | 0fa4fe0f46e1341c691e983b6f4424738a6d9c05026e36ef6f28ceea2f782748 |
| src/hermes/lib/voice-live.ts | verbatim | apps/desktop/src/lib/voice-live.ts | 94e9ef84c959f4c414cc2490baddbe271e1b72291abbe855cccfdb4de1fa7d0e | 94e9ef84c959f4c414cc2490baddbe271e1b72291abbe855cccfdb4de1fa7d0e |
| src/hermes/lib/voice-playback.routing.test.ts | verbatim | apps/desktop/src/lib/voice-playback.routing.test.ts | 459a11d89c1fd9e1d1fa848ea3eb77b4c0652d4f0df3c6851bd651d18e375dc6 | 459a11d89c1fd9e1d1fa848ea3eb77b4c0652d4f0df3c6851bd651d18e375dc6 |
| src/hermes/lib/voice-playback.same-turn.test.ts | verbatim | apps/desktop/src/lib/voice-playback.same-turn.test.ts | 21ab75a08dab1270a2f348bdeeb541f6a04b9074d2c57a331a60148a3d376169 | 21ab75a08dab1270a2f348bdeeb541f6a04b9074d2c57a331a60148a3d376169 |
| src/hermes/lib/voice-playback.test.ts | verbatim | apps/desktop/src/lib/voice-playback.test.ts | ed5796783da571db5601fb36ebecf85825fea8dc8e200438872e43fedef1b6d2 | ed5796783da571db5601fb36ebecf85825fea8dc8e200438872e43fedef1b6d2 |
| src/hermes/lib/voice-playback.ts | shim | apps/desktop/src/lib/voice-playback.ts | 2e394edad71ef04ee4b7baef5fb4f3329f9a02909037d456cac6899a24ee2646 | 500c684c94025b11951c3b414fdab689ecfb5702b9c9b49c1b87fdeae7fadb12 |
| src/hermes/lib/voice-stop-word.test.ts | verbatim | apps/desktop/src/lib/voice-stop-word.test.ts | e6c1aa165b2b02b480a350d29beb1fca09da5dde87356ac84c66d364e7251512 | e6c1aa165b2b02b480a350d29beb1fca09da5dde87356ac84c66d364e7251512 |
| src/hermes/lib/voice-stop-word.ts | verbatim | apps/desktop/src/lib/voice-stop-word.ts | c49c877ab57f9bd8ec1c45f337450952d074cf3cdae056660de8b21970fc0786 | c49c877ab57f9bd8ec1c45f337450952d074cf3cdae056660de8b21970fc0786 |
| src/hermes/lib/voice-stream.ts | verbatim | apps/desktop/src/lib/voice-stream.ts | 28234e6d0c0d2eec0882279445e786ee7b7d2d2bf8e53a349c14468e9464cb51 | 28234e6d0c0d2eec0882279445e786ee7b7d2d2bf8e53a349c14468e9464cb51 |
| src/hermes/lib/voice-tts-echo.test.ts | verbatim | apps/desktop/src/lib/voice-tts-echo.test.ts | 7e6648edaf0eccfa3b0b787fb702cec0d9110a46a33a62b5145d20922821852b | 7e6648edaf0eccfa3b0b787fb702cec0d9110a46a33a62b5145d20922821852b |
| src/hermes/lib/voice-tts-echo.ts | verbatim | apps/desktop/src/lib/voice-tts-echo.ts | 0f46880899ed3b9768bb3eac5bfd91223fe04f4f49894aaac825812ba81d59f7 | 0f46880899ed3b9768bb3eac5bfd91223fe04f4f49894aaac825812ba81d59f7 |
| src/hermes/lib/wake-client-capture.test.ts | verbatim | apps/desktop/src/lib/wake-client-capture.test.ts | 11ea29ac74f03c143c48931bc523cd6ece3172578604acddcecbc045db6c6eef | 11ea29ac74f03c143c48931bc523cd6ece3172578604acddcecbc045db6c6eef |
| src/hermes/lib/wake-client-capture.ts | verbatim | apps/desktop/src/lib/wake-client-capture.ts | 6ec5e571bd051cac11e64db4aea4f59d36ff6c0dc33942da015490373e37e334 | 6ec5e571bd051cac11e64db4aea4f59d36ff6c0dc33942da015490373e37e334 |
| src/hermes/lib/wake-indicator.test.ts | verbatim | apps/desktop/src/lib/wake-indicator.test.ts | 9441d5530fac0b5b571652bd3aead9709e3ed250ed85c9bea695e50e7faf2a66 | 9441d5530fac0b5b571652bd3aead9709e3ed250ed85c9bea695e50e7faf2a66 |
| src/hermes/lib/wake-indicator.ts | patch | apps/desktop/src/lib/wake-indicator.ts | a0e08d370d77a54d064cf959021628a60be0f8b958e138d3230848de5fe41d63 | 8a89919a5f82e98961e30eaa945da0fcbebccb0b266d8dd2b49aec09242491bb |
| src/hermes/lib/yolo-session.ts | verbatim | apps/desktop/src/lib/yolo-session.ts | e60bc173704d3c83d7c15e39104911f317f6026c28e793f635b78811b7ba35bb | e60bc173704d3c83d7c15e39104911f317f6026c28e793f635b78811b7ba35bb |
| src/hermes/pr9-capabilities.ts | new | — | — | 27cf9b87793cb1606350b785df4efae2b71b2ebfdd45fb0c6b2e9e506ea6044e |
| src/hermes/pr9-disabled-native.ts | new | — | — | b1cb56f0a1b4983c73d31cc7dd92f17ac1c02e1fe5c3c493ce334535c41d39d6 |
| src/hermes/pr9-native-types.ts | new | — | — | 56470ae8db881c5a83e99314fb66f10a4c15c768899b99f008f4818ea07f09a0 |
| src/hermes/pr9-particle-types.ts | new | — | — | 0c67907e9958eda643d07b21fac4e49ebd8bbfcfe7f4832ad01e2757ad380823 |
| src/hermes/store/activity.ts | verbatim | apps/desktop/src/store/activity.ts | 89cd0cf61b47084f442787bc95965cf093947d181e96ef99c267542743d517d1 | 89cd0cf61b47084f442787bc95965cf093947d181e96ef99c267542743d517d1 |
| src/hermes/store/agent-notices.test.ts | verbatim | apps/desktop/src/store/agent-notices.test.ts | f0787d2cb84bf810a80f67b1c4c7f9950a16884fb45ee65e97cddf2e242016f1 | f0787d2cb84bf810a80f67b1c4c7f9950a16884fb45ee65e97cddf2e242016f1 |
| src/hermes/store/agent-notices.ts | verbatim | apps/desktop/src/store/agent-notices.ts | f00b7b326481354678e7742140fea72dd4f31a9a8f6de7d21e49319a98ecdd7f | f00b7b326481354678e7742140fea72dd4f31a9a8f6de7d21e49319a98ecdd7f |
| src/hermes/store/agent-plugins.test.ts | verbatim | apps/desktop/src/store/agent-plugins.test.ts | 90c4456e8450ac269da8efb461879dee71d20c6af99c137ece6e1193c63e481f | 90c4456e8450ac269da8efb461879dee71d20c6af99c137ece6e1193c63e481f |
| src/hermes/store/agent-plugins.ts | verbatim | apps/desktop/src/store/agent-plugins.ts | fef80ca6f7489e6eaedb7df5bb88393758339a0d1e0e4497aa2e3ab0a248f7b0 | fef80ca6f7489e6eaedb7df5bb88393758339a0d1e0e4497aa2e3ab0a248f7b0 |
| src/hermes/store/ambient.ts | patch | apps/desktop/src/store/ambient.ts | 27be4c3eee0dab12d4ca7bb4edeb92b02d2b89da65f67998fe4225cba8ada65a | 772b6c306a51b3167c1cb5ea6e0bb7b469fb03f5252c7020664e653669dd4597 |
| src/hermes/store/appearance-apply.ts | verbatim | apps/desktop/src/store/appearance-apply.ts | dd4320408c6c3b4c2e064d2abdb32b183b289161dbde7554f8ad85c135b3caa1 | dd4320408c6c3b4c2e064d2abdb32b183b289161dbde7554f8ad85c135b3caa1 |
| src/hermes/store/artifacts.test.ts | verbatim | apps/desktop/src/store/artifacts.test.ts | 33891d0a9c4509a9b5f20c2b599d8283e85e70442f1fb882de0519ca4543d8f0 | 33891d0a9c4509a9b5f20c2b599d8283e85e70442f1fb882de0519ca4543d8f0 |
| src/hermes/store/artifacts.ts | verbatim | apps/desktop/src/store/artifacts.ts | 4420894a9a29ee6f9ef1d157b4a998b7ab42f1c8da94ca8e3ae15b04787f2b43 | 4420894a9a29ee6f9ef1d157b4a998b7ab42f1c8da94ca8e3ae15b04787f2b43 |
| src/hermes/store/backdrop.ts | verbatim | apps/desktop/src/store/backdrop.ts | 3939b4f25b4fe4880818f602f2557b17ea35523b6eb6e6a54890c1853a3580cb | 3939b4f25b4fe4880818f602f2557b17ea35523b6eb6e6a54890c1853a3580cb |
| src/hermes/store/background-delegation.test.ts | verbatim | apps/desktop/src/store/background-delegation.test.ts | b7404097e64fff7fd3776cbe7f518d614e701f50ccb93671e6753fd92750cf2e | b7404097e64fff7fd3776cbe7f518d614e701f50ccb93671e6753fd92750cf2e |
| src/hermes/store/background-delegation.ts | verbatim | apps/desktop/src/store/background-delegation.ts | dffae424160cb1bf2c6ef670d50a4dc28c756aebf83f7191e12aa71117d8b44e | dffae424160cb1bf2c6ef670d50a4dc28c756aebf83f7191e12aa71117d8b44e |
| src/hermes/store/busy-input-mode.ts | verbatim | apps/desktop/src/store/busy-input-mode.ts | f393c819f61b350957bd87f978dd2dddfa89163d4a06cbf857512fe982d3a62c | f393c819f61b350957bd87f978dd2dddfa89163d4a06cbf857512fe982d3a62c |
| src/hermes/store/coding-status.ts | patch | apps/desktop/src/store/coding-status.ts | ae9f2d119c8ff7dcc20d2b3e7b30ca5fc3bab5487135c1c7c88cf7c961d69ed1 | 05308c402e52e82a7b1094234292c585e4f546e55dc671a8723a63414ec63be5 |
| src/hermes/store/command-palette.ts | verbatim | apps/desktop/src/store/command-palette.ts | e04fccdb8b0e4540ac52d2b675b43349a41c4b02e81dd646aed4761c44ea2ba1 | e04fccdb8b0e4540ac52d2b675b43349a41c4b02e81dd646aed4761c44ea2ba1 |
| src/hermes/store/composer-actions.ts | verbatim | apps/desktop/src/store/composer-actions.ts | ad8d43f83e15bb6566a87dc32e5cf0408884bbc0ca3cdc942a4d6cc189e229d4 | ad8d43f83e15bb6566a87dc32e5cf0408884bbc0ca3cdc942a4d6cc189e229d4 |
| src/hermes/store/composer-draft-retry.ts | verbatim | apps/desktop/src/store/composer-draft-retry.ts | 70fedf50644e18aa30a3e96c46693408e742af4ed985a7c0211eb9a0360b8c7a | 70fedf50644e18aa30a3e96c46693408e742af4ed985a7c0211eb9a0360b8c7a |
| src/hermes/store/composer-input-history.test.ts | verbatim | apps/desktop/src/store/composer-input-history.test.ts | 8421682a637f550971bca79cf594d41a12f52ecd9f803fa2704e196581a0bab1 | 8421682a637f550971bca79cf594d41a12f52ecd9f803fa2704e196581a0bab1 |
| src/hermes/store/composer-input-history.ts | verbatim | apps/desktop/src/store/composer-input-history.ts | 01fc0ded36a53ae18c88a6a55243fa9d84afbc237b416e526800d621f7ec7ea1 | 01fc0ded36a53ae18c88a6a55243fa9d84afbc237b416e526800d621f7ec7ea1 |
| src/hermes/store/composer-popout-preference.test.ts | verbatim | apps/desktop/src/store/composer-popout-preference.test.ts | 8caa798b1cf1266d58097a60ccb714bf0007b9a0888a13830c43a192e9e4bd1a | 8caa798b1cf1266d58097a60ccb714bf0007b9a0888a13830c43a192e9e4bd1a |
| src/hermes/store/composer-popout.test.ts | verbatim | apps/desktop/src/store/composer-popout.test.ts | d769ee31eda82f7e17dcf02f39b1bb7547826890e4d4c1ae89384b33951bb298 | d769ee31eda82f7e17dcf02f39b1bb7547826890e4d4c1ae89384b33951bb298 |
| src/hermes/store/composer-popout.ts | verbatim | apps/desktop/src/store/composer-popout.ts | 491d7ec3ddb36fb92f2632e79b8add54f923978d8256497d188c1c8cedbedbd3 | 491d7ec3ddb36fb92f2632e79b8add54f923978d8256497d188c1c8cedbedbd3 |
| src/hermes/store/composer-status.test.ts | verbatim | apps/desktop/src/store/composer-status.test.ts | fa88b9f38cc458bff26d42f39298137fd7c0d81109d66bb95de01631bff9c224 | fa88b9f38cc458bff26d42f39298137fd7c0d81109d66bb95de01631bff9c224 |
| src/hermes/store/confirm.ts | verbatim | apps/desktop/src/store/confirm.ts | 0fd580f172042b252c20de539b5bf0f063c0612049daee2039ac0a5ad38c6269 | 0fd580f172042b252c20de539b5bf0f063c0612049daee2039ac0a5ad38c6269 |
| src/hermes/store/connection-request.test.ts | verbatim | apps/desktop/src/store/connection-request.test.ts | a516b3ef1180dcea7a9fde5a17bb55b5c6ffe21df991aaadf246219d251bf8f4 | a516b3ef1180dcea7a9fde5a17bb55b5c6ffe21df991aaadf246219d251bf8f4 |
| src/hermes/store/connection-request.ts | verbatim | apps/desktop/src/store/connection-request.ts | 150478f46f42e0e5bdbc1cac6e0b734150fc399be7f714771745e84a7ee7eaf8 | 150478f46f42e0e5bdbc1cac6e0b734150fc399be7f714771745e84a7ee7eaf8 |
| src/hermes/store/connections.ts | patch | apps/desktop/src/store/connections.ts | b738b618b1774b12fb0798e562fe68bb26d781657d7a166ee0ca0a58a8238584 | 973df1202f0b36c0b9339422ea0e44abc330a0bad3baa563ec58a5eae60dd2e8 |
| src/hermes/store/cron.test.ts | verbatim | apps/desktop/src/store/cron.test.ts | 2831e4e582b7f18f0defbb2fb6ca94042aac77c90a8a3159018582c80e615181 | 2831e4e582b7f18f0defbb2fb6ca94042aac77c90a8a3159018582c80e615181 |
| src/hermes/store/cron.ts | verbatim | apps/desktop/src/store/cron.ts | ee05ab53a317052a26755471a93d99fa1a76fae8174f0b3c144ffe5200f8f51e | ee05ab53a317052a26755471a93d99fa1a76fae8174f0b3c144ffe5200f8f51e |
| src/hermes/store/custom-models.test.ts | verbatim | apps/desktop/src/store/custom-models.test.ts | 990d6a0dbbfa16a1c56874431da13027bf10fa671a57f0f00b38995cc44551d0 | 990d6a0dbbfa16a1c56874431da13027bf10fa671a57f0f00b38995cc44551d0 |
| src/hermes/store/custom-models.ts | verbatim | apps/desktop/src/store/custom-models.ts | caf8563245a4774c6a5f7f3978cad4f9530df4a6b7756b237557cd83ee266f65 | caf8563245a4774c6a5f7f3978cad4f9530df4a6b7756b237557cd83ee266f65 |
| src/hermes/store/dead-session-prune.test.ts | verbatim | apps/desktop/src/store/dead-session-prune.test.ts | e6962392e7c93af8409433aa4b01b169501cceb1cf1c3eef22268b6db6b7f396 | e6962392e7c93af8409433aa4b01b169501cceb1cf1c3eef22268b6db6b7f396 |
| src/hermes/store/dead-session-prune.ts | verbatim | apps/desktop/src/store/dead-session-prune.ts | 8c524b8c3ef2eab87cc94f7342676699be51d118421a60d162b1168577b655a9 | 8c524b8c3ef2eab87cc94f7342676699be51d118421a60d162b1168577b655a9 |
| src/hermes/store/default-profile.ts | patch | apps/desktop/src/store/default-profile.ts | de4d508312c5eda2ddc3ab5f7e07497d04574ef8716ec5df569f8a1fac5c00e3 | c6b1349557cca49e13d975f851a1ee998dc78e69e1a837ea12de8ae76dda67c8 |
| src/hermes/store/display-timestamps.ts | verbatim | apps/desktop/src/store/display-timestamps.ts | 4c20a7515e3106c2c9ea5e6870f02a1700070a0c62914bb9137b0da6f31274de | 4c20a7515e3106c2c9ea5e6870f02a1700070a0c62914bb9137b0da6f31274de |
| src/hermes/store/display-toggles.ts | shim | apps/desktop/src/store/display-toggles.ts | a8ec13f9eedecc397a4e6712cf8d5d17a5d30fc90dd810e2533af01d892d49f7 | eeb6e7fb1fc1010471a1fc9e459c6ef35f1be35faa1e59b1d37d826863e159a7 |
| src/hermes/store/embed-consent.ts | verbatim | apps/desktop/src/store/embed-consent.ts | 5393f86c924ba21f41d3575a613a2c5e1ae8688d7326a14cc094c6b6e8e683e2 | 5393f86c924ba21f41d3575a613a2c5e1ae8688d7326a14cc094c6b6e8e683e2 |
| src/hermes/store/external-links.test.ts | verbatim | apps/desktop/src/store/external-links.test.ts | 77c453e46bf567c7606bda06977b124d222385c1a69038bf1db9613d1b15a245 | 77c453e46bf567c7606bda06977b124d222385c1a69038bf1db9613d1b15a245 |
| src/hermes/store/external-links.ts | verbatim | apps/desktop/src/store/external-links.ts | ec4798cc813af90d098acfff25fd7521e4da5c1974145b3f758422caf894dec6 | ec4798cc813af90d098acfff25fd7521e4da5c1974145b3f758422caf894dec6 |
| src/hermes/store/favorite-models.test.ts | verbatim | apps/desktop/src/store/favorite-models.test.ts | 09830d8ae75dce07003d43c914f270965a5e04183b1aa4c1104c3f7436e7d7b2 | 09830d8ae75dce07003d43c914f270965a5e04183b1aa4c1104c3f7436e7d7b2 |
| src/hermes/store/favorite-models.ts | verbatim | apps/desktop/src/store/favorite-models.ts | ce3e03df20c540829edab7ba58242e0d3e671fea2ea7288c3b1a56b672ca06c7 | ce3e03df20c540829edab7ba58242e0d3e671fea2ea7288c3b1a56b672ca06c7 |
| src/hermes/store/find-in-page.ts | patch | apps/desktop/src/store/find-in-page.ts | 4017d061faf27d8701dbe13cab1681bfd26402a3ca0785a6694b6e5efc37b25f | b9409a6eeb45cde91acbe725148f40096da86d5ca59355a0410709a29e2bedbc |
| src/hermes/store/fleet-roster.ts | patch | apps/desktop/src/store/fleet-roster.ts | 48c774868967c87a6ea1417dcd2410099fac5466800d9ab5e7ad3950c4e42a7a | b8b1954d4d44b579848223f373c650020fc0ba74376679742c83a72927c3ceb5 |
| src/hermes/store/free-tier-challenge.test.ts | verbatim | apps/desktop/src/store/free-tier-challenge.test.ts | fe681a8fab45a97221b0c8bf1b7b8766470dbda30da73e270ddea54aff3370b4 | fe681a8fab45a97221b0c8bf1b7b8766470dbda30da73e270ddea54aff3370b4 |
| src/hermes/store/free-tier-challenge.ts | patch | apps/desktop/src/store/free-tier-challenge.ts | ac7467905d4c6b43ae43d784a8f54fb83adf132a048cef743bbec5874f11d4cd | 86c8754d8f6c3862a0b2250578cd9ea3ecbcacb7253716f0cef3ce3e50377808 |
| src/hermes/store/free-tier-setup-failure.test.ts | verbatim | apps/desktop/src/store/free-tier-setup-failure.test.ts | 6cbedd8ff333a577812463738ede973b35382b53da238d17997acb1249717379 | 6cbedd8ff333a577812463738ede973b35382b53da238d17997acb1249717379 |
| src/hermes/store/free-tier-sign-in.test.ts | verbatim | apps/desktop/src/store/free-tier-sign-in.test.ts | 38769be2c5a29f1eeb14e7febc7a0b6e0ede67309e2d5a35e6187708a47509ec | 38769be2c5a29f1eeb14e7febc7a0b6e0ede67309e2d5a35e6187708a47509ec |
| src/hermes/store/free-tier.ts | verbatim | apps/desktop/src/store/free-tier.ts | a2cce13bd3c33c36fdd701d067e03a0326dde9f8e9627ab2e9b9354fbf57b85f | a2cce13bd3c33c36fdd701d067e03a0326dde9f8e9627ab2e9b9354fbf57b85f |
| src/hermes/store/gateway-switch.test.ts | verbatim | apps/desktop/src/store/gateway-switch.test.ts | b42e09981683eebd96535aa81f06d20fbecc64fae88f9db60c064db6a3746d32 | b42e09981683eebd96535aa81f06d20fbecc64fae88f9db60c064db6a3746d32 |
| src/hermes/store/gateway-switch.ts | verbatim | apps/desktop/src/store/gateway-switch.ts | 7941c57c86783fb9953f01924ca4579f3fa9610288c617eead17cd2579ca43e0 | 7941c57c86783fb9953f01924ca4579f3fa9610288c617eead17cd2579ca43e0 |
| src/hermes/store/haptics.ts | verbatim | apps/desktop/src/store/haptics.ts | c586f41c6a44658c581b0adee5bb1af755912d7d53161305e1838e1d3bd37e21 | c586f41c6a44658c581b0adee5bb1af755912d7d53161305e1838e1d3bd37e21 |
| src/hermes/store/hud.test.ts | verbatim | apps/desktop/src/store/hud.test.ts | 0eede59f529b3a312d2b94c6f8f73968c2d9f1e76890c48a0557ab9f13e78ed3 | 0eede59f529b3a312d2b94c6f8f73968c2d9f1e76890c48a0557ab9f13e78ed3 |
| src/hermes/store/hud.ts | shim | apps/desktop/src/store/hud.ts | 21a015cdefb03fd013a5e9cb296bd8f7cccf3fb790fc7fead4951bfc3f2117a1 | 62104288437a3bff215f415668a7e75186d73cf62aae48329c7386bc2eea7468 |
| src/hermes/store/interface-mode.test.ts | verbatim | apps/desktop/src/store/interface-mode.test.ts | d65e9d204b11f222de5e88c99739f3bfb213fd60ac0dfecb099e72b77031e15f | d65e9d204b11f222de5e88c99739f3bfb213fd60ac0dfecb099e72b77031e15f |
| src/hermes/store/intro-splash.ts | verbatim | apps/desktop/src/store/intro-splash.ts | 3101c0606f1563d2a9e1d38ba180c4ed713b3addfaae66d5438cc31037cb172e | 3101c0606f1563d2a9e1d38ba180c4ed713b3addfaae66d5438cc31037cb172e |
| src/hermes/store/keybinds.test.ts | verbatim | apps/desktop/src/store/keybinds.test.ts | c97d25730d5546d37313648f5bcdd39884a1377ad4ed8813c42286a43fb7f3ec | c97d25730d5546d37313648f5bcdd39884a1377ad4ed8813c42286a43fb7f3ec |
| src/hermes/store/keybinds.ts | verbatim | apps/desktop/src/store/keybinds.ts | faf9715985659faf8031654c2318650710575b6c8f2149183f6a129076fb6187 | faf9715985659faf8031654c2318650710575b6c8f2149183f6a129076fb6187 |
| src/hermes/store/layout-dismissed-projects.test.ts | verbatim | apps/desktop/src/store/layout-dismissed-projects.test.ts | 4d1d538bfab59c8964ef2b78023b66424c6e596749473fdb4ca03ea3b58205bb | 4d1d538bfab59c8964ef2b78023b66424c6e596749473fdb4ca03ea3b58205bb |
| src/hermes/store/layout-flipped-sides.test.ts | verbatim | apps/desktop/src/store/layout-flipped-sides.test.ts | 81a0459684b5a9b56d17338ee7cdcaf5520e127d81d06da1b76c635ce870a185 | 81a0459684b5a9b56d17338ee7cdcaf5520e127d81d06da1b76c635ce870a185 |
| src/hermes/store/layout-pinned-order.test.ts | verbatim | apps/desktop/src/store/layout-pinned-order.test.ts | 2307d5ec7829cb81509d264d6fd8fc0de5932ddc588361f9c0fd8c8547f1c204 | 2307d5ec7829cb81509d264d6fd8fc0de5932ddc588361f9c0fd8c8547f1c204 |
| src/hermes/store/layout-sidebar-view.test.ts | verbatim | apps/desktop/src/store/layout-sidebar-view.test.ts | 116d7195c26226f28dd1eb292478d4955f5cb027a996fd318d424576d327f374 | 116d7195c26226f28dd1eb292478d4955f5cb027a996fd318d424576d327f374 |
| src/hermes/store/layout.ts | verbatim | apps/desktop/src/store/layout.ts | 4ae5310b2f1c0f145baa680483e82698810dbf98d0f6c870f09d33d22b2a3e91 | 4ae5310b2f1c0f145baa680483e82698810dbf98d0f6c870f09d33d22b2a3e91 |
| src/hermes/store/local-models-flag.ts | patch | apps/desktop/src/store/local-models-flag.ts | 80a5c80f101bc02aceae8fde7aa7c5f9a161414cd108cd46f92392f7faa843f7 | a1459d04e3f664bf373024bd49147c6394efc6b265381c81a50d5bd02f080f1c |
| src/hermes/store/local-runtime-jobs.test.ts | verbatim | apps/desktop/src/store/local-runtime-jobs.test.ts | f4d3f7a5724942d77106818918c7db76c6e8a2294a173f730e84e8fa8da8ec98 | f4d3f7a5724942d77106818918c7db76c6e8a2294a173f730e84e8fa8da8ec98 |
| src/hermes/store/local-runtime-jobs.ts | verbatim | apps/desktop/src/store/local-runtime-jobs.ts | 5a9073fe0be0bdcfe4b83fde05596237e3b943f9bc05a0ee51132a377c9ab7fb | 5a9073fe0be0bdcfe4b83fde05596237e3b943f9bc05a0ee51132a377c9ab7fb |
| src/hermes/store/local-setup-offer.ts | verbatim | apps/desktop/src/store/local-setup-offer.ts | bd3ccde0221b5aff91c27c7338a7029bbac8dcf15fdd4ed754d959bcb6cd47a3 | bd3ccde0221b5aff91c27c7338a7029bbac8dcf15fdd4ed754d959bcb6cd47a3 |
| src/hermes/store/model-presets.test.ts | verbatim | apps/desktop/src/store/model-presets.test.ts | a28bf5de6db497824c09d16316ee501088f8905193b90e29aa628321db98cc91 | a28bf5de6db497824c09d16316ee501088f8905193b90e29aa628321db98cc91 |
| src/hermes/store/model-presets.ts | verbatim | apps/desktop/src/store/model-presets.ts | 31767d2b87d6f1445da51c0d96d80b61ac4928a0419efd69f57686d7ad6b5089 | 31767d2b87d6f1445da51c0d96d80b61ac4928a0419efd69f57686d7ad6b5089 |
| src/hermes/store/model-pricing.ts | verbatim | apps/desktop/src/store/model-pricing.ts | 49c9c90554fd5bcdd0a51f4756818ca211f2f85a50ba29b31da3d9e862815574 | 49c9c90554fd5bcdd0a51f4756818ca211f2f85a50ba29b31da3d9e862815574 |
| src/hermes/store/model-visibility.test.ts | verbatim | apps/desktop/src/store/model-visibility.test.ts | 7ada134b9120361a0eadb608e0e4671fee3971e48805b3b2772756691a1fb124 | 7ada134b9120361a0eadb608e0e4671fee3971e48805b3b2772756691a1fb124 |
| src/hermes/store/model-visibility.ts | verbatim | apps/desktop/src/store/model-visibility.ts | 7aa9f39822b950a545c24d3b657aeb3e0d710333d8dfd4a3533bd21388233722 | 7aa9f39822b950a545c24d3b657aeb3e0d710333d8dfd4a3533bd21388233722 |
| src/hermes/store/notifications.test.ts | verbatim | apps/desktop/src/store/notifications.test.ts | 4b04db4529c68341c2ef85849ad0c3bd7d90b0c980f2dde9f35f9c9cc7136d51 | 4b04db4529c68341c2ef85849ad0c3bd7d90b0c980f2dde9f35f9c9cc7136d51 |
| src/hermes/store/onboarding-intro.ts | verbatim | apps/desktop/src/store/onboarding-intro.ts | 6f363147bab2495d26c83df35c65704c8fb68906378e280302c7f6eb33a38b3e | 6f363147bab2495d26c83df35c65704c8fb68906378e280302c7f6eb33a38b3e |
| src/hermes/store/onboarding-phase.ts | verbatim | apps/desktop/src/store/onboarding-phase.ts | f691a7ac737e7cbc89a4f48acc05308ed63f30eda217c3a00b540b9c4428c848 | f691a7ac737e7cbc89a4f48acc05308ed63f30eda217c3a00b540b9c4428c848 |
| src/hermes/store/onboarding-presence.ts | verbatim | apps/desktop/src/store/onboarding-presence.ts | 3d6e8a5354577266c03d91e6e84fc0ebcc87c00620bdf214061fbd2363514e06 | 3d6e8a5354577266c03d91e6e84fc0ebcc87c00620bdf214061fbd2363514e06 |
| src/hermes/store/onboarding-run.test.ts | verbatim | apps/desktop/src/store/onboarding-run.test.ts | c7edc50275504b81852eb46133eafc5cd42a014fc306882e0c0fe247d799d8ef | c7edc50275504b81852eb46133eafc5cd42a014fc306882e0c0fe247d799d8ef |
| src/hermes/store/onboarding-run.ts | patch | apps/desktop/src/store/onboarding-run.ts | 7895cc57b9e91de661f485104df026740221bc2a48ccf67060a99895cd29d176 | b9b4a54df5f94394af4d06262be737c01185c2e0ddde2d36a82f164f225a55f6 |
| src/hermes/store/pane-focus.ts | verbatim | apps/desktop/src/store/pane-focus.ts | 94b1f4140a9b2970f17a1736ade29a5f7f35cd240274e68f1186399a89875646 | 94b1f4140a9b2970f17a1736ade29a5f7f35cd240274e68f1186399a89875646 |
| src/hermes/store/panes.test.ts | verbatim | apps/desktop/src/store/panes.test.ts | 1b344f8c66ee584a7cc7779e23042c06a69c3eee28a9fd7acdf665004d930064 | 1b344f8c66ee584a7cc7779e23042c06a69c3eee28a9fd7acdf665004d930064 |
| src/hermes/store/panes.ts | verbatim | apps/desktop/src/store/panes.ts | b794597749f2a9e0d5f3ea26f4507d6fec60941c216f7995a319f0fcad802abc | b794597749f2a9e0d5f3ea26f4507d6fec60941c216f7995a319f0fcad802abc |
| src/hermes/store/parked-card-answers.test.ts | verbatim | apps/desktop/src/store/parked-card-answers.test.ts | e0bc18dbea0ca30f1e08795ec465159f254f4a9d772213ab1a967796b9d07c95 | e0bc18dbea0ca30f1e08795ec465159f254f4a9d772213ab1a967796b9d07c95 |
| src/hermes/store/pet-gallery.test.ts | verbatim | apps/desktop/src/store/pet-gallery.test.ts | b9e2bbff950d993cec059ae8f5b0e2e3e0dd7a411930723c2b88111dff3c9d90 | b9e2bbff950d993cec059ae8f5b0e2e3e0dd7a411930723c2b88111dff3c9d90 |
| src/hermes/store/pet-gallery.ts | shim | apps/desktop/src/store/pet-gallery.ts | 4c2786c6c112ce19929d2b5095aa4562ddd4fdc150d8d09476057e1048df3ab2 | 6b8ef11861c183dddbb4c6f6cf20c6a861e1346a5ecf8b03a7ce5947e1c4064a |
| src/hermes/store/pet-generate.ts | shim | apps/desktop/src/store/pet-generate.ts | 7ea0640e13c3d44adead30c996fdf708370cfa1ff3d9715ca8e4696856d0785d | 3e5446d0570a861d00262d0d0ef334596ed53cad77e7201784d2ebd53aa01d9c |
| src/hermes/store/pet-overlay.ts | shim | apps/desktop/src/store/pet-overlay.ts | e2671033846d406a88a69b616803cd2333273cb224a29ae3e6b89f95ea95382b | 1ea04a64fa56272cf208a5c62ac371328a16f05b1c4df0a8273ffd00297d30c8 |
| src/hermes/store/pet-plugin-messages.ts | verbatim | apps/desktop/src/store/pet-plugin-messages.ts | f975e297d3186bcbf0139d4e0eef1d32e24c32c53112804984c9ac3f1375a2d0 | f975e297d3186bcbf0139d4e0eef1d32e24c32c53112804984c9ac3f1375a2d0 |
| src/hermes/store/plugin-catalog-install.test.ts | verbatim | apps/desktop/src/store/plugin-catalog-install.test.ts | 588a463cc6ef0e1a513a77686399f38121d340999aaa169d4ab2caddfed7caaa | 588a463cc6ef0e1a513a77686399f38121d340999aaa169d4ab2caddfed7caaa |
| src/hermes/store/plugin-catalog-install.ts | verbatim | apps/desktop/src/store/plugin-catalog-install.ts | 098657509988026eed1ce310eb8db5bfed7e9f238a57345a39e61d9c341269f2 | 098657509988026eed1ce310eb8db5bfed7e9f238a57345a39e61d9c341269f2 |
| src/hermes/store/plugin-install-request.ts | verbatim | apps/desktop/src/store/plugin-install-request.ts | a232272d7bd72db40529a6a9667dd8eb8c3eaaaca04856d85572426f7fe72fbf | a232272d7bd72db40529a6a9667dd8eb8c3eaaaca04856d85572426f7fe72fbf |
| src/hermes/store/power.ts | patch | apps/desktop/src/store/power.ts | c8ff8b85636b9945165f702b6244397f8c678a4358a2f1dea8f66689a4f4c1a1 | 2f04dc0a51f4a85ee770c9747d2689bc0e8cfeddfd6f67a63193008676f9fb9c |
| src/hermes/store/preview-explicit.ts | verbatim | apps/desktop/src/store/preview-explicit.ts | a210e9d5d90195a9d1a7791b3b485de9638fcfa3179d9af136e0d92ba8fdfd6e | a210e9d5d90195a9d1a7791b3b485de9638fcfa3179d9af136e0d92ba8fdfd6e |
| src/hermes/store/preview-status-edge-cases.test.ts | verbatim | apps/desktop/src/store/preview-status-edge-cases.test.ts | 891612f9e920a141e50867bde93902ebf42a53a4737305fb56dc30f81b1eb41d | 891612f9e920a141e50867bde93902ebf42a53a4737305fb56dc30f81b1eb41d |
| src/hermes/store/preview-status-lifecycle.test.ts | verbatim | apps/desktop/src/store/preview-status-lifecycle.test.ts | ea3247d266c7cc23fae8c5e01e519e31abacec3c8896a1adf945aec26790af53 | ea3247d266c7cc23fae8c5e01e519e31abacec3c8896a1adf945aec26790af53 |
| src/hermes/store/preview-status.test.ts | verbatim | apps/desktop/src/store/preview-status.test.ts | ff8016f6f4de20b75939c00c459afe97b8e7b572e61e54fb28c72f9603e916ff | ff8016f6f4de20b75939c00c459afe97b8e7b572e61e54fb28c72f9603e916ff |
| src/hermes/store/profile-rail-prefs.ts | verbatim | apps/desktop/src/store/profile-rail-prefs.ts | 6b2bda68f7b8eff94c2bf082ae44ccd6ccb5e921dd09647d0c3e4ac96b6449b6 | 6b2bda68f7b8eff94c2bf082ae44ccd6ccb5e921dd09647d0c3e4ac96b6449b6 |
| src/hermes/store/profile-share.test.ts | verbatim | apps/desktop/src/store/profile-share.test.ts | c035703fb46a75ab63cb5416bc4349cb89a18be2bcf2df3a4e1e3a4e927e2992 | c035703fb46a75ab63cb5416bc4349cb89a18be2bcf2df3a4e1e3a4e927e2992 |
| src/hermes/store/profile-share.ts | patch | apps/desktop/src/store/profile-share.ts | 7b9725e770bdc7724723cbedb7c1377ac7062964de6e331931f9d415977b5813 | 7008d9db882faa8a27f945f31362b832a2872f44f2aa353ba4c512bfb0dcbd41 |
| src/hermes/store/project-boundary-types.ts | new | — | — | b5e0d0da5afafeda32509dc3c1a3d1e654fbcc9ce8114f015b6aeb670e169313 |
| src/hermes/store/project-scope.ts | verbatim | apps/desktop/src/store/project-scope.ts | c9526ae3db4f5bff1adab0fe2fa48631c60864fa1bf78a23c781652780829d3d | c9526ae3db4f5bff1adab0fe2fa48631c60864fa1bf78a23c781652780829d3d |
| src/hermes/store/prompts.test.ts | verbatim | apps/desktop/src/store/prompts.test.ts | 436abbb1aa078c3e848499bdd697c2d2f03ab07a6f0778cafaea48e5b473cc1a | 436abbb1aa078c3e848499bdd697c2d2f03ab07a6f0778cafaea48e5b473cc1a |
| src/hermes/store/provider-collapse.ts | verbatim | apps/desktop/src/store/provider-collapse.ts | 232b211b5de22f8c1575614e2e307167f0a4332f41eb88768db3f284c5c7b74c | 232b211b5de22f8c1575614e2e307167f0a4332f41eb88768db3f284c5c7b74c |
| src/hermes/store/pull-requests.ts | patch | apps/desktop/src/store/pull-requests.ts | 23e6c8806c289b94f76992ebc42fb3cd428769ebee311600afcdefbd4e6727e1 | 2aa7fbd158467116b6e6f7ddc1859917d06cfa82856aeaf39065d4399fbe53e3 |
| src/hermes/store/reactions-enabled.ts | verbatim | apps/desktop/src/store/reactions-enabled.ts | d15fa20c153e896b01ffabee97b8de94237a0121a1a0b929b0536e9472f849a7 | d15fa20c153e896b01ffabee97b8de94237a0121a1a0b929b0536e9472f849a7 |
| src/hermes/store/reactions-local.test.ts | verbatim | apps/desktop/src/store/reactions-local.test.ts | 0aa73624bacac5718ce86bcf0aa8e7d53505a980b13ee1457e3e84a14599ae47 | 0aa73624bacac5718ce86bcf0aa8e7d53505a980b13ee1457e3e84a14599ae47 |
| src/hermes/store/reactions-local.ts | verbatim | apps/desktop/src/store/reactions-local.ts | 32ba6762f8b6eee5af4842ccc9b43d89f92c3800dd8424675af57a0160382b33 | 32ba6762f8b6eee5af4842ccc9b43d89f92c3800dd8424675af57a0160382b33 |
| src/hermes/store/reactions.test.ts | verbatim | apps/desktop/src/store/reactions.test.ts | 45142fc27f867bc710494e8fd71b01888181184c6eb14ebc1df64b47d99c5d1d | 45142fc27f867bc710494e8fd71b01888181184c6eb14ebc1df64b47d99c5d1d |
| src/hermes/store/reactions.ts | verbatim | apps/desktop/src/store/reactions.ts | a46842cfd1a3210c7304efc180d2d44189be5230d8c718fd0d9c8fec8089c768 | a46842cfd1a3210c7304efc180d2d44189be5230d8c718fd0d9c8fec8089c768 |
| src/hermes/store/read-only-transcript.test.ts | verbatim | apps/desktop/src/store/read-only-transcript.test.ts | 432d8e27f547a37e86a772f09e7bbebf4c3b4c536c7bbe72d747a11e7d5b5564 | 432d8e27f547a37e86a772f09e7bbebf4c3b4c536c7bbe72d747a11e7d5b5564 |
| src/hermes/store/read-only-transcript.ts | verbatim | apps/desktop/src/store/read-only-transcript.ts | 747b631f560fafa980108e8cae53d11d2e6330dec66afb28ac5d6b1718ccfe7e | 747b631f560fafa980108e8cae53d11d2e6330dec66afb28ac5d6b1718ccfe7e |
| src/hermes/store/reasoning-disclosure.test.ts | verbatim | apps/desktop/src/store/reasoning-disclosure.test.ts | 4e74d38fc28c62487d327d46a762c844647960c9cf1b0cbc448fc306207b02c5 | 4e74d38fc28c62487d327d46a762c844647960c9cf1b0cbc448fc306207b02c5 |
| src/hermes/store/reasoning-disclosure.ts | verbatim | apps/desktop/src/store/reasoning-disclosure.ts | b66fffbc1ed299b2a5c6635be5e46033498706695d434d7904bcd4670ec46fa6 | b66fffbc1ed299b2a5c6635be5e46033498706695d434d7904bcd4670ec46fa6 |
| src/hermes/store/review.ts | patch | apps/desktop/src/store/review.ts | 63eef48da3a9135c250b353716584350ca9ec26e8256638fd87b90963cb0daaf | 3e6a51c6497e266dfe9e1f4e2d40b0cf5c824bf4d05a7eaabd5fbb147c52fa5c |
| src/hermes/store/route-tiles.ts | verbatim | apps/desktop/src/store/route-tiles.ts | 2291af26fe5b356aa7db7a3d9f81b8eb890181e2d1c5f7d90fcf9cdb4547a482 | 2291af26fe5b356aa7db7a3d9f81b8eb890181e2d1c5f7d90fcf9cdb4547a482 |
| src/hermes/store/send-diagnostics.ts | shim | apps/desktop/src/store/send-diagnostics.ts | 8c3e5e4b6add733316d85f028b63e1b12e78791fefff4e81bfd935e7aa410e76 | ca630f212a1ded4d7877960da4bcf48bfd4d6af22709c6a262024eec3d0f18e2 |
| src/hermes/store/session-color.test.ts | verbatim | apps/desktop/src/store/session-color.test.ts | 98af88036c1293e09434dfd2803e14b19c1f34edbec753058549dadbfcaa1934 | 98af88036c1293e09434dfd2803e14b19c1f34edbec753058549dadbfcaa1934 |
| src/hermes/store/session-color.ts | verbatim | apps/desktop/src/store/session-color.ts | b329910bc3c09ccfb3a334fd115f2c3f69cfee794d5657d1d07f4fb03279675e | b329910bc3c09ccfb3a334fd115f2c3f69cfee794d5657d1d07f4fb03279675e |
| src/hermes/store/session-dot-state.test.ts | verbatim | apps/desktop/src/store/session-dot-state.test.ts | 0c960e86c8f67fe6730f32ee9e5442dc9789f13b74faa381c03c6f674f62f869 | 0c960e86c8f67fe6730f32ee9e5442dc9789f13b74faa381c03c6f674f62f869 |
| src/hermes/store/session-dot-state.ts | verbatim | apps/desktop/src/store/session-dot-state.ts | 68a45ef3a5947b3f75bd086975827d204304150e5c5f9972c1632b70d8995b9d | 68a45ef3a5947b3f75bd086975827d204304150e5c5f9972c1632b70d8995b9d |
| src/hermes/store/session-list-density.test.ts | verbatim | apps/desktop/src/store/session-list-density.test.ts | 2333eec77bd0682293eabc13d83c97d036cfbf450eae8d5e22964adf24ba8384 | 2333eec77bd0682293eabc13d83c97d036cfbf450eae8d5e22964adf24ba8384 |
| src/hermes/store/session-list-density.ts | verbatim | apps/desktop/src/store/session-list-density.ts | 88aa108e03284169fdf56b88d517d0bade69b966540ed195610470907b5f7a48 | 88aa108e03284169fdf56b88d517d0bade69b966540ed195610470907b5f7a48 |
| src/hermes/store/session-owner-resolution.test.ts | verbatim | apps/desktop/src/store/session-owner-resolution.test.ts | 7c79fa16b9b46f41eed1602c74ff5c0345c76bc54b73e6e0a35320cab82da196 | 7c79fa16b9b46f41eed1602c74ff5c0345c76bc54b73e6e0a35320cab82da196 |
| src/hermes/store/session-pin-connection-scope.test.ts | verbatim | apps/desktop/src/store/session-pin-connection-scope.test.ts | 992d89b6f8432a55537c1b8ec29080fa54978819b0e16940052d26e349ed0e76 | 992d89b6f8432a55537c1b8ec29080fa54978819b0e16940052d26e349ed0e76 |
| src/hermes/store/session-pin-sync.test.ts | verbatim | apps/desktop/src/store/session-pin-sync.test.ts | f26a58d41f74007c7a6215993fe8fee8195460e44995d4986c84ff96b12124f4 | f26a58d41f74007c7a6215993fe8fee8195460e44995d4986c84ff96b12124f4 |
| src/hermes/store/session-pin-sync.ts | patch | apps/desktop/src/store/session-pin-sync.ts | fe1e4753b5d938a082a0b851566e5a1e284129baea5b29e478de7239d68e02d5 | 5ce50141334b0b0bdef3676a798754557c3d0414f1646abf4aa67eaf5c644fac |
| src/hermes/store/session-states-eviction.test.ts | verbatim | apps/desktop/src/store/session-states-eviction.test.ts | baa1d9e55fa75ed3555fd4dc34194f242905184f933bcffd1c7e29ad2c4619bb | baa1d9e55fa75ed3555fd4dc34194f242905184f933bcffd1c7e29ad2c4619bb |
| src/hermes/store/session-states-foreground-scopes.test.ts | verbatim | apps/desktop/src/store/session-states-foreground-scopes.test.ts | a36ce36e493a5fb2812c85b9d054107375d8e7c97a1a90d2632876cbf4e85b63 | a36ce36e493a5fb2812c85b9d054107375d8e7c97a1a90d2632876cbf4e85b63 |
| src/hermes/store/session-states-interrupted-settle.test.ts | verbatim | apps/desktop/src/store/session-states-interrupted-settle.test.ts | 789f9e1f12fde28f9b157ca5ead689951b9f54c06c15764ee0218733620c6b49 | 789f9e1f12fde28f9b157ca5ead689951b9f54c06c15764ee0218733620c6b49 |
| src/hermes/store/session-states-profile-rename.test.ts | verbatim | apps/desktop/src/store/session-states-profile-rename.test.ts | bc94acc49ae47a0620495ec4019221975457daa413b93ad7684b2b6024af65c3 | bc94acc49ae47a0620495ec4019221975457daa413b93ad7684b2b6024af65c3 |
| src/hermes/store/session-states-reconnect.test.ts | verbatim | apps/desktop/src/store/session-states-reconnect.test.ts | 41cb6e7d8ffb541d59e99053dda1f6a854b0bac54f5fe036d9067416c4d5d549 | 41cb6e7d8ffb541d59e99053dda1f6a854b0bac54f5fe036d9067416c4d5d549 |
| src/hermes/store/session-states-runtime-map.test.ts | verbatim | apps/desktop/src/store/session-states-runtime-map.test.ts | ed526492baf3209ff5afaf241a328a23ca0473d282da78a8a95d486de1fb6567 | ed526492baf3209ff5afaf241a328a23ca0473d282da78a8a95d486de1fb6567 |
| src/hermes/store/session-states-scopes.test.ts | verbatim | apps/desktop/src/store/session-states-scopes.test.ts | f3095d55181c1f7dccacf71d1eb37521a9d3bb9a4ffe446cea5b96ceb6924cea | f3095d55181c1f7dccacf71d1eb37521a9d3bb9a4ffe446cea5b96ceb6924cea |
| src/hermes/store/session-switcher.test.ts | verbatim | apps/desktop/src/store/session-switcher.test.ts | 832944192e2f029ad470132a85c47c2acec5b630b24a3f0d51dd0092d1e13c69 | 832944192e2f029ad470132a85c47c2acec5b630b24a3f0d51dd0092d1e13c69 |
| src/hermes/store/session-switcher.ts | verbatim | apps/desktop/src/store/session-switcher.ts | bec7f3b27b22077dfe7d681d2086a4c91f14c543504bf12c59dac2e5151a8230 | bec7f3b27b22077dfe7d681d2086a4c91f14c543504bf12c59dac2e5151a8230 |
| src/hermes/store/session-watchdog.test.ts | verbatim | apps/desktop/src/store/session-watchdog.test.ts | 4626e142ee258dbc9e234ecf505d03fb5827ba2f98ab394522de7adeeafb7159 | 4626e142ee258dbc9e234ecf505d03fb5827ba2f98ab394522de7adeeafb7159 |
| src/hermes/store/setup-beat.test.ts | verbatim | apps/desktop/src/store/setup-beat.test.ts | 410d28419330d660195b17c492251c5517c1940c862b294d0b2eb86263000f07 | 410d28419330d660195b17c492251c5517c1940c862b294d0b2eb86263000f07 |
| src/hermes/store/setup-beat.ts | verbatim | apps/desktop/src/store/setup-beat.ts | 5bb6d2927c682aa9a3c9a22abbab420b4b822e4aabdfb65d358685c8a259c526 | 5bb6d2927c682aa9a3c9a22abbab420b4b822e4aabdfb65d358685c8a259c526 |
| src/hermes/store/shared-metrics.ts | patch | apps/desktop/src/store/shared-metrics.ts | 3c129843d077c48e9b49dfd8e95b23e54e974794fd69ffb1e2abe39d1fe58dec | 802886ed09febfbd61f42f0d3916bc09bb7afc521fed4c4ea826fcba4da13bb9 |
| src/hermes/store/sidebar-archive.test.ts | verbatim | apps/desktop/src/store/sidebar-archive.test.ts | 4d11f2d28083c773c9e7f6679b48b0991fc34ec35ec6601ce6ace9803a447d7d | 4d11f2d28083c773c9e7f6679b48b0991fc34ec35ec6601ce6ace9803a447d7d |
| src/hermes/store/sidebar-archive.ts | verbatim | apps/desktop/src/store/sidebar-archive.ts | 67834bcc6cbf35e3e8c3ddf20af2cef71ace105b2ace2810a6e400211ff32f84 | 67834bcc6cbf35e3e8c3ddf20af2cef71ace105b2ace2810a6e400211ff32f84 |
| src/hermes/store/sidebar-nav.test.ts | verbatim | apps/desktop/src/store/sidebar-nav.test.ts | c11016ff91c48cc1e1ad764440129439b57e1fc4ef10ef311952a52062c700ee | c11016ff91c48cc1e1ad764440129439b57e1fc4ef10ef311952a52062c700ee |
| src/hermes/store/sidebar-nav.ts | verbatim | apps/desktop/src/store/sidebar-nav.ts | 9698e20aed0175ed5d312958cf65271cca52db347eef53ea7d6334b663388a96 | 9698e20aed0175ed5d312958cf65271cca52db347eef53ea7d6334b663388a96 |
| src/hermes/store/sidebar-sort.test.ts | verbatim | apps/desktop/src/store/sidebar-sort.test.ts | e43892b897c4992f0b93a9c1f49fc9fd40d9b86deef116e993ef2ed83773506e | e43892b897c4992f0b93a9c1f49fc9fd40d9b86deef116e993ef2ed83773506e |
| src/hermes/store/sidebar-sort.ts | verbatim | apps/desktop/src/store/sidebar-sort.ts | 43dc5ec24ba6eee6a4e3dfb53da1765e36ba2b9464dc81a846ec9546e3bb1f88 | 43dc5ec24ba6eee6a4e3dfb53da1765e36ba2b9464dc81a846ec9546e3bb1f88 |
| src/hermes/store/starmap.test.ts | verbatim | apps/desktop/src/store/starmap.test.ts | e5c6827e7c0848b926444b8f169dd9a86839ea2911f6d4fd4468adefd212d07a | e5c6827e7c0848b926444b8f169dd9a86839ea2911f6d4fd4468adefd212d07a |
| src/hermes/store/starmap.ts | verbatim | apps/desktop/src/store/starmap.ts | 692f42dbd5eb10f547d9e9991ced8d1ef75a40d194b6539c100115ec2a597e94 | 692f42dbd5eb10f547d9e9991ced8d1ef75a40d194b6539c100115ec2a597e94 |
| src/hermes/store/system-actions.test.ts | verbatim | apps/desktop/src/store/system-actions.test.ts | ee5c7c8a8e432811a3f547efed0ca3a16fd8a59bc48cfd89e544be4ac77e714e | ee5c7c8a8e432811a3f547efed0ca3a16fd8a59bc48cfd89e544be4ac77e714e |
| src/hermes/store/system-actions.ts | verbatim | apps/desktop/src/store/system-actions.ts | ea006157e6eaf86adffa9d0b0a4f394c8727aae7d040b78f7e63edbebef74277 | ea006157e6eaf86adffa9d0b0a4f394c8727aae7d040b78f7e63edbebef74277 |
| src/hermes/store/text-direction.ts | verbatim | apps/desktop/src/store/text-direction.ts | f2db978ba6a782829f7c0945add5e53c69007f9d9f98610a91e938631d29e33b | f2db978ba6a782829f7c0945add5e53c69007f9d9f98610a91e938631d29e33b |
| src/hermes/store/theme-pick.ts | verbatim | apps/desktop/src/store/theme-pick.ts | 240bb91506f4f91bf3a9ce371e48f820973e1ec6c2722ed384b8535530f55065 | 240bb91506f4f91bf3a9ce371e48f820973e1ec6c2722ed384b8535530f55065 |
| src/hermes/store/thread-scroll-restore.test.ts | verbatim | apps/desktop/src/store/thread-scroll-restore.test.ts | 59dc937f09cf3ed8fdf793934fdc826701431e9e1086c52c1ecbf4db059ee1b7 | 59dc937f09cf3ed8fdf793934fdc826701431e9e1086c52c1ecbf4db059ee1b7 |
| src/hermes/store/thread-scroll.test.ts | verbatim | apps/desktop/src/store/thread-scroll.test.ts | bad9b95d96da1865f6ecbdd879c5d4623963046c634791fee226a1dbcc911e09 | bad9b95d96da1865f6ecbdd879c5d4623963046c634791fee226a1dbcc911e09 |
| src/hermes/store/thread-scroll.ts | verbatim | apps/desktop/src/store/thread-scroll.ts | 52b9d67d4b8a192ea6860f273d29f7af364133e1ecdaede3bb0546ff47ed2c50 | 52b9d67d4b8a192ea6860f273d29f7af364133e1ecdaede3bb0546ff47ed2c50 |
| src/hermes/store/thread-timeline.test.ts | verbatim | apps/desktop/src/store/thread-timeline.test.ts | 2eeb9f3d67e0d014d946fade06bee28a3d9c85bc8a7d7a1281ec6351d199e6ca | 2eeb9f3d67e0d014d946fade06bee28a3d9c85bc8a7d7a1281ec6351d199e6ca |
| src/hermes/store/thread-timeline.ts | verbatim | apps/desktop/src/store/thread-timeline.ts | 4e656023bbc3a31a860a601b4690a3f459af85cacb12552f933e8c025364c430 | 4e656023bbc3a31a860a601b4690a3f459af85cacb12552f933e8c025364c430 |
| src/hermes/store/tips.test.ts | verbatim | apps/desktop/src/store/tips.test.ts | 495b47393dcc155ab2a5af48993aa83f9640d5ec9343246fb37c700a495b1b69 | 495b47393dcc155ab2a5af48993aa83f9640d5ec9343246fb37c700a495b1b69 |
| src/hermes/store/tips.ts | verbatim | apps/desktop/src/store/tips.ts | bdfb969877ac84f0b855595d757532090db043973325e86b5ec59433f18dc365 | bdfb969877ac84f0b855595d757532090db043973325e86b5ec59433f18dc365 |
| src/hermes/store/tool-activity.test.ts | verbatim | apps/desktop/src/store/tool-activity.test.ts | c9088df3a1bb5053555890c6c95675ea54eca3c1b851332e334128323848ed63 | c9088df3a1bb5053555890c6c95675ea54eca3c1b851332e334128323848ed63 |
| src/hermes/store/tool-activity.ts | verbatim | apps/desktop/src/store/tool-activity.ts | fd967d51a46f2618f85964932f823e2fdc5d8b8a7cc11a41725ec717811bc3fd | fd967d51a46f2618f85964932f823e2fdc5d8b8a7cc11a41725ec717811bc3fd |
| src/hermes/store/tool-dismiss.ts | verbatim | apps/desktop/src/store/tool-dismiss.ts | 9e705e93a792c565876885bc75dbf213faec31a3d006535fc067a420ffca17eb | 9e705e93a792c565876885bc75dbf213faec31a3d006535fc067a420ffca17eb |
| src/hermes/store/tool-drafting.ts | verbatim | apps/desktop/src/store/tool-drafting.ts | 8efba938e2c13848565d01e20d0bd5fba601fc7055becddc3c2f82f59fb22971 | 8efba938e2c13848565d01e20d0bd5fba601fc7055becddc3c2f82f59fb22971 |
| src/hermes/store/tool-progress.ts | verbatim | apps/desktop/src/store/tool-progress.ts | e1875b36900ed215c53eefb3e1694a0cdd5b34a9d06f3df68c249c875ce5dcee | e1875b36900ed215c53eefb3e1694a0cdd5b34a9d06f3df68c249c875ce5dcee |
| src/hermes/store/tool-view.test.ts | verbatim | apps/desktop/src/store/tool-view.test.ts | 18ad07ce43ca1ff17f904eb76e848e508236f881b88b84201cfd5932356666c8 | 18ad07ce43ca1ff17f904eb76e848e508236f881b88b84201cfd5932356666c8 |
| src/hermes/store/tool-view.ts | verbatim | apps/desktop/src/store/tool-view.ts | 53d3f2d5c95c88270bd3b4710a46148ab053e5ac8a99999a8f7bdaaa14540272 | 53d3f2d5c95c88270bd3b4710a46148ab053e5ac8a99999a8f7bdaaa14540272 |
| src/hermes/store/tours.ts | verbatim | apps/desktop/src/store/tours.ts | aae723ba3ec19aeda120630dad8a9e2f053787c83d9ac1ede273c977db6da005 | aae723ba3ec19aeda120630dad8a9e2f053787c83d9ac1ede273c977db6da005 |
| src/hermes/store/transcript-lightbox.ts | verbatim | apps/desktop/src/store/transcript-lightbox.ts | 232e3eca6c8dce469546829c2664291d4d6690586c9bb7f8efef3e2fd0af259a | 232e3eca6c8dce469546829c2664291d4d6690586c9bb7f8efef3e2fd0af259a |
| src/hermes/store/translucency.test.ts | verbatim | apps/desktop/src/store/translucency.test.ts | 73ad87dfdfbe829cd641d7b242cdd920d2e8ac4b9d2cb5c8f3a071147be71e09 | 73ad87dfdfbe829cd641d7b242cdd920d2e8ac4b9d2cb5c8f3a071147be71e09 |
| src/hermes/store/translucency.ts | patch | apps/desktop/src/store/translucency.ts | 4ecac1d4ffb11c5e7fd6921c4d37a94769317ce996981d48acbbf50e6910f6b0 | 63b03cd71046463147923d0133640ce30803487b1b7450421eb099b74462422e |
| src/hermes/store/translucency.win10.test.ts | verbatim | apps/desktop/src/store/translucency.win10.test.ts | 7f6fa515e75ff32a42d14d090d3f52103babdd50fa6a3077b692ceb60b4d0aaf | 7f6fa515e75ff32a42d14d090d3f52103babdd50fa6a3077b692ceb60b4d0aaf |
| src/hermes/store/video-playback-speed.ts | verbatim | apps/desktop/src/store/video-playback-speed.ts | cdbedbdfd6370e842d5def5becfc65f75b942f8b6a2e4ec20c9ab92b3e496f83 | cdbedbdfd6370e842d5def5becfc65f75b942f8b6a2e4ec20c9ab92b3e496f83 |
| src/hermes/store/voice-live.test.ts | verbatim | apps/desktop/src/store/voice-live.test.ts | ce173e387edd8877c93438c0e4706b878f3179f391c426398f381396e0d0fb3f | ce173e387edd8877c93438c0e4706b878f3179f391c426398f381396e0d0fb3f |
| src/hermes/store/voice-live.ts | verbatim | apps/desktop/src/store/voice-live.ts | d1de1a84ea1782efe645c34725952ca46587dbe04bbed9ffdf84a548a6575530 | d1de1a84ea1782efe645c34725952ca46587dbe04bbed9ffdf84a548a6575530 |
| src/hermes/store/voice-playback.ts | verbatim | apps/desktop/src/store/voice-playback.ts | 693afada486e1a6b24bbb18b40a414acf43d71137cdee194e76bf337d0f1ccce | 693afada486e1a6b24bbb18b40a414acf43d71137cdee194e76bf337d0f1ccce |
| src/hermes/store/voice-prefs.test.ts | verbatim | apps/desktop/src/store/voice-prefs.test.ts | 48f152883c83ccec725ad23f010d71bae40483ab9e7332b414bf92f20e8b2066 | 48f152883c83ccec725ad23f010d71bae40483ab9e7332b414bf92f20e8b2066 |
| src/hermes/store/voice-prefs.ts | verbatim | apps/desktop/src/store/voice-prefs.ts | b525b1dcd93639bebc1ecdccb19e246144e91340a6d154f612d3607e7f5d8536 | b525b1dcd93639bebc1ecdccb19e246144e91340a6d154f612d3607e7f5d8536 |
| src/hermes/store/wake-word-client-capture.test.ts | verbatim | apps/desktop/src/store/wake-word-client-capture.test.ts | a890a6027101870e2ad15b757bf84187b2b080abdf30fd3066c5d6891e658265 | a890a6027101870e2ad15b757bf84187b2b080abdf30fd3066c5d6891e658265 |
| src/hermes/store/wake-word.test.ts | verbatim | apps/desktop/src/store/wake-word.test.ts | 3cdf6d7f4af4ccf1fe369169000a814595bcc67b85a51e4f3c6e3a3b564bf69e | 3cdf6d7f4af4ccf1fe369169000a814595bcc67b85a51e4f3c6e3a3b564bf69e |
| src/hermes/store/wake-word.ts | verbatim | apps/desktop/src/store/wake-word.ts | 2399fa2d54b839cdd514c360e08e5ef7bc02265d2b0000eae84b4349725ee9ae | 2399fa2d54b839cdd514c360e08e5ef7bc02265d2b0000eae84b4349725ee9ae |
| src/hermes/store/workspace-events.ts | verbatim | apps/desktop/src/store/workspace-events.ts | fe51b656e3292613144cc642f60bcbc777d73b93cfa8f4b17a770ce486986e3a | fe51b656e3292613144cc642f60bcbc777d73b93cfa8f4b17a770ce486986e3a |
| src/hermes/styles.css | patch | apps/desktop/src/styles.css | 4dbc9cb59f06fd6be4e5ae6ae4e8b861e8ceb26e2165cbc8bbdab7e2fe7dccaf | cc42db628f7614f338f81599c47676c4f3e711707bc0b75d476102549278a9e5 |
| src/hermes/test/fixtures/identifier_speech_corpus.json | verbatim | tests/fixtures/identifier_speech_corpus.json | a147e113e54833a2260d477dee3020ca23b9c1bd00c4211fb13fd3934b68135b | a147e113e54833a2260d477dee3020ca23b9c1bd00c4211fb13fd3934b68135b |
| src/hermes/test/fixtures/session-resume-active-turn.json | verbatim | tests/fixtures/session-resume-active-turn.json | 05ebdc61124bd53f48abfa7dd1be0653ebec20eebf182e347c6f08cd80489300 | 05ebdc61124bd53f48abfa7dd1be0653ebec20eebf182e347c6f08cd80489300 |
| src/hermes/test/jsdom.ts | verbatim | apps/desktop/src/test/jsdom.ts | 358a0bb06559e4dcfb64456c8cbdacc5cccb07da208858dcf290e462f70c825b | 358a0bb06559e4dcfb64456c8cbdacc5cccb07da208858dcf290e462f70c825b |
| src/hermes/test/original-fixtures.d.ts | new | — | — | 6c49b894d48e20762e60d8fec78e603abff893d5bee0633d23dc90b13d9e1fa5 |
| src/hermes/test/window-state.ts | verbatim | apps/desktop/src/test/window-state.ts | 0926ea88b39daa13d0b05341bc8d457365d85a5b0c9dc08907a4943835488679 | 0926ea88b39daa13d0b05341bc8d457365d85a5b0c9dc08907a4943835488679 |
| src/hermes/themes/accent-override.ts | verbatim | apps/desktop/src/themes/accent-override.ts | 08e473b6aafa2932468a49152c35a91d9640b08c32aa579134bfa68a43208343 | 08e473b6aafa2932468a49152c35a91d9640b08c32aa579134bfa68a43208343 |
| src/hermes/themes/accents.ts | verbatim | apps/desktop/src/themes/accents.ts | 98ef554400b44e2c4a2271ef00ef993cdd427f7c1d7c9da3fb1b099503888c4e | 98ef554400b44e2c4a2271ef00ef993cdd427f7c1d7c9da3fb1b099503888c4e |
| src/hermes/themes/chat-font.ts | verbatim | apps/desktop/src/themes/chat-font.ts | 488b3da0ef70b551079e7163da524b63d9023e4e103e4f00526e67792a4e8125 | 488b3da0ef70b551079e7163da524b63d9023e4e103e4f00526e67792a4e8125 |
| src/hermes/themes/color.ts | verbatim | apps/desktop/src/themes/color.ts | e27bd48928bdb34ebde3cb93e528b4c677d761eee0b8819efc82714e65ebc4b5 | e27bd48928bdb34ebde3cb93e528b4c677d761eee0b8819efc82714e65ebc4b5 |
| src/hermes/themes/context.test.tsx | verbatim | apps/desktop/src/themes/context.test.tsx | a846821efbc5308e6ae8741d2ab8b943b441d4b1134fe6215c10c0d8e9bbc6f2 | a846821efbc5308e6ae8741d2ab8b943b441d4b1134fe6215c10c0d8e9bbc6f2 |
| src/hermes/themes/context.tsx | patch | apps/desktop/src/themes/context.tsx | c4c9feea2b7791ec971890df3d7bf22436f9fa29ef92df3142cae553f66afa8a | 4ce64f1cafe7f24b784cb7a773e9b0fc16a02f8bc3047620a2f20de2aaa92848 |
| src/hermes/themes/index.ts | verbatim | apps/desktop/src/themes/index.ts | 220ac44e4e33a42bd895e93a953a2aa179e8dbdf2d1bda794d20050075884461 | 220ac44e4e33a42bd895e93a953a2aa179e8dbdf2d1bda794d20050075884461 |
| src/hermes/themes/persist-accent.test.tsx | verbatim | apps/desktop/src/themes/persist-accent.test.tsx | e9eee580082915316159ddde443c06fe5a6e4f3ebdb09b0c670d5927e1185456 | e9eee580082915316159ddde443c06fe5a6e4f3ebdb09b0c670d5927e1185456 |
| src/hermes/themes/persist-accent.ts | verbatim | apps/desktop/src/themes/persist-accent.ts | 5fbb2ee55e131126877846cc3b938b7f98a112502e0600ce2012b67027a47527 | 5fbb2ee55e131126877846cc3b938b7f98a112502e0600ce2012b67027a47527 |
| src/hermes/themes/presets.test.ts | verbatim | apps/desktop/src/themes/presets.test.ts | 2fee5a38390a0114a2d926b3c1aa191a7bf3604277e9e6951b15802aa81a1e8a | 2fee5a38390a0114a2d926b3c1aa191a7bf3604277e9e6951b15802aa81a1e8a |
| src/hermes/themes/presets.ts | verbatim | apps/desktop/src/themes/presets.ts | be92a1408f3aa0e5c6e8fe5958169f03a910524a4cdfd77f83579794467a908d | be92a1408f3aa0e5c6e8fe5958169f03a910524a4cdfd77f83579794467a908d |
| src/hermes/themes/request.test.tsx | verbatim | apps/desktop/src/themes/request.test.tsx | 773b7315afcad1b23ca0384317700536e25034e50e828b8f158ac09d97b28c97 | 773b7315afcad1b23ca0384317700536e25034e50e828b8f158ac09d97b28c97 |
| src/hermes/themes/request.ts | verbatim | apps/desktop/src/themes/request.ts | a05dbc088c637a9254204c671160fec98b2449c25703f701e259b0d69d292b54 | a05dbc088c637a9254204c671160fec98b2449c25703f701e259b0d69d292b54 |
| src/hermes/themes/retint.test.ts | verbatim | apps/desktop/src/themes/retint.test.ts | edc5b102562895861a41db4508debd658edc70ab542b5f5f4c8dd09329e12d41 | edc5b102562895861a41db4508debd658edc70ab542b5f5f4c8dd09329e12d41 |
| src/hermes/themes/retint.ts | verbatim | apps/desktop/src/themes/retint.ts | 94d881848d4db14001da02573b77c3320d04eb95b3a52161ebbc1e538f28fcda | 94d881848d4db14001da02573b77c3320d04eb95b3a52161ebbc1e538f28fcda |
| src/hermes/themes/skin.test.ts | verbatim | apps/desktop/src/themes/skin.test.ts | fdd4f29fdfa65c0844aeba68c58e9f1b80219242e90368046be5f25d9f5dd677 | fdd4f29fdfa65c0844aeba68c58e9f1b80219242e90368046be5f25d9f5dd677 |
| src/hermes/themes/skin.ts | verbatim | apps/desktop/src/themes/skin.ts | 06a7d427bc8ede26a3fe5b216406612e45313d85face296af5632a4d71a0015d | 06a7d427bc8ede26a3fe5b216406612e45313d85face296af5632a4d71a0015d |
| src/hermes/themes/types.ts | verbatim | apps/desktop/src/themes/types.ts | 9db990ce6db85bfd7fd4c4e0c070c85a68c1d517282f1e93046d082ad8cfa53b | 9db990ce6db85bfd7fd4c4e0c070c85a68c1d517282f1e93046d082ad8cfa53b |
| src/hermes/themes/user-themes.test.ts | verbatim | apps/desktop/src/themes/user-themes.test.ts | 451e7eb059c6c483bf1f8d3ba14966acb5d43ee6936293c5dad829dc4155ee6f | 451e7eb059c6c483bf1f8d3ba14966acb5d43ee6936293c5dad829dc4155ee6f |
| src/hermes/themes/user-themes.ts | verbatim | apps/desktop/src/themes/user-themes.ts | ca736082a63ec1400a444d3b3bfcba1d805a7f4ba02b31ea90757784bedce7d8 | ca736082a63ec1400a444d3b3bfcba1d805a7f4ba02b31ea90757784bedce7d8 |
| src/hermes/themes/vscode.test.ts | verbatim | apps/desktop/src/themes/vscode.test.ts | f54039199589836633c95e537c42917046716d2d7999baad06b9dfc59cb72785 | f54039199589836633c95e537c42917046716d2d7999baad06b9dfc59cb72785 |
| src/hermes/themes/vscode.ts | verbatim | apps/desktop/src/themes/vscode.ts | f6a0786aec698ec16e5ecf43a3a9bb23ceba3fea42b54a8c2c8bb286125365af | f6a0786aec698ec16e5ecf43a3a9bb23ceba3fea42b54a8c2c8bb286125365af |
| src/hermes/vitest.setup.ts | patch | apps/desktop/vitest.setup.ts | c254baaed023b509535996290a12512bba9b029f9eac7eba60d25fca0199d90a | d96068a658f2baf4c574d430ca983f4a1949a54d63d6f659a17c8d022e8f8241 |
| src/platform/pr9-entrypoints.test.tsx | new | — | — | 9ac757f9dee6ab2930544951f8ea78f01fcd646b4e0696f36baeee176c7f058b |
| src/platform/pr9-render-boundaries.test.tsx | new | — | — | 564dd8c4a1d1167eb5ab856efc4ddab66eadf5db53cddca92e948c439e769230 |
| src/platform/pr9-ui-boundaries.test.tsx | new | — | — | 50bb85e18db29e7751cd9f1054c2446eb471d2cc6e793334ba5106b17394ef48 |
| src/platform/pr9-ui-offline-setup.ts | new | — | — | 7557e7252a0541634e9cdf1664186a69bf80aeba095590ec4e34a05aedcce6d7 |
| src/platform/pr9-upstream-ui-bridge-setup.ts | new | — | — | 86b21a302b5d1c1f1bcc6c7320b59b23e5d003845c8e5d30ca108021866916f2 |
| src/protocol/format.test.ts | verbatim | apps/shared/src/format.test.ts | cce0210601359e16fceeaef0c9d9d20eb5cb51affdbb1e6dcc23a7e750e4616d | cce0210601359e16fceeaef0c9d9d20eb5cb51affdbb1e6dcc23a7e750e4616d |
| src/protocol/format.ts | verbatim | apps/shared/src/format.ts | 70b1b4b8e3e013225b40be5c819540b1508e46678b5d83e6676bb68d71d9d521 | 70b1b4b8e3e013225b40be5c819540b1508e46678b5d83e6676bb68d71d9d521 |
| src/protocol/i18n.test.ts | verbatim | apps/shared/src/i18n.test.ts | c1363a7ce9b9e2a600422a954cd5ae34177ec71dd51e3f3b77fcfcc35edea1cf | c1363a7ce9b9e2a600422a954cd5ae34177ec71dd51e3f3b77fcfcc35edea1cf |
| src/protocol/i18n.ts | verbatim | apps/shared/src/i18n.ts | 346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca | 346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca |
| src/protocol/pr9-ansi.ts | verbatim | apps/shared/src/ansi.ts | 2b79f0d7de3a64b648733a45e0b26767bed45d341028e2ae9618ee3cc0bb6daa | 2b79f0d7de3a64b648733a45e0b26767bed45d341028e2ae9618ee3cc0bb6daa |
| src/protocol/pr9-extra/billing-types.ts | patch | apps/shared/src/billing-types.ts | 69d529855196cf2736895bfee788e53f1bd1debbcdb30c21912b1ac1c8c5433b | 1d5f3fca675945d2314daa2382001d678ddfac99fbd10196e00e2099f519ae0f |
| src/protocol/pr9-extra/color.ts | verbatim | apps/shared/src/color.ts | 608965b40f22d98eede54d49319a5423acc12565f7d769a681ec725f33073cba | 608965b40f22d98eede54d49319a5423acc12565f7d769a681ec725f33073cba |
| src/protocol/pr9-extra/fuzzy.ts | verbatim | apps/shared/src/fuzzy.ts | 005f379adcc672e24133062b99d0e581c38dd00f4f5bacea37b0722af671c4cc | 005f379adcc672e24133062b99d0e581c38dd00f4f5bacea37b0722af671c4cc |
| src/protocol/pr9-extra/model-search-text.ts | verbatim | apps/shared/src/model-search-text.ts | d9def71ec6168ce884ea653c6ce6b7ae626449f5d1e88ccb14e66a700a69d22a | d9def71ec6168ce884ea653c6ce6b7ae626449f5d1e88ccb14e66a700a69d22a |
| src/protocol/pr9-extra/plan-usage.ts | patch | apps/shared/src/plan-usage.ts | 0be46f42a8e6ff2c9de84d30384f0af79dca8b23cd52ea60110063ec02a54acf | b29fb1c5019e04c3720c532f7acea6a11a038823413779dcae6fba9a51f248ef |
| src/protocol/pr9-extra/skin.ts | verbatim | apps/shared/src/skin.ts | 59b74561b98c14510dd2eb346e1357273d0cdd2e901eeb687b997571d04e4760 | 59b74561b98c14510dd2eb346e1357273d0cdd2e901eeb687b997571d04e4760 |
| src/protocol/pr9-extra/slash.ts | verbatim | apps/shared/src/slash.ts | 44118cc7d445af3331999ed354fb85fa160705baab8804787e974bb7be84e869 | 44118cc7d445af3331999ed354fb85fa160705baab8804787e974bb7be84e869 |
| src/protocol/pr9-extra/theme-presets.ts | verbatim | apps/shared/src/theme-presets.ts | ada88929c0500ce1f0a5b9d097c85c0ea6959c65107146bee8017445bd0e937b | ada88929c0500ce1f0a5b9d097c85c0ea6959c65107146bee8017445bd0e937b |
| src/protocol/pr9-extra/translucency.ts | verbatim | apps/shared/src/translucency.ts | 7912511a1becefaa8ec442a75d557d8cd8b7e45f07579d7cb1e698563fc80a74 | 7912511a1becefaa8ec442a75d557d8cd8b7e45f07579d7cb1e698563fc80a74 |
| src/protocol/pr9-full.ts | new | — | — | e9c386fdcd29b7a742bef2c0a3e23f0456c706bed3c7894219338a777ea0d8fc |
| src/protocol/pr9-ui.ts | new | — | — | 7e485a888ae57805e6fa82c74c24432eb504ea50322626e2420ebd0a2b04c626 |
| src/protocol/reasoning-effort.test.ts | verbatim | apps/shared/src/reasoning-effort.test.ts | 35ffdbc2feee62f11d7b82b2cf130c517d21ff6af5314e7eb2803d53846d38ee | 35ffdbc2feee62f11d7b82b2cf130c517d21ff6af5314e7eb2803d53846d38ee |
| src/protocol/reasoning-effort.ts | verbatim | apps/shared/src/reasoning-effort.ts | 9ac6319a8947d25e7241c3eab625aa85f1ebd9df16ddf4950a8c98ce3ff070ae | 9ac6319a8947d25e7241c3eab625aa85f1ebd9df16ddf4950a8c98ce3ff070ae |
| PR9_PORTING.md | new | — | — | 자기참조 제외 |
| pr9-ui-tests.json | new | — | — | 91d86bd078513b34053c7871c1959c584468a396528ba0b13f610a419eafd19a |
| pr9-excluded-tests.json | new | — | — | 79bc400c825ca9a6270375d9c1c6d9e1467ea573ca7825afc4704dc35180c737 |
| pr9-excluded-cases.json | new | — | — | e6a90c07a6055821b2d2dbc1ccc2909c7b9b835cdc8273254995e592592f8fe0 |
| pr9-test-review.json | new | — | — | 4b09f526ade21eeb5efca320a1be48c8b116b343a4860c615ea7b923fe120a5f |
| pr9-audit.json | new | — | — | 자기참조 제외 |

## 기존 설정 SHA
| 파일 | 이전 SHA256 | 현재 SHA256 |
|---|---|---|
| tsconfig.json | c8c9d4abbc22b3584a3b33b4f63e2180b4ad1c924a636ed8b365ed92cbc9782a | 2a200f40f72cec3e2b17e0827497e98b04b2fdddf183a032233b682f4facbd4d |
| vitest.config.ts | 28680eb583a5c01e544d6de8727fbf246a7721b8f38aa6044a98ce99f2a2efe2 | 8eb9bf2d395b065152fef2913c20517d0cf21b5142bb8b946afb427effdd211e |
| vitest.integration.config.ts | 13fafb194a8d8334a38173db87540f437844cb59f6c8383310c1e9836cf18553 | 6411460fdc60c1458cf7032f1d97de444caca70fdce9959a0354ebf54cfb4a27 |
| vitest.real-model.config.ts | 6843c1b6f36bc8d740fa7087cb171f2fe555434892c2ad73bd9ab74e01ddd6c4 | 6e4e1e1ef8b574cec757bcd891ee80194ccd06000d6f3c34baeeceb12315c8fb |


# PR-9b 웹 조합 (2026-10-25)

## 웹 레이아웃 후속 수정 (2026-10-25)

- 세로 배치 원인은 `src/styles.css`의 전역 `main { flex-direction:column }`이었다. `src/hermes/styles.css`의 composer-dock 규칙은 폭/패딩을 지정하며, absolute 위치는 원본 ChatBar의 Tailwind 클래스에서 온다. 두 원본 파일은 수정하지 않았다.
- `src/web/web.css`: 모든 선택자를 `.hermes-web` 범위로 한정. row/240px 사이드바/14px 기본 글꼴, 높이 제한과 독립 스크롤, 상단 우측 사용량, 선택 세션 강조, 검증 통과(초록)/실패(빨강) 적용. 검증 문구와 data-verification 유지.
- `src/web/HermesWebApp.tsx`: 선택 세션 aria-current 및 컴포저 래퍼 추가. 원본 Thread/ChatBar 구조와 클래스는 유지한다. 일반 dock을 래퍼 안의 relative 흐름으로 전환하여 실제 높이를 확보하므로 고정 88px padding-bottom은 필요하지 않다. pop-out 위치는 유지한다.
- 회귀: 세션 클릭/새 채팅 시 aria-current 설정·해제 및 jsdom CSS cascade 검사 추가. CSS 검사는 기본 main의 column 유지, 웹 row/스크롤 경계/dock의 흐름 참여를 확인하며 실제 픽셀 배치·포인터 hit-test를 증명하지 않는다.
- 검증: `npx tsc --noEmit -p .`, 관련 Vitest(`src/web src/yield`), `npm test`, `npm run build` 실행. 빌드는 기존 동적/정적 import 및 500kB 초과 청크 경고가 있다. 설치·서버·Mongo·브라우저는 실행하지 않았으며 Chrome 시각/클릭 재검증은 미수행이다.

## 실제 브라우저 확인에서 발견·수정한 결함
- 사용자 Chrome 확인에서 세션 클릭 시 라우트가 `/`에 남아 선택이 초기화되고 `session.resume`이 누락됨. 원본 `session-row → wiring.openStoredSession → openSession → navigate(sessionRoute)`에 맞춰 웹 버튼을 라우트 이동으로 수정했다.
- `useRouteResume` 인자는 원본 wiring.tsx:784–801과 대조해 cache의 3개 ref까지 누락 없음을 확인. 새 채팅은 원본 `startFreshSessionDraft`의 `NEW_CHAT_ROUTE` 이동·상태 초기화를 유지한다.
- 실제 HashRouter + 가짜 gateway의 jsdom 회귀로 클릭 → hash 변경 → resume 요청 → 11초 후 선택 유지 → 새 채팅 초기화를 검증한다. 수정 전 hash 실패를 재현했으며 이번 작업에서 서버·Mongo·브라우저는 실행하지 않았다.

## 설계와 실행 체크리스트

원본 H는 `/tmp/hermes-agent-ro`, pin `5894e750`이며 수정하지 않았다. 기존 9a 파일은 수정하지 않고 웹 조합을 추가한다. 기본 App의 import/render 문장은 그대로 두고 opt-in 분기만 추가한다. 새 ChatView 전체를 마운트하면 titlebar·intro 등이 따라오므로 공개 `ChatRuntimeBoundary`와 Thread/ChatBar를 조합한다. 원본 Thread를 기존 RunView로 대체하지 않았다.

- [x] 기준 문서 §7.1 PR-9 / 666–695 / D03·D05·D06·D10·Q6·Q7과 8b authority 경계 확인.
- [x] 순수 opt-in, 표 비교 및 Yield 렌더러 테스트 → 신규 구현.
- [x] 단일 웹 host/provider/session/stream/Thread/ChatBar 조합. Q1 resolver 미주입 fail-closed.
- [x] main opt-in, Vite alias·proxy, 동적 CSS, 개발 연결 모듈 분리.
- [x] 개발 서버·스크린샷 도구·runbook 작성. 서버/브라우저 실행 없음.
- [x] 자동 요청 감사: 연결 전/연결 후 11초. 원본 목록 훅의 cron 자동 호출을 웹 전용 조합에서 제거.
- [x] 최종 tsc / 전체 Vitest / npm test / build 결과 확정(아래 실행 범위 구분).
- [ ] 사용자 브라우저·Mongo 실행 확인. 이 항목은 Codex 실행 금지 범위.

## 원본 → 신규 파일 매핑

| 신규/변경 파일 | 근거와 이식 방식 |
|---|---|
| `src/web/HermesWebApp.tsx` | H `apps/desktop/src/app/index.tsx:1–6` contrib 분리 원칙, `src/main.tsx:95–121` QueryClient/i18n/HashRouter provider; profile i18n 자동 REST 대신 명시 locale·configClient=null |
| 같은 파일의 Thread/ChatBar 조합 | H `src/app/chat/index.tsx:202–449,778–867` 공개 runtime boundary·Thread·composer. SessionView는 H `src/app/chat/session-view.tsx:73–174`; 원본 소스 그대로 사용. titlebar/settings/pane/preview/terminal/onboarding/tour/pet/voice controller 마운트 없음 |
| `src/web/use-web-session.ts` | H `src/app/contrib/wiring.tsx:380–558,680–714,784–805` state cache, session actions, prompt actions, stream, route resume 조합. host의 gateway를 단일 primary registry에 연결, effect 해제 시 unsubscribe |
| `src/web/use-web-session-list.ts` | H `src/app/session/hooks/use-session-list-actions.ts:328–520`, `src/api/sessions.ts:104–123`. 원본 refreshSessions가 마지막에 cron 조회를 항상 실행하므로 그대로 마운트할 수 없음. 원본 listSessions API와 store setter만 이용하는 웹 조합 훅. 원본 patch 없음 |
| `src/web/connection.ts`, `entry.tsx`, `dev-connection.ts` | PB D02·D10·Q1, PR8 WebHostAdapter. `/api/ws`, authority 명시. 인증 해석은 주입, 자동 legacy fallback 없음. 8b의 명시 bridge API(installApiBridge/installDesktopBridge) 사용; 완전한 Electron API처럼 window.hermesDesktop를 위장하지 않음 |
| `src/web/opt-in.ts` / 테스트 | PB §7.1 PR-9. query만 검사, hash는 opt-in으로 간주하지 않음 |
| `src/web/web.css`, `font-policy.ts` | PB D03/Q6. --hermes-system-font 하나로 지정. 원본 CSS는 무수정; Vite transform에서 KaTeX @font-face만 제외하여 번들 폰트 출력을 막음. 원본 수식의 글꼴 배치와 차이가 날 수 있음 |
| `src/yield/YieldVerify.tsx`, `YieldResults.tsx` | Y `harness/router.py:45–46,100–116`, `native_verification.py:7–17`, `gateway/events.py:85–140`; observations.verify_answer/completion_review, scope.verdict, summary, goal_revision. 후보 답변을 생성하거나 표시하지 않음 |
| `src/yield/use-yield-results.ts`, `YieldTable.tsx` | Y `harness/router.py:157–164`, 기존 console `Artifacts.tsx:39–65`, `types.ts`. 서버의 named-table columns/rows/units/complete/total_rows 사용. 100행 페이지 표시, 총 행 수 표시 |
| `src/yield/YieldArtifact.tsx` | Y `harness/router.py:167–174`, `store.py:198–205`, console `artifactUtils.ts`. 서버 ID API만 사용, 서버 소유권 판정. iframe은 빈 sandbox로 스크립트 차단. 미리보기는 사용자가 클릭했을 때만 fetch |
| `src/yield/table-dedupe.ts` | PB PR-9 표 중복 제거(`html-tables.ts` 포함). GFM AST의 실제 table 노드만 비교(코드블록 보존), 셀 Unicode/공백 정규화, 헤더 순서 정렬. 완전한 전체 행이 있을 때만 markdown 표 생략. 원본 MarkdownText/Thread 수정 없이 SessionView 파생 atom으로 적용 |
| `src/hermes/components/confirm-host.tsx`, `.test.tsx` | H 동명 파일 verbatim 신규 반입. 실제 웹 루트의 메시지 편집 확인 Dialog; 원본 6개 테스트 복구 |
| `src/hermes/components/ui/dialog-dismiss-repro.test.tsx` | H 동명 테스트 verbatim 신규 반입. 진단 실행 후 제외 유지(아래) |
| `tests-integration/pr9_dev_server.py` | 기존 `pr8_gateway_server.py:28–76,112–133,193–230`, 실제 MongoSessionStore/HarnessStore API로 일반/결과/대기 세션 3개씩 시드, OfflineController 재사용. 임시 zz_pr9 UUID DB, loopback/DEV identity, 종료 drop |
| `e2e-web/screenshot.mjs`, `PR9B_RUNBOOK.md` | 사용자 실행용. 세션 목록·표/artifact/검증·clarify 화면. 기존 e2e/ 무수정 |

## main.tsx 변경 전/후 diff

**PB가 허용한 PR-9 opt-in 변경.** 기존 App import, styles import, StrictMode render 문장은 한 글자도 바꾸지 않고 else에 유지했다. query를 제거하고 새로고침하면 기본 UI로 돌아간다.

```diff
--- src/main.tsx (9a)
+++ src/main.tsx (9b)
@@ -2,4 +2,9 @@
 import { createRoot } from 'react-dom/client';
 import App from './App';
 import './styles.css';
+import { isHermesOptIn } from './web/opt-in';
+if (isHermesOptIn(window.location.search)) {
+  void import('./web/entry').then(entry => entry.mount());
+} else {
 createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
+}
```

## vite.config.ts 변경 전/후 diff

기존 manualChunks와 legacy proxy 항목은 보존한다. 좁은 `/harness/sessions|results|artifacts` 경로와 `/gateway-m1`, `/api/ws`를 기존 proxy보다 앞에 추가하여 HARNESS_GATEWAY_TARGET을 사용한다. 폰트 제외용 플러그인도 additive이며 기존 원본 CSS 파일은 변경하지 않는다.

```diff
--- vite.config.ts (9a)
+++ vite.config.ts (9b)
@@ -1,14 +1,36 @@
+import { hermesSystemFonts } from './src/web/font-policy';
 import { defineConfig } from 'vite';
+import { fileURLToPath } from 'node:url';
 import react from '@vitejs/plugin-react';
 import tailwindcss from '@tailwindcss/vite';
+const resolve = { alias: {
+  '@radix-ui/react-focus-scope': fileURLToPath(new URL('./node_modules/radix-ui/node_modules/@radix-ui/react-focus-scope/dist/index.mjs', import.meta.url)),
+  '@hermes/shared/billing': fileURLToPath(new URL('./src/protocol/pr9-extra/billing-types.ts', import.meta.url)),
+  '@hermes/shared/color': fileURLToPath(new URL('./src/protocol/pr9-extra/color.ts', import.meta.url)),
+  '@hermes/shared/skin': fileURLToPath(new URL('./src/protocol/pr9-extra/skin.ts', import.meta.url)),
+  '@hermes/shared/translucency': fileURLToPath(new URL('./src/protocol/pr9-extra/translucency.ts', import.meta.url)),
+  '@hermes/shared/ansi': fileURLToPath(new URL('./src/protocol/pr9-ansi.ts', import.meta.url)),
+  '@hermes/shared/i18n': fileURLToPath(new URL('./src/protocol/i18n.ts', import.meta.url)),
+  '@hermes/shared': fileURLToPath(new URL('./src/protocol/pr9-full.ts', import.meta.url)),
+  '@': fileURLToPath(new URL('./src/hermes', import.meta.url)),
+} };
+const gatewayTarget = process.env.HARNESS_GATEWAY_TARGET || 'http://127.0.0.1:8001';
 export default defineConfig({
-  plugins: [react(), tailwindcss()],
+  resolve,
+  plugins: [hermesSystemFonts(), react(), tailwindcss()],
   build: { rollupOptions: { output: { manualChunks: { markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight'] } } } },
   server: {
     host: '127.0.0.1', port: 5175, strictPort: true,
-    proxy: Object.fromEntries(['/runs', '/harness', '/sessions', '/session', '/health'].map(path => [path, {
+    proxy: {
+      '/harness/sessions': { target: gatewayTarget, changeOrigin: true },
+      '/harness/results': { target: gatewayTarget, changeOrigin: true },
+      '/harness/artifacts': { target: gatewayTarget, changeOrigin: true },
+      '/api/ws': { target: gatewayTarget, ws: true, changeOrigin: true },
+      '/gateway-m1': { target: gatewayTarget, ws: true, changeOrigin: true },
+      ...Object.fromEntries(['/runs', '/harness', '/sessions', '/session', '/health'].map(path => [path, {
       target: 'http://127.0.0.1:8001', changeOrigin: true, timeout: 0, proxyTimeout: 0,
       configure(proxy) { proxy.on('proxyRes', response => { if (response.headers['content-type']?.includes('text/event-stream')) { response.headers['cache-control'] = 'no-cache, no-transform'; response.headers['x-accel-buffering'] = 'no'; } }); },
     } satisfies import('vite').ProxyOptions])),
+    },
   },
 });
```

## 테스트 재검토·자동 호출 감사

- `pr9-test-review.json`의 9b 사유 항목을 다시 검토했다. ConfirmHost는 실제 조합에 필요해 소스/테스트를 무수정 복구, 6/6 통과.
- contrib desktop integration/tile delegate, pane context menu/titlebar, 모델 설정/SDK/settings/find-bar는 웹 채팅 조합에서 마운트하지 않아 제외 유지. completed-reply-refresh도 실제 사용하는 runtime/stream/cache와 production hydration helper 검증에 해당하여 원본 17개를 복구했고 통과했다. 현재 완료 후 이력 재개는 원본 useSessionActions를 사용한다.
- dialog-dismiss-repro 재실행: 1 실패/1 통과 (`/tmp/pr9b-web4.log`). Dialog 안의 열린 Select overlay 클릭이 부모 Dialog도 닫는다. 실제 9b ConfirmHost/UrlDialog는 내부 Select가 없으며 ConfirmHost 실제 suite는 통과. 원본 Dialog/Select patch 없이 재현 테스트를 정규 suite에서 제외. 중첩 Select를 추가하기 전에 해결해야 한다. 실브라우저 결과는 미검증.
- 조합 DOM 테스트: resolver 없는 경우 connect 0회; 연결 대기 화면; 가짜 host를 통한 서버 목록 표시; 연결 전·후 fake timer 11초; 모든 RPC/REST를 manifest implemented와 대조. 전역 offline setup이 fetch/WebSocket/EventSource 실호출을 차단한다.
- 첫 감사에서 `/api/cron/jobs` 자동 호출이 검출됐고 원본 목록 훅 대신 웹 목록 훅으로 제거했다. `useMessageStream`, `usePromptActions`, `useSessionActions`, `useRouteResume`, cache, Thread, composer는 원본 재사용.

## 위험·미결정·서버 요청 사항

- **Q1 운영 인증 미결정**: entry 실행 전 window.hermesWebOptions.resolver/fetcher 주입이 필요하다. resolver 없이 연결하지 않는다. WebHostAdapter의 기존 명시 bridge 방식을 사용하므로 전역 Electron 객체를 새로 만들지 않는다.
- **실제 화면/통합은 미검증**: Codex는 지시대로 dev server·Vite dev·Playwright·Mongo·LLM·Docker를 실행하지 않았다. 작성한 fixture와 스크린샷 도구는 py_compile/소스 검사까지만 검증했다. PR-10 실제 실행/rollback 승인을 대체하지 않는다.
- **서버 요청**: gateway event projection은 verification/table/artifact 전체 메타데이터를 제공하지 않는다. 현재 별도 소유권 검사 `/harness/sessions`, `/harness/results`, `/harness/artifacts`를 사용한다. 운영 인증 middleware가 이 경로에도 같은 principal을 적용해야 한다. 인증 규격을 UI가 추정하지 않는다.
- **결과 표시 범위**: 세션 이력 최근 50 run, 표 첫 100행이다. 총 행 수는 표시하고 일부 페이지만 있으면 중복 표를 숨기지 않는다. 더 오래된 이력·다음 표 페이지 탐색은 아직 미구현이다. artifact HTML 미리보기를 열면 구조적으로 명확한 헤더/행 표도 비교한다. 병합 셀/중복 헤더는 동일성을 추정하지 않는다. 아직 열지 않은 artifact는 내용을 다운로드하지 않으므로 비교하지 않는다.
- **CSS**: 새 CSS는 opt-in 동적 진입점에서만 로드한다. 원본 전역 reset/body/html/:root 규칙은 그대로 있어 같은 document에서 UI를 새로고침 없이 바꾸는 방식에는 영향을 줄 수 있다. 지원 rollback은 query 제거 후 전체 새로고침이다. legacy CSS 자체는 무수정이다.
- **번들**: 원본 다국어·수식·하이라이터·Mermaid의 큰 lazy 청크가 남는다. 기본 UI에서 로드하지 않는지 dist/index.html 및 JS import를 확인한다.
- **개발 fixture**: 승인 시드는 없고 clarify 시드는 있다. OfflineController는 새 실행을 유지하지만 답변을 만들지 않는다. SIGKILL은 DB 정리를 실행할 수 없으므로 runbook에 기록했다.
- **추가 설치 후보**: 설치 0. 이번 표 AST 파서의 remark-parse/mdast는 이미 설치된 전이 의존성을 사용한다. 독립 의존성으로 고정하려면 사용자 소유 package 설정에서 remark-parse 및 @types/mdast 직접 선언을 검토할 수 있다. 9a 후보 목록은 유지한다.

## 현재 판정

전체 정적 검증 결과와 사용자 화면 확인을 아래에 갱신한다. 화면/서버 실행이 끝난 것처럼 완료를 주장하지 않는다.


## 최종 빌드·번들 검사

| 항목 | 9a 기준 | 9b 최종 | 변화 |
|---|---:|---:|---:|
| 기본 index JS | 245,988 B | 247,546 B | +1,558 B (+0.63%) |
| 기본 index CSS | 143,294 B | 143,372 B | +78 B (+0.054%) |
| opt-in UI CSS | 없음 | 279,257 B | 동적 import 때만 로드 |
| 주 opt-in UI JS 청크 | 없음 | 약 5,185.28 kB | 원본 locale/렌더러 의존 포함, 큰 청크 경고 유지 |
| 번들 폰트(w[off]/woff2/ttf/otf) | 미사용 | 0 파일 | KaTeX 전이 폰트도 제외 |

`dist/index.html`은 기본 index JS/CSS와 기존 markdown preload만 참조한다. 기본 index JS에서 새 UI 청크는 `import("./mermaid-GHXKKRXX-ohWqJ-5l.js")`로만 로드된다(청크 이름은 Vite의 공통 의존 이름). 기본 문서가 새 CSS를 link하지 않음을 확인했다. Tailwind 자동 소스 탐색에 따른 기본 CSS 78 B 증가는 기록하며 원본 styles.css는 변경하지 않았다.

빌드 후 검사: `rg -l 'user=alice|user=bob|Development connection is disabled|Development host must be same-origin|dev-connection' dist --glob '*.js'` → 일치 파일 없음. `@nous-research/ui`도 없음. 단순 `alice|bob` 검색은 원본 언어/하이라이터 사전 문자열과 충돌하므로 개발 코드 고유 표식과 모듈 경로를 검사했다. 원본 인증 형식·fake resolver가 운영 코드에 남아 있지 않음을 확인했다. 상세: `/tmp/pr9b-bundle-audit.json`.

### 신규·변경 파일 전체

- `e2e-web/screenshot.mjs` (신규)
- `src/web/HermesWebApp.test.tsx` (신규)
- `src/web/HermesWebApp.tsx` (신규)
- `src/web/connection.test.ts` (신규)
- `src/web/connection.ts` (신규)
- `src/web/dev-connection.ts` (신규)
- `src/web/entry.tsx` (신규)
- `src/web/font-policy.ts` (신규)
- `src/web/opt-in.test.ts` (신규)
- `src/web/opt-in.ts` (신규)
- `src/web/use-web-session-list.ts` (신규)
- `src/web/use-web-session.ts` (신규)
- `src/web/web.css` (신규)
- `src/yield/YieldArtifact.tsx` (신규)
- `src/yield/YieldResults.tsx` (신규)
- `src/yield/YieldTable.tsx` (신규)
- `src/yield/YieldVerify.tsx` (신규)
- `src/yield/html-tables.test.ts` (신규)
- `src/yield/html-tables.ts` (신규)
- `src/yield/renderers.test.tsx` (신규)
- `src/yield/table-dedupe.test.ts` (신규)
- `src/yield/table-dedupe.ts` (신규)
- `src/yield/use-yield-results.ts` (신규)
- `tests-integration/pr9_dev_server.py` (신규)
- `PR9B_RUNBOOK.md` (신규)
- `pr9b-audit.json` (신규)
- `src/hermes/components/confirm-host.tsx` (신규)
- `src/hermes/components/confirm-host.test.tsx` (신규)
- `src/hermes/components/ui/dialog-dismiss-repro.test.tsx` (신규)
- `src/hermes/app/contrib/hooks/completed-reply-refresh.test.tsx` (신규)

기존 변경: `src/main.tsx`, `vite.config.ts`, `vitest.config.ts`, `pr9-test-review.json`, 본 문서. `package*.json`, README, tsconfig, protocol/platform, 기존 앱 보호 파일, 기존 9a 원본/patch 파일은 모두 무수정. 신규 원본 4파일은 H와 SHA 동일.


## 최종 검증 수치와 인계 상태

| 검사 | 결과 | 로그 |
|---|---|---|
| TypeScript | 오류 0, exit 0. 마지막 `npm run build`의 `tsc --noEmit`도 통과 | `/tmp/pr9b-tsc-last.log`, `/tmp/pr9b-build-verified.log` |
| 전체 Vitest 회귀 | 585파일 통과 / 6파일 skip, 5,787테스트 통과 / 99 skip, 298.58초, exit 0 | `/tmp/pr9b-vitest-final.log` |
| 최종 변경 범위 재검사 | 8파일 / 51테스트 통과, 13.67초. HTML artifact 비교·미리보기 추가와 원본 completed-reply-refresh 복구 후 재검사 | `/tmp/pr9b-final-focused.log` |
| 기존 npm test / core.test.ts | 10/10, exit 0 | `/tmp/pr9b-npm-final.log` |
| 최종 build | 16,876 modules, exit 0, 24.50초. 큰 lazy 청크 경고 있음 | `/tmp/pr9b-build-verified.log` |
| integration / real-model 설정 | `vitest list --config` 각각 13 / 1개 수집, exit 0. 소켓·모델 실행 안 함 | `/tmp/pr9b-integration-list.log`, `/tmp/pr9b-model-list.log` |
| Python | pr9_dev_server.py py_compile 통과. import/서버/Mongo 실행 안 함 | `/tmp/pr9b-dev-server.pyc` |
| 보호 파일·원본 | 보호 SHA 위반 0. 새 verbatim 원본 4파일 모두 SHA 일치. H HEAD `5894e750c3dba980d43b25aa29e3958149ba2c61` | `pr9b-audit.json` |

전체 회귀는 추가 작업 도중 수집한 585파일 스냅샷이다. 이후 추가된 html-tables/완료 답변 갱신 원본 테스트 및 최종 미리보기 변경은 별도 8파일·51개로 검사했다. 이를 현재 설정의 단일 전체 실행 587파일이라고 합쳐 주장하지 않는다. 기존 보호 코드에는 변경이 없으며 전체 회귀 중 실제 소켓/서버/모델은 실행하지 않았다. 일부 원본 jsdom 검사는 기존 localStorage/scrollTo/act 경고를 출력한다.

복구한 테스트는 ConfirmHost 6개와 completed-reply-refresh 17개다. dialog-dismiss-repro는 현재도 1실패/1통과여서 정규 suite에서 제외 유지하고 재현 파일/로그를 남겼다. 실제 조합에서는 중첩 Dialog/Select를 사용하지 않는다.

**상태: 웹 조합 및 오프라인 검증 결과를 인계한다. 실제 화면·개발 fixture 검증까지 완료한 상태는 아니다.**

남은 사용자 실행 파일/항목:
- [ ] `tests-integration/pr9_dev_server.py`: 실제 Mongo 연결, 세션/질문/결과 시드 확인, 정상 종료 DB drop 확인.
- [ ] `e2e-web/screenshot.mjs`: Vite dev 연결 후 3장 촬영, `/tmp/pr9b_shots/*.png` 검토.
- [ ] `PR9B_RUNBOOK.md`: 새 채팅/중지/clarify/세션 재개/새로고침/기본 App 화면을 실제 브라우저에서 확인.
- [ ] 운영 Q1 resolver/fetcher 공급 및 서버의 동일 principal 인증 연결. 서버 변경 파일 없음; 운영 공급 파일은 미결정.
- [ ] PR-10 실제 데이터/모델/rollout·두 rollback 검증. 이번 실행 범위 밖.

패키지 설치·커밋·push·checkout 없음. 다음 실행 순서는 PR9B_RUNBOOK.md를 따른다.

## PR-9c 실제 모델 화면 확인용 개발 서버

### 원본 이탈/차이: 서버 authority 거부 규칙에 맞춘 웹 계층 정규화

- `src/web/gateway-compat.ts`를 `HermesWebApp`의 호스트 조립 시 적용한다.
  authority 호스트 인스턴스의 `gateway.request`만 한 번 감싸며 원본 Hermes,
  platform, 서버 코드는 변경하지 않는다.
- 근거: `harness/gateway/session_rpc.py:228-240`의 unavailable 필드는 값과
  무관하게 `handler_not_connected`(-32005)로 거부된다. 원본
  `session-create-request.ts`의 `DROPPABLE_CREATE_FIELDS` 재시도는 다른
  code-4000 오류 문구에만 반응하므로 이 거부를 처리하지 못한다.
- `session.create`에서 `service_tier`의 normal/빈 문자열/null/undefined,
  `cwd_explicit`의 false/빈 문자열/null/undefined만 생략한다. 현재 Desktop의
  `fast:false`도 생략한다. 그렇지 않으면 원본 `canonical-protocol.ts:500-530`이
  이를 다시 `service_tier:'normal'`로 만든다. 이는 웹에서 기본 tier를 서버에
  위임하는 의도적인 차이다.
- model/provider/toolsets/reasoning/cwd, `cwd_explicit:true`, 비기본 tier와
  `fast:true`는 유지한다. `fast:true`와 명시적 normal tier가 함께 오면 원본의
  tier 우선순위를 보존하기 위해 normal도 유지한다. 미지원 명시적 선택은
  서버 fail-closed 정책에 맡긴다. `prompt.submit` 등 다른 메서드는 그대로
  통과하며 재시도, 순서, 타임아웃, AbortSignal, 반환 Promise/오류를 변경하지 않는다.
- 검증은 웹 조합 테스트와 메모리 FakeWebSocket의 실제 gateway 변환 프레임을
  사용한다. 서버·Mongo·브라우저는 실행하지 않으며 실제 서버 재검증은 별도다.
- 이번 변경 검증: `npx tsc --noEmit -p .` 통과, 관련 Vitest 4파일/35개 통과
  (`gateway-compat`, `HermesWebApp`, `connection`, `platform/web-host`),
  `npm test` 10개 통과, `npm run build` 통과. Vitest의 localStorage 경고와
  빌드의 동적/정적 import 혼용·큰 청크 경고는 남아 있다.

### 기존 개발 fixture 작업 기록

- `tests-integration/pr9_dev_server.py`: 사용자별 승인 대기 시드를 추가하고
  `lifespan_factory(uri, database, seed)` 주입을 지원한다. 기본 OfflineController,
  resolver/policy/router 조립과 main 진입점은 유지한다. 승인/질문 시드는 run 이력만
  사용하며 메시지 행과 혼합하지 않는다. 앞선 9b 기록의 승인 시드 부재를 해소한다.
- `tests-integration/pr9_real_dev_server.py` (신규): 실제 harness lifespan/controller에
  PR9 resolver와 seed를 연결한다. localhost Mongo 검증, 명시 opt-in, 임시 DB,
  도구 가드, 실행 예산, worker 추적/정리, loopback 상태 조회를 제공한다.
  실제 controller의 settings는 덮어쓰지 않는다.
- `tests-integration/README.md`: 실행·연결·종료 방법과 승인 시드의 범위를 기록한다.
- `PR9_PORTING.md`: 본 절 추가.

원본 이탈 없음: Hermes/UI 원본과 `src/`, `harness/` 및 engine,
`pr8_real_model_server.py`는 수정하지 않았다. 변경은 개발 fixture와 문서에 한정한다.
`.env`·scripts 읽기/수정, 설치·서버·Mongo·브라우저·네트워크 실행은 하지 않았다.

한계: 승인 시드는 UI/응답 계약 확인용이며 실제 도구 재개는 없다. `read_result` 외
도구는 차단하므로 외부 DB 조회나 보고서 생성 성공 시나리오는 확인하지 못한다.
실행 예산은 획득된 run 수를 제한하며 한 run 내부의 모델 호출 횟수를 뜻하지 않는다.
fake identity는 운영 인증이 아니다. 검증 범위는 두 Python 파일의 `py_compile`이며
실제 스트리밍·중지·승인 화면, 신호 종료와 DB/worker 정리는 수동 실행으로 확인해야 한다.

### 9c fix6 — 첫 턴 Stop의 execution_generation 전달 누락

- 증상/원인: 새 채팅 첫 실행에서 UI가 generation 0으로 Stop을 보내지만 run은
  generation 1이다. receipt에는 값이 있어도 canonical client는 receipt에서 이를
  채택하지 않는다. run 이벤트와 stale_generation 거절에도 값이 없어서 resume 전까지
  현재 fence를 알 수 없었다.
- 원본 확인: 읽기 전용 `/tmp/hermes-agent-ro`, HEAD `5894e750`.
  - `gateway/session_authority.py:1084-1095`: claim generation을
    `live.event_stream.execution.execution_generation`으로 저장하고 message.start를 발행한다.
  - `gateway/session_events.py:38-45`: execution stamp를 이벤트 params 최상위에
    펼친 후 `_stamp_event`로 링에 고정한다. `:77-89`는 고정 프레임을 since로 반환한다.
  - `gateway/session_controls.py:22-35,321-326`: 정수 execution_generation을
    `error.data`로 직렬화한다. `:782-787`은 mutation의 stale_generation/revision_conflict에서
    DB의 최신 fence를 다시 읽는다. 원본의 모든 interrupt 거절이 이 보강을 한다는 뜻은
    아니다. 이번 interrupt/cancel/resolve_unknown 적용은 같은 wire 계약을 사용하는
    호스트 보강이며, 원본 코드를 그대로 이식한 것은 아니다.
- 수정: `harness/gateway/execution.py`의 일곱 stale_generation 거절을 공통 helper로
  연결해 현재 session handle의 generation을 다시 읽어 data에 넣는다. RpcError는 이미
  data를 지원하므로 확장하지 않았다. 취소 CAS, run identity, epoch fencing은 유지한다.
- 수정: `harness/gateway/events.py`의 ingest에서 retain 호출 전에 top-level
  execution_generation을 추가한다. 일반 run 이벤트는 읽은 run의 epoch를 사용한다.
  approval/clarify payload에 기존 generation이 있으면 이를 보존해 두 위치를 일치시킨다.
  과거 prompt의 generation을 재획득된 run의 epoch로 덮어쓰지 않는 좁은 선택이다.
  freeze/size/seq/replay 순서/링 한도 로직 및 기존 frozen 프레임은 변경하지 않는다.
- 스키마 검토: `src/protocol/gateway-events.ts:66-70`의 GatewayEvent는 이미
  `execution_generation?: number`를 허용한다. generated 계약 파일 및 src/protocol,
  src/hermes는 수정하지 않았다.
- 테스트 계약 검토: 재개 전에 추가된 네 테스트의 기대값은 유지했다.
  기존 기대값 변경은 `test_delayed_interrupt_cannot_cancel_next_run_reusing_epoch`의
  error.data에 현재 generation을 추가한 것 하나다. 나머지 세 테스트는 신규 회귀 테스트
  (`test_first_turn_interrupt_refusal_names_current_generation`,
  `test_stale_cancel_and_unknown_resolution_name_fresh_generation`,
  `test_run_generation_survives_frozen_ring_and_both_since_paths`). 이번 재개에서는 테스트를
  추가 수정하지 않았다. 원본의 session 단조 generation과 달리 호스트는 run-local epoch를
  재사용하므로 delayed-interrupt 거절 자체는 기존 호스트 계약이며 유지해야 한다.
- 남는 위험: 과거에 이미 고정된 프레임에는 새 필드를 소급 추가하지 않는다.
  run 원본 이벤트는 일반 이벤트별 epoch를 저장하지 않으므로, 재획득 후 처음 ingest되는
  과거 일반 이벤트는 읽은 run epoch를 사용한다. 이를 정확히 복구하려면 저장 계약 변경이
  필요하며 이번 최소 수정 범위 밖이다. 오류의 현재 fence도 응답 직후 바뀔 수 있으므로
  성공 허가가 아니라 재시도 힌트이고, 기존 CAS/fencing이 계속 최종 판단한다.
  실제 모델/브라우저 Stop 화면은 이번 실행 범위에서 검증하지 않았다.
- 검증(이번 재개): 수정 전 PR-7a 단위 테스트 19 통과/4 실패를 재현했고, 수정 후
  같은 파일 23개가 통과했다. 두 서버 Python 파일과 해당 테스트의 py_compile 통과.
  harness_console에서 tsc 통과, Vitest `src/web src/protocol` 15파일/107개 통과,
  npm test 10개 통과, build 통과. localStorage 경고 및 혼합 import/큰 청크 빌드 경고가 있다.
- Mongo 검증 제한: 최신 재개 안내의 허용에 따라 gateway pytest를 실행했지만,
  Mongo 연결이 샌드박스 `Operation not permitted`로 거절되어 중단했다(59 통과,
  Mongo setup/cleanup 단계 2 실패 후 중단). 환경변수 URI의 값은 출력하지 않았다.
  설치·서버·Mongo·브라우저·소켓 서버 기동 및 커밋/push는 하지 않았다.
- 사용자 재실행 명령(프로젝트 루트):
  `../.venv/bin/python -m pytest harness/tests/test_gateway_*.py -p no:cacheprovider -q`
  이번에는 전체 harness pytest를 실행하지 않았다.
- Mongo 파일(`*_mongo.py`, `test_gateway_session_db.py`)을 제외한 gateway 테스트는
  **813개 전부 통과**했다. in-process ASGI/WebSocket 테스트이며 실제 서버 소켓은 열지 않았다.

### 9c fix7 — 실행 종료 직후 결과 검증 표시 갱신 누락

- 결함: Stop 후 `run_finished{status:'cancelled'}`로 채팅은 idle이 되지만 결과
  패널은 “검증 중 — 확정 답변 대기 · 리비전 1”에 머문다. 완료/실패에도 같은
  경쟁 상태가 있고, 세션을 다시 열면 최종 상태가 표시된다.
- 원인: `useYieldResults`의 `refetchInterval: busy ? 3000 : false`는 실행 종료 시
  폴링만 중단한다. 마지막 응답이 running이면 종료 상태를 가져올 마지막 조회가 없다.
- 수정: `src/yield/use-yield-results.ts`에서 이전 sessionId/busy를 ref에 보관하고,
  같은 유효 세션에서 busy가 true→false로 전이할 때만 즉시 한 번 refetch한다.
  첫 렌더, idle 유지, 세션 전환에는 추가 refetch하지 않는다. 훅 시그니처와
  HermesWebApp, 원본 Hermes/platform/protocol 코드는 변경하지 않았다.
- 회귀: fake fetcher와 실제 QueryClient로 running→completed/cancelled/failed의
  반환 data 갱신, idle 유지 요청 수, 세션 전환 요청 수를 검증하는 5개 테스트 추가.
  수정 전 종료 상태 3개 테스트가 running 잔존으로 실패했고 수정 후 5개 모두 통과했다.
- 검증 범위: 오프라인 훅/웹 테스트이며 실제 엔진·서버·브라우저·Mongo는 실행하지 않았다.
- 최종 검증: `npx tsc --noEmit -p .` 통과, `npx vitest run src/yield src/web`
  10파일/53개 통과(신규 5개 포함), `npm test` 10개 통과, `npm run build` 통과.
  기존 localStorage 및 동적/정적 import 혼용·500 kB 초과 청크 경고는 유지된다.

### 9c — 읽기 전용 도구 허용 목록

- `tests-integration/pr9_real_dev_server.py`에 `PR9_ALLOW_TOOLS`(쉼표 구분,
  기본 빈값)를 추가했다. 순수 함수 `resolve_allowed_tools(registry, raw)`가
  등록 여부와 `read_only is True`·`effect == 'read'`를 검증하며, 잘못된 목록은
  `make_real_app()`에서 요청 처리 전에 `ValueError`로 실패한다.
- 허용 도구의 원래 핸들러와 기존 `read_result` 예외를 유지하고 나머지는
  계속 차단·기록한다. 명시적 허용 목록은 `/__dev/state`의 `allowed`에 표시하며,
  목록이 있으면 외부 DB 조회 가능성을 stderr 한 줄로 경고한다(검증된 이름만 출력).
- README에 사용법을 추가했다. `test_pr9_allow_tools.py`의 오프라인 pytest
  **14개 통과**, 대상 서버 파일의 `py_compile` 통과. 검증은 `domain_registry()`와
  순수 함수만 사용했으며 서버·브라우저·Mongo·Oracle·도구 호출은 실행하지 않았다.
