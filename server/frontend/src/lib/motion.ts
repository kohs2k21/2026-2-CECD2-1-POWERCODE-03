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
} as const;
