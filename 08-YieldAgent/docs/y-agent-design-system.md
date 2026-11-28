# Y-Agent 디자인 시스템

웹 콘솔과 데스크톱 앱(`harness_console`)의 글래스, 서피스, 아이콘, 카탈로그, 컴포저·진행 상태 및 뉴트럴 팔레트 완성 기준이다. 글자 크기·글꼴 기준은 [y-agent-typography.md](y-agent-typography.md)를 함께 따른다.

공통 CSS 변수는 [yuri.css](../harness_console/src/web/yuri.css)의 `.hermes-web.yuri-glass`와 [web.css](../harness_console/src/web/web.css)에 정의한다. 새 화면·컨트롤·카드·아이콘을 추가할 때 임의의 `rgba`/그라데이션을 새로 쓰지 말고 아래 토큰과 공용 클래스를 재사용한다.

## 1. 글래스 컨트롤 토큰 (`--yagent-glass-*`)

헤더 컨트롤, 뱃지, 입력창, 툴바 필 버튼 등 **떠 있는 유리 요소**는 모두 동일한 토큰을 사용한다.

| 토큰 | 값 | 용도 |
| --- | --- | --- |
| `--yagent-glass-border` | `#777e8a26` | 기본 유리 테두리 |
| `--yagent-glass-border-focus` | `#77849859` | 호버·포커스 테두리 |
| `--yagent-glass-bg` | `linear-gradient(180deg, #ffffffd6, #ffffff70 58%, #f2f4f85c)` | 기본 유리 배경 (상→하 균일 광택) |
| `--yagent-glass-bg-hover` | `linear-gradient(180deg, #fffffff0, #ffffff99 58%, #f2f4f878)` | 호버·포커스 배경 |
| `--yagent-glass-blur` | `blur(8px) saturate(110%)` | `backdrop-filter` |
| `--yagent-glass-shadow` | `inset 0 1px 1px #fffffff5, inset 0 -1px 1px #ffffff99, 0 2px 6px #343b500d` | 상·하단 유리 하이라이트 + 연한 외곽 그림자 |

> [!NOTE]
> `web.css`에 `.hermes-web * { box-shadow: none; }` 리셋이 있으므로 글래스·카드 그림자에는 `box-shadow: var(--yagent-glass-shadow) !important;`를 함께 적용한다.

### 적용 대상
- 좌측 사이드바 토글 버튼 (`.web-sidebar-toggle`)
- 상단 `Y-Agent` 뱃지 (`.yuri-identity h2::before`)
- 우측 최대화/최소화 컨트롤 (`.yuri-window-controls`)
- 하단 채팅 입력창 (`[data-slot="composer-surface"]`, `"수율에 대해 물어보세요"`)
- 카탈로그·관리 페이지 툴바 (내 스킬, 내 MCP 서버 등: 우측 생성 버튼 `.skills-create`, 세그먼트 필터 트랙 `.skills-filters`, 검색 인풋 `.skills-search`)

### 글래스 내부 활성 인디케이터 (`--yagent-glass-pill-*`)

글래스 캡슐 안에서 슬라이딩으로 선택 상태를 표시하는 알약(`.skills-filter-pill` 등)에 사용한다.

| 토큰 | 값 |
| --- | --- |
| `--yagent-glass-pill-bg` | `linear-gradient(155deg, #eaeef6cc, #dfe5f099)` |
| `--yagent-glass-pill-border` | `#777e8a22` |
| `--yagent-glass-pill-shadow` | `inset 0 1px 1px #ffffffd9, 0 1px 3px rgba(52, 59, 80, 0.08)` |

### 강조·파괴적 글래스 변형 (`--yagent-glass-primary-*`, `--yagent-glass-destructive-*`)

| 토큰 | 값 | 용도 |
| --- | --- | --- |
| `--yagent-glass-primary-border` | `#68738c38` | 소프트 슬레이트 강조 테두리 |
| `--yagent-glass-primary-bg` / `-hover` | `linear-gradient(180deg, #eef1f8db, #e3e8f4ad)` / `#e7ebf6eb → #dae0f0cc` | 일반 확인 버튼 |
| `--yagent-glass-destructive-border` | `#c25b6738` | 소프트 로즈 오류·삭제 테두리 |
| `--yagent-glass-destructive-bg` / `-hover` | `linear-gradient(180deg, #fef2f4de, #f9e2e6b3)` / `#fce8eceb → #f5d4dac7` | 삭제·복원 버튼, 오류 알림 박스·실패 헤더 |

## 2. 소프트 카드 서피스 (`--yagent-surface-card-*`)

대화 중 확인 질문(HITL) 카드, 내 스킬 카드, 내 MCP 서버 카드처럼 본문 위에 올라가는 정돈된 카드에 사용한다.

| 토큰 | 값 |
| --- | --- |
| `--yagent-surface-card-bg` | `#ffffffde` |
| `--yagent-surface-card-border` | `#ebedf2` |
| `--yagent-surface-card-shadow` | `0 2px 10px rgba(36, 38, 50, 0.035)` |

