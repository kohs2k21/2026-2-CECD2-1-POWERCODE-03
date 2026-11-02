import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

export type LinkTab = { id: string; label: string; to: string };

/** URL 탭: 밑줄형 Link + aria-current=page. 로컬 Tabs ARIA(role=tab)를 쓰지 않는다(ui.md·navigation.md). */
export const LinkTabs = ({
  label,
  tabs,
  activeId,
  className,
}: {
  label: string;
  tabs: LinkTab[];
  activeId: string;
  className?: string;
}) => (
  <nav aria-label={label} className={cn("flex gap-1 overflow-x-auto border-b", className)}>
    {tabs.map((tab) => {
      const current = tab.id === activeId;
      return (
        <Link
          key={tab.id}
          to={tab.to}
          aria-current={current ? "page" : undefined}
          className={cn(
            "whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-menu text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
            current && "border-primary text-foreground",
          )}
        >
          {tab.label}
        </Link>
      );
    })}
  </nav>
);
