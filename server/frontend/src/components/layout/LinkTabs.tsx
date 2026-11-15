import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

export type LinkTab = { id: string; label: string; to: string };

/**
 * URL 탭: 밑줄형 Link + aria-current=page. 로컬 Tabs ARIA(role=tab)를 쓰지 않는다(ui.md·navigation.md).
 * state(선택): 탭 이동에 실을 이동 state — 하위 화면이 들어올 때 받은 돌아올 주소(returnTo)를 탭을 바꿔도 유지할 때. 없으면 전처럼 state 없이 이동.
 */
export const LinkTabs = ({
  label,
  tabs,
  activeId,
  className,
  state,
}: {
  label: string;
  tabs: LinkTab[];
  activeId: string;
  className?: string;
  state?: unknown;
}) => (
  <nav aria-label={label} className={cn("flex gap-1 overflow-x-auto border-b", className)}>
    {tabs.map((tab) => {
      const current = tab.id === activeId;
      return (
        <Link
          key={tab.id}
          to={tab.to}
          state={state}
          aria-current={current ? "page" : undefined}
          className={cn(
            "whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-menu text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
            current && "border-primary font-semibold text-foreground",
          )}
        >
          {tab.label}
        </Link>
      );
    })}
  </nav>
);
