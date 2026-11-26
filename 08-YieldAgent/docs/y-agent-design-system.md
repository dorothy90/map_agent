# Y-Agent 디자인 시스템

웹 콘솔과 데스크톱 앱(`harness_console`)의 글래스, 서피스, 사이드바 뉴트럴 팔레트 기준이다. 글자 크기·글꼴 기준은 [y-agent-typography.md](y-agent-typography.md)를 함께 따른다.

공통 CSS 변수는 [yuri.css](../harness_console/src/web/yuri.css)의 `.hermes-web.yuri-glass`에 정의한다. 새 컨트롤·카드·필 버튼을 추가할 때 임의의 `rgba`/그라데이션을 새로 쓰지 말고 아래 토큰을 재사용한다.

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
- 내 스킬 화면 툴바 (`새 스킬` 버튼, `전체 / 일반` 세그먼트 트랙, `스킬 검색` 인풋)

### 글래스 내부 활성 인디케이터 (`--yagent-glass-pill-*`)

글래스 캡슐 안에서 슬라이딩으로 선택 상태를 표시하는 알약(`.skills-filter-pill` 등)에 사용한다.

| 토큰 | 값 |
| --- | --- |
| `--yagent-glass-pill-bg` | `linear-gradient(155deg, #eaeef6cc, #dfe5f099)` |
| `--yagent-glass-pill-border` | `#777e8a22` |
| `--yagent-glass-pill-shadow` | `inset 0 1px 1px #ffffffd9, 0 1px 3px rgba(52, 59, 80, 0.08)` |

## 2. 소프트 카드 서피스 (`--yagent-surface-card-*`)

대화 중 확인 질문(HITL) 카드와 내 스킬 카드처럼 본문 위에 올라가는 정돈된 카드에 사용한다.

| 토큰 | 값 |
| --- | --- |
| `--yagent-surface-card-bg` | `#ffffffde` |
| `--yagent-surface-card-border` | `#ebedf2` |
| `--yagent-surface-card-shadow` | `0 2px 10px rgba(36, 38, 50, 0.035)` |

- 카드 라운드: HITL 카드 `20px`, 스킬 카드 `18px`

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

## 4. 모달 & 확인 다이얼로그 (`.skill-dialog`, `.yagent-confirm-dialog`)

스킬 상세 모달과 공통 `ConfirmDialog`(대화 삭제, 체크포인트 복원 등)는 동일한 프로스트 화이트 모달 시스템을 공유한다.

- **오버레이**: `rgba(24, 27, 36, 0.22)` + `backdrop-filter: blur(5px)` (일반 창에서는 `.yuri-window-frame` 안쪽 `inset: 10px 10px 14px; border-radius: 20px;`으로 클리핑)
- **모달 쉘**: 프로스트 화이트(`linear-gradient(180deg, #fffffff7, #fafbfef0)`), `backdrop-filter: blur(16px) saturate(115%)`, 상단 인셋 하이라이트 + 소프트 그림자
- **확인 다이얼로그(`ConfirmDialog`)**:
  - 폭 `min(340px, calc(100vw - 44px))`, 라운드 `20px`, 우상단 중복 닫기(`×`) 버튼 생략 (`취소`·`Esc`·바깥 클릭으로 닫힘)
  - 하단 버튼: 높이 `32px`, 알약형(`border-radius: 999px`), 글자 크기 `--yagent-font-caption`(`12px`)
    - 취소: 공통 글래스 필(`--yagent-glass-*`)
    - 일반 확인: 소프트 슬레이트 글래스 필(`--yagent-glass-primary-*` + 공통 `--yagent-glass-blur` / `--yagent-glass-shadow`)
    - 삭제·복원(파괴적 액션): 소프트 로즈 글래스 필(`--yagent-glass-destructive-*` + 공통 `--yagent-glass-blur` / `--yagent-glass-shadow`)

## 5. 드롭다운·팝업(Popover / Menu) 및 공통 모션 규칙

사이드바 세션 메뉴(`이름 바꾸기 / 보관 / 삭제`), 입력창 제품 선택 팝업(`전체 제품`), 기타 드롭다운 리스트는 모두 동일한 서피스와 모션 규칙을 따른다.

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
   - **트리거 화살표(`Chevron`)**: 열림 상태에서 `180deg` 회전(`170ms cubic-bezier(0.22, 1, 0.36, 1)`), 닫힐 때 정반대 역순(`180° ↔ 0°`) 복귀
3. **모달(Dialog)**: 열림 `180ms` 팝인(`translateY(6px) scale(0.97) → none`), 닫힘 `120~125ms` 팝아웃(`data-state="closed"`)
4. **접기/펼치기(Disclosure / 인라인 검색창)**: 높이(`height: 0 ↔ auto` 또는 `grid-template-rows: 0fr ↔ 1fr`) + `opacity` 동시 전환(`180~220ms`)
5. **화면·단계 전환**: 사이드바 선택 이동 및 탭 필터는 스프링 슬라이드(`layoutId`), 본문/HITL 단계 전환은 `160~200ms` 소프트 라이즈(`translateY(4~7px) → 0` + 페이드인)
6. **접근성**: `prefers-reduced-motion: reduce` 환경에서는 모든 모션을 비활성화한다.
