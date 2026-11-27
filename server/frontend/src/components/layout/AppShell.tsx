import type { PropsWithChildren, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * 앱 공통 틀: 상단 메뉴 + 본문(본문으로 이동 링크 대상 #workspace-content).
 * 넓게 보기(wide, ui.md "넓게 보기"): 본문 최대 폭 1600px(--container-workspace) ↔ 100%. 좌우 여백은 그대로, 폭은 layout 320ms standard로 바뀐다
 * (가운데 기준으로 양쪽이 함께 벌어짐). 동작 줄이기면 styles.css 미디어쿼리가 길이를 없앤다.
 */
export const AppShell = ({ topNav, wide = false, children }: PropsWithChildren<{ topNav: ReactNode; wide?: boolean }>) => (
  <div className="min-h-screen bg-background">
    {topNav}
    <main
      id="workspace-content"
      tabIndex={-1}
      data-wide={wide || undefined}
      className={cn(
        "mx-auto w-full max-w-workspace px-4 py-(--page-block-padding) outline-none transition-[max-width] duration-(--motion-duration-layout) ease-motion-standard md:px-8",
        wide && "max-w-full",
      )}
    >
      {children}
    </main>
  </div>
);
