Created: 2026-10-08T12:53:45+09:00
Updated: 2026-11-21T03:00:00+09:00
Author: frontend_lead · Claude Code(#100)
Status: current

# 공통 UI 사용

- UI 동작·표현의 단일 기준 문서; 에이전트 지침에는 이 문서 참조만 유지
- 스타일(사용자 11/03): shadcn 부품(new-york, `frontend/components.json`) 기본. Tailwind 4는 부품 내부와 배치·간격 유틸리티로만 사용. 자체 CSS 클래스·인라인 style·색상 하드코딩 금지
- 공통 부품 재사용; 화면별 중복 구현·동일 색상/크기 하드코딩 복제 금지. import는 `@/` 별칭
- 기본 부품(`frontend/src/components/ui/`): button, badge, card, tabs, table, select, native-select, combobox, input, textarea, label, checkbox, radio-group, switch, dialog, Modal, popover, tooltip, separator, skeleton, scroll-area, alert, feedback
- 화면 골격 부품(`frontend/src/components/layout/`): AppShell, ScreenTitle(`PageHeader.tsx`), SectionCard, SideNav(사이드바 한 벌), LinkTabs(URL 탭), DescriptionList, NavigationGuard. 주소 선택값 도구: `hooks/useUrlState.ts`(useUrlOption·useUrlStep)
- 휴대폰 폭(사용자 11/21): 768px 미만 = Tailwind `max-md`. 배치는 `max-md:` 클래스로만 바꿔 PC(768px 이상) DOM·배치를 그대로 둔다. 동작이 달라지는 곳(첫 화면·알림 버튼)만 `hooks/useIsMobile.ts`(같은 기준 matchMedia, 없으면 PC). 휴대폰 범위는 알림 → 판정·사건 상세뿐(navigation.md 휴대폰 폭), `SectionCard` 좌우 안쪽 여백은 휴대폰에서 16px. 하단 시트·서랍은 쓰지 않는다
- 공통 토큰: `frontend/src/lib/tokens.css` 한 벌 — 원천 값(`--ink`, `--canvas` 등 기존 별칭 이름) → shadcn 의미 변수(`--background`, `--primary`, `--muted-foreground` 등) → Tailwind `@theme`(예: `bg-primary`, `text-muted-foreground`, `bg-warning-soft`, `text-page-title`). 새 색·크기는 이 파일에만 추가
- 화면 제목(사용자 11/03, 설정은 11/05): 운영 현황·이상 분석·탐지 설정·설정은 보이는 제목 줄을 두지 않음(상단 메뉴·사이드바의 현재 표시로 위치 확인). 화면 낭독기·문서 구조용 `ScreenTitle`(sr-only h1)만 두고, 제목 줄 옆 명령은 본문 첫 영역(오른쪽 정렬 줄·첫 카드 머리)으로. 탐지 설정 하위 화면의 상단 뒤로가기 아이콘은 유지. 설정 › 사용자 환경의 `저장 미지원`은 첫 카드(로그인 계정) 제목 줄 오른쪽(`SectionCard` `action`, 본문 위 빈 줄 없음, 사용자 11/28). 사건 상세의 돌아가기(`← 사건 목록`·`← 알림으로`)도 첫 카드(사건 요약) 제목 줄 오른쪽(불러오는 중·실패면 본문 위 한 줄). 카드 제목 줄 오른쪽 명령은 설명 줄이 없으면 한 줄에 제목과 세로 가운데(최소 28px — 머리 높이는 이전과 같다)
- 탐지 설정 타이포그래피: 이상 분석 기준; 사이드바 실폭 264px·메뉴 최소 높이 48px·사이드바 제목(`heading`)·그룹 제목 13px/600/18px 흐린 글자(`text-group`, 제목 있으면 제목 h2·그룹 h3, 없으면 그룹 h2)·메뉴 글자 14px/21px 500, 현재 항목 600(`text-menu` + 현재 `font-semibold`, URL 탭 같음)·페이지 제목 28px/600/36px(`text-page-title`)·로그인 화면 제목 22px/600/30px(`text-auth-title`, 375px 폭에서도 한 줄), 섹션 제목 15/22(`text-section-title`), 공통 Pretendard 상속
- 버튼: `variant="default|outline|ghost|secondary|destructive|link"`, `size="default|sm|lg|icon|icon-sm"`; 기본 `type="button"`, 제출은 `type="submit"` 명시
- URL 탐색: `Button asChild` + React Router `Link`; URL 탭은 `LinkTabs`(로컬 Tabs ARIA 적용 금지). 화면 안 지역 탭만 `Tabs`
- 선택 입력: 폼 입력은 `NativeSelect`(기본 키보드·모바일 동작), 목록 도구(정렬·페이지 크기)는 `Select`, 항목이 수십 개를 넘는 선택(예: 인터페이스)은 검색 선택 `Combobox`(검색 입력 + 목록, ↑↓·Enter·Esc)
- 모달: `Modal`의 `isOpen/onOpenChange/title/description/size`; 확인창은 `Dialog`·`DialogContent`·`DialogTitle`·`DialogDescription` 조합. Radix 직접 import는 `components/ui/` 안에서만
- 모달 하단 확인·취소 버튼: 공통 `DialogFooter`; 본문과 24px 여백·16px 상단 패딩·구분선, 버튼 사이 12px 이상
- 닫기: Escape·외부 클릭은 기본 닫기; `onOpenChange(false)`를 취소로 처리. 기본 포커스 복귀, 미저장 보호의 폐기는 명시 버튼에서만 수행
- 조회 상태: `LoadingState`, `EmptyState`, `ErrorState title/onRetry/retryLabel`; 재조회 실패 시 이전 데이터 유지. 조회 오류는 인라인 표시·재시도, 중복 토스트 금지
- 서버 조회 훅: `useAccountQuery`(`frontend/src/lib/query.ts`) — 계정별 key·AbortSignal·세션 변경 후 응답 폐기·자동 재조회 없음·진행 중 작업만 5초 폴링(상세는 navigation.md 상태 책임)
- 아이콘 버튼: 접근 가능한 이름 필수. 전환: 아래 "움직임"
- 알림: `frontend/src/lib/notify.ts`; 앱 Toaster 1개. 성공 2.4초·오류 6초·로딩 명시 종료; 같은 원인은 같은 ID로 갱신
- 피드백: 실제 성공 후 완료 표시; 비동기 작업 접수와 최종 완료 구분. 입력 오류는 입력 근처, 저장 실패 시 편집 내용 유지
- 운영 이상 이벤트: 목록·알림 패널에 유지; 이벤트마다 토스트 발행 금지
- 조회 상태: 최초 로딩 / 빈 응답 / 검색 결과 없음 / 실패 / 연결 끊김 구분; 이전 데이터의 존재를 정상 연결로 표시 금지
- 레이어: 모달·드래그 미리보기·토스트 토큰 분리; `--z-dialog` < `--z-drag` < `--z-toast` < `--z-tooltip`
- 아이콘: Tabler; 위젯 배치: react-grid-layout. 단순 효과 CSS / 복합 전환 Motion, reduced-motion 준수

## 움직임

- 토큰 한 벌(사용자 11/12 후속 → 11/13): CSS `frontend/src/lib/tokens.css` `--motion-*` = Motion(motion/react) `frontend/src/lib/motion.ts` `MOTION_MS`·`MOTION_EASE`(같은 값, `tests/common-ui.mjs` 확인). 새 의존성 없음(tw-animate-css·motion 기존)
- 길이: `fast` 120ms(체크·토글·누름, 작은 표면 퇴장) · `base` 180ms(팝오버·선택 목록·툴팁·제목 줄·로딩/빈/오류 표시 등장, 대화상자·토스트 퇴장) · `slow` 240ms(대화상자·토스트 등장) · `layout` 320ms(하위 목록 펼침·넓게 보기처럼 배치가 바뀌는 전환)
- 스프링(`lib/motion.ts` `MOTION_SPRING`, motion `{ type: "spring", stiffness, damping }`·질량 1): `grow` 600/34 · `stretchLead` 760/48 · `stretchTrail` 240/30
- 곡선: 등장 `ease-motion-out` cubic-bezier(0.2, 0, 0, 1) · 퇴장 `ease-motion-in` cubic-bezier(0.4, 0, 1, 1) · 상태 변화 `ease-motion-standard` cubic-bezier(0.4, 0, 0.2, 1). 퇴장은 등장보다 한 단계 짧게
- 모양(`motionClasses`): 대화상자·Modal(팝업) = 투명도 + 97% 확대 + 아래 8px, 배경 = 투명도만 / 팝오버·Select·Combobox = 투명도 + 95% 확대 + 열린 쪽 가장자리 4px / 툴팁 = 같은 모양 / 토스트(스낵바) = 아래 8px + 97% 확대(`--animate-toast-in/out`, `AppProvider`의 `ToastBar`) / 체크·라디오 표시 = 50%에서 커지며 나타남 / 체크 상자·스위치·눌림 버튼 색·위치 = `toggle` / 버튼 누름(좋아요·토글 버튼 포함) = 97% 눌림(`press`) / 섹션 제목 줄(`SectionCard` 머리)·`LoadingState`·`EmptyState`·`ErrorState` = 처음 그려질 때 투명도 + 아래 4px(`enter`)
- 흘러나오기(가짜 생성 효과, AI 판정 근거 요약 `[AI 근거 생성 요청]` 뒤, 사용자 11/26): 채팅 앱 스트리밍처럼 배지 → 한 줄 요약 → 문단 → 표 머리·행(ML은 참가자 묶음) → 빈 이유 → 확인할 것 제목·항목 → 고정 안내 순으로 조각을 이어 붙이고, 새 조각마다 `slow` 길이로 투명 → 불투명. 글은 어절 단위(괄호 이름은 한 조각, 줄바꿈 묶음 유지), 표 행·제목·안내는 통째. 아직 안 나온 조각은 DOM에 없어 자리를 차지하지 않고 카드는 나온 만큼 자란다(뼈대·미리 잡은 크기 없음). 간격 어절 45ms·행 90ms·블록 사이 140ms, 마지막 페이드 끝 ≤ 3,000ms(넘으면 모든 간격을 같은 비율로 줄임, 짧으면 더 빨리 끝남) — `lib/motion.ts` `STREAM_MS`, 시계 `useTextStream`(경과 시간 기준 rAF, 숨은 탭에서 돌아오면 밀린 조각을 한꺼번에·상한이 지났으면 바로 끝), CSS `styles.css` `.stream-in`. 끝나면 효과 없는 표시와 같은 DOM이다. 누른 때 한 번만(재조회·넓게 보기·다시 들어오기는 재생 안 함), 나중에 다듬은 문단이 오면 그 문단만 `enter` 한 번. 동작 줄이기면 효과 없이 즉시 전부
- 메뉴 현재 표시(`SideNav`, 사용자 11/28): 상위 메뉴 항목 = 제자리 피어남(이동 없이 도착지 배경이 92% → 100% `grow` 스프링 + 투명도 `base`, 출발지 배경은 `fast` ease-in으로 사라짐). 하위 목록(탐지 설정 현재 메뉴 목차·이상 분석 `완료` 아래 확인 결과별) = 늘었다 줄기(목록에 왼쪽 2px 선 하나, 이동 방향 앞 가장자리 `stretchLead`·뒤 가장자리 `stretchTrail`로 늘었다 줄어듦, 하위 목록 높이 전환 동안 칸 위치를 다시 잰다). 방식은 `SIDE_NAV_INDICATOR` 한 곳에서 단계별로 정한다. 공통: 글자·아이콘 색은 표시가 켜질 때(늘었다 줄기는 표시 가운데가 대상 칸 가운데의 칸 높이 1/4 안에 들어올 때) `base` ease-out으로 켜지고 떠나는 칸은 `fast`로 꺼진다, 현재(이동 대상) 칸은 마우스 올림 배경이 켜지지 않는다, 동작 줄이기면 즉시. 이상 분석 `완료` 아래 하위 항목은 늘 펼친 채(`subFixed`, 펼침·접힘 없음), 탐지 설정 목차는 현재 메뉴에만 펼침(높이 `layout` standard)
- 스크롤 막대 자리(사용자 11/28): 문서는 스크롤 막대 자리를 늘 비워 둔다(`scrollbar-gutter: stable`, 지원 브라우저만 — 대화상자 스크롤 잠금의 오른쪽 여백 보정은 끄고, 대화상자 배경은 그 자리까지 덮는다) — 화면·분류마다 내용 길이가 달라 막대가 생겼다 사라져도 본문 폭이 같다. 겹쳐 그리는 막대(휴대폰)는 영향 없음
- 넓게 보기(사용자 11/28): 상단 메뉴 오른쪽 알림 버튼 왼쪽 아이콘 버튼 하나, 모든 화면 같은 자리(IconArrowsMaximize/IconArrowsMinimize, 이름 `넓은 화면으로 보기`/`콤팩트 화면으로 보기`, `aria-pressed`, 켜지면 옅은 배경). 켜면 본문(`AppShell` `main`)의 1600px 폭 제한만 풀어 좌우를 채운다(좌우 여백 그대로, 왼쪽 메뉴 그대로). 폭은 `layout` standard로 바뀐다. 값은 화면 공통 하나(브라우저 저장소 `esb_layout_wide`, 이전 이상 분석 전용 값은 뜻이 달라 이어받지 않음). 휴대폰 폭은 버튼 숨김. 이상 분석 목록·상세·사건 화면 머리의 넓게 보기 버튼과 메뉴 접기 동작은 없다
- 화면 전환(사용자 11/28): 키가 바뀌면 이전 화면이 투명해지며 사라지고(`fast` ease-in) 새 화면이 나타난다(`base` ease-out), 투명도만. 키 = 상단 메뉴 단위(`AppLayout`), 이상 분석·탐지 설정 본문은 경로 단위, 설정 본문은 항목(주소의 `section` 값) 단위 — 사이드바는 두고 본문에만 건다. 그 밖에 쿼리·해시만 바뀐 이동(분류·쪽·탭·단계·목차)은 전환 없음. 주소 교체(replace) 이동(옛 주소 넘김·주소 정규화·작업 ID → 판정 ID·병합 사건 id 교체·첫 화면 넘김)·동작 줄이기는 효과 없이 바로 바꾼다. 사라지는 화면은 React 화면으로 남기지 않는다: 키가 바뀌는 커밋에서 DOM을 바꾸기 직전 이전 화면 DOM의 정적 사본을 떠 같은 자리(화면 기준 위치·크기)에 겹쳐 사라지게 한 뒤 지우고, 이전 화면 컴포넌트는 그 커밋에서 바로 내려간다 — 사라지는 동안 이전 화면에서 나오는 이동·조회·저장소 변경이 없다. 사본은 `inert`·`aria-hidden`·누름 막음, id 속성 없음(SVG `defs` 안의 id는 화살촉 등 참조를 위해 둠), 사본 안 CSS 애니메이션·전환은 멈춘다(떠난 순간의 모습 그대로 사라짐). 새 화면은 곧바로 마운트되고 사본이 사라질 때까지 투명. 사본이 사라지는 중에 또 이동하면 그 사본을 이어서 사라지게 한다. 맨 위로 올리기는 미루지 않고 바로 한다(사본은 화면 기준 고정이라 튀지 않고, 새 화면의 작업 영역 스크롤이 그 뒤에 이긴다). 목차 영역까지의 부드러운 스크롤만 같은 커밋에 시작된 전환(바깥·안쪽 어느 단위든)의 사본이 사라진 뒤 시작한다. 안쪽 전환이 진행 중일 때 바깥 전환이 오면 안쪽 사본은 끝까지 사라지게 둔다. 한계: canvas 내용·입력 칸의 입력 중 값은 사본에 그려지지 않고, 사본 안 고정 줄(sticky)은 원래 흐름 위치에 그려진다. `components/layout/PageFade.tsx`
- 로딩 표시(사용자 11/28, `components/ui/pending.tsx`): 이전 결과를 들고 다시 조회하는 동안 — 조건(분류·검색·필터·정렬·기간 등)이 바뀌면 행 자리 뼈대(`Skeleton`, 목록 행 모양), 같은 조건의 쪽 넘김이면 이전 행을 흐리게(투명도 50%·누름 막음) + 목록 위 2px 진행 막대(`--animate-progress-slide`). 대기가 250ms보다 짧으면 표시 없음, 한번 보이면 최소 350ms(`PENDING_MS`). 문구는 화면 낭독용 기존 `불러오는 중...`만. `aria-busy`는 다시 조회하는 동안 내내(표시가 보이기 전 지연 포함). 적용: 판정 목록·사건 목록·응답 코드 목록(뼈대/흐리게), 규칙 추천(인터페이스 선택이 결과 안에 있어 흐리게만). 화면 조각을 받는 동안(Suspense)은 250ms 뒤 `LoadingState`. 최초 로딩은 그대로 `LoadingState`
- 목차 스크롤(사용자 11/28, `features/detection/useDetectionScroll.ts`): 탐지 설정 목차를 누르면 그 영역으로 부드럽게 이동 — 길이 160ms + √거리(px)×9, 240~560ms(`SCROLL_MS`), 곡선 standard, 프레임마다 목적지를 다시 잰다. 이동하는 동안·이동한 뒤 누른 항목이 현재 표시로 고정되고(지나가는 영역으로 튀지 않음, 짧은 마지막 영역도 그 항목) 사용자가 휠·터치·키·누르기를 하거나 창 스크롤 막대를 끌면(목적지·문서 높이는 그대로인데 위치가 달라짐·이동 중이 아닐 때의 스크롤 — 문서 높이가 바뀌어 생긴 위치 변화는 제외) 고정이 풀리고 이동은 그 자리에서 멈춘다. 브라우저 뒤로/앞으로 뒤에는 고정이 남지 않는다. 도착 뒤 1.2초 동안 목적지가 밀리면(늦게 생긴 위쪽 내용) 바로 맞춘다. 대상 위 로딩 표시(`LoadingState`·대기 표시)는 최대 8초 기다린다. 같은 항목 다시 누르기도 이동. 동작 줄이기면 즉시 그 자리로(도착 뒤 맞춤·목차 고정은 같음)
- 운영 현황 위젯 격자: 격자의 위치·크기 전환(라이브러리 기본 CSS)은 편집 중과 편집을 끝낸 뒤 250ms(`GRID_SETTLE_MS`)만 켠다 — 화면에 들어올 때 첫 배치가 미끄러지지 않게
- 하단 시트·서랍 부품은 아직 없음. 추가하면 `slow` 등장·`base` 퇴장, 열린 쪽 가장자리에서 미끄러짐으로 맞춘다
- 화면 코드에서 `animate-in`·`duration-200`·`ease-[...]` 같은 값을 직접 쓰지 않고 `motionClasses`·`motionTransition(길이, 곡선)`을 쓴다. 예외: 운영 현황 위젯 편집의 스프링·흔들림(끌기 손맛, 위젯 전용)
- 동작 줄이기: CSS는 `styles.css` base 미디어쿼리(길이 0.01ms), Motion은 앱 `MotionConfig reducedMotion="user"`·개별 `useReducedMotion`

```tsx
<Button asChild variant="outline">
  <Link to="/analysis?q=delay">이상 분석</Link>
</Button>
<ErrorState title="조회에 실패했습니다" onRetry={refetch} />

const query = useAccountQuery({ name: "operations-overview", read: readOperationsOverview });

notify.loading("저장 중", "save:settings");
// 실제 작업 성공 뒤 동일 ID로 교체
notify.success("저장 완료", "save:settings");
// 실패 시: notify.error("저장 실패", "save:settings");
```

- 세션 변경: AuthSessionProvider에서 `notify.clear()` 즉시 제거. 비동기 호출자는 취소·세션 검증 후 현재 계정의 결과만 알림; 이전 작업의 지연 완료 재발행 금지
- 차트: Recharts Tooltip 유지; 공통 일반 Tooltip과 구분. 신규 라이브러리 도입은 조율 대상
