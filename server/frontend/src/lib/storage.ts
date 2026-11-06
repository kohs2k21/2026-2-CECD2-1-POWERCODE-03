/** 브라우저에만 두는 화면 편의 설정(넓게 보기 등). 서버 상태·편집 초안·토큰은 여기에 두지 않는다. */
export const storageKeys = {
  layoutWide: (view: "analysis") => `esb_layout_wide_${view}`,
} as const;

export function getStored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function setStored<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 저장소를 쓸 수 없으면 현재 화면에서만 유지한다.
  }
}
