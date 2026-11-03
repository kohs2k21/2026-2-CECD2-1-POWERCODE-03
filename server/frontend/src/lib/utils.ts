import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tokens.css의 글자 크기 토큰(`--text-<이름>`, 하위 속성 `--text-<이름>--*` 제외).
 * tailwind-merge는 기본 크기(sm·xs…)만 알아서 `text-menu` 같은 토큰을 글자 색으로 보고 뒤의 `text-muted-foreground`와 겹친다며 지운다.
 * 크기 토큰으로 등록해 색 클래스와 함께 남게 한다. 토큰을 추가하면 이 목록도 추가한다(tests/sidebar.mjs가 tokens.css와 대조).
 */
export const fontSizeTokens = ["nav-compact", "brand-compact", "group", "menu", "page-title", "auth-title", "section-title", "body"] as const;

const merge = extendTailwindMerge({ extend: { theme: { text: [...fontSizeTokens] } } });

export const cn = (...inputs: ClassValue[]) => {
  return merge(clsx(inputs));
};
