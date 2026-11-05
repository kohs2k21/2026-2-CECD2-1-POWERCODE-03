import type { PropsWithChildren, ReactNode } from "react";

/** 앱 공통 틀: 상단 메뉴 + 본문(본문으로 이동 링크 대상 #workspace-content). */
export const AppShell = ({ topNav, children }: PropsWithChildren<{ topNav: ReactNode }>) => (
  <div className="min-h-screen bg-background">
    {topNav}
    <main id="workspace-content" tabIndex={-1} className="mx-auto w-full max-w-workspace px-4 py-(--page-block-padding) outline-none md:px-8">
      {children}
    </main>
  </div>
);
