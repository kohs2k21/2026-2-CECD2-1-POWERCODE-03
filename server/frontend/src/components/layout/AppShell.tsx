import type { PropsWithChildren, ReactNode } from "react";

type AppShellProps = PropsWithChildren<{
  topNav: ReactNode;
}>;

export const AppShell = ({ topNav, children }: AppShellProps) => (
  <main className="app-shell">
    {topNav}
    <section className="workspace" id="workspace-content" tabIndex={-1}>
      {children}
    </section>
  </main>
);