- 카드 라운드: HITL 카드 `20px`, 카탈로그 카드(스킬·MCP) `18px`

## 3. 배경 및 사이드바 무채색 뉴트럴 팔레트

사이드바와 메뉴에는 보라기·초록기·청록기 없이 **완전 무채색(R=G=B)** 뉴트럴 그레이를 사용한다 ([sidebar.css](../harness_console/src/web/sidebar.css), [yuri.css](../harness_console/src/web/yuri.css)).

| 역할 | 색상 |
| --- | --- |
| 메인 창 배경 | `linear-gradient(150deg, #fff, #f6f7f9 68%, #eef0f4)` |
| 사이드바 배경 | `#f7f7f7` |
| 사이드바 구분선 / 메뉴 테두리 | `#e8e8e8` / `#e6e6e6` |
| 사이드바 항목 호버 | `#eeeeee` |
| 사이드바 현재 선택 (`aria-current="page"`) | `#e8e8e8` |
| 기본 텍스트 / 보조 텍스트 | `#262626` / `#3f3f3f` / `#737373` |
| 입력창 테두리 / 포커스 | `#dfdfdf` / `#a3a3a3` |

## 4. 모달, 확인 다이얼로그 & 결과 패널 (`.skill-dialog`, `.yagent-confirm-dialog`)

스킬 상세 모달, MCP 서버 추가/편집 모달, 공통 `ConfirmDialog`(대화 삭제, 체크포인트 복원 등)는 모두 동일한 **중앙 프로스트 화이트 모달 시스템**으로 통일한다. 우측 슬라이드 드로어는 사용하지 않는다.

- **오버레이 (`.skill-dialog-overlay`, `.yagent-dialog-overlay`)**: `rgba(24, 27, 36, 0.22)` + `backdrop-filter: blur(5px)` (일반 창에서는 `.yuri-window-frame` 안쪽 `inset: 10px 10px 14px; border-radius: 20px;`으로 클리핑)
- **중앙 모달 쉘 (`.skill-dialog`)**:
  - 폭 `min(600px, calc(100vw - 48px))`, 최대 높이 `calc(100dvh - 56px)`, 라운드 `22px`, `border: 1px solid #e5e7ef`
  - 프로스트 화이트(`#fffffff8` 또는 `linear-gradient(180deg, #fffffff7, #fafbfef0)`), `backdrop-filter: blur(16px) saturate(115%)`, 상단 인셋 하이라이트 + 소프트 그림자(`0 20px 56px rgba(24, 28, 42, 0.16), inset 0 1px 1px #ffffff`)
  - 내부 구조: `.skill-dialog-head`(제목·서브메타·우상단 `.skill-close` 원형 `30px` 글래스 버튼) → `.skill-dialog-scroll`(본문/폼 스크롤 영역) → 필요 시 `.mcp-dialog-footer`(`padding: 14px 24px 16px; border-top: 1px solid #eff1f5; background: #fafbfe80;`, 하단 힌트 `.mcp-hint` + 우측 액션 `.mcp-panel-actions`, 삭제 버튼 `.mcp-delete`는 `margin-right: auto`)
- **확인 다이얼로그 (`.yagent-confirm-dialog`)**:
  - 폭 `min(340px, calc(100vw - 44px))`, 라운드 `20px`, 우상단 중복 닫기(`×`) 버튼 생략 (`취소`·`Esc`·바깥 클릭으로 닫힘)
  - 하단 버튼: 높이 `32px`, 알약형(`border-radius: 999px`), `--yagent-font-caption`(`12px`)
    - 취소: 공통 글래스 필(`--yagent-glass-*`)
    - 일반 확인: 소프트 슬레이트 글래스 필(`--yagent-glass-primary-*`)
    - 삭제·복원(파괴적 액션): 소프트 로즈 글래스 필(`--yagent-glass-destructive-*`)

### 모달 내부 결과·패널 박스 규격 (`.mcp-test-result`)

연결 테스트 결과나 검증 결과를 모달 내부에 표시할 때 **과한 통초록 배경을 금지**하고 뉴트럴 헤더 + 흰색 본문 + 아이콘 포인트 조합을 사용한다 ([McpServers.css](../harness_console/src/web/McpServers.css)).

- **컨테이너 (`.mcp-test-result`)**: `border: 1px solid #e6e8f0; border-radius: 12px; background: #ffffff; overflow: hidden; margin-bottom: 12px;`
- **통과 헤더 (`.mcp-test-pass`)**: 뉴트럴 연회색 배경(`#f4f5f9`), 텍스트 `#2e313b`, `font-weight: 480`, `padding: 10px 12px`. 좌측 `.mcp-test-summary` 앞에 서클체크 포인트(`.mcp-test-check`, `#4a8566` `14px`)만 배치하고, 우측 `.mcp-test-meta`는 `11.5px` / `#7e8494`로 시각·소요 시간을 표기한다.
- **실패 헤더 (`.mcp-test-fail`)**: `background: var(--yagent-glass-destructive-bg); color: #9c4853; font-weight: 480;`.
- **본문 리스트 (`.mcp-test-result ul`)**: 흰색(`#ffffff`) 배경 유지, 상단 경계 `border-top: 1px solid #eceef5`, 항목 구분선 `border-bottom: 1px solid #eff1f5`, 코드명 `#292c35` / 설명 `#777b88`.

