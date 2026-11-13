Created: 2026-10-08T12:53:45+09:00
Updated: 2026-11-13T18:00:00+09:00
Author: frontend_lead · Claude Code(#100)
Status: current

# 공통 UI 사용

- UI 동작·표현의 단일 기준 문서; 에이전트 지침에는 이 문서 참조만 유지
- 스타일(사용자 11/03): shadcn 부품(new-york, `frontend/components.json`) 기본. Tailwind 4는 부품 내부와 배치·간격 유틸리티로만 사용. 자체 CSS 클래스·인라인 style·색상 하드코딩 금지
- 공통 부품 재사용; 화면별 중복 구현·동일 색상/크기 하드코딩 복제 금지. import는 `@/` 별칭
- 기본 부품(`frontend/src/components/ui/`): button, badge, card, tabs, table, select, native-select, combobox, input, textarea, label, checkbox, radio-group, switch, dialog, Modal, popover, tooltip, separator, skeleton, scroll-area, alert, feedback
- 화면 골격 부품(`frontend/src/components/layout/`): AppShell, ScreenTitle(`PageHeader.tsx`), SectionCard, SideNav(사이드바 한 벌), LinkTabs(URL 탭), DescriptionList, NavigationGuard. 주소 선택값 도구: `hooks/useUrlState.ts`(useUrlOption·useUrlStep)
- 공통 토큰: `frontend/src/lib/tokens.css` 한 벌 — 원천 값(`--ink`, `--canvas` 등 기존 별칭 이름) → shadcn 의미 변수(`--background`, `--primary`, `--muted-foreground` 등) → Tailwind `@theme`(예: `bg-primary`, `text-muted-foreground`, `bg-warning-soft`, `text-page-title`). 새 색·크기는 이 파일에만 추가
- 화면 제목(사용자 11/03, 설정은 11/05): 운영 현황·이상 분석·탐지 설정·설정은 보이는 제목 줄을 두지 않음(상단 메뉴·사이드바의 현재 표시로 위치 확인). 화면 낭독기·문서 구조용 `ScreenTitle`(sr-only h1)만 두고, 제목 줄 옆 명령은 본문 첫 영역(오른쪽 정렬 줄·첫 카드 머리)으로. 탐지 설정 하위 화면의 상단 뒤로가기 아이콘은 유지. 설정의 `저장 미지원`은 내용 영역 오른쪽 위
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
- 길이: `fast` 120ms(체크·토글·누름, 작은 표면 퇴장) · `base` 180ms(팝오버·선택 목록·툴팁·제목 줄·로딩/빈/오류 표시 등장, 대화상자·토스트 퇴장) · `slow` 240ms(대화상자·토스트 등장) · `layout` 320ms(사이드바 접기처럼 배치가 바뀌는 전환)
- 곡선: 등장 `ease-motion-out` cubic-bezier(0.2, 0, 0, 1) · 퇴장 `ease-motion-in` cubic-bezier(0.4, 0, 1, 1) · 상태 변화 `ease-motion-standard` cubic-bezier(0.4, 0, 0.2, 1). 퇴장은 등장보다 한 단계 짧게
- 모양(`motionClasses`): 대화상자·Modal(팝업) = 투명도 + 97% 확대 + 아래 8px, 배경 = 투명도만 / 팝오버·Select·Combobox = 투명도 + 95% 확대 + 열린 쪽 가장자리 4px / 툴팁 = 같은 모양 / 토스트(스낵바) = 아래 8px + 97% 확대(`--animate-toast-in/out`, `AppProvider`의 `ToastBar`) / 체크·라디오 표시 = 50%에서 커지며 나타남 / 체크 상자·스위치·눌림 버튼 색·위치 = `toggle` / 버튼 누름(좋아요·토글 버튼 포함) = 97% 눌림(`press`) / 섹션 제목 줄(`SectionCard` 머리)·`LoadingState`·`EmptyState`·`ErrorState` = 처음 그려질 때 투명도 + 아래 4px(`enter`)
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
