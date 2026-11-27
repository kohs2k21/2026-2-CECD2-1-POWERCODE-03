/**
 * 움직임 토큰(contracts/ui.md "움직임"). 길이·곡선 값은 lib/tokens.css의 --motion-*와 같다(tests/common-ui.mjs가 확인).
 * - fast 120ms: 체크·토글·누름 피드백, 작은 표면 퇴장
 * - base 180ms: 팝오버·선택 목록·툴팁·제목·로딩 표시 등장, 대화상자·토스트 퇴장
 * - slow 240ms: 대화상자·토스트 등장
 * - layout 320ms: 사이드바 접기처럼 배치가 바뀌는 전환
 * 동작 줄이기: CSS는 styles.css base 미디어쿼리, Motion은 앱 MotionConfig reducedMotion="user".
 * 클래스 문자열은 Tailwind가 찾을 수 있게 조각을 끼워 만들지 않고 그대로 적는다.
 */
export const MOTION_MS = { fast: 120, base: 180, slow: 240, layout: 320 } as const;
export const MOTION_EASE = {
  out: [0.2, 0, 0, 1],
  in: [0.4, 0, 1, 1],
  standard: [0.4, 0, 0.2, 1],
} as const;

type Bezier = [number, number, number, number];
/** Motion(motion/react) transition: 같은 토큰. */
export const motionTransition = (duration: keyof typeof MOTION_MS, ease: keyof typeof MOTION_EASE = "out") => ({
  duration: MOTION_MS[duration] / 1000,
  ease: [...MOTION_EASE[ease]] as Bezier,
});

/**
 * 스프링(motion/react `{ type: "spring", stiffness, damping }`, 질량 1). ui.md "움직임" 메뉴 현재 표시.
 * - grow: 상위 메뉴 항목 현재 표시가 제자리에서 피어남(92% → 100%, 투명도는 base)
 * - stretchLead·stretchTrail: 하위 목록 현재 표시(왼쪽 선)가 이동 방향 앞 가장자리는 빠르게, 뒤 가장자리는 늦게 따라와 늘었다 줄어듦
 */
export const MOTION_SPRING = {
  grow: { stiffness: 600, damping: 34 },
  stretchLead: { stiffness: 760, damping: 48 },
  stretchTrail: { stiffness: 240, damping: 30 },
} as const;
export const springTransition = (key: keyof typeof MOTION_SPRING) => ({ type: "spring" as const, ...MOTION_SPRING[key] });

/**
 * 목차 부드러운 스크롤(ui.md "움직임" 스크롤): 길이 = base + √거리(px) × perSqrtPx, minMs~maxMs. 곡선은 standard.
 * 도착 뒤 settleMs 동안 목적지가 움직이면(늦게 생긴 위쪽 내용) 바로 맞춘다. 대상 위 로딩 표시는 waitMs까지 기다린다.
 */
export const SCROLL_MS = { base: 160, perSqrtPx: 9, min: 240, max: 560, settle: 1200, wait: 8000, poll: 100 } as const;
export const scrollDuration = (distance: number) =>
  Math.round(Math.min(SCROLL_MS.max, Math.max(SCROLL_MS.min, SCROLL_MS.base + Math.sqrt(Math.abs(distance)) * SCROLL_MS.perSqrtPx)));

/** 조회 대기 표시(ui.md "움직임" 로딩 표시): 대기가 delay보다 짧으면 표시하지 않고, 한번 보이면 최소 min 동안 유지(깜박임 방지). */
export const PENDING_MS = { delay: 250, min: 350 } as const;

/** 운영 현황 위젯 격자 전환은 편집 중과 편집을 끝낸 뒤 이만큼만 켠다(첫 배치·다시 들어올 때 미끄러지지 않게). 라이브러리 기본 전환 200ms보다 길게. */
export const GRID_SETTLE_MS = 250;

/** cubic-bezier 값 계산(rAF로 직접 움직이는 스크롤용). */
export const cubicBezier = (x1: number, y1: number, x2: number, y2: number) => {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sy = (t: number) => ((ay * t + by) * t + cy) * t;
  const dx = (t: number) => (3 * ax * t + 2 * bx) * t + cx;
  return (x: number) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 6; i++) {
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= (sx(t) - x) / d;
    }
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 20 && Math.abs(sx(t) - x) > 1e-5; i++) {
      if (sx(t) < x) lo = t;
      else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
};