## 5. 드롭다운·팝업(Popover / Menu) 및 공통 모션 규칙

사이드바 세션 메뉴(`이름 바꾸기 / 보관 / 삭제`), 입력창 제품 선택 팝업(`전체 제품`), 컴포저 옵션 메뉴(`.web-composer-options-menu`), 기타 드롭다운 리스트는 모두 동일한 서피스와 모션 규칙을 따른다.

### 팝업 서피스 토큰 (`--yagent-popover-*`)

| 토큰 | 값 |
| --- | --- |
| `--yagent-popover-bg` | `#fffffff5` |
| `--yagent-popover-border` | `#e6e8f0` |
| `--yagent-popover-blur` | `blur(14px) saturate(115%)` |
| `--yagent-popover-shadow` | `0 10px 28px rgba(24, 28, 42, 0.11), 0 2px 6px rgba(24, 28, 42, 0.05), inset 0 1px 1px #ffffff` |

- **모서리 라운드**: `14px` (내부 항목 `7~8px`)

### 공통 모션 & 인터랙션 규칙
1. **표준 이징 곡선**: 진입 `cubic-bezier(0.22, 1, 0.36, 1)`, 퇴장 `ease-in`
2. **드롭다운 / 컨텍스트 메뉴**:
   - 팝업 본문에 `.yagent-popover`를 적용한다. `data-state="open|closed"`로 상태를 전달하고, 트리거에 붙는 변을 `transform-origin`으로 지정한다. 위로 열리는 메뉴는 `data-side="top"`을 사용한다.
   - **열림 (`160ms`)**: 트리거 쪽 `4px`에서 제자리로 이동하며 `opacity: 0 → 1`, `scale(.96) → 1`.
   - **닫힘 (`120ms`)**: 트리거 쪽으로 `3px` 이동하며 `opacity: 1 → 0`, `scale(1) → .96`. 입력을 즉시 닫고 애니메이션 끝에 DOM을 정리한다.
   - **트리거 화살표(`Chevron`)**: 드롭다운은 `180deg` 회전(`170ms cubic-bezier(0.22, 1, 0.36, 1)`), 인라인 접기/펼치기(`>` 화살표)는 `90deg` 회전(`160ms ease-out`).
3. **모달(Dialog)**: 열림 `180ms` 팝인(`translateY(6px) scale(0.97) → none`), 닫힘 `120~125ms` 팝아웃(`data-state="closed"`)
4. **접기/펼치기(Disclosure / 인라인 검색창)**: 높이(`height: 0 ↔ auto` 또는 `grid-template-rows: 0fr ↔ 1fr`) + `opacity` 동시 전환(`180~220ms`)
5. **화면·단계 전환**: 사이드바 선택 이동 및 탭 필터는 스프링 슬라이드(`layoutId`), 본문/HITL 단계 전환은 `160~200ms` 소프트 라이즈(`translateY(4~7px) → 0` + 페이드인)
6. **접근성**: `prefers-reduced-motion: reduce` 환경에서는 모든 모션·회전 스피너·shimmer를 비활성화한다.

## 6. 카탈로그·관리 페이지 및 카드 리스트 규격

내 스킬(`SkillsCatalog`), 내 MCP 서버(`McpServers`)와 같은 관리·카탈로그 화면은 [SkillsCatalog.css](../harness_console/src/web/SkillsCatalog.css)의 공통 레이아웃·툴바 클래스를 기반으로 구성하며, 새로운 카드 리스트 화면을 추가할 때도 아래 규격을 그대로 따른다.

### 6.1 페이지 공통 레이아웃 & 헤더

- **루트 컨테이너 (`.skills-catalog`)**:
  - `container-type: inline-size; flex: 1; min-height: 0; width: 100%; overflow: auto;`를 지정해 뷰포트가 아닌 **본문 패널 너비 기준 `@container` 쿼리**로 반응형 동작을 제어한다.
  - 화면별 전용 스타일이 필요하면 `.skills-catalog`를 유지한 채 보조 클래스(예: `.skills-catalog.mcp-page`)를 조합한다.
- **내부 래퍼 (`.skills-inner`)**:
  - `max-width: 980px; margin-inline: auto;`로 최대 읽기 폭을 제한한다.
  - 패딩은 컨테이너 폭에 따라 단계적으로 축소한다: 기본 `24px 28px 40px` → `≤ 760px` `20px 20px 32px` → `≤ 480px` `14px 16px 28px`.
