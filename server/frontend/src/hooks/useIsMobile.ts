import { useSyncExternalStore } from "react";

/**
 * 휴대폰 폭(ui.md "휴대폰 폭"): 768px 미만 = Tailwind v4 `max-md`가 빌드 CSS에 남기는 미디어쿼리와 같은 문자열(`@media not all and (min-width:48rem)` — CSS와 JS가 같은 답을 내고, 범위 문법을 모르는 옛 Safari도 읽는다). 배치는 `max-md:` 클래스로 바꾸고,
 * 동작이 달라지는 곳(첫 화면·알림 버튼)만 이 값을 쓴다. matchMedia가 없는 환경(시험 렌더러·서버 렌더)은 PC로 본다.
 */
export const MOBILE_QUERY = "not all and (min-width: 48rem)";

const media = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(MOBILE_QUERY) : null);

export const isMobileViewport = () => media()?.matches ?? false;

const subscribe = (onChange: () => void) => {
  const query = media();
  if (!query || typeof query.addEventListener !== "function") return () => {};
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
};

export const useIsMobile = () => useSyncExternalStore(subscribe, isMobileViewport, () => false);
