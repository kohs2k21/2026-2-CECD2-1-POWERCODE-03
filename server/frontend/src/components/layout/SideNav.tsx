import { useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { IconMenu2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SideNavItem = {
  id: string;
  label: ReactNode;
  to: string;
  icon?: ReactNode;
  count?: number;
};
export type SideNavGroup = { title?: string; items: SideNavItem[] };

/**
 * 사이드바 한 벌(탐지 설정·이상 분석 분류·설정 항목 공용).
 * - 항목은 URL Link, 현재 항목은 aria-current="page"(ui.md 공통 내부 탐색)
 * - 실폭 264px, 메뉴 최소 높이 48px. 제목(heading)·그룹 제목 13/600/18 흐린 글자(`text-group`), 메뉴 14/21(`text-menu`) 500, 현재 항목 600
 * - 제목 수준: heading이 있으면 heading h2·그룹 h3, 없으면 그룹 h2(이상 분석)
 * - 좁은 화면: 메뉴 버튼으로 펼침, Escape·항목 선택 시 접고 버튼으로 포커스 복귀
 */
export const SideNav = ({
  label,
  heading,
  groups,
  activeId,
  menuId,
  className,
}: {
  label: string;
  heading?: string;
  groups: SideNavGroup[];
  activeId: string | null;
  menuId?: string;
  className?: string;
}) => {
  const generatedId = useId();
  const navId = menuId ?? generatedId;
  const [expanded, setExpanded] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const GroupTitle = heading ? "h3" : "h2";
  const collapse = () => {
    if (!expanded) return;
    setExpanded(false);
    toggleRef.current?.focus();
  };
  return (
    <aside
      data-slot="side-nav"
      className={cn("w-full shrink-0 md:w-(--section-sidebar-width)", className)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && expanded) {
          event.preventDefault();
          collapse();
        }
      }}
    >
      <Button
        ref={toggleRef}
        variant="outline"
        className="mb-2 w-full justify-start md:hidden"
        aria-expanded={expanded}
        aria-controls={navId}
        onClick={() => setExpanded((value) => !value)}
      >
        <IconMenu2 aria-hidden="true" />
        {heading ?? label}
      </Button>
      <nav
        id={navId}
        aria-label={label}
        className={cn("grid gap-4 rounded-lg border bg-card p-3 md:sticky md:top-4", !expanded && "max-md:hidden")}
      >
        {heading && <h2 className="px-3 text-group text-muted-foreground">{heading}</h2>}
        {groups.map((group, index) => (
          <section key={group.title ?? index} className="grid gap-1.5">
            {group.title && <GroupTitle className="px-3 text-group text-muted-foreground">{group.title}</GroupTitle>}
            {group.items.map((item) => {
              const current = item.id === activeId;
              return (
                <Link
                  key={item.id}
                  to={item.to}
                  aria-current={current ? "page" : undefined}
                  onClick={collapse}
                  className={cn(
                    "flex min-h-(--section-menu-height) items-center gap-3 rounded-md px-3 text-menu text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                    current && "bg-accent font-semibold text-foreground",
                  )}
                >
                  {item.icon && <span className="flex size-5 items-center justify-center [&_svg]:size-4">{item.icon}</span>}
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {typeof item.count === "number" && (
                    <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", current && "text-foreground")}>{item.count}</span>
                  )}
                </Link>
              );
            })}
          </section>
        ))}
      </nav>
    </aside>
  );
};