- **헤더 (`.skills-heading`)**:
  - `display: flex; align-items: center; justify-content: space-between; gap: 12px;`로 좌측 제목 블록과 우측 주 액션을 배치한다.
  - **제목(`h1`)**: `--yagent-font-heading`(`16px`), `font-weight: 650`, `line-height: 1.4`, `letter-spacing: -.03em`, `#252730`.
  - **설명(`p`)**: `--yagent-font-caption`(`12px`), `#828694`, `margin-top: 3px`.
  - **우측 글래스 액션 버튼 (`.skills-create`)**: `flex: none; height: 34px; padding: 0 13px; border-radius: 999px;`, `--yagent-glass-*` 토큰 + `box-shadow: var(--yagent-glass-shadow) !important;` 적용. 호버 시 `--yagent-glass-bg-hover` / `--yagent-glass-border-focus`, 클릭(`:active`) 시 `transform: scale(.98)`.

### 6.2 글래스 툴바 & 반응형 규칙 (`.skills-toolbar`)

툴바는 `display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; margin: 18px 0;`(`≤ 480px`에서 `margin: 14px 0; gap: 8px;`)으로 구성하며, 좌측 세그먼트 필터와 우측 검색창이 한 줄에서 공존하다가 협소 폭에서만 자연스럽게 줄바꿈되도록 설계한다.

1. **슬라이드 세그먼트 필터 (`.skills-filters`)**
   - **무조건 1열 유지**: `display: inline-flex; flex: none; flex-wrap: nowrap; max-width: 100%; min-height: 34px; padding: 2px 3px; gap: 2px; border-radius: 999px;`와 `overflow-x: auto; scrollbar-width: none;`를 적용해 탭이 많아져도 2줄로 깨지지 않게 한다.
   - 트랙은 `--yagent-glass-*` 토큰을 사용한다.
   - **개별 탭 (`.skills-filter-tab`)**: `flex: none; height: 28px; padding: 0 12px;`(`≤ 480px` `0 10px`, `≤ 400px` `0 9px`), `white-space: nowrap; font-size: var(--yagent-font-caption);`. 비활성 `#777b85`, 활성(`[aria-pressed="true"]`) `#292c36` / `font-weight: 580`, 카운트(`<small>`) `#8b909e`.
   - **활성 슬라이딩 필 (`.skills-filter-pill`)**: `motion.span`에 화면별 고유 `layoutId`(예: `skills-filter-pill`, `mcp-filter-pill`)와 `transition={{ type: 'spring', stiffness: 460, damping: 36 }}`을 부여하고 `--yagent-glass-pill-*` 토큰으로 스타일링한다.
2. **유연한 검색창 (`.skills-search`)**
   - `display: flex; flex: 1 1 112px; min-width: 112px; max-width: 176px; height: 34px; padding: 0 12px; gap: 7px; border-radius: 999px;`에 `--yagent-glass-*` 토큰을 적용한다(`:focus-within` 시 `--yagent-glass-border-focus` + `--yagent-glass-bg-hover`).
   - 내부 `input[type="search"]`는 `min-width: 0; width: 100%; font-size: var(--yagent-font-caption);`로 두고 브라우저 기본 `::-webkit-search-cancel-button`은 숨긴다.
   - 검색어가 비어 있지 않을 때만 `.skills-search-clear`(`18×18px` 원형 버튼, `aria-label="검색어 지우기"`, 호버 `#e9ecf3`)를 노출한다.
   - **줄바꿈 동작**: 필터 1열 + 검색창 최소 폭(`112px`)이 한 줄에 들어가는 구간에서는 검색창이 `176px → 112px` 사이로 유동 축소된다. 그보다 좁아지면 `.skills-toolbar`의 `flex-wrap: wrap`으로 검색창만 다음 줄로 내려가며, `@container (max-width: 400px)`에서 `max-width: none;`을 적용해 한 줄 전체 너비를 채운다.

### 6.3 카드 그리드 & 카드 서피스 규격

#### 반응형 그리드 (`.skills-grid` / `.mcp-grid`)

| 컨테이너 폭 (`@container`) | 열 수 (`grid-template-columns`) | 간격 (`gap`) | 비고 |
| --- | --- | --- | --- |
| 기본 (`> 760px`) | `repeat(3, minmax(0, 1fr))` | `12px` | 표준 3열 카탈로그 |
| `max-width: 760px` | `repeat(2, minmax(0, 1fr))` | `12px` | 2열 축소 (`McpServers`는 `780px` 기준) |
| `max-width: 480px` | `minmax(0, 1fr)` | `12px` | 단일 1열 (`McpServers`는 `510px` 기준) |

#### 카드 서피스 및 상태