/** Radix data-state 등장·퇴장 클래스(tw-animate-css animate-in/out + 토큰 길이·곡선). */
export const motionClasses = {
  /** 대화상자 배경: 투명도만 */
  overlay:
    "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 duration-(--motion-duration-slow) ease-motion-out data-[state=closed]:duration-(--motion-duration-base) data-[state=closed]:ease-motion-in",
  /** 대화상자(팝업): 투명도 + 97% 확대 + 아래에서 8px */
  dialog:
    "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.97] data-[state=open]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-[0.97] duration-(--motion-duration-slow) ease-motion-out data-[state=closed]:duration-(--motion-duration-base) data-[state=closed]:ease-motion-in",
  /** 팝오버·선택 목록·검색 선택: 투명도 + 95% 확대 + 열린 쪽 가장자리에서 4px */
  surface:
    "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 duration-(--motion-duration-base) ease-motion-out data-[state=closed]:duration-(--motion-duration-fast) data-[state=closed]:ease-motion-in",
  /** 툴팁(열린 상태가 delayed-open·instant-open이라 data-state=open으로 거르지 않음) */
  tooltip:
    "animate-in fade-in-0 zoom-in-95 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 duration-(--motion-duration-base) ease-motion-out data-[state=closed]:duration-(--motion-duration-fast) data-[state=closed]:ease-motion-in",
  /** 새로 나타나는 블록(섹션 제목 줄·로딩·빈 상태·오류): 투명도 + 아래에서 4px, 처음 그려질 때 한 번 */
  enter: "animate-in fade-in-0 slide-in-from-bottom-1 duration-(--motion-duration-base) ease-motion-out",
  /** 체크·라디오 표시: 50%에서 커지며 나타나고 줄며 사라짐 */
  check:
    "data-[state=checked]:animate-in data-[state=checked]:fade-in-0 data-[state=checked]:zoom-in-50 data-[state=unchecked]:animate-out data-[state=unchecked]:fade-out-0 data-[state=unchecked]:zoom-out-50 duration-(--motion-duration-fast) ease-motion-out",
  /** 토글·선택 상태 색 변화(체크 상자·스위치·눌림 버튼) */
  toggle: "transition-[color,background-color,border-color,box-shadow,translate] duration-(--motion-duration-fast) ease-motion-standard",
  /** 누름 피드백(버튼·좋아요·토글 버튼): 97%로 눌림 */
  press:
    "transition-[color,background-color,border-color,box-shadow,scale] duration-(--motion-duration-fast) ease-motion-standard active:not-disabled:scale-97",
  /** 메뉴 현재 표시가 켜질 때(도착) 글자·아이콘 색: base ease-out + 누름 97% */
  litOn: "transition-[color,background-color,scale] duration-(--motion-duration-base) ease-motion-out active:not-disabled:scale-97",
  /** 메뉴 현재 표시가 떠날 때(꺼짐) 글자·아이콘 색: fast standard + 누름 97% */
  litOff: "transition-[color,background-color,scale] duration-(--motion-duration-fast) ease-motion-standard active:not-disabled:scale-97",
  /** 화면 전환·새 행 페이드(로딩 표시 등장 등): 투명도만 base */
  fadeIn: "animate-in fade-in-0 duration-(--motion-duration-base) ease-motion-out",
} as const;

/**
 * 흘러나오기(ui.md "움직임" 흘러나오기 — AI 판정 근거 요약): 글자를 어절 단위로 앞에서부터 이어 붙이고, 새 조각마다 `slow` 길이로 투명 → 불투명.
 * word = 어절 간격, row = 표 행·통째 블록 간격, block = 블록 사이 쉼, total = 마지막 조각의 페이드가 끝나는 시각 상한.
 * 자연 길이가 total을 넘으면 모든 간격을 같은 비율로 줄인다(짧으면 더 빨리 끝남). CSS는 styles.css `.stream-in`.
 */
export const STREAM_MS = { word: 45, row: 90, block: 140, total: 3000 } as const;
