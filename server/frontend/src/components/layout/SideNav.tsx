import { Fragment, useId, useRef, useState, type ReactNode } from "react";
import { Link, type To } from "react-router-dom";
import { IconMenu2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SideNavItem = {
  id: string;
  label: ReactNode;
  to: string;
  icon?: ReactNode;
  count?: number;
  /** 약하게 보이는 항목(예: 이상 분석 위험도 미정 — 와이어프레임 muted·13px). */
  muted?: boolean;
  /** 항목 아래 들여쓴 하위 목록(예: 탐지 설정 현재 메뉴의 화면 영역 목차). 펼칠 항목에만 넘긴다. */
  sub?: SideNavSubItem[];
};
/** 하위 목록 항목: 같은 화면 안 이동이면 replace(뒤로 가기에 쌓지 않음), current는 지금 보이는 영역(aria-current="location"). */
export type SideNavSubItem = { id: string; label: ReactNode; to: To; replace?: boolean; current?: boolean };
/** separated: 위 그룹과 구분선으로 나눈 별도 구역(예: 이상 분석 분류함). */
export type SideNavGroup = { title?: string; items: SideNavItem[]; separated?: boolean };

/**
 * 사이드바 한 벌(탐지 설정·이상 분석 분류·설정 항목 공용).
 * - 항목은 URL Link, 현재 항목은 aria-current="page"(ui.md 공통 내부 탐색)
 * - 실폭 264px, 메뉴 최소 높이 48px. 제목(heading)·그룹 제목 13/600/18 흐린 글자(`text-group`), 메뉴 14/21(`text-menu`) 500, 현재 항목 600
 * - 제목 수준: heading이 있으면 heading h2·그룹 h3, 없으면 그룹 h2(이상 분석)
 * - 좁은 화면: 메뉴 버튼으로 펼침, Escape·항목 선택 시 접고 버튼으로 포커스 복귀
 * - header: 그룹 위에 두는 보조 조작(예: 이상 분석 분류 보기 전환)
 * - 넓은 화면: 페이지 스크롤에도 머리글 아래에 고정. 화면보다 길면 사이드바 안에서 스크롤. 고정은 aside에 건다(nav에 걸면 aside 높이가 같아 움직일 공간이 없다)
 * - sub(선택): 항목 아래 들여쓴 하위 목록(목차). 13px 흐린 글자, 현재 영역은 왼쪽 선·진한 글자
 */
export const SideNav = ({
  label,
  heading,
  groups,
  activeId,
  menuId,
  className,
  header,
}: {
  label: string;
  heading?: string;
  header?: ReactNode;
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
      className={cn("w-full shrink-0 md:sticky md:top-[calc(var(--top-nav-offset)+var(--page-block-padding))] md:w-(--section-sidebar-width) md:self-start", className)}
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
        className={cn("grid gap-4 rounded-lg border bg-card p-3 md:max-h-[calc(100dvh-var(--top-nav-offset)-2*var(--page-block-padding))] md:overflow-y-auto", !expanded && "max-md:hidden")}
      >
        {heading && <h2 className="px-3 text-group text-muted-foreground">{heading}</h2>}
        {header}
        {groups.map((group, index) => (
          <section key={group.title ?? index} className={cn("grid gap-1.5", group.separated && "border-t pt-3")}>
            {group.title && <GroupTitle className="px-3 text-group text-muted-foreground">{group.title}</GroupTitle>}
            {group.items.map((item) => {
              const current = item.id === activeId;
              return (
                <Fragment key={item.id}>
                  <Link
                    to={item.to}
                    aria-current={current ? "page" : undefined}
                    onClick={collapse}
                    className={cn(
                      "flex min-h-(--section-menu-height) items-center gap-3 rounded-md px-3 text-menu text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                      current && "bg-accent font-semibold text-foreground",
                    )}
                  >
                    {item.icon && <span className="flex size-5 items-center justify-center [&_svg]:size-4">{item.icon}</span>}
                    <span className={cn("min-w-0 flex-1 truncate", item.muted && "text-body", item.muted && !current && "text-muted-foreground")}>{item.label}</span>
                    {typeof item.count === "number" && (
                      <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", current && "text-foreground")}>{item.count}</span>
                    )}
                  </Link>
                  {item.sub && item.sub.length > 0 && (
                    <ul data-slot="side-nav-sub" className="ml-6 grid gap-0.5 border-l">
                      {item.sub.map((sub) => (
                        <li key={sub.id}>
                          <Link
                            to={sub.to}
                            replace={sub.replace}
                            aria-current={sub.current ? "location" : undefined}
                            onClick={collapse}
                            className={cn(
                              "-ml-px flex min-h-8 items-center rounded-r-md border-l-2 border-transparent px-3 text-body text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                              sub.current && "border-foreground font-semibold text-foreground",
                            )}
                          >
                            <span className="min-w-0 flex-1 truncate">{sub.label}</span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </Fragment>
              );
            })}
          </section>
        ))}
      </nav>
    </aside>
  );
};