| 상태 | 스타일 규격 |
| --- | --- |
| **기본 카드** (`.skill-card`, `.mcp-card`) | `border: 1px solid var(--yagent-surface-card-border)` (`#ebedf2`), `background: var(--yagent-surface-card-bg)` (`#ffffffde`), `box-shadow: var(--yagent-surface-card-shadow) !important`, `border-radius: 18px`, `padding: 15px 16px 13px`, `display: flex; flex-direction: column; gap: 12px;` |
| **호버 / 포커스** | `border-color: #d6dae6`, `background: #fffffff2`, `box-shadow: 0 6px 18px rgba(36, 38, 50, 0.065) !important`, `transform: translateY(-1px)` (`150ms ease`) |
| **비활성 (`off`)** | `[data-enabled="false"]` 또는 `.mcp-off`: 카드 서피스(`--yagent-surface-card-bg`)는 유지하고 제목·아이콘·본문에 `opacity: .68`을 적용해 활성 항목과 구분한다. 스위치·액션 컨트롤은 선명도와 조작 가능성을 유지한다. |
| **추가 카드 (Dashed)** (`.mcp-add-card`) | 그리드 마지막 슬롯 생성 유도 카드. `border: 1px dashed #d5d9e4`, `border-radius: 18px`, `background: linear-gradient(180deg, #ffffffa6, #f7f8fc8c)`, 중앙 정렬(`flex-direction: column; justify-content: center; align-items: center; gap: 4px;`). 주 라벨 `--yagent-font-ui`(`#5b6172`, `font-weight: 520`), 보조 문구 `--yagent-font-caption`(`#8b909e`), 아이콘 `.mcp-plus-icon.mcp-plus-icon-lg`(`24px`). 호버 시 `border-color: #aeb5c8; background: #ffffffde; color: #292c35; transform: translateY(-1px);`. |

### 6.4 카드 3단 시각 위계 & 인터랙션 원칙

카드는 **상단 식별 → 본문 핵심 정보 → 하단 메타/고스트 액션**의 3단 시각 위계를 따르며, 모든 카탈로그 카드에 동일한 인터랙션과 정보 밀도 원칙을 적용한다.

1. **상단 행 (`.skill-card-top` / `.mcp-card-head`)**:
   - 좌측에 아이콘 배지(`.skill-icon-badge`: `32×32px`, `border-radius: 10px`, `#e7eaf2`) 또는 상태 점(`.mcp-dot`: `7×7px`, 기본 `#c0c4d0`, 정상 `.mcp-dot-ok` `#4a8566`, 오류 `.mcp-dot-error` `#c25b67`) + 제목(`h2`, `--yagent-font-ui` `14px`, `font-weight: 580`, `#292c35`, `line-height: 1.45`)을 둔다.
   - 우측에 토글 스위치(`.skill-switch`: `role="switch"`, `30×18px` 트랙, OFF `#dadce4` / ON `#676d80`, `14px` 흰 노브, `z-index: 1`)를 배치하고 클릭 시 `stopPropagation()`으로 카드 전체 클릭과 독립 동작하게 한다.
2. **본문 영역 (`.skill-card-body` / `.mcp-card-body`)**:
   - **핵심 요약**: `--yagent-font-caption`(`12px`, `#777b88`, `line-height: 1.6`, 2줄 말줄임 `-webkit-line-clamp: 2`, `word-break: keep-all`) 또는 축약 명령어(`.mcp-card-cmd`, `#6d7384`, 한 줄 말줄임 + `title` 전문).
   - **플랫 태그 / 도구 칩 (`.mcp-tools code`)**: 테두리 없이(`border: 0`) 연한 플랫 배경(`#f2f4f8`), 텍스트 `#4f5463`, `border-radius: 6px`, `padding: 2px 7px`, `11px` 모노스페이스로 표시한다. 최대 3개까지 노출하고 초과분은 `+N` 칩(`.mcp-tools-more`, `position: relative; z-index: 1;`)으로 축약해 호버 시 `<Tip>`으로 나머지 항목을 보여준다.
   - **오류 / 빈 본문 안내**: 오류 메시지는 `.mcp-error` 2줄 말줄임, 비활성·로딩 빈 상태는 `.mcp-card-empty`(`#8b909e`, `12px`).
3. **하단 푸터 (`.skill-card-footer` / `.mcp-card-footer`)**:
   - `margin-top: auto; padding-top: 10px; border-top: 1px solid #f0f1f6;`으로 카드 높이가 달라도 하단 기준선을 맞춘다.
   - **좌측 메타 (`.mcp-tool-badge` / `.skill-category`)**: 도구 수 등 상태 메타는 **칩 박스 없이** 캡션 텍스트(`color: #787c8a; font-size: var(--yagent-font-caption); font-weight: 480;`) + 아이콘(`.mcp-wrench-icon` `13px`)으로 가볍게 배치하고, 필요 시 `<Tip>` 호버로 전체 목록을 노출한다. (단순 분류만 표시하는 스킬 카드는 `.skill-category` 연한 필 `#f2f4f8` 유지)
   - **우측 고스트 액션 (`.mcp-test-btn`)**: 평상시 투명 배경·테두리(`background: transparent; border: 1px solid transparent; color: #5c6170; font-weight: 480; padding: 3px 8px; margin-right: -8px; border-radius: 999px;`, `stopPropagation()`)를 유지하고 **호버·포커스 시에만 연한 pill(`background: #f0f2f7; color: #262932;`)**로 반응한다.
     - 진행 중: 회전 스피너(`Loader2` + `.mcp-spin`, `0.85s linear infinite`, `#7e8494`, `prefers-reduced-motion: reduce` 시 정지)
     - 완료: 서클체크 초록 포인트(`.mcp-test-check`, `#4a8566` `14px`) + 뉴트럴 텍스트(`테스트 완료`)
     - 오류: `.mcp-test-btn-error`(`color: #9c4853`, 호버 시 `--yagent-glass-destructive-bg-hover` / `--yagent-glass-destructive-border`)
4. **인터랙션·정보 밀도 & 모달 통일 원칙**:
   - 카드 하단 `⋯` 메뉴 버튼 대신 **카드 전체 클릭(키보드 포커스·Enter/Space 접근성 포함, `.skill-open::after { inset: 0; }` 패턴 등)**으로 상세/편집 모달을 연다.
   - 상세·편집·추가 화면은 우측 슬라이드 드로어 대신 스킬과 동일한 **중앙 모달(`.skill-dialog` 규격, 4장 참조)**로 통일한다.
   - 목록 카드에는 핵심 식별 정보(제목 + 상태 점 `●` + 토글 + 핵심 요약/도구 목록 + 간결한 하단 메타/고스트 액션)만 노출하고, 긴 실행 경로 전문·테스트 시각·상세 설정 폼·삭제 버튼 등은 모달 내부에 배치한다.
5. **로딩 / 빈 결과 / 오류 안내 (`.skills-notice`)**: `margin: 32px auto; text-align: center; color: #7d818e; font-size: var(--yagent-font-ui);`와 `role="status"` / `role="alert"`를 사용한다.

### 6.5 신규 카탈로그 화면 개발 체크리스트

- [ ] **공통 CSS 재사용**: `SkillsCatalog.css`를 import하고 `.skills-catalog`, `.skills-inner`, `.skills-heading`, `.skills-create`, `.skills-toolbar`, `.skills-filters`, `.skills-filter-tab`, `.skills-filter-pill`, `.skills-search`, `.skills-search-clear`, `.skill-switch`, `.skill-dialog`를 우선 재사용했는가?
- [ ] **토큰 준수**: `--yagent-glass-*`, `--yagent-glass-pill-*`, `--yagent-surface-card-*`, `--yagent-font-*` 변수를 사용하고 그림자에 `!important`를 포함했는가?
- [ ] **세그먼트 필터 1열 및 고유 `layoutId`**: 필터 트랙이 `flex-wrap: nowrap` 1열을 유지하고, `motion.span`의 `layoutId`가 다른 화면과 충돌하지 않는가?
- [ ] **검색창 축소·줄바꿈·초기화**: `.skills-search`가 `112px ~ 176px`에서 축소되다 협소 폭(`≤ 400px`)에서 `max-width: none`으로 줄바꿈되며, 입력 시 `.skills-search-clear`가 노출되는가?
- [ ] **컨테이너 반응형 그리드**: 뷰포트가 아닌 `@container` 브레이크포인트(3열 → 2열 → 1열)를 적용했는가?
- [ ] **카드 3단 위계·인터랙션·중앙 모달**: 플랫 태그(`#f2f4f8` + `+N` `<Tip>`), 박스 없는 좌측 메타, 호버 시 pill로 반응하는 우측 고스트 액션, `⋯` 없는 카드 전체 클릭 → `.skill-dialog` 중앙 모달, 독립 `stopPropagation()` 토글 스위치를 따르는가?
- [ ] **아이콘 & 상태 포인트**: 커스텀 Linear SVG(`mask` + `currentColor`)를 사용하고, 통배경 채우기 대신 아이콘·도트 포인트 색(`#4a8566` / `#9c4853`)을 적용했는가?
- [ ] **접근성 및 모션 감소**: `aria-labelledby`, `role="group"` + `aria-pressed`, `role="switch"` + `aria-checked`, `role="status"`/`role="alert"`, `prefers-reduced-motion: reduce` 대응을 완료했는가?

## 7. 아이콘 시스템 규격 (`public/icons/`)

### SVG 제작 및 렌더링 규칙

- **커스텀 / Figma Linear SVG 규격**: `width="24" height="24" viewBox="0 0 24 24" fill="none"`, `stroke="#171717"`, `stroke-width="1.5"`, `stroke-linecap="round"`, `stroke-linejoin="round"`를 기본으로 맞춘다.
- **CSS `mask` + `currentColor` 렌더링**: `<img>` 대신 `<span aria-hidden="true" />`에 `background-color: currentColor`와 `-webkit-mask` / `mask: url("/icons/<name>.svg") center / contain no-repeat;`를 적용한다. 텍스트 색상·호버·비활성 톤을 자동 상속하며 불필요한 이미지 노드를 만들지 않는다.

### 주요 공용 아이콘 카탈로그

| 파일 (`/icons/...`) | 표준 크기 | 용도 및 공용 클래스 |
| --- | --- | --- |
| `add-circle.svg` | `16px` / `24px` | 컴포저 `+` 버튼(`.web-composer-add`), 서버 추가 버튼·대시 카드(`.mcp-plus-icon`, `.mcp-plus-icon-lg`) |
| `check-circle.svg` | `14px` | 테스트 완료·연결 통과 아이콘 포인트(`.mcp-test-check`) |
| `wrench.svg` | `13px` | 카드 하단 도구 개수 메타(`.mcp-wrench-icon`) |
| `status-error.svg` | `14px` | 트레이스 오류(`.web-trace-icon`), **`transform: rotate(180deg)` 반전으로 `i` 안내 힌트(`.mcp-info-icon`) 재활용** |
| `chevron-right.svg` | `11px~12px` | 진행 상태 펼침(`.web-reply-progress-chevron`), 실행 단계 토글(`.web-trace-chevron`, 열림 시 `rotate(90deg)`) |
| `composer-chevron.svg` / `chevron-down.svg` | `13px` / `12px` | 컴포저 옵션 하위 메뉴(`.web-option-chevron`), 드롭다운 펼침 |
| `mcp.svg` | `16px` | 컴포저 MCP 서버 메뉴(`.web-mcp-icon`) |
| `skills.svg` | `16px` | 컴포저·사이드바 내 스킬 메뉴(`.web-option-skill-icon`) |
| `search.svg` | `15px` | 카탈로그 검색창(`.skill-mask-search`) |
| `stacked-layers.svg` | `16px~17px` | 제품 선택 트리거(`.web-product-icon`), 스킬 기본 아이콘(`.skill-mask-layers`) |
| `attach-file.svg`, `steer.svg`, `new-chat.svg`, `send.svg`, `stop.svg` | `16px~18px` | 컴포저 첨부·스티어·새 대화·전송·중지 액션 |
| `status-done.svg`, `status-stopped.svg`, `history.svg` | `14px` | 조회·계산 기록 트리거 및 단계 상태 아이콘 |

### 상태 색상 포인트 규칙

통배경 채우기(진한 초록·빨강 박스) 대신 **아이콘과 상태 점(`●`)에만 차분한 포인트 색**을 두고 버튼·헤더 서피스는 뉴트럴 톤을 유지한다.

| 상태 | 포인트 색 | 적용 대상 |
| --- | --- | --- |
| 완료 / 연결됨 | `#4a8566` | `.mcp-test-check`(서클체크), `.mcp-dot-ok`(상태 점) |
| 오류 / 실패 | `#9c4853` (텍스트·버튼) / `#c25b67` (도트) / `#bd777c` (트레이스 아이콘) | `.mcp-test-btn-error`, `.mcp-error`, `.mcp-test-fail`, `.mcp-dot-error`, `.web-trace-step[data-tool-state=error]` |
| 중립 / 안내 / 대기 | `#989dab` (`i` 힌트) / `#7e8494` (스피너) / `#c0c4d0` (꺼짐 도트) | `.mcp-info-icon`, `.mcp-spin`, `.mcp-dot` |

## 8. 채팅 컴포저 & 진행 상태 표시 규격

### 8.1 채팅 컴포저 입력창 (`web.css`)

- **서피스**: `[data-slot="composer-surface"]`에 `border-radius: 24px`와 `--yagent-glass-*` 토큰을 적용한다.
- **대칭 패딩 원칙**: 좌측 `+` 옵션 버튼(`.web-composer-options`)이 배치된 입력창(`[data-slot="composer-fade"]`)은 **새 채팅(`[data-new-chat]`)과 일반 채팅 모두 대칭 패딩(`padding: 6px 7px`)**으로 통일해 좌우·상하 여백 균형을 맞춘다.
- **1줄 수직 중앙 정렬**:
  - 좌측 `+` 버튼(`.web-composer-add`)과 우측 전송/중지 버튼(`.web-send-button`)을 `--web-composer-action-size: 34px` 원형으로 통일한다.
  - 입력 영역(`[contenteditable]`)에 `min-height: 34px; padding: 5px 0; font-size: var(--conversation-text-font-size); line-height: 24px;`를 적용해 1줄 기준 높이(`24 + 5×2 = 34px`)가 양쪽 원형 버튼과 정확히 일치하도록 수직 중앙 정렬한다.
  - 그리드 레이아웃: `grid-template-columns: auto minmax(0, 1fr) auto; grid-template-areas: "context input controls"; align-items: end; column-gap: 6px;` (첨부 파일 칩 `.web-chat-file-chips` 존재 시 상단 `"attachments attachments attachments"` 행 추가).

### 8.2 진행 상태 표시 (`ReplyProgress`, `YuriMascot.tsx` / `web.css`)

- **구조**: `.web-reply-progress` 안에 `64×64` 마스코트(`.web-reply-progress-mascot`)와 2행 카피(`.web-reply-progress-copy`)를 나란히 배치한다. 인라인 실행 기록을 펼칠 수 있는 상태(`details` 존재)에서는 카피를 `<button className="web-trace-progress-toggle" aria-expanded>`로 감싼다.
- **1행 — Shimmer 상태 문구 (`.web-reply-progress-label`)**:
  - `--yagent-font-ui`(`14px`), `{label}…` 문구에 좌→우 그라데이션 스윕(`yagent-progress-sweep`, `1.5s linear infinite`, `#707070 ↔ #202020`, `prefers-reduced-motion: reduce` 시 정지)을 적용한다.
- **2행 — 경과 시간·도구 요약 + `>` 화살표 (`.web-reply-progress-meta`)**:
  - `display: inline-flex; align-items: center; gap: 4px; color: var(--muted-foreground); opacity: .62; font-size: 11.5px; line-height: 1.3; font-variant-numeric: tabular-nums;`로 경과 시간과 도구 요약(`예: 2분 18초 · 도구 4건 (완료 2 · 오류 2)`)을 표시한다.
  - `details`가 있을 때 맨 오른쪽에 `.web-reply-progress-chevron`(`11×11px`, `/icons/chevron-right.svg` CSS `mask`, `background: currentColor`)을 배치해 클릭 가능성을 암시한다.
  - 클릭 시 하단 `.web-live-trace` 인라인 실행 단계를 여닫으며, `.web-trace-progress-toggle[aria-expanded=true] .web-reply-progress-chevron`에 `transform: rotate(90deg)`(`160ms ease-out`, `prefers-reduced-motion: reduce` 시 `transition: none`)을 적용한다.

## 9. 재활용 빠른 참조표 (토큰 & 공용 클래스 치트시트)

새 화면이나 컴포넌트를 만들 때 바로 복사·조합해 쓸 수 있는 핵심 토큰과 공용 클래스 요약이다.

### 핵심 CSS 변수 치트시트

| 카테고리 | 변수명 | 대표 용도 |
| --- | --- | --- |
| **글래스 컨트롤** | `--yagent-glass-bg`, `--yagent-glass-bg-hover`, `--yagent-glass-border`, `--yagent-glass-border-focus`, `--yagent-glass-blur`, `--yagent-glass-shadow` | 헤더 버튼, 입력창, 툴바 필터 트랙·검색창·생성 버튼 |
| **글래스 활성 필** | `--yagent-glass-pill-bg`, `--yagent-glass-pill-border`, `--yagent-glass-pill-shadow` | 세그먼트 탭 슬라이딩 활성 인디케이터 |
| **강조·오류 글래스** | `--yagent-glass-primary-*`, `--yagent-glass-destructive-*` | 확인 버튼, 삭제·복원 버튼, 오류 박스·실패 헤더 |
| **카드 서피스** | `--yagent-surface-card-bg`, `--yagent-surface-card-border`, `--yagent-surface-card-shadow` | HITL 카드, 스킬 카드, MCP 카드 |
| **팝업 서피스** | `--yagent-popover-bg`, `--yagent-popover-border`, `--yagent-popover-blur`, `--yagent-popover-shadow` | 드롭다운, 컨텍스트 메뉴, 컴포저 플라이아웃 |
| **타이포그래피** | `--yagent-font-heading` (`16px`), `--yagent-font-chat` (`15px`), `--yagent-font-ui` (`14px`), `--yagent-font-caption` (`12px`), `--hermes-system-font` | 전 화면 글자 위계 통일 |
| **상태 포인트** | 완료 `#4a8566` · 오류 `#9c4853` / `#c25b67` · 힌트 `#989dab` · 중립 도트 `#c0c4d0` | 아이콘·상태 도트 포인트 (통배경 채우기 지양) |

### 공용 클래스 치트시트

| 영역 | 공용 클래스 | 정의 파일 | 용도 |
| --- | --- | --- | --- |
| **카탈로그 골격** | `.skills-catalog`, `.skills-inner`, `.skills-heading`, `.skills-create` | `SkillsCatalog.css` | 컨테이너 쿼리 페이지, `980px` 래퍼, 헤더 + 글래스 생성 버튼 |
| **글래스 툴바** | `.skills-toolbar`, `.skills-filters`, `.skills-filter-tab`, `.skills-filter-pill`, `.skills-search`, `.skills-search-clear` | `SkillsCatalog.css` | 1열 슬라이드 필터 + `112~176px` 유연 검색창 + 지우기 버튼 |
| **카드·스위치** | `.skills-grid`, `.skill-card`, `.skill-open`, `.skill-switch`, `.skill-category`, `.skills-notice` | `SkillsCatalog.css` | 3→2→1열 그리드, 전체 클릭 카드, 독립 스위치, 빈/오류 안내 |
| **MCP 카드·결과** | `.mcp-card`, `.mcp-dot` / `-ok` / `-error`, `.mcp-tools`, `.mcp-tool-badge`, `.mcp-test-btn`, `.mcp-add-card`, `.mcp-test-result` | `McpServers.css` | 상태 도트, 플랫 태그, 고스트 액션, 대시 추가 카드, 뉴트럴 결과 패널 |
| **모달·팝업** | `.skill-dialog-overlay`, `.skill-dialog`, `.skill-dialog-head`, `.skill-dialog-scroll`, `.skill-close`, `.yagent-confirm-dialog`, `.yagent-popover` | `SkillsCatalog.css`, `yuri.css`, `web.css` | 중앙 프로스트 모달, 확인 다이얼로그, 표준 팝오버 |
| **컴포저·진행 상태** | `.web-composer-add`, `.web-reply-progress`, `.web-trace-progress-toggle`, `.web-reply-progress-label`, `.web-reply-progress-meta`, `.web-reply-progress-chevron` | `web.css` | 대칭 패딩 컴포저, shimmer + 메타 + 90도 회전 `>` 화살표 |
